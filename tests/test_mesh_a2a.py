"""A2A 节点大脑合并(merged_brain)回归:
  1. MCP server 工具逻辑(经 FastMCP client 内存会话直连)
  2. /internal/mesh/* 端点(令牌鉴权/委派/长轮询/复核)
  3. NODE._work_merged 分支(A2A 任务书注入、自评解析、轻量计分)
  4. 插件写入 + delegation toolset 禁用
"""
import json
import sys
import yaml
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.context_probe
utils.context_probe.probe_context_window = lambda *a, **k: None


# ---------------- _parse_self_verdict ----------------

def test_parse_self_verdict_extracts_json_tail():
    from core.node import NODE
    output = "执行过程……\n多项工作完成。\n{\"outcome\": \"passed\", \"summary\": \"全部验收项通过\"}"
    outcome, summary = NODE._parse_self_verdict(output)
    assert outcome == "passed" and "验收" in summary
    outcome, summary = NODE._parse_self_verdict("中间提到 {\"outcome\": \"blocked\"} 但最后没有 JSON")
    assert outcome == "blocked"
    assert NODE._parse_self_verdict("没有任何 JSON") == (None, "")
    assert NODE._parse_self_verdict("") == (None, "")


def test_a2a_section_mentions_tools_and_discipline():
    from core.node import NODE
    node = NODE.__new__(NODE)
    node.node_id = "ags_test"
    node._execution_id = "exec-test-1"
    section = node._a2a_section()
    assert "ags_test" in section and "exec-test-1" in section
    for tool in ("list_nodes", "delegate_to_node", "wait_delegation", "report_delegation_verdict"):
        assert tool in section
    assert "最后一行必须是 JSON" in section
    assert "explore_boost" in section and "种群成长" in section


# ---------------- MCP server 工具函数 ----------------

@pytest.fixture()
def mesh_env(monkeypatch):
    """MCP server 工具直连假 provider（同进程架构：runtime 注入数据访问）。"""
    import tools.adgs_mcp_server as srv
    calls = {}

    def directory(*args, **kwargs):
        return calls.get("directory_response", [])

    def delegate(child_node_id, task_md, parent_execution_id, shared_resources=None):
        calls["delegate_args"] = (child_node_id, task_md, parent_execution_id)
        return calls.get("delegate_response", {"ok": True, "delegation_id": "abc123"})

    def wait(delegation_id, wait_s):
        calls["wait_args"] = (delegation_id, wait_s)
        return calls.get("wait_response", {"ok": True, "status": "running", "elapsed": 30,
                                           "child_node_id": "ags_2"})

    def verdict(delegation_id, passed, note, *extra):
        calls["verdict_args"] = (delegation_id, passed, note)
        return calls.get("verdict_response", {"ok": True, "child_node_id": "ags_2"})

    srv.configure_provider(directory=directory, delegate=delegate, wait=wait, verdict=verdict)
    srv._calls = calls
    yield srv


def test_mcp_list_nodes_formats_directory(mesh_env):
    srv = mesh_env
    srv._calls["directory_response"] = [
        {"id": "ags_1", "describe": "擅长网页抓取", "score": 55.2,
         "success_rate": 0.83, "tasks_done": 12, "skills": ["web_scrape"],
         "explore_boost": 1.02, "explore_recommended": False},
        {"id": "ags_new", "describe": "新节点", "score": 51.0,
         "success_rate": 0.5, "tasks_done": 0, "skills": [],
         "explore_boost": 1.30, "explore_recommended": True}]
    text = srv.list_nodes()
    assert "ags_1" in text and "网页抓取" in text and "web_scrape" in text
    # 探索推荐标记与加成字段必须呈现（CEO 路由的 PUCT 哲学在 A2A 路径保持一致；
    # 排序/截断细节归 list_nodes 实现，不在此钉死）
    assert "【探索推荐】" in text and "ags_new" in text and "探索加成" in text
    srv._calls["directory_response"] = []
    assert "没有其他活跃节点" in srv.list_nodes()


