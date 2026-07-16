from __future__ import annotations

import logging
from dataclasses import dataclass

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.identity.models import User

from . import repository

_logger = logging.getLogger(__name__)


@dataclass
class UserPatch:
    platform_role: str | None = None
    status: str | None = None


def get_platform_users(
    *,
    search: str | None = None,
    role: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[User], int]:
    return identity_repo.list_users(
        search=search,
        role=role,
        status=status,
        page=page,
        page_size=page_size,
    )


def update_platform_user(
    user_id: str,
    *,
    platform_role: str | None = None,
    status: str | None = None,
) -> User | None:
    updates: dict = {}
    if platform_role is not None:
        valid_roles = {"user", "system_staff", "system_admin"}
        if platform_role not in valid_roles:
            return None
        updates["platform_role"] = platform_role
    if status is not None:
        valid_statuses = {"active", "inactive", "banned"}
        if status not in valid_statuses:
            return None
        updates["status"] = status
    if not updates:
        return identity_repo.find_user_by_id(user_id)
    return identity_repo.update_user(user_id, **updates)


# Sidebar nav items mapped to the permission key that reveals them —
# used by the role editor's "Sidebar" tab.
SIDEBAR_NAV_MATRIX = [
    {
        "label": "Platform",
        "items": [
            {"title": "Dashboard", "href": "/dashboard", "permission": None},
            {"title": "Users", "href": "/platform/users", "permission": "platform.users.read"},
            {"title": "Organizations", "href": "/platform/organizations", "permission": "platform.organizations.read"},
            {"title": "Application Staff", "href": "/platform/staff", "permission": "platform.staff.read"},
            {"title": "Staff Roles", "href": "/platform/roles", "permission": "platform.roles.read"},
        ],
    },
]


def _format_role_name(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").title()


def get_platform_rbac() -> dict:
    roles = repository.list_roles()
    permissions = repository.list_permissions()
    role_data = []
    for role in roles:
        role_perms = repository.get_permissions_for_role(role.id)
        members = repository.get_role_members(role.id)
        try:
            position = int(role.position or "0")
        except (TypeError, ValueError):
            position = 0
        role_data.append({
            "id": role.id,
            "name": role.name,
            "display_name": _format_role_name(role.name),
            "color": role.color,
            "description": role.description or "",
            "is_system": role.is_system,
            "is_mutable": not role.is_system,
            "position": position,
            "permission_keys": [p.key for p in role_perms],
            "member_count": len(members),
            "members": [
                {"id": m.id, "name": m.name, "email": m.email, "status": m.status}
                for m in members
            ],
        })
    role_data.sort(key=lambda r: (-r["position"], r["display_name"]))
    perm_groups: dict[str, list[dict]] = {}
    for p in permissions:
        if p.group not in perm_groups:
            perm_groups[p.group] = []
        perm_groups[p.group].append({"id": p.id, "key": p.key, "label": p.label})
    return {
        "roles": role_data,
        "permission_groups": [
            {"group": g, "permissions": sorted(perms, key=lambda x: x["key"])}
            for g, perms in sorted(perm_groups.items())
        ],
        "nav_matrix": SIDEBAR_NAV_MATRIX,
    }


def create_platform_role(
    *,
    name: str,
    color: str = "#6366f1",
    description: str | None = None,
) -> tuple[dict | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "Role name is required."
    if repository.get_role_by_name(name):
        return None, f"A role named “{name}” already exists."
    role = repository.create_role(name=name, color=color or "#6366f1", description=description)
    return {"id": role.id}, None


def update_platform_role(
    role_id: str,
    *,
    name: str | None = None,
    color: str | None = None,
    description: str | None = None,
    actor_user_id: str | None = None,
) -> str | None:
    role = repository.get_role(role_id)
    if not role:
        return "Role not found."
    if actor_user_id and not repository.can_actor_manage_role(actor_user_id, role_id):
        return "You cannot edit a role equal to or higher than your own position."
    if name is not None:
        name = name.strip()
        if not name:
            return "Role name is required."
        existing = repository.get_role_by_name(name)
        if existing and existing.id != role.id:
            return f"A role named “{name}” already exists."
        role.name = name
    if color is not None:
        role.color = color
    if description is not None:
        role.description = description
    role.save()
    return None


def duplicate_platform_role(role_id: str, actor_user_id: str | None = None) -> tuple[str | None, str | None]:
    source = repository.get_role(role_id)
    if not source:
        return None, "Role not found."
    if actor_user_id and not repository.can_actor_manage_role(actor_user_id, role_id):
        return None, "You cannot duplicate a role equal to or higher than your own position."
    base = f"{source.name}_copy"
    name = base
    n = 2
    while repository.get_role_by_name(name):
        name = f"{base}_{n}"
        n += 1
    role = repository.create_role(
        name=name,
        color=source.color,
        description=source.description,
    )
    perms = repository.get_permissions_for_role(source.id)
    repository.set_role_permissions(role.id, [p.id for p in perms])
    return role.id, None


def delete_platform_role(role_id: str, actor_user_id: str | None = None) -> str | None:
    if actor_user_id and not repository.can_actor_manage_role(actor_user_id, role_id):
        return "You cannot delete a role equal to or higher than your own position."
    if not repository.delete_role(role_id):
        return "Role not found."
    return None


def toggle_role_permission(role_id: str, permission_key: str, enabled: bool, actor_user_id: str | None = None) -> str | None:
    role = repository.get_role(role_id)
    if not role:
        return "Role not found."
    if actor_user_id and not repository.can_actor_manage_role(actor_user_id, role_id):
        return "You cannot manage permissions for a role equal to or higher than your own position."
    perms = {p.key: p for p in repository.list_permissions()}
    target = perms.get(permission_key)
    if not target:
        return "Unknown permission."
    current = {p.id for p in repository.get_permissions_for_role(role_id)}
    if enabled:
        current.add(target.id)
    else:
        current.discard(target.id)
    repository.set_role_permissions(role_id, list(current))
    return None


def add_role_member(role_id: str, user_id: str, granted_by: str | None = None) -> str | None:
    role = repository.get_role(role_id)
    if not role:
        return "Role not found."
    user = identity_repo.find_user_by_id(user_id)
    if not user:
        return "User not found."
    if granted_by and not repository.can_actor_manage_role(granted_by, role_id):
        return "You cannot assign a role equal to or higher than your own position."
    if granted_by and not repository.can_actor_manage_user(granted_by, user_id):
        return "You cannot manage a staff member equal to or higher than your own position."
    repository.assign_role_to_user(user_id, role_id, granted_by=granted_by)
    return None


def remove_role_member(role_id: str, user_id: str, actor_user_id: str | None = None) -> str | None:
    if actor_user_id and not repository.can_actor_manage_role(actor_user_id, role_id):
        return "You cannot remove a role equal to or higher than your own position."
    if actor_user_id and not repository.can_actor_manage_user(actor_user_id, user_id):
        return "You cannot manage a staff member equal to or higher than your own position."
    repository.remove_role_from_user(user_id, role_id)
    return None


def reorder_platform_roles(role_ids: list[str], actor_user_id: str) -> str | None:
    roles = {str(r.id): r for r in repository.list_roles()}
    ordered_ids = [str(rid) for rid in role_ids if str(rid) in roles]
    if len(ordered_ids) != len(role_ids) or not ordered_ids:
        return "Role order contains an unknown role."
    for role_id in ordered_ids:
        if not repository.can_actor_manage_role(actor_user_id, role_id):
            return "You cannot reorder a role equal to or higher than your own position."
    positions = []
    for role_id in ordered_ids:
        try:
            positions.append(int(roles[role_id].position or "0"))
        except (TypeError, ValueError):
            positions.append(0)
    for role_id, position in zip(ordered_ids, sorted(positions, reverse=True)):
        repository.update_role_position(role_id, position)
    return None


def get_platform_staff() -> list[dict]:
    users = User.objects.filter(deleted_at__isnull=True).all()
    staff = [
        u for u in users
        if u.platform_role in ("system_staff", "system_admin")
    ]
    roles_by_id = {r.id: r for r in repository.list_roles()}
    out = []
    for u in staff:
        assigned = repository.get_user_platform_roles(u.id)
        out.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone or "",
            "platform_role": u.platform_role,
            "status": u.status,
            "role_ids": [r.id for r in assigned],
            "roles": [
                {
                    "id": r.id,
                    "name": _format_role_name(roles_by_id[r.id].name) if r.id in roles_by_id else r.name,
                    "color": r.color,
                }
                for r in assigned
            ],
        })
    return out


