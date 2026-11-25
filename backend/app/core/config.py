"""应用运行配置（环境变量 / 启动参数）。

注意区分两层配置：
- 本模块：进程级运行参数（端口、日志级别），来自环境变量；
- config 表（M8）：业务配置（LLM、路径、白名单等），运行期由设置页面修改、热生效。
"""

from pydantic_settings import BaseSettings


class RuntimeSettings(BaseSettings):
    host: str = "127.0.0.1"      # 安全铁律：仅监听本机回环（需求 3.1）
    port: int = 8686
    log_level: str = "INFO"
    # SQLite 向量扩展是否尝试加载（V1 spike 验证项，失败时降级并记录警告）
    enable_vec_ext: bool = True

    model_config = {"env_prefix": "WORKBENCH_", "env_file": ".env", "extra": "ignore"}


runtime = RuntimeSettings()
