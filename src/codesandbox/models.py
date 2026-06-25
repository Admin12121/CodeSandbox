from codesandbox.features.identity.models import ApiKey, LoginAttempt, Session, User, UserPasskey
from codesandbox.features.organizations.models import (
    Organization,
    OrganizationDatabase,
    OrganizationInvitation,
    OrganizationMember,
    OrganizationMemberRole,
    OrganizationPermission,
    OrganizationRole,
    OrganizationRolePermission,
)
from codesandbox.features.platform_admin.models import (
    PlatformPermission,
    PlatformRole,
    PlatformRolePermission,
    PlatformUserRole,
)

__all__ = [
    "User",
    "Session",
    "ApiKey",
    "LoginAttempt",
    "UserPasskey",
    "PlatformRole",
    "PlatformPermission",
    "PlatformRolePermission",
    "PlatformUserRole",
    "Organization",
    "OrganizationMember",
    "OrganizationInvitation",
    "OrganizationRole",
    "OrganizationPermission",
    "OrganizationRolePermission",
    "OrganizationMemberRole",
    "OrganizationDatabase",
]

# Auth extensions — imported here so NexORM registers them for migrations
from codesandbox.features.identity.models import AuthAccount, TwoFactorMethod, VerificationToken  # noqa: F401
# Passkey model registered for migrations
from codesandbox.features.identity.models import UserPasskey as _UserPasskey  # noqa: F401
