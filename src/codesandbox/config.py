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
        def env_bool(name: str, default: bool = False) -> bool:
            raw = os.environ.get(name)
            if raw is None:
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

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
        self.s3_artifact_bucket = os.environ.get("S3_ARTIFACT_BUCKET", "codesandbox-artifacts")
        self.github_client_id = os.environ.get("GITHUB_CLIENT_ID", "")
        self.github_client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
        self.google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        self.google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        self.app_url = os.environ.get("APP_URL", "http://localhost")
        self.resend_api_key = os.environ.get("RESEND_APIKEY", "")
        self.from_email = os.environ.get("FROM_EMAIL", "CodeSandbox <noreply@admin12121.com>")
        self.cookie_secure = os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
        self.worker_callback_token_ttl_seconds = int(os.environ.get("WORKER_CALLBACK_TOKEN_TTL_SECONDS", "900"))
        self.control_plane_internal_url = os.environ.get(
            "CONTROL_PLANE_INTERNAL_URL", "http://app:5000"
        ).rstrip("/")
        self.sandbox_job_signing_key = os.environ.get(
            "SANDBOX_JOB_SIGNING_KEY", self.secret_key
        )
        self.sandbox_allowed_images = tuple(
            image.strip()
            for image in os.environ.get("SANDBOX_ALLOWED_IMAGES", "").split(",")
            if image.strip()
        )
        self.sandbox_require_image_allowlist = env_bool(
            "SANDBOX_REQUIRE_IMAGE_ALLOWLIST", False
        )
        self.sandbox_provisioning_stale_seconds = int(
            os.environ.get("SANDBOX_PROVISIONING_STALE_SECONDS", "300")
        )
        self.sandbox_heartbeat_stale_seconds = int(
            os.environ.get("SANDBOX_HEARTBEAT_STALE_SECONDS", "45")
        )
        self.sandbox_stopping_stale_seconds = int(
            os.environ.get("SANDBOX_STOPPING_STALE_SECONDS", "120")
        )
        self.sandbox_cleanup_stale_seconds = int(
            os.environ.get("SANDBOX_CLEANUP_STALE_SECONDS", "300")
        )
        self.sandbox_reconcile_interval_seconds = int(
            os.environ.get("SANDBOX_RECONCILE_INTERVAL_SECONDS", "30")
        )
        self.worker_offline_timeout_seconds = int(
            os.environ.get("WORKER_OFFLINE_TIMEOUT_SECONDS", "30")
        )
        self.sandbox_artifact_retention_days = int(
            os.environ.get("SANDBOX_ARTIFACT_RETENTION_DAYS", "30")
        )
        self.sandbox_max_upload_bytes = int(
            os.environ.get("SANDBOX_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))
        )
        self.nats_url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        # Read explicitly rather than relying on Flask's `flask run --debug` CLI flag,
        # since the app is served via `uvicorn` (ASGI) in dev, which never sets it.
        self.debug = os.environ.get("FLASK_DEBUG", "0") == "1"
        self.billing_dev_topup_enabled = env_bool("BILLING_DEV_TOPUP_ENABLED", False)

        # ── Billing: Stripe (GBP topups) ──────────────────────────────────
        self.stripe_secret_key = os.environ.get("STRIPE_SECRET_KEY", "")
        self.stripe_publishable_key = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
        self.stripe_webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

        # ── Billing: eSewa ePay v2 (NPR topups) ───────────────────────────
        # Defaults are eSewa's published UAT test credentials — fine for
        # dev, must be overridden via env in production.
        self.esewa_product_code = os.environ.get("ESEWA_PRODUCT_CODE", "EPAYTEST")
        self.esewa_secret_key = os.environ.get("ESEWA_SECRET_KEY", "8gBm/:&EnhH.1/q")
        self.esewa_form_url = os.environ.get(
            "ESEWA_FORM_URL", "https://rc-epay.esewa.com.np/api/epay/main/v2/form"
        )
        self.esewa_status_url = os.environ.get(
            "ESEWA_STATUS_URL", "https://rc.esewa.com.np/api/epay/transaction/status/"
        )

        # ── Billing: NRB forex (GBP/NPR display rate) ─────────────────────
        self.nrb_forex_url = os.environ.get(
            "NRB_FOREX_URL", "https://www.nrb.org.np/api/forex/v1/rates"
        )
        self.nrb_forex_cache_ttl_seconds = int(os.environ.get("NRB_FOREX_CACHE_TTL_SECONDS", "21600"))  # 6h


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
