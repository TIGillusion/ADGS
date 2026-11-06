# Web3 消息 Markdown 排版

用户反馈：完成报告中的 Markdown 与数学公式按纯文本显示，阅读困难。

## 实现

- 统一 `MarkdownBody` 渲染组件，应用到 CEO 提问、进度报告、完成报告、普通回复，以及图谱轮次指令和委派任务书；历史与实时消息采用相同渲染路径。
- CommonMark、GFM 表格/任务清单/删除线，KaTeX 行内与块级公式，代码语法高亮和复制按钮。
- 沿用已有字体与明暗主题，添加标题层级、段落间距、列表缩进、引用边线、表格和代码块排版；宽表、长公式与代码块在各自区域内滚动。
- 原始 HTML 不执行，脚本链接被 Markdown 默认 URL 规则移除，KaTeX trust=false；未知语言或无效公式保留可读内容。
- KaTeX 字体 CSS 与渲染器使用同一版本 0.16.47；字体随本机构建提供，不依赖外部公式服务。

## 验证

- 前端 192 项测试通过，包含 Markdown 结构、数学公式、代码复制和不执行 HTML/脚本链接的覆盖；TypeScript/Vite 构建通过。
- 真实已保存会话的最后完成报告呈现标题、加粗、引用与公式，渲染前后会话文件 SHA256 一致。
- Playwright 验证明暗主题、嵌套列表、表格、任务清单、公式、代码复制，以及 1600px/1000px 宽度无卡片横向越界。测试仅在浏览器内加入示例消息，未向 CEO 发任务；拦截图谱自动写入保护原会话。
- 截图：`web3/test/.markdown-real-dark.png`、`.markdown-sample-dark.png`、`.markdown-sample-light.png`、`.markdown-sample-narrow.png`。
- 已构建到 `web3/dist`，刷新页面生效；未重启后端。
