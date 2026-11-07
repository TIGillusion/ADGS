import pytest


@pytest.fixture
def evidence(monkeypatch):
    import utils.context_probe
    monkeypatch.setattr(utils.context_probe,'probe_context_window',lambda *a,**kw:None)
    from core.node import NODE
    return NODE._mechanical_evidence


@pytest.mark.parametrize('name,prefix',[('report.py','叶子B实现'),('reconcile.py','叶子A实现')])
def test_inline_chinese_description_is_not_part_of_ascii_filename(tmp_path,evidence,name,prefix):
    (tmp_path/name).write_text('# actual delivery',encoding='utf-8')
    result=evidence(f'{prefix}{name}及自身测试，保存实现。只写{name}。禁止修改输入。',str(tmp_path))
    assert f'存在: {name}' in result
    assert '缺失' not in result


def test_missing_explicit_inline_output_is_still_rejected(tmp_path,evidence):
    result=evidence('请写入 missing/output.json 并报告结果。',str(tmp_path))
    assert '缺失: missing/output.json' in result


def test_formal_chinese_filename_and_quoted_inline_path_are_preserved(tmp_path,evidence):
    (tmp_path/'中文报告.md').write_text('done',encoding='utf-8')
    assert '存在: 中文报告.md' in evidence('## 交付物\n- 中文报告.md',str(tmp_path))
    assert '存在: 中文报告.md' in evidence('请写入 `中文报告.md` 并报告。',str(tmp_path))


def test_inline_failure_description_does_not_require_basename_in_root(tmp_path,evidence):
    (tmp_path/'report.py').write_text('# implementation',encoding='utf-8')
    target=tmp_path/'.adgs_artifacts/eval-B/report-jsfix.html'
    target.parent.mkdir(parents=True);target.write_text('<html>fixed</html>',encoding='utf-8')
    task=('只写 report.py；使用沙箱输入测试。\n\n## 上层补充要求\n'
          '续接修复（外部验收判定当前 report.html 失败）。修复 report.py；'
          '运行 python report.py --output .adgs_artifacts/eval-B/report-jsfix.html。'
          '写入仅限 report.py 和 .adgs_artifacts/eval-B/。')
    result=evidence(task,str(tmp_path))
    assert '缺失' not in result
    assert '存在: report.py' in result
    assert '存在: .adgs_artifacts/eval-B/report-jsfix.html' in result


def test_inline_readonly_background_does_not_create_output_obligation(tmp_path,evidence):
    (tmp_path/'result.json').write_text('{}',encoding='utf-8')
    task='请读取 missing/input.csv。\n然后写入 result.json 并汇报，禁止修改输入。'
    result=evidence(task,str(tmp_path))
    assert 'missing/input.csv' not in result
    assert '存在: result.json' in result


@pytest.mark.parametrize('name',['.output.json','./nested/output.json'])
def test_inline_output_keeps_leading_dot_and_checks_actual_file(tmp_path,evidence,name):
    target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True)
    task=f'请写入 {name} 并汇报。'
    assert f'缺失: {name}' in evidence(task,str(tmp_path))
    target.write_text('{}',encoding='utf-8')
    result=evidence(task,str(tmp_path))
    assert f'存在: {name}' in result and '缺失' not in result
