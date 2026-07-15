from __future__ import annotations

import json
from decimal import Decimal


GOD_TEAR_SLUG = "god-tear-static-reverse"
GOD_TEAR_LEGACY_SLUG = "reverse-decompile"
GOD_TEAR_IMAGE = "docker.io/admin12121/decompile:stable"
GOD_TEAR_REPOSITORY = "https://github.com/Admin12121/decompile"
GOD_TEAR_SEED_VERSION = 12
MANAGED_TEMPLATE_SEED_VERSION = 6


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
        "managed_seed": "ubuntu-study-terminal",
        "seed_version": MANAGED_TEMPLATE_SEED_VERSION,
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "security_profile": "root_study",
        "test_config": {"requirements": ["runtime_started", "terminal_ready"]},
    }
    return _single_template(
        name="Ubuntu Study Terminal",
        slug="ubuntu-study-terminal",
        description="Ubuntu terminal study environment with full Internet and a functional root shell.",
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
        "managed_seed": "kali-study-terminal",
        "seed_version": MANAGED_TEMPLATE_SEED_VERSION,
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "security_profile": "root_study",
        "test_config": {"requirements": ["runtime_started", "terminal_ready"]},
    }
    return _single_template(
        name="Kali Linux Study Terminal",
        slug="kali-study-terminal",
        description="Kali Linux terminal study environment with full Internet and a functional root shell.",
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
    # The image contains a non-root `vscode` account and sudo. The bootstrap
    # renames UID/GID 1000 to the authenticated platform username, prepares a
    # real writable workspace and then leaves interactive terminals on UID 1000.
    return """set -eu
name="${CODESANDBOX_USERNAME:-student}"
case "$name" in
  *[!a-z0-9_-]*|'') name=student ;;
esac
old_user="$(getent passwd 1000 | cut -d: -f1 || true)"
old_group="$(getent group 1000 | cut -d: -f1 || true)"

if [ -n "$old_group" ] && [ "$old_group" != "$name" ]; then
  if command -v groupmod >/dev/null 2>&1; then
    groupmod -n "$name" "$old_group" 2>/dev/null || sed -i "s/^${old_group}:/${name}:/" /etc/group
  else
    sed -i "s/^${old_group}:/${name}:/" /etc/group
  fi
  [ ! -f /etc/gshadow ] || sed -i "s/^${old_group}:/${name}:/" /etc/gshadow
elif [ -z "$old_group" ]; then
  if command -v groupadd >/dev/null 2>&1; then
    groupadd -g 1000 "$name"
  else
    printf '%s:x:1000:\\n' "$name" >> /etc/group
  fi
fi

if [ -n "$old_user" ] && [ "$old_user" != "$name" ]; then
  if command -v usermod >/dev/null 2>&1; then
    usermod -l "$name" -d "/home/$name" -m "$old_user" 2>/dev/null || sed -i "s/^${old_user}:/${name}:/" /etc/passwd
  else
    sed -i "s/^${old_user}:/${name}:/" /etc/passwd
  fi
  [ ! -f /etc/shadow ] || sed -i "s/^${old_user}:/${name}:/" /etc/shadow
elif [ -z "$old_user" ]; then
  if command -v useradd >/dev/null 2>&1; then
    useradd -m -u 1000 -g 1000 -s /bin/bash "$name"
  else
    mkdir -p "/home/$name"
    printf '%s:x:1000:1000::/home/%s:/bin/bash\\n' "$name" "$name" >> /etc/passwd
  fi
fi

mkdir -p "/home/$name"
chown 1000:1000 "/home/$name"
if command -v usermod >/dev/null 2>&1; then
  usermod -d "/home/$name" -s /bin/bash "$name" 2>/dev/null || true
  if getent group sudo >/dev/null 2>&1; then
    usermod -aG sudo "$name" 2>/dev/null || true
  fi
fi

# Set an actual, unlocked password so sudo authentication works when it asks.
# The intended lab password is the same as the platform username.
printf '%s:%s\\n' "$name" "$name" | chpasswd
usermod -U "$name" 2>/dev/null || true

printf '%s ALL=(ALL) ALL\n' "$name" > /etc/sudoers.d/90-codesandbox-user
chmod 0440 /etc/sudoers.d/90-codesandbox-user
if command -v visudo >/dev/null 2>&1; then
  visudo -cf /etc/sudoers.d/90-codesandbox-user >/dev/null
fi
mkdir -p /workspace
if [ ! -e /workspace/README.md ]; then
  cat > /workspace/README.md <<EOF
# Welcome to your Ubuntu Coding IDE

Hello ${name}.

Your writable project directory is /workspace.
The integrated terminal starts as ${name} (UID 1000).
Use sudo when a task needs administrator privileges.
Your sudo password is: ${name}
EOF
fi
chown -R 1000:1000 /workspace
chmod 0770 /workspace
mkdir -p /var/lib/apt/lists/partial
chown -R root:root /var/lib/apt/lists
chmod 0755 /var/lib/apt/lists /var/lib/apt/lists/partial
printf '[ubuntu-ide] ready as %s (uid 1000, sudo password matches username)\\n' "$name"
exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
"""


def _ubuntu_ide(admin_user_id: str) -> dict:
    runtime = {
        "managed_seed": "ubuntu-coding-ide",
        "seed_version": MANAGED_TEMPLATE_SEED_VERSION,
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
            "platform username as UID 1000 and can sudo with the username as password; direct root login is not exposed."
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
    # The published image has existed with more than one CLI contract. Newer
    # builds accept INPUT OUTPUT, while older cached builds accept INPUT only
    # and create <name>.ghidra-out in the current directory. Try the explicit
    # form first, then perform a compatibility retry without hiding real tool
    # errors. Network/AI/open behavior is controlled through environment vars.
    return r"""set +e
# Ghidra 12 rejects project paths containing hidden workspace path segments
# ("Path element starting with '.' is not permitted").
# Keep all Java/Ghidra temp state under non-hidden workspace paths.
export CODESANDBOX_RUNTIME_TMP=/workspace/codesandbox-runtime-tmp
export HOME="$CODESANDBOX_RUNTIME_TMP/decompile-home"
export TMPDIR="$CODESANDBOX_RUNTIME_TMP/tmp"
export XDG_CACHE_HOME="$CODESANDBOX_RUNTIME_TMP/cache"
# Size the JVM from the cgroup/plan limit instead of using one fixed heap for
# every test and customer plan. Leave enough memory for Python, native Ghidra
# allocations, JADX/ILSpy helpers, filesystem cache and the wrapper itself.
java_sizing="$(python3 - <<'PY_JAVA_LIMITS'
import os

MiB = 1024 * 1024
limit_bytes = 0
for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
    try:
        raw = open(path, encoding="utf-8").read().strip()
    except OSError:
        continue
    if raw and raw != "max":
        try:
            value = int(raw)
        except ValueError:
            continue
        # Ignore cgroup-v1's very large sentinel for "unlimited".
        if 0 < value < (1 << 60):
            limit_bytes = value
            break
if not limit_bytes:
    try:
        limit_bytes = max(1, int(os.environ.get("CODESANDBOX_RAM_LIMIT_GB", "2"))) * 1024**3
    except ValueError:
        limit_bytes = 2 * 1024**3

total_mb = max(1024, limit_bytes // MiB)
# Keep Java materially below the cgroup limit. Ghidra/JADX plus their wrappers
# can allocate native memory and filesystem cache outside the Java heap; letting
# Java take ~65% of a small dev-host test profile can still pressure the host.
reserve_mb = max(1024, min(2048, total_mb // 2))
heap_mb = max(512, min(total_mb - reserve_mb, (total_mb * 35) // 100, 1024))
metaspace_mb = max(128, min(384, total_mb // 20))
try:
    cpu_count = max(1, int(os.environ.get("CODESANDBOX_VCPU_LIMIT", "1")))
except ValueError:
    cpu_count = 1
cpu_count = 1
print(heap_mb, metaspace_mb, cpu_count)
PY_JAVA_LIMITS
)"
set -- $java_sizing
java_opts="-Xms64m -Xmx${1}m -XX:MaxMetaspaceSize=${2}m -XX:ActiveProcessorCount=${3} -XX:+ExitOnOutOfMemoryError -Djava.io.tmpdir=$TMPDIR"
export JAVA_TOOL_OPTIONS="$java_opts"
export JDK_JAVA_OPTIONS="$java_opts"
export _JAVA_OPTIONS="$java_opts"
printf '[god-tear] memory plan: %s GiB; JVM heap: %s MiB; CPUs: %s\n' "${CODESANDBOX_RAM_LIMIT_GB:-unknown}" "$1" "$3"
export MALLOC_ARENA_MAX=2
export GHIDRA_TIMEOUT=30
export DECOMPILE_NO_UNPACK=1
ulimit -c 0 2>/dev/null || true
ulimit -n 1024 2>/dev/null || true
ulimit -u 128 2>/dev/null || true
# Cap each child process below the container limit. This prevents Java-based
# helpers from sizing themselves from host-visible memory if they ignore the
# exported Java options.
ulimit -v "$(( (${CODESANDBOX_RAM_LIMIT_GB:-2} * 1024 * 1024 * 80) / 100 ))" 2>/dev/null || true
export DECOMPILE_IN_DOCKER=1
export DECOMPILE_NO_AI=1
export DECOMPILE_NO_OPEN=1
export DECOMPILE_VERBOSE=1
export DECOMPILE_ASCII=1
umask 007

mkdir -p "$HOME/.config" "$HOME/.cache" "$XDG_CACHE_HOME" "$TMPDIR" /workspace
input_file=""
for candidate in /input/* /input/.[!.]* /input/..?*; do
  if [ -f "$candidate" ]; then
    input_file="$candidate"
    break
  fi
done

if [ -z "$input_file" ]; then
  printf '[god-tear] ANALYSIS_FAILED: no uploaded input file found\n' >&2
  printf '[god-tear] CODESANDBOX_ANALYSIS_FINISHED result=failed exit=2\n'
  exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
fi

# Reject oversized inputs and archive bombs before invoking Ghidra/JADX.
if ! python3 - "$input_file" <<'PY_PREFLIGHT'
import os, sys, zipfile
path = sys.argv[1]
max_input = 128 * 1024 * 1024
max_unpacked = 512 * 1024 * 1024
max_entries = 20000
max_ratio = 100
size = os.path.getsize(path)
if size <= 0:
    raise SystemExit('input is empty')
if size > max_input:
    raise SystemExit(f'input exceeds safe analysis limit ({size} > {max_input} bytes)')
if zipfile.is_zipfile(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise SystemExit(f'archive has too many entries ({len(infos)} > {max_entries})')
        unpacked = sum(max(0, int(item.file_size)) for item in infos)
        packed = sum(max(0, int(item.compress_size)) for item in infos)
        if unpacked > max_unpacked:
            raise SystemExit(f'archive expands beyond safe limit ({unpacked} > {max_unpacked} bytes)')
        ratio = unpacked / max(1, packed)
        if ratio > max_ratio:
            raise SystemExit(f'archive compression ratio is unsafe ({ratio:.1f} > {max_ratio})')
PY_PREFLIGHT
then
  printf '[god-tear] ANALYSIS_FAILED: unsafe or oversized input\n' >&2
  printf '[god-tear] CODESANDBOX_ANALYSIS_FINISHED result=failed exit=2\n'
  exec /bin/sh -c 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done'
fi

run_decompile() {
  # Do not terminate a valid analysis at an arbitrary memory percentage or a
  # hardcoded 15-minute wrapper timeout. The selected plan/test profile's
  # Docker cgroup and the platform instance timeout remain the boundaries.
  decompile --no-ai "$@"
}

original="${CODESANDBOX_INPUT_NAME:-${input_file##*/}}"
base="${original##*/}"
safe="$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '_')"
safe="${safe#.}"
[ -n "$safe" ] || safe=binary
out="/workspace/$safe"
work="$CODESANDBOX_RUNTIME_TMP/god-tear-work-$safe"
tmp_log="$CODESANDBOX_RUNTIME_TMP/god-tear-$safe.log"
status_file="$out/ANALYSIS_STATUS.json"
rm -rf "$out" "$work" "$tmp_log"
mkdir -p "$out" "$work"

printf '[god-tear] analysing %s from %s into %s\n' "$original" "$input_file" "$out"
if ! command -v decompile >/dev/null 2>&1; then
  rc=127
  printf 'The decompile command is missing from the configured image.\n' >"$tmp_log"
else
  run_decompile "$input_file" "$out" >"$tmp_log" 2>&1
  rc=$?

  # Compatibility with older :stable images whose CLI accepts only INPUT.
  if [ "$rc" -ne 0 ] && grep -qi 'too many arguments' "$tmp_log"; then
    printf '[god-tear] retrying legacy one-argument CLI\n' >>"$tmp_log"
    legacy_log="$work/legacy-analysis.log"
    (
      cd "$work" || exit 1
      run_decompile "$input_file"
    ) >"$legacy_log" 2>&1
    legacy_rc=$?
    cat "$legacy_log" >>"$tmp_log" 2>/dev/null || true
    if [ "$legacy_rc" -eq 0 ]; then
      generated=""
      for candidate in "$work"/*.ghidra-out; do
        if [ -d "$candidate" ]; then generated="$candidate"; break; fi
      done
      if [ -n "$generated" ]; then
        cp -a "$generated"/. "$out"/
        rc=0
      else
        printf '[god-tear] legacy CLI exited successfully but produced no output directory\n' >>"$tmp_log"
        rc=1
      fi
    else
      rc=$legacy_rc
    fi
  fi
fi

cp "$tmp_log" "$out/analysis.log" 2>/dev/null || true
cat "$tmp_log" 2>/dev/null || true
rm -rf "$work" "$tmp_log"

result=success
if [ "$rc" -ne 0 ]; then
  result=failed
  cat > "$out/ANALYSIS_FAILED.txt" <<EOF
Static analysis returned exit code $rc.

Inspect analysis.log for the exact decompiler error. The sandbox remains open
for manual inspection, but Test Launch will not pass until analysis succeeds.
EOF
  printf '[god-tear] ANALYSIS_FAILED exit=%s\n' "$rc" >&2
else
  printf '[god-tear] ANALYSIS_OK\n'
fi

python3 - "$status_file" "$original" "$safe" "$rc" "$result" <<'PY_STATUS'
import json, sys
path, original, output_name, code, result = sys.argv[1:]
with open(path, 'w', encoding='utf-8') as handle:
    json.dump({
        'input_name': original,
        'output_directory': '/workspace/' + output_name,
        'exit_code': int(code),
        'result': result,
    }, handle, indent=2)
    handle.write('\n')
PY_STATUS

printf '[god-tear] CODESANDBOX_ANALYSIS_FINISHED result=%s exit=%s\n' "$result" "$rc"
if [ "$rc" -eq 0 ]; then
  printf '[god-tear] CODESANDBOX_ANALYSIS_COMPLETE\n'
fi
rm -rf "$CODESANDBOX_RUNTIME_TMP"
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
                "completion_requirements": ["log:CODESANDBOX_ANALYSIS_FINISHED"],
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
        "managed_seed": GOD_TEAR_SLUG,
        "seed_version": GOD_TEAR_SEED_VERSION,
        "entrypoint": ["/bin/sh", "-lc"],
        "image_pull_policy": "if_not_present",
        "workspace_enabled": True,
        "terminal_scope": "container",
        "allowed_file_types": ["*"],
        "allow_extensionless_input": True,
        "max_input_size_mb": 128,
        "required_args": ["decompile"],
        "forbidden_args": ["--ai", "--update", "--image", "--docker-image", "--local"],
        "test_resources": {
            "vcpu": 1,
            "ram_gb": 2,
            "disk_gb": 3,
            "max_timeout_hr": 1,
        },
        "resource_guard": {
            # The decompiler should run inside the Java/process limits above.
            # Do not treat normal high container memory as a test failure; only
            # keep a final host-emergency circuit breaker.
            "memory_high_watermark_pct": 0,
            "host_min_available_mb": 2048,
            "max_runtime_seconds": 0,
        },
        "test_config": {
            "requirements": [
                "runtime_started",
                "log:CODESANDBOX_ANALYSIS_COMPLETE",
                "filesystem_ready",
            ]
        },
        "ui": {
            "background_run": {"completion_log": "CODESANDBOX_ANALYSIS_FINISHED"},
            "lab_ui": {"filesystem_root": "/workspace", "start_path": "/"},
        },
        "environment": {
            "HOME": "/workspace/codesandbox-runtime-tmp/decompile-home",
            "DECOMPILE_IN_DOCKER": "1",
            "DECOMPILE_NO_AI": "1",
            "DECOMPILE_NO_OPEN": "1",
            "DECOMPILE_NO_UNPACK": "1",
            "GHIDRA_TIMEOUT": "30",
            "MALLOC_ARENA_MAX": "2",
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
        "output_mount_path": "",
        "artifact_paths": '["/workspace"]',
        "input_required": True,
        "max_upload_mb": 128,
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
        "pids_limit": 192,
        "allow_full_internet": False,
        "max_timeout_hr": 1,
        "runtime_config": files,
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
            updated_by_id=admin_user_id,
        )
    return general, internet


def _create_or_upgrade_managed(
    values: dict,
    plan_states: dict[str, bool],
    *,
    seed_key: str,
    seed_version: int,
):
    """Create or upgrade only a platform-managed seeded template row."""
    from codesandbox.features.sandbox import repository as repo

    template = repo.get_template_by_slug(values["slug"])
    if template is None:
        template = repo.create_template(**values)
    else:
        current_runtime = _runtime_from_files(template.runtime_config)
        current_key = str(current_runtime.get("managed_seed") or "")
        try:
            current_version = int(current_runtime.get("seed_version") or 0)
        except (TypeError, ValueError):
            current_version = 0
        expected_image = str(values.get("docker_image") or "")
        managed_row = str(template.docker_image or "") == expected_image and (
            not current_key or current_key == seed_key
        )
        if managed_row and current_version != seed_version:
            update = dict(values)
            update.pop("created_by_id", None)
            update.pop("icon_path", None)
            update.update(
                status="maintenance",
                last_test_status="untested",
                last_tested_at=None,
                last_test_error=None,
            )
            template = repo.update_template(str(template.id), **update)

    for order, (plan_id, enabled) in enumerate(plan_states.items()):
        if repo.get_template_plan(str(template.id), plan_id) is None:
            repo.upsert_template_plan(
                str(template.id), plan_id, is_enabled=enabled, sort_order=order
            )
    return template


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
    _create_or_upgrade_managed(
        _ubuntu_study(admin_user_id), internet_only,
        seed_key="ubuntu-study-terminal", seed_version=MANAGED_TEMPLATE_SEED_VERSION,
    )
    _create_or_upgrade_managed(
        _kali_study(admin_user_id), internet_only,
        seed_key="kali-study-terminal", seed_version=MANAGED_TEMPLATE_SEED_VERSION,
    )
    _create_or_upgrade_managed(
        _ubuntu_ide(admin_user_id), internet_only,
        seed_key="ubuntu-coding-ide", seed_version=MANAGED_TEMPLATE_SEED_VERSION,
    )

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
        update.pop("icon_path", None)
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

    print("  seeded Ubuntu, Kali, IDE, reverse workflow, and safe malware VM blueprint")
