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

Lifecycle (mock):
  start → "started" after ~3 s → metrics stream → "stopped" after timeout or stop signal
  stop  → signals running simulation to stop early
  kill  → signals stop, sends "killed" immediately
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import sys
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
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "dev-worker-token-change-in-production")
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
        except Exception as exc:
            log.warning("NATS unavailable (%s) — metrics will not be streamed", exc)

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


# ── HTTP callback ─────────────────────────────────────────────────────────────

def callback(callback_url: str, callback_token: str, instance_id: str, event: str, data: dict | None = None) -> None:
    payload = {"instance_id": instance_id, "event": event, "data": data or {}}
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

    while not stop_ev.is_set():
        # Simulate gradual change
        cpu = max(5, min(95, cpu + random.gauss(0, 4)))
        mem = max(64, min(2048, mem + random.gauss(0, 20)))
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
                "net_rx_kb": round(net_rx, 2),
                "net_tx_kb": round(net_tx, 2),
            },
        )
        stop_ev.wait(1)


# ── Job handlers ──────────────────────────────────────────────────────────────

def simulate_start(job: dict) -> None:
    instance_id = job["instance_id"]
    cb_url = job.get("callback_url", CONTROL_PLANE_URL + "/internal/worker/callback")
    cb_tok = job.get("callback_token", WORKER_TOKEN)

    stop_ev = threading.Event()
    with _lock:
        _stop_events[instance_id] = stop_ev

    # Simulate provisioning delay (2–4 s)
    prov_time = random.uniform(2, 4)
    log.info("provisioning %s (%.1fs) …", instance_id[:8], prov_time)
    _publish_event(instance_id, "provisioning")

    if stop_ev.wait(prov_time):
        callback(cb_url, cb_tok, instance_id, "stopped", {"reason": "stop_during_provision"})
        _publish_event(instance_id, "stopped", {"reason": "stop_during_provision"})
        with _lock:
            _stop_events.pop(instance_id, None)
        return

    # Send "started" callback + event
    callback(cb_url, cb_tok, instance_id, "started")
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

    callback(cb_url, cb_tok, instance_id, "stopped", {"reason": reason})
    _publish_event(instance_id, "stopped", {"reason": reason})
    log.info("stopped   %s (%s)", instance_id[:8], reason)

    with _lock:
        _stop_events.pop(instance_id, None)


def handle_stop(job: dict) -> None:
    instance_id = job["instance_id"]
    cb_url = job.get("callback_url", CONTROL_PLANE_URL + "/internal/worker/callback")
    cb_tok = job.get("callback_token", WORKER_TOKEN)

    with _lock:
        ev = _stop_events.get(instance_id)
    if ev:
        ev.set()
        log.info("signalled stop for %s", instance_id[:8])
    else:
        time.sleep(0.5)
        callback(cb_url, cb_tok, instance_id, "stopped", {"reason": "direct_stop"})
        _publish_event(instance_id, "stopped", {"reason": "direct_stop"})


def handle_kill(job: dict) -> None:
    instance_id = job["instance_id"]
    cb_url = job.get("callback_url", CONTROL_PLANE_URL + "/internal/worker/callback")
    cb_tok = job.get("callback_token", WORKER_TOKEN)

    with _lock:
        ev = _stop_events.pop(instance_id, None)
    if ev:
        ev.set()
    callback(cb_url, cb_tok, instance_id, "killed", {"reason": "kill_signal"})
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
        t = threading.Thread(target=simulate_start, args=(job,), daemon=True)
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
