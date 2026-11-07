"""ADGS v3 hermes.py — Hermes 实例封装（增强版）

每个节点绑定一个 hermes profile，提供：
  - execute() / resume()：任务执行（kill+resume 模式，双信号活动检测）
  - create() / setup_newborn() / delete_temp()：profile 生命周期（含临时克隆）
  - list_skills() / install_skill()：技能基因读取与安装（繁衍用）
  - session_id 提取（正则，兼容新旧格式）
"""
import json
import codecs
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from contextlib import contextmanager, ExitStack

# 确保项目根在 sys.path（直接运行本模块自测时也能 import utils）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from utils.chat import get_debug_level
from web.event_bus import bus


# HERMES_HOME：hermes 运行时数据根目录（profile / 会话 / 日志 / 记忆等）。
# 重定向到项目内 .hermes，实现「hermes 产生的一切数据都保留在 ADGS 项目内」；
# hermes 的代码与 venv（hermes-agent）仍留在安装位置，只有数据目录被重定向。
# 必须在任何 hermes 子进程启动前设置 —— 本模块 import 即生效，所有 subprocess 继承。
_HERMES_HOME = Path(__file__).resolve().parent.parent / ".hermes"
os.environ["HERMES_HOME"] = str(_HERMES_HOME)

PROFILES_DIR = _HERMES_HOME / "profiles"

# opencode 正式 provider 的用户插件（每个 profile 独立 HERMES_HOME，需各自放置）。
# 源在顶层 .hermes/plugins/model-providers/opencode/；新 profile 由
# _sync_opencode_plugin() 从源复制。
_OPENCODE_PLUGIN_SRC = _HERMES_HOME / "plugins" / "model-providers" / "opencode"


_FROZEN_HERMES = _ROOT / "runtime" / "hermes" / "hermes-agent-cn-runtime-win32-x64.exe"


def hermes_executable() -> str:
    """项目内打包版 hermes 优先（相对路径，与外部升级解耦）；无则回退 PATH 系统安装。"""
    if _FROZEN_HERMES.is_file():
        return str(_FROZEN_HERMES)
    return "hermes"


def _sync_opencode_plugin(profile_name: str) -> bool:
    """把 opencode 用户插件复制进 profile 目录（profile = 独立 HERMES_HOME）。

    hermes 的 profile 模式把每个 ``profiles/<name>/`` 当作独立 HERMES_HOME，
    用户插件（plugins/model-providers/）必须存在于 profile 目录内才会被发现
    （2026-09-05 实测：只放顶层 .hermes/plugins/ 时 CLI --profile 扫不到）。
    顶层 .hermes/plugins/model-providers/opencode/ 是共享源。
    """
    try:
        source = _OPENCODE_PLUGIN_SRC if _OPENCODE_PLUGIN_SRC.is_dir() else _ROOT / "tools" / "hermes_opencode"
        if not all((source / name).is_file() for name in ("__init__.py", "plugin.yaml")):
            return False
        dst = PROFILES_DIR / profile_name / "plugins" / "model-providers" / "opencode"
        dst.mkdir(parents=True, exist_ok=True)
        for fn in ("__init__.py", "plugin.yaml"):
            src_f = source / fn
            if src_f.exists():
                shutil.copy2(src_f, dst / fn)
        return True
    except Exception as e:
        print(f"[hermes] 同步 opencode 插件到 {profile_name} 失败: {e}")
        return False


