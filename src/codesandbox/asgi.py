"""
ASGI entry-point.

Mounts the Flask WSGI app via WSGIMiddleware and adds async WebSocket routes
for real-time sandbox monitoring (metrics streamed from NATS).

Run with: uvicorn codesandbox.asgi:app --host 0.0.0.0 --port 5000 --reload
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import nats as _nats
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from codesandbox.config import get_settings

log = logging.getLogger(__name__)

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")

# Must match the salt used by the token-issuing route in features/sandbox/routes.py.
_WS_TOKEN_SALT = "sandbox.monitor-ws"
_WS_TOKEN_MAX_AGE = 30  # seconds — the browser fetches and uses it immediately

_nc: "_nats.aio.client.Client | None" = None
_nc_lock = asyncio.Lock()


async def _get_nats() -> "_nats.aio.client.Client":
    global _nc
    if _nc is None or _nc.is_closed:
        async with _nc_lock:
            if _nc is None or _nc.is_closed:
                try:
                    _nc = await _nats.connect(NATS_URL, name="control-plane-ws")
                    log.info("NATS connected: %s", NATS_URL)
                except Exception as exc:
                    log.warning("NATS unavailable (%s) — monitor WS will not receive metrics", exc)
                    raise
    return _nc  # type: ignore[return-value]


@asynccontextmanager
async def lifespan(app: Starlette):
    try:
        await _get_nats()
    except Exception:
        pass
    yield
    if _nc and not _nc.is_closed:
        await _nc.drain()


def _verify_ws_token(token: str, instance_id: str, required_purpose: str = "monitor") -> bool:
    """Validate the short-lived signed token issued by GET .../monitor-token.

    Tokens issued before the `purpose` field existed have no such key — treat
    that as "monitor" so already-open monitor sessions don't break.
    """
    if not token:
        return False
    secret = get_settings().secret_key
    try:
        payload = URLSafeTimedSerializer(secret, salt=_WS_TOKEN_SALT).loads(
            token, max_age=_WS_TOKEN_MAX_AGE
        )
    except (BadSignature, SignatureExpired):
        return False
    if not isinstance(payload, dict) or payload.get("instance_id") != instance_id:
        return False
    return payload.get("purpose", "monitor") == required_purpose


# ── Per-instance NATS fan-out (one subscription serves every connected viewer) ─

class _InstanceFanout:
    __slots__ = ("subs", "viewers")

    def __init__(self) -> None:
        self.subs: list = []
        self.viewers: set[asyncio.Queue] = set()


_fanouts: dict[str, _InstanceFanout] = {}
_fanouts_lock = asyncio.Lock()


async def _acquire_fanout(instance_id: str) -> _InstanceFanout | None:
    """Get (or create) the shared NATS subscription for an instance_id."""
    async with _fanouts_lock:
        fanout = _fanouts.get(instance_id)
        if fanout is not None:
            return fanout

        try:
            nc = await _get_nats()
        except Exception:
            return None

        fanout = _InstanceFanout()

        async def _broadcast(msg: "_nats.aio.msg.Msg") -> None:
            for q in fanout.viewers:
                try:
                    q.put_nowait(msg.data)
                except asyncio.QueueFull:
                    pass

        fanout.subs.append(
            await nc.subscribe(f"codesandbox.sandbox.metrics.{instance_id}", cb=_broadcast)
        )
        fanout.subs.append(
            await nc.subscribe(f"codesandbox.sandbox.events.{instance_id}", cb=_broadcast)
        )
        _fanouts[instance_id] = fanout
        return fanout


async def _release_fanout(instance_id: str, queue: asyncio.Queue) -> None:
    """Drop a viewer; tear down the NATS subscription once nobody is left watching."""
    async with _fanouts_lock:
        fanout = _fanouts.get(instance_id)
        if fanout is None:
            return
        fanout.viewers.discard(queue)
        if fanout.viewers:
            return
        for sub in fanout.subs:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        _fanouts.pop(instance_id, None)


# ── WebSocket: real-time instance monitor ─────────────────────────────────────

async def ws_sandbox_monitor(websocket: WebSocket) -> None:
    """
    Streams sandbox metrics to the browser in real-time.

    Auth: requires a `token` query param — a short-lived signed token issued by
    GET /platform/sandboxes/<instance_id>/monitor-token to a session that has
    permission to view/manage this instance. This route sits on the Starlette
    layer outside the Flask blueprint, so it never sees the session cookie
    directly; the token is how it borrows that authorization decision.

    NATS subject: codesandbox.sandbox.metrics.<instance_id>
    Payload:  {"type":"metrics","ts":…,"cpu_pct":…,"mem_mb":…,"net_rx_kb":…,"net_tx_kb":…}

    Also relays lifecycle events on: codesandbox.sandbox.events.<instance_id>
    """
    instance_id: str = websocket.path_params["instance_id"]
    token = websocket.query_params.get("token", "")

    if not _verify_ws_token(token, instance_id):
        await websocket.close(code=1008)  # policy violation — reject before accept()
        return

    await websocket.accept()

    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    fanout = await _acquire_fanout(instance_id)
    if fanout is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "NATS unavailable"}))
        await websocket.close()
        return

    async with _fanouts_lock:
        fanout.viewers.add(queue)

    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=25)
                # Send as text, not send_bytes: browsers deliver binary WS
                # frames as a Blob in e.data, and JSON.parse(blob) throws —
                # the client's try/catch silently swallowed every metrics
                # and lifecycle-event message. data is already UTF-8 JSON
                # from NATS, so this is just a decode, no reshaping needed.
                await websocket.send_text(data.decode())
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await _release_fanout(instance_id, queue)


# ── WebSocket: real, isolated terminal for a running Test Launch instance ──────

async def ws_sandbox_terminal(websocket: WebSocket) -> None:
    """
    Bridges the browser to a real PTY spawned per-instance in the worker
    container (see worker/worker.py). Not (yet) an exec into the template's
    own container image — that requires the real container runtime worker
    (Phase 6a). Isolated per instance_id: its own PTY, its own process group.

    Auth: same short-lived signed token pattern as ws_sandbox_monitor, scoped
    to purpose="terminal" so a monitor token can't be replayed here.

    Bridges to the worker over NATS:
      publish   codesandbox.sandbox.terminal.<id>.ctl    {"action":"open"|"close"|"resize",...}
      publish   codesandbox.sandbox.terminal.<id>.input  {"data": "<keystrokes>"}
      subscribe codesandbox.sandbox.terminal.<id>.output {"type":"ready"|"output"|"closed",...}
    """
    instance_id: str = websocket.path_params["instance_id"]
    token = websocket.query_params.get("token", "")

    if not _verify_ws_token(token, instance_id, required_purpose="terminal"):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        nc = await _get_nats()
    except Exception:
        await websocket.send_text(json.dumps({"type": "error", "message": "NATS unavailable"}))
        await websocket.close()
        return

    ctl_subject = f"codesandbox.sandbox.terminal.{instance_id}.ctl"
    input_subject = f"codesandbox.sandbox.terminal.{instance_id}.input"
    output_subject = f"codesandbox.sandbox.terminal.{instance_id}.output"

    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)

    async def _enqueue(msg: "_nats.aio.msg.Msg") -> None:
        try:
            queue.put_nowait(msg.data)
        except asyncio.QueueFull:
            pass

    sub = await nc.subscribe(output_subject, cb=_enqueue)
    await nc.publish(ctl_subject, json.dumps({"action": "open"}).encode())

    async def _pump_output() -> None:
        while True:
            data = await queue.get()
            await websocket.send_text(data.decode())

    pump_task = asyncio.create_task(_pump_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                body = json.loads(raw)
            except Exception:
                continue
            if body.get("type") == "data":
                await nc.publish(input_subject, json.dumps({"data": body.get("data", "")}).encode())
            elif body.get("type") == "resize":
                await nc.publish(ctl_subject, json.dumps({
                    "action": "resize",
                    "cols": int(body.get("cols", 80)),
                    "rows": int(body.get("rows", 24)),
                }).encode())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        pump_task.cancel()
        try:
            await sub.unsubscribe()
        except Exception:
            pass
        try:
            await nc.publish(ctl_subject, json.dumps({"action": "close"}).encode())
        except Exception:
            pass


# ── Build the ASGI app ────────────────────────────────────────────────────────

def _make_app() -> Starlette:
    from codesandbox.app import app as flask_wsgi

    return Starlette(
        lifespan=lifespan,
        routes=[
            WebSocketRoute("/ws/sandbox/{instance_id}/monitor", ws_sandbox_monitor),
            WebSocketRoute("/ws/sandbox/{instance_id}/terminal", ws_sandbox_terminal),
            Mount("/", WSGIMiddleware(flask_wsgi)),
        ],
    )


app = _make_app()
