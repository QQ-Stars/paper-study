# Paper Study React Clean-room 工作区设计规范

## 状态与审批

- 状态：设计已批准，可直接进入实施。
- 设计基线批准：2026-08-04。
- 书面规范与后续计划预先批准：2026-08-05。用户明确要求后续不再逐阶段确认，直接在新分支完成并交付。
- 实施分支：`codex/react-clean-room-workspace`。
- 实施目录：`frontend/`。
- React 显式入口：`/workspace/`。
- 旧前端显式入口：`/legacy/`。
- 根入口开关：`UI_ENTRY=react|legacy`，只决定 `/` 的映射。
- 设计原型：`.superpowers/brainstorm/20260804-react-cleanroom/content/` 下的五张独立原型与工程设计页。
- 本规范取代 `2026-08-03-spatial-research-workspace-design.md` 作为 React 重构的视觉与工程依据。旧规范只保留为历史资料，不能向 React 组件继承 DOM、CSS 或视觉结构。

## 背景

Paper Study 当前由 Node.js HTTP Server、SQLite、Python Agent 和一个长期演化的原生前端组成。旧前端覆盖论文管理、PDF 阅读、复习、学术检索、后台任务、洞察和设置，但其全局可变状态、DOM ID、脚本加载顺序、手动事件绑定以及复杂资源生命周期已经形成高耦合。

本项目不是把 `public/index.html` 翻译成 JSX，也不是在旧界面外包一层 React。目标是在保持后端、数据库和业务语义的前提下，从空白的 React 工程、全新的信息架构和已批准的黑曜石/翡翠视觉系统开始，建立独立、可测试、可回退的研究工作区。

## 目标

- 使用 React 19、TypeScript 和 Vite 建立独立前端。
- 保留所有已验证的研究工作流、API、数据库、PDF 来源、复习算法和 NDJSON 协议。
- 以研究任务重新组织 Dashboard、Library、Reader、Reviews、Acquire、Jobs、Insights 和 Settings。
- 将服务端事实、客户端工作区状态、流式会话状态和短生命周期 UI 状态分配给唯一 owner。
- 把 PDF.js、Markdown/KaTeX、Worker、GSAP 和 ECharts 封装为有明确接口与 teardown 的深模块。
- 先在 `/workspace/` 与旧前端并行运行，验证通过后将根入口切换到 React，同时持续保留 `/legacy/` 回退。
- 以用户任务、数据写入、错误恢复、取消、焦点和资源清理为验收对象，不以旧 DOM 选择器或截图为验收对象。

## 非目标

- 不使用 Next.js、SSR、Redux、Bootstrap、大型 UI 组件库、shadcn 默认视觉、WebGL 或 Three.js。
- 不修改数据库结构、复习算法、Python Agent 语义或既有 API payload。
- 不在本轮创建第二套浅色主题；令牌允许以后扩展，但首发只验收批准的暗色主题。
- 不复制旧 HTML 层级、DOM ID、CSS class、样式文件、脚本模板或测试选择器。
- 不把旧前端嵌入 React，也不让两套应用在同一文档中共同挂载。
- 不在完成稳定观察期前删除旧前端。
- 不为视觉效果伪造论文、任务、时间线、图表或精确数字。

## Clean-room 防火墙

### 允许作为证据

- API 路径、HTTP method、payload、响应与错误状态。
- 数据库字段语义、论文 ID、状态、收藏、笔记、复习计划和任务状态。
- 用户可以完成的任务及其必要 loading、empty、success、failure、cancel 状态。
- NDJSON 事件、终止事件、部分成功和连接关闭行为。
- PDF 字节来源、页面渲染、文本选择、双栏过滤、断词和多段缓冲规则。
- Markdown、KaTeX、URL、Worker 和病态输入的安全契约。
- Settings 的脱敏、空 secret 保留和目录创建规则。

### 禁止继承

- `public/index.html` 的层级、ID、导航、布局和组件边界。
- `public/style.css`、`academic.css`、`spatial.css` 的颜色、字体、间距、圆角、阴影或选择器。
- `public/app.js` 的全局变量、函数拆分、调用顺序、`querySelector`、`innerHTML` 和手动事件回绑。
- `window.PaperTitles`、`Ndjson`、`SpatialWorkspace`、`MarkdownRenderingCoordinator`、`pdfjsLib`、`echarts` 等旧全局脚本接口。
- 旧 DOM 集成测试对新 React 组件树的任何约束。
- 旧 UI 截图作为视觉相似度门槛。

