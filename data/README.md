# data/ 运行时数据目录（隔离业务数据与代码）

- `workbench.db`  SQLite 单文件（业务/全文/向量/会话/配置，02 文档 D3）
- `backups/`      每日备份（VACUUM INTO，保留 7 份，OQ-10）
- `logs/`         后台日志

此目录内容全部由后端自动创建，请勿手工删除 workbench.db（索引/向量可重建，但配置与会话不可）。