def _sync_opencode_env(profile_name: str, *, base_url: str, api_key: str,
                       overwrite: bool = False) -> bool:
    """确保 profile 的 .env 里有 OPENCODE_API_KEY / OPENCODE_BASE_URL（幂等）。

    hermes 的 api-key provider 凭据从 profile 目录 .env 读（get_env_value →
    load_env → get_env_path = profile home/.env）。config.yaml 的 model.api_key
    只对 custom 端点生效，真 opencode provider 路径需要 env 里的
    OPENCODE_API_KEY。无 base_url/api_key 时仅补缺失键。
    """
    try:
        env_f = PROFILES_DIR / profile_name / ".env"
        text = env_f.read_text(encoding="utf-8", errors="replace") if env_f.exists() else ""
        lines = text.splitlines(keepends=True)
        out, seen = [], set()
        for ln in lines:
            key = ln.split("=", 1)[0].strip() if "=" in ln else ""
            if key in ("OPENCODE_API_KEY", "OPENCODE_BASE_URL"):
                if key in seen:
                    continue
                seen.add(key)
                replacement = api_key if key == "OPENCODE_API_KEY" else base_url
                if overwrite and replacement:
                    ln = f"{key}={replacement}\n"
            out.append(ln)
        if not text.endswith(("\n", "\r")):
            pass
        body = "".join(out)
        if body and not body.endswith(("\n", "\r")):
            body += "\n"
        if "OPENCODE_API_KEY" not in seen and api_key:
            body += f"OPENCODE_API_KEY={api_key}\n"
        if "OPENCODE_BASE_URL" not in seen and base_url:
            body += f"OPENCODE_BASE_URL={base_url}\n"
        if body != text:
            temporary = env_f.with_name(".env." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(env_f)
        return True
    except Exception as e:
        print(f"[hermes] 同步 .env 到 {profile_name} 失败: {e}")
        return False


# session_id 提取（新旧格式兼容）
_SESSION_ID_RE = re.compile(r"[Ss]ession(?:_id)?:\s*(\S+)")


def _extract_session_id(text: str) -> Optional[str]:
    """从输出中提取 session_id（超时/异常/正常退出各分支复用）"""
    if not text:
        return None
    m = _SESSION_ID_RE.search(text)
    return m.group(1) if m else None


def _extract_skill_name(text: str, path: Path = None) -> str:
    """从 SKILL.md frontmatter 提取 name 字段（无则用目录名）"""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if m:
        fm = m.group(1)
        nm = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        if nm:
            return nm.group(1).strip().strip('"\'')
    if path:
        return path.parent.name
    return ""


def _skills_root(profile_name):
    root = (PROFILES_DIR / profile_name / "skills").resolve()
    if not root.is_relative_to(PROFILES_DIR.resolve()):
        raise ValueError("Invalid profile path")
    return root


def _load_profile_config(profile_name):
    import yaml
    path = PROFILES_DIR / profile_name / "config.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Hermes profile configuration is not a mapping")
    return data


def _merged_brain_enabled():
    """A2A 节点大脑合并开关（settings.yaml agent.merged_brain，默认关）。"""
    try:
        from config.config import config as _cfg
        return bool(getattr(_cfg, "node_merged_brain", False))
    except Exception:
        return False


def _mesh_token():
    """协作令牌：与 web_app 共用 .adgs_runtime/mesh_token（runtime 先启动，必已生成）。"""
    token_file = Path(os.environ.get(
        "ADGS_STATE_DIR", str(_ROOT / ".adgs_runtime"))) / "mesh_token"
    try:
        return token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _mesh_mcp_state() -> dict | None:
    """runtime 内嵌 mesh MCP 的端点/令牌（mesh_mcp.json；未就绪返回 None）。"""
    state_file = Path(os.environ.get(
        "ADGS_STATE_DIR", str(_ROOT / ".adgs_runtime"))) / "mesh_mcp.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        return state if state.get("url") and state.get("token") else None
    except (OSError, ValueError):
        return None


