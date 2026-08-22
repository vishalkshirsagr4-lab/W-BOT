import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "College Community Bot"
    VERSION: str = "1.0.0"
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    # Database
    # Render may not provide all env vars; keep optional to avoid startup crash.
    MONGODB_URI: str | None = None
    DATABASE_NAME: str = "college_bot_db"

    # Security
    JWT_SECRET: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # APIs
    AI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    WEATHER_API_KEY: str = ""
    CRICKET_API_KEY: str | None = None
    GEMINI_MODEL: str | None = None

    # Nezuko Assistant
    NEZUKO_WAKE_WORD: str = "nezuko"
    CONVERSATION_TTL_SECONDS: int = 60 * 60 * 24 * 7
    ADMIN_PHONE_NUMBERS: str = "918660108587"
    OWNER_NUMBER: str = "918660108587"

    # Load from .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached instance of the settings.
    lru_cache ensures we don't read the .env file on every single request.
    """
    return Settings()