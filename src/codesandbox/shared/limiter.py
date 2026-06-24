from __future__ import annotations

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Instantiated without a Flask app — decorators are registered at import time.
# init_limiter() must be called inside create_app() before any request.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",  # overridden in init_limiter with Redis
    strategy="fixed-window",
)


def init_limiter(app: Flask, redis_url: str) -> None:
    app.config["RATELIMIT_STORAGE_URI"] = redis_url
    app.config["RATELIMIT_STRATEGY"] = "fixed-window"
    app.config["RATELIMIT_HEADERS_ENABLED"] = True
    limiter.init_app(app)