def _ensure_mesh_plugin(profile_name: str = None) -> bool:
    """写入/刷新种群协作插件（hermes Agent Plugin v1 + streamable-http MCP）。

    MCP server 由 ADGS runtime 进程内嵌托管（tools/adgs_mcp_server.py），
    所有 profile 的 worker 共享同一个 server（http://127.0.0.1:<port>/mcp，
    token 鉴权）。端点变化（runtime 重启换端口/轮换 token）→ mesh_mcp.json 更新 →
    _profile_ready 检测不一致 → 本函数重写 mcp.json。

    hermes 的 profile 模式把每个 profiles/<name>/ 当独立 HERMES_HOME，插件只会从
    profile 自己的 plugins/ 目录加载（2026-10-24 实测 ags_591 工具搜不到的根因：
    只写顶层 .hermes/plugins/ 时 profile 内 tool_search 无命中）。因此带 profile_name
    时同时把插件文件落到该 profile 内；不带则只维护顶层共享源（兼容旧调用）。
    """
    state = _mesh_mcp_state()
    if not state:
        return False  # runtime 尚未就绪；下次 ensure_profile 再试
    home = PROFILES_DIR.parent
    targets = [home / "plugins" / "adgs-mesh"]
    if profile_name:
        targets.append(PROFILES_DIR / profile_name / "plugins" / "adgs-mesh")
    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "adgs-mesh",
        "version": "1.2.0",
        "description": "ADGS 节点种群协作：list_nodes / probe_node / delegate_to_node / wait_delegation / report_delegation_verdict",
    }
    mcp_config = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "mesh": {
                "type": "streamable-http",
                "url": state["url"],
                "headers": {"X-ADGS-Mesh-Token": state["token"]},
            }
        },
    }
    ok = True
    for plugin_dir in targets:
        try:
            plugin_dir.mkdir(parents=True, exist_ok=True)
            _write_if_changed(plugin_dir / "plugin.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            _write_if_changed(plugin_dir / "mcp.json", json.dumps(mcp_config, ensure_ascii=False, indent=2))
        except OSError:
            ok = False
    _ensure_mesh_enabled(home)
    if profile_name:
        _ensure_mesh_enabled_profile(profile_name)
        if not _ensure_mesh_mcp_servers(profile_name):
            ok = False
    return ok


def _ensure_mesh_enabled_profile(profile_name: str):
    """把 adgs-mesh 加入 profile config 的 plugins.enabled 允许清单（幂等）。"""
    config_path = PROFILES_DIR / profile_name / "config.yaml"
    try:
        import yaml
        data = _load_profile_config(profile_name)
        plugins = data.setdefault("plugins", {})
        enabled = plugins.get("enabled") or []
        if "adgs-mesh" not in enabled:
            plugins["enabled"] = [*enabled, "adgs-mesh"]
            temporary = config_path.with_suffix(".yaml." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            temporary.replace(config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"[hermes] 启用 {profile_name} 的 adgs-mesh 插件失败: {exc}")


def _ensure_mesh_mcp_servers(profile_name: str) -> bool:
    """同步 profile config.yaml 的 mcp_servers.mesh 段（幂等）。

    hermes 加载远端 MCP 的实际生效路径是 config 的 mcp_servers（2026-10-24 实测：
    仅 plugins/ 目录 + plugins.enabled 时 hermes 侧 tool_search 无命中；worker
    ags_591 自行写入 mcp_servers 段后才可用）。url/token 必须与 mesh_mcp.json
    一致——runtime 重启轮换 token 后由 ensure_profile → 本函数自愈。
    """
    state = _mesh_mcp_state()
    if not state:
        return False
    config_path = PROFILES_DIR / profile_name / "config.yaml"
    try:
        import yaml
        data = _load_profile_config(profile_name)
        servers = data.setdefault("mcp_servers", {})
        mesh = servers.get("mesh") or {}
        desired = {"url": state["url"],
                   "headers": {"X-ADGS-Mesh-Token": state["token"]}}
        if mesh.get("url") != desired["url"] or mesh.get("headers") != desired["headers"]:
            servers["mesh"] = desired
            temporary = config_path.with_suffix(".yaml." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            temporary.replace(config_path)
        return True
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"[hermes] 同步 {profile_name} 的 mcp_servers 失败: {exc}")
        return False


def _ensure_mesh_enabled(home: Path):
    """把 adgs-mesh 加入全局 config 的 plugins.enabled 允许清单（幂等）。"""
    config_path = home / "config.yaml"
    try:
        import yaml
        data = {}
        if config_path.is_file():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
        plugins = data.setdefault("plugins", {})
        enabled = plugins.get("enabled")
        if isinstance(enabled, str):
            enabled = [part.strip() for part in enabled.split(",") if part.strip()]
        if not isinstance(enabled, list):
            enabled = []
        if "adgs-mesh" not in enabled:
            plugins["enabled"] = [*enabled, "adgs-mesh"]
            temporary = config_path.with_suffix(".yaml." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            temporary.replace(config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"[hermes] 启用 adgs-mesh 插件失败: {exc}")


def _write_if_changed(path: Path, text: str):
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return
        temporary = path.with_suffix(path.suffix + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def _mesh_plugin_current(profile_name=None):
    """merged 模式下校验 adgs-mesh 插件内容与启用状态是否就绪（轻量 JSON 读）。

    带 profile_name 时校验该 profile 自己的插件文件（hermes 只从 profile 级
    plugins/ 加载）；不带则校验顶层共享源。"""
    home = PROFILES_DIR.parent if not profile_name else PROFILES_DIR / profile_name
    try:
        state = _mesh_mcp_state()
        if not state:
            return False
        mcp_path = home / "plugins" / "adgs-mesh" / "mcp.json"
        if not mcp_path.is_file():
            return False
        server = (json.loads(mcp_path.read_text(encoding="utf-8"))).get("mcpServers", {}).get("mesh", {})
        if server.get("type") != "streamable-http" or server.get("url") != state["url"]:
            return False
        if (server.get("headers") or {}).get("X-ADGS-Mesh-Token") != state["token"]:
            return False
        import yaml as _yaml
        config_path = (PROFILES_DIR / profile_name if profile_name else home) / "config.yaml"
        if not config_path.is_file():
            return False
        cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if "adgs-mesh" not in ((cfg.get("plugins") or {}).get("enabled") or []):
            return False
        if profile_name:
            # hermes 实际加载远端 MCP 走 config.mcp_servers；段缺失或 token 过期都算未就绪
            mesh = ((cfg.get("mcp_servers") or {}).get("mesh") or {})
            if mesh.get("url") != state["url"]:
                return False
            if (mesh.get("headers") or {}).get("X-ADGS-Mesh-Token") != state["token"]:
                return False
        return True
    except (OSError, ValueError, AttributeError):
        return False


def _profile_ready(profile_name, mesh_required=None):
    try:
        data = _load_profile_config(profile_name)
        model = data.get("model") or {}
        if not isinstance(model, dict) or not model.get("default"):
            return False
        if (data.get("session") or {}).get("terminal_continue", True) is False:
            return False
        if profile_name != "ceo" and (data.get("approvals") or {}).get("single_query_mode") != "approve":
            return False
        disabled = (data.get("agent") or {}).get("disabled_toolsets") or []
        if isinstance(disabled, str):
            disabled = [part.strip() for part in disabled.split(",") if part.strip()]
        if "clarify" not in disabled:
            return False
        merged = _merged_brain_enabled() if mesh_required is None else mesh_required
        if merged and ("delegation" not in disabled or not _mesh_plugin_current(profile_name)):
            return False
        if model.get("provider") == "opencode":
            provider = (data.get("providers") or {}).get("opencode") or {}
            home = PROFILES_DIR / profile_name
            env_file = home / ".env"
            env = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
            return bool(provider.get("base_url") and provider.get("api_key") and
                        all(re.search(r"^" + key + r"=.+$", env, re.M) for key in ("OPENCODE_API_KEY", "OPENCODE_BASE_URL")) and
                        (home / "plugins" / "model-providers" / "opencode" / "plugin.yaml").is_file())
        return True
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        return False


def _configure_profile(profile_name, worker_cfg, overwrite_model=False, mesh_required=None):
    """Atomic YAML update, preserving unrelated values and old model snapshots."""
    import yaml
    try:
        home = PROFILES_DIR / profile_name
        if not home.is_dir():
            return False
        data = _load_profile_config(profile_name)
        before = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        model = data.setdefault("model", {})
        if overwrite_model or not model.get("default"):
            try:
                from config.config import config as app_config
                context = app_config.worker_ctx_window
            except (ImportError, AttributeError):
                context = 32768
            model.update({"provider": "opencode", "default": worker_cfg.get("exec_model") or "qwen3.8-27b", "context_length": int(worker_cfg.get("context_length") or worker_cfg.get("worker_ctx_window") or context)})
            for key in ("base_url", "api_key"):
                if worker_cfg.get(key):
                    model[key] = worker_cfg[key]
        if model.get("provider") == "opencode":
            provider = data.setdefault("providers", {}).setdefault("opencode", {})
            provider.setdefault("name", "opencode")
            provider.setdefault("api_mode", "chat_completions")
            for key in ("base_url", "api_key"):
                if overwrite_model or not provider.get(key):
                    provider[key] = model.get(key) or worker_cfg.get(key) or ""
            if not provider.get("base_url") or not provider.get("api_key"):
                return False
            plugin = home / "plugins" / "model-providers" / "opencode" / "plugin.yaml"
            if not plugin.is_file() and not _sync_opencode_plugin(profile_name):
                return False
            env_file = home / ".env"
            env_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
            if overwrite_model or not all(re.search(r"^" + key + r"=.+$", env_text, re.M) for key in ("OPENCODE_API_KEY", "OPENCODE_BASE_URL")):
                if not _sync_opencode_env(profile_name, base_url=provider["base_url"], api_key=provider["api_key"], overwrite=overwrite_model):
                    return False
        session = data.setdefault("session", {})
        session["terminal_continue"] = True
        if profile_name != "ceo":
            approvals = data.setdefault("approvals", {})
            if approvals.get("single_query_mode") != "approve":
                # ADGS workers are explicitly trusted by the operator and run
                # with -q. Hermes otherwise denies even ordinary Python -c
                # checks because there is no interactive approval terminal.
                # Preserve the original local config before this migration.
                config_file = home / "config.yaml"
                if config_file.is_file():
                    backup = home / "backups" / "adgs-runtime"
                    backup.mkdir(parents=True, exist_ok=True)
                    (backup / ("before-trusted-query-" + uuid.uuid4().hex + ".yaml")).write_bytes(config_file.read_bytes())
                approvals["single_query_mode"] = "approve"
        agent = data.setdefault("agent", {})
        disabled = agent.get("disabled_toolsets") or []
        if isinstance(disabled, str):
            disabled = [part.strip() for part in disabled.split(",") if part.strip()]
        if "clarify" not in disabled:
            agent["disabled_toolsets"] = [*disabled, "clarify"]
            disabled = agent["disabled_toolsets"]
        merged = _merged_brain_enabled() if mesh_required is None else mesh_required
        if merged:
            if "delegation" not in disabled:
                # A2A 合并模式：硬禁 hermes 内部 subagent 委派（delegate_task），
                # 节点间协作只走 adgs-mesh MCP 工具，两条委派链路不混淆。
                agent["disabled_toolsets"] = [*disabled, "delegation"]
            # hermes 插件按 profile config 的 plugins.enabled 允许清单加载
            # （全局清单不合并进 profile——2026-10-23 实测 worker 搜不到工具的根因）。
            plugins = data.setdefault("plugins", {})
            enabled = plugins.get("enabled") or []
            if "adgs-mesh" not in enabled:
                plugins["enabled"] = [*enabled, "adgs-mesh"]
        after = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        if before != after:
            temporary = home / (".config." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(after, encoding="utf-8")
            temporary.replace(home / "config.yaml")
        if merged:
            _ensure_mesh_plugin(profile_name)
        return _profile_ready(profile_name, mesh_required=mesh_required)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        # No credentials or config values are included in diagnostics.
        return False


def _find_skill(profile_name, name):
    root = _skills_root(profile_name)
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Invalid skill path")
    if (candidate / "SKILL.md").is_file():
        return candidate
    matches = []
    for document in sorted(root.rglob("SKILL.md")):
        if not document.resolve().is_relative_to(root):
            continue
        try:
            if _extract_skill_name(document.read_text(encoding="utf-8"), document) == name:
                matches.append(document.parent)
        except OSError:
            continue
    if len(matches) > 1:
        raise ValueError(f"Ambiguous skill name: {name}")
    return matches[0] if matches else None


@contextmanager
def _skill_write_lock(root, name):
    """Use Hermes' own per-basename lock path so CLI mutations coordinate too."""
    import hashlib
    path = root / ".locks" / (hashlib.sha256(Path(name).name.encode("utf-8", "surrogatepass")).hexdigest() + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b" ")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _extract_frontmatter_field(text: str, field: str) -> str:
    """从 SKILL.md frontmatter 提取指定字段值（如 description）

    兼容多行折叠语法：description: > / | 时取后续缩进行合并。
    字段不存在返回 ""。
    """
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return ""
    fm = m.group(1)
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        mm = re.match(rf"^{field}:\s*(.*)$", line)
        if not mm:
            continue
        val = mm.group(1).strip()
        if val in (">", "|", ">-", ">+", ">|", "|+", "|-", ">&", "|&"):
            # 多行折叠：合并后续缩进行
            parts = []
            for sub in lines[i + 1:]:
                if sub.startswith(" ") or sub.startswith("\t"):
                    parts.append(sub.strip())
                else:
                    break
            val = " ".join(parts)
        return val.strip().strip('"\'')
    return ""


class HermesInstance:
    """绑定单个 profile 的 hermes 控制实例"""

    def __init__(self, profile_name: str):
        self.profile = profile_name

    @property
    def profile_dir(self) -> Path:
        return PROFILES_DIR / self.profile

    # ================================================================
    # 基础命令
    # ================================================================

    def _run(self, args: list, timeout: int = 30) -> subprocess.CompletedProcess:
        """执行 hermes CLI 命令（shell=False + 列表参数，Windows 安全）"""
        cmd = [hermes_executable(), "--profile", self.profile] + args
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def config_set(self, key: str, value: str) -> bool:
        r = self._run(["config", "set", key, value])
        return r.returncode == 0

    def config_show(self) -> str:
        r = self._run(["config", "show"])
        return r.stdout + r.stderr

    def is_healthy(self) -> bool:
        try:
            r = self._run(["config", "show"], timeout=8)
            out = r.stdout + r.stderr
            return "api_key" in out and "model" in out.lower()
        except Exception:
            return False

    # ================================================================
    # 技能基因（繁衍核心）
    # ================================================================

    @staticmethod
    def list_skills(profile_name: str) -> Dict[str, str]:
        """读取 profile 的自定义技能（排除内置 bundle）

        返回 {skill_name: SKILL.md 首段描述}
        内置 skill 通过 .bundled_manifest（name:hash 逐行）识别；
        skill 名取 frontmatter 的 name 字段，而不是目录名（目录名是分类）

        注意：新建 profile 自带内置 bundle 但可能没有 manifest 文件，
        因此排除名单合并主 profile(default) 的 manifest —— 内置 bundle
        对同一套 hermes 安装是一致的。
        """
        pdir = PROFILES_DIR / profile_name / "skills"
        if not pdir.exists():
            return {}
        bundled = set()
        # 排除名单来源：profile 自身 manifest + 主 profile skills manifest
        # （新 profile 自带内置 bundle 但常缺 manifest，主 skills 的 manifest 兜底）
        manifest_sources = [
            pdir / ".bundled_manifest",
            PROFILES_DIR / "default" / "skills" / ".bundled_manifest",
            PROFILES_DIR.parent / "skills" / ".bundled_manifest",  # 主 skills
        ]
        for manifest in manifest_sources:
            if manifest and manifest.exists():
                for line in manifest.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if ":" in line:
                        bundled.add(line.split(":")[0])
        skills = {}
        for sk_md in pdir.rglob("SKILL.md"):
            try:
                text = sk_md.read_text(encoding="utf-8")
            except Exception:
                continue
            name = _extract_skill_name(text, sk_md)
            if not name or name in bundled:
                continue
            # 描述取 frontmatter 的 description 字段（干净文本），
            # 而非整段文件头 —— 避免把完整内容当 desc 传给基因继承
            desc = _extract_frontmatter_field(text, "description") or ""
            if not desc:
                head = text[:200].strip().replace("\n", " ")
                desc = head
            skills[name] = desc
        return skills

    @staticmethod
    def install_skill(profile_name: str, name: str, desc: str,
                      content: str = None, source_dir: str = None):
        """为 profile 安装一个自定义 skill（写 SKILL.md）

        content: 可选，完整 SKILL.md 内容（含 frontmatter）。
                 不传则用简短模板（name + desc）。
        """
        if isinstance(content, dict):
            source_dir = content.get("source_dir") or source_dir
            content = content.get("content")
        root = _skills_root(profile_name)
        sdir = _find_skill(profile_name, name) or (root / name).resolve()
        if not sdir.is_relative_to(root) or sdir == root:
            raise ValueError("Invalid skill path")
        locks = {(str(root), sdir.name)}
        source = None
        if source_dir:
            source = Path(source_dir).resolve()
            if not source.is_relative_to(PROFILES_DIR.resolve()) or not (source / "SKILL.md").is_file():
                raise ValueError("Invalid inherited skill package")
            source_profile = source.relative_to(PROFILES_DIR.resolve()).parts[0]
            source_root = _skills_root(source_profile)
            if not source.is_relative_to(source_root):
                raise ValueError("Inherited package is outside profile skills")
            locks.add((str(source_root), source.name))
        with ExitStack() as stack:
            # Stable ordering coordinates parent reads and child writes with
            # installed Hermes mutations, without cross-inheritance deadlocks.
            for lock_root, basename in sorted(locks):
                stack.enter_context(_skill_write_lock(Path(lock_root), basename))
            if source:
                for entry in source.rglob("*"):
                    if entry.is_symlink() or not entry.resolve().is_relative_to(source):
                        raise ValueError("Inherited skill package contains unsafe links")
                if source != sdir:
                    shutil.copytree(source, sdir, dirs_exist_ok=True)
            sdir.mkdir(parents=True, exist_ok=True)
            content = content or f"""---
name: {name}
description: {desc or "ADGS 进化继承的技能基因"}
---
# {name}

{desc or "（由 ADGS 基因重组继承）"}
"""
            temporary = sdir / (".SKILL." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(sdir / "SKILL.md")
        return True

    @staticmethod
    def get_skill(profile_name: str, name: str) -> str:
        """读取 profile 指定技能的 SKILL.md 全文（不存在返回 ""）"""
        sdir = _find_skill(profile_name, name)
        p = sdir / "SKILL.md" if sdir else None
        try:
            if p and p.exists():
                return p.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    @staticmethod
    def get_skill_package(profile_name: str, name: str) -> dict:
        """Full inheritable skill, including scripts/references/assets."""
        sdir = _find_skill(profile_name, name)
        if not sdir:
            return {}
        with _skill_write_lock(_skills_root(profile_name), sdir.name):
            return {"content": (sdir / "SKILL.md").read_text(encoding="utf-8"), "source_dir": str(sdir)}

    @staticmethod
    def delete_skill(profile_name: str, name: str) -> bool:
        """删除 profile 的指定技能目录（不存在视为成功）"""
        sdir = _find_skill(profile_name, name)
        try:
            with _skill_write_lock(_skills_root(profile_name), sdir.name if sdir else name):
                if sdir and sdir.exists():
                    shutil.rmtree(sdir)
            return True
        except Exception as e:
            print(f"[hermes] 删除技能 {profile_name}/{name} 失败: {e}")
            return False

    # ================================================================
    # 任务执行（kill+resume）
    # ================================================================

    def execute(self, instruction: str, model: str = None,
                live_log: Path = None, timeout: int = 3600,
                inactivity_timeout: int = 1800, cwd: str = None,
                execution_id: str = None, attempt_id: str = None,
                run_id: str = None, task_id: str = None, cancel=None) -> Dict:
        """Execute one turn in an owned terminal; quiet output is never liveness."""
        from core.terminal_runtime import run_hermes
        return run_hermes(self.profile, instruction, model=model, live_log=live_log,
                          timeout=timeout, inactivity_timeout=inactivity_timeout,
                          cwd=cwd, execution_id=execution_id, attempt_id=attempt_id,
                          run_id=run_id, task_id=task_id, cancel=cancel)

    def resume(self, instruction: str, session_id: str, model: str = None,
               live_log: Path = None, timeout: int = 3600,
               inactivity_timeout: int = 1800, cwd: str = None,
               execution_id: str = None, attempt_id: str = None,
               run_id: str = None, task_id: str = None, cancel=None) -> Dict:
        """Resume the exact persisted session, with a new separately logged attempt."""
        from core.terminal_runtime import run_hermes
        return run_hermes(self.profile, instruction, model=model, live_log=live_log,
                          timeout=timeout, inactivity_timeout=inactivity_timeout,
                          cwd=cwd, session_id=session_id, execution_id=execution_id,
                          attempt_id=attempt_id, run_id=run_id, task_id=task_id, cancel=cancel)

    # ================================================================
    # Profile 生命周期（静态方法）
    # ================================================================

    @staticmethod
    def create(name: str, clone_from: str = None) -> bool:
        """创建新 profile（可克隆）"""
        cmd = [hermes_executable(), "profile", "create", name]
        if clone_from:
            cmd += ["--clone-from", clone_from]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def setup_newborn(profile_name: str, config: dict) -> bool:
        """Atomically configure one newborn without spawning config CLI processes."""
        worker = config.get("models", {}).get("hermes_default", {})
        return _configure_profile(profile_name, worker, overwrite_model=True)

    @staticmethod
    def _read_model_default(profile_name: str) -> str:
        """直接读 profile 的 config.yaml 里 model.default（轻量，不起子进程）"""
        cfg = PROFILES_DIR / profile_name / "config.yaml"
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        m = re.search(r"^\s*default:\s*(\S+)", text, re.MULTILINE)
        return m.group(1) if m else ""

    @staticmethod
    def ensure_model(profile_name: str, worker_cfg: dict) -> bool:
        """Explicit model migration; ordinary ensure_profile preserves its snapshot."""
        return _configure_profile(profile_name, worker_cfg, overwrite_model=True)

    @staticmethod
    def ensure_provider_map(profile_name: str) -> bool:
        return _configure_profile(profile_name, {}, overwrite_model=False)

    @staticmethod
    def create_temp_clone(parent_name: str) -> Optional[str]:
        """临时克隆 profile 用于并行（C1 场景）：{parent}_tmp_{8hex}"""
        tmp_name = f"{parent_name}_tmp_{uuid.uuid4().hex[:8]}"
        ok = HermesInstance.create(tmp_name, clone_from=parent_name)
        return tmp_name if ok else None

    @staticmethod
    def delete_temp(temp_name: str):
        """删除临时克隆 profile（并行结束清理）"""
        pdir = PROFILES_DIR / temp_name
        try:
            if pdir.exists():
                shutil.rmtree(pdir)
        except Exception as e:
            print(f"[hermes] 删除临时 profile {temp_name} 失败: {e}")

    @staticmethod
    def delete(profile_name: str):
        """删除 profile"""
        pdir = PROFILES_DIR / profile_name
        try:
            if pdir.exists():
                shutil.rmtree(pdir)
        except Exception:
            pass


ANYSEARCH_DESC = (
    "通用网络搜索工具（anysearch API）。任务需要查询最新信息、文档、"
    "代码示例、教程、新闻或任何实时网络内容时使用。支持 tag（文档/代码/"
    "新闻/学术等）和 params（按库/框架过滤）过滤。无认证即可用。"
)


def ensure_profile(profile_name: str, worker_cfg: dict,
                   anysearch_src: Path = None) -> bool:
    """Provision once; share the same durable profile across concurrent sessions."""
    directory = PROFILES_DIR / profile_name
    if _profile_ready(profile_name):
        return True
    # Short provisioning lock only; NEVER held during task execution.
    with _skill_write_lock(PROFILES_DIR.parent, "profile-" + profile_name):
        if _profile_ready(profile_name):
            return True
        existed = directory.exists()
        if not existed and not HermesInstance.create(profile_name):
            return False
        if not _configure_profile(profile_name, worker_cfg, overwrite_model=not existed):
            return False
        if not existed and anysearch_src and anysearch_src.is_file():
            HermesInstance.install_skill(profile_name, "anysearch", ANYSEARCH_DESC,
                                         content=anysearch_src.read_text(encoding="utf-8"))
        return _profile_ready(profile_name)
