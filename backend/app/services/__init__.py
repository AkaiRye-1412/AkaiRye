"""业务服务层（02 文档 3.3 五层中的「服务层」，与 4.1 模块映射一一对应）。

- vault.py        vault 读写 + watchdog 监听 + 冲突检测（M2）
- indexer.py      笔记索引：白名单、8s 防抖、增量、三件套写入（M3/M1）
- ingest.py       PDF 入库队列：状态机、MinerU 子进程调度、切块、向量化（M3/M9，Q-11）
- chunking.py     切块策略（笔记按日期小节 / 手册按章节，FR-RAG-06）
- rag.py          混合检索（FTS5 + sqlite-vec + RRF）+ LLM 生成 + 来源组装（C1/M3）
- dashboard.py    看板统计（进程内缓存，NFR-PERF-04）（M1）
- config_service.py 配置管理（Pydantic 校验、热生效事件）（M8）
- backup.py       每日备份 VACUUM INTO + 7 份轮转（OQ-10）（M8）
- pdf_scan.py     PDF 根目录扫描 → manual 全量镜像同步（M9，Q-15）
- terminal.py     终端会话管理（ConPTY 多标签、进程随标签常驻、内置命令）（M5）

三期服务（git_status.py 等）在 M6 启动时补充。
"""
