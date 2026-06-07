# 多模态幻觉 · 论文学习 App

一个**本地、零依赖**的论文精读工具：左侧论文列表、中间 PDF、右侧「论文讲解 + 我的笔记」。笔记以 `notes/*.md` 存储，纳入 git 版本控制。

## 运行

> 需要 Node.js（已验证 v22）。**无需 npm install**（纯内置模块）。

```bash
cd study-app
node server.js
```

然后浏览器打开 **http://localhost:5173**

## 目录结构

```
study-app/
├─ server.js          # 零依赖本地服务（静态 + PDF 流 + 笔记读写 API）
├─ config.json        # papersDir 指向 PDF 文件夹（默认 ../paper），端口
├─ public/            # 前端（index.html / style.css / app.js / vendor/marked）
├─ data/
│  ├─ papers.json     # 38 篇论文元数据（标题/会议/年份/方向/2024学习顺序）
│  └─ progress.json   # 每篇学习状态（未开始/学习中/已理解）
└─ notes/             # ★ 学习笔记，每篇一个 .md（git 跟踪）
```

## 用法
- **看 PDF**：点左侧任一论文，中间显示 PDF。
- **看讲解**：右侧「📖 论文讲解」读取 `../paper/<同名>.md`（已写好的详细讲解，只读）。
- **记笔记**：右侧「✍️ 我的笔记」可编辑、保存到 `notes/<同名>.md`。
  - 你也可以在与 Claude 的对话里说「**记录**」，由它直接写入该文件。
- **进度**：右上角状态下拉（未开始/学习中/已理解），左侧圆点同步（灰/橙/绿）。
- **学习顺序**：2024 论文带序号①②③…，按推荐顺序精读。

## 说明
- PDF **不入 git**（体积大），通过相对路径 `../paper/` 引用现有文件夹。
- 换机器时只需保证 `../paper/` 里有同名 PDF 即可；笔记随 git 同步。
