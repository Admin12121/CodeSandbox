from __future__ import annotations

import json
from decimal import Decimal


GOD_TEAR_SLUG = "god-tear-static-reverse"
GOD_TEAR_LEGACY_SLUG = "reverse-decompile"
GOD_TEAR_IMAGE = "docker.io/admin12121/decompile:stable"
GOD_TEAR_REPOSITORY = "https://github.com/Admin12121/decompile"


def _launch_script() -> str:
    return """set -eu
export HOME=/tmp/decompile-home
export DECOMPILE_IN_DOCKER=1
export DECOMPILE_NO_AI=1
export DECOMPILE_NO_OPEN=1
umask 007
mkdir -p "$HOME/.config" "$HOME/.cache"

printf '[god-tear] starting isolated static analysis\\n'
rc=0
decompile --no-ai --no-open /input/sample /output || rc=$?
if [ "$rc" -ne 0 ]; then
  printf '[god-tear] analysis failed with exit code %s\\n' "$rc" >&2
  exit "$rc"
fi

printf '[god-tear] CODESANDBOX_ANALYSIS_COMPLETE\\n'

# Test Launch must exit so the worker can evaluate exit_zero. Normal user
# runs stay alive so the same isolated container and workspace can switch
# from Background Run to Lab UI without losing the generated output.
if [ "${CODESANDBOX_TEST_RUN:-0}" = "1" ]; then
  exit 0
fi

exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
"""


def _runtime_files() -> str:
    """Return the Config-IDE files stored in SandboxTemplate.runtime_config."""
    runtime = {
        # The upstream image has ENTRYPOINT ["decompile"]. Override it so the
        # analysis and the post-analysis Lab UI share one container lifecycle.
        "entrypoint": ["/bin/sh", "-lc"],
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "primary_input_alias": "sample",
        "required_args": ["--no-ai", "--no-open", "/input/sample", "/output"],
        "forbidden_args": ["--ai", "--update", "--image", "--docker-image", "--local"],
        "allowed_file_types": [
            "bin", "elf", "exe", "dll", "sys", "so", "dylib", "macho",
            "apk", "aab", "dex", "jar", "war", "ear", "class", "ipa",
            "app", "zip",
        ],
        "max_input_size_mb": 100,
        "test_config": {
            "success_condition": "exit_zero",
        },
        "ui": {
            "background_run": {
                "completion_log": "CODESANDBOX_ANALYSIS_COMPLETE",
            },
            "lab_ui": {
                "filesystem_root": "/workspace",
                "start_path": "/",
            },
        },
        "environment": {
            "HOME": "/tmp/decompile-home",
            "DECOMPILE_IN_DOCKER": "1",
            "DECOMPILE_NO_AI": "1",
            "DECOMPILE_NO_OPEN": "1",
        },
        "source_repository": GOD_TEAR_REPOSITORY,
    }

    workflow = {
        "mode": "workflow",
        "start_node_id": "god-tear-background",
        "allow_cycles": False,
        "nodes": [
            {
                "id": "god-tear-background",
                "label": "Static Analysis",
                "ui_mode": "background_run",
                "position": {"x": 120, "y": 180},
                "carry_artifacts": True,
                "auto_start": True,
                "continue_label": "Open Full UI after analysis completes",
            },
            {
                "id": "god-tear-full-ui",
                "label": "Reverse Engineering Workspace",
                "ui_mode": "lab_ui",
                "position": {"x": 540, "y": 180},
                "carry_artifacts": True,
                "auto_start": False,
                "continue_label": "",
            },
        ],
        "edges": [
            {
                "id": "god-tear-background-to-full-ui",
                "source": "god-tear-background",
                "target": "god-tear-full-ui",
                # The analysis container deliberately remains running after
                # the completion marker, so this transition is user-driven.
                "condition": "manual",
                "label": "Open Full UI",
            }
        ],
    }

    return json.dumps(
        {
            "runtime.json": json.dumps(runtime, indent=2),
            "workflow.json": json.dumps(workflow, indent=2),
            "README.md": (
                "# God Tear — Static Reverse Lab\n\n"
                "Upload one supported binary or application package. The first "
                "workflow stage runs static decompilation with networking disabled. "
                "After `[god-tear] CODESANDBOX_ANALYSIS_COMPLETE` appears, open "
                "the Full UI stage to inspect `/workspace`, which shares the same "
                "isolated volume as `/output`.\n"
            ),
        },
        separators=(",", ":"),
    )


