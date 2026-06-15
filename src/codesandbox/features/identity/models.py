from __future__ import annotations

from datetime import datetime, timezone

from nexorm.fields import (
    BooleanField,
    DateTimeField,
    ForeignKey,
    StringField,
    TextField,
    UUIDField,
)
from nexorm.model import Model
from nexorm.uuid import uuid7


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Model):
    id = UUIDField(primary_key=True, default=uuid7)
    email = StringField(max_length=255, unique=True)
    name = StringField(max_length=255)
    password_hash = TextField(nullable=True)
    platform_role = StringField(max_length=30, default="user")
    status = StringField(max_length=20, default="active")
    email_verified = BooleanField(default=False)
    phone = StringField(max_length=30, nullable=True)
    last_login_at = DateTimeField(nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)
    deleted_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "users"


class Session(Model):
    id = UUIDField(primary_key=True, default=uuid7)
    user_id = ForeignKey(to=User, on_delete="CASCADE")
    token_hash = StringField(max_length=64, unique=True)
    expires_at = DateTimeField()
    ip_address = StringField(max_length=45, nullable=True)
    user_agent = TextField(nullable=True)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "sessions"


class ApiKey(Model):
    id = UUIDField(primary_key=True, default=uuid7)
    user_id = ForeignKey(to=User, on_delete="CASCADE")
    name = StringField(max_length=100)
    key_hash = StringField(max_length=64, unique=True)
    prefix = StringField(max_length=10)
    expires_at = DateTimeField(nullable=True)
    last_used_at = DateTimeField(nullable=True)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "api_keys"


class LoginAttempt(Model):
    id = UUIDField(primary_key=True, default=uuid7)
    email = StringField(max_length=255, nullable=True, index=True)
    ip_address = StringField(max_length=45, nullable=True, index=True)
    succeeded = BooleanField(default=False)
    failure_reason = StringField(max_length=50, nullable=True)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "login_attempts"
