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
    MONGODB_URI: str
    DATABASE_NAME: str = "college_bot_db"

    # Security
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # APIs
    AI_API_KEY: str
    WEATHER_API_KEY: str = ""

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