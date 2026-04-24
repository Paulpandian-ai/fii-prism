"""Runtime configuration.

Local dev reads from environment variables / .env.local. Production reads from AWS Secrets
Manager via the ECS task role — the API fetches secrets at boot, not via .env files.
"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="NEXT_PUBLIC_APP_ENV")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    # In prod (Fargate), individual POSTGRES_* env vars are injected from the Aurora secret
    # and database_url is composed from them. In local dev, a pre-formed DATABASE_URL wins.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    postgres_user: str | None = Field(default=None, alias="POSTGRES_USER")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")
    postgres_host: str | None = Field(default=None, alias="POSTGRES_HOST")
    postgres_port: str | None = Field(default=None, alias="POSTGRES_PORT")
    postgres_db: str | None = Field(default=None, alias="POSTGRES_DB")

    # Agent providers — not used yet, declared for later sections.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")

    @model_validator(mode="after")
    def _compose_database_url(self) -> "Settings":
        if self.database_url:
            return self
        if all([self.postgres_user, self.postgres_password, self.postgres_host, self.postgres_db]):
            port = self.postgres_port or "5432"
            user = quote_plus(self.postgres_user or "")
            pw = quote_plus(self.postgres_password or "")
            self.database_url = (
                f"postgresql+psycopg://{user}:{pw}@{self.postgres_host}:{port}/{self.postgres_db}"
            )
        else:
            # Fallback for bare `uv run` commands without docker-compose up.
            self.database_url = "postgresql+psycopg://fii:fii_local_dev@localhost:5432/fii_prism"
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
