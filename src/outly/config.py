from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_ENCRYPTION_KEY = "0" * 64


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./outly.db"
    redis_url: str = "redis://localhost:6379"
    encryption_key: str = DEV_ENCRYPTION_KEY
    jwt_access_secret: str = "dev-access-secret"
    jwt_refresh_secret: str = "dev-refresh-secret"
    access_token_expires: str = "15m"
    refresh_token_expires: str = "30d"
    google_client_id: str = ""
    client_url: str = ""
    server_base_url: str = "http://localhost:8000"
    tracking_base_url: str = ""
    attachment_dir: str = "var/attachments"
    port: int = 8000
    env: str = "development"
    worker_concurrency: int = 5
    cooldown_duration_ms: int = 300_000
    stale_sending_threshold_ms: int = 300_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