构建期必须检查 `frontend/src` 的 import graph，禁止导入 `public/` 或 `/legacy/`。浏览器测试必须断言 React 页面不加载任何旧应用 HTML、CSS 或 JavaScript；只允许继续访问同源 `/api/*`、`/pdfbytes` 和 `/papers/*`。

## 方案选择

设计阶段比较了三种信息组织方式：论文甲板优先、研究台账优先、研究方向画布优先。最终采用“论文甲板优先”。

- Dashboard 用最多五层真实论文构成签名交互，当前论文驱动检查器和真实时间线。
- Library 保持高密度台账，不强行复制甲板。
- Reader 以 PDF 为中性主舞台。
- Acquire/Jobs 使用流式任务工作区。
- Insights 使用数据图表，Settings 使用紧凑表单。

该方案保留参考图最有价值的空间选择语言，同时避免在缺少真实关系数据时制造装饰画布。

## 视觉设计

### 参考图提取

- 桌面外框由全局导航、中央主舞台和上下文检查器形成稳定阅读顺序。
- 信息密度高，但大尺度识别信息集中于中央，细数据集中在边缘带和检查器。
- 近黑、略带绿色倾向的背景；银灰/暗绿表面；翡翠绿只表示当前对象、主动作、焦点与成功。
- 1px 半透明描边、顶部内高光、实体暗色回退构成玻璃与金属层级。
- 论文卡沿 Z/Y 轴轻度展开，选中卡正面化，最多五层，约 7:9 比例。
- 光照来自当前对象，外围克制衰减；禁止粒子、呼吸光和持续循环装饰动画。
- 页面标题 24–28px、卡片标题 16–18px、正文 13–14px、元数据 11–12px；计数使用表格数字或等宽数字。
- 交互通过前移、变亮、描边和检查器同步表达；不依赖 hover 才能理解。

### 已批准原型

1. Desktop Dashboard，1440×900：五层真实论文甲板、右侧检查器、真实时间线。
2. Desktop Library，1440×900：高密度台账、筛选、排序、批量选择和固定预览。
3. Desktop Reader，1440×900：中性 PDF 舞台、页导航、缩放、选文翻译、笔记和 AI 工具。
4. Desktop Acquire/Jobs，1440×900：查询配置、NDJSON 候选流和任务上下文；无任务时显示真实 0 状态。
5. Mobile Dashboard，390×844：单论文聚焦、相邻卡暗示和展开的上下文 sheet；sheet 收起后恢复全局底部导航。

### 设计令牌

| 角色 | 锁定值 |
| --- | --- |
| Obsidian | `#050706` |
| Surface | `#0E1310` |
| Raised | `#1A231D` |
| Emerald | `#36E88A` |
| Emerald Deep | `#0DAA61` |
| Critical | `#FF655C` |
| Warning | `#E8AE53` |
| Primary text | `#F0F5F1` |
| Muted text | `#91A098` |
| Weak text | `#5F6D65` |
| Hairline | `rgba(255,255,255,.09)` |
| Strong line | `rgba(255,255,255,.16)` |

令牌可为对比度或真实浏览器差异做小幅校准，但角色不可改变。翡翠绿不能承担错误或警告语义，也不能铺满所有界面。

### 材质、形状与动效

- 层级顺序：近黑背景 → 暗绿实体表面 → 半透明抬升表面。
- 模糊只用于少量浮层；无 `backdrop-filter` 时必须使用高对比实体表面。
- 控件圆角 6–9px，面板 13px，主舞台 18px，悬浮层上限 24px；禁止无差别胶囊化。
- 微反馈 120–180ms，面板和路由 180–280ms，甲板重排 280–420ms。
- 主要动画属性限定为 `x`、`y`、`z`、`scale`、`rotation`、`autoAlpha`。
- 禁止动画 `width`、`height`、`top`、`left`、`margin`、`padding`。
- `prefers-reduced-motion` 下直接提交终态，保留完全相同的状态、键盘和焦点行为。

## 信息架构与路由

