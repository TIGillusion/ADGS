"""Isolated regression tests: no credentials, live tasks, model requests or profiles."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.chat import CHAT, _clean_args, validate_arguments


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = types.SimpleNamespace(DATA_DIR=Path(self.tmp.name), workspace=self.tmp.name,
            ceo_url="http://fixture.invalid", ceo_model="fixture", ceo_key="fixture",
            brain_url="http://fixture.invalid", brain_model="fixture", brain_key="fixture",
            ctx_window=512, ceo_ctx_window=4096, compress_trigger=.8, compress_budget=.6,
            max_workers_effective=2)
        self.events, self.calls = [], []
        self.node = types.SimpleNamespace(id="ags_fixture")
        owner = self

        class FakeNode:
            def __init__(self, node_id):
                self.node_id = node_id

            def work(self, md, **kwargs):
                owner.calls.append({"md": md, **kwargs})
                return {"success": True, "outcome": "passed", "evaluation": {"passed": True},
                        "hermes_session_id": "session-" + kwargs["execution_id"],
                        "profile": self.node_id, **kwargs}

            def continue_execution(self, execution_id, instruction, **kwargs):
                owner.calls.append({"continue": execution_id, "instruction": instruction, **kwargs})
                return {"success": True, "outcome": "passed", "evaluation": {"passed": True},
                        "execution_id": execution_id, "hermes_session_id": "original-session"}

            @staticmethod
            def spawn_parallel(*args, **kwargs):
                raise AssertionError("CEO must not create temporary profiles")

        class FakeChat:
            def __init__(self, **kwargs):
                pass

        config_module = types.ModuleType("config.config")
        config_module.config = self.config
        node_module = types.ModuleType("core.node")
        node_module.NODE = FakeNode
        router_module = types.ModuleType("core.router")
        router_module.route = lambda *a, **kw: self.node
        registry_module = types.ModuleType("core.registry")
        registry_module.registry = types.SimpleNamespace(get=lambda nid: self.node)
        bus_module = types.ModuleType("web.event_bus")
        bus_module.bus = types.SimpleNamespace(emit=lambda kind, **data: self.events.append((kind, data)))
        chat_module = types.ModuleType("utils.chat")
        chat_module.CHAT, chat_module.validate_arguments = FakeChat, validate_arguments
        self.modules = patch.dict(sys.modules, {"config.config": config_module,
            "core.node": node_module, "core.router": router_module, "core.registry": registry_module,
            "web.event_bus": bus_module, "utils.chat": chat_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.compress = load_file("core.compress", "core/compress.py")
        self.ceo_module = load_file("_fixture_ceo", "core/ceo.py")
        self.ceo = self.ceo_module.CEO()
        self.ceo.activate_session("fixture")
        self.ceo._run_id = "run-fixture"

    def test_provider_outage_preserves_run_and_stops_queue_advance(self):
        for kind in ("network_error", "timeout", "provider_error"):
            with self.subTest(kind=kind):
                self.ceo._task_done = False
                self.ceo._run_id = "run-fixture"
                self.ceo.chat.call_tools = lambda *a, **kw: {
                    "error": "LLM call failed: Connection error.", "error_kind": kind}
                with patch.object(self.ceo_module, "should_compress", return_value=False):
                    result = self.ceo.talk("continue existing work")
                self.assertIn("Connection error", result)
                self.assertFalse(self.ceo._states["fixture"]["_task_done"])
                self.assertEqual(self.ceo._run_id, "run-fixture")
                self.assertEqual(self.events[-1][1]["status"], "error")

    def test_protocol_exhaustion_preserves_unfinished_run(self):
        self.ceo.chat.call_tools = lambda *a, **kw: {"content": "missing tool call"}
        with patch.object(self.ceo_module, "should_compress", return_value=False):
            result = self.ceo.talk("continue existing work")
        self.assertIn("协议", result)
        self.assertFalse(self.ceo._states["fixture"]["_task_done"])
        self.assertEqual(self.ceo._run_id, "run-fixture")
        self.assertEqual(self.events[-1][1]["status"], "blocked")

    def test_cold_activation_restores_saved_history_facts_and_does_not_reload_live_chat(self):
        self.ceo.history.append({"role": "user", "content": "original unsatisfied request"})
        self.ceo._facts_for()["tasks"]["original-e"] = {"outcome": "interrupted"}
        self.ceo.save_conversation("fixture")
        fresh = self.ceo_module.CEO()
        fresh.activate_session("fixture")
        self.assertEqual(fresh.history[-1]["content"], "original unsatisfied request")
        self.assertEqual(fresh._facts_for()["tasks"]["original-e"]["outcome"], "interrupted")
        fresh.history.append({"role": "user", "content": "new in-memory progress"})
        fresh.activate_session("fixture")
        self.assertEqual(fresh.history[-1]["content"], "new in-memory progress")

    def test_corrupt_saved_chat_is_not_replaced_with_empty_history(self):
        directory = self.config.DATA_DIR / "sessions"
        directory.mkdir()
        path = directory / "broken.json"
        original = '{"history": "invalid"}'
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.ceo.activate_session("broken")
        self.assertNotIn("broken", self.ceo._histories)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_same_owner_preworker_checkpoint_restores_missing_ledger_after_restart(self):
        checkpoint = {"session_name": "fixture", "node_id": "ags_fixture", "profile": "ags_fixture",
            "task_id": "original-task", "run_id": "original-run", "task_md": "contract",
            "state": "infra_failure", "phase": "finished", "result": {"outcome": "infra_failure"}}
        with patch("core.execution_store.execution_store.get", return_value=checkpoint), \
             patch("core.terminal_runtime.terminal_store.list_attempts", return_value=[]):
            result = self.ceo._continue_task({"execution_id": "original", "instruction": "retry decision"})
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(self.calls[-1]["continue"], "original")
        self.assertEqual(self.calls[-1]["task_id"], "original-task")

    def test_exhausted_exploration_removes_tools_and_bounds_ignored_schema(self):
        schemas = []
        reads = []
        def model(messages, tools, **kwargs):
            schemas.append({t["function"]["name"] for t in tools})
            return {"tool_calls": [{"id": str(len(schemas)), "name": "read_file",
                                   "arguments": {"path": "fixture.txt"}}]}
        self.ceo.chat.call_tools = model
        self.ceo._read_file = lambda args: reads.append(args) or "fixture content"
        with patch.object(self.ceo_module, "should_compress", return_value=False):
            self.ceo.talk("resume the original execution; do not dispatch a replacement")
        self.assertEqual(len(reads), 5)
        self.assertEqual(len(schemas), 8)
        self.assertIn("read_file", schemas[4])
        self.assertNotIn("read_file", schemas[5])
        self.assertIn("continue_task", schemas[5])
        self.assertFalse(self.ceo._states["fixture"]["_task_done"])
        feedback = "\n".join(m.get("content") or "" for m in self.ceo.history)
        self.assertIn("不得用新任务代替", feedback)

    def test_worker_provider_failure_is_given_to_ceo_to_decide(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                model_calls = []
                def model(messages, *args, **kwargs):
                    model_calls.append(messages)
                    if len(model_calls) == 1:
                        return {"tool_calls": [{"id": "c", "name": "continue_task",
                            "arguments": {"execution_id": "existing", "instruction": "continue"}}]}
                    return {"tool_calls": [{"id": "f", "name": "finish", "arguments": {
                        "status": "blocked", "summary": "节点服务 HTTP 500，待恢复后续接 existing。",
                        "workflow": "继续原执行", "artifacts": []}}]}
                failed = {"infra_failure": True, "outcome": "interrupted", "error": "provider HTTP 500"}
                self.ceo.chat.call_tools = model
                self.ceo._continue_task = lambda args: {"results": [failed]} if nested else failed
                with patch.object(self.ceo_module, "should_compress", return_value=False):
                    result = self.ceo.talk("continue existing task")
                self.assertIn("HTTP 500", result)
                self.assertEqual(len(model_calls), 2)
                self.assertIn("HTTP 500", model_calls[1][-1]["content"])
                self.assertEqual(self.events[-1][0], "chat.ceo_finish")
                self.assertEqual(self.events[-1][1]["status"], "blocked")
                self.assertFalse(self.ceo._states["fixture"]["_task_done"])
                self.assertEqual(self.ceo._run_id, "run-fixture")
                self.assertEqual(self.ceo.history[-1]["role"], "tool")

    def test_ceo_quota_failure_is_visible_and_persisted(self):
        self.config.ceo_key = "fake-private-key"
        self.ceo.chat.call_tools = lambda *args, **kwargs: {
            "error_kind": "provider_error", "http_status": 429,
            "error": "LLM call failed: insufficient_quota (fake-private-key)",
        }
        with patch.object(self.ceo_module, "should_compress", return_value=False):
            result = self.ceo.talk("继续原任务")
        event = self.events[-1][1]
        self.assertEqual(event["text"], result)
        self.assertIn("CEO 模型额度不足（HTTP 429）", result)
        self.assertNotIn(self.config.ceo_key, result)
        self.ceo.save_conversation("fixture")
        fresh = self.ceo_module.CEO()
        fresh.activate_session("fixture")
        self.assertEqual(fresh.history[-1], {"role": "assistant", "content": result})
        self.assertFalse(fresh._task_done)
        self.assertEqual(fresh._run_id, "run-fixture")

    def test_invalid_tool_exhaustion_preserves_unfinished_run(self):
        self.ceo.chat.call_tools = lambda *a, **kw: {"tool_calls": [{"name": "unknown_fixture", "arguments": {}}]}
        with patch.object(self.ceo_module, "should_compress", return_value=False):
            self.ceo.talk("continue existing work")
        self.assertFalse(self.ceo._states["fixture"]["_task_done"])
        self.assertEqual(self.ceo._run_id, "run-fixture")

    def test_pause_arriving_during_model_call_prevents_new_dispatch(self):
        stop=threading.Event()
        self.ceo._stop_requested=stop.is_set
        def model(*args,**kwargs):
            stop.set()
            return {'tool_calls':[{'name':'single_task','id':'should-not-dispatch','arguments':{}}]}
        self.ceo.chat.call_tools=model
        self.ceo._dispatch_step=lambda *a,**kw:self.fail('paused decision must not launch a task')
        with patch.object(self.ceo_module,'should_compress',return_value=False):
            self.ceo.talk('original work')
        self.assertFalse(self.ceo._task_done)
        self.assertEqual(self.events[-1][1]['status'],'paused')

    def test_decision_limit_preserves_unfinished_run(self):
        with patch.object(self.ceo, "_dynamic_limit", return_value=0):
            self.ceo.talk("continue existing work")
        self.assertFalse(self.ceo._states["fixture"]["_task_done"])
        self.assertEqual(self.ceo._run_id, "run-fixture")
        self.assertEqual(self.events[-1][1]["status"], "limit")

    def test_exhausted_turn_resumes_same_run_without_resetting_sequence(self):
        self.ceo._task_seq = 8
        with patch.object(self.ceo, "_dynamic_limit", return_value=0):
            self.ceo.talk("original unfinished request")
            self.ceo.talk("resume after budget recovery")
        self.assertEqual(self.ceo._run_id, "run-fixture")
        self.assertEqual(self.ceo._task_seq, 8)
        requests = self.ceo._facts_for()["requests"]
        self.assertEqual([r["run_id"] for r in requests[-2:]], ["run-fixture"] * 2)
        self.assertEqual(requests[-2]["text"], "original unfinished request")
        self.assertFalse(self.ceo._task_done)

    def task(self, name, depends=None):
        return {"name": name, "goal": name, "requirements": ["retain artifacts"],
                "acceptance_criteria": ["verified"], "deliverables": [name + ".txt"],
                "permissions": "read only", "depends_on": depends or []}

    def test_wrong_or_duplicate_tool_ids_are_not_kept(self):
        assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]}
        wrong = {"role": "tool", "tool_call_id": "b", "content": "done"}
        self.assertEqual(self.compress.sanitize_tool_messages([assistant, wrong]), [])
        duplicate = [assistant, {**wrong, "tool_call_id": "a"}, {**wrong, "tool_call_id": "a"}]
        self.assertEqual(self.compress.sanitize_tool_messages(duplicate), [])

    def test_tool_arguments_count_and_ceo_window(self):
        message = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "a", "function": {"arguments": "x" * 5000}}]}
        self.assertGreater(self.compress._count_tokens([message]), 2500)
        self.assertFalse(self.compress.should_compress([message]))
        self.assertTrue(self.compress.should_compress([message], ctx_window=512))

    def test_compression_budget_and_exchange_boundaries(self):
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "original " * 400}]
        for i in range(6):
            messages.extend([{"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]},
                             {"role": "tool", "tool_call_id": str(i), "content": "result " * 100}])
        result = self.compress.compress(messages, ctx_window=1200)
        self.assertLessEqual(self.compress._count_tokens(result), 720)
        self.assertEqual(result, self.compress.sanitize_tool_messages(result))

    def test_same_profile_independent_execution_ids(self):
        result = self.ceo._dispatch_parallel("goal", [self.task("a"), self.task("b")], [0, 1])
        self.assertEqual(len(result), 2)
        self.assertEqual(len({r["execution_id"] for r in result}), 2)
        self.assertEqual(len({r["task_id"] for r in result}), 2)
        self.assertEqual({r["profile"] for r in result}, {"ags_fixture"})
        self.assertTrue(all("read only" in call["md"] for call in self.calls))

    def test_dependency_ready_does_not_wait_for_unrelated_slow_task(self):
        original = self.ceo._execute_prepared
        c_started, b_done = threading.Event(), threading.Event()

        def execute(item):
            if item["name"] == "b":
                self.assertTrue(c_started.wait(2), "C was blocked by unrelated B")
                b_done.set()
            if item["name"] == "c":
                self.assertFalse(b_done.is_set())
                c_started.set()
            return original(item)

        self.ceo._execute_prepared = execute
        result = self.ceo._dispatch_step("split_tasks", {"overall_goal": "g",
            "subtasks": [self.task("a"), self.task("b"), self.task("c", [0])]})
        self.assertTrue(c_started.is_set())
        self.assertTrue(all(r["success"] for r in result["results"]))

    def test_failed_dependency_blocks_successor(self):
        original = self.ceo._execute_prepared

        def execute(item):
            result = original(item)
            result.update(success=False, outcome="unjudged")
            return result

        self.ceo._execute_prepared = execute
        result = self.ceo._dispatch_step("split_tasks", {"overall_goal": "g",
            "subtasks": [self.task("a"), self.task("c", [0])]})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["results"][1]["outcome"], "blocked")

    def test_cycles_and_invalid_dependencies_never_dispatch(self):
        for tasks in ([self.task("a", [1]), self.task("b", [0])],
                      [self.task("a", [3])], [self.task("a", [True])]):
            result = self.ceo._dispatch_step("split_tasks", {"overall_goal": "g", "subtasks": tasks})
            self.assertEqual(result["outcome"], "protocol_error")
        self.assertEqual(self.calls, [])

    def test_continue_and_finish_require_original_verified_execution(self):
        self.assertIsNotNone(self.ceo._finish_rejection("completed"))
        result = self.ceo._dispatch_parallel("g", [self.task("a")], [0])[0]
        self.assertIsNone(self.ceo._finish_rejection("completed"))
        self.ceo._continue_task({"execution_id": result["execution_id"], "instruction": "add proof"})
        self.assertEqual(self.calls[-1]["continue"], result["execution_id"])
        self.assertEqual(self.calls[-1]["run_id"], "run-fixture")
        with patch("core.execution_store.execution_store.get", return_value=None):
            self.assertEqual(self.ceo._continue_task({"execution_id": "foreign", "instruction": "x"})["outcome"], "protocol_error")

    def test_continue_recovers_missing_ledger_from_same_chat_checkpoint(self):
        checkpoint = {"session_name": "fixture", "node_id": "ags_fixture",
                      "task_id": "original-task", "run_id": "original-run",
                      "task_md": "original read-only contract", "session_id": "original-hermes",
                      "state": "passed"}
        with patch("core.execution_store.execution_store.get", return_value=checkpoint):
            result = self.ceo._continue_task({"execution_id": "original", "instruction": "add proof"})
        self.assertTrue(result["success"])
        self.assertEqual(self.calls[-1]["continue"], "original")
        self.assertEqual(self.calls[-1]["run_id"], "original-run")
        self.assertEqual(self.ceo._facts_for()["tasks"]["original"]["task_md"], checkpoint["task_md"])

    def test_continue_rejects_foreign_or_incomplete_checkpoint(self):
        checkpoint = {"session_name": "foreign", "node_id": "ags_fixture",
                      "task_id": "original-task", "run_id": "original-run",
                      "task_md": "contract", "session_id": "original-hermes"}
        for saved in (checkpoint, {**checkpoint, "session_name": "fixture", "session_id": None}):
            with patch("core.execution_store.execution_store.get", return_value=saved):
                result = self.ceo._continue_task({"execution_id": "original", "instruction": "x"})
            self.assertEqual(result["outcome"], "protocol_error")
        self.assertEqual(self.calls, [])

    def test_summary_is_valid_json_and_contains_all_results(self):
        result = {"tasks": 2, "results": [{"execution_id": "a", "work_log": "x" * 9000,
            "evaluation": {"passed": True}}, {"execution_id": "b", "evaluation": {"passed": False}}]}
        text = json.dumps(self.ceo._result_summary(result))
        decoded = json.loads(text)
        self.assertEqual(len(decoded["results"]), 2)
        self.assertFalse(decoded["results"][1]["evaluation"]["passed"])

    def test_session_state_and_interruption_are_isolated(self):
        self.ceo._task_seq = 8
        self.ceo._facts_for()["requests"].append({"text": "original immutable", "run_id": "run-fixture"})
        self.ceo.activate_session("other")
        self.assertEqual(self.ceo._task_seq, 0)
        self.ceo.mark_interrupted("fixture", "timeout")
        self.assertFalse(self.ceo._task_done)
        self.ceo.activate_session("fixture")
        self.assertEqual(self.ceo._task_seq, 8)
        self.assertFalse(self.ceo._task_done)
        self.assertIn("original immutable", json.dumps(self.ceo._facts_for()))

    def test_loading_running_session_does_not_replace_progress(self):
        self.ceo.history.append({"role": "user", "content": "live progress"})
        self.ceo._talk_session = "fixture"
        self.ceo.load_conversation("fixture")
        self.assertEqual(self.ceo.history[-1]["content"], "live progress")

    def test_persistence_keeps_full_results_facts_and_role_state(self):
        self.ceo._task_seq = 4
        result = self.ceo._dispatch_parallel("g", [self.task("a")], [0])[0]
        self.ceo.save_conversation("fixture")
        other = self.ceo_module.CEO()
        other.load_conversation("fixture")
        self.assertEqual(other._task_seq, 5)
        self.assertIn(result["execution_id"], other._facts_for()["tasks"])
        self.assertIn(result["execution_id"], other._execution_results["fixture"])

    def test_saving_unknown_session_does_not_copy_another_session(self):
        self.ceo.history.append({"role": "user", "content": "private fixture progress"})
        with self.assertRaises(ValueError):
            self.ceo.save_conversation("foreign")
        self.assertFalse((self.config.DATA_DIR / "sessions" / "foreign.json").exists())
        self.ceo.save_as("alias")
        saved = json.loads((self.config.DATA_DIR / "sessions" / "alias.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["history"][-1]["content"], "private fixture progress")

    def test_blocked_dependency_can_resume_after_original_predecessor_passes(self):
        original = self.ceo._execute_prepared

        def fail(item):
            result = original(item)
            result.update(success=False, outcome="unjudged", evaluation={"passed": False, "unjudged": True})
            self.ceo._record_result(item["execution_id"], result)
            return result

        self.ceo._execute_prepared = fail
        result = self.ceo._dispatch_step("split_tasks", {"overall_goal": "g",
            "subtasks": [self.task("a"), self.task("c", [0])]})
        a, c = result["results"]
        still_blocked = self.ceo._continue_task({"execution_id": c["execution_id"], "instruction": "continue"})
        self.assertEqual(still_blocked["outcome"], "blocked")
        self.ceo._continue_task({"execution_id": a["execution_id"], "instruction": "provide proof"})
        resumed = self.ceo._continue_task({"execution_id": c["execution_id"], "instruction": "continue"})
        self.assertTrue(resumed["success"])
        self.assertEqual(self.calls[-1]["execution_id"], c["execution_id"])
        self.assertIn("前驱最新验收证据", self.calls[-1]["md"])

    def test_nested_children_keep_ids_contracts_and_can_continue(self):
        parent = self.ceo._dispatch_parallel("g", [self.task("parent")], [0])[0]
        leaf = {"execution_id": "leaf-execution", "task_id": "leaf-task", "node_id": "ags_leaf",
                "run_id": "run-fixture", "profile": "ags_leaf", "hermes_session_id": "leaf-session",
                "task_md": "original leaf contract", "success": False, "unjudged": True,
                "outcome": "evaluation_unavailable", "error": "report format unavailable"}
        child = {"execution_id": "child-execution", "task_id": "child-task", "node_id": "ags_child",
                 "run_id": "run-fixture", "action": "split_tasks", "success": False,
                 "unjudged": True, "outcome": "unjudged", "children": [leaf],
                 "subtasks": [self.task("leaf")]}
        parent.update(success=False, outcome="unjudged", unjudged=True, children=[child],
                      subtasks=[self.task("child")])
        self.ceo._record_result(parent["execution_id"], parent)
        facts = self.ceo._facts_for()["tasks"]
        self.assertEqual(facts["leaf-execution"]["parent_execution_id"], "child-execution")
        self.assertEqual(facts["leaf-execution"]["goal"], "leaf")
        self.assertEqual(facts["leaf-execution"]["outcome"], "evaluation_unavailable")
        summary = json.loads(json.dumps(self.ceo._result_summary(parent)))
        self.assertEqual(summary["children"][0]["children"][0]["execution_id"], "leaf-execution")
        self.assertEqual(summary["children"][0]["children"][0]["error"], "report format unavailable")
        resumed = self.ceo._continue_task({"execution_id": "leaf-execution", "instruction": "provide valid report"})
        self.assertTrue(resumed["success"])
        self.assertEqual(self.calls[-1]["continue"], "leaf-execution")

    def test_continuing_nested_child_keeps_its_data_dependencies(self):
        parent = self.ceo._dispatch_parallel("g", [self.task("parent")], [0])[0]
        predecessor = {"execution_id": "child-a", "task_id": "task-a", "node_id": "ags_fixture",
                       "task_md": "original contract a", "success": False, "outcome": "failed"}
        successor = {"execution_id": "child-b", "task_id": "task-b", "task_md": "original contract b",
                     "success": False, "outcome": "blocked"}
        parent.update(success=False, outcome="failed", children=[predecessor, successor],
                      subtasks=[self.task("a"), self.task("b", [0])])
        self.ceo._record_result(parent["execution_id"], parent)
        self.assertEqual(self.ceo._facts_for()["tasks"]["child-b"]["blocked_by"], ["child-a"])
        calls_before = len(self.calls)
        blocked = self.ceo._continue_task({"execution_id": "child-b", "instruction": "continue"})
        self.assertEqual(blocked["outcome"], "blocked")
        self.assertEqual(len(self.calls), calls_before)
        self.ceo._continue_task({"execution_id": "child-a", "instruction": "add proof"})
        resumed = self.ceo._continue_task({"execution_id": "child-b", "instruction": "continue"})
        self.assertTrue(resumed["success"])
        self.assertEqual(self.calls[-1]["execution_id"], "child-b")

    def test_conflicting_writers_are_serial_without_success_dependency(self):
        from core.scheduling import dependency_results
        a_done = threading.Event()
        b_started = threading.Event()
        tasks = [{**self.task("a"), "permissions": "modify project", "write_paths": ["src"]},
                 {**self.task("b"), "permissions": "modify project", "write_paths": ["SRC/file.py"]}]

        def execute(index, _results):
            if index == 0:
                self.assertFalse(b_started.wait(.05), "Conflicting writer B overlapped A")
                a_done.set()
                return {"success": False, "outcome": "failed"}
            b_started.set()
            self.assertTrue(a_done.is_set())
            return {"success": True}

        results = dependency_results(tasks, execute, 2, workspace=self.config.workspace)
        self.assertFalse(results[0]["success"])
        self.assertEqual(results[0]["outcome"], "failed")
        self.assertTrue(results[1]["success"])

    def test_disjoint_writers_run_in_parallel_in_ceo(self):
        original = self.ceo._execute_prepared
        b_started = threading.Event()

        def execute(item):
            if item["name"] == "a":
                self.assertTrue(b_started.wait(2), "Disjoint writes were serialized")
            else:
                b_started.set()
            return original(item)

        self.ceo._execute_prepared = execute
        tasks = [{**self.task("a"), "permissions": "modify project", "write_paths": ["a.py"]},
                 {**self.task("b"), "permissions": "modify project", "write_paths": ["b.py"]}]
        result = self.ceo._dispatch_step("split_tasks", {"overall_goal": "g", "subtasks": tasks})
        self.assertTrue(all(r["success"] for r in result["results"]))

    def test_unknown_writer_reserves_workspace_but_readonly_does_not(self):
        from core.scheduling import write_resources, resources_conflict
        unknown = write_resources({"permissions": "modify project"}, self.config.workspace)
        known = write_resources({"permissions": "modify project", "write_paths": ["nested/a.py"]}, self.config.workspace)
        read = write_resources({"permissions": "read only"}, self.config.workspace)
        self.assertTrue(resources_conflict(unknown, known))
        self.assertFalse(resources_conflict(read, known))

    def test_dispatch_single_propagates_contract_workspace_to_node(self):
        """Nested delegation must resolve write paths in the task workspace.

        The CEO renders contracts in its configured workspace, but previously
        called NODE.work without forwarding that workspace. A node that then
        delegated a child parsed the parent paths against the process-global
        workspace and rejected valid children as out of scope.
        """
        task_workspace = str(Path(self.tmp.name) / "task-workspace")
        result = self.ceo._dispatch_single("# 任务书\n## 工作区\n" + task_workspace,
                                           "workspace-test", self.node,
                                           task_id="task-workspace",
                                           execution_id="exec-workspace",
                                           run_id="run-fixture",
                                           workspace=task_workspace)
        self.assertTrue(result["success"])
        self.assertEqual(self.calls[-1]["workspace"], task_workspace)


class RequestTests(unittest.TestCase):
    def client(self, create):
        chat = CHAT.__new__(CHAT)
        chat._timeout = 5
        chat._cfg = (types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))), "fixture", {})
        return chat

    def test_wrong_json_shapes_are_protocol_errors(self):
        self.assertIsNone(_clean_args("[]"))
        self.assertIsNotNone(validate_arguments({"n": True}, {"type": "object", "properties": {"n": {"type": "integer"}}}))

    def test_sdk_does_not_add_hidden_retries(self):
        with patch("utils.chat.OpenAI") as factory:
            CHAT("http://fixture.invalid", "fixture", "fixture")
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    def test_network_retry_stays_required_and_shares_deadline(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            raise TimeoutError("request timed out")

        with patch("utils.chat.time.monotonic", side_effect=[0, 0, 0, 4, 5]), \
                patch("utils.chat.time.sleep"):
            result = self.client(create).call_tools([], [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool_choice"], "required")
        self.assertEqual(result["error_kind"], "timeout")

    def test_call_timeout_is_a_hard_cap_and_zero_does_not_reset_it(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content="", tool_calls=[types.SimpleNamespace(id="c",
                function=types.SimpleNamespace(name="f", arguments="{}"))])
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        tool = {"function": {"name": "f", "parameters": {"type": "object"}}}
        self.client(create).call_tools([], [tool], timeout=.25)
        self.assertGreater(calls[0]["timeout"], 0)
        self.assertLessEqual(calls[0]["timeout"], .25)
        result = self.client(create).call_tools([], [tool], timeout=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["error_kind"], "timeout")

    def test_existing_deadline_caps_both_call_types(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content="done", tool_calls=[types.SimpleNamespace(id="c",
                function=types.SimpleNamespace(name="f", arguments="{}"))])
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        tool = {"function": {"name": "f", "parameters": {"type": "object"}}}
        with patch("utils.chat.time.monotonic", return_value=10):
            self.client(create).call_tools([], [tool], timeout=4, deadline=10.2)
            result = self.client(create).get_full_response([], timeout=4, deadline=10.2)
        self.assertEqual(result, "done")
        self.assertTrue(all(0 < c["timeout"] <= .201 for c in calls))
        with patch("utils.chat.time.monotonic", return_value=11):
            result = self.client(create).get_full_response([], timeout=4, deadline=10.2)
        self.assertTrue(result.startswith("__LLM_ERROR__"))
        self.assertEqual(len(calls), 2)

    def test_late_provider_response_is_not_accepted_as_success(self):
        now = [10.0]
        message = types.SimpleNamespace(content="late answer", tool_calls=[types.SimpleNamespace(id="c",
            function=types.SimpleNamespace(name="f", arguments="{}"))])

        def create(**kwargs):
            now[0] = 10.3
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        tool = {"function": {"name": "f", "parameters": {"type": "object"}}}
        with patch("utils.chat.time.monotonic", side_effect=lambda: now[0]):
            result = self.client(create).call_tools([], [tool], timeout=.2)
            self.assertEqual(result["error_kind"], "timeout")
            self.assertEqual(result["tool_calls"], [])
            now[0] = 10.0
            result = self.client(create).get_full_response([], timeout=.2)
            self.assertTrue(result.startswith("__LLM_ERROR__"))

    def test_recovery_text_request_rejects_structured_tool_calls(self):
        def create(**kwargs):
            self.assertNotIn("tools", kwargs)
            message = types.SimpleNamespace(content="已恢复执行", tool_calls=[object()])
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        result = self.client(create).get_full_response([], reject_tool_calls=True)
        self.assertEqual(result, "__LLM_ERROR__: unexpected tool call")

    def test_empty_tool_id_is_a_repairable_protocol_error(self):
        message = types.SimpleNamespace(content="report ready", tool_calls=[types.SimpleNamespace(id="",
            function=types.SimpleNamespace(name="f", arguments="{}"))])
        tool = {"function": {"name": "f", "parameters": {"type": "object"}}}
        result = self.client(lambda **kwargs: types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message)])).call_tools([], [tool])
        self.assertEqual(result["error_kind"], "protocol_error")
        self.assertEqual(result["tool_calls"], [])
        self.assertIn("nonempty", result["error"])

    def test_missing_report_repairs_are_bounded_protocol_errors(self):
        message = types.SimpleNamespace(content="already done", tool_calls=[])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        result = self.client(create).call_tools([], [])
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["error_kind"], "protocol_error")
        self.assertTrue(all(c["tool_choice"] == "required" for c in calls))

    def test_invalid_history_error_is_not_retried_or_downgraded(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            error = RuntimeError("tool message does not match tool_call_id")
            error.status_code = 400
            raise error

        result = self.client(create).call_tools([], [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["error_kind"], "provider_error")
        self.assertEqual(calls[0]["tool_choice"], "required")

    def test_choice_downgrade_is_only_for_explicit_unsupported_choice(self):
        calls = []
        tool = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                error = RuntimeError("tool_choice required is unsupported")
                error.status_code = 400
                raise error
            message = types.SimpleNamespace(content="", tool_calls=[types.SimpleNamespace(id="c", function=types.SimpleNamespace(name="f", arguments="{}"))])
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        result = self.client(create).call_tools([], [tool])
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual([c["tool_choice"] for c in calls], ["required", "auto"])


if __name__ == "__main__":
    unittest.main()
