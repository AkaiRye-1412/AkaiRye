"""路径常量：运行时数据统一隔离在项目根 data/ 目录（02 文档 5 章目录设计）。

WORKBENCH_DATA_DIR 环境变量可覆盖数据目录（测试隔离专用：pytest 经 conftest
写入独立临时目录，防止测试 teardown 污染真实运行数据）。
"""

import os
from pathlib import Path

# 项目根目录 = backend/app/core/paths.py 向上四级（core → app → backend → AkaiRye）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR") or (PROJECT_ROOT / "data"))
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"

DB_PATH = DATA_DIR / "workbench.db"

# 静态前端构建产物目录（生产模式由 FastAPI 托管，02 文档 3.2）
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def ensure_runtime_dirs() -> None:
    """确保运行时目录存在（幂等）。"""
    for d in (DATA_DIR, BACKUP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
