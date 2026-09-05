# 论文复现公开展示架构

日期：2026-08-29

## 决策

采用“结构化公开元数据 + Markdown 正文 + 单向导出器 + 独立 Hexo/Fluid 站点”。私有数据库仍是内容源，展示站只消费导出的静态文件。

## 发布门禁

```text
project.status == completed
AND publication.decision == approved
AND validate_publication_snapshot() == passed
```

复现结论独立于进度，允许：`reproduced`、`partial`、`inconsistent`、`not_reproduced`。

## 字段映射

| 管理系统数据 | Hexo 输出 | 公开规则 |
| --- | --- | --- |
| 公开标题 | `title` | 必填，YAML 安全编码 |
| 公开摘要 | `subtitle` + 结论摘要 | 必填，隐私检查 |
| 稳定 slug | `permalink` | 首次发布后不可修改 |
| 论文年份 | `paper_year` | 用于自定义年份归档 |
| 研究方向 / 会议信息 | `categories` / `venue` | 空值过滤 |
| 项目标签 / 结论 | `tags` | 去重 |
| 复现正文 | Markdown 正文 | 拒绝脚本、密钥和本地路径 |
| 结果对照 | Markdown 表格 | 自动生成 |
| 实验运行 | 运行摘要 | 不导出命令、参数、配置和问题私记 |
| 已批准附件 | `source/images/reproductions/<slug>/` | 项目隔离 + 大小/SHA-256 校验 |
| 私人笔记 | 不导出 | 强制排除 |

## 状态流转

```text
draft -> approved -> validate -> published
  ^          |          |           |
  |          |          v           v
  +----------+-------- failed      stale --republish--> published
                         |           |
                         +-----------+--revoke---------> revoked
```

正文、项目元数据、实验、结果或附件变化会把已发布状态标记为 `stale`。项目一旦离开 `completed`，已有静态页面立即撤回。

## 隔离

- 根管理系统与 `paper-showcase` 使用不同的 `package.json`、锁文件、`node_modules` 和构建目录。
- `paper-showcase` 不导入后端模块，不读取数据库，不访问私有 API。
- `_references/` 仅保留 Fluid/Hexo 源码分析副本并被父仓库忽略。