React Router 以 `/workspace` 为 basename：

| 路由 | 布局模式 | 任务 |
| --- | --- | --- |
| `/workspace/dashboard` | inspector rail + timeline | 今日上下文、论文甲板、复习与任务摘要 |
| `/workspace/library` | inspector rail | 搜索、筛选、排序、批量选择、预览和论文 CRUD |
| `/workspace/reader/:paperId` | reader wide | PDF、页导航、笔记、讲解、全文翻译和选文翻译 |
| `/workspace/reviews` | standard | 四组复习队列、开始/完成计划和 Reader 跳转 |
| `/workspace/acquire` | standard + progress | 查询草稿、候选流、核验、入库和本地 PDF |
| `/workspace/jobs/:jobId?` | inspector drawer | Jobs、候选确认、日志与 Schedules |
| `/workspace/insights` | standard | 趋势、馆藏、venue、引用图、推荐和语义检索 |
| `/workspace/settings` | standard | 模型、密钥、目录、研究主题、向量配置和连通性测试 |

`WorkspaceShell` 只负责导航、命令栏、面包屑、Outlet、响应式 Panel Host、Skip Link、全局 announcer 和页面标题。每个 route module 通过 handle 声明标题、布局模式和可选 inspector/timeline slot；Shell 不根据 pathname 猜业务布局。

每条 lazy route 有独立错误边界。任何论文、复习、图表或设置加载失败只影响所属 route 或 panel，不能让整个工作区无法启动。

## 响应式与焦点

### 大于等于 1100px

- 全局导航、主舞台和固定检查器同时可见。
- Dashboard 甲板最多五层。
- Reader 以 PDF 舞台为主要宽度所有者。

### 761–1099px

- 侧栏收窄；检查器进入带遮罩的抽屉。
- 甲板减少可见空间层数，但仍显示准确位置和总数。
- 抽屉打开时记录触发器、锁定必要滚动、建立焦点陷阱；关闭或断点变化时恢复安全焦点。

### 小于等于 760px

- 使用持久全局底部导航。
- 研究队列进入左抽屉，论文检查器进入底部 sheet；一次最多一个模态层。
- 已批准手机原型表示底部 sheet 展开态；sheet 收起后恢复全局底部导航。
- 不依赖 hover，所有主要触控目标至少 44×44 CSS px。

全局导航切页后聚焦页面标题。Reader 内换论文不无故打断当前控件焦点。Escape 关闭最内层浮层，关闭后恢复触发器；流式日志只通过 `aria-live` 播报阶段、完成和可恢复错误，不逐行朗读。

## 功能契约

### Dashboard 与论文甲板

- 数据来自真实 papers/reviews/jobs；时间线只从真实状态变更、复习节点和任务派生。
- 空集时 `selectedIndex=-1`，显示解释性空状态，不生成示意卡片。
- 非空时优先保留仍存在的 `workspaceSelectionId`，否则选择第一篇。
- 单击只选择并同步检查器；Enter、双击或明确“打开阅读”动作进入 Reader。
- ArrowLeft/ArrowRight 和前后按钮在结果集内移动，首尾钳制，不循环。
- 筛选或排序后按 paper id 保留选择；选择消失时选择新结果第一篇。
- 单篇只渲染一层；多篇最多渲染五个真实相邻项；准确总数独立显示。
- 甲板使用 `listbox/option` 与 roving tabindex；指针选择不抢输入焦点，键盘移动后聚焦新卡。

### Library 与论文管理

- 搜索覆盖英文题名、中文题名、venue、type 和 topic。
- 来源支持 all/seed/collected；排序支持 added/relevance/year/citations/title。
- 状态、收藏、年份和来源筛选可组合；语义结果按 score 排序。
- 区分普通无结果、收藏为空和语义无命中。
- 行内显示状态、收藏、PDF、CCF、来源、相关度、引用数和创建时间。
- 支持客户端批量选择和固定预览，但批量选择不能扩张后端 API 语义。
- 新增、编辑、删除、收藏和状态操作必须等待服务端确认；乐观更新可用，但必须可回滚。

### Reviews

