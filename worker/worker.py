"""
Mock sandbox worker — development only.

Reads jobs from Redis, simulates sandbox lifecycle with delays, and posts
status callbacks back to the Flask control plane via HTTP.

Real-time metrics are published to NATS so the admin monitor panel can
display live CPU/RAM/network charts without polling.

Queue:   LPUSH codesandbox:sandbox-jobs <json>  (Flask enqueues)
         BRPOP codesandbox:sandbox-jobs <timeout> (this worker consumes)

NATS subjects published:
  codesandbox.sandbox.metrics.<instance_id>  — metrics every ~1 s while running
  codesandbox.sandbox.events.<instance_id>   — lifecycle events
  codesandbox.sandbox.terminal.<instance_id>.output — PTY output bytes

NATS subjects subscribed (wildcard, one subscription covers every instance):
  codesandbox.sandbox.terminal.*.ctl    — {"action":"open"|"close"|"resize",...}
  codesandbox.sandbox.terminal.*.input  — {"data": "<keystrokes>"}

Lifecycle (mock):
  start → "started" after ~3 s → metrics stream → "stopped" after timeout or stop signal
  stop  → signals running simulation to stop early
  kill  → signals stop, sends "killed" immediately

Terminal: a real PTY (/bin/bash) spawned per instance_id on "open", killed on
"close". This is a genuine shell in the WORKER container, not (yet) an exec
into the template's own container image — that requires the real container
runtime worker (Phase 6a). Isolated per instance: its own PTY, its own
process group, torn down independently of any other session.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import random
import shutil
import signal
import struct
import sys
import termios
import threading
import time

import nats
import redis
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mock-worker")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
QUEUE_KEY = "codesandbox:sandbox-jobs"
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "http://app:5000")
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")

# instance_id → stop event (signals running simulations to stop early)
_stop_events: dict[str, threading.Event] = {}
_lock = threading.Lock()
_running = True

# ── NATS async pub ────────────────────────────────────────────────────────────

_nats_loop: asyncio.AbstractEventLoop | None = None
_nats_client: nats.aio.client.Client | None = None


def _start_nats_loop() -> None:
    """Run an asyncio event loop in a background thread for NATS publishing."""
    global _nats_loop, _nats_client

    async def _connect():
        global _nats_client
        try:
            _nats_client = await nats.connect(NATS_URL, name="mock-worker")
            log.info("NATS connected: %s", NATS_URL)
            await _nats_client.subscribe("codesandbox.sandbox.terminal.*.ctl", cb=_on_terminal_ctl)
            await _nats_client.subscribe("codesandbox.sandbox.terminal.*.input", cb=_on_terminal_input)
            await _nats_client.subscribe("codesandbox.sandbox.fs.*.request", cb=_on_fs_request)
            log.info("terminal control channel ready")
        except Exception as exc:
            log.warning("NATS unavailable (%s) — metrics/terminal will not work", exc)

    loop = asyncio.new_event_loop()
    _nats_loop = loop
    loop.run_until_complete(_connect())
    loop.run_forever()


def _nats_publish(subject: str, payload: dict) -> None:
    """Thread-safe NATS publish from a sync context."""
    if _nats_loop is None or _nats_client is None or _nats_client.is_closed:
        return
    data = json.dumps(payload).encode()
    asyncio.run_coroutine_threadsafe(_nats_client.publish(subject, data), _nats_loop)


# ── Per-instance workspace isolation ────────────────────────────────────────────
# Every instance gets its own directory instead of sharing /tmp — without this,
# concurrent sandbox instances could read/write each other's files.

_WORKSPACE_ROOT = "/tmp/workspaces"


def _workspace_dir(instance_id: str) -> str:
    """Per-instance isolated working directory, created + seeded on first use."""
    path = os.path.join(_WORKSPACE_ROOT, instance_id)
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        try:
            with open(os.path.join(path, "main.py"), "w") as f:
                f.write('print("hello from your sandbox")\n')
            with open(os.path.join(path, "README.md"), "w") as f:
                f.write("# Workspace\n\nFiles here are isolated to this sandbox instance.\n")
        except OSError:
            pass
    return path


def _resolve_workspace_path(instance_id: str, rel_path: str) -> str | None:
    """Resolve rel_path against the instance's workspace root.

    Returns None if the resolved path would escape the workspace root — via
    `../` traversal, an absolute path, or a symlink pointing outside it.
    """
    root_real = os.path.realpath(_workspace_dir(instance_id))
    candidate = os.path.join(root_real, rel_path.lstrip("/"))
    candidate_real = os.path.realpath(candidate)
    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        return None
    return candidate_real


# ── Filesystem operations (REST, relayed via NATS request-reply) ───────────────

_FS_READ_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
_FS_WRITE_MAX_BYTES = 5 * 1024 * 1024   # 5 MB


def _fs_list(instance_id: str, rel_path: str) -> dict:
    target = _resolve_workspace_path(instance_id, rel_path)
    if target is None:
        return {"ok": False, "error": "invalid path"}
    if not os.path.isdir(target):
        return {"ok": False, "error": "not a directory"}
    entries = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                    if not is_dir and not is_file:
                        continue  # skip symlinks/sockets/etc — nothing to browse into
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append({
                    "name": entry.name,
                    "type": "dir" if is_dir else "file",
                    "size": None if is_dir else st.st_size,
                })
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"ok": True, "entries": entries}


def _fs_read(instance_id: str, rel_path: str) -> dict:
    target = _resolve_workspace_path(instance_id, rel_path)
    if target is None:
        return {"ok": False, "error": "invalid path"}
    if not os.path.isfile(target):
        return {"ok": False, "error": "not a file"}
    try:
        size = os.path.getsize(target)
        if size > _FS_READ_MAX_BYTES:
            return {"ok": False, "error": "file too large to open (max 10 MB)"}
        with open(target, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": True, "binary": True, "size": size}
    return {"ok": True, "binary": False, "content": text, "size": size}


def _fs_write(instance_id: str, rel_path: str, content: str) -> dict:
    target = _resolve_workspace_path(instance_id, rel_path)
    if target is None:
        return {"ok": False, "error": "invalid path"}
    if os.path.isdir(target):
        return {"ok": False, "error": "is a directory"}
    data = content.encode("utf-8")
    if len(data) > _FS_WRITE_MAX_BYTES:
        return {"ok": False, "error": "file too large to save (max 5 MB)"}
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def _fs_mkdir(instance_id: str, rel_path: str) -> dict:
    target = _resolve_workspace_path(instance_id, rel_path)
    if target is None:
        return {"ok": False, "error": "invalid path"}
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def _fs_rename(instance_id: str, rel_old: str, rel_new: str) -> dict:
    old_t = _resolve_workspace_path(instance_id, rel_old)
    new_t = _resolve_workspace_path(instance_id, rel_new)
    if old_t is None or new_t is None:
        return {"ok": False, "error": "invalid path"}
    if not os.path.exists(old_t):
        return {"ok": False, "error": "not found"}
    try:
        os.makedirs(os.path.dirname(new_t), exist_ok=True)
        os.rename(old_t, new_t)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def _fs_delete(instance_id: str, rel_path: str) -> dict:
    target = _resolve_workspace_path(instance_id, rel_path)
    if target is None:
        return {"ok": False, "error": "invalid path"}
    if target == os.path.realpath(_workspace_dir(instance_id)):
        return {"ok": False, "error": "cannot delete workspace root"}
    try:
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


async def _on_fs_request(msg) -> None:
    instance_id = _subject_instance_id(msg.subject)
    if not instance_id:
        return
    try:
        body = json.loads(msg.data.decode())
    except Exception:
        await _fs_reply(msg, {"ok": False, "error": "bad request"})
        return

    op = body.get("op", "")
    try:
        if op == "list":
            result = _fs_list(instance_id, body.get("path", "/"))
        elif op == "read":
            result = _fs_read(instance_id, body.get("path", ""))
        elif op == "write":
            result = _fs_write(instance_id, body.get("path", ""), body.get("content", ""))
        elif op == "mkdir":
            result = _fs_mkdir(instance_id, body.get("path", ""))
        elif op == "rename":
            result = _fs_rename(instance_id, body.get("old", ""), body.get("new", ""))
        elif op == "delete":
            result = _fs_delete(instance_id, body.get("path", ""))
        else:
            result = {"ok": False, "error": "unknown op"}
    except Exception:
        log.exception("fs op %r failed for %s", op, instance_id[:8])
        result = {"ok": False, "error": "internal error"}

    await _fs_reply(msg, result)


async def _fs_reply(msg, result: dict) -> None:
    try:
        await msg.respond(json.dumps(result).encode())
    except Exception:
        pass


# ── Terminal (real PTY, isolated per instance) ─────────────────────────────────

_pty_sessions: dict[str, dict] = {}
_pty_lock = threading.Lock()


def _terminal_output_subject(instance_id: str) -> str:
    return f"codesandbox.sandbox.terminal.{instance_id}.output"


def _pty_reader(instance_id: str, master_fd: int, stop_ev: threading.Event) -> None:
    """Blocking read loop on the PTY master fd, run in its own thread."""
    try:
        while not stop_ev.is_set():
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            _nats_publish(_terminal_output_subject(instance_id), {
                "type": "output", "data": chunk.decode(errors="replace"),
            })
    finally:
        _nats_publish(_terminal_output_subject(instance_id), {"type": "closed"})
        with _pty_lock:
            _pty_sessions.pop(instance_id, None)


def _terminal_open(instance_id: str) -> None:
    with _pty_lock:
        if instance_id in _pty_sessions:
            return  # already open
    try:
        pid, master_fd = pty.fork()
    except OSError as exc:
        log.warning("pty.fork failed for %s: %s", instance_id[:8], exc)
        return

    if pid == 0:
        # Child: replace with an interactive shell in its own session, rooted
        # in this instance's isolated workspace (same dir the fs API reads
        # from) so the terminal and file tree stay in sync.
        shell = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
        env = dict(os.environ, TERM="xterm-256color", PS1="sandbox-test:\\w\\$ ")
        os.chdir(_workspace_dir(instance_id))
        os.execvpe(shell, [shell], env)
        os._exit(1)  # pragma: no cover — only reached if execvpe fails

    stop_ev = threading.Event()
    reader = threading.Thread(target=_pty_reader, args=(instance_id, master_fd, stop_ev), daemon=True)
    with _pty_lock:
        _pty_sessions[instance_id] = {"pid": pid, "master_fd": master_fd, "stop_ev": stop_ev}
    reader.start()
    log.info("terminal opened for %s (pid=%s)", instance_id[:8], pid)
    _nats_publish(_terminal_output_subject(instance_id), {"type": "ready"})


def _terminal_close(instance_id: str) -> None:
    with _pty_lock:
        session = _pty_sessions.pop(instance_id, None)
    if not session:
        return
    session["stop_ev"].set()
    try:
        os.kill(session["pid"], signal.SIGHUP)
    except OSError:
        pass
    try:
        os.close(session["master_fd"])
    except OSError:
        pass
    log.info("terminal closed for %s", instance_id[:8])


def _terminal_resize(instance_id: str, cols: int, rows: int) -> None:
    with _pty_lock:
        session = _pty_sessions.get(instance_id)
    if not session:
        return
    try:
        fcntl.ioctl(session["master_fd"], termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def _terminal_write(instance_id: str, data: str) -> None:
    with _pty_lock:
        session = _pty_sessions.get(instance_id)
    if not session:
        return
    try:
        os.write(session["master_fd"], data.encode())
    except OSError:
        pass


def _subject_instance_id(subject: str) -> str | None:
    # codesandbox.sandbox.terminal.<instance_id>.ctl|input
    parts = subject.split(".")
    return parts[3] if len(parts) >= 5 else None


async def _on_terminal_ctl(msg) -> None:
    instance_id = _subject_instance_id(msg.subject)
    if not instance_id:
        return
    try:
        body = json.loads(msg.data)
    except Exception:
        return
    action = body.get("action")
    if action == "open":
        threading.Thread(target=_terminal_open, args=(instance_id,), daemon=True).start()
    elif action == "close":
        _terminal_close(instance_id)
    elif action == "resize":
        _terminal_resize(instance_id, int(body.get("cols", 80)), int(body.get("rows", 24)))


async def _on_terminal_input(msg) -> None:
    instance_id = _subject_instance_id(msg.subject)
    if not instance_id:
        return
    try:
        body = json.loads(msg.data)
    except Exception:
        return
    data = body.get("data", "")
    if data:
        _terminal_write(instance_id, data)


# ── HTTP callback ─────────────────────────────────────────────────────────────

def callback(
    callback_url: str,
    callback_token: str,
    job_id: str,
    instance_id: str,
    event: str,
    data: dict | None = None,
) -> None:
    payload = {"job_id": job_id, "instance_id": instance_id, "event": event, "data": data or {}}
    try:
        resp = requests.post(
            callback_url,
            json=payload,
            headers={"Authorization": f"Bearer {callback_token}"},
            timeout=10,
        )
        log.info("callback %s %s → %s", instance_id[:8], event, resp.status_code)
    except Exception as exc:
        log.warning("callback failed %s %s: %s", instance_id[:8], event, exc)


def _publish_event(instance_id: str, event: str, data: dict | None = None) -> None:
    _nats_publish(
        f"codesandbox.sandbox.events.{instance_id}",
        {"type": "event", "instance_id": instance_id, "event": event, "data": data or {}},
    )


# ── Metrics simulation ────────────────────────────────────────────────────────

def _stream_metrics(instance_id: str, stop_ev: threading.Event) -> None:
    """Publish mock CPU/RAM/network metrics to NATS every second until stopped."""
    cpu = random.uniform(15, 30)
    mem = random.uniform(256, 512)
    disk = random.uniform(20, 40)

    while not stop_ev.is_set():
        # Simulate gradual change
        cpu = max(5, min(95, cpu + random.gauss(0, 4)))
        mem = max(64, min(2048, mem + random.gauss(0, 20)))
        disk = max(5, min(95, disk + random.gauss(0, 0.5)))  # drifts slowly, unlike cpu/mem
        net_rx = max(0, random.uniform(0, 50) + random.choice([0, 0, 0, 200]))
        net_tx = max(0, random.uniform(0, 20))

        _nats_publish(
            f"codesandbox.sandbox.metrics.{instance_id}",
            {
                "type": "metrics",
                "instance_id": instance_id,
                "ts": int(time.time()),
                "cpu_pct": round(cpu, 2),
                "mem_mb": int(mem),
                "disk_pct": round(disk, 2),
                "net_rx_kb": round(net_rx, 2),
                "net_tx_kb": round(net_tx, 2),
            },
        )
        stop_ev.wait(1)


# ── Job handlers ──────────────────────────────────────────────────────────────

def simulate_start(job: dict, stop_ev: threading.Event) -> None:
    job_id = job["job_id"]
    instance_id = job["instance_id"]
    cb_url = job.get("callback_url", CONTROL_PLANE_URL + "/internal/worker/callback")
    cb_tok = job["callback_token"]

    # Simulate provisioning delay (2–4 s)
    prov_time = random.uniform(2, 4)
    log.info("provisioning %s (%.1fs) …", instance_id[:8], prov_time)
    _publish_event(instance_id, "provisioning")

    if stop_ev.wait(prov_time):
        callback(cb_url, cb_tok, job_id, instance_id, "stopped", {"reason": "stop_during_provision"})
        _publish_event(instance_id, "stopped", {"reason": "stop_during_provision"})
        with _lock:
            _stop_events.pop(instance_id, None)
        return

    # Send "started" callback + event
    callback(cb_url, cb_tok, job_id, instance_id, "started")
    _publish_event(instance_id, "started")
    log.info("running   %s", instance_id[:8])

    # Stream metrics in a daemon thread while running
    metrics_thread = threading.Thread(
        target=_stream_metrics, args=(instance_id, stop_ev), daemon=True
    )
    metrics_thread.start()

    # Run until stop signal or timeout
    runtime = int(job.get("runtime_policy", {}).get("max_timeout_sec", 30))
    runtime = min(runtime, 60)  # cap mock runtime to 60 s

    if stop_ev.wait(runtime):
        reason = "user_stop"
    else:
        reason = "timeout"

    stop_ev.set()  # ensure metrics thread exits
    metrics_thread.join(timeout=2)

    callback(cb_url, cb_tok, job_id, instance_id, "stopped", {"reason": reason})
    _publish_event(instance_id, "stopped", {"reason": reason})
    log.info("stopped   %s (%s)", instance_id[:8], reason)

    with _lock:
        _stop_events.pop(instance_id, None)


def handle_stop(job: dict) -> None:
    job_id = job["job_id"]
    instance_id = job["instance_id"]
    cb_url = job.get("callback_url", CONTROL_PLANE_URL + "/internal/worker/callback")
    cb_tok = job["callback_token"]

    with _lock:
        ev = _stop_events.get(instance_id)
    if ev:
        ev.set()
        log.info("signalled stop for %s", instance_id[:8])
        callback(cb_url, cb_tok, job_id, instance_id, "stopped", {"reason": "stop_signal"})
        _publish_event(instance_id, "stopped", {"reason": "stop_signal"})
    else:
        time.sleep(0.5)
        callback(cb_url, cb_tok, job_id, instance_id, "stopped", {"reason": "direct_stop"})
        _publish_event(instance_id, "stopped", {"reason": "direct_stop"})


def handle_kill(job: dict) -> None:
    job_id = job["job_id"]
    instance_id = job["instance_id"]
    cb_url = job.get("callback_url", CONTROL_PLANE_URL + "/internal/worker/callback")
    cb_tok = job["callback_token"]

    with _lock:
        ev = _stop_events.pop(instance_id, None)
    if ev:
        ev.set()
    callback(cb_url, cb_tok, job_id, instance_id, "killed", {"reason": "kill_signal"})
    _publish_event(instance_id, "killed", {"reason": "kill_signal"})
    log.info("killed %s", instance_id[:8])


def process_job(raw: str) -> None:
    try:
        job = json.loads(raw)
    except Exception:
        log.warning("malformed job payload: %r", raw[:120])
        return

    action = job.get("action", "")
    instance_id = job.get("instance_id", "?")
    log.info("job  action=%s instance=%s", action, instance_id[:8])

    if action == "start":
        stop_ev = threading.Event()
        with _lock:
            _stop_events[instance_id] = stop_ev
        t = threading.Thread(target=simulate_start, args=(job, stop_ev), daemon=True)
        t.start()
    elif action == "stop":
        handle_stop(job)
    elif action == "kill":
        handle_kill(job)
    else:
        log.warning("unknown action: %s", action)


def main() -> None:
    log.info("mock worker starting — redis=%s nats=%s", REDIS_URL, NATS_URL)

    # Start NATS event loop in background thread
    nats_thread = threading.Thread(target=_start_nats_loop, daemon=True)
    nats_thread.start()
    time.sleep(1)  # give NATS loop time to connect

    client = redis.from_url(REDIS_URL, decode_responses=True)

    def _shutdown(sig, frame):
        global _running
        log.info("shutdown signal received")
        _running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while _running:
        try:
            result = client.brpop(QUEUE_KEY, timeout=2)
            if result:
                _, raw = result
                process_job(raw)
        except redis.exceptions.ConnectionError as exc:
            log.warning("redis connection error: %s — retrying in 3s", exc)
            time.sleep(3)
        except Exception as exc:
            log.exception("unexpected error: %s", exc)
            time.sleep(1)

    log.info("mock worker stopped")


if __name__ == "__main__":
    main()