def search_platform_role_member_candidates(role_id: str, query: str = "", limit: int = 10) -> list[dict]:
    role_member_ids = {u.id for u in repository.get_role_members(role_id)}
    q = (query or "").strip().lower()
    matches: list[dict] = []

    for user in get_platform_staff():
        if user["id"] in role_member_ids:
            continue
        haystack = f"{user['name']} {user['email']}".lower()
        if q and q not in haystack:
            continue
        matches.append(user)
        if len(matches) >= limit:
            break

    return matches


def save_staff_member(
    *,
    member_id: str | None,
    name: str,
    email: str,
    phone: str | None,
    role_ids: list[str],
    granted_by: str | None = None,
) -> tuple[str | None, str | None]:
    """Create or update a staff member. Returns (member_id, error)."""
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name:
        return None, "Name is required."

    if member_id:
        user = identity_repo.find_user_by_id(member_id)
        if not user:
            return None, "Staff member not found."
        if granted_by and not repository.can_actor_manage_user(granted_by, member_id):
            return None, "You cannot manage a staff member equal to or higher than your own position."
        identity_repo.update_user(member_id, name=name, phone=(phone or None))
    else:
        if not email:
            return None, "Email is required."
        if identity_repo.find_user_by_email(email):
            return None, f"An account with {email} already exists."
        user = identity_repo.create_user(email=email, name=name, password_hash=None)
        identity_repo.update_user(
            user.id,
            platform_role="system_staff",
            phone=(phone or None),
        )
        member_id = user.id
        try:
            from codesandbox.config import get_settings
            from codesandbox.shared.email import send_staff_account_created
            send_staff_account_created(
                to=email,
                name=name,
                login_url=f"{get_settings().app_url}/login",
            )
        except Exception as exc:
            _logger.error("Staff-account-created email failed for %s: %s", email, exc)

    valid_role_ids = {r.id for r in repository.list_roles()}
    wanted = {rid for rid in role_ids if rid in valid_role_ids}
    if granted_by:
        for rid in wanted:
            if not repository.can_actor_manage_role(granted_by, rid):
                return None, "You cannot assign a role equal to or higher than your own position."
    current = {r.id for r in repository.get_user_platform_roles(member_id)}
    for rid in wanted - current:
        repository.assign_role_to_user(member_id, rid, granted_by=granted_by)
    for rid in current - wanted:
        repository.remove_role_from_user(member_id, rid)
    return member_id, None