- 组别为 overdue、today、upcoming、completed，并支持真实空组。
- 复习间隔固定为 `[0,1,2,4,7,15,30]` 天，相对 `started_at`。
- 论文状态循环固定为 `未开始 → 学习中 → 已理解 → 未开始`。
- 设置为“已理解”由后端自动确保复习计划；前端不得计算或补写计划。
- item 保留论文元数据、current/completed step、next due 和 `total_steps=7`。
- `POST /api/reviews/start` 缺 id 为 400，论文不存在为 404，已有计划返回原计划。
- `POST /api/reviews/complete` 无计划为 404，返回 `{ok, plan, reviews}`；`reviews` 是提交后的权威快照，必须原子替换 review cache。
- 第七轮写 `completed_at`；对已完成计划重复完成保持幂等。

### Reader 与资料

- Reader 的 `:paperId` 是当前论文唯一事实源。
- 读取真实 PDF，惰性渲染 canvas + text layer，缩放范围 50%–300%。
- 读取/保存笔记，读取/生成讲解和全文翻译，支持批量讲解和选文翻译。
- `GET /api/note`、`/api/explainer`、`/api/translation` 返回文本；空字符串是合法空状态。
- 任何 query/mutation 在启动时固定捕获 paper id，响应只能更新该实体；禁止在 `await` 后读取“当前论文”决定写入目标。
- 换论文时取消或丢弃旧响应，并清空原生选区、翻译浮层和多段文本缓冲。
- 缩放只清原生选区和浮层，保留多段文本缓冲，并恢复当前页与页内相对锚点。

### Acquire、本地 PDF、Jobs 与 Schedules

- 学术来源只接受 Semantic Scholar、arXiv、OpenAlex 和 DBLP。
- 研究搜索历史最多保存 12 条，并通过 SafeStorage 访问；存储不可用时退化为内存状态，不阻止工作区启动。
- 查询与至少一个来源必填；`/api/search` max 默认 10、最大 60，默认年份 2024–2026；旧 `/api/ingest` max 上限 50。
- `expand`、`onlyA` 和可编辑 `queries` 保持现有语义；扩展失败回退原 query。
- 已入库候选不可再次勾选；只有确认后才写入论文库。
- 本地目录扫描最多递归四层、最多 2000 个文件；保留 TOTAL/PARSED/ADDED/DUP/SKIP 和部分成功详情。
- Job 状态固定为 pending/running/review/done/failed。活动详情约 2–3 秒轮询，离开 route、任务静止或展开候选详情时停止。
- 候选只能确认选中项；未选项保持 pending，不提供不存在的“忽略”动作。
- Job detail 404、删除 Job 及其候选、确认候选和关闭空任务保持服务端现有语义。
- Schedule 默认七天、最少一天；支持创建、启停、删除。新建 `next_run=now`；服务启动约 8 秒首次检查，之后每 10 分钟；前端不能以本地计时替代调度器。

### Insights

- 趋势和馆藏树从论文 Query 派生；引用图来自服务端。
- 引用边固定为 `src 引用 dst`；箭头、入度/出度和点击解释必须一致。
- 构建完成后重新请求引用图；节点可以打开对应论文。
- 无真实数据或无边时显示解释性空状态，不初始化空 ECharts 图。
- 推荐、embedding、语义检索、venue 规范化和引用构建都是用户显式命令，不作为自动 Query。

### Settings

- DTO 覆盖 provider、baseUrl、model、LLM/S2/embedding 密钥状态与末四位、pdf/explainer/translation 三个目录、researchTheme、embedProvider、embedApiBase 和 embedApiModel。
- secret 输入始终空白；空提交保留旧值，不能回传掩码。
- 非空目录由服务端按现有规则创建；路径解析保持现有语义。
- `POST /api/test-llm` 的业务失败可能仍为 HTTP 200，必须检查 `{ok, output}`。
- 保存、测试中、已保存和失败状态必须明确可见。

## React 工程与模块边界

```text
frontend/
  index.html
  package.json
  vite.config.ts
  src/
    main.tsx
    app/
      App.tsx
      router.tsx
      providers/
      stores/workspaceStore.ts
    components/
      workspace-shell/
      navigation/
      command-bar/
      inspector/
      overlays/
      feedback/
    features/
      dashboard/
      library/
      reader/
      reviews/
      acquire/
      jobs/
      insights/
      settings/
    lib/
      api/
      streaming/
      pdf/
      markdown/
      charts/
      motion/
      accessibility/
      storage/
    styles/
      tokens.css
      reset.css
      global.css
      materials.css
      motion.css
    test/
      renderApp.tsx
      fixtures/
      mocks/
```

