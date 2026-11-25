"""看板统计服务（M1，FR-DASH / NFR-PERF-04）：note_file 聚合 + 进程内缓存。

TODO(业务实现)：
- summary：进行中项目数 / 本月新增 / 待跟进（active 且 updated_date 距今 > follow_up_days）；
- trend：range ∈ {month, 30d, 6m}（FR-DASH-05 三档，Q-06）→ [{bucket, count}]；
- 缓存失效：note_file 变更事件触发。
"""


class DashboardService:
    def __init__(self) -> None:
        self._cache: dict | None = None

    def summary(self, conn) -> dict:
        raise NotImplementedError("业务实现阶段：指标卡汇总")

    def trend(self, conn, range_key: str) -> list[dict]:
        raise NotImplementedError("业务实现阶段：趋势序列")
