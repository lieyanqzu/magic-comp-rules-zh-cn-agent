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
    # SQL echo（打印每条 SQL 与参数）。即使 dev 也默认关闭：
    # rule_chunks 包含 1024 维 embedding，每条 SELECT 会把 21KB 向量字符串化打印两次，
    # 严重拖慢检索（实测 ~22s/SQL → <1s）。需要排查时单独打开。
    db_echo: bool = False

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
    # 单次响应的 max_tokens 上限。上游（OpenAI/Claude/各种网关）对此值有自己的硬上限，
    # 这里只是默认值，前端可通过 X-LLM-Max-Tokens 覆盖。给得太小会让长答案被截断（已观测）
    # 给得太大会触发 provider 4xx；32K 是大多数生产模型的合理上限。
    llm_max_tokens: int = 32000
    sse_heartbeat_interval: float = 15.0  # SSE 心跳间隔（秒）

    # 检索配置
    retrieval_rrf_k: int = 60  # RRF 融合常数（无 reranker 时的兜底融合）
    retrieval_cache_ttl: int = 300  # 检索结果 Redis 缓存 TTL（秒）
    retrieval_cache_enabled: bool = True
    # 单路召回数：reranker 启用时建议 50，靠它精排；关闭时建议 30 减少 PG 压力
    retrieval_recall_per_branch: int = 50
    # 重排后置信度阈值，影响返回给 LLM 的 confidence_hint
    retrieval_high_threshold: float = 0.7
    retrieval_low_threshold: float = 0.4

    # Reranker（精排）配置：在 hybrid_search 召回后用 cross-encoder 重排
    reranker_enabled: bool = True
    reranker_api_key: str = ""  # 留空时回落到 embedding_api_key → openai_api_key
    reranker_base_url: str = ""  # 留空时回落到 embedding_base_url → openai_base_url
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_timeout: float = 8.0
    reranker_cache_size: int = 256  # 进程内 LRU 大小

    # LLM 响应缓存
    llm_cache_enabled: bool = False
    llm_cache_ttl: int = 600

    # 启动时自动增量入库（基于 content_hash + 删除孤儿）
    # 适合规则文档作为 git 子模块、随时更新的场景；本地频繁重启时建议关闭
    auto_ingest_on_startup: bool = False
    # 自动入库时是否同步生成 embedding（关闭可加快启动，但新增/变更 chunk 没有向量）
    auto_ingest_embeddings: bool = True
    # 入库前是否清洗规则文本（去英文镜像行 / HTML 标签 / 内部链接），仅作用于 cr 文档。
    # 切换此开关会让所有 cr chunk 的 content_hash 变化，下次入库会全量重新生成 embedding。
    cleanup_text_on_ingest: bool = True

    # 前端静态资源目录。设置后 FastAPI 会把它 mount 成 /，配合 SPA history fallback。
    # 留空则不挂载（开发时前端跑独立 dev server）。生产 Docker 镜像会设为 /app/frontend-dist。
    frontend_dist_dir: str = ""

    @property
    def rules_root_path(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / self.rules_root_dir

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


settings = Settings()
