"""ADGS v3 节点注册表 — 活动池 + 存档池，JSON 持久化，线程安全

节点 = 智能体(Leader) + hermes(执行者) 的组合体。
每个节点有：
  - id: 唯一标识 (ags_1, ags_2, ...)
  - describe: 文本描述（任务路由向量匹配用，初始统一，任务后分化）
  - success_rate: 成功率（跟着节点走，EMA 平滑）
  - score: 历史累计打分
  - gen: 代数（初始=0，繁衍子代=亲代+1）
  - birth_type: initial / recombine / whiteboard
  - parents: 亲代 id 列表（重组有 2 个，白板为空）
  - status: active / archived
"""
import json
import threading
import time
import math
from pathlib import Path
from typing import Dict, List, Optional

from config.config import config

REGISTRY_PATH = config.PROJECT_ROOT / "data" / "registry.json"
ARCHIVE_DIR = config.PROJECT_ROOT / "data" / "archive"
GENERALIST_DESCRIPTION = ("通用智能体节点：具备完整的任务规划、代码编写、测试验证与文档输出能力，"
                          "可执行各类开发与研究任务。")


class Node:
    def __init__(self, node_id: str, describe: str, gen: int = 0,
                 birth_type: str = "initial", parents: Optional[List[str]] = None,
                 success_rate: float = 0.5, score: float = 50.0):
        self.id = node_id
        self.describe = describe
        self.gen = gen
        self.birth_type = birth_type          # initial/recombine/whiteboard
        self.parents = parents or []
        self.success_rate = success_rate      # 初始 0.5（无先验）
        self.score = score                    # 初始 50（中性分）
        self.tasks_done = 0                   # 完成任务数
        self.status = "active"
        self.created_at = time.time()
        self.updated_at = time.time()
        self.archived_at = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "describe": self.describe,
            "gen": self.gen,
            "birth_type": self.birth_type,
            "parents": self.parents,
            "success_rate": round(self.success_rate, 4),
            "score": round(self.score, 2),
            "tasks_done": self.tasks_done,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        n = cls(d["id"], d["describe"], d.get("gen", 0),
                d.get("birth_type", "initial"), d.get("parents", []),
                d.get("success_rate", 0.5), d.get("score", 50.0))
        # 迁移：旧数据 score=0 且从未做过任务（tasks_done=0）→ 视为中性 50
        # （0 分是历史 bug 产物；先读 tasks_done 再判断，避免恒为默认 0）
        tasks_done = d.get("tasks_done", 0)
        if n.score == 0 and tasks_done == 0:
            n.score = 50.0
        n.tasks_done = tasks_done
        n.status = d.get("status", "active")
        n.created_at = d.get("created_at", time.time())
        n.updated_at = d.get("updated_at", time.time())
        n.archived_at = d.get("archived_at")
        return n


