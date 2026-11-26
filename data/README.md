# data/ 运行时数据目录（隔离业务数据与代码）

- `workbench.db`  SQLite 单文件（业务/全文/向量/会话/配置，02 文档 D3）
- `backups/`      每日备份（VACUUM INTO，保留 7 份，OQ-10）
- `logs/`         后台日志

此目录内容全部由后端自动创建，请勿手工删除 workbench.db（索引/向量可重建，但配置与会话不可）。

## 新环境（clone 后）想立刻看到界面数据

在仓库根执行：

```bash
python scripts/seed_demo.py
```

它只修改配置表里的 `paths.vault_root`（指向本目录的 `demo_vault`，5 篇虚构笔记）
与 `index.whitelist`，**不会读取或写入任何 api_key**；若已配置过别的 vault_root
会拒绝执行，需要显式 `--yes`。