def test_mcp_delegate_returns_receipt(mesh_env):
    srv = mesh_env
    text = srv.delegate_to_node("parent-exec-1", "ags_2", "# 子任务书\n目标……")
    assert "abc123" in text
    assert srv._calls["delegate_args"] == ("ags_2", "# 子任务书\n目标……", "parent-exec-1")
    srv._calls["delegate_response"] = {"ok": False, "error": "已达最大委派深度"}
    with pytest.raises(RuntimeError, match="最大委派深度"):
        srv.delegate_to_node("p", "ags_2", "x")


def test_mcp_wait_reports_running_and_finished(mesh_env):
    srv = mesh_env
    text = srv.wait_delegation("abc123", 5)
    assert "仍在执行" in text and srv._calls["wait_args"] == ("abc123", 5)
    srv._calls["wait_response"] = {"ok": True, "status": "passed", "elapsed": 400,
                                   "child_node_id": "ags_2", "summary": "已完成部署",
                                   "deliverables": ["report.md"], "verdict": None}
    text = srv.wait_delegation("abc123", 5)
    assert "已完成部署" in text and "report.md" in text and "report_delegation_verdict" in text


def test_mcp_wait_caps_timeout(mesh_env):
    srv = mesh_env
    srv.wait_delegation("abc", 9999)
    assert srv._calls["wait_args"] == ("abc", 150)


def test_mcp_verdict_roundtrip(mesh_env):
    srv = mesh_env
    text = srv.report_delegation_verdict("abc", True, "汇报翔实")
    assert "通过" in text
    assert srv._calls["verdict_args"] == ("abc", True, "汇报翔实")
    srv._calls["verdict_response"] = {"ok": False, "error": "委派尚未结束，先 wait_delegation"}
    with pytest.raises(RuntimeError, match="wait_delegation"):
        srv.report_delegation_verdict("abc", False, "x")


# ---------------- web_app 内部端点 ----------------

@pytest.fixture()
def mesh_app(tmp_path, monkeypatch):
    """web_app with mesh registry isolation + token env."""
    monkeypatch.setenv("ADGS_STATE_DIR", str(tmp_path))
    import importlib
    import web_app
    importlib.reload(web_app)
    monkeypatch.setattr(web_app.bus, "emit", lambda *a, **k: None)
    with web_app._mesh_lock:
        web_app._mesh_delegations.clear()
    token = web_app._mesh_token()
    client = web_app.app.test_client()
    headers = {"X-ADGS-Mesh-Token": token}
    yield web_app, client, headers
    with web_app._mesh_lock:
        web_app._mesh_delegations.clear()


def test_mesh_endpoints_reject_bad_token(mesh_app):
    web, client, _ = mesh_app
    assert client.post("/internal/mesh/delegations", json={}).status_code == 401
    assert client.get("/internal/mesh/delegations/x").status_code == 401
    assert client.get("/internal/mesh/directory").status_code == 401
    assert client.post("/internal/mesh/delegations/x/verdict", json={"passed": True}).status_code == 401


class _FakeRegistry:
    def get(self, nid): return object()
    def active(self):
        return [type("N", (), {"id": nid, "describe": "", "score": 50.0,
                               "success_rate": 0.5, "tasks_done": 0, "skills": {}})()
                for nid in ("ags_x", "ags_child")]


