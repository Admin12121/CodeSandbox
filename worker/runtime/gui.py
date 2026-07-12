from __future__ import annotations

import base64
import logging
import socket
import threading

log = logging.getLogger("codesandbox-worker.gui")


class DockerGuiProxy:
    """Relays raw bytes between a sandbox container's internal VNC (or other
    GUI framebuffer) port and the browser, over NATS — mirrors
    DockerTerminalManager's shape/threading model exactly, but moves raw
    binary chunks (base64-wrapped in the NATS JSON envelope, matching the
    rest of this worker's publish() convention) instead of PTY text.

    The browser never gets a raw container IP/port (docs/plan.md Phase
    10.6) — it only ever sees `/ws/sandbox/<id>/gui`, proxied by the
    control plane, which relays to this worker over NATS, which is the only
    thing that ever opens the actual TCP connection to the container.
    """

    def __init__(self, registry, publish, worker_id: str) -> None:
        self.registry = registry
        self.publish = publish
        self.worker_id = worker_id
        self._sessions: dict[str, dict] = {}
        self._lock = threading.RLock()

    def _subject(self, instance_id: str) -> str:
        # See DockerTerminalManager._subject — codesandbox.sandbox.> is the
        # worker's actual NATS publish grant, not codesandbox.worker.>.
        return f"codesandbox.sandbox.{instance_id}.gui.output"

    def _publish(self, instance_id: str, payload: dict) -> None:
        self.publish(self._subject(instance_id), payload)

    def open(self, instance_id: str) -> None:
        with self._lock:
            if instance_id in self._sessions:
                self._publish(instance_id, {"type": "ready"})
                return
        runner = self.registry.get(instance_id)
        if runner is None or not getattr(runner, "is_running", False):
            self._publish(instance_id, {"type": "error", "message": "Sandbox is not running."})
            return
        gui_config = (runner.policy or {}).get("desktop_gui") or {}
        port = int(gui_config.get("internal_port") or 0)
        if not port:
            self._publish(instance_id, {
                "type": "error",
                "message": "This template has no desktop_gui.internal_port configured.",
            })
            return
        container_name = f"cs-{instance_id}"
        try:
            sock = socket.create_connection((container_name, port), timeout=5)
        except Exception as exc:
            log.warning("gui connect failed instance=%s port=%s error=%s", instance_id[:8], port, exc)
            self._publish(instance_id, {
                "type": "error",
                "message": f"Could not reach the GUI service on port {port}.",
            })
            return

        stop_event = threading.Event()
        session = {"socket": sock, "stop": stop_event}
        with self._lock:
            self._sessions[instance_id] = session
        threading.Thread(target=self._read, args=(instance_id, session), daemon=True).start()
        self._publish(instance_id, {"type": "ready"})

    def _read(self, instance_id: str, session: dict) -> None:
        sock = session["socket"]
        try:
            while not session["stop"].is_set():
                chunk = sock.recv(65536)
                if not chunk:
                    break
                self._publish(instance_id, {
                    "type": "output",
                    "data_b64": base64.b64encode(chunk).decode("ascii"),
                })
        except Exception:
            pass
        finally:
            with self._lock:
                if self._sessions.get(instance_id) is session:
                    self._sessions.pop(instance_id, None)
            self._publish(instance_id, {"type": "closed"})

    def write(self, instance_id: str, data: bytes) -> None:
        with self._lock:
            session = self._sessions.get(instance_id)
        if session is None:
            return
        try:
            session["socket"].sendall(data)
        except Exception:
            self.close(instance_id)

    def close(self, instance_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(instance_id, None)
        if session is None:
            return
        session["stop"].set()
        try:
            session["socket"].close()
        except Exception:
            pass

    def close_all(self) -> None:
        with self._lock:
            instance_ids = list(self._sessions)
        for instance_id in instance_ids:
            self.close(instance_id)