依赖方向固定为 `app → components/features → lib → browser/backend`。`lib` 不反向依赖 feature；feature 通过 `index.ts` 暴露小接口，禁止跨目录深层导入和万能 services。`App.tsx` 只组合 Router、QueryClient、错误边界、全局 announcer 和 WorkspaceShell，不包含业务请求。

## 状态所有权

| Owner | 唯一职责 |
| --- | --- |
| React Router | 当前 route、Reader `:paperId`、浏览器前进后退 |
| Zustand | `workspaceSelectionId`、Dashboard/Library 筛选排序、面板、密度和暗色偏好 |
| React Query | Paper、Review、Note、Explainer、Translation、Job、Schedule、Insight、Settings 等服务端事实 |
| Feature reducer | 一次 NDJSON 会话的 run id、阶段、progress、候选、terminal 与错误 |
| Local state | popover、字段草稿、选择缓冲 UI、单页 render 等短生命周期状态 |

`workspaceSelectionId` 只恢复 Dashboard/Library 的最近选择，不包含 Paper DTO。Reader 直接消费 URL；进入 Reader 可以单向记录最近选择，但 store 不得覆盖 URL。可推导计数、结果列表、`canPrevious` 和 `canNext` 不持久化。

## Query 与 mutation

查询键至少包括：

```text
papers.list
papers.detail(id)
artifacts.note(id)
artifacts.explainer(id)
artifacts.translation(id)
reviews.list
jobs.list
jobs.detail(id)
jobs.schedules
settings.current
insights.citeGraph
```

- 收藏/状态：乐观修补 paper list/detail；失败回滚；状态成功后失效 reviews。
- 笔记/讲解/翻译：只更新固定 paper id 的 artifact key。
- 论文增删改/入库：更新 papers list/detail 和相关 artifact；删除时移除该实体缓存。
- Review start/complete：更新 reviews 和受影响 paper；complete 用权威 reviews 快照原子替换。
- Job confirm：任何 terminal 都重取 job detail/list；`added>0` 时再刷新 papers。
- 引用构建只刷新 cite graph；venue 规范化刷新 papers 与相关 Insights。

普通 GET 最多两次总尝试，只重试网络或 5xx；4xx、协议错误、业务冲突和取消不重试。昂贵命令和有副作用的流式任务不自动重启。

## API 客户端与 NDJSON

公开入口固定为：

```ts
api.json<T>(request, decoder): Promise<T>
api.text(request): Promise<string>
api.bytes(request): Promise<ArrayBuffer>
api.ndjson<Event, Result>(request, contract, onEvent): Promise<Result>
```

客户端统一：

- 传递 `AbortSignal`。
- 非 2xx 时读取 JSON 或 text 错误并归一化为 `AppError.http`。
- JSON 必须通过端点 decoder；不得在组件中直接断言未知 payload。
- `AbortError` 保留原身份，不包装成失败 toast。
- NDJSON 使用 `TextDecoder`、跨 chunk 缓冲、行号和端点级事件 decoder。
- 支持残行、无末尾换行、无 stream body 时的单 JSON 降级。
- 拒绝缺失 terminal、重复 terminal 和 terminal 后事件。

### 终止事件

- `result`：title-translations、search、verify-venue、explain、explain-batch、translate、recommend、embed、semsearch、import-pdfs、download-pdfs、norm-venues、cite-build。
- `done`：ingest-selected、jobs/confirm。

### 取消与最终一致性

“合法 terminal”只决定能否显示完整成功，不等于此前没有副作用。

- 纯读取流：只有合法 terminal 才提交结果。
- 有副作用流：terminal、失败或取消后按端点把相关 Query 标记 stale。
- title-translations：取消后轮询 GET 状态至 `running=false`，再刷新 papers 和 pending 状态。
- explain-batch：取消后重取 papers 和 pending 计数；已生成内容继续有效。
- jobs/confirm：即使 `done.ok=false` 也重取 job detail/list；后端可能已经改变候选状态。
- 其余无法观测服务端结束的副作用流：不宣称服务端已取消；下次进入相关 route 时强制 refetch。
- UI 文案使用“已停止接收”，只有服务端端点明确支持取消时才使用“任务已取消”。

