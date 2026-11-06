from config.prompts import render_task_md


def test_auto_report_does_not_expand_explicit_write_paths(tmp_path):
    allowed = tmp_path / "fixture"
    allowed.mkdir()
    forbidden = tmp_path / "model" / "report.md"
    task = render_task_md("goal", "write input", [], [], [str(allowed / "input.json")],
        "仅允许写入 fixture/input.json", report_path=str(forbidden), workspace=str(tmp_path / "model"),
        write_paths=[str(allowed / "input.json")])
    assert str(forbidden) not in task
    assert "当前会话输出完整执行报告，不创建文件" in task
    assert "工作目录不扩大权限" in task


def test_explicit_report_file_or_directory_remains_authorized(tmp_path):
    report = tmp_path / "report.md"
    for paths in ([str(report)], [str(tmp_path)]):
        task = render_task_md("goal", "write report", [], [], [str(report)],
            "允许写入报告", report_path=str(report), workspace=str(tmp_path), write_paths=paths)
        assert f"将完整执行报告写入 {report}" in task


def test_read_only_task_never_requires_report_file_even_if_declared(tmp_path):
    report = tmp_path / "report.md"
    task = render_task_md("goal", "read", [], [], [], "只读；不得修改任何文件", report_path=str(report),
        workspace=str(tmp_path), write_paths=[str(report)])
    assert "当前会话输出完整执行报告，不创建文件" in task