def test_mesh_delegate_requires_known_parent_and_depth(mesh_app, monkeypatch):
    web, client, headers = mesh_app
    monkeypatch.setattr(web, "registry", _FakeRegistry())
    monkeypatch.setattr(web.execution_store, "get", lambda eid: None)
    r = client.post("/internal/mesh/delegations", headers=headers,
                    json={"child_node_id": "ags_x", "task_md": "t", "parent_execution_id": "nope"})
    assert r.status_code in (404, 409), r.get_data(as_text=True)
    assert "不存在" in r.get_json()["error"] or "深度" in r.get_json()["error"]
    monkeypatch.setattr(web.execution_store, "get",
                        lambda eid: {"depth": web.config.max_delegation_depth, "node_id": "ags_p"})
    r = client.post("/internal/mesh/delegations", headers=headers,
                    json={"child_node_id": "ags_x", "task_md": "t", "parent_execution_id": "deep"})
    assert r.status_code == 409 and "最大委派深度" in r.get_json()["error"]


def test_mesh_delegate_wait_and_verdict_roundtrip(mesh_app, monkeypatch):
    web, client, headers = mesh_app
    monkeypatch.setattr(web.execution_store, "get",
                        lambda eid: {"depth": 0, "node_id": "ags_parent", "run_id": "r1",
                                     "workspace": ".", "deadline_at": time.time() + 600})
    monkeypatch.setattr(web, "registry", _FakeRegistry())
    submitted = {}
    web = web  # noqa: 显式引用当前 reload 后的模块

    class FakeNode:
        def __init__(self, nid): self.node_id = nid
        def work(self, task_md, **kw):
            submitted["node"], submitted["task_md"], submitted["kw"] = self.node_id, task_md, kw
            time.sleep(0.2)
            return {"success": True, "outcome": "passed", "work_log": "done",
                    "evaluation": {"passed": True, "detail": "自评通过", "deliverables": ["a.md"]},
                    "summary": "自评通过"}

    class FakePool:
        """同步执行线程体（不依赖线程时序），复刻 _mesh_run_child 的落账逻辑。"""
        def __init__(self, node_cls): self.node_cls = node_cls
        def submit(self, fn, *args):
            delegation_id, child_node_id, task_md, parent_execution_id = args
            record = web._mesh_delegations.get(delegation_id, {})
            try:
                result = self.node_cls(child_node_id).work(
                    task_md, upper=record.get("parent_node_id", "mesh"), timeout=60, depth=1,
                    task_id="mesh_x", run_id="r1", parent_cancel=None, workspace=".")
                status = "passed" if result.get("success") and result.get("outcome") == "passed"                     else result.get("outcome") if result.get("outcome") in ("blocked", "failed")                     else "interrupted"
                summary, deliverables = web._mesh_summary_of(result)
            except Exception as exc:
                status, summary, deliverables = "interrupted", str(exc), []
            with web._mesh_lock:
                record.update({"status": status, "summary": summary,
                               "deliverables": deliverables, "ended_at": time.time()})
                web._mesh_delegations[delegation_id] = record
                web._mesh_lock.notify_all()

            class _F:
                def result(self): return None
            return _F()

    monkeypatch.setattr(web, "_MESH_POOL", FakePool(FakeNode))
    r = client.post("/internal/mesh/delegations", headers=headers,
                    json={"child_node_id": "ags_child", "task_md": "子任务", "parent_execution_id": "p1"})
    body = r.get_json()
    assert body["ok"], body
    did = body["delegation_id"]
    r = client.get(f"/internal/mesh/delegations/{did}?wait=5", headers=headers)
    data = r.get_json()
    assert data["ok"] and data["status"] == "passed", data
    assert "自评通过" in data["summary"] and data["deliverables"] == ["a.md"]
    assert submitted["node"] == "ags_child" and "子任务" in submitted["task_md"]
    assert submitted["kw"]["depth"] == 1 and submitted["kw"]["run_id"] == "r1"
    r = client.post(f"/internal/mesh/delegations/{did}/verdict", headers=headers,
                    json={"passed": True, "note": "汇报翔实"})
    assert r.get_json()["ok"]
    r = client.get(f"/internal/mesh/delegations/{did}", headers=headers)
    assert r.get_json()["verdict"]["passed"] is True
    with web._mesh_lock:
        web._mesh_delegations["live"] = {"status": "running", "child_node_id": "c",
                                         "parent_node_id": "p", "created_at": time.time()}
    r = client.post("/internal/mesh/delegations/live/verdict", headers=headers,
                    json={"passed": False})
    assert r.status_code == 409


