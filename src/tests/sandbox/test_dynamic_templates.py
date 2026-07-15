from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

from tests._context import TestCase, TestContext

_SYNTHETIC_TEMPLATES = [
    dict(
        slug="python-ctf",
        name="Python CTF Lab",
        sandbox_type="ctf",
        runtime_class="container",
        docker_image="python:3.12-slim",
        interface_mode="terminal",
        network_mode="disabled",
        default_command="/bin/bash",
    ),
    dict(
        slug="node-lab",
        name="Node.js Lab",
        sandbox_type="coding",
        runtime_class="container",
        docker_image="node:20-slim",
        interface_mode="terminal,editor",
        network_mode="restricted",
        default_command=None,
    ),
    dict(
        slug="static-malware-scan",
        name="Static Malware Analysis",
        sandbox_type="malware",
        runtime_class="tool_job",
        docker_image="ubuntu:22.04",
        interface_mode="background",
        network_mode="disabled",
        default_command='["clamscan", "/input"]',
    ),
]


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        id="basic",
        name="Basic",
        ind_vcpu=1, ind_ram_gb=1, ind_disk_gb=10, ind_cost_hr=Decimal("0.1000"),
        org_vcpu=2, org_ram_gb=2, org_disk_gb=20, org_cost_hr=Decimal("0.1500"),
        min_billable_minutes=1,
        allowed_network_modes='["disabled","restricted"]',
        is_active=True,
        sort_order=0,
    )


def test_new_template_types_launch_with_zero_code_changes(ctx: TestContext) -> None:
    """Proves the platform supports adding an arbitrary new sandbox template
    (a CTF lab, a Node lab, a malware scanner — none of which existed when
    this test was written) purely through template configuration: build a
    real runtime policy for each and confirm it's launch-shaped, with no
    per-template branching anywhere in PolicyBuilder."""
    from codesandbox.features.sandbox.runtime.policy import PolicyBuilder, resolve_effective_plan

    plan = _plan()
    for spec in _SYNTHETIC_TEMPLATES:
        template = SimpleNamespace(
            id=f"tpl-{spec['slug']}",
            slug=spec["slug"],
            name=spec["name"],
            sandbox_type=spec["sandbox_type"],
            runtime_class=spec["runtime_class"],
            docker_image=spec["docker_image"],
            interface_mode=spec["interface_mode"],
            network_mode=spec["network_mode"],
            allow_full_internet=False,
            default_command=spec["default_command"],
            working_dir="/workspace",
            input_mount_path="/input",
            output_mount_path="/output",
            artifact_paths='["/output"]',
            input_required=False,
            max_upload_mb=50,
            read_only_root=True,
            allow_root=False,
            run_as_user="65532:65532",
            pids_limit=256,
            max_timeout_hr=2,
            runtime_config=None,
        )
        effective = resolve_effective_plan(template, plan)
        policy = PolicyBuilder().build(template, effective)

        if "/" not in spec["docker_image"].split(":", 1)[0]:
            expected_image = f"docker.io/library/{spec['docker_image']}"
        else:
            expected_image = spec["docker_image"]
        assert policy["docker_image"] == expected_image
        assert policy["runtime_class"] == spec["runtime_class"]
        assert policy["runtime_provider"] == "docker"
        assert policy["network_mode"] in {"disabled", "restricted"}
        assert policy["security"]["cap_drop"] == ["ALL"]
        # malware/reverse_engineering templates must never resolve full internet,
        # regardless of template name — a generic (sandbox_type-based) rule,
        # not a per-template one.
        if spec["sandbox_type"] == "malware":
            assert not policy["full_internet_enabled"]


def test_allowed_file_types_reads_from_runtime_config(ctx: TestContext) -> None:
    """Input-upload restrictions (Phase 7) come from a template's own
    runtime_config, same mechanism as required/forbidden command args —
    not a hardcoded per-template extension list."""
    from codesandbox.features.sandbox.runtime.policy import parse_runtime_config

    runtime_config = json.dumps({
        "runtime.json": json.dumps({
            "allowed_file_types": ["exe", "elf", "APK"],
            "max_input_size_mb": 250,
        })
    })
    parsed = parse_runtime_config(runtime_config)
    assert parsed["allowed_file_types"] == ["exe", "elf", "APK"]
    assert parsed["max_input_size_mb"] == 250

    # Absent/empty config must fail open (no extra restriction), not crash.
    assert parse_runtime_config(None) == {}
    assert parse_runtime_config("not json") == {}


TESTS: list[TestCase] = [
    TestCase("new template types launch with zero code changes", "sandbox", test_new_template_types_launch_with_zero_code_changes),
    TestCase("allowed_file_types reads from runtime_config", "sandbox", test_allowed_file_types_reads_from_runtime_config),
]
