"""PDF 入库队列服务（M3/M9，FR-RAG-04~10）：状态机 + MinerU 子进程调度（Q-11）。

状态机（8 态，Q-15）：discovered → queue → parsing → ready/failed；
ready + 内容变化 → update → rebuilding（旧 ready 块保持可检索，OQ-12）→ ready。
路径失效 → invalid（重定位后置回 ready，FR-RAG-09）。

TODO(业务实现)：
- 进程内任务队列（D8）消费 ingest_task（持久化，重启恢复 queued/running）；
- MinerU CLI 子进程调用（独立 venv，隔离 torch 重依赖，Q-11 结论 A）；
- 进度经 broker.publish(ingest.progress)（500ms 节流）与 broker.publish(manual.updated)（跃迁即时）。
"""


class IngestService:
    def enqueue(self, conn, manual_ids: list[int], action: str = "ingest") -> list[int]:
        raise NotImplementedError("业务实现阶段：批量入库（幂等：已排队返回既有任务）")

    def run_loop(self) -> None:
        raise NotImplementedError("业务实现阶段：后台队列消费循环")

    def recover_on_startup(self, conn) -> None:
        raise NotImplementedError("业务实现阶段：重启后恢复 queued/running 任务（NFR-DATA-04）")
