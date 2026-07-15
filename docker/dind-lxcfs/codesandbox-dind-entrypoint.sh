#!/bin/sh
set -eu

LXCFS_ROOT="${LXCFS_ROOT:-/var/lib/lxcfs}"
LXCFS_LOG="${LXCFS_LOG:-/var/log/lxcfs.log}"

mkdir -p "$LXCFS_ROOT" "$(dirname "$LXCFS_LOG")"

# A recreated DinD container can inherit a stale FUSE mountpoint. Remove it
# before starting a fresh LXCFS process.
if command -v fusermount3 >/dev/null 2>&1; then
    fusermount3 -u "$LXCFS_ROOT" >/dev/null 2>&1 || true
fi

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

if [ "$ready" -ne 1 ]; then
    echo "[docker-engine] LXCFS failed to become ready" >&2
    cat "$LXCFS_LOG" >&2 2>/dev/null || true
    exit 1
fi

echo "[docker-engine] LXCFS ready at $LXCFS_ROOT"

# Keep the upstream Docker-in-Docker initialization and TLS behavior.
trap - EXIT INT TERM
exec dockerd-entrypoint.sh "$@"