def _default_command() -> str:
    # runtime.policy._command() accepts a JSON argv list. With /bin/sh -lc as
    # the entrypoint, the one argv item is the complete controlled launch script.
    return json.dumps([_launch_script()], separators=(",", ":"))


def _template_values(admin_user_id: str) -> dict:
    workflow_files = json.loads(_runtime_files())
    return {
        "name": "God Tear — Static Reverse Lab",
        "slug": GOD_TEAR_SLUG,
        "description": (
            "Upload a binary or application package, run isolated static "
            "decompilation in the background, then inspect generated source, "
            "disassembly, metadata, and reports in the Lab UI."
        ),
        "icon_path": None,
        "docker_image": GOD_TEAR_IMAGE,
        "default_command": _default_command(),
        "working_dir": "/workspace",
        "input_mount_path": "/input",
        "output_mount_path": "/output",
        # The current Docker runner mounts one per-instance volume at both
        # locations. The tool writes through /output and Lab UI reads the same
        # files through /workspace.
        "artifact_paths": '["/output"]',
        "input_required": True,
        "max_upload_mb": 100,
        "sandbox_type": "reverse_engineering",
        "runtime_class": "container",
        "interface_mode": "background_run,lab_ui",
        "allowed_ui_modes": '["background_run","lab_ui"]',
        "default_ui_mode": "background_run",
        "interface_behavior": "workflow",
        "ui_workflow_json": workflow_files["workflow.json"],
        "network_mode": "disabled",
        "allow_root": False,
        "read_only_root": True,
        "run_as_user": "65532:65532",
        "pids_limit": 256,
        "allow_full_internet": False,
        "max_timeout_hr": 2,
        "runtime_config": _runtime_files(),
        "created_by_id": admin_user_id,
        "status": "maintenance",
    }


def seed_god_tear_static_reverse(admin_user_id: str) -> None:
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

    template = sandbox_repo.get_template_by_slug(GOD_TEAR_SLUG)
    if template is None:
        values = _template_values(admin_user_id)
        legacy = sandbox_repo.get_template_by_slug(GOD_TEAR_LEGACY_SLUG)
        if legacy is not None and str(legacy.docker_image) == GOD_TEAR_IMAGE:
            # One-time migration of the original bundled reverse-decompile seed.
            # Reuse its ID so existing template-plan mappings and references are
            # preserved instead of creating a duplicate template.
            update_values = dict(values)
            update_values.pop("created_by_id", None)
            update_values["last_test_status"] = "untested"
            update_values["last_tested_at"] = None
            update_values["last_test_error"] = None
            template = sandbox_repo.update_template(str(legacy.id), **update_values)
        else:
            template = sandbox_repo.create_template(**values)

    if template is None:
        raise RuntimeError("God Tear sandbox template could not be created.")

    # Seed the relation only once. Rerunning seed must not re-enable or
    # overwrite a plan mapping that an administrator changed later.
    template_plan = sandbox_repo.get_template_plan(str(template.id), str(plan.id))
    if template_plan is None:
        sandbox_repo.upsert_template_plan(
            str(template.id),
            str(plan.id),
            is_enabled=True,
            sort_order=0,
        )


def seed_sandbox_templates(admin_user_id: str) -> None:
    print("Seeding God Tear static reverse sandbox...")
    seed_god_tear_static_reverse(admin_user_id)
    print("  done")
