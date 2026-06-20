from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from nexorm.exceptions import DoesNotExist

from .models import (
    Organization,
    OrganizationDatabase,
    OrganizationInvitation,
    OrganizationMember,
    OrganizationMemberRole,
    OrganizationPermission,
    OrganizationRole,
    OrganizationRolePermission,
)


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] or "org"


def list_organizations(
    *,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Organization], int]:
    qs = Organization.objects.filter()
    if status and status != "all":
        qs = qs.filter(status=status)
    all_orgs = qs.order_by("-created_at").all()
    if search:
        q = search.lower()
        all_orgs = [o for o in all_orgs if q in o.name.lower() or q in o.slug.lower()]
    total = len(all_orgs)
    offset = (page - 1) * page_size
    return all_orgs[offset : offset + page_size], total


def get_organization(org_id: str) -> Organization | None:
    try:
        return Organization.objects.get(id=org_id)
    except DoesNotExist:
        return None


def get_organization_by_slug(slug: str) -> Organization | None:
    return Organization.objects.filter(slug=slug).first()


def create_organization(
    *,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> Organization:
    slug = _slugify(name)
    base_slug = slug
    counter = 1
    while get_organization_by_slug(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1
    org = Organization(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        description=description,
        created_by=created_by,
    )
    org.save()
    seed_org_roles(org.id)  # roles must exist before add_member tries to assign owner
    if created_by:
        add_member(org_id=org.id, user_id=created_by)
    return org


def update_organization(org_id: str, **kwargs) -> Organization | None:
    org = get_organization(org_id)
    if org is None:
        return None
    for key, value in kwargs.items():
        setattr(org, key, value)
    org.updated_at = datetime.now(timezone.utc)
    org.save()
    return org


def get_member(org_id: str, user_id: str) -> OrganizationMember | None:
    return OrganizationMember.objects.filter(org_id=org_id, user_id=user_id).first()


def add_member(org_id: str, user_id: str) -> OrganizationMember:
    existing = get_member(org_id, user_id)
    if existing:
        return existing
    member = OrganizationMember(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
    )
    member.save()
    owner_role = OrganizationRole.objects.filter(org_id=org_id, name="owner").first()
    if owner_role:
        mr = OrganizationMemberRole(
            id=str(uuid.uuid4()),
            member_id=member.id,
            role_id=owner_role.id,
        )
        mr.save()
    return member


def remove_member(org_id: str, user_id: str) -> None:
    member = get_member(org_id, user_id)
    if member:
        roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
        for r in roles:
            r.delete()
        member.delete()


def list_members(org_id: str) -> list[OrganizationMember]:
    return OrganizationMember.objects.filter(org_id=org_id).all()


def get_member_count(org_id: str) -> int:
    return OrganizationMember.objects.filter(org_id=org_id).count()


def create_invitation(
    org_id: str,
    email: str,
    invited_by: str | None = None,
) -> OrganizationInvitation:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    inv = OrganizationInvitation(
        id=str(uuid.uuid4()),
        org_id=org_id,
        email=email,
        token=token,
        invited_by=invited_by,
        expires_at=expires_at,
    )
    inv.save()
    return inv


def list_org_roles(org_id: str) -> list[OrganizationRole]:
    return OrganizationRole.objects.filter(org_id=org_id).order_by("position").all()


def seed_org_roles(org_id: str) -> None:
    defaults = [
        ("owner", "#ef4444", True),
        ("admin", "#f59e0b", True),
        ("member", "#6366f1", True),
    ]
    for name, color, is_system in defaults:
        if not OrganizationRole.objects.filter(org_id=org_id, name=name).first():
            role = OrganizationRole(
                id=str(uuid.uuid4()),
                org_id=org_id,
                name=name,
                color=color,
                is_system=is_system,
            )
            role.save()


# ── User-facing repository functions ─────────────────────────────────────────


def get_user_organizations(user_id: str) -> list[Organization]:
    """Return all organizations where the given user is a member."""
    memberships = OrganizationMember.objects.filter(user_id=user_id).all()
    orgs = []
    for m in memberships:
        org = get_organization(m.org_id)
        if org is not None:
            orgs.append(org)
    return orgs


def get_members_with_info(org_id: str) -> list[dict]:
    """Return members with user name/email and their role names."""
    from codesandbox.features.identity import repository as identity_repo

    members = OrganizationMember.objects.filter(org_id=org_id).all()
    result = []
    for m in members:
        user = identity_repo.find_user_by_id(m.user_id)
        if user is None:
            continue
        member_roles = OrganizationMemberRole.objects.filter(member_id=m.id).all()
        role_names = []
        role_colors = []
        role_ids = []
        for mr in member_roles:
            role = None
            try:
                role = OrganizationRole.objects.get(id=mr.role_id)
            except Exception:
                pass
            if role:
                role_names.append(role.name)
                role_colors.append(role.color)
                role_ids.append(role.id)
        result.append({
            "id": m.id,
            "user_id": m.user_id,
            "name": user.name,
            "email": user.email,
            "roles": role_names,
            "role_colors": role_colors,
            "role_ids": role_ids,
            "joined_at": m.joined_at,
        })
    return result


def create_org_role(
    org_id: str,
    name: str,
    color: str,
    description: str | None = None,
) -> OrganizationRole:
    role = OrganizationRole(
        id=str(uuid.uuid4()),
        org_id=org_id,
        name=name.strip(),
        color=color or "#6366f1",
        description=description,
        is_system=False,
    )
    role.save()
    return role


def delete_org_role(role_id: str) -> bool:
    """Delete a custom (non-system) role. Returns False if system role."""
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False
    if role.is_system:
        return False
    for rp in OrganizationRolePermission.objects.filter(role_id=role_id).all():
        rp.delete()
    for mr in OrganizationMemberRole.objects.filter(role_id=role_id).all():
        mr.delete()
    role.delete()
    return True


def assign_role_to_member(member_id: str, role_id: str) -> bool:
    if OrganizationMemberRole.objects.filter(member_id=member_id, role_id=role_id).first():
        return True
    mr = OrganizationMemberRole(
        id=str(uuid.uuid4()),
        member_id=member_id,
        role_id=role_id,
    )
    mr.save()
    return True


def remove_role_from_member(member_id: str, role_id: str) -> None:
    mr = OrganizationMemberRole.objects.filter(member_id=member_id, role_id=role_id).first()
    if mr:
        mr.delete()


def find_invitation_by_token(token: str) -> OrganizationInvitation | None:
    return OrganizationInvitation.objects.filter(token=token).first()


def mark_invitation_accepted(invitation: OrganizationInvitation) -> None:
    invitation.status = "accepted"
    invitation.save()


def ensure_creator_is_owner(org_id: str, user_id: str) -> None:
    """Repair orgs created before the seed-before-add-member fix."""
    if not OrganizationRole.objects.filter(org_id=org_id, name="owner").first():
        seed_org_roles(org_id)
    owner_role = OrganizationRole.objects.filter(org_id=org_id, name="owner").first()
    if not owner_role:
        return
    member = get_member(org_id, user_id)
    if not member:
        member = OrganizationMember(id=str(uuid.uuid4()), org_id=org_id, user_id=user_id)
        member.save()
    if not OrganizationMemberRole.objects.filter(member_id=member.id, role_id=owner_role.id).first():
        mr = OrganizationMemberRole(id=str(uuid.uuid4()), member_id=member.id, role_id=owner_role.id)
        mr.save()


def delete_member(member_id: str) -> None:
    """Delete OrganizationMemberRole rows then the member itself."""
    roles = OrganizationMemberRole.objects.filter(member_id=member_id).all()
    for r in roles:
        r.delete()
    try:
        member = OrganizationMember.objects.get(id=member_id)
        member.delete()
    except Exception:
        pass


def get_org_database(org_id: str) -> OrganizationDatabase | None:
    return OrganizationDatabase.objects.filter(org_id=org_id).first()


def provision_org_database(org_id: str) -> OrganizationDatabase | None:
    """
    Create a per-org MySQL database from the cyberrange_org_template schema.
    Stores the mapping in organization_databases. Idempotent — skips if already exists.
    """
    existing = get_org_database(org_id)
    if existing and existing.status == "ready":
        return existing

    org = get_organization(org_id)
    if org is None:
        return None

    db_name = f"cyberrange_org_{org.slug.replace('-', '_')}"

    if existing is None:
        existing = OrganizationDatabase(
            id=str(uuid.uuid4()),
            org_id=org_id,
            db_name=db_name,
            status="provisioning",
        )
        existing.save()
    else:
        existing.db_name = db_name
        existing.status = "provisioning"
        existing.updated_at = datetime.now(timezone.utc)
        existing.save()

    try:
        import os
        import pymysql
        from codesandbox.config import get_settings
        settings = get_settings()
        db_url = settings.database_url
        # Parse mysql://user:pass@host:port/dbname
        import urllib.parse as _up
        parsed = _up.urlparse(db_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 3306
        user = parsed.username or "root"
        password = parsed.password or ""

        conn = pymysql.connect(host=host, port=port, user=user, password=password, charset="utf8mb4")
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")

        # Run the template SQL against the new database
        template_path = os.path.join(os.path.dirname(__file__), "../../../../docs/cyberrange_organization_schema_mysql.sql")
        template_path = os.path.normpath(template_path)
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                sql_text = f.read()
            # Swap the CREATE DATABASE / USE statements to target the new DB name
            import re as _re
            sql_text = _re.sub(r"CREATE DATABASE IF NOT EXISTS `[^`]+`[^;]+;", "", sql_text, flags=_re.IGNORECASE)
            sql_text = _re.sub(r"USE `[^`]+`;", f"USE `{db_name}`;", sql_text, flags=_re.IGNORECASE)
            sql_text = _re.sub(r"SET foreign_key_checks\s*=\s*0;", "SET foreign_key_checks = 0;", sql_text, flags=_re.IGNORECASE)
            conn.select_db(db_name)
            cur.execute("SET foreign_key_checks = 0")
            for stmt in _split_sql_statements(sql_text):
                stmt = stmt.strip()
                if stmt:
                    try:
                        cur.execute(stmt)
                    except Exception:
                        pass
            cur.execute("SET foreign_key_checks = 1")
        conn.commit()
        cur.close()
        conn.close()

        existing.status = "ready"
        existing.provisioned_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
        existing.db_user = user
        existing.save()
    except Exception as exc:
        existing.status = "failed"
        existing.updated_at = datetime.now(timezone.utc)
        existing.save()
        import logging
        logging.getLogger(__name__).error("Failed to provision org DB %s: %s", org_id, exc)

    return existing


def _split_sql_statements(sql: str) -> list[str]:
    """Naive SQL splitter on ';' respecting string literals."""
    import re as _re
    # Remove single-line comments
    sql = _re.sub(r"--[^\n]*", "", sql)
    # Split on ; outside quotes (simple heuristic)
    stmts = []
    buf = []
    in_str = False
    str_char = ""
    for ch in sql:
        if not in_str and ch in ("'", '"', '`'):
            in_str = True
            str_char = ch
        elif in_str and ch == str_char:
            in_str = False
        if not in_str and ch == ";":
            stmts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        stmts.append("".join(buf))
    return stmts


_ORG_PERMISSIONS = [
    ("org.members.invite",  "Invite Members",                 "Members"),
    ("org.members.remove",  "Remove Members",                 "Members"),
    ("org.roles.assign",    "Assign Roles to Members",        "Roles"),
    ("org.roles.manage",    "Create and Delete Custom Roles", "Roles"),
    ("org.settings.edit",   "Edit Organization Settings",     "Settings"),
]


def ensure_org_permissions_seeded() -> None:
    for key, label, group in _ORG_PERMISSIONS:
        if not OrganizationPermission.objects.filter(key=key).first():
            p = OrganizationPermission(
                id=str(uuid.uuid4()), key=key, label=label, group=group,
            )
            p.save()


def get_all_org_permissions() -> list[OrganizationPermission]:
    return OrganizationPermission.objects.filter().all()


def get_permissions_for_org_role(role_id: str) -> list[str]:
    rps = OrganizationRolePermission.objects.filter(role_id=role_id).all()
    keys: list[str] = []
    for rp in rps:
        try:
            p = OrganizationPermission.objects.get(id=rp.permission_id)
            keys.append(p.key)
        except Exception:
            pass
    return keys


def set_org_role_permission(role_id: str, permission_key: str, enabled: bool) -> bool:
    perm = OrganizationPermission.objects.filter(key=permission_key).first()
    if not perm:
        return False
    existing = OrganizationRolePermission.objects.filter(
        role_id=role_id, permission_id=perm.id
    ).first()
    if enabled and not existing:
        rp = OrganizationRolePermission(
            id=str(uuid.uuid4()), role_id=role_id, permission_id=perm.id,
        )
        rp.save()
    elif not enabled and existing:
        existing.delete()
    return True


def delete_organization(org_id: str) -> None:
    """Cascade-delete all org data then the org itself."""
    members = OrganizationMember.objects.filter(org_id=org_id).all()
    for m in members:
        for r in OrganizationMemberRole.objects.filter(member_id=m.id).all():
            r.delete()
        m.delete()
    for inv in OrganizationInvitation.objects.filter(org_id=org_id).all():
        inv.delete()
    for role in OrganizationRole.objects.filter(org_id=org_id).all():
        for p in OrganizationRolePermission.objects.filter(role_id=role.id).all():
            p.delete()
        role.delete()
    org = get_organization(org_id)
    if org:
        org.delete()
