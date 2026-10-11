from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application Settings
    PROJECT_NAME: str = "Social Media Studio"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "sqlite:///./social_media.db"

    @property
    def normalized_database_url(self) -> str:
        """Ensure standard postgresql:// scheme for SQLAlchemy 2.x compatibility."""
        if self.DATABASE_URL.startswith("postgres://"):
            return self.DATABASE_URL.replace("postgres://", "postgresql://", 1)
        return self.DATABASE_URL

    # Platform Adapters Configuration
    DEFAULT_PUBLISHER: str = "mock_x"
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    ENABLE_MOCK_X: bool = True
    ENABLE_MOCK_LINKEDIN: bool = True
    # Scheduler Configuration
    SCHEDULER_POLL_INTERVAL_SECONDS: float = 2.0
    SCHEDULER_BATCH_SIZE: int = 10

@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()


settings = get_settings()
