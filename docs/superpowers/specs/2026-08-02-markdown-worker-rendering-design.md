# Markdown / KaTeX Worker 渲染隔离设计

## 背景与目标

`public/markdown-rendering.js` 已把 Markdown、原始 HTML、链接、图片和 KaTeX 的输出边界收紧，并有独立回归测试。不过它仍在浏览器主线程同步执行。质量复查已证实，畸形 HTML-like 文本、连续 raw HTML、长 URL 中的数学分隔符、低密度强调符号和无效数学分隔符都可能触发秒级解析时间。

本项的目标不是继续给同步扫描器补充输入特例，而是让所有应用内 Markdown / KaTeX 解析在 Worker 中运行。无论输入如何病态，主线程都应在约 200ms 内恢复，并以安全的字面文本降级。

现有的六个 `renderMd(element, text)` 调用点、DOM ID、数据请求和业务流程保持不变。

## 方案选择

考虑过三种方案：

1. 继续为同步解析器添加有界扫描和病态输入预检。
   - 能减少已知案例，但每个新 lexer / tokenizer 边缘条件都可能引入新的主线程阻塞路径。
2. 使用一个常驻 Worker 队列。
   - 可减少启动开销，但单个卡住的任务会占住队列；终止它又会影响其他待处理请求。
3. 每次渲染使用一个独立、可终止的 Worker（采用）。
   - 每个输入都有明确的 200ms 时间边界；超时只终止对应 Worker，不会阻塞或取消其他元素的渲染。

单次 Worker 的启动开销是可接受的，因为本应用仅在笔记、讲解、译文和划词译文更新时渲染，而不是在每次键盘输入时持续重排。安全性和可预测的页面响应优先于这一小段启动成本。

## 架构

新增三个清晰分工的文件：

- `public/markdown-rendering.js`：保留现有可在 Node 和 Worker 中运行的安全核心，继续导出 `createMarkdownRenderer`。它不再由应用主线程直接调用。
- `public/markdown-rendering-worker.js`：Worker 入口。它依次加载 bundled Marked、KaTeX 和安全核心，处理一个 `{ id, text }` 消息，并回传 `{ id, html }` 或错误信号。
- `public/markdown-rendering-coordinator.js`：主线程协调器。它创建、计时和终止 Worker，只负责把已验证的 Worker 结果写入目标元素，或安全地降级为字面文本。

`public/index.html` 在 `app.js` 前加载协调器；Worker 入口不添加为页面脚本。`app.js` 创建一个协调器实例，原有 `renderMd(el, text)` 保持其调用签名，只转交给该实例的 `renderInto(el, text)`。

KaTeX 的 CSS 继续由页面中既有的 `vendor/katex/katex.min.css` 提供；Worker 只生成公式 HTML，不需要 DOM 或样式表访问。

## 主线程协调器

协调器对外提供 `createMarkdownRenderCoordinator(options)`，返回至少包含 `renderInto(element, value)` 的对象。默认 Worker URL 为 `markdown-rendering-worker.js`，默认时限为 200ms；测试可注入 Worker 工厂、时钟和时限。

一次 `renderInto` 的生命周期如下：

1. 将输入安全地转换为可显示文本；无法转换时使用空字符串。
2. 为目标元素递增版本号。若该元素已有未完成任务，立即清理计时器并终止其 Worker。
3. 创建专属 Worker，登记 `message` 与 `error` 回调，启动 200ms 计时器，并发送带有唯一 `id` 的请求。
4. 只有当响应的 `id` 和元素版本仍与当前任务一致，且 `html` 是字符串时，才把结果写入 `element.innerHTML`。
5. Worker 构造、`postMessage`、Worker 错误、异常消息或超时都会终止该 Worker，并把同一份源文本写入 `element.textContent`。这个降级路径绝不使用输入构造 HTML。

新请求会终止同一元素的旧 Worker；不同元素的请求彼此独立。已失效的消息、计时器和错误回调不得覆盖新结果。协调器可以在 Worker 不可用的环境中工作：它立即走安全字面回退，而不是重新在主线程运行解析器。

渲染完成前保留目标元素先前已经安全渲染的内容，避免短暂闪烁；完成、失败或超时后再原子性更新目标。

## Worker 协议与安全核心

Worker 入口以相对于自身的地址加载：

```js
importScripts('vendor/marked.min.js', 'vendor/katex/katex.min.js', 'markdown-rendering.js');
```

初始化一次安全核心：`createMarkdownRenderer({ getMarked, getKatex })`。每次消息仅调用 `render(text)`，并用同一个 `id` 回传结果。若入口初始化或一次消息处理失败，主线程的错误事件或时间限制会触发字面回退。

安全核心现有的策略不变：原始 HTML 字面化、图片仅保留 alt 文本、仅保留 `http:` / `https:` / `mailto:` 链接，KaTeX 固定使用 `trust: false`、`throwOnError: false` 和 `maxExpand: 1000`。Node 中可注入 Marked / KaTeX 的测试接口也保持不变。

Worker 不能安全、通用地序列化页面上任意 Marked hook、walkTokens、renderer 或 extension 函数。因此应用页面的 Worker 路径明确使用仓库 bundled 的默认 Marked / KaTeX 配置；安全核心的可注入宿主配置仅继续服务于 Node 回归测试。当前页面没有依赖运行时注入的 Marked 扩展；若未来需要该能力，应另行设计可序列化的扩展协议，不能把可执行函数跨线程传递。

## 错误处理与用户可见行为

- 正常、快速的输入继续显示已安全转换的 Markdown 与 KaTeX。
- 在 200ms 内未完成的输入显示原文，不执行 HTML，不加载图片，也不创建链接或 KaTeX DOM；页面其余交互保持可用。
- Worker 不支持、加载失败或消息协议异常时采用同样的字面回退。
- 旧异步结果永远不能覆盖随后输入的内容。
- 该机制防止同步解析长期占用主线程，但不会承诺中断浏览器本身的 Worker 启动或垃圾回收；用户可感知的 Markdown 解析工作被隔离并由终止操作约束。

## 测试与验收

新增协调器与 Worker 协议的 Node 测试，至少覆盖：

1. 成功消息写入安全核心返回的 HTML，且清理 Worker 和计时器。
2. 超时、Worker 错误、不可构造 Worker、`postMessage` 失败和异常消息都使用 `textContent` 字面回退。
3. 同一元素连续两次渲染时，旧 Worker 被终止，延迟的旧响应不会覆盖新响应。
4. 不同元素的渲染相互独立。
5. Worker 入口按正确顺序加载本地依赖，并以匹配的 `id` 回传字符串 HTML。
6. 页面脚本顺序与 `renderMd` 委托关系保持正确，旧同步 `marked.parse` / KaTeX 逻辑不再留在 `app.js`。
7. 既有 `test/markdown-rendering.test.js` 全部安全与兼容回归继续通过。

完成后运行完整 Node 测试、Python 测试，并在浏览器中检查普通 Markdown、四种公式分隔符、恶意 HTML / URL / 图片和一个已知病态大输入。浏览器验收的关键标准是：病态输入至多约 200ms 后显示安全原文，页面仍可点击和切换。

## 非目标

- 不重写安全核心的每一个同步扫描算法；Worker 隔离是本项的主线程可用性边界。
- 不支持 Markdown 中的原始 HTML、SVG、iframe、远程图片或任意用户定义的 Marked 可执行扩展。
- 不改变后端接口、数据库、Markdown 源文本或其他页面的 `innerHTML` 使用点。
- 不引入新的第三方依赖或网络请求。
