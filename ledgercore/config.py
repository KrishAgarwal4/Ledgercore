import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables or .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "LedgerCore"
    APP_ENV: str = Field(default="development", description="development | production | test")
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # PostgreSQL Connection URL
    # Supports asyncpg: postgresql://[user[:password]@][host][:port]/[dbname]
    DATABASE_URL: str = Field(
        default="postgresql://localhost:5432/ledgercore",
        description="PostgreSQL connection string"
    )

    # Test Database URL
    TEST_DATABASE_URL: str = Field(
        default="postgresql://localhost:5432/ledgercore_test",
        description="PostgreSQL connection string for test runner"
    )

    # Database connection pool parameters
    DB_POOL_MIN_SIZE: int = 5
    DB_POOL_MAX_SIZE: int = 30
    DB_COMMAND_TIMEOUT_SEC: float = 15.0

    # Idempotency settings
    IDEMPOTENCY_CACHE_TTL_DAYS: int = 30


settings = Settings()