## PDF 深模块

每篇论文对应一个递增 generation。所有请求、loading task、页面任务、选择和翻译提交前核对 generation 与固定 paper id。

### `PdfReaderSession`

- `open(id, signal)`：请求 `/pdfbytes?id=…`，创建 loading task，核对 generation 后提交 document。
- `setZoom(scale)`：捕获当前页与页内相对锚点；取消 page render/text task，断开页面 observer，归零 canvas，按新 viewport 重建并恢复锚点；保留 document。
- `dispose()`：幂等。pending 时销毁 loading task；resolved 后由单一 owner 销毁 document，禁止盲目同时 destroy。还要 abort fetch、取消全部页面任务、断开 IntersectionObserver/ResizeObserver、归零 canvas。

### `PdfSelectionController`

- 唯一拥有 mouseup、selectionchange、外部点击 listener、延迟 timer、原生 Selection 和翻译 popover。
- 双栏策略先检测中部 gutter；从某栏开始后只收集同栏且在纵向范围内的 span。
- 默认过滤字号低于页面中位值约 0.7 倍的脚注/上标；几何不可用时才回退原生文本。
- 合并 `represen-\ntation` 一类断词；其他硬换行转空格，段落以 `\n\n` 保留。
- Alt 或“续选”追加多段缓冲；不静默截断超过 6000 字符的输入。
- 缩放清 timer/listener 产生的暂态、原生选区和 popover，但保留文本缓冲；换论文和卸载全部清空。

### `SelectionTranslator`

- 新请求 abort 旧请求；paper generation 变化立即 abort。
- 结果携带 request id、paper id 和 generation；任一不匹配都丢弃。
- `/api/translate-text` 空文本为 400，超过 6000 字符为 413；前端不得静默截断，也不得把失败 payload 当译文。

## Markdown 与 KaTeX

讲解、翻译和笔记文本全部视为不可信输入。

1. Worker 接收 source、job id 和 generation，并设置超时。
2. Worker 返回版本化、可 structured-clone 的受限 AST DTO；不能返回 React element 或 HTML 文档。
3. 原始 HTML 被转义，图片只保留 alt 文本。
4. URL 只允许绝对 `http:`、`https:` 和 `mailto:`；拒绝 relative、fragment、query、`javascript:`、`data:` 和 `file:`。
5. 主线程 adapter 把普通 AST DTO 映射为 React elements。
6. KaTeX 使用 `trust:false`、`maxExpand:1000`；HTML/图片上下文不解析数学。
7. 只有经过 KaTeX/MathML 白名单清洗的字符串可进入唯一 `TrustedMathHtml`。
8. 除 `TrustedMathHtml` 外，项目禁止 `dangerouslySetInnerHTML`，并用 ESLint restricted syntax 和代码审查执行。
9. Worker 异常、超时、病态强调或恶意输入回退为纯 React 文本节点；timeout/terminate 后 late message 不得提交。

Worker、PDF worker 和 KaTeX 字体通过 `new URL(..., import.meta.url)` 交给 Vite 输出。

## GSAP 与 ECharts

### GSAP

- 插件在单一 bootstrap 模块注册一次。
- React 使用 `useGSAP`、scoped ref、`contextSafe` 和 `revertOnUpdate:true`。
- `gsap.matchMedia()` 统一断点和 reduced-motion。
- 面板序列使用 timeline；论文甲板重排使用 Flip。
- 动画只是状态呈现者，不能拥有业务状态、导航或焦点决策。
- 卸载时 revert context、kill timeline/tween、清 delayedCall 和事件监听器。

### ECharts

- 容器尺寸非零后才 init。
- ResizeObserver 只观察所属容器，rAF 合并 resize。
- tooltip 使用 rich text 或统一转义，禁止注入论文元数据 HTML。
- 卸载顺序为 cancel rAF → disconnect observer → off handlers → dispose → clear ref。
- StrictMode 验收看 live resource：探测挂载完全清理，重挂后恰有一个 live instance，最终卸载为零；不要求整个测试期间只创建一次。

## 新旧入口与服务端

共存阶段开始即建立：