# ---------------- merged 分支(node) ----------------

def _merged_node(monkeypatch):
    """构造绕过 __init__ 的 merged 模式 NODE 桩（含独立验收官挂点）。"""
    from config.config import config
    monkeypatch.setattr(config, "node_merged_brain", True)
    from core.node import NODE
    node = NODE.__new__(NODE)
    node.node_id = "ags_m"
    node.profile = "ags_m"
    node._execution_id = "exec-m"
    node._work_task_id = "t1"
    node._cancel = type("C", (), {"is_set": lambda self: False})()
    node._score_base = 50.0
    node._workspace = "."
    node.reg = type("R", (), {"get": lambda self, nid: type("N", (), {"score": 50.0})()})()
    node._emit_terminal_status = lambda *a, **k: None
    node._unjudged = lambda *a, **k: {"unjudged": True}
    node._infra_reason = lambda log: ""
    return node


def test_work_merged_runs_hermes_and_scores_self_report(monkeypatch):
    node = _merged_node(monkeypatch)
    scored = {}
    node._update_node = lambda passed, score: scored.update(passed=passed, score=score)
    emitted = []
    monkeypatch.setattr("core.node.bus.emit", lambda *a, **k: emitted.append(k))
    output = ('执行过程：读取任务书→探测→实施→复验，关键输出齐全，'
              '遇到的问题均已解决，最终结果符合验收标准，证据见工作区。' * 4
              + '\n{"outcome": "passed", "summary": "完成"}')
    node._run_hermes = lambda instruction, timeout=None: (
        emitted.append({"a2a_check": "种群协作" in instruction}) or
        {"exit_code": 0, "output": output, "session_id": "s1", "infra_failure": False})
    # 独立验收官对照任务书判定通过：计分以验收为准，与申报脱钩
    node.agent = type("A", (), {"evaluate": lambda self, task_md, work_log, old_score=0,
                                context_brief="", timeout=None: {
                                    "passed": True, "score": 51.5, "raw_score": 5,
                                    "passed_items": ["交付物存在"], "failed_items": [],
                                    "detail": "逐条核验通过"}})()
    result = node._work_merged("# 任务\n目标", "ceo", True, "t1", 60)
    assert result["success"] and result["outcome"] == "passed"
    assert result["evaluation"]["self_reported"] == "passed"
    assert scored == {"passed": True, "score": 51.5}
    assert emitted and any(e.get("a2a_check") for e in emitted)


def test_work_merged_reviewer_rejects_false_self_report(monkeypatch):
    """申报 passed 但独立验收不通过 → failed，并标注申报与验收不一致。"""
    node = _merged_node(monkeypatch)
    scored = {}
    node._update_node = lambda passed, score: scored.update(passed=passed, score=score)
    monkeypatch.setattr("core.node.bus.emit", lambda *a, **k: None)
    output = ('我执行了任务：步骤都跑了一遍，应该没问题，证据大概在工作区里，'
              '细节略。' * 10 + '\n{"outcome": "passed", "summary": "应该没问题"}')
    node._run_hermes = lambda instruction, timeout=None: {
        "exit_code": 0, "output": output, "session_id": "s2", "infra_failure": False}
    node.agent = type("A", (), {"evaluate": lambda self, task_md, work_log, old_score=0,
                                context_brief="", timeout=None: {
                                    "passed": False, "score": 48.0, "raw_score": -5,
                                    "passed_items": [], "failed_items": [
                                        {"criterion": "交付物存在", "why": "机械核验缺失"}],
                                    "detail": "交付物缺失"}})()
    result = node._work_merged("# 任务\n目标", "ceo", True, "t1", 60)
    assert not result["success"] and result["outcome"] == "failed"
    assert "不一致" in result["evaluation"]["detail"]
    assert scored == {"passed": False, "score": 48.0}


