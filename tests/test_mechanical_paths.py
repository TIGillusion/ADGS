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
