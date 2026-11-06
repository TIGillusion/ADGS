"""ADGS v3 node.py — NODE 闭环

节点 = 智能体(Leader) + HermesInstance(执行者)
流程：
  接任务(markdown) → 智能体决策(ask/plan/split) → hermes 执行 → 验收打分
  → 成功率 EMA 更新 → 描述分化（skill 总结合并）→ 上报

并行：同一 AGS 共享持久 profile，每个任务使用独立 Hermes session 和执行记录。

并发控制：全局信号量限制活跃执行；MCP 等待下属时借出名额，返回前重新取得。
等待中的 Hermes 进程仍可存在，但不会额外占据模型执行名额。
此前每层 ThreadPoolExecutor 独立计数，嵌套委派（CEO 拆→节点再拆）叠加成
4+ 个并发（实测 4 个 yolo 进程并行，超出 vllm_concurrency=2）。
所有 hermes 执行入口（work / spawn_parallel / _run_child）统一 acquire。
"""
import threading
import logging
import time
import traceback
import uuid
import sqlite3
import re
import json
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

from config.config import config
from core.agent import AGENT
from core.hermes import HermesInstance, ensure_profile
from core.registry import Node, Registry, registry as _registry
from utils import embedding as emb
from web.event_bus import bus, event_scope, get_task_session
from core.execution_store import PROFILE_NOT_READY, WORKER_HOLD_REASON, execution_store, recovery_grant, worker_launch_on_hold
from core.contracts import task_section, evidence_excerpt
from core.delegation_runtime import owned_execution_slot
from core.scheduling import dependency_results, validate_dependencies, write_resources, validate_new_artifacts

TASK_DIR = config.PROJECT_ROOT / "data" / "tasks"
_ACTIVE_EXECUTIONS = {}
_ACTIVE_EXECUTIONS_LOCK = threading.Lock()
_MAINTENANCE_QUEUE = queue.Queue()
_MAINTENANCE_LOCK = threading.Lock()
_MAINTENANCE_STARTED = False


def active_cancel(execution_id):
    """返回活跃执行的取消事件（A2A 委派用它联动父执行取消），不存在返回 None。"""
    with _ACTIVE_EXECUTIONS_LOCK:
        return _ACTIVE_EXECUTIONS.get(execution_id)


def _maintenance_worker():
    while True:
        callback, snapshot, identifiers, store = _MAINTENANCE_QUEUE.get()
        try:
            store.put(identifiers["execution_id"], maintenance_state="running")
            with event_scope(**identifiers):
                callback(snapshot["evaluation"], snapshot["task_md"], snapshot["work_log"])
            store.put(identifiers["execution_id"], maintenance_state="completed")
        except Exception as exc:
            try:
                store.put(identifiers["execution_id"], maintenance_state="pending", maintenance_error=str(exc))
                bus.emit("node.maintenance", **identifiers,
                         detail=f"成果已验收，描述更新待恢复: {type(exc).__name__}")
            except Exception:
                pass
        finally:
            _MAINTENANCE_QUEUE.task_done()


class _Cancellation:
    def __init__(self, parent=None):
        self.own = threading.Event()
        self.parent = parent

    def set(self):
        self.own.set()

    def is_set(self):
        return self.own.is_set() or (self.parent is not None and self.parent.is_set())


def cancel_execution(execution_id):
    with _ACTIVE_EXECUTIONS_LOCK:
        event = _ACTIVE_EXECUTIONS.get(execution_id)
        if event:
            event.set()
    from core.terminal_runtime import terminal_store, cancel_attempt
    attempts = terminal_store.list_attempts(execution_id=execution_id)
    stopped = [cancel_attempt(a["attempt_id"]) for a in attempts
               if a["status"] in ("starting", "running", "stopping")]
    return bool(event) or any(stopped)

# 环境故障指纹（2026-09-29）：命中即视为不可抗（网络断/凭据失效/LLM 服务端故障），
# 非节点能力问题 → 豁免计分。只收执行器/LLM 客户端自身的报错文案，避免把任务
# 内容里恰好出现的词误判（如任务要求写一个含 "connection" 的文件）。
_INFRA_PATTERNS = (
    # utils/chat.py 对 LLM SDK 级失败的统一包装（worker/brain 任何一侧）
    "llm call failed",
    "__llm_error__",
    # 连接类（openai SDK / httpx / requests 文案）
    "connection error", "connection refused", "connection reset",
    "connection aborted", "api connection error", "apiconnectionerror",
    "connecterror", "network is unreachable",
    # DNS
    "getaddrinfo failed", "name or service not known",
    "temporary failure in name resolution",
    # TLS
    "certificate verify failed", "certificate_verify_failed", "ssl: certificate",
    # 凭据/鉴权（hermes CLI 与 LLM 网关）
    "no usable credentials", "autherror", "unauthorized", "invalid api key",
    # CLI initialization can publish a session ID before persisting a session.
    # A rejected model configuration needs repair, not a resume of that ID.
    "hermes couldn't start the model connection",
    # Provider admission rejected the request before execution. Repeating a
    # full correction contract makes an overflowing context even larger.
    "contextwindowexceedederror", "context window exceeded",
    # LLM 服务端故障
    "rate limit", "internal server error", "bad gateway", "service unavailable",
    "compression summary failed", "inference request expired while waiting for admission",
    "provider repeatedly failed during hermes request",
)

class DynamicSemaphore:
    """可热调上限的信号量（web 系统设置：并行上限保存后立即生效）。

    与 BoundedSemaphore 语义一致（内部以持有数计数），set_limit 可随时改上限：
    扩容立即放行等待者；缩容不抢占已持有者（不中断正在执行的 hermes），
    持有数自然回落到新上限后才放行新的 acquire。
    """

    def __init__(self, limit: int):
        self._cond = threading.Condition()
        self._limit = max(1, int(limit))
        self._held = 0

    def acquire(self, blocking: bool = True, timeout=None, cancel=None) -> bool:
        with self._cond:
            if cancel is not None and cancel.is_set():
                return False
            if not blocking and self._held >= self._limit:
                return False
            deadline = None if timeout is None else time.monotonic() + timeout
            while self._held >= self._limit:
                if cancel is not None and cancel.is_set():
                    return False
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(min(0.25, remaining) if remaining is not None else 0.25)
            self._held += 1
            return True

    def release(self):
        with self._cond:
            self._held = max(0, self._held - 1)
            self._cond.notify()

    def set_limit(self, limit: int):
        with self._cond:
            self._limit = max(1, int(limit))
            self._cond.notify_all()


# 全局并发信号量：同时执行的 hermes 进程 ≤ 生效并行度
# （max_workers_effective = min(max_workers, vllm_concurrency)，settings.yaml 默认 2）。
# 跨所有线程池/嵌套委派层共享，防止嵌套 ThreadPoolExecutor 叠加超并发。
# BUG 修复（2026-08-30）：此前误用 config.vllm_concurrency（=3）创建信号量，
# 导致 max_workers=2 时仍允许 3 个 hermes 并发执行（web 图谱同时出现 3 个
# 执行中节点）。并发槽应与派发层生效并行度一致。
# 2026-09-28：改 DynamicSemaphore，web 系统设置改并行上限后 resize 立即生效。
_EXEC_SEM = DynamicSemaphore(config.max_workers_effective)


def resize_exec_semaphore(n: int):
    """热调整全局并行上限（不中断已持有的槽，缩容在释放时回收）"""
    _EXEC_SEM.set_limit(n)


def _acquire_exec(node_id: str = None):
    """占一个 hermes 并发槽（阻塞等待）。

    node_id 给定时，等待期间发 node.status=waiting（前端图谱黄色等待态），
    拿到槽后不发 running（由节点 work 流程后续 hermes.start 触发）。
    """
    if node_id:
        bus.emit("node.status", node=node_id, status="waiting")
    return _EXEC_SEM.acquire()


def _release_exec():
    _EXEC_SEM.release()


