"""utils/hermes.py — Hermes CLI 封装（简版）

注意：节点任务执行的完整封装在 core/hermes.py（execute/resume/kill+resume、
双信号活动检测、session_id 提取）——本文件是遗留简版，当前无调用方。
"""

import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List


class HermesInstance:
    def __init__(self, profile_name: str):
        self.profile = profile_name

    def _run(self, args: list, timeout: int = 30) -> subprocess.CompletedProcess:
        """短命令（配置/健康检查），非任务执行"""
        return subprocess.run(
            ["hermes", "--yolo", "--profile", self.profile] + args,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)

    def config_set(self, key: str, value: str) -> bool:
        try:
            r = self._run(["config", "set", key, value])
            return r.returncode == 0
        except Exception:
            return False

    def config_show(self) -> str:
        try:
            r = self._run(["config", "show"])
            return r.stdout
        except Exception:
            return ""

    def is_healthy(self) -> bool:
        try:
            r = self._run(["config", "show"], timeout=20)
            return r.returncode == 0
        except Exception:
            return False

    def execute(self, instruction: str, model: str = None,
                live_log: Path = None, timeout: int = 600,
                inactivity_timeout: int = 120) -> Dict:
        """
        执行任务：hermes --yolo chat -Q -q <instruction>
        返回 {"output": str, "exit_code": int, "error": str}

        基于无活动超时：live.log 不再增长才算超时。
        """
        cmd = ["hermes", "--yolo", "--profile", self.profile]
        if model:
            cmd += ["-m", model]
        cmd += ["chat", "-Q", "-q", instruction]

        # 写 live.log 头部
        if live_log:
            live_log.parent.mkdir(parents=True, exist_ok=True)
            with open(str(live_log), 'w', encoding='utf-8') as f:
                f.write(f"=== Profile: {self.profile} ===\n")
                if model:
                    f.write(f"=== Model: {model} ===\n")
                f.write("\n")

        stdout_chunks = []
        stderr_chunks = []

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace'
            )

            # 后台线程读 stderr
            def _read_stderr():
                for line in proc.stderr:
                    stderr_chunks.append(line)
                    if live_log:
                        with open(str(live_log), 'a', encoding='utf-8') as f:
                            f.write(f"[brain] {line}"); f.flush()
            t_err = threading.Thread(target=_read_stderr, daemon=True)
            t_err.start()

            # 后台线程读 stdout
            def _read_stdout():
                for line in proc.stdout:
                    stdout_chunks.append(line)
                    if live_log:
                        with open(str(live_log), 'a', encoding='utf-8') as f:
                            f.write(line); f.flush()
            t_out = threading.Thread(target=_read_stdout, daemon=True)
            t_out.start()

            # 主线程监控超时
            start = time.time()
            last_active = start
            last_size = live_log.stat().st_size if live_log and live_log.exists() else 0

            while proc.poll() is None:
                time.sleep(3)
                now = time.time()

                # 总墙钟超时
                if now - start > timeout:
                    proc.kill()
                    t_out.join(2); t_err.join(2)
                    return {"output": "".join(stdout_chunks), "exit_code": -1,
                            "error": f"Wall timeout after {timeout}s"}

                # 无活动超时（live.log 不再增长）
                if live_log and live_log.exists():
                    cur = live_log.stat().st_size
                    if cur > last_size:
                        last_size = cur
                        last_active = now

                if now - last_active > inactivity_timeout:
                    proc.kill()
                    t_out.join(2); t_err.join(2)
                    return {"output": "".join(stdout_chunks), "exit_code": -1,
                            "error": f"Inactivity timeout ({inactivity_timeout}s)"}

            # 正常退出
            t_out.join(3); t_err.join(3)

        except Exception as e:
            return {"output": "", "exit_code": -1, "error": str(e)}

        stdout = "".join(stdout_chunks)
        if proc.returncode != 0:
            stderr_text = "".join(stderr_chunks[-100:])
            return {"output": stdout, "exit_code": proc.returncode,
                    "error": stderr_text[:500]}
        return {"output": stdout, "exit_code": 0, "error": ""}

    # ================================================================
    # Profile 管理（静态方法，不绑定实例）
    # ================================================================

    @staticmethod
    def create(name: str, clone_from: str = None) -> bool:
        """创建新 profile（可克隆现有）"""
        cmd = ["hermes", "--yolo", "profile", "create", name]
        if clone_from:
            cmd += ["--clone", clone_from]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def setup_newborn(profile_name: str, config: dict):
        """新出生 profile 的最小配置注入"""
        h = HermesInstance(profile_name)
        h.config_set("model.provider", config.get("provider", ""))
        h.config_set("model.model", config.get("model", ""))
        h.config_set("model.base_url", config.get("base_url", ""))
        h.config_set("model.api_key", config.get("api_key", ""))