class Registry:
    """活动池 + 存档池管理"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else REGISTRY_PATH
        self._lock = threading.RLock()
        self._nodes: Dict[str, Node] = {}
        self._seq = 0
        self._credits = {}
        self.load()

    # ---------------- 持久化 ----------------

    def load(self):
        with self._lock:
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    self._nodes = {k: Node.from_dict(v) for k, v in data.get("nodes", {}).items()}
                    self._seq = data.get("seq", 0)
                    self._credits = data.get("credits", {})
                    return
                except Exception as e:
                    # 数据损坏：备份损坏文件，再从存档恢复，绝不静默清空
                    print(f"[registry] 加载失败({e})，尝试备份+恢复...")
                    import shutil, time as _t
                    try:
                        bak = self.path.with_suffix(
                            f".corrupt_{int(_t.time())}.json")
                        shutil.copy2(self.path, bak)
                        print(f"[registry] 损坏文件已备份到 {bak}")
                    except Exception:
                        pass
                    self._restore_from_archive()
                    return
            self._nodes = {}
            self._seq = 0

    def _restore_from_archive(self):
        """从存档目录恢复节点（取每个 id 的最新存档）"""
        import glob, re as _re
        files = glob.glob(str(ARCHIVE_DIR / "*.json"))
        latest = {}
        for f in files:
            try:
                d = json.loads(open(f, encoding="utf-8").read())
                nid = d.get("id", "")
                m = _re.search(r"_(\d+)\.json$", f)
                ts = int(m.group(1)) if m else 0
                if nid and (nid not in latest or ts > latest[nid][0]):
                    latest[nid] = (ts, d)
            except Exception:
                continue
        restored = 0
        for nid, (_, data) in latest.items():
            try:
                n = Node.from_dict(data)
                # Recovery preserves archival status; it is not a resurrection request.
                self._nodes[nid] = n
                restored += 1
            except Exception:
                continue
        if restored:
            self._seq = max((int(x.split("_")[1]) for x in self._nodes
                             if "_" in x and x.split("_")[1].isdigit()), default=0)
            self.save()
            print(f"[registry] 从存档恢复 {restored} 个节点")
        else:
            print("[registry] 存档为空，重建空 registry")

    def save(self):
        # 2026-08-30 修复（P2-12）：原子写——先写临时文件再 rename，
        # 掉电/崩溃不会留下截断的 registry.json（423KB 直接 write_text
        # 覆盖，写一半断电即损坏；损坏恢复兜底只能找回已存档节点）。
        from core.contracts import utf8_safe
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "seq": self._seq,
                "nodes": {k: v.to_dict() for k, v in self._nodes.items()},
                "credits": self._credits,
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                utf8_safe(json.dumps(data, ensure_ascii=False, indent=2)), encoding="utf-8")
            tmp.replace(self.path)  # Windows 上 replace 覆盖是原子的

    # ---------------- 节点操作 ----------------

    def next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"ags_{self._seq}"

    def add(self, node: Node):
        with self._lock:
            self._nodes[node.id] = node
            self.save()

    def get(self, node_id: str) -> Optional[Node]:
        with self._lock:
            return self._nodes.get(node_id)

    def remove(self, node_id: str):
        with self._lock:
            self._nodes.pop(node_id, None)
            self.save()

    def active(self) -> List[Node]:
        with self._lock:
            return [n for n in self._nodes.values() if n.status == "active"]

    def archived(self) -> List[Node]:
        with self._lock:
            return [n for n in self._nodes.values() if n.status == "archived"]

    def count(self) -> int:
        with self._lock:
            return len([n for n in self._nodes.values() if n.status == "active"])

    def update(self, node_id: str, **kwargs):
        with self._lock:
            n = self._nodes.get(node_id)
            if not n:
                return
            for k, v in kwargs.items():
                setattr(n, k, v)
            n.updated_at = time.time()
            self.save()

    def record_result(self, node_id, passed, score_delta, credit_id, alpha=0.2, revise=False):
        """One atomic, idempotent credit per execution; concurrent sessions add deltas."""
        if type(passed) is not bool or not math.isfinite(float(score_delta)):
            raise ValueError("invalid judged result")
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return None
            key = f"{node_id}:{credit_id}"
            previous = self._credits.get(key)
            if previous is not None:
                if not revise or not isinstance(previous, dict):
                    return node.to_dict()
                old_contribution = previous.get("applied_delta", previous["score_delta"])
                without_previous = max(0.0, node.score - old_contribution)
                node.score = round(max(0.0, without_previous + float(score_delta)), 2)
                elapsed = max(0, node.tasks_done - previous["task_number"])
                correction = alpha * (int(passed) - int(previous["passed"])) * (1 - alpha) ** elapsed
                node.success_rate = round(min(1.0, max(0.0, node.success_rate + correction)), 4)
                node.updated_at = time.time()
                previous.update(passed=passed, score_delta=float(score_delta),
                                applied_delta=node.score - without_previous, ts=node.updated_at)
                self.save()
                return node.to_dict()
            node.success_rate = round((1 - alpha) * node.success_rate + alpha * int(passed), 4)
            old_score = node.score
            node.score = round(max(0.0, node.score + float(score_delta)), 2)
            node.tasks_done += 1
            node.updated_at = time.time()
            self._credits[key] = {"passed": passed, "score_delta": float(score_delta),
                                  "applied_delta": node.score - old_score,
                                  "task_number": node.tasks_done, "ts": node.updated_at}
            self.save()
            return node.to_dict()

    # ---------------- 淘汰存档 ----------------

    def archive_node(self, node_id: str) -> dict:
        """淘汰节点：存档快照 + 移出活动池（不物理删除）"""
        with self._lock:
            n = self._nodes.get(node_id)
            if not n:
                return {}
            n.status = "archived"
            n.archived_at = time.time()
            snapshot = n.to_dict()
            # 写入存档目录
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            fname = ARCHIVE_DIR / f"{node_id}_archived_{int(n.archived_at)}.json"
            fname.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            self.save()
            return snapshot

    def revive_node(self, node_id: str) -> dict:
        """复活节点：archived → active（profile 与技能基因完整保留）

        淘汰只存档不删 hermes profile，因此复活无需重建 ——
        直接回到活动池即可继续接受任务（2026-08-28 用户需求）。
        """
        with self._lock:
            n = self._nodes.get(node_id)
            if not n:
                return {}
            if n.status != "archived":
                return {"unchanged": True}
            n.status = "active"
            n.archived_at = None
            n.updated_at = time.time()
            self.save()
            return n.to_dict()

    # ---------------- 初始化 ----------------

    def ensure_initial(self, describe: str, count: int = None):
        """首次运行：创建初始节点（全部统一描述，初始无区别）

        同时为每个初始节点创建对应的 hermes profile —— 否则节点执行时
        hermes 会报 "Profile 'ags_N' does not exist"（繁衍子代由
        evolution.py 创建 profile，初始节点在此补齐）。
        profile 已存在则跳过 create（幂等）；创建失败仅告警不中断。
        """
        count = count or config.initial_size
        with self._lock:
            if self.count() > 0:
                return False
            from core.hermes import ensure_profile
            _anysearch_src = config.PROJECT_ROOT / "skills" / "anysearch" / "SKILL.md"
            _worker_cfg = {
                "exec_model": config.worker_model,
                "base_url": config.worker_url,
                "api_key": config.worker_key,
            }
            for _ in range(count):
                node_id = self.next_id()
                n = Node(node_id, describe, gen=0, birth_type="initial")
                # 创建 hermes profile + 配置模型 + 装 anysearch（幂等，缺失才建）
                if ensure_profile(node_id, _worker_cfg, anysearch_src=_anysearch_src):
                    self._nodes[node_id] = n
                    print(f"[init] 节点 {node_id} 就绪（profile + anysearch 已装）")
                else:
                    print(f"[init] 创建节点 {node_id} hermes profile 失败（hermes 未装好？）")
            self.save()
            return self.count() > 0


registry = Registry()