class NODE:
    """一个节点的完整执行上下文（智能体 + hermes + 状态）"""

    def __init__(self, node_id: str, registry: Registry = None,
                 profile_name: str = None):
        self.node_id = node_id
        self.reg = registry or _registry
        node = self.reg.get(node_id)
        self.profile = profile_name or node_id
        # 智能体（Leader）：描述跟随注册表，每次决策前刷新
        self.agent = AGENT(config.brain_url, config.brain_model, config.brain_key,
                           node_id=node_id, describe=node.describe if node else "")
        self.hermes = HermesInstance(self.profile)

    # ================================================================
    # 任务执行闭环
    # ================================================================

    def _emit_terminal_status(self, task_id: str, passed: bool,
                              score: float = None, reworks: int = 0,
                              detail: str = "", infra: bool = False, outcome: str = None):
        """实例终态 node.status（done/failed），带 task 精确命中 node@task 实例。

        前端图谱据此把 status/score/passed 写回实例节点。所有闭环路径
        （正常验收 / 委派完成 / 早期失败 / 决策失败 / 异常中断）返回前
        必须发一次终态——否则实例永远卡在 running/delegating 瞬态
        （2026-09-03 用户报告：任务完成后部分节点仍显示"运行中"）。
        重复发终态无害（前端 upsert 幂等）。
        infra=True：环境故障导致的失败（不可抗，未计分），事件带 infra 字段
        供前端区分展示；status 仍为 failed（实例不能挂在瞬态）。
        """
        if not task_id:
            return
        data = {"node": self.node_id, "profile": self.profile,
                "status": "done" if passed else ("interrupted" if infra or outcome in ("protocol_error", "interrupted", "evaluation_unavailable", "blocked", "context_overflow") else "failed"),
                "outcome": outcome or ("passed" if passed else ("unjudged" if infra else "failed")),
                "passed": bool(passed), "reworks": reworks}
        if infra:
            data["infra"] = True
        if score is not None:
            data["score"] = score
        if detail:
            data["detail"] = detail[:300]
        data["task"] = task_id
        bus.emit("node.status", **data)

    def _infra_reason(self, work_log: dict) -> str:
        """扫描执行结果判断是否环境故障（网络/凭据/LLM 服务端），返回原因或 ""

        只在"执行确实没跑成"的前提下被调用（exit_code != 0 或输出极短），
        避免把任务正常完成但日志里恰好含相关词的情况误判。
        """
        hay = " ".join([
            str(work_log.get("error") or ""),
            str(work_log.get("output") or "")[-3000:],
        ]).lower()
        for p in _INFRA_PATTERNS:
            if p in hay:
                return p
        return ""

    def _prepare_execution(self, task_md, task_id, execution_id=None, run_id=None, timeout=None, workspace=None, depth=0):
        self._execution_id = execution_id or uuid.uuid4().hex
        self._work_task_id = task_id or self._execution_id
        self._run_id = run_id
        self._workspace = workspace or config.workspace
        self._delegation_depth = depth
        self._session_name = get_task_session()
        self._cancel = _Cancellation(getattr(self, "_parent_cancel", None))
        self._prior_evidence = ""
        self._credit_revision = False
        with _ACTIVE_EXECUTIONS_LOCK:
            if self._execution_id in _ACTIVE_EXECUTIONS:
                raise RuntimeError("同一个执行实例已在运行，不能重复写入会话")
            _ACTIVE_EXECUTIONS[self._execution_id] = self._cancel
        try:
            budget, _ = self._compute_timeouts(task_md, timeout)
            self._deadline = time.monotonic() + budget
            node = self.reg.get(self.node_id)
            self._score_base = float(node.score) if node else 50.0
            execution_store.put(self._execution_id, node_id=self.node_id, profile=self.profile,
                                task_id=self._work_task_id, run_id=run_id, task_md=task_md,
                                workspace=self._workspace, session_name=self._session_name, state="running", phase="decision",
                                deadline_at=time.time() + budget, decision_pending=True, depth=depth)
        except BaseException:
            # work()/continue_execution() enter their cleanup only AFTER this
            # preparation. A failed checkpoint must not keep a phantom owner.
            with _ACTIVE_EXECUTIONS_LOCK:
                if _ACTIVE_EXECUTIONS.get(self._execution_id) is self._cancel:
                    _ACTIVE_EXECUTIONS.pop(self._execution_id)
            raise

    def work(self, task_md: str, context: str = "", upper: str = "ceo",
             timeout: int = None, depth: int = 0, record_score: bool = True,
             task_id: str = None, execution_id: str = None, run_id: str = None,
             parent_cancel=None, workspace=None, parent_execution_id=None) -> dict:
        self._parent_cancel = parent_cancel
        self._prepare_execution(task_md, task_id, execution_id, run_id, timeout, workspace, depth=depth)
        with event_scope(execution_id=self._execution_id, run_id=run_id, task_id=self._work_task_id,
                         session=self._session_name):
            try:
                instruction_id = uuid.uuid4().hex
                execution_store.put(self._execution_id, interaction_round_id=instruction_id, upper=upper,
                                    parent_execution_id=parent_execution_id)
                bus.emit("graph.instruction", node=self.node_id, parent=upper,
                         instruction_id=instruction_id, context_mode="new", task_md=task_md,
                         parent_execution_id=parent_execution_id)
                result = self._work_inner(task_md, context, upper, timeout, depth,
                                          record_score, self._work_task_id)
                return self._save_result(result)
            except Exception as exc:
                self._emit_terminal_status(self._work_task_id, False, infra=True,
                                           detail=str(exc), outcome="interrupted")
                execution_store.put(self._execution_id, state="interrupted", error=str(exc),
                                    ended_at=time.time())
                raise
            finally:
                with _ACTIVE_EXECUTIONS_LOCK:
                    _ACTIVE_EXECUTIONS.pop(self._execution_id, None)

    def _save_result(self, result):
        result = dict(result or {})
        result.update(execution_id=self._execution_id, node_id=self.node_id, profile=self.profile,
                      task_id=self._work_task_id, run_id=self._run_id)
        result.setdefault("outcome", "passed" if result.get("success") else
                          ("waiting_user" if result.get("need_clarify") else
                           "unjudged" if result.get("unjudged") or result.get("infra_failure") else "failed"))
        result["hermes_session_id"] = result.get("session_id")
        execution_store.put(self._execution_id, result=result, state=result["outcome"],
                            session_id=result.get("session_id"), phase="finished",
                            error=result.get("error") or "",
                            leader_history=getattr(self.agent, "history", []), ended_at=time.time())
        if result.get("success"):
            try:
                self._schedule_maintenance(result)
            except Exception as exc:
                # The judged result is already durable. Optional learning must
                # never turn a passed delivery into interrupted/rework.
                logging.getLogger(__name__).exception("Background learning scheduling failed")
                try:
                    execution_store.put(self._execution_id, maintenance_state="pending",
                                        maintenance_error=f"{type(exc).__name__}: {exc}")
                except Exception:
                    logging.getLogger(__name__).exception("Could not save pending learning state")
        return result

    def _schedule_maintenance(self, result):
        """Learning must never delay delivering a judged result to the CEO."""
        global _MAINTENANCE_STARTED
        identifiers = {"execution_id": self._execution_id, "task_id": self._work_task_id,
                       "run_id": self._run_id, "session": getattr(self, "_session_name", None),
                       "node": self.node_id, "profile": self.profile}
        snapshot = {"evaluation": dict(result.get("evaluation") or {}),
                    "task_md": evidence_excerpt(result.get("task_md", ""), 1500),
                    "work_log": evidence_excerpt(result.get("work_log") or
                            self._summarize_children(result.get("children", [])), 5000)}
        execution_store.put(self._execution_id, maintenance_state="pending")
        _MAINTENANCE_QUEUE.put((self.evolve_describe, snapshot, identifiers, execution_store))
        with _MAINTENANCE_LOCK:
            if not _MAINTENANCE_STARTED:
                threading.Thread(target=_maintenance_worker, daemon=True, name="adgs-learning").start()
                _MAINTENANCE_STARTED = True

    def continue_execution(self, execution_id, instruction, *, task_id=None, run_id=None, timeout=None,
                           parent_cancel=None):
        saved = execution_store.get(execution_id)
        if not saved or saved.get("node_id") != self.node_id or saved.get("profile") != self.profile:
            return {"success": False, "outcome": "protocol_error", "unjudged": True,
                    "error": "执行不存在或不属于当前 AGS"}
        session_id = saved.get("session_id") or (saved.get("result") or {}).get("session_id")
        delegated = (saved.get("result") or {}).get("action") == "split_tasks"
        decision_only = False
        if not session_id and not delegated and saved.get("state") != "running":
            from core.terminal_runtime import terminal_store
            from core.execution_store import preworker_attempts_safe, preworker_resume_evidence
            # Legacy records have no flag. Require a known pre-worker failure
            # and no durable attempt before retrying the Leader decision.
            decision_only = (preworker_resume_evidence(saved) and
                preworker_attempts_safe(terminal_store.list_attempts(execution_id=execution_id)))
        if not session_id and not delegated and not decision_only:
            return {"success": False, "outcome": "protocol_error", "unjudged": True,
                    "error": "原执行未建立 Hermes 会话，不能猜测另一个 session"}
        original = saved["task_md"]
        task_md = original + "\n\n## 上层补充要求（保留原权限）\n" + instruction
        # Persist the revised contract so acceptance, rework and later resumes
        # all retain the requirements given to Hermes in this continuation.
        saved = {**saved, "task_md": task_md}
        self._parent_cancel = parent_cancel
        self._prepare_execution(task_md, task_id or saved["task_id"], execution_id,
                                run_id or saved.get("run_id"), timeout, saved.get("workspace"))
        self._credit_revision = True
        self._prior_evidence = (saved.get("result") or {}).get("work_log") or saved.get("work_log", "")
        self._workspace = saved.get("workspace") or config.workspace
        self._delegation_depth = saved.get('depth',0)
        self._session_name = saved.get("session_name") or self._session_name
        self.agent.history = saved.get("leader_history", [])
        grant = recovery_grant(execution_id)
        with event_scope(execution_id=execution_id, run_id=self._run_id, task_id=self._work_task_id,
                         session=self._session_name):
            try:
                if grant and grant.get("review_model") == "ceo":
                    # This NODE is local to the continued execution. Keep the
                    # unavailable worker provider configured for other profiles.
                    self.agent.chat.reconfigure(url=config.ceo_url, model=config.ceo_model, key=config.ceo_key)
                instruction_id = uuid.uuid4().hex
                execution_store.put(execution_id, interaction_round_id=instruction_id)
                bus.emit("graph.instruction", node=self.node_id, parent=saved.get("upper", "ceo"),
                         instruction_id=instruction_id, context_mode="continue", instruction=instruction,
                         task_md=task_md, parent_execution_id=saved.get("parent_execution_id"))
                if decision_only and grant:
                    execution_store.put(execution_id, decision_pending=False)
                    log = self._run_hermes(task_md, timeout)
                    result = self._complete_execution(task_md, log, "ceo", True)
                elif decision_only:
                    result = self._work_inner(task_md, "", "ceo", timeout, 0, True, self._work_task_id)
                elif delegated:
                    result = self._continue_delegation(saved, instruction)
                else:
                    # The original conversation already contains its contract.
                    # Reinjecting the entire cumulative task on every resume
                    # grows context quadratically and can prevent any tool work.
                    log = self._resume_hermes(self._continuation_instruction(task_md, instruction), session_id)
                    result = self._complete_execution(task_md, log, "ceo", True)
                return self._save_result(result)
            except Exception as exc:
                self._emit_terminal_status(self._work_task_id, False, infra=True,
                                           detail=str(exc), outcome="interrupted")
                execution_store.put(execution_id, state="interrupted", error=str(exc), ended_at=time.time())
                raise
            finally:
                with _ACTIVE_EXECUTIONS_LOCK:
                    _ACTIVE_EXECUTIONS.pop(execution_id, None)

    def _continuation_instruction(self, task_md, instruction):
        import hashlib
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        contract = (TASK_DIR / f"continuation-contract-{uuid.uuid4().hex}.md").resolve()
        contract.write_text(task_md, encoding="utf-8")
        execution_store.put(self._execution_id, contract_path=str(contract),
                            contract_sha256=hashlib.sha256(contract.read_bytes()).hexdigest())
        return ("【原会话续接：保留已完成成果，仅补缺项】\n"
                "总体目标、原权限和累计验收要求仍然有效。完整合同（含历史补充）保存在：\n"
                f"{contract}\n"
                "该合同只读，按需读取相关章节；上层仍以完整合同验收。"
                "不要重复输出整份任务书或重新执行已完成工作。\n\n"
                "【本次上层补充】\n" + instruction)

    def _unjudged(self, task_md, log, outcome, reason):
        self._emit_terminal_status(self._work_task_id, False, infra=True, outcome=outcome, detail=reason)
        return {"success": False, "unjudged": True, "infra_failure": outcome != "protocol_error",
                "outcome": outcome, "error": reason, "task_md": task_md,
                "work_log": log.get("output", ""), "session_id": log.get("session_id"),
                "termination_reason": log.get("termination_reason")}

    def _continue_delegation(self, saved, instruction):
        """Resume only unfinished children; the parent does not need a Hermes session."""
        from core.router import route
        previous = saved.get("result") or {}
        tasks, old_children = previous.get("subtasks") or [], previous.get("children") or []
        if not tasks or len(tasks) != len(old_children):
            return self._unjudged(saved["task_md"], {}, "protocol_error", "原委派记录缺少子执行合同")
        def submit(index, completed):
            child = dict(old_children[index])
            latest = execution_store.get(child.get("execution_id")) if child.get("execution_id") else None
            if latest and latest.get("result"):
                child.update(latest["result"])
            if child.get("success") and (child.get("evaluation") or {}).get("passed", True):
                return dict(child)
            child_id = child.get("execution_id")
            checkpoint = execution_store.get(child_id) if child_id else None
            profile = child.get("node_id") or child.get("profile") or (checkpoint or {}).get("node_id")
            extra = instruction + "\n\n" + self._depends_context(tasks[index], completed)
            budget = max(0, self._deadline - time.monotonic())
            if budget <= 0 or self._cancel.is_set():
                return {**child, "success": False, "unjudged": True, "outcome": "interrupted", "error": "委派预算耗尽或取消"}
            if checkpoint and profile and (checkpoint.get("session_id") or
                    (checkpoint.get("result") or {}).get("action") == "split_tasks"):
                result = NODE(profile, registry=self.reg).continue_execution(child_id, extra,
                    task_id=child.get("task_id"), run_id=self._run_id, timeout=budget,
                    parent_cancel=self._cancel)
            else:
                contract = child.get("task_md") or self._render_subtask("恢复原委派", tasks[index], saved["task_md"], index, "")
                routed = self.reg.get(profile) if profile else route(contract, reg=self.reg, verbose=False)
                if not routed:
                    return {**child, "success": False, "unjudged": True, "outcome": "blocked", "error": "无可用 AGS"}
                child_id = child_id or uuid.uuid4().hex
                task_id = child.get("task_id") or f"task-{uuid.uuid4().hex}"
                bus.emit("graph.dispatch", parent=f"{self.node_id}@{self._execution_id}", task=task_id,
                         task_id=task_id, execution_id=child_id, run_id=self._run_id,
                         node=routed.id, profile=routed.id, mode="session")
                result = NODE(routed.id, registry=self.reg).work(contract, context="已有成果保留。" + extra,
                    upper=self.node_id, timeout=budget, task_id=task_id, execution_id=child_id, run_id=self._run_id,
                    parent_cancel=self._cancel, workspace=self._workspace, parent_execution_id=self._execution_id)
            return {**child, **result, "name": tasks[index].get("name", "")}
        children = dependency_results(tasks, submit, config.max_workers_effective, workspace=self._workspace)
        for index, child in enumerate(children):
            children[index] = {**old_children[index], **child}
        if all(c.get("success") for c in children) and (previous.get("evaluation") or {}).get("failed_items"):
            repair = saved["task_md"] + "\n\n## 已完成成果（保留，禁止重做）\n" + self._summarize_children(children)
            repair += "\n\n## 仅补齐总体验收缺项\n" + json.dumps(previous["evaluation"]["failed_items"], ensure_ascii=False)
            log = self._run_hermes(repair + "\n\n" + instruction)
            result = self._complete_execution(saved["task_md"], log, "ceo", True)
            result["children"] = children
            return result
        evaluation = self._evaluate_delegation_and_update(children, saved["task_md"])
        success = bool(evaluation.get("passed")) and not evaluation.get("unjudged")
        outcome = "passed" if success else ("unjudged" if evaluation.get("unjudged") else "failed")
        bus.emit("node.eval", node=self.node_id, profile=self.profile, **evaluation)
        self._emit_terminal_status(self._work_task_id, success, score=evaluation.get("score"),
                                   infra=bool(evaluation.get("unjudged")), outcome=outcome)
        return {"success": success, "action": "split_tasks", "outcome": outcome,
                "children": children, "subtasks": tasks, "task_md": saved["task_md"],
                "evaluation": evaluation, "unjudged": bool(evaluation.get("unjudged"))}

    def _work_inner(self, task_md: str, context: str = "", upper: str = "ceo",
                    timeout: int = None, depth: int = 0, record_score: bool = True,
                    task_id: str = None) -> dict:
        node = self.reg.get(self.node_id)
        if not node:
            return self._unjudged(task_md, {}, "interrupted", "节点不存在")
        if self._cancel.is_set():
            return self._unjudged(task_md, {}, "interrupted", "执行已取消")
        if config.node_merged_brain:
            # A2A 合并模式：worker(hermes) 直接承担拆分/委派/验收决策，
            # 种群协作经 MCP 工具(list_nodes/delegate_to_node/wait/verdict)。
            return self._work_merged(task_md, upper, record_score, task_id, timeout)
        self.agent.describe = node.describe
        self.agent.reset_history()
        self.agent.append_history("user", "[原始任务合同]\n" + task_md)
        decision_args = {"context": context} if context else {}
        if isinstance(self.agent, AGENT):
            decision_args["timeout"] = max(0, self._deadline - time.monotonic())
        decision = self.agent.decide(task_md, **decision_args)
        bus.emit("node.brain", node=self.node_id, profile=self.profile, action=decision.get("action"),
                 detail=str(decision.get("reason") or decision.get("question") or decision.get("goal") or "")[:1000])
        if decision.get("action") == "ask_upper":
            return {"success": False, "need_clarify": True, "outcome": "waiting_user",
                    "action": "ask_upper", "question": decision.get("question"),
                    "need_info": decision.get("need_info", []), "task_md": task_md}
        if decision.get("action") == "split_tasks":
            tasks = decision.get("subtasks") or []
            try:
                validate_dependencies(tasks)
            except ValueError as exc:
                return self._unjudged(task_md, {}, "protocol_error", str(exc))
            if depth < config.max_delegation_depth:
                execution_store.put(self._execution_id, phase="delegation", decision_pending=False)
                result = self._delegate(decision, task_md, upper, timeout, depth)
                # Preflight failures (for example an invalid child write scope)
                # are already recorded by _unjudged. Do not feed the empty
                # children list into _evaluate_delegation, which used to mask
                # the real error as the misleading "无有效子任务".
                if result.get("outcome") == "protocol_error" and not result.get("children"):
                    return result
                evaluation = self._evaluate_delegation_and_update(result.get("children", []), task_md) if record_score else {}
                result["evaluation"] = evaluation
                if record_score:
                    result["success"] = bool(evaluation.get("passed")) and not evaluation.get("unjudged")
                    result["outcome"] = "passed" if result["success"] else "failed"
                if evaluation.get("unjudged"):
                    result.update(unjudged=True, infra_failure=True, outcome="unjudged")
                bus.emit("node.eval", node=self.node_id, profile=self.profile, **evaluation)
                self._emit_terminal_status(task_id, bool(result.get("success")), score=evaluation.get("score"),
                                           infra=bool(evaluation.get("unjudged")),
                                           detail=evaluation.get("detail", "委派完成"), outcome=result.get("outcome"))
                return result
            decision = {"action": "plan_hermes", "task_md": self._merge_subtasks(task_md, tasks)}
        if decision.get("action") != "plan_hermes":
            return self._unjudged(task_md, {}, decision.get("outcome", "protocol_error"),
                                  str(decision.get("detail", "决策格式不可用")))
        task_md_final = task_md + "\n\n## Leader 执行计划（不能覆盖原始权限与验收）\n" + decision.get("task_md", "")
        task_md_final += "\n\n技能固化只在原始权限允许时进行；不得扩大权限或省略交付验收。"
        execution_store.put(self._execution_id, decision_pending=False)
        self.agent.append_history("assistant", "[执行计划]\n" + evidence_excerpt(task_md_final, 3000))
        self.agent.compress_history_if_needed()
        log = self._run_hermes(task_md_final, timeout)
        return self._complete_execution(task_md, log, upper, record_score, timeout)

    # ================================================================
    # A2A 合并模式（merged_brain）：worker 直接决策拆分与委派
    # ================================================================

    def _a2a_section(self) -> str:
        """任务书附加段：告知 worker 它的种群身份与协作工具（MCP: adgs-mesh）。"""
        depth = getattr(self,'_delegation_depth',0)
        depth_policy = (f'当前深度 {depth}/{config.max_delegation_depth}，已到委派深度上限：不要再发新委派，执行本层工作并如实汇报。'
                        if depth >= config.max_delegation_depth else
                        f'当前深度 {depth}/{config.max_delegation_depth}，可以继续向下委派；子节点同样有拆分和委派权。')
        return f"""## 种群协作（A2A，节点身份：{self.node_id}，你的执行 ID：{self._execution_id}）

你是 ADGS 节点种群的一员，拥有完全的主观能动性：调查、选人、拆分、委派、验收
由你自主决策。你首先是这份任务的负责人，需要选择合适的分工，而不是默认把所有工作塞进自己的一次会话。
{depth_policy}

开工前先做复杂度和分工判断：
- 分工必须服从任务书权限、预算和深度限制；用户明确禁止委派时不委派，子任务权限不能超过父任务。
- 多个独立模块、多个研究方向、长链路推导、较大工具调用量或多项独立验收的困难任务，
  只要有清晰边界，就应先拆成子任务委派，再做整合与验收；不必等到自己失败才开始协作。
- 长链路任务也可按阶段串行委派：将已有发现和下一阶段目标交接给新执行实例，不必全部由自己一路做到底。
- 先给出简短分工计划，说明自己负责什么、下属分别负责什么，再调查候选并派发；
  简单连贯任务可以直接完成，说明无需拆分的原因即可。
- 你可以将独立子任务委派给另一个“自己”：delegate_to_node 的 node_id 填 {self.node_id}，
  系统会创建全新的执行实例和对话上下文，共享同一角色的长期记忆、技能及模型配置。
  这不是恢复当前对话，不能把整份原任务原封不动递归转发；子任务必须比父任务更小、更明确。
  独立验收优先选择未参与实现的其他角色，避免同一思路反复自证。

你拥有以下种群协作工具
（MCP 服务器 adgs-mesh，工具名可能带 adgs-mesh 前缀，以实际可用工具为准）：

- 种群目录（list_nodes）：查询活跃节点（生态位/技能/分数/成功率/探索加成/与你的协作次数）。
  支持 query 语义检索——建议从子任务的不同侧面各查一次（如"设备控制"、"测试 报告"），
  多角度收集候选；调查不消耗委派深度，多查几次是正常且被鼓励的。
- 节点档案（probe_node）：深查候选节点的技能包（真实能力证据，比描述宣称可靠）、
  近期执行战绩、当前忙闲、与你的双向协作史。list_nodes 圈定 2-3 个候选后逐一 probe 对比。
- 委派（delegate_to_node）：把一个边界清晰的子任务（含目标/要求/验收标准/交付物/允许写入范围）
  委派给指定节点；第一参数 parent_execution_id 填你的执行 ID；立即返回回执，不阻塞。
- 跟进（wait_delegation）：等待委派结果；未完成返回进度可再次调用，完成返回子节点汇报。
- 复核（report_delegation_verdict）：审阅子节点汇报后给出验收判定，驱动其进化分。

协作纪律：
1. 先调查后决策：委派前用 list_nodes 多角度查询、probe_node 深查候选，综合技能、战绩、
   忙闲、协作史后再决定委派给谁（或自己完成）。凭印象直接委派是失职。
2. 任务适配优先：目录按当前子任务适配排序，擅长该任务的节点优先；缺少匹配专长时选通用节点，
   不要让不相关的累计高分压过通用节点。累计分不是该任务能力，成功率与熟人关系只在适配相近时作参考。
   记住你的协作者：probe_node 返回的协作史与你的长期记忆都是熟人证据。把可靠协作者
   （节点 id + 擅长什么）写入你的长期记忆，下次遇到同类任务先查记忆，在适配相近的候选中考虑熟人；
   但同时兼顾种群成长——同类候选中专精够用时，给 explore_boost 高（接手机会少）的
   节点留机会，不要让种群退化成永远同一批人干活。
3. 困难且可拆的工作以委派为默认选择：并行探索、跨专长实现、独立验证和串行阶段都可以交给下属。
   你可以自己承担一部分、同时把其他清晰子任务委派出去；不要以“我也能做”为理由包办大任务。
   同批独立子任务先全部派发再等待；有真实依赖时才按依赖顺序委派。
4. 外部资源互斥：子任务会操作真实设备、端口、远端服务等外部资源时，必须在 task_md 里
   写明资源名与互斥要求（delegate_to_node 的 shared_resources 参数），同一外部资源的
   多个委派必须串行（wait 完成并复核后再发下一个），并行发出会互相打架。
5. 验证优先外包：需要核验下属汇报时，优先委派一个未参与该工作的节点做独立验证
   （把验收标准+被验汇报发给它），以其结论作为你打分的依据——让"验证"成为专门生态位，
   验证能力会在种群中进化；只有很小的核验才自己动手。
6. 打分是你的直系职责：所有下属执行完毕后，综合全部汇报（含验证节点结论）对每个直系
   下属调用 report_delegation_verdict 打分，用 score 参数（-10~+10）区分质量差异；
   打分要如实、有依据，每次幅度有限，不能一次打穿。
7. 每个委派必须有清晰验收标准；收到汇报后必须复核并给出判定；不达标可附 note 再次委派返工。
8. 本任务结束前，所有已发出的委派必须 wait 到结果并逐一给出复核判定。
9. 委派深度与总预算有限：同一子任务不要无限换人重试，两轮失败就如实计入汇报。
10. 你的执行结果会经过独立验收（对照任务书逐条核验，交付物路径做机械核验），且你的
    直系上级掌握最终打分权；自评申报只是参考，未完成就申报 passed 会被驳回并计为失败。
11. 结束时，最终输出的最后一行必须是 JSON：{{"outcome": "passed|blocked", "summary": "一句话结论"}}
    中断或没写报告不会被视为完成——系统会注入确认要求你明确终结状态（继续/完成报告/放弃报告）；
    放弃也是合法终结，但必须如实报告。"""

    @staticmethod
    def _parse_self_verdict(output: str):
        """从 worker 最终输出提取自评 JSON 尾行 → (outcome, summary)。"""
        import re
        text = (output or "").strip()
        for line in reversed([ln.strip() for ln in text.splitlines()[-8:] if ln.strip()]):
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            outcome = data.get("outcome")
            if outcome in ("passed", "blocked", "failed"):
                return outcome, str(data.get("summary", ""))[:500]
        m = re.search(r'\{"outcome"\s*:\s*"(passed|blocked|failed)"[^}]*\}', text[-2000:])
        if m:
            try:
                data = json.loads(m.group(0))
                return data.get("outcome"), str(data.get("summary", ""))[:500]
            except ValueError:
                pass
        return None, ""

    @staticmethod
    def _mechanical_evidence(task_md: str, workspace: str) -> str:
        """机械核验：交付物清单里形似路径的项逐个检查存在性（独立验收的证据输入）。"""
        from pathlib import Path
        block = task_section(task_md, "交付物") or ""
        lines, checked = [], 0
        for raw in block.splitlines():
            item = raw.strip().lstrip("-*• ").strip()
            if not item:
                continue
            m = re.search(r'(?:[A-Za-z]:[\\/][^\s，。；;"\']+|[\w\-./\\]+\.[A-Za-z0-9]{1,6})', item)
            if not m:
                continue
            token = m.group(0).strip('.,;，。、"')
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = Path(workspace or ".") / candidate
            checked += 1
            try:
                exists = candidate.exists()
            except OSError:
                exists = False
            lines.append(f"- {'存在' if exists else '缺失'}: {token}")
        if not checked:
            return "交付物清单无可核验的文件路径，跳过机械核验。"
        return f"机械核验 {checked} 项交付物路径：\n" + "\n".join(lines)

    def _independent_acceptance(self, task_md: str, output: str,
                                self_reported, summary) -> dict:
        """独立验收（merged 模式）：申报≠验收，验收权在 AGENT 复核层。

        worker 自评只是申报；AGENT.evaluate 对照任务书逐条判定（旧模式固有的
        验收层，merged 改造时曾被绕过——depth=0 任务一度变成自己给自己打分，
        半成品也会被"按产出推断 passed"，2026-10-24 T9 实测漏判）。
        返回 {passed, evaluation, summary}；验收官不可用时返回 unjudged 结果。"""
        mech = self._mechanical_evidence(task_md, getattr(self, "_workspace", None))
        review_input = str(output or "")[-9000:] + "\n\n【系统机械核验】\n" + mech
        budget = getattr(self, "_deadline", None)
        remaining = max(120, int(budget - time.monotonic())) if budget else 600
        review = self.agent.evaluate(
            task_md, review_input, old_score=getattr(self, "_score_base", 50.0),
            context_brief=f"worker 自评申报: {self_reported or '缺失'}；{summary or ''}"[:1200],
            timeout=min(600, remaining))
        if review.get("unjudged"):
            # 验收官自身不可用（协议/超时/infra）：这是验收侧故障，不是 worker 的
            # 协议错误——如实标 unjudged，成果保留待核实，不推断成败。
            return {"unjudged_result": self._unjudged(
                task_md, {"output": output}, "unjudged",
                f"独立验收不可用（申报为 {self_reported or '缺失'}）: {str(review.get('detail', ''))[:200]}。"
                f"成果保留待核实，不作为成功计入。")}
        passed = bool(review.get("passed"))
        mismatch = (self_reported == "passed") != passed
        evaluation = {"passed": passed, "outcome": "passed" if passed else "failed",
                      "self_reported": self_reported or "missing",
                      "raw_score": review.get("raw_score"),
                      "score": review.get("score"),
                      "passed_items": review.get("passed_items") or [],
                      "failed_items": review.get("failed_items") or [],
                      "detail": ((summary or "") + " ｜验收: " if summary else "验收: ")
                                + str(review.get("detail") or ("passed" if passed else "failed"))
                                + (("；⚠申报与验收不一致（申报 %s）" % self_reported) if mismatch else "")}
        return {"passed": passed, "evaluation": evaluation}

    def _termination_confirm_instruction(self, last_output: str) -> str:
        """任务终结确认（自动注入）：无申报报告时唤醒 worker 明确终结状态。

        中断/没报告不能推断成败——没干完就继续干，干完或放弃都必须交报告，
        确保任务无论成败都有明确终结（2026-10-24 用户设计）。"""
        return f"""【任务终结确认（系统自动注入）】

你上一轮结束时没有输出规范的终结报告（要求：完整执行报告——执行过程/关键输出/
遇到的问题/最终结果——外加最后一行 {{"outcome": "passed|blocked", "summary": "一句话结论"}}）。
请立即明确任务状态，三选一：

- 未完成 → 继续执行，直到满足验收标准；
- 已完成 → 输出完整报告 + 末行 outcome JSON；
- 决定放弃/无法继续 → 输出报告说明原因与已达到的程度 + 末行 {{"outcome": "blocked", "summary": "..."}}。

放弃也是合法终结，但必须如实报告；不允许不告而终。

【你上一轮的输出尾部】
{str(last_output)[-1200:]}"""

    def _work_merged(self, task_md: str, upper: str, record_score: bool,
                     task_id: str, timeout: int) -> dict:
        """合并模式执行体：worker 全权决策执行；完成后自评申报 + AGENT 独立验收计分。"""
        bus.emit("node.brain", node=self.node_id, profile=self.profile, action="plan_hermes",
                 detail="merged_brain：先判断复杂度与分工，困难可拆任务优先 A2A 委派")
        instruction = self._a2a_section() + "\n\n" + task_md
        execution_store.put(self._execution_id, decision_pending=False)
        log = self._run_hermes(instruction, timeout)
        if log.get("exit_code") != 0 or log.get("infra_failure"):
            reason = self._infra_reason(log)
            return self._unjudged(task_md, log, "interrupted",
                                  reason or log.get("error") or "执行被中断，成果待核对")
        output = str(log.get("output") or "")
        outcome, summary = self._parse_self_verdict(output)
        # ---- 任务终结保障：中断/没报告不推断成败，自动注入确认直到明确终结 ----
        confirm_rounds = 0
        session_id = log.get("session_id")
        evidence_outputs = [output] if output else []
        while (confirm_rounds < 2 and session_id
               and (outcome is None
                    or (outcome in ("passed", "failed") and len(output.strip()) < 200))):
            if getattr(self, '_deadline', float('inf')) <= time.monotonic():
                return self._unjudged(task_md, {**log, 'output':'\n\n'.join(dict.fromkeys(evidence_outputs)), 'session_id':session_id,'termination_reason':'wall_timeout'},
                                      'interrupted', '任务总预算耗尽，终结报告尚未补齐；原会话与已有成果保留，可用 continue_task 续接。')
            confirm_rounds += 1
            bus.emit("node.brain", node=self.node_id, profile=self.profile, action="resume",
                     detail=f"任务未终结（缺申报或报告不完整），自动注入确认（第 {confirm_rounds} 次）")
            resumed = self._resume_hermes(self._termination_confirm_instruction(output), session_id, timeout)
            if resumed.get('output'):
                evidence_outputs.append(resumed['output'])
            if resumed.get("exit_code") != 0 or resumed.get("infra_failure"):
                retained = {**resumed, 'session_id':resumed.get('session_id') or session_id,
                            'output':'\n\n'.join(dict.fromkeys(evidence_outputs))}
                return self._unjudged(task_md, retained, 'interrupted',
                                      '终结报告确认未能完成：'+str(resumed.get('error') or self._infra_reason(resumed) or '执行被中断')+'；原会话与已有成果保留。')
            log = resumed
            output = str(log.get("output") or "")
            outcome, summary = self._parse_self_verdict(output)
            session_id = log.get("session_id") or session_id
        if outcome is None:
            result = self._unjudged(task_md, {**log,'session_id':session_id,'output':'\n\n'.join(dict.fromkeys(evidence_outputs))}, "unjudged",
                                  f"任务未终结：自动注入确认 {confirm_rounds} 次后仍无申报报告。"
                                  "成果保留待核实，不作为成功计入。")
            # A worker missing its report is not a broken provider. Let the CEO
            # review or continue this execution instead of silently aborting.
            result['infra_failure'] = False
            return result
        verdict = self._independent_acceptance(task_md, output, outcome, summary)
        if verdict.get("unjudged_result"):
            return verdict["unjudged_result"]
        passed = verdict["passed"]
        evaluation = verdict["evaluation"]
        if record_score:
            # 分数以独立验收为准（evaluate 已按 old + raw×delta 计好新分）；
            # A2A 子执行随后仍有父复核（web verdict 端点）二次修正。
            self._update_node(passed, float(evaluation.get("score")
                                            or getattr(self, "_score_base", 50.0)))
        bus.emit("node.eval", node=self.node_id, profile=self.profile, reworks=0, **evaluation)
        self._emit_terminal_status(task_id or self._work_task_id, passed, score=None,
                                   outcome=evaluation["outcome"])
        return {"success": passed, "outcome": evaluation["outcome"], "action": "plan_hermes",
                "task_md": task_md, "work_log": output, "evaluation": evaluation,
                "upper": upper, "session_id": log.get("session_id"),
                "summary": summary or evaluation["detail"]}

    def _execution_evidence(self, task_md, log):
        evidence = ["[本轮进程结果；历史工具进程退出不等于本轮 Hermes 退出]\n" +
                    json.dumps({key: log.get(key) for key in
                                ("attempt_id", "session_id", "exit_code", "termination_reason", "error")},
                               ensure_ascii=False), str(log.get("output") or "")]
        started = None
        if log.get("attempt_id"):
            from core.terminal_runtime import terminal_store
            try:
                attempt = terminal_store.get_attempt(log["attempt_id"])
                if attempt:
                    started = attempt.get("started")
            except (sqlite3.Error, OSError):
                # Legacy/unavailable metadata is explicitly labelled below.
                pass
        session_id = log.get("session_id")
        db_path = self.hermes.profile_dir / "state.db" if hasattr(self.hermes, "profile_dir") else None
        if session_id and db_path and db_path.exists():
            try:
                with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
                    if started is not None:
                        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? AND timestamp>=? ORDER BY id DESC LIMIT 16",
                                            (session_id, started)).fetchall()
                    else:
                        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 16",
                                            (session_id,)).fetchall()
                    evidence.append("\n[本轮会话消息]" if started is not None else "\n[原会话近期消息；未限定 attempt，需核对发生时间]")
                    evidence.append(
                                    "\n".join(f"{role}: {evidence_excerpt(content, 1800)}" for role, content in reversed(rows)))
            except sqlite3.Error:
                pass
        # Current structured artifacts expose incomplete/selftest output even if
        # an older prose report still claims that the full execution finished.
        for name in dict.fromkeys(re.findall(r"(?:[A-Za-z]:[/\\][^\n<>\"|]+?\.(?:md|json)|/[^\n<>\"|]+?\.(?:md|json))", task_md)):
            try:
                path = Path(name.strip()).resolve()
                path.relative_to(Path(getattr(self, "_workspace", config.workspace)).resolve())
                if path.is_file():
                    modified = path.stat().st_mtime
                    scope = ("本轮已更新" if modified >= started else "早于本轮；仅能证明历史成果，不能证明新增要求") if started is not None else "未限定本轮时间"
                    if path.suffix.lower() == ".json":
                        document = json.loads(path.read_text(encoding="utf-8"))
                        def summarize(value, depth=0):
                            if isinstance(value, dict):
                                if depth >= 3:
                                    return {"keys": list(value)[:40]}
                                return {key: summarize(item, depth + 1) for key, item in list(value.items())[:60]}
                            if isinstance(value, list):
                                counts = {}
                                for item in value:
                                    if isinstance(item, dict) and "verdict" in item:
                                        verdict = str(item["verdict"])
                                        counts[verdict] = counts.get(verdict, 0) + 1
                                return {"length": len(value), "verdict_counts": counts}
                            if isinstance(value, str):
                                return evidence_excerpt(value, 300)
                            return value
                        evidence.insert(1, f"\n[当前 JSON 产物 {path.name}；mtime={modified}；{scope}；null/pending/selftest 不等于全量验收完成]\n" +
                                        evidence_excerpt(json.dumps(summarize(document), ensure_ascii=False), 10000))
                    else:
                        evidence.append(f"\n[报告 {path.name}；mtime={modified}；{scope}]\n" + evidence_excerpt(path.read_text(encoding="utf-8"), 10000))
            except (ValueError, OSError, UnicodeError):
                pass
        return "\n".join(evidence)

    def _complete_execution(self, task_md, log, upper, record_score, timeout=None):
        corrections = 0
        while log.get("exit_code") != 0 and corrections < config.correction_limit:
            reason = self._infra_reason(log)
            if reason or log.get("infra_failure") or getattr(self, "_deadline", 0) <= time.monotonic():
                return self._unjudged(task_md, log, "interrupted", reason or log.get("error") or "执行预算耗尽")
            if not log.get("session_id"):
                break
            corrections += 1
            self.agent.append_history("user", f"[修正 {corrections}] {log.get('error', '')}")
            correction = self._correction_instruction("", log.get("error"))
            log = self._resume_hermes(self._continuation_instruction(task_md, correction), log["session_id"], timeout)
        if log.get("exit_code") != 0:
            return self._unjudged(task_md, log, "interrupted", log.get("error") or "执行被中断，成果待核对")
        old_score = getattr(self, "_score_base", self.reg.get(self.node_id).score)
        reworks, evidence = 0, self._execution_evidence(task_md, log)
        if getattr(self, "_prior_evidence", ""):
            evidence = ("[历史执行证据；仅用于保留已有成果。历史错误、退出和终止不得归因于本轮；"
                        "新增要求只能用本轮新增证据验收]\n" + self._prior_evidence +
                        "\n\n[续接执行的新证据]\n" + evidence)
        while True:
            if hasattr(self, "_execution_id"):
                execution_store.put(self._execution_id, phase="acceptance")
            self.agent.append_history("user", "[执行证据]\n" + evidence_excerpt(evidence, 5000))
            self.agent.compress_history_if_needed()
            eval_args = {"old_score": old_score, "context_brief": self.agent.history_brief()}
            if isinstance(self.agent, AGENT):
                eval_args["timeout"] = max(0, self._deadline - time.monotonic())
            evaluation = self.agent.evaluate(task_md, evidence, **eval_args)
            if evaluation.get("unjudged"):
                result = self._unjudged(task_md, log, evaluation.get("outcome", "evaluation_unavailable"),
                                        evaluation.get("detail", "验收不可用"))
                result.update(evaluation=evaluation, work_log=evidence)
                bus.emit("node.eval", node=self.node_id, profile=self.profile, **evaluation)
                return result
            if evaluation.get("passed") or reworks >= config.rework_limit or not log.get("session_id"):
                break
            reworks += 1
            fix = self._continuation_instruction(task_md, self._rework_instruction("", evaluation, reworks))
            # Surface why a finished CLI is being resumed. This is an interim
            # assessment, not a terminal failure or a population score update.
            bus.emit("node.rework", node=self.node_id, profile=self.profile,
                     reworks=reworks, instruction=fix, **evaluation)
            next_log = self._resume_hermes(fix, log["session_id"], timeout)
            evidence += "\n\n[补充执行证据]\n" + self._execution_evidence(task_md, next_log)
            log = next_log
            if log.get("exit_code") != 0:
                return self._unjudged(task_md, log, "interrupted", log.get("error") or "返工中断")
        passed = bool(evaluation.get("passed"))
        if record_score:
            self._update_node(passed, evaluation.get("score", old_score))
            # Report the actual atomically committed population score.
            evaluation["score"] = self.reg.get(self.node_id).score
        bus.emit("node.eval", node=self.node_id, profile=self.profile, reworks=reworks, **evaluation)
        self._emit_terminal_status(self._work_task_id, passed, score=evaluation.get("score"),
                                   reworks=reworks, outcome="passed" if passed else "failed")
        return {"success": passed, "outcome": "passed" if passed else "failed", "action": "plan_hermes",
                "task_md": task_md, "work_log": evidence, "evaluation": evaluation, "upper": upper,
                "session_id": log.get("session_id")}

    def _compute_timeouts(self, instruction: str, timeout: int):
        """按任务书关键词放宽超时，返回 (timeout, inactivity)"""
        explicit = timeout is not None
        timeout = config.worker_timeout if timeout is None else timeout
        # 长任务自动放宽：任务书含长文/大规模生成关键词时，用更长超时
        # （hermes 生成长文本时可能长时间无 stdout，默认 inactivity 会误杀）
        long_keywords = ("50万字", "长文", "大批量", "大规模", "长篇",
                         "万字", "全书", "多文档", "批量生成")
        inactivity = config.inactivity_timeout
        if not explicit and any(k in (instruction or "") for k in long_keywords):
            timeout = max(timeout, getattr(config, "long_task_timeout", 7200))
            inactivity = max(inactivity, 2400)  # 长任务无活动容忍 40 分钟
            print(f"[node:{self.node_id}] 检测到长任务关键词，超时放宽到 {timeout}s "
                  f"(inactivity {inactivity}s)", flush=True)
        return timeout, inactivity

    def _worker_model(self) -> str:
        """本次执行用的 hermes 模型：profile 钉住的 model.default 优先。

        2026-09-27 模型热切换规则（web 模型配置入口）：profile 的
        model.default 是它出生/创建时的快照 —— 旧节点保持旧模型，
        之后新建的节点（出生/自愈补建）才用全局 worker 新配置。
        无钉住值（损坏 profile）回退当前全局配置。
        """
        pinned = HermesInstance._read_model_default(self.profile)
        return pinned or config.worker_model

    def _ensure_live_log(self):
        """确保 profile 就位并返回 live.log 路径（工作区 .adgs_logs/ 下）"""
        ws = getattr(self, "_workspace", config.workspace)
        # 执行前确保 profile 就位（首次初始化/迁移后缺失时自动创建，自愈）
        ready = ensure_profile(self.profile, {
            "exec_model": config.worker_model,
            "base_url": config.worker_url,
            "api_key": config.worker_key,
        }, anysearch_src=config.PROJECT_ROOT / "skills" / "anysearch" / "SKILL.md")
        if not ready:
            raise RuntimeError(PROFILE_NOT_READY)
        # live.log 放工作区下的 .adgs_logs/（不污染工作区内容）
        log_dir = Path(ws) / ".adgs_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{self.node_id}_{getattr(self, '_attempt_id', uuid.uuid4().hex)}_live.log"

    def _execute_attempt(self, instruction, timeout=None, session_id=None):
        budget, inactivity = self._compute_timeouts(instruction, timeout)
        remaining = min(budget, max(0, getattr(self, "_deadline", time.monotonic() + budget) - time.monotonic()))
        if remaining <= 0:
            return {"output": "", "exit_code": -1, "error": "任务总预算耗尽",
                    "session_id": session_id, "infra_failure": True, 'termination_reason':'wall_timeout'}
        self._attempt_id = uuid.uuid4().hex
        bus.emit("node.status", node=self.node_id, profile=self.profile, status="waiting",
                 task=getattr(self, "_work_task_id", None), attempt_id=self._attempt_id, waiting_reason="执行并发槽")
        if not _EXEC_SEM.acquire(timeout=remaining, cancel=getattr(self, "_cancel", None)):
            return {"output": "", "exit_code": -1, "error": "等待并发槽时预算耗尽或取消",
                    "session_id": session_id, "infra_failure": True,
                    'termination_reason':'cancelled' if getattr(self,'_cancel',None) and self._cancel.is_set() else 'wall_timeout'}
        with owned_execution_slot(self._execution_id, _EXEC_SEM,
                                  getattr(self, '_deadline', time.monotonic()+budget), getattr(self, '_cancel', None)):
            remaining = max(0, getattr(self, "_deadline", time.monotonic() + budget) - time.monotonic())
            if remaining <= 0:
                return {"output": "", "exit_code": -1, "error": "任务总预算耗尽",
                        "session_id": session_id, "infra_failure": True, 'termination_reason':'wall_timeout'}
            phase = "resume" if session_id else "execute"
            if worker_launch_on_hold() and not recovery_grant(self._execution_id):
                raise RuntimeError(WORKER_HOLD_REASON)
            try:
                live_log = self._ensure_live_log()
            except (RuntimeError, OSError):
                if not session_id:
                    execution_store.put(self._execution_id, phase="preflight", decision_pending=True)
                raise
            execution_store.put(self._execution_id, phase=phase, attempt_id=self._attempt_id)
            bus.emit("node.status", node=self.node_id, profile=self.profile, status="running",
                     task=self._work_task_id, attempt_id=self._attempt_id)
            bus.emit("hermes.start", node=self.node_id, profile=self.profile, phase=phase,
                     attempt_id=self._attempt_id)
            kwargs = dict(model=self._worker_model(), live_log=live_log,
                          timeout=remaining, inactivity_timeout=inactivity, cwd=self._workspace,
                          execution_id=self._execution_id, attempt_id=self._attempt_id,
                          task_id=self._work_task_id, run_id=self._run_id, cancel=self._cancel)
            result = self.hermes.resume(instruction, session_id, **kwargs) if session_id else self.hermes.execute(instruction, **kwargs)
            execution_store.put(self._execution_id, session_id=result.get("session_id") or session_id,
                                work_log=result.get("output", ""))
            bus.emit("hermes.end", node=self.node_id, profile=self.profile, phase=phase,
                     attempt_id=self._attempt_id, exit_code=result.get("exit_code"),
                     error=result.get("error") or "")
            return result

    def _run_hermes(self, instruction: str, timeout: int = None) -> dict:
        return self._execute_attempt(instruction, timeout)

    def _resume_hermes(self, instruction: str, session_id: str, timeout: int = None) -> dict:
        return self._execute_attempt(instruction, timeout, session_id)

    def _correction_instruction(self, task_md: str, last_error: str) -> str:
        """生成 resume 修正指令：告知上次失败原因，让其基于现有进度继续"""
        return f"""【kill+resume 修正指令】

上一次执行未成功完成（原因：{last_error or '超时或异常退出'}）。
请先检查工作区当前已有进度（文件/代码/报告/产物），在此基础上继续完成剩余工作，
并修复导致上次失败的问题。验收标准与原始任务书如下，保持不变：

{task_md}"""

    def _rework_instruction(self, task_md: str, eval_res: dict,
                            reworks: int = 1) -> str:
        """生成返工指令：基于验收失败项，让 hermes 在同一 session 上继续修正

        设计.txt:37：上层/节点认为验收不充分时，发补充要求，hermes 在保留
        上下文（同一 session）的情况下继续。

        是否重新排查根因取决于失败证据，不能由返工次数推断。
        已验证修复只缺落盘、报告或复验时，应直接补齐，不重做探索。
        """
        failed = eval_res.get("failed_items", []) or []
        lines = []
        for f in failed:
            if isinstance(f, dict):
                lines.append(f"- {f.get('criterion', '?')}: {f.get('why', '')}")
            else:
                lines.append(f"- {f}")
        if not lines:
            lines.append("（未列出具体失败项，请自查验收标准逐条核对）")

        direction = (
            "【依据证据补齐】\n"
            "优先使用原会话已确认的根因、已验证的修复方案与现有产物，直接补齐具体失败项。\n"
            "已验证方案尚未落盘时先实施并复验；仅缺报告、格式或验收证据时只补这些缺项。\n"
            "返工次数不证明根因错误。只有新失败证据反驳原根因或已实施方案时，才用最小复现重新排查并调整方向。\n"
            "只修正本次会话产生且确认错误的改动；保留用户和其他会话的成果。\n"
        )

        return f"""【返工指令 - 验收未通过】

上次验收未通过，失败项如下：
{chr(10).join(lines)}

{direction}
请在工作区现有成果基础上补齐/修正上述失败项。
验收标准与原始任务书如下，保持不变：

{task_md}"""

    # ================================================================
    # 递归委派（节点间任务传递，设计.txt 第 18/30/43 行）
    # ================================================================

    @staticmethod
    def _safe_name(name: str, idx: int) -> str:
        """子任务名 → 安全文件名（去非法字符，截断）"""
        import re
        s = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or f"sub{idx}")).strip("_")
        return s[:30] or f"sub{idx}"

    @staticmethod
    def _extract_overall(task_md: str) -> str:
        """从任务书 markdown 提取「总体任务」段（子任务书继承总体目标）"""
        import re
        m = re.search(r"##\s*总体任务\s*\n([\s\S]*?)(?=\n##|\Z)", task_md)
        if m:
            return m.group(1).strip()[:500]
        return (task_md or "").strip()[:500]

    def _run_child(self, child_id: str, sub_md: str, depth: int,
                   upper: str, timeout: int, task_id: str = None) -> dict:
        """在子线程里递归执行子节点任务（depth+1）

        task_id: 本次子任务在委派方图谱中的序号 key（graph.dispatch 同源），
        透传给子节点 work —— 子节点实例 key = 子节点 id @ task_id，
        其内部再委派时序号节点才能挂回自己的实例（跨层树枝）。
        """
        child = NODE(child_id, registry=self.reg)
        return child.work(sub_md, upper=upper, timeout=timeout, depth=depth,
                          task_id=task_id)

    @staticmethod
    def _summarize_children(children: list) -> str:
        """子任务结果汇总为一段文本（回填 brain 上下文）"""
        lines = []
        for c in children:
            name = c.get("name", c.get("node_id", "?"))
            ok = "✓" if c.get("success") else "✗"
            err = c.get("error", "")
            lines.append(f"- [{ok}] {name}: {err or '完成'}")
        return "\n".join(lines)

    def _merge_subtasks(self, task_md: str, subtasks: list) -> str:
        """深度到顶时，把子任务合并成单一任务书，强制自己完成"""
        parts = [task_md, "\n## 合并执行说明",
                 "本节点已达最大委派深度，需自行完成以下全部子任务："]
        for i, st in enumerate(subtasks, 1):
            if not isinstance(st, dict):
                continue
            parts.append(f"\n### 子任务 {i}: {st.get('name', '')}")
            parts.append(f"目标: {st.get('goal', '')}")
            req = st.get("requirements", []) or []
            acc = st.get("acceptance_criteria", []) or []
            deli = st.get("deliverables", []) or []
            if req:
                parts.append("需求: " + "；".join(str(r) for r in req))
            if acc:
                parts.append("验收: " + "；".join(str(a) for a in acc))
            if deli:
                parts.append("交付: " + "；".join(str(d) for d in deli))
        return "\n".join(parts)

    def _render_subtask(self, overall: str, st: dict, task_md: str,
                        i: int, dep_ctx: str) -> str:
        """渲染单个子任务书（背景含原任务书 + 依赖前驱结果）"""
        from config.prompts import render_task_md
        from core.scheduling import artifact_dir
        name = st.get("name", f"sub{i}")
        rp = (f"{artifact_dir(getattr(self, '_workspace', config.workspace), getattr(self, '_run_id', ''))}"
              f"/{self._safe_name(name, i)}_{uuid.uuid4().hex[:10]}_report.md")
        bg = f"（由节点 {self.node_id} 委派）原任务背景（当前子任务只验收自身交付）：\n{task_md}\n{st.get('background', '')}"
        if dep_ctx:
            bg = dep_ctx + "\n\n" + bg
        rendered = render_task_md(
            overall, st.get("goal", ""), st.get("requirements", []) or [],
            st.get("acceptance_criteria", []) or [],
            st.get("deliverables", []) or [],
            task_section(task_md, "权限告知") + "\n" + (st.get("permissions") or ""),
            background=bg, report_path=rp, workspace=getattr(self, "_workspace", config.workspace))
        if st.get("write_paths") is not None:
            rendered += "\n\n## 写路径约束\n" + json.dumps(st["write_paths"], ensure_ascii=False)
        return rendered

    def _depends_context(self, st: dict, stage_results: dict) -> str:
        """生成依赖前驱子任务的结果上下文（注入依赖方，实现 depends_on 语义）"""
        deps = st.get("depends_on", []) or []
        if not deps:
            return ""
        parts = []
        for d in deps:
            if not isinstance(d, int) or d not in stage_results:
                continue
            r = stage_results[d]
            status = "完成" if r.get("success") else "失败"
            summary = (r.get("summary") or r.get("work_log") or "")[:800]
            parts.append(f"【前驱子任务 {d}（{r.get('name', '?')}）结果：{status}】\n{summary}")
        return "\n\n".join(parts)

    def _answer_child(self, result: dict) -> str:
        """委派节点回答子节点的 ask_upper 提问（用节点 brain 生成答案）"""
        q = result.get("question", "")
        need = result.get("need_info", []) or []
        ctx = (f"下层节点 {result.get('node_id')} 对任务提出疑问：{q}\n"
               f"需要信息：{', '.join(need) if need else '无'}\n"
               f"任务书：{result.get('task_md', '')[:1000]}\n"
               f"请以上层节点身份给出明确、简洁的补充说明。")
        try:
            answer = self.agent.chat.get_full_response(
                [{"role": "system", "content": "你是 ADGS 节点智能体，正在回答下层节点的任务疑问。"},
                 {"role": "user", "content": ctx}], max_tokens=300)
            if not answer or answer.startswith("__LLM_ERROR__"):
                return "（自动澄清）任务书已包含全部必要信息，请按验收标准执行。"
            return answer
        except Exception:
            return "（自动澄清）任务书已包含全部必要信息，请按验收标准执行。"

    def _resolve_child_clarify(self, sub_md: str, nid: str, r: dict,
                               timeout: int, depth: int,
                               task_id: str = None) -> dict:
        """子节点 ask_upper 提问 → 委派节点回答后重派同一子节点（最多 2 次）

        设计.txt 第 19 行：下层节点没理解任务可向上层讨论确认。
        task_id: 该子任务的图谱序号 key（重派沿用同一实例，透传给 work）。
        """
        for _ in range(2):
            if not r.get("need_clarify"):
                return r
            print(f"[node:{self.node_id}] 子节点 {r.get('node_id')} 提问，"
                  f"委派节点回答后重派", flush=True)
            answer = self._answer_child(r)
            sub_md = sub_md + f"\n\n【上层节点（{self.node_id}）补充说明】\n{answer}"
            if nid == self.node_id:
                r = self.spawn_parallel(self.node_id, sub_md, self.reg,
                                        upper=self.node_id, depth=depth + 1,
                                        task_id=task_id)
            else:
                r = self._run_child(nid, sub_md, depth + 1, self.node_id,
                                    timeout, task_id)
        return r

    def _evaluate_delegation(self, children: list, old_score: float) -> dict:
        if not children:
            return {"passed": False, "score": old_score, "unjudged": True,
                    "outcome": "protocol_error", "detail": "无有效子任务"}
        if any(c.get("infra_failure") or c.get("unjudged") for c in children):
            return {"passed": False, "score": old_score, "unjudged": True,
                    "outcome": "unjudged", "detail": "部分子任务无法判断，保留已完成成果"}
        passed = all(c.get("success") for c in children)
        raw = [float((c.get("evaluation") or {}).get("raw_score", 5 if c.get("success") else -5)) for c in children]
        quality = max(-10.0, min(10.0, sum(raw) / len(raw)))
        return {"passed": passed, "raw_score": quality,
                "score": max(0.0, old_score + quality * config.delta_coef),
                "detail": f"当前子任务评价 {quality:.2f}；不使用历史累计分",
                "outcome": "passed" if passed else "failed"}

    def _evaluate_delegation_and_update(self, children: list, task_md=None) -> dict:
        """委派节点也进化：基于子任务表现打分 + 描述分化。

        返回评估结果（供 _work_inner 把分数带进委派实例终态事件；
        节点不存在时返回 {}）。
        """
        node = self.reg.get(self.node_id)
        if not node:
            return {}
        eval_res = self._evaluate_delegation(children, getattr(self, "_score_base", node.score))
        if task_md and not eval_res.get("unjudged") and isinstance(self.agent, AGENT):
            execution_store.put(self._execution_id, phase="acceptance")
            evidence = json.dumps([{"name": c.get("name"), "task_md": c.get("task_md"),
                "evaluation": c.get("evaluation"), "success": c.get("success"),
                "work_log": evidence_excerpt(c.get("work_log", ""), 5000)} for c in children], ensure_ascii=False)
            eval_res = self.agent.evaluate(task_md, evidence, old_score=getattr(self, "_score_base", node.score),
                                          timeout=max(0, self._deadline - time.monotonic()))
        if not eval_res.get("unjudged"):
            self._update_node(eval_res.get("passed", False), eval_res.get("score", node.score))
            eval_res["score"] = self.reg.get(self.node_id).score
        brief = self._summarize_children(children)
        return eval_res

    def _delegate(self, decision: dict, task_md: str, upper: str, timeout: int, depth: int) -> dict:
        from core.router import route
        tasks = decision.get("subtasks") or []
        validate_dependencies(tasks)
        parent_permissions = task_section(task_md, "权限告知")
        parent_paths = task_section(task_md, "写路径约束")
        parent_paths = json.loads(parent_paths) if parent_paths else None
        ws = getattr(self, "_workspace", config.workspace)
        parent_resources = write_resources({"permissions": parent_permissions,
                                            "write_paths": parent_paths}, ws)
        tasks = [dict(t) for t in tasks]
        for child in tasks:
            child["permissions"] = child.get("permissions") or parent_permissions
            if "write_paths" not in child and parent_paths is not None:
                child["write_paths"] = parent_paths
            try:
                validate_new_artifacts(child, ws, parent_permissions)
            except ValueError as exc:
                return self._unjudged(task_md, {}, "protocol_error", str(exc))
            claims = write_resources(child, ws)
            if any(not any(c == p or c.startswith(p + "/") for p in parent_resources) for c in claims):
                return self._unjudged(task_md, {}, "protocol_error", "子任务写路径超出上层权限")
        bus.emit("node.status", node=self.node_id, profile=self.profile, status="delegating",
                 task=self._work_task_id)
        overall = decision.get("overall_goal") or self._extract_overall(task_md)
        stage_ids = {}
        parent_key = f"{self.node_id}@{self._execution_id}"
        identities = [(f"task-{uuid.uuid4().hex}", uuid.uuid4().hex) for _ in tasks]

        def submit(index, completed):
            # The scheduler gives all concurrently admitted tasks the same completed
            # dependency snapshot; a later serial wave receives a different snapshot.
            stage_id = stage_ids.setdefault(tuple(sorted(completed)), f"stage-{uuid.uuid4().hex}")
            task = tasks[index]
            validate_new_artifacts(task, ws, parent_permissions)
            task_id, execution_id = identities[index]
            if self._cancel.is_set():
                return {"success": False, "unjudged": True, "outcome": "interrupted", "error": "上层执行已取消"}
            sub_md = self._render_subtask(overall, task, task_md, index, self._depends_context(task, completed))
            child = route(sub_md, reg=self.reg, verbose=False)
            if child is None:
                return {"success": False, "unjudged": True, "outcome": "blocked", "error": "无可用节点"}
            bus.emit("graph.task", id=task_id, task_id=task_id, execution_id=execution_id,
                     run_id=self._run_id, stage_id=stage_id, name=task.get("name", ""),
                     goal=task.get("goal", ""), task_md=sub_md, overall=overall, parent=parent_key, seq=index + 1, count=1)
            bus.emit("graph.dispatch", parent=parent_key, task=task_id, task_id=task_id,
                     execution_id=execution_id, node=child.id, profile=child.id, mode="session",
                     goal=task.get("goal", ""), task_md=sub_md, run_id=self._run_id)
            budget = max(0, self._deadline - time.monotonic())
            if budget <= 0:
                return {"success": False, "unjudged": True, "outcome": "interrupted", "error": "委派总预算耗尽"}
            try:
                result = NODE(child.id, registry=self.reg).work(sub_md, upper=self.node_id,
                         timeout=budget, depth=depth + 1, task_id=task_id,
                         execution_id=execution_id, run_id=self._run_id, parent_cancel=self._cancel,
                         workspace=self._workspace, parent_execution_id=self._execution_id)
            except Exception as exc:
                result = {"success": False, "unjudged": True, "outcome": "interrupted", "error": str(exc),
                          "node_id": child.id, "profile": child.id, "execution_id": execution_id,
                          "task_id": task_id, "run_id": self._run_id, "task_md": sub_md}
            if result.get("need_clarify"):
                # Clarification is a suspended task; do not invent missing user facts.
                result.update(unjudged=True, outcome="waiting_user")
            result["name"] = task.get("name", "")
            return result

        children = dependency_results(tasks, submit, config.max_workers_effective,
                                      workspace=getattr(self, "_workspace", config.workspace))
        for index, child in enumerate(children):
            child.setdefault("task_id", identities[index][0])
            child.setdefault("execution_id", identities[index][1])
            child.setdefault("task_md", self._render_subtask(overall, tasks[index], task_md, index, ""))
        unjudged = any(c.get("unjudged") or c.get("infra_failure") for c in children)
        return {"success": all(c.get("success") for c in children), "action": "split_tasks",
                "children": children, "subtasks": tasks, "task_md": task_md,
                "unjudged": unjudged, "infra_failure": unjudged,
                "outcome": "unjudged" if unjudged else "passed" if all(c.get("success") for c in children) else "failed"}

    def _update_node(self, passed: bool, new_score: float):
        node = self.reg.get(self.node_id)
        if not node:
            return
        if hasattr(self.reg, "record_result"):
            self.reg.record_result(self.node_id, passed, new_score - getattr(self, "_score_base", node.score),
                                   getattr(self, "_execution_id", uuid.uuid4().hex), config.ema_alpha,
                                   revise=getattr(self, "_credit_revision", False))
            return
        # Compatibility for lightweight registries used by integrations and tests.
        node.success_rate = (1 - config.ema_alpha) * node.success_rate + config.ema_alpha * int(passed)
        node.score = max(0.0, new_score)
        node.tasks_done += 1
        self.reg.update(node.id, success_rate=round(node.success_rate, 4),
                        score=round(node.score, 2), tasks_done=node.tasks_done)

    def evolve_describe(self, eval_res: dict = None, task_md: str = "",
                        work_log: str = ""):
        """描述分化：任务后由节点智能体（LLM）微调 hermes 能力描述

        - 纯 LLM 结果：参考旧描述，吸收本次任务经验与新技能
        - 描述用于向量路由 + 生态位拥挤度
        """
        node = self.reg.get(self.node_id)
        if not node:
            return
        skills = HermesInstance.list_skills(self.profile)
        new_desc = self.agent.update_describe(
            old_describe=node.describe,
            task_md=task_md,
            work_log=work_log,
            skills=skills,
            max_len=config.describe_max_len)
        if new_desc and new_desc != node.describe:
            node.describe = new_desc
            self.reg.update(node.id, describe=node.describe)

    # ================================================================
    # 同一 AGS 的独立会话并行
    # ================================================================

    @staticmethod
    def spawn_parallel(node_id: str, task_md: str, registry: Registry = None,
                       upper: str = None, depth: int = 0, record_score: bool = True,
                       task_id: str = None, execution_id: str = None, run_id: str = None) -> dict:
        """Another session of the same AGS; never clone or delete its persistent profile."""
        reg = registry or _registry
        if not reg.get(node_id):
            return {"success": False, "unjudged": True, "outcome": "blocked", "error": "节点不存在"}
        return NODE(node_id, registry=reg).work(task_md, upper=upper or "ceo", depth=depth,
                   record_score=record_score, task_id=task_id, execution_id=execution_id, run_id=run_id)


if __name__ == "__main__":
    print("NODE 模块加载正常")
