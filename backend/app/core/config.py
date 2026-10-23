from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_DIR / ".env", ".env"), extra="ignore")

    database_url: str = "sqlite:///./cyberassist.db"

    llm_provider: str = "mock"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60.0

    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_dim: int = 384

    rag_top_k: int = 5
    rag_min_score: float = 0.05
    source_max_age_days: int = 180  # older last_verified dates are shown as "stale"
    knowledge_base_dir: str = str(ROOT_DIR / "knowledge_base")

    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    max_message_chars: int = 6000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