def test_work_merged_reviewer_unavailable_returns_unjudged(monkeypatch):
    """验收官不可用（协议/超时）→ unjudged，不再按产出推断 passed。"""
    node = _merged_node(monkeypatch)
    node._unjudged = lambda task_md, log, outcome, reason: {
        "unjudged": True, "reason": reason, "outcome": outcome}
    monkeypatch.setattr("core.node.bus.emit", lambda *a, **k: None)
    node._run_hermes = lambda instruction, timeout=None: {
        "exit_code": 0, "output": "执行完成，证据充分。" * 40
                                  + '\n{"outcome": "passed", "summary": "完成"}',
        "session_id": "s3", "infra_failure": False}
    node.agent = type("A", (), {"evaluate": lambda self, task_md, work_log, old_score=0,
                                context_brief="", timeout=None: {
                                    "passed": False, "unjudged": True,
                                    "outcome": "evaluation_unavailable",
                                    "detail": "验收超时"}})()
    result = node._work_merged("# 任务", "ceo", True, "t1", 60)
    assert result["unjudged"] and "独立验收不可用" in result["reason"]


def test_work_merged_unterminated_prompts_resume_then_settle(monkeypatch):
    """任务终结保障：无申报 → 自动注入确认；确认后交报告 → 正常验收计分。"""
    node = _merged_node(monkeypatch)
    scored = {}
    node._update_node = lambda passed, score: scored.update(passed=passed, score=score)
    monkeypatch.setattr("core.node.bus.emit", lambda *a, **k: None)
    resumes = []

    def fake_run(instruction, session_id=None, timeout=None):
        if "任务终结确认" in instruction:
            resumes.append(instruction)
            return {"exit_code": 0,
                    "output": ("确认：任务已完成。执行过程/关键输出/问题/结果齐全。" * 8
                               + '\n{"outcome": "passed", "summary": "确认完成"}'),
                    "session_id": "s4", "infra_failure": False}
        return {"exit_code": 0, "output": "干了一半，还没写报告。", "session_id": "s4",
                "infra_failure": False}

    node._run_hermes = fake_run
    node._resume_hermes = fake_run
    node.agent = type("A", (), {"evaluate": lambda self, task_md, work_log, old_score=0,
                                context_brief="", timeout=None: {
                                    "passed": True, "score": 52.0, "raw_score": 4,
                                    "passed_items": ["报告齐全"], "failed_items": [],
                                    "detail": "通过"}})()
    result = node._work_merged("# 任务", "ceo", True, "t1", 60)
    assert len(resumes) == 1 and "任务终结确认" in resumes[0]
    assert result["success"] and scored == {"passed": True, "score": 52.0}


def test_work_merged_unterminated_after_confirm_returns_unjudged(monkeypatch):
    """注入确认两次后仍无申报 → unjudged（不再按产出长度推断 passed）。"""
    node = _merged_node(monkeypatch)
    node._unjudged = lambda task_md, log, outcome, reason: {
        "unjudged": True, "reason": reason, "outcome": outcome}
    monkeypatch.setattr("core.node.bus.emit", lambda *a, **k: None)
    node._run_hermes = lambda instruction, timeout=None: {
        "exit_code": 0, "output": "只有断断续续的过程输出，始终没有报告。", "session_id": "s5",
        "infra_failure": False}
    node._resume_hermes = lambda instruction, session_id, timeout=None: {
        "exit_code": 0, "output": "还是没有报告。", "session_id": "s5", "infra_failure": False}
    result = node._work_merged("# 任务", "ceo", True, "t1", 60)
    assert result["unjudged"] and "任务未终结" in result["reason"]
    assert not result['infra_failure']


