"""路径常量：运行时数据统一隔离在项目根 data/ 目录（02 文档 5 章目录设计）。

WORKBENCH_DATA_DIR 环境变量可覆盖数据目录（测试隔离专用：pytest 经 conftest
写入独立临时目录，防止测试 teardown 污染真实运行数据）。

数据库文件位置（程序自身的 workbench.db）可自定义：因为配置本身存在数据库里，位置不能
存进数据库（鸡生蛋），所以单独落 data/instance.json 的 `db_file_dir`。
注意它与设置页「数据库索引位置」（paths.db_roots，知识库要解析的资料目录）是两回事。
"""

import json
import os
from pathlib import Path

# 项目根目录 = backend/app/core/paths.py 向上四级（core → app → backend → AkaiRye）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR") or (PROJECT_ROOT / "data"))
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"

# 默认数据库文件（未自定义位置时使用）
DB_PATH = DATA_DIR / "workbench.db"

# 数据库位置等「启动前必须知道」的实例级配置（不存数据库）
INSTANCE_FILE = DATA_DIR / "instance.json"

# 静态前端构建产物目录（生产模式由 FastAPI 托管，02 文档 3.2）
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def load_instance() -> dict:
    try:
        data = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_instance(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INSTANCE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def db_file_dir() -> str:
    """已设定的数据库文件目录（workbench.db 所在目录）；未设定返回空串。

    兼容旧键 db_roots（早期把「数据库文件位置」存成列表，仅第 1 个生效）。
    """
    inst = load_instance()
    cur = str(inst.get("db_file_dir") or "").strip()
    if cur:
        return cur
    legacy = inst.get("db_roots") or []
    if isinstance(legacy, list):
        for x in legacy:
            s = str(x).strip()
            if s:
                return s
    return ""


def db_path() -> Path:
    """当前生效的数据库文件路径。

    优先级：WORKBENCH_DB_PATH 环境变量（测试/运维）> instance.db_file_dir > data/workbench.db。
    """
    override = os.environ.get("WORKBENCH_DB_PATH")
    if override:
        return Path(override).expanduser()
    custom = db_file_dir()
    if custom:
        return Path(custom).expanduser() / "workbench.db"
    return DB_PATH


def ensure_runtime_dirs() -> None:
    """确保运行时目录存在（幂等）。"""
    for d in (DATA_DIR, BACKUP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    db_path().parent.mkdir(parents=True, exist_ok=True)
