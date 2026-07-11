#!/usr/bin/env python
"""One-time seed: default platform permissions, roles, and first system_admin user."""
from __future__ import annotations

import sys
from pathlib import Path
from decimal import Decimal

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


def seed_reverse_decompile(admin_user_id: str) -> None:
    from codesandbox.features.sandbox import repository as sandbox_repo

    plan = sandbox_repo.get_plan("general")
    if plan is None:
        plan = sandbox_repo.create_plan(
            plan_id="general",
            name="General",
            sort_order=0,
            ind_vcpu=1,
            ind_ram_gb=2,
            ind_disk_gb=10,
            ind_cost_hr=Decimal("0.2000"),
            org_vcpu=2,
            org_ram_gb=4,
            org_disk_gb=20,
            org_cost_hr=Decimal("0.4000"),
            min_billable_minutes=1,
            allowed_network_modes='["disabled","restricted"]',
            updated_by_id=admin_user_id,
        )

    template = sandbox_repo.get_template_by_slug("reverse-decompile")
    if template is None:
        template = sandbox_repo.create_template(
            name="Reverse Engineering - Decompile",
            slug="reverse-decompile",
            description="Static reverse engineering and decompilation workspace.",
            icon_path=None,
            docker_image="docker.io/admin12121/decompile:stable",
            default_command="decompile --no-ai /input/sample /output",
            working_dir="/workspace",
            input_mount_path="/input",
            output_mount_path="/output",
            artifact_paths='["/output"]',
            input_required=True,
            max_upload_mb=100,
            sandbox_type="reverse_engineering",
            runtime_class="tool_job",
            interface_mode="terminal,background",
            network_mode="disabled",
            allow_root=False,
            read_only_root=True,
            run_as_user="65532:65532",
            pids_limit=256,
            allow_full_internet=False,
            max_timeout_hr=2,
            type_config=None,
            created_by_id=admin_user_id,
            status="active",
        )
        sandbox_repo.update_template(
            str(template.id), last_test_status="passed"
        )
    sandbox_repo.upsert_template_plan(
        str(template.id), str(plan.id), is_enabled=True
    )


def seed():
    print("Seeding default permissions…")
    seed_default_permissions()
    print("  done")

    print("Seeding default roles…")
    seed_default_roles()
    print("  done")

    admin_email = "admin@codesandbox.dev"
    admin_password = "admin@12"

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

    print("Seeding reverse-decompile sandbox...")
    seed_reverse_decompile(str(user.id))
    print("  done")

    print()
    print("Seed complete.")
    print(f"  Login: {admin_email}")
    print(f"  Password: {admin_password}")
    print("  (change this password immediately!)")


if __name__ == "__main__":
    seed()
