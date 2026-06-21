from __future__ import annotations

from datetime import datetime, timezone

from nexorm.fields import BooleanField, DateTimeField, ForeignKey, IntegerField, StringField, TextField
from nexorm.model import Model

from codesandbox.features.identity.models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Model):
    id = StringField(primary_key=True, max_length=36)
    name = StringField(max_length=120)
    slug = StringField(max_length=80, unique=True)
    description = TextField(nullable=True)
    logo_url = StringField(max_length=500, nullable=True)
    website = StringField(max_length=255, nullable=True)
    industry = StringField(max_length=80, nullable=True)
    size = StringField(max_length=30, nullable=True)
    location = StringField(max_length=120, nullable=True)
    contact_email = StringField(max_length=255, nullable=True)
    status = StringField(max_length=20, default="active")
    created_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "organizations"


class OrganizationMember(Model):
    id = StringField(primary_key=True, max_length=36)
    org_id = ForeignKey(to=Organization, on_delete="CASCADE")
    user_id = ForeignKey(to=User, on_delete="CASCADE")
    joined_at = DateTimeField(default=_now)

    class Meta:
        table_name = "organization_members"


class OrganizationInvitation(Model):
    id = StringField(primary_key=True, max_length=36)
    org_id = ForeignKey(to=Organization, on_delete="CASCADE")
    email = StringField(max_length=255)
    token = StringField(max_length=64, unique=True)
    status = StringField(max_length=20, default="pending")
    invited_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)
    expires_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "organization_invitations"


class OrganizationRole(Model):
    id = StringField(primary_key=True, max_length=36)
    org_id = ForeignKey(to=Organization, on_delete="CASCADE")
    name = StringField(max_length=80)
    color = StringField(max_length=20, default="#6366f1")
    description = TextField(nullable=True)
    is_system = BooleanField(default=False)
    position = IntegerField(default=0)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "organization_roles"


class OrganizationPermission(Model):
    id = StringField(primary_key=True, max_length=36)
    key = StringField(max_length=100, unique=True)
    label = StringField(max_length=120)
    group = StringField(max_length=60, default="general")

    class Meta:
        table_name = "organization_permissions"


class OrganizationRolePermission(Model):
    id = StringField(primary_key=True, max_length=36)
    role_id = ForeignKey(to=OrganizationRole, on_delete="CASCADE")
    permission_id = ForeignKey(to=OrganizationPermission, on_delete="CASCADE")

    class Meta:
        table_name = "organization_role_permissions"


class OrganizationMemberRole(Model):
    id = StringField(primary_key=True, max_length=36)
    member_id = ForeignKey(to=OrganizationMember, on_delete="CASCADE")
    role_id = ForeignKey(to=OrganizationRole, on_delete="CASCADE")

    class Meta:
        table_name = "organization_member_roles"


class OrganizationDatabase(Model):
    id = StringField(primary_key=True, max_length=36)
    org_id = ForeignKey(to=Organization, on_delete="CASCADE")
    db_name = StringField(max_length=160)
    db_host = StringField(max_length=255, default="127.0.0.1")
    db_port = IntegerField(default=3306)
    db_user = StringField(max_length=160, default="")
    status = StringField(max_length=30, default="provisioning")
    provisioned_at = DateTimeField(nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "organization_databases"
