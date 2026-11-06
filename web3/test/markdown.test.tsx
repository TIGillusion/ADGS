import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MarkdownBody } from '@/components/MarkdownBody';

afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});

describe('Markdown 正文',()=>{
  it('标题、加粗、引用、嵌套列表、表格与任务清单呈现文档结构',()=>{
    const {container}=render(<MarkdownBody text={'## 真正的成果\n\n**完整证据**和`report.md`。\n\n> 验证不等于证明\n\n1. 第一步\n   - 子步骤\n2. 第二步\n\n| 项目 | 结论 |\n| --- | --- |\n| T1 | 通过 |\n\n- [x] 已复核\n- [ ] 待证明'}/>);
    expect(screen.getByRole('heading',{level:2,name:'真正的成果'})).toBeTruthy();
    expect(container.querySelector('strong')?.textContent).toBe('完整证据');
    expect(container.querySelector('blockquote')?.textContent).toContain('验证不等于证明');
    expect(container.querySelector('ol ul')).toBeTruthy();
    expect(screen.getByRole('table')).toBeTruthy();
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked();
    expect(screen.getAllByRole('checkbox')[1]).not.toBeChecked();
  });
  it('渲染行内与独立公式，非法公式仍显示原表达式',()=>{
    const {container}=render(<MarkdownBody text={'对所有 $k\\ge1$ 成立。\n\n$$\n\\frac{4}{n}=\\frac{1}{x}+\\frac{1}{y}+\\frac{1}{z}\n$$\n\n非法命令 $\\notARealMathCommand{x}$'}/>);
    expect(container.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('.katex-display math')).toBeTruthy();
    expect(container.querySelector('annotation')?.textContent).toBe('k\\ge1');
    expect(container.textContent).toContain('notARealMathCommand');
  });
  it('代码块高亮并复制原代码，不把代码内 Markdown 当正文解释',async()=>{
    const writeText=vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator',{clipboard:{writeText}});
    const {container}=render(<MarkdownBody text={'```python\nprint("**原样代码**")\n```'}/>);
    expect(container.querySelector('pre code.hljs')).toBeTruthy();
    expect(container.querySelector('pre strong')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'复制代码'}));
    await waitFor(()=>expect(writeText).toHaveBeenCalledWith('print("**原样代码**")'));
    expect(screen.getByText('已复制')).toBeTruthy();
  });
  it('未知代码语言正常显示，禁止原始 HTML 和脚本链接',()=>{
    const {container}=render(<MarkdownBody text={'```unknown-language\n原文\n```\n\n[危险](javascript:alert%281%29)\n\n<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n[文档](https://example.com/docs)'}/>);
    expect(container.querySelector('pre')?.textContent).toBe('原文\n');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('危险').getAttribute('href')).not.toContain('javascript:');
    expect(screen.getByRole('link',{name:'文档'}).getAttribute('rel')).toContain('noopener');
  });
});
