from __future__ import annotations

from dataclasses import dataclass

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.identity.models import User

from . import repository


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
    role_data.sort(key=lambda r: (r["position"], r["display_name"]))
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
) -> str | None:
    role = repository.get_role(role_id)
    if not role:
        return "Role not found."
    if role.is_system:
        return "System roles are read-only."
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


def duplicate_platform_role(role_id: str) -> tuple[str | None, str | None]:
    source = repository.get_role(role_id)
    if not source:
        return None, "Role not found."
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


def delete_platform_role(role_id: str) -> str | None:
    if not repository.delete_role(role_id):
        return "System roles cannot be deleted."
    return None


def toggle_role_permission(role_id: str, permission_key: str, enabled: bool) -> str | None:
    role = repository.get_role(role_id)
    if not role:
        return "Role not found."
    if role.is_system:
        return "System roles are read-only."
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
    repository.assign_role_to_user(user_id, role_id, granted_by=granted_by)
    return None


def remove_role_member(role_id: str, user_id: str) -> None:
    repository.remove_role_from_user(user_id, role_id)


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

    valid_role_ids = {r.id for r in repository.list_roles()}
    wanted = {rid for rid in role_ids if rid in valid_role_ids}
    current = {r.id for r in repository.get_user_platform_roles(member_id)}
    for rid in wanted - current:
        repository.assign_role_to_user(member_id, rid, granted_by=granted_by)
    for rid in current - wanted:
        repository.remove_role_from_user(member_id, rid)
    return member_id, None
