from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests._context import SkipTest, TestCase, TestContext


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_dependency_audit_inputs_are_present(ctx: TestContext) -> None:
    root = _repo_root()
    pyproject = root / "pyproject.toml"
    lockfile = root / "uv.lock"

    assert pyproject.is_file(), "pyproject.toml is required for dependency audit input"
    assert lockfile.is_file(), "uv.lock is required so audits run against a locked graph"

    project_text = pyproject.read_text(encoding="utf-8")
    assert "dependencies = [" in project_text
    assert 'readme = "README.md"' in project_text
    assert "[project.scripts]" in project_text


def test_pip_audit_reports_no_known_vulnerabilities(ctx: TestContext) -> None:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--format",
        "json",
        "--progress-spinner",
        "off",
    ]
    completed = _run_audit(command)

    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    missing_scanner = (
        "No module named pip_audit" in stderr
        or "No module named pip_audit" in stdout
    )
    if missing_scanner:
        uvx = shutil.which("uvx")
        if not uvx:
            raise SkipTest("pip-audit is not installed and uvx is unavailable")
        command = [
            uvx,
            "--from",
            "pip-audit",
            "pip-audit",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]
        completed = _run_audit(command)
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()

    network_or_service_error = any(
        marker in (stderr + stdout).lower()
        for marker in (
            "connection",
            "connect timeout",
            "read timeout",
            "temporarily unavailable",
            "failed to establish",
            "name resolution",
            "certificate verify failed",
        )
    )
    if completed.returncode not in (0, 1) and network_or_service_error:
        raise SkipTest("pip-audit advisory service is unavailable from this environment")

    assert stdout, stderr or "pip-audit produced no JSON output"
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(stderr or stdout) from exc

    dependencies = payload.get("dependencies", [])
    findings: list[str] = []
    for dependency in dependencies:
        vulns = dependency.get("vulns") or []
        if not vulns:
            continue
        name = dependency.get("name", "<unknown>")
        version = dependency.get("version", "?")
        vuln_ids = ", ".join(str(v.get("id", "?")) for v in vulns[:4])
        findings.append(f"{name}=={version}: {vuln_ids}")

    assert not findings, "known dependency vulnerabilities found: " + "; ".join(findings[:12])
    assert completed.returncode == 0, stderr or "pip-audit exited non-zero without findings"


def _run_audit(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


TESTS = [
    TestCase("dependency audit inputs are present", "dependency_audit", test_dependency_audit_inputs_are_present),
    TestCase("pip-audit reports no known vulnerabilities", "dependency_audit", test_pip_audit_reports_no_known_vulnerabilities),
]
