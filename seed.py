#!/usr/bin/env python
"""One-time seed: default platform permissions, roles, and first system_admin user."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codesandbox.config import get_settings
from codesandbox.infrastructure.nexorm import configure_db

settings = get_settings()
configure_db(settings.database_url)

import codesandbox.models  # noqa: F401 — registers all models

from codesandbox.features.platform_admin.repository import (
    seed_default_permissions,
    seed_default_roles,
    get_role_by_name,
    assign_role_to_user,
)
from codesandbox.features.identity.repository import find_user_by_email, create_user, update_user
from werkzeug.security import generate_password_hash


def seed():
    print("Seeding default permissions…")
    seed_default_permissions()
    print("  done")

    print("Seeding default roles…")
    seed_default_roles()
    print("  done")

    admin_email = "admin@codesandbox.local"
    admin_password = "changeme123"

    user = find_user_by_email(admin_email)
    if user:
        print(f"Admin user already exists: {admin_email}")
    else:
        print(f"Creating admin user: {admin_email}")
        user = create_user(
            email=admin_email,
            name="Platform Admin",
            password_hash=generate_password_hash(admin_password),
        )
        update_user(user.id, platform_role="system_admin", email_verified=True)
        print(f"  Created user {user.id}")

    admin_role = get_role_by_name("system_admin")
    if admin_role:
        assign_role_to_user(user.id, admin_role.id)
        print(f"  Assigned system_admin role")

    print()
    print("Seed complete.")
    print(f"  Login: {admin_email}")
    print(f"  Password: {admin_password}")
    print("  (change this password immediately!)")


if __name__ == "__main__":
    seed()
