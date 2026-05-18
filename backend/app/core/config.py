"""应用配置模块，使用 Pydantic Settings 管理环境变量。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，从环境变量或 .env 文件加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/mtg_judge"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # Embedding 配置（可独立于 chat API）
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "BAAI/bge-m3"
    mtgch_api_url: str = "https://mtgch.com/api/v1"
    rules_root_dir: str = ".."

    # 数据库连接池
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # 安全配置
    api_key: str = ""  # 空则跳过认证
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60  # 每窗口最大请求数
    rate_limit_window: int = 60  # 窗口秒数
    cors_origins: str = "*"  # 逗号分隔的允许来源，* 表示全部
    # 是否信任反向代理传入的 X-Forwarded-For/X-Real-IP 头。
    # 仅在前置了可信反代（nginx、ingress、ALB 等）时设为 true，否则攻击者可伪造 IP 绕过限流。
    trust_proxy_headers: bool = False
    # 同时被信任的代理跳数（从 X-Forwarded-For 右侧第 N+1 个 IP 取真实客户端）。
    # 默认 1 表示只剥一层；K8s ingress + service mesh 可能需要更大值。
    trusted_proxy_hops: int = 1

    # LLM 编排配置
    llm_max_tool_rounds: int = 5
    llm_temperature: float = 0.1
    llm_request_timeout: float = 120.0
    llm_max_retries: int = 3
    llm_retry_min_wait: float = 1.0
    llm_retry_max_wait: float = 10.0
    sse_heartbeat_interval: float = 15.0  # SSE 心跳间隔（秒）

    # 检索配置
    retrieval_rrf_k: int = 60  # RRF 融合常数
    retrieval_cache_ttl: int = 300  # 检索结果 Redis 缓存 TTL（秒）
    retrieval_cache_enabled: bool = True

    # LLM 响应缓存
    llm_cache_enabled: bool = False
    llm_cache_ttl: int = 600

    @property
    def rules_root_path(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / self.rules_root_dir

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


settings = Settings()
