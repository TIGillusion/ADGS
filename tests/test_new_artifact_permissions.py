import pytest
from core.scheduling import validate_new_artifacts, dependency_results


def test_existing_evidence_cannot_be_overwritten_by_readonly_review(tmp_path):
    evidence = tmp_path / 'closure_verify_after.json'
    evidence.write_text('original')
    task = {'write_paths': ['closure_verify_after.json'], 'permissions': '允许输出报告'}
    with pytest.raises(ValueError, match='已存在'):
        validate_new_artifacts(task, tmp_path, '只读模型；仅允许新建核查报告，严禁覆盖已有证据')
    assert evidence.read_text() == 'original'


def test_fresh_artifact_allowed_but_later_collision_rejected(tmp_path):
    task = {'write_paths': ['review.json'], 'permissions': '仅允许新建报告'}
    validate_new_artifacts(task, tmp_path)
    (tmp_path / 'review.json').write_text('created by other task')
    with pytest.raises(ValueError, match='已存在'):
        validate_new_artifacts(task, tmp_path)


def test_authorized_edit_keeps_existing_target(tmp_path):
    (tmp_path / 'source.js').write_text('source')
    validate_new_artifacts({'write_paths': ['source.js'], 'permissions': '允许修改源文件'}, tmp_path)


def test_create_only_glob_cannot_hide_existing_evidence(tmp_path):
    with pytest.raises(ValueError, match='通配符'):
        validate_new_artifacts({'write_paths': ['*.json'], 'permissions': '仅允许新建统计文件'}, tmp_path)


def test_direct_ceo_schedule_rejects_existing_evidence_before_launch(tmp_path):
    (tmp_path / 'evidence.json').write_text('original')
    tasks = [{'permissions': '仅允许新建报告', 'write_paths': ['evidence.json']}]
    called = []
    with pytest.raises(ValueError, match='已存在'):
        dependency_results(tasks, lambda *args: called.append(args), 1, tmp_path)
    assert not called
