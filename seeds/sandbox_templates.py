from __future__ import annotations

import json
from decimal import Decimal


GOD_TEAR_SLUG = "god-tear-static-reverse"
GOD_TEAR_LEGACY_SLUG = "reverse-decompile"
GOD_TEAR_IMAGE = "docker.io/admin12121/decompile:stable"
GOD_TEAR_REPOSITORY = "https://github.com/Admin12121/decompile"
GOD_TEAR_SEED_VERSION = 2


def _files(runtime: dict, workflow: dict | None = None, readme: str = "") -> str:
    values = {"runtime.json": json.dumps(runtime, indent=2)}
    if workflow is not None:
        values["workflow.json"] = json.dumps(workflow, indent=2)
    if readme:
        values["README.md"] = readme
    return json.dumps(values, separators=(",", ":"))


def _argv_script(script: str) -> str:
    return json.dumps([script], separators=(",", ":"))


def _runtime_from_files(raw: str | None) -> dict:
    """Read runtime.json from a template's file-bundle without importing app code."""
    try:
        files = json.loads(raw or "{}")
        runtime = json.loads(files.get("runtime.json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return runtime if isinstance(runtime, dict) else {}


def _keepalive_script(prefix: str) -> str:
    return f"""set -eu
printf '[{prefix}] sandbox ready\\n'
exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
"""


def _single_template(
    *,
    name: str,
    slug: str,
    description: str,
    image: str,
    ui_mode: str,
    network_mode: str,
    allow_root: bool,
    read_only_root: bool,
    run_as_user: str | None,
    runtime: dict,
    command: str,
    admin_user_id: str,
    sandbox_type: str = "interactive",
    max_timeout_hr: int = 4,
) -> dict:
    runtime = dict(runtime)
    runtime.setdefault("entrypoint", ["/bin/sh", "-lc"])
    return {
        "name": name,
        "slug": slug,
        "description": description,
        "icon_path": None,
        "docker_image": image,
        "default_command": _argv_script(command),
        "working_dir": "/workspace",
        "input_mount_path": "",
        "output_mount_path": "",
        "artifact_paths": "[]",
        "input_required": False,
        "max_upload_mb": 50,
        "sandbox_type": sandbox_type,
        "runtime_class": "container",
        "interface_mode": ui_mode,
        "allowed_ui_modes": json.dumps([ui_mode], separators=(",", ":")),
        "default_ui_mode": ui_mode,
        "interface_behavior": "single",
        "ui_workflow_json": None,
        "network_mode": network_mode,
        "allow_root": allow_root,
        "read_only_root": read_only_root,
        "run_as_user": run_as_user,
        "pids_limit": 512,
        "allow_full_internet": network_mode == "full_internet",
        "max_timeout_hr": max_timeout_hr,
        "runtime_config": _files(runtime),
        "created_by_id": admin_user_id,
        "status": "maintenance",
    }


def _ubuntu_study(admin_user_id: str) -> dict:
    runtime = {
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "test_config": {"requirements": ["runtime_started", "terminal_ready"]},
    }
    return _single_template(
        name="Ubuntu Study Terminal",
        slug="ubuntu-study-terminal",
        description="Ubuntu terminal study environment with full Internet and a root shell.",
        image="docker.io/library/ubuntu:24.04",
        ui_mode="terminal_only",
        network_mode="full_internet",
        allow_root=True,
        read_only_root=False,
        run_as_user=None,
        runtime=runtime,
        command=_keepalive_script("ubuntu-study"),
        admin_user_id=admin_user_id,
    )


def _kali_study(admin_user_id: str) -> dict:
    runtime = {
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "test_config": {"requirements": ["runtime_started", "terminal_ready"]},
    }
    return _single_template(
        name="Kali Linux Study Terminal",
        slug="kali-study-terminal",
        description="Kali Linux terminal study environment with full Internet and a root shell.",
        image="docker.io/kalilinux/kali-rolling:latest",
        ui_mode="terminal_only",
        network_mode="full_internet",
        allow_root=True,
        read_only_root=False,
        run_as_user=None,
        runtime=runtime,
        command=_keepalive_script("kali-study"),
        admin_user_id=admin_user_id,
    )


def _ide_bootstrap_script() -> str:
    # The image contains a non-root `vscode` account and sudo. The container's
    # bootstrap process renames the UID-1000 identity to the authenticated
    # platform username, then all interactive terminals run as numeric UID/GID
    # 1000. Password hashes are deliberately never copied into a sandbox;
    # sudo is passwordless and therefore equivalent to root privilege.
    return """set -eu
name="${CODESANDBOX_USERNAME:-student}"
case "$name" in
  *[!a-z0-9_-]*|'') name=student ;;
esac
old_user="$(getent passwd 1000 | cut -d: -f1 || true)"
old_group="$(getent group 1000 | cut -d: -f1 || true)"
if [ -n "$old_user" ] && [ "$old_user" != "$name" ]; then
  sed -i "s/^${old_user}:/${name}:/" /etc/passwd
fi
if [ -n "$old_group" ] && [ "$old_group" != "$name" ]; then
  sed -i "s/^${old_group}:/${name}:/" /etc/group
fi
printf '%s ALL=(ALL) NOPASSWD:ALL\\n' "$name" > /etc/sudoers.d/90-codesandbox-user
chmod 0440 /etc/sudoers.d/90-codesandbox-user
printf '[ubuntu-ide] ready as %s (uid 1000)\\n' "$name"
exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
"""


def _ubuntu_ide(admin_user_id: str) -> dict:
    runtime = {
        "entrypoint": ["/bin/sh", "-lc"],
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "container_start_user": "0:0",
        "terminal_user": "1000:1000",
        "allow_sudo": True,
        "test_config": {
            "requirements": ["runtime_started", "terminal_ready", "filesystem_ready"]
        },
        "ui": {"lab_ui": {"filesystem_root": "/workspace", "start_path": "/"}},
    }
    return _single_template(
        name="Ubuntu Coding IDE",
        slug="ubuntu-coding-ide",
        description=(
            "Ubuntu Lab UI with full Internet. Interactive sessions use the authenticated "
            "platform username as UID 1000 and have passwordless sudo; direct root login is not exposed."
        ),
        image="mcr.microsoft.com/devcontainers/base:noble",
        ui_mode="lab_ui",
        network_mode="full_internet",
        allow_root=False,
        read_only_root=False,
        run_as_user="1000:1000",
        runtime=runtime,
        command=_ide_bootstrap_script(),
        admin_user_id=admin_user_id,
        max_timeout_hr=8,
    )


def _reverse_script() -> str:
    return """set -eu
export HOME=/tmp/decompile-home
export DECOMPILE_IN_DOCKER=1
export DECOMPILE_NO_AI=1
export DECOMPILE_NO_OPEN=1
umask 007
mkdir -p "$HOME/.config" "$HOME/.cache"
original="${CODESANDBOX_INPUT_NAME:-binary}"
base="${original##*/}"
safe="$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '_')"
[ -n "$safe" ] || safe=binary
out="/output/$safe"
mkdir -p "$out"
printf '[god-tear] analysing %s into %s\\n' "$original" "$out"
decompile --no-ai --no-open /input/sample "$out"
printf '[god-tear] CODESANDBOX_ANALYSIS_COMPLETE\\n'
exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
"""


def _reverse_values(admin_user_id: str) -> dict:
    workflow = {
        "mode": "workflow",
        "start_node_id": "reverse-background",
        "allow_cycles": False,
        "nodes": [
            {
                "id": "reverse-background",
                "label": "Static Analysis",
                "ui_mode": "background_run",
                "position": {"x": 120, "y": 180},
                "auto_start": True,
                "carry_artifacts": True,
                "completion_requirements": ["log:CODESANDBOX_ANALYSIS_COMPLETE"],
                "continue_label": "Open Full UI",
            },
            {
                "id": "reverse-full-ui",
                "label": "Reverse Engineering Workspace",
                "ui_mode": "lab_ui",
                "position": {"x": 540, "y": 180},
                "auto_start": False,
                "carry_artifacts": True,
            },
        ],
        "edges": [
            {
                "id": "reverse-open-full-ui",
                "source": "reverse-background",
                "target": "reverse-full-ui",
                "condition": "manual",
                "label": "Open Full UI",
            }
        ],
    }
    runtime = {
        "seed_version": GOD_TEAR_SEED_VERSION,
        "entrypoint": ["/bin/sh", "-lc"],
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "primary_input_alias": "sample",
        "allowed_file_types": ["*"],
        "allow_extensionless_input": True,
        "max_input_size_mb": 500,
        "required_args": ["--no-ai", "--no-open", "/input/sample"],
        "forbidden_args": ["--ai", "--update", "--image", "--docker-image", "--local"],
        "test_config": {
            "requirements": [
                "runtime_started",
                "log:CODESANDBOX_ANALYSIS_COMPLETE",
                "terminal_ready",
                "filesystem_ready",
            ]
        },
        "ui": {
            "background_run": {"completion_log": "CODESANDBOX_ANALYSIS_COMPLETE"},
            "lab_ui": {"filesystem_root": "/workspace", "start_path": "/"},
        },
        "environment": {
            "HOME": "/tmp/decompile-home",
            "DECOMPILE_IN_DOCKER": "1",
            "DECOMPILE_NO_AI": "1",
            "DECOMPILE_NO_OPEN": "1",
        },
        "source_repository": GOD_TEAR_REPOSITORY,
    }
    files = _files(
        runtime,
        workflow,
        "# God Tear Static Reverse Lab\n\nUpload any non-empty file. Analysis runs without external Internet, then the same isolated volume opens in Lab UI.\n",
    )
    return {
        "name": "God Tear — Static Reverse Lab",
        "slug": GOD_TEAR_SLUG,
        "description": (
            "Upload a binary or application package, run network-isolated static analysis, "
            "then inspect the named output directory in the same Lab UI instance."
        ),
        "icon_path": None,
        "docker_image": GOD_TEAR_IMAGE,
        "default_command": _argv_script(_reverse_script()),
        "working_dir": "/workspace",
        "input_mount_path": "/input",
        "output_mount_path": "/output",
        "artifact_paths": '["/output"]',
        "input_required": True,
        "max_upload_mb": 500,
        "sandbox_type": "reverse_engineering",
        "runtime_class": "container",
        "interface_mode": "background_run,lab_ui",
        "allowed_ui_modes": '["background_run","lab_ui"]',
        "default_ui_mode": "background_run",
        "interface_behavior": "workflow",
        "ui_workflow_json": json.dumps(workflow, separators=(",", ":")),
        "network_mode": "restricted",
        "allow_root": False,
        "read_only_root": True,
        "run_as_user": "65532:65532",
        "pids_limit": 512,
        "allow_full_internet": False,
        "max_timeout_hr": 4,
        "runtime_config": files,
        "created_by_id": admin_user_id,
        "status": "maintenance",
    }


def _malware_blueprint(admin_user_id: str) -> dict:
    # This is intentionally a VM-worker blueprint, not a Docker malware runner.
    # Running untrusted malware as root with direct Internet inside the shared
    # DinD container worker would expose the host and surrounding networks.
    report_html = """<!doctype html><meta charset='utf-8'><style>body{font:14px system-ui;padding:24px;color:#111}code{background:#eee;padding:2px 5px}</style><h1>Malware Analysis Report</h1><p>The VM worker should populate the report API with processes, filesystem changes, network IOCs, persistence, privilege escalation attempts and lateral-movement evidence.</p><p>This template remains in maintenance until a disposable QEMU worker and controlled egress collector are configured.</p>"""
    workflow = {
        "mode": "workflow",
        "start_node_id": "malware-run",
        "allow_cycles": False,
        "nodes": [
            {
                "id": "malware-run",
                "label": "Dynamic Analysis",
                "ui_mode": "background_run",
                "position": {"x": 80, "y": 180},
                "completion_requirements": ["log:MALWARE_REPORT_COMPLETE"],
                "auto_start": True,
            },
            {
                "id": "malware-report",
                "label": "Behavior Report",
                "ui_mode": "custom_page",
                "position": {"x": 440, "y": 180},
                "custom_html": report_html,
                "completion_requirements": ["custom_page_ready"],
                "auto_start": False,
            },
            {
                "id": "malware-reverse",
                "label": "Reverse Workspace",
                "ui_mode": "lab_ui",
                "position": {"x": 800, "y": 180},
                "auto_start": False,
            },
        ],
        "edges": [
            {"id": "malware-to-report", "source": "malware-run", "target": "malware-report", "condition": "manual", "label": "Open report"},
            {"id": "malware-to-reverse", "source": "malware-report", "target": "malware-reverse", "condition": "manual", "label": "Open reverse workspace"},
        ],
    }
    runtime = {
        "image_pull_policy": "never",
        "workspace_enabled": True,
        "allowed_file_types": ["*"],
        "allow_extensionless_input": True,
        "max_input_size_mb": 500,
        "driver": {
            "required_worker": "qemu_vm",
            "network_policy": "controlled-egress-capture",
            "snapshot_restore": True,
            "report_schema": "malware-behavior-v1",
        },
        "test_config": {
            "requirements": [
                "runtime_started",
                "log:MALWARE_REPORT_COMPLETE",
                "custom_page_ready",
                "terminal_ready",
                "filesystem_ready",
            ]
        },
    }
    return {
        "name": "Malware Analysis Lab — VM Worker Required",
        "slug": "malware-analysis-vm",
        "description": (
            "Safe dynamic-analysis blueprint for a disposable QEMU VM with captured/controlled egress, "
            "behavior report, and reverse workspace. It is intentionally not runnable on the Docker worker."
        ),
        "icon_path": None,
        "docker_image": "local://codesandbox-malware-analysis-vm",
        "default_command": None,
        "working_dir": "/workspace",
        "input_mount_path": "/input",
        "output_mount_path": "/output",
        "artifact_paths": '["/output"]',
        "input_required": True,
        "max_upload_mb": 500,
        "sandbox_type": "malware",
        "runtime_class": "fullvm",
        "interface_mode": "background_run,custom_page,lab_ui",
        "allowed_ui_modes": '["background_run","custom_page","lab_ui"]',
        "default_ui_mode": "background_run",
        "interface_behavior": "workflow",
        "ui_workflow_json": json.dumps(workflow, separators=(",", ":")),
        "network_mode": "restricted",
        "allow_root": True,
        "read_only_root": False,
        "run_as_user": None,
        "pids_limit": 1024,
        "allow_full_internet": False,
        "max_timeout_hr": 2,
        "runtime_config": _files(runtime, workflow),
        "created_by_id": admin_user_id,
        "status": "maintenance",
    }


def _ensure_plans(admin_user_id: str):
    from codesandbox.features.sandbox import repository as repo

    general = repo.get_plan("general")
    if general is None:
        general = repo.create_plan(
            plan_id="general",
            name="General Isolated",
            sort_order=0,
            ind_vcpu=2,
            ind_ram_gb=4,
            ind_disk_gb=20,
            ind_cost_hr=Decimal("0.0000"),
            org_vcpu=4,
            org_ram_gb=8,
            org_disk_gb=40,
            org_cost_hr=Decimal("0.0000"),
            min_billable_minutes=0,
            allowed_network_modes='["disabled","restricted"]',
            updated_by_id=admin_user_id,
        )
    internet = repo.get_plan("local-internet")
    if internet is None:
        internet = repo.create_plan(
            plan_id="local-internet",
            name="Local Full Internet",
            sort_order=1,
            ind_vcpu=2,
            ind_ram_gb=4,
            ind_disk_gb=30,
            ind_cost_hr=Decimal("0.0000"),
            org_vcpu=4,
            org_ram_gb=8,
            org_disk_gb=60,
            org_cost_hr=Decimal("0.0000"),
            min_billable_minutes=0,
            allowed_network_modes='["disabled","restricted","full_internet"]',
            updated_by_id=admin_user_id,
        )
    return general, internet


def _create_if_missing(values: dict, plan_states: dict[str, bool]):
    from codesandbox.features.sandbox import repository as repo

    template = repo.get_template_by_slug(values["slug"])
    if template is None:
        template = repo.create_template(**values)
    for order, (plan_id, enabled) in enumerate(plan_states.items()):
        if repo.get_template_plan(str(template.id), plan_id) is None:
            repo.upsert_template_plan(
                str(template.id), plan_id, is_enabled=enabled, sort_order=order
            )
    return template


def seed_sandbox_templates(admin_user_id: str) -> None:
    from codesandbox.features.sandbox import repository as repo

    general, internet = _ensure_plans(admin_user_id)
    print("Seeding sandbox templates…")
    internet_only = {str(general.id): False, str(internet.id): True}
    isolated_only = {str(general.id): True, str(internet.id): False}
    _create_if_missing(_ubuntu_study(admin_user_id), internet_only)
    _create_if_missing(_kali_study(admin_user_id), internet_only)
    _create_if_missing(_ubuntu_ide(admin_user_id), internet_only)

    reverse_values = _reverse_values(admin_user_id)
    reverse = repo.get_template_by_slug(GOD_TEAR_SLUG)
    if reverse is None:
        reverse = repo.get_template_by_slug(GOD_TEAR_LEGACY_SLUG)
    reverse_needs_upgrade = bool(
        reverse is not None
        and str(reverse.docker_image or "") == GOD_TEAR_IMAGE
        and (
            str(reverse.slug) == GOD_TEAR_LEGACY_SLUG
            or _runtime_from_files(reverse.runtime_config).get("seed_version")
            != GOD_TEAR_SEED_VERSION
        )
    )
    if reverse_needs_upgrade:
        update = dict(reverse_values)
        update.pop("created_by_id", None)
        update.update(last_test_status="untested", last_tested_at=None, last_test_error=None, status="maintenance")
        reverse = repo.update_template(str(reverse.id), **update)
    elif reverse is None:
        reverse = repo.create_template(**reverse_values)
    if reverse is None:
        raise RuntimeError("Reverse sandbox template could not be created.")
    if repo.get_template_plan(str(reverse.id), str(general.id)) is None:
        repo.upsert_template_plan(str(reverse.id), str(general.id), is_enabled=True, sort_order=0)
    if repo.get_template_plan(str(reverse.id), str(internet.id)) is None:
        repo.upsert_template_plan(str(reverse.id), str(internet.id), is_enabled=False, sort_order=1)

    _create_if_missing(_malware_blueprint(admin_user_id), isolated_only)
    print("  seeded Ubuntu, Kali, IDE, reverse workflow, and safe malware VM blueprint")
