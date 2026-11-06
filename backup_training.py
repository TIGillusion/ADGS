#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADGS 训练状态 备份 / 迁移工具

ADGS 的"训练效果"分散在三处，迁移时必须一起带走，否则新机器上
每个节点执行时会被 ensure_profile() 重建为空 profile（技能基因与
长期记忆全丢）：

  1. data/registry.json                 —— 种群状态（描述/成功率/代数/亲代/任务数）
  2. data/archive/*.json                —— 淘汰节点存档快照
  3. .hermes/profiles/ags_*             —— 每节点 skills(技能基因) + memories(长期记忆)
                                          （项目内，由 HERMES_HOME 重定向而来）

用法一（独立命令行，在项目根用项目自带 python 跑）:

  备份（打包成 zip，默认只打包活动节点 + 精简缓存）:
    .venv\\Scripts\\python backup_training.py backup

  打包全部 ags_* profile（含已淘汰节点的孤儿 profile）:
    .venv\\Scripts\\python backup_training.py backup --all

  顺带打包 models/（目标机可完全离线，约 92MB）:
    .venv\\Scripts\\python backup_training.py backup --models

  完整保留 profile 里所有内容（含 cache/logs/sessions，体积最大）:
    .venv\\Scripts\\python backup_training.py backup --full-profile

  在目标机恢复（继承训练效果）:
    .venv\\Scripts\\python backup_training.py restore ADGS_state_xxx.zip

用法二（在 ADGS 交互终端里，等价快捷指令）:

  /backup             打包当前训练状态
  /backup --all --models
  /restore            交互式输入备份 zip 路径并恢复
  /restore D:\\path\\ADGS_state_xxx.zip
"""
import argparse
import shutil
import sys
import time
import zipfile
from pathlib import Path

from core.hermes import PROFILES_DIR  # 项目内 .hermes/profiles（与 core/hermes.py 一致）

# 项目根 = 本脚本所在目录
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = PROJECT_ROOT / "models"

# 精简模式下跳过的 profile 子目录/文件（可重建的运行痕迹，非训练成果）
SLIM_SKIP_DIRS = {
    "cache", "audio_cache", "image_cache", "logs", "lsp", "sandboxes",
    "sessions", "cron", "hooks", "pairing", "plans", "skins", "home",
    "workspace",
}
# 精简模式下保留的核心（其余文件一律跳过）
SLIM_KEEP_FILES = {
    "SOUL.md", "config.yaml", ".env", "state.db",
    ".skills_prompt_snapshot.json", "auth.lock", "processes.json",
}


def load_registry():
    """读取 registry.json，返回 {活动节点 id 集合, 全部节点 id 集合}"""
    reg = DATA_DIR / "registry.json"
    if not reg.exists():
        print(f"[!] 未找到 {reg}，将只打包 profiles（无种群状态）")
        return set(), set()
    import json
    data = json.loads(reg.read_text(encoding="utf-8"))
    nodes = data.get("nodes", {})
    active = {k for k, v in nodes.items() if v.get("status") == "active"}
    return active, set(nodes.keys())


def iter_profile_ids(all_profiles: bool) -> list:
    """确定要打包的 profile id 列表"""
    if all_profiles:
        return sorted(p.name for p in PROFILES_DIR.glob("ags_*") if p.is_dir())
    active, _ = load_registry()
    return sorted(active)


def _add_dir_to_zip(zf: zipfile.ZipFile, src: Path, arcroot: str,
                    slim: bool, full_profile: bool):
    """把目录写入 zip；arcroot 为 zip 内前缀"""
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        parts = rel.parts
        # 精简模式：跳过缓存目录与非常规文件
        if slim and not full_profile:
            if len(parts) > 1 and parts[0] in SLIM_SKIP_DIRS:
                continue
            if len(parts) == 1 and parts[0] not in SLIM_KEEP_FILES:
                continue
        arcname = f"{arcroot}/{rel.as_posix()}"
        zf.write(f, arcname)


def do_backup(output_dir=None, all_profiles=False, include_models=False,
              full_profile=False, slim=True) -> str:
    """打包训练状态为 zip，返回产物路径字符串。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else (DATA_DIR / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ADGS_state_{ts}.zip"

    ids = iter_profile_ids(all_profiles)
    if not ids:
        print("[!] 没有可打包的 profile（profiles 目录缺失或 registry 无活动节点）")
        sys.exit(1)

    print(f"[1/4] 收集节点 profile: {len(ids)} 个 "
          f"({'全部' if all_profiles else '仅活动'}，"
          f"slim={'关' if full_profile else '开'})")
    missing = [i for i in ids if not (PROFILES_DIR / i).exists()]
    if missing:
        print(f"[!] 以下 profile 不存在（将被跳过）: {missing}")
    ids = [i for i in ids if (PROFILES_DIR / i).exists()]

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # ① 种群状态
        if (DATA_DIR / "registry.json").exists():
            zf.write(DATA_DIR / "registry.json", "registry.json")
            print(f"[2/4] 已加入 registry.json")
        # ② 淘汰存档
        arch = DATA_DIR / "archive"
        if arch.exists():
            _add_dir_to_zip(zf, arch, "archive", slim=False, full_profile=True)
        # ③ 节点 profile
        for i, pid in enumerate(ids, 1):
            _add_dir_to_zip(zf, PROFILES_DIR / pid, f"profiles/{pid}",
                            slim=slim, full_profile=full_profile)
            if i % 10 == 0 or i == len(ids):
                print(f"       profiles: {i}/{len(ids)}")
        # 可选：models
        if include_models and MODELS_DIR.exists():
            print("[3/4] 打包 models/ ...")
            _add_dir_to_zip(zf, MODELS_DIR, "models", slim=False, full_profile=True)

    sz = out_path.stat().st_size / (1024 * 1024)
    print(f"[4/4] 完成 -> {out_path}")
    print(f"      大小 {sz:.1f} MB，节点 {len(ids)} 个")
    return str(out_path)


def do_restore(zip_path, project_root=None, profiles_dir=None) -> dict:
    """从备份 zip 恢复训练状态到本机，返回恢复摘要 dict。

    profiles_dir 默认取项目内 .hermes/profiles（core/hermes.py 的 PROFILES_DIR），
    测试时可覆盖。
    """
    zp = Path(zip_path)
    if not zp.exists():
        print(f"[!] 找不到 {zp}")
        sys.exit(1)

    # 目标项目根：默认脚本所在目录，可 --project 覆盖
    proj = Path(project_root) if project_root else PROJECT_ROOT
    dst_data = proj / "data"
    dst_archive = dst_data / "archive"
    dst_profiles = Path(profiles_dir) if profiles_dir else PROFILES_DIR

    summary = {"registry": False, "archive": 0, "profiles": [], "models": False}

    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
        print(f"[1/4] 解压 {zp.name}（{len(names)} 个文件）...")

        # ① registry.json（覆盖前备份旧文件）
        if "registry.json" in names:
            dst_data.mkdir(parents=True, exist_ok=True)
            old = dst_data / "registry.json"
            if old.exists():
                bak = dst_data / f"registry_prev_{int(time.time())}.json"
                shutil.copy2(old, bak)
                print(f"      已备份旧 registry -> {bak.name}")
            zf.extract("registry.json", dst_data)
            summary["registry"] = True
            print(f"[2/4] registry.json 已恢复")

        # ② archive（合并，不覆盖）
        arch_files = [n for n in names
                      if n.startswith("archive/") and not n.endswith("/")]
        if arch_files:
            dst_archive.mkdir(parents=True, exist_ok=True)
            for n in arch_files:
                zf.extract(n, dst_data)
            summary["archive"] = len(arch_files)
            print(f"[3/4] archive 已恢复 {len(arch_files)} 个文件")

        # ③ profiles（zip 内 "profiles/ags_N/..." → dst_profiles/ags_N/...）
        profile_files = [n for n in names
                         if n.startswith("profiles/") and not n.endswith("/")]
        profile_ids = sorted({n.split("/")[1] for n in profile_files
                              if len(n.split("/")) > 1})
        if profile_files:
            dst_profiles.mkdir(parents=True, exist_ok=True)
            for pid in profile_ids:
                if (dst_profiles / pid).exists():
                    shutil.rmtree(dst_profiles / pid)
            prefix = "profiles/"
            for n in profile_files:
                target = dst_profiles / n[len(prefix):]
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(n) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            summary["profiles"] = profile_ids
            print(f"[4/4] profiles 已恢复 {len(profile_ids)} 个 -> {dst_profiles}")

        # ④ models（可选）
        model_files = [n for n in names if n.startswith("models/")]
        if model_files:
            for n in model_files:
                zf.extract(n, proj)
            summary["models"] = True
            print(f"      models 已恢复 -> {proj / 'models'}")

    return summary


def main():
    ap = argparse.ArgumentParser(description="ADGS 训练状态备份/迁移")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help="打包训练状态为 zip")
    b.add_argument("--all", action="store_true", help="打包全部 ags_*（含已淘汰孤儿 profile）")
    b.add_argument("--models", action="store_true", help="顺带打包 models/（embedding 模型）")
    b.add_argument("--full-profile", action="store_true",
                   help="完整保留 profile（含 cache/logs/sessions，体积大）")
    b.add_argument("--slim", action="store_true", default=True,
                   help="精简 profile（默认，跳过缓存/日志/会话）")
    b.add_argument("-o", "--output", default=None, help="输出目录（默认 data/backups/）")

    r = sub.add_parser("restore", help="从 zip 恢复训练状态到本机")
    r.add_argument("zip", help="备份 zip 路径")
    r.add_argument("--project", default=None, help="目标项目根（默认脚本所在目录）")

    args = ap.parse_args()
    if args.cmd == "backup":
        do_backup(args.output, args.all, args.models, args.full_profile, args.slim)
    else:
        do_restore(args.zip, args.project)


if __name__ == "__main__":
    main()
