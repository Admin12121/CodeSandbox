from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    secret_key: str
    database_url: str
    session_cookie_name: str
    session_ttl_hours: int
    redis_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str

    def __init__(self) -> None:
        self.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
        self.database_url = os.environ.get(
            "DATABASE_URL",
            "mysql://codesandbox:codesandbox@127.0.0.1:3306/codesandbox",
        )
        self.session_cookie_name = os.environ.get("SESSION_COOKIE_NAME", "cs_session")
        self.session_ttl_hours = int(os.environ.get("SESSION_TTL_HOURS", "24"))
        self.redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.s3_endpoint = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:9000")
        self.s3_access_key = os.environ.get("S3_ACCESS_KEY", "minioadmin")
        self.s3_secret_key = os.environ.get("S3_SECRET_KEY", "minioadmin")
        self.s3_bucket = os.environ.get("S3_BUCKET", "codesandbox")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
