"""启动入口：python run.py（OQ-05 命令行启动；打包/托盘自启为三期）。"""

import uvicorn

from app.core.config import runtime

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=runtime.host,       # 安全铁律：仅监听 127.0.0.1
        port=runtime.port,
        reload=False,
    )
