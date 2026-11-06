# 全量迁移：hermes 节点 model.provider  vllm → opencode（伪装，2026-09-04）
#
# 用户要求：把系统里所有 hermes 节点 provider 名改成 opencode 系，
# 不改 base_url、不改行为。
#
# 为什么不能"只"改 model.provider 那一行：
#   hermes_cli/auth.py 的 provider 解析函数内有一张硬编码别名表
#   （"opencode": "opencode-zen"，函数内局部 dict，用户插件无法覆盖）。
#   裸把 provider 改成 "opencode" 会被劫持到内置 opencode-zen profile
#   （base_url=https://opencode.ai/zen/v1 + OPENCODE_ZEN_API_KEY）→ 请求
#   打到外部 opencode.ai 并 401（2026-09-04 实测复现），违反"不改 base_url"。
#
# 正确做法（hermes 官方"命名自定义 provider"机制，零改 hermes 源码）：
#   model.provider: opencode
#   providers:
#     opencode:
#       base_url: <该 profile 原 model.base_url>   # 不改，原样镜像
#       api_key:  <该 profile 原 model.api_key>    # 不改，原样镜像
#   运行时 resolve_runtime_provider() 先走 _resolve_named_custom_runtime()：
#   _get_named_custom_provider("opencode") 命中 providers.opencode 条目，
#   且 resolve_provider("opencode")→"opencode-zen"≠"opencode" 不触发内置让位，
#   于是路由到本条目（provider 归一为 custom，base_url/api_key 用镜像值），
#   最终 agent.log 显示 provider=custom base_url=<vLLM 端点>，行为与 vllm 完全一致。
#   （已用 tests/smoke_hermes.py 真实执行验证：hello.txt 正常生成。）
#
# 安全性：
#   - 仅处理 `model:` 段下 2 空格缩进的 `  provider: vllm` + 空 `providers: {}`。
#   - base_url/api_key 从每个 profile 自身 model 段读取后镜像，不硬编码。
#   - 迁移前把原 vllm 版 config.yaml 备份到 profiles-backup-opencode-20260904/。
#   - 幂等：已是 opencode 的跳过。

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / ".hermes" / "profiles"
BACKUP = ROOT / ".hermes" / "profiles-backup-opencode-20260904"

OLD_PROVIDER = "  provider: vllm"
NEW_PROVIDER = "  provider: opencode"
OLD_PROVS = "providers: {}"

def grab(text, field):
    m = re.search(rf"^\s{{2}}{field}:\s*(\S+)\s*$", text, re.M)
    return m.group(1) if m else ""

changed, already, skipped, no_url = 0, 0, 0, 0
for pdir in sorted(PROFILES.iterdir()):
    cfg = pdir / "config.yaml"
    if not pdir.is_dir() or not cfg.exists():
        continue
    text = cfg.read_text(encoding="utf-8")

    if NEW_PROVIDER in text:
        already += 1
        continue
    if OLD_PROVIDER not in text or OLD_PROVS not in text:
        skipped += 1
        print(f"SKIP   {pdir.name} (no vllm line / no empty providers block)")
        continue

    base_url = grab(text, "base_url")
    api_key = grab(text, "api_key")
    if not base_url or not api_key:
        no_url += 1
        print(f"SKIP   {pdir.name} (missing model.base_url/api_key: url={base_url!r})")
        continue

    bdir = BACKUP / pdir.name
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg, bdir / "config.yaml")

    new_provs = (
        "providers:\n"
        f"  opencode:\n"
        f"    base_url: {base_url}\n"
        f"    api_key: {api_key}"
    )
    out = text.replace(OLD_PROVIDER, NEW_PROVIDER).replace(OLD_PROVS, new_provs)
    cfg.write_text(out, encoding="utf-8")
    changed += 1

print(f"\ndone: changed={changed} already_opencode={already} "
      f"skipped={skipped} missing_url={no_url}  backup->{BACKUP}")
