# Paper Reproduction Atlas

`paper-showcase` 是与私有管理系统隔离的 Hexo 静态站点。它只包含导出后的公开 Markdown 与已批准资源，不读取 SQLite、不调用管理系统 API，也不保存私人笔记或本地路径。

展示站不覆盖 Fluid 的 layout、CSS 或目录组件，使用安装的官方 `hexo-theme-fluid` 原生 `index`、`post`、`archive`、`category`、`tag` 和 `about` 模板。后端发布器只负责把研究项目编辑器中的公开标题、摘要、正文、分类、标签、论文信息和附件转换为标准 Hexo 文章；首页和分类页由 Fluid/Hexo 原生生成。

## 内容流

```text
研究项目（私有数据库）
  -> 论文复现或文章 / 博客
  -> 项目状态 completed
  -> 人工批准 approved
  -> 公开内容校验
  -> backend/app/application/showcase_export.py
  -> source/_posts/{reproductions|articles}/<stable-slug>.md
  -> source/images/{reproductions|articles}/<stable-slug>--<artifact-id>.<ext>
  -> Hexo + Fluid
  -> public/
```

重复发布通过 `.showcase/manifest.json` 更新同一篇文章；撤回只删除清单中属于该项目的文件。复现文章归入 `论文复现` 分类，文章/博客归入 `文章` 分类，可从官方 Fluid 的分类页访问。

## 本地运行

Node.js 版本要求见 `.node-version`。

```powershell
cd paper-showcase
npm ci
npm run build
npm run serve
```

站点默认根路径为 `/paper-study/`。本地服务由 Hexo 输出实际访问地址。

## 自动更新

本地开发直接保持 `npm run serve` 运行即可。Hexo 会监听 `source/`，后端公开发布写入文章或附件后，页面会自动重新生成，不需要手动执行 `npm run build`。

如果服务器使用 Nginx/Caddy 托管 `public/`，可让一个常驻进程负责只生成静态文件：

```powershell
cd paper-showcase
npm ci
npm run watch
```

`npm run watch` 会监听公开导出文件并自动更新 `public/`；服务器上应使用 systemd、PM2 或 Windows 服务管理器保持它运行。`npm run build` 只用于首次构建、发布前检查或 CI。

## 从命令行导出

通常应在管理系统的“公开发布”面板中完成保存、校验和发布。也可以对已经批准的项目运行：

```powershell
.venv\Scripts\python.exe scripts\export_showcase.py --project repro_<id>
```

导出命令不会启动 Hexo，也不会把数据库复制进本目录。只要 `npm run serve` 或 `npm run watch` 正在运行，导出完成后会自动更新站点；没有常驻监听进程时，再执行一次 `npm run build` 即可。

## GitHub Pages

`.github/workflows/deploy-paper-showcase.yml` 只在 `paper-showcase/**` 变化时构建这个子项目：

1. `npm ci` 使用本目录锁文件。
2. `npm run build:pages` 按 Pages 提供的站点根路径生成静态文件。
3. 仅上传 `paper-showcase/public/`。

仓库的 Pages Source 需要设置为 **GitHub Actions**。

## 安全边界

- `reproduction_notes` 永不导出。
- 未完成、未批准或校验失败的项目不能发布；复现项目还必须选择复现结论。
- 私有附件 API URL 会改写为站内静态路径。
- 附件必须在项目目录内，并通过大小与 SHA-256 校验。
- HTML 附件、脚本标签、事件处理器、密钥、本地绝对路径和跨项目附件引用会被阻止。
- 展示站构建前还有一次 `public-guard` 静态检查。
