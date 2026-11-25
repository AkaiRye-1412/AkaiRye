"""FastAPI 依赖注入。"""

import sqlite3
from collections.abc import Generator

from app.db import database


def get_db() -> Generator[sqlite3.Connection, None, None]:
    with database.get_conn() as conn:
        yield conn
