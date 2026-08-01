# Markdown / KaTeX 安全渲染设计

## 背景

笔记、讲解、全文译文和划词译文都通过 `public/app.js` 的 `renderMd()` 展示。当前实现将 `marked.parse()` 的结果直接写入 `innerHTML`。bundled marked v15 会保留原始 HTML、事件属性与 `javascript:` / `data:` URL；因此手写笔记、LLM 输出或外部文本都可能变成可执行页面内容。

本项延续采集流程的安全渲染优化，但只处理 Markdown 与公式这条共享富文本路径。

## 目标

- 保留日常阅读所需的标准 Markdown 与现有四种数学公式分隔符。
- 原始 HTML、远程图片和危险 URL 不得生成可执行或可加载的 DOM 内容。
- 只允许 `http:`、`https:`、`mailto:` 链接；危险链接退化为普通文本。
- KaTeX 明确运行在不信任模式，畸形公式不得中断页面渲染。
- 六个既有入口继续共用一个渲染函数，且不改变其 DOM ID、请求与工作流。

## 非目标

- 不做全站 `innerHTML` 重构；本项不覆盖采集、列表、后台任务或 PDF 视图的其他输出。
- 不支持 Markdown 中嵌入的原始 HTML、SVG、iframe、视频或远程图片。
- 不引入新的第三方前端依赖或联网构建步骤。

## 方案选择

选择一个独立、可在浏览器和 Node 测试中运行的本地 Markdown 渲染模块，而非加入 DOMPurify。

原因是这里可接受的内容语法很窄：Markdown 本身由受控的 marked renderer 生成，原始 HTML 和图片直接禁用，链接和公式有单独的明确定义边界。把策略集中为小模块既保持离线静态资源架构，又能用真实的 marked / KaTeX 进行回归测试。模块不得修改 `window.marked` 的全局配置。

## 模块边界与数据流

新增 `public/markdown-rendering.js`，使用与 `ingest-rendering.js` 一致的浏览器 / CommonJS 兼容封装，并导出：

- `createMarkdownRenderer({ marked, katex })`
- 返回 `render(text)`，生成受控 HTML 字符串；以及 `renderInto(element, text)`，作为唯一写入目标元素的入口。

`public/index.html` 在 `app.js` 前加载该模块。`app.js` 创建一个实例，并让现有 `renderMd(el, text)` 仅委托给 `renderInto()`。笔记加载与保存预览、讲解、译文和划词译文因此仍使用原来的调用点，但共享相同策略。

渲染顺序如下：

1. 先暂存 `$...$`、`$$...$$`、`\\(...\\)`、`\\[...\\]` 中的公式，避免 marked 将 LaTeX 中的 `_` 或链接样式误解析。
2. 使用每次调用独立的 marked renderer 解析其余 Markdown。
3. renderer 只产生标准 Markdown 结构；原始 HTML、图片与危险链接按下文规则退化。
4. 将暂存的公式替换为 KaTeX 的受控输出，再由 `renderInto()` 写入目标元素。

输入文本中的任何一段不直接作为 HTML 字符串拼入输出；唯一例外是受信任的 KaTeX 库产生的公式标记。

## Markdown 安全策略

### 保留的内容

保留 marked 正常生成的段落、标题、强调、删除线、列表、引用、代码、表格、任务列表、分隔线和安全链接。现有样式继续负责这些结构的显示。

### 原始 HTML

所有 Markdown 中的原始 HTML token 都转义后以字面文本显示。例如 `<img src=x onerror=alert(1)>` 和 `<svg onload=...>` 不会创建元素、属性或事件处理器。这样既阻断执行，也让用户能看见原始内容。

### 链接

链接地址去除首尾空白并经 URL 协议校验。仅允许 `http:`、`https:` 与 `mailto:`；协议大小写不影响判断。`javascript:`、`data:`、`vbscript:`、`blob:`、`file:`、协议相对 URL 与无法解析的 URL 一律不生成 `<a>`，仅保留链接标签的行内 Markdown 内容。

### 图片

Markdown 图片一律不生成 `<img>` 或任何资源请求。其 alt 文本作为普通、安全的行内文本保留；不允许 `data:` SVG 或外部跟踪图片绕过该规则。

## KaTeX 安全与容错

公式使用 `katex.renderToString()`，并显式传入：

- `trust: false`
- `throwOnError: false`
- `maxExpand: 1000`

因此 `\\href`、`\\htmlStyle` 等需信任的命令不会生成用户指定的链接、样式或 HTML。公式语法不完整时，KaTeX 的现有可见错误输出继续显示；若 KaTeX 不可用或调用异常，则公式源文本经 HTML 转义后显示。无论哪种情况，单个公式都不得抛出到页面级别。

## 兼容性与错误处理

- `null`、`undefined` 和非字符串 Markdown 输入按空字符串或其可显示字符串处理，不抛出异常。
- 标准的论文链接（通常为 HTTPS）、普通 Markdown 格式与现有公式显示保持可用。
- 被阻止的 HTML、图片和链接只影响该片段，不影响同一段中的其他 Markdown 或后续公式。
- 模块不改变笔记原文、LLM 响应或服务端存储；策略只作用于浏览器展示层。

## 测试与验收

新增 `test/markdown-rendering.test.js`，直接载入本地 marked 与 KaTeX，并至少覆盖：

1. 标题、加粗、列表、代码、HTTPS 链接与普通公式的显示基线。
2. `<img onerror>`、`<svg onload>`、`<script>`、内联 `onclick` 等原始 HTML 仅作为文本，结果不含可解析危险元素或事件属性。
3. 大小写混合的 `javascript:`、`data:`、`blob:`、`file:` 和协议相对 URL 不生成链接或图片；HTTPS 与 mailto 链接仍保留。
4. Markdown 图片只保留 alt 文本，结果没有 `<img>`、`src` 或资源 URL。
5. `\\href{javascript:...}` 与 `\\htmlStyle` 不能产生危险 URL / 属性，并锁定 KaTeX 选项。
6. 畸形或过度展开的公式不抛异常，且输出不含未净化 HTML。
7. 页面接线保证模块在 `app.js` 前加载，六个富文本入口仍通过共享 `renderMd()` 委托。

完成后运行 `npm.cmd test` 与 `.venv\\Scripts\\python.exe -m unittest discover -s test -p "test_*.py"`。再用浏览器验证恶意笔记、讲解或译文只显示字面文本，安全链接和数学公式仍可用。
