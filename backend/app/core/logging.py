"""日志配置：输出到控制台 + data/logs/workbench.log。"""

import logging
import sys

from app.core.paths import LOG_DIR


def setup_logging(level: int = logging.INFO) -> None:
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_DIR / "workbench.log", encoding="utf-8"))
    except OSError:  # 目录不可写时仅保留控制台输出
        pass
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
