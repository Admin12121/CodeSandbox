from __future__ import annotations

from datetime import datetime, timezone

from nexorm.fields import BooleanField, DateTimeField, ForeignKey, StringField, TextField
from nexorm.model import Model

from codesandbox.features.identity.models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformRole(Model):
    id = StringField(primary_key=True, max_length=36)
    name = StringField(max_length=80, unique=True)
    color = StringField(max_length=20, default="#6366f1")
    description = TextField(nullable=True)
    is_system = BooleanField(default=False)
    position = StringField(max_length=10, default="0")
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "platform_roles"


class PlatformPermission(Model):
    id = StringField(primary_key=True, max_length=36)
    key = StringField(max_length=100, unique=True)
    label = StringField(max_length=120)
    group = StringField(max_length=60, default="general")
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "platform_permissions"


class PlatformRolePermission(Model):
    id = StringField(primary_key=True, max_length=36)
    role_id = ForeignKey(to=PlatformRole, on_delete="CASCADE")
    permission_id = ForeignKey(to=PlatformPermission, on_delete="CASCADE")

    class Meta:
        table_name = "platform_role_permissions"


class PlatformUserRole(Model):
    id = StringField(primary_key=True, max_length=36)
    user_id = ForeignKey(to=User, on_delete="CASCADE")
    role_id = ForeignKey(to=PlatformRole, on_delete="CASCADE")
    granted_at = DateTimeField(default=_now)
    granted_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="granted_roles")

    class Meta:
        table_name = "platform_user_roles"
