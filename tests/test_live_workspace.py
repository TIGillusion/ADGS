from types import SimpleNamespace

from core.node import NODE


def test_bare_mesh_contract_checks_named_file_in_its_workspace(tmp_path):
    workspace = tmp_path / "project"
    wrong = tmp_path / "platform/theory"
    workspace.mkdir()
    wrong.mkdir(parents=True)
    (wrong / "STRUCTURE.md").write_text("placeholder")
    contract = "Write theory/STRUCTURE.md skeleton only, then report."
    evidence = NODE._mechanical_evidence(contract, str(workspace))
    assert "缺失: theory/STRUCTURE.md" in evidence
    (workspace / "theory").mkdir()
    (workspace / "theory/STRUCTURE.md").write_text("project artifact")
    assert "存在: theory/STRUCTURE.md" in NODE._mechanical_evidence(contract, str(workspace))


def test_missing_deliverable_cannot_be_overridden_by_model_pass(tmp_path):
    node = object.__new__(NODE)
    node._workspace, node._score_base = str(tmp_path), 50
    node.agent = SimpleNamespace(evaluate=lambda *a, **kw: {
        "passed": True, "score": 52, "raw_score": 10, "detail": "worker claims it wrote the file"})
    result = node._independent_acceptance("Write theory/STRUCTURE.md then report.",
                                          "Wrote file in another project", "passed", "done")
    assert not result["passed"]
    assert result["evaluation"]["score"] == 50
    assert "缺失: theory/STRUCTURE.md" in result["evaluation"]["failed_items"]
