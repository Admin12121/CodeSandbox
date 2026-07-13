from __future__ import annotations

import json
from decimal import Decimal


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
        # This is an example seed template only, created through the same
        # dynamic runtime_config mechanism any admin-authored template uses.
        runtime_config = json.dumps({
            "runtime.json": json.dumps({
                "required_args": ["--no-ai"],
                "primary_input_alias": "sample",
                "allowed_file_types": ["exe", "elf", "apk", "jar", "dex", "dll", "so", "ipa"],
                "max_input_size_mb": 500,
            }),
        })
        template = sandbox_repo.create_template(
            name="Reverse Engineering - Decompile",
            slug="reverse-decompile",
            description="Static reverse engineering and decompilation workspace.",
            icon_path=None,
            docker_image="docker.io/admin12121/decompile:stable",
            default_command="--no-ai --no-open /input/sample /output",
            working_dir="/workspace",
            input_mount_path="/input",
            output_mount_path="/output",
            artifact_paths='["/output"]',
            input_required=True,
            max_upload_mb=500,
            sandbox_type="reverse_engineering",
            runtime_class="tool_job",
            interface_mode="terminal,background",
            allowed_ui_modes='["terminal_only","background_run"]',
            default_ui_mode="background_run",
            network_mode="disabled",
            allow_root=False,
            read_only_root=True,
            run_as_user="65532:65532",
            pids_limit=256,
            allow_full_internet=False,
            max_timeout_hr=2,
            runtime_config=runtime_config,
            created_by_id=admin_user_id,
            # Admin must run a real Test Launch before this becomes selectable.
            status="maintenance",
        )
    sandbox_repo.upsert_template_plan(
        str(template.id), str(plan.id), is_enabled=True
    )


def seed_sandbox_templates(admin_user_id: str) -> None:
    print("Seeding reverse-decompile sandbox...")
    seed_reverse_decompile(admin_user_id)
    print("  done")

