#!/bin/sh
set -eu

LXCFS_ROOT="${LXCFS_ROOT:-/var/lib/lxcfs}"
LXCFS_LOG="${LXCFS_LOG:-/var/log/lxcfs.log}"
SANDBOX_LXCFS_ENABLED="${SANDBOX_LXCFS_ENABLED:-auto}"
SANDBOX_LXCFS_STATE_FILE="${SANDBOX_LXCFS_STATE_FILE:-/certs/client/codesandbox-lxcfs-enabled}"

mkdir -p "$LXCFS_ROOT" "$(dirname "$LXCFS_LOG")"

is_true() {
    case "$1" in
        1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
        *) return 1 ;;
    esac
}

is_false() {
    case "$1" in
        0|false|FALSE|False|no|NO|No|off|OFF|Off) return 0 ;;
        *) return 1 ;;
    esac
}

write_lxcfs_state() {
    mkdir -p "$(dirname "$SANDBOX_LXCFS_STATE_FILE")"
    printf '%s\n' "$1" >"$SANDBOX_LXCFS_STATE_FILE"
}

can_attempt_lxcfs() {
    command -v lxcfs >/dev/null 2>&1 &&
        command -v fusermount3 >/dev/null 2>&1 &&
        test -e /dev/fuse &&
        grep -qw fuse /proc/filesystems
}

start_lxcfs() {
    # A recreated DinD container can inherit a stale FUSE mountpoint. Remove it
    # before starting a fresh LXCFS process.
    fusermount3 -u "$LXCFS_ROOT" >/dev/null 2>&1 || true

    lxcfs -u "$LXCFS_ROOT" >"$LXCFS_LOG" 2>&1 &
    lxcfs_pid=$!

    cleanup_lxcfs() {
        kill "$lxcfs_pid" >/dev/null 2>&1 || true
    }
    trap cleanup_lxcfs EXIT INT TERM

    ready=0
    for _ in $(seq 1 100); do
        if kill -0 "$lxcfs_pid" >/dev/null 2>&1 \
            && test -r "$LXCFS_ROOT/proc/cpuinfo" \
            && test -r "$LXCFS_ROOT/proc/meminfo" \
            && test -r "$LXCFS_ROOT/proc/stat" \
            && test -r "$LXCFS_ROOT/sys/devices/system/cpu/online"; then
            ready=1
            break
        fi
        sleep 0.1
    done

    if [ "$ready" -eq 1 ]; then
        return 0
    fi

    cleanup_lxcfs
    trap - EXIT INT TERM
    return 1
}

if is_false "$SANDBOX_LXCFS_ENABLED"; then
    write_lxcfs_state false
    echo "[docker-engine] LXCFS disabled by SANDBOX_LXCFS_ENABLED=$SANDBOX_LXCFS_ENABLED"
elif can_attempt_lxcfs && start_lxcfs; then
    write_lxcfs_state true
    echo "[docker-engine] LXCFS ready at $LXCFS_ROOT"
elif is_true "$SANDBOX_LXCFS_ENABLED"; then
    echo "[docker-engine] LXCFS failed to become ready" >&2
    cat "$LXCFS_LOG" >&2 2>/dev/null || true
    exit 1
else
    write_lxcfs_state false
    echo "[docker-engine] LXCFS unavailable; continuing without container-aware procfs"
fi

# Keep the upstream Docker-in-Docker initialization and TLS behavior.
trap - EXIT INT TERM
exec dockerd-entrypoint.sh "$@"
