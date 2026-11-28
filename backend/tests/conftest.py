"""pytest 全局配置：测试数据目录隔离。

必须在导入 app 之前设置 WORKBENCH_DATA_DIR，使 DB/备份/日志全部落到本次
pytest 会话的独立临时目录——否则 TestClient 会读写真实 data/workbench.db，
测试 teardown（如清空 paths 配置）将污染运行中的真实数据（2026-11-25 事故：
跑测试清掉了用户已保存的 vault_root 配置）。
"""

import os
import tempfile

os.environ.setdefault("WORKBENCH_DATA_DIR", tempfile.mkdtemp(prefix="wb_test_data_"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个测试用全新数据库（会话共享目录下按测试重置，杜绝跨测试残留）。"""
    from pathlib import Path

    from app.core import paths

    for suffix in ("", "-wal", "-shm"):
        Path(str(paths.DB_PATH) + suffix).unlink(missing_ok=True)
    # 实例级配置（数据库存放位置等）也必须逐测试重置，否则上一个测试切换的库位置会污染后续用例
    paths.INSTANCE_FILE.unlink(missing_ok=True)
    yield