def test_confirmation_with_exhausted_budget_preserves_session_and_returns_to_ceo(monkeypatch):
    from core.ceo import CEO
    node=_merged_node(monkeypatch)
    del node._unjudged
    node._deadline=time.monotonic()-1
    node._run_hermes=lambda *a,**kw:{'exit_code':0,'output':'尚未完成的推导与已有成果','session_id':'original'}
    node._resume_hermes=lambda *a,**kw:pytest.fail('预算耗尽后不应宣称已启动续接')
    monkeypatch.setattr('core.node.bus.emit',lambda *a,**kw:None)
    result=node._work_merged('任务','ceo',True,'t1',60)
    assert result['termination_reason']=='wall_timeout'
    assert result['session_id']=='original' and '已有成果' in result['work_log']
    assert CEO._dispatch_infrastructure_error(result)==''


def test_confirmation_launch_failure_keeps_actual_cause_and_previous_work(monkeypatch):
    from core.ceo import CEO
    node=_merged_node(monkeypatch)
    del node._unjudged
    node._run_hermes=lambda *a,**kw:{'exit_code':0,'output':'已有证明和中间结果','session_id':'original'}
    node._resume_hermes=lambda *a,**kw:{'exit_code':-1,'output':'','error':'provider HTTP 500','infra_failure':True,'termination_reason':'provider_error'}
    monkeypatch.setattr('core.node.bus.emit',lambda *a,**kw:None)
    result=node._work_merged('任务','ceo',True,'t1',60)
    assert 'HTTP 500' in result['error'] and '已有证明' in result['work_log']
    assert result['session_id']=='original' and result['termination_reason']=='provider_error'
    assert 'HTTP 500' in CEO._dispatch_infrastructure_error(result)


def test_work_merged_infra_failure_returns_unjudged(monkeypatch):
    from config.config import config
    monkeypatch.setattr(config, "node_merged_brain", True)
    from core.node import NODE
    node = NODE.__new__(NODE)
    node.node_id = "ags_m"
    node.profile = "ags_m"
    node._execution_id = "exec-m"
    node._cancel = type("C", (), {"is_set": lambda self: False})()
    node.reg = type("R", (), {"get": lambda self, nid: None})()
    node._infra_reason = lambda log: "provider 无响应"
    node._unjudged = lambda task_md, log, outcome, reason: {"unjudged": True, "reason": reason}
    node._run_hermes = lambda instruction, timeout=None: {
        "exit_code": 1, "output": "", "infra_failure": True,
        "error": "provider 无响应", "termination_reason": "provider_error"}
    result = node._work_merged("# 任务", "ceo", False, "t1", 60)
    assert result["unjudged"] and "provider" in result["reason"]


# ---------------- 插件写入与 toolset 禁用 ----------------

def test_mesh_plugin_written_and_delegation_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from config.config import config
    monkeypatch.setattr(config, "node_merged_brain", True)
    import core.hermes as hermes
    monkeypatch.setattr(hermes, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(hermes, "_mesh_mcp_state",
                        lambda: {"url": "http://127.0.0.1:8022/mcp", "token": "tok-fixed"})
    hermes._ensure_mesh_plugin()
    plugin = tmp_path / "plugins" / "adgs-mesh"
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "adgs-mesh"
    mcp = json.loads((plugin / "mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["mesh"]
    assert server["type"] == "streamable-http"
    assert server["url"] == "http://127.0.0.1:8022/mcp"
    assert server["headers"]["X-ADGS-Mesh-Token"] == "tok-fixed"
    # hermes 插件 opt-in：必须写入全局 config 的 plugins.enabled 才会暴露工具
    global_config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert "adgs-mesh" in global_config["plugins"]["enabled"]
    # 状态不一致（runtime 换端口）→ _profile_ready 判 False 触发刷新
    monkeypatch.setattr(hermes, "_mesh_mcp_state",
                        lambda: {"url": "http://127.0.0.1:9999/mcp", "token": "tok-fixed"})
    assert hermes._mesh_plugin_current() is False
