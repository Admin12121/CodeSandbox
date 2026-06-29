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
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")

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


# ── WebSocket: real-time instance monitor ─────────────────────────────────────

async def ws_sandbox_monitor(websocket: WebSocket) -> None:
    """
    Streams sandbox metrics to the browser in real-time.

    NATS subject: codesandbox.sandbox.metrics.<instance_id>
    Payload:  {"type":"metrics","ts":…,"cpu_pct":…,"mem_mb":…,"net_rx_kb":…,"net_tx_kb":…}

    Also relays lifecycle events on: codesandbox.sandbox.events.<instance_id>
    """
    instance_id: str = websocket.path_params["instance_id"]
    await websocket.accept()

    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

    async def _enqueue(msg: "_nats.aio.msg.Msg") -> None:
        try:
            queue.put_nowait(msg.data)
        except asyncio.QueueFull:
            pass

    subs = []
    try:
        nc = await _get_nats()
        subs.append(await nc.subscribe(f"codesandbox.sandbox.metrics.{instance_id}", cb=_enqueue))
        subs.append(await nc.subscribe(f"codesandbox.sandbox.events.{instance_id}", cb=_enqueue))
    except Exception as exc:
        log.warning("NATS subscribe failed for %s: %s — sending error frame", instance_id[:8], exc)
        await websocket.send_text(json.dumps({"type": "error", "message": "NATS unavailable"}))
        await websocket.close()
        return

    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=25)
                await websocket.send_bytes(data)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        for sub in subs:
            try:
                await sub.unsubscribe()
            except Exception:
                pass


# ── Build the ASGI app ────────────────────────────────────────────────────────

def _make_app() -> Starlette:
    from codesandbox.app import create_app as _flask_factory
    flask_wsgi = _flask_factory()

    return Starlette(
        lifespan=lifespan,
        routes=[
            WebSocketRoute("/ws/sandbox/{instance_id}/monitor", ws_sandbox_monitor),
            Mount("/", WSGIMiddleware(flask_wsgi)),
        ],
    )


app = _make_app()
