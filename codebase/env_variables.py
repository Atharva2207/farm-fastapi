from typing import List
from pydantic_settings import BaseSettings
import sys
from pathlib import Path


class Settings(BaseSettings):
    db_usr: str
    db_pwd: str
    db_host: str
    db_port: int
    db_name: str
    redis_host: str
    redis_port: int
    GOV_API_KEY: str
    GOV_API_URL: str
    secret_key: str
    ALGORITHM: str
    TOMORROW_IO_BASE_URL: str
    TOMORROW_IO_API_KEY: str
    SENTINEL_HUB_CLIENT_ID: str
    SENTINEL_HUB_CLIENT_SECRET: str
    allowed_origins: List[str] = []

    class Config:
        env_file = Path(Path(__file__).resolve().parent) / ".env"
        print("server started successfully!!")


setting = Settings()
