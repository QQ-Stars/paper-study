# Paper-Study 路线图

当前主线已经完成：本地 FastAPI 后端、`ui-redesign` 前端、SQLite 迁移、后台任务、
PDF/OCR、全文讲解与翻译、语义检索、复习队列、Obsidian 投影和只读 MCP 服务。

## 当前可用

- Windows 一键启动和停止：`start.cmd` / `stop.cmd`
- 本地数据库自动初始化、迁移、备份和旧种子导入
- 论文采集、手动 PDF 导入、阅读、笔记、进度和复习
- 后台处理队列与定时任务
- 本地 model2vec 语义检索和 P3 trigram 字面检索
- FastAPI 静态托管 `ui-redesign/dist`

## 后续候选

这些项目不会改变当前的单机默认运行方式：

- 更细的 PDF 备份与恢复界面
- 更完整的外部嵌入供应商配置
- MCP 查询工具的更多只读视图
- 多用户、对象存储和公网部署（需要单独的安全设计）

Docker、旧 Node Web 服务和旧前端不在路线图中。