- `/workspace/*`：React build、hashed assets 和 SPA fallback。
- `/legacy/*`：原 `public/index.html` 与旧静态资产 alias。
- `/`：由 `UI_ENTRY` 决定映射到 React 或 legacy。

`UI_ENTRY` 在 Node 进程启动时读取，改变后需要重启服务。为避免同一 SPA 维护两个 basename，`UI_ENTRY=react` 时 `/` 重定向到 `/workspace/`；`UI_ENTRY=legacy` 时 `/` 继续直接提供旧首页。两个显式入口始终存在；切换不迁移数据库或重建数据。

静态解析必须用 `path.relative` 或等价方法确认目标仍位于授权目录内。React HTML 使用 `no-cache`，hashed assets 使用长期 immutable 缓存。所有 API 和文件请求保持 origin-relative。

React 页面 CSP 至少包含：

```text
default-src 'self'
script-src 'self'
worker-src 'self'
connect-src 'self'
style-src 'self' 'unsafe-inline'
object-src 'none'
base-uri 'self'
frame-ancestors 'none'
```

PDF.js 设置 `isEvalSupported:false`。首期样式允许 `self` 和必要 inline style，以兼容 PDF text layer、GSAP 和 KaTeX；不得放宽 script eval。

## 测试策略

### 根测试

现有 `npm test` 必须持续通过，作为后端、数据库和旧前端行为基线。

### Vitest

- DTO decoder、错误归一化和 `api.text` 空字符串。
- NDJSON chunk、残行、无末尾换行、非 2xx、无 body、缺失/重复 terminal 和终止后事件。
- 论文甲板 reducer、筛选、排序和选择 reconciliation。
- Review 权威快照、状态显示和竞态。
- PDF 双栏、脚注/上标过滤、断词、硬换行、6000 字符边界和锚点。
- Markdown 恶意向量、URL policy、AST DTO 和数学白名单。
- chart option 和 SafeStorage。

### React Testing Library

- Query loading/error/retry 和 mutation rollback。
- route title、焦点陷阱、Escape、焦点恢复和 live region。
- 未 resolve loading task 即换论文。
- 快速换论文/缩放时旧页面任务取消。
- 旧翻译晚到不得覆盖新 popover。
- Worker timeout/terminate 后 late message 不得提交。
- StrictMode 探测挂载 cleanup、重挂后一个 live owner、最终卸载为零。
- fetch abort、render cancel、observer disconnect、document/loading destroy、Worker terminate、ECharts dispose 和 GSAP revert 的 spy 断言。

### Playwright

- Dashboard/Deck、Library、Reader、Reviews、Acquire、Jobs/Schedules、Insights 和 Settings 完整工作流。
- 真实 PDF、缩放、text layer、多段选择与翻译。
- NDJSON 取消、协议失败、部分成功和恢复。
- 深路由刷新、浏览器前进后退和 Reader URL 单一事实源。
- 键盘、焦点、抽屉/sheet、390×844、平板和 1440×900。
- CSP、worker、font、MIME 和 console 无 error/warn。
- React 页面不加载任何 legacy 资产。

### 泄漏与视觉

- 切 route、换论文、缩放各 20 次；重复启动/取消流、打开图表和面板。
- Canvas、IntersectionObserver、ResizeObserver、Worker、selection controller、chart instance、GSAP context 和监听器 live count 回到基线。
- 内存不得持续增长。
- 五张批准原型为新 UI 视觉基线；旧 UI 截图不参与视觉门槛。
- 验证 reduced-motion 和无 backdrop-filter 回退；PDF 纸张保持中性。

## 分阶段实施与门槛

