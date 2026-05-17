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
    embedding_model: str = "text-embedding-3-small"
    mtgch_api_url: str = "https://api.mtgch.com"
    scryfall_api_url: str = "https://api.scryfall.com"
    rules_root_dir: str = ".."

    @property
    def rules_root_path(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / self.rules_root_dir

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


settings = Settings()
