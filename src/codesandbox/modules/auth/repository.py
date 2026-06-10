from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from codesandbox.config import get_settings


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    name: str
    password_hash: str | None
    platform_role: str
    status: str


def _connect():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Auth repository requires psycopg.") from exc

    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def find_user_by_email(email: str) -> UserRecord | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id::text, email::text, name, password_hash, platform_role, status
            FROM users
            WHERE email = %s AND deleted_at IS NULL
            """,
            (email,),
        ).fetchone()

    return UserRecord(**row) if row else None


def create_user(email: str, name: str, password_hash: str) -> UserRecord:
    with _connect() as conn:
        row = conn.execute(
            """
            INSERT INTO users (email, name, password_hash)
            VALUES (%s, %s, %s)
            RETURNING id::text, email::text, name, password_hash, platform_role, status
            """,
            (email, name, password_hash),
        ).fetchone()
        conn.commit()

    return UserRecord(**row)


def create_session(
    *,
    user_id: str,
    token_hash: str,
    expires_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (user_id, token_hash, expires_at, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, token_hash, expires_at, ip_address, user_agent),
        )
        conn.commit()


def record_login_attempt(
    *,
    email: str | None,
    ip_address: str | None,
    succeeded: bool,
    failure_reason: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO login_attempts (email, ip_address, succeeded, failure_reason)
            VALUES (%s, %s, %s, %s)
            """,
            (email, ip_address, succeeded, failure_reason),
        )
        conn.commit()
