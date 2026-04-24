"""Runtime config for ingestion jobs. Reads from env; falls back to local defaults."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore"
    )

    # DB — same pattern as apps/api.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    postgres_user: str | None = Field(default=None, alias="POSTGRES_USER")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")
    postgres_host: str | None = Field(default=None, alias="POSTGRES_HOST")
    postgres_port: str | None = Field(default=None, alias="POSTGRES_PORT")
    postgres_db: str | None = Field(default=None, alias="POSTGRES_DB")

    # Providers.
    polygon_api_key: str | None = Field(default=None, alias="POLYGON_API_KEY")
    fmp_api_key: str | None = Field(default=None, alias="FMP_API_KEY")
    finnhub_api_key: str | None = Field(default=None, alias="FINNHUB_API_KEY")
    fred_api_key: str | None = Field(default=None, alias="FRED_API_KEY")
    voyage_api_key: str | None = Field(default=None, alias="VOYAGE_API_KEY")

    # AWS.
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    raw_data_bucket: str | None = Field(default=None, alias="RAW_DATA_BUCKET")

    log_level: str = Field(default="info", alias="LOG_LEVEL")

    @model_validator(mode="after")
    def _compose_database_url(self) -> Settings:
        if self.database_url:
            return self
        if all([self.postgres_user, self.postgres_password, self.postgres_host, self.postgres_db]):
            port = self.postgres_port or "5432"
            user = quote_plus(self.postgres_user or "")
            pw = quote_plus(self.postgres_password or "")
            self.database_url = (
                f"postgresql+psycopg://{user}:{pw}@{self.postgres_host}:{port}"
                f"/{self.postgres_db}?client_encoding=utf8"
            )
        else:
            self.database_url = (
                "postgresql+psycopg://fii:fii_local_dev@localhost:5432/fii_prism"
                "?client_encoding=utf8"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
