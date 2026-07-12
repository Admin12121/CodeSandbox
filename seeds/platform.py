from __future__ import annotations

from werkzeug.security import generate_password_hash


ADMIN_EMAIL = "admin@codesandbox.dev"
ADMIN_PASSWORD = "admin@12"


def seed_platform_admin() -> str:
    from codesandbox.features.identity.repository import (
        create_user,
        find_user_by_email,
        update_user,
    )
    from codesandbox.features.platform_admin.repository import (
        assign_role_to_user,
        get_role_by_name,
        seed_default_permissions,
        seed_default_roles,
    )

    print("Seeding default permissions…")
    seed_default_permissions()
    print("  done")

    print("Seeding default roles…")
    seed_default_roles()
    print("  done")

    user = find_user_by_email(ADMIN_EMAIL)
    if user:
        print(f"Admin user already exists: {ADMIN_EMAIL}")
    else:
        print(f"Creating admin user: {ADMIN_EMAIL}")
        user = create_user(
            email=ADMIN_EMAIL,
            name="Platform Admin",
            password_hash=generate_password_hash(ADMIN_PASSWORD),
        )
        update_user(user.id, platform_role="system_admin", email_verified=True)
        print(f"  Created user {user.id}")

    admin_role = get_role_by_name("system_admin")
    if admin_role:
        assign_role_to_user(user.id, admin_role.id)
        print("  Assigned system_admin role")

    return str(user.id)


def print_seed_credentials() -> None:
    print()
    print("Seed complete.")
    print(f"  Login: {ADMIN_EMAIL}")
    print(f"  Password: {ADMIN_PASSWORD}")
    print("  (change this password immediately!)")