1. **契约护栏**：创建 frontend、typed API/NDJSON 测试、`/workspace/*` 和 `/legacy/*`。门槛：根测试绿色，`/` 仍 legacy。
2. **只读骨架**：WorkspaceShell、路由、设计令牌、Papers Query、Dashboard、Library、SafeStorage。门槛：不加载任何旧资产。
3. **Reader 纵切**：PDF session、text layer、缩放、选择、只读资料、安全 Markdown。门槛：真实 PDF 与 cleanup/stress 绿色。
4. **写入工作流**：收藏、状态、笔记、复习和论文 CRUD。门槛：API/数据库行为与 legacy 一致，错误可回滚。
5. **长任务与分析**：Acquire、Jobs/Schedules、Insights、ECharts、GSAP/Flip。门槛：取消、失败、部分成功和泄漏测试绿色。
6. **完整验收与切根**：运行全量测试、视觉、CSP、性能和回退演练；通过后把本分支默认根入口切到 React，同时保留 `/legacy/`。用户已在 2026-08-05 预先批准后续设计、计划和交付，因此本阶段不再等待新的确认。
7. **稳定观察**：记录两个正式版本发布，或人工使用日志累计至少 14 个不同活跃使用日；保留完整测试报告和至少一次回退演练。
8. **独立删除旧前端**：只有稳定观察门槛满足后，以独立提交删除。该删除不属于本次即时交付，不能伪造等待期。

### 立即回退触发器

出现以下任一情况，根入口改回 legacy 并重启服务：

- 论文内容或写入目标错误。
- 复习算法、日期或计划偏离。
- CSP 阻断关键资源。
- PDF 内存持续增长或 cleanup 测试失败。
- 关键工作流 P0/P1。
- 数据库异常或任务状态失真。

由于本重构不迁移数据库，回退不需要数据转换。

## 风险与对策

| 风险 | 级别 | 对策 |
| --- | --- | --- |
| 全局论文数组与隐式同步 | P0 | React Query/Zustand/reducer/local state 分 owner；禁止万能 store |
| 跨论文异步污染 | P0 | 请求固定捕获 paper id；route generation 和 query key 二次闸门 |
| DOM ID、innerHTML、旧脚本顺序 | P0 | 独立 ESM 工程；禁止导入 public；React 与 legacy 永不共挂 |
| Markdown/KaTeX 安全回退 | P0 | Worker AST DTO、主线程 React adapter、唯一 TrustedMathHtml |
| PDF loading/render/selection 生命周期 | P0 | 幂等 session、缩放/销毁两级 teardown、selection controller |
| 全局监听器与 StrictMode 重复绑定 | P0 | controller dispose、live resource 计数测试 |
| 有副作用流取消后仍落库 | P1 | 不自动 retry；按端点标记 stale 与权威重取 |
| 旧 mutation 不统一检查失败 | P1 | typed client、服务端确认、乐观回滚、4xx 不重试 |
| Job polling 与 ECharts 实例 | P1 | route-scoped Query polling；adapter off/dispose |
| localStorage 不可用 | P2 | SafeStorage；读取/写入异常不阻断启动 |

## 固定决策与可调细节

固定：

- `frontend/` 独立工程。
- `/workspace/`、`/legacy/` 和 `UI_ENTRY` 三入口策略。
- Router/Zustand/React Query/reducer/local state 的所有权。
- 黑曜石/翡翠暗色视觉角色、论文甲板、移动 sheet/底部导航关系。
- API/数据库/复习/NDJSON/PDF 来源和 Markdown 安全语义。
- PDF 两级 teardown、唯一 TrustedMathHtml、GSAP/ECharts cleanup。
- 行为测试、clean-room 资产断言和旧前端稳定观察期。

可在实施中微调：

- 不改变阅读顺序前提下的精确间距、断点和栏宽。
- 在可读性与性能门槛内的甲板倾角、可见层位置和动效时长。
- 通过对比度验证后的翡翠色轻微校准。
- 检查器在中等宽度进入抽屉的精确断点。

任何改变入口隔离、状态所有权、真实数据绑定、Reader URL 单一事实源、移动主内容优先级、安全边界或回退能力的调整，都不属于可调细节。

## 完成定义

即时交付完成需同时满足：

- `frontend/` 可独立安装、构建和测试。
- 所有八个路由可达，关键业务能力有真实 API 接线和明确状态。
- `/workspace/*` 深路由刷新工作，`/legacy/*` 可用，根入口可逆。
- React 页面不加载旧应用资产。
- 根 `npm test`、frontend unit/component tests、Playwright 关键工作流和生产构建通过。
- PDF、Worker、ECharts、GSAP 和全局 listener 的 cleanup 验证通过。
- 1440×900 与 390×844 视觉检查符合批准基线，无 console error/warn。
- `UI_ENTRY=legacy` 回退演练通过。
- 旧前端仍保留；未伪造 14 天稳定观察门槛。
