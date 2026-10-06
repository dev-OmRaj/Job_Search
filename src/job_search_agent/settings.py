from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ollama_api_key: str = Field(default="", repr=False)
    ollama_cloud_url: str = "https://ollama.com"
    ollama_cloud_model: str = "gpt-oss:120b"
    ollama_local_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "embeddinggemma"
    embedding_batch_size: int = Field(default=32, ge=1, le=512)
    embedding_max_retries: int = Field(default=2, ge=0, le=10)
    embedding_retry_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    database_url: str = "sqlite:///data/job_search.db"
    company_database_url: str = "sqlite:///data/companies.db"
    export_dir: Path = Path("data/exports")
    resume_dir: Path = Path("resumes")
    user_agent: str = "JobSearchAgent/0.1 (+manual public careers-page research)"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
