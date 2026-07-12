from __future__ import annotations

import logging
import threading

log = logging.getLogger("codesandbox-worker.terminal")


class DockerTerminalManager:
    def __init__(self, registry, publish, worker_id: str) -> None:
        self.registry = registry
        self.publish = publish
        self.worker_id = worker_id
        self._sessions: dict[tuple[str, str], dict] = {}
        self._lock = threading.RLock()
        self.max_sessions_per_instance = 3

    @staticmethod
    def _raw_socket(wrapper):
        return getattr(wrapper, "_sock", wrapper)

    def _subject(self, instance_id: str) -> str:
        # Under codesandbox.sandbox.> (not codesandbox.worker.>) — that's the
        # worker's actual NATS publish grant and the control plane's actual
        # subscribe grant (see docker/nats/nats-server.conf); ctl/input stay
        # under codesandbox.worker.> since those are published by the
        # control plane and subscribed to by the worker, the other way round.
        return f"codesandbox.sandbox.{instance_id}.terminal.output"

    def _publish(self, instance_id: str, terminal_id: str, payload: dict) -> None:
        self.publish(self._subject(instance_id), {
            "terminal_id": terminal_id,
            **payload,
        })

    def _session_key(self, instance_id: str, terminal_id: str) -> tuple[str, str]:
        return instance_id, terminal_id or "terminal-1"

    def open(self, instance_id: str, terminal_id: str = "terminal-1") -> None:
        key = self._session_key(instance_id, terminal_id)
        with self._lock:
            if key in self._sessions:
                self._publish(instance_id, terminal_id, {"type": "ready"})
                return
            count = sum(1 for existing_instance_id, _ in self._sessions if existing_instance_id == instance_id)
            if count >= self.max_sessions_per_instance:
                self._publish(instance_id, terminal_id, {
                    "type": "error",
                    "message": "Maximum 3 terminal sessions per instance.",
                })
                return
        runner = self.registry.get(instance_id)
        if runner is None or not getattr(runner, "is_running", False):
            self._publish(instance_id, terminal_id, {
                "type": "error",
                "message": "Sandbox is not running.",
            })
            return
        try:
            exec_id, wrapper = runner.open_terminal()
            raw = self._raw_socket(wrapper)
        except Exception as exc:
            log.warning("terminal open failed instance=%s error=%s", instance_id[:8], exc)
            self._publish(instance_id, terminal_id, {
                "type": "error",
                "message": "Container shell is unavailable.",
            })
            return

        stop_event = threading.Event()
        session = {
            "exec_id": exec_id,
            "wrapper": wrapper,
            "socket": raw,
            "stop": stop_event,
            "runner": runner,
        }
        with self._lock:
            self._sessions[key] = session
        threading.Thread(
            target=self._read,
            args=(instance_id, terminal_id, session),
            daemon=True,
        ).start()
        self._publish(instance_id, terminal_id, {"type": "ready"})

    def _read(self, instance_id: str, terminal_id: str, session: dict) -> None:
        key = self._session_key(instance_id, terminal_id)
        raw = session["socket"]
        try:
            while not session["stop"].is_set():
                if hasattr(raw, "recv"):
                    chunk = raw.recv(4096)
                else:
                    chunk = raw.read(4096)
                if not chunk:
                    break
                self._publish(instance_id, terminal_id, {
                    "type": "output",
                    "data": chunk.decode("utf-8", errors="replace"),
                })
        except Exception:
            pass
        finally:
            with self._lock:
                if self._sessions.get(key) is session:
                    self._sessions.pop(key, None)
            self._publish(instance_id, terminal_id, {"type": "closed"})

    def write(self, instance_id: str, terminal_id: str, data: str) -> None:
        with self._lock:
            session = self._sessions.get(self._session_key(instance_id, terminal_id))
        if session is None:
            return
        raw = session["socket"]
        encoded = data.encode()
        try:
            if hasattr(raw, "sendall"):
                raw.sendall(encoded)
            else:
                raw.write(encoded)
        except Exception:
            self.close(instance_id, terminal_id)

    def resize(self, instance_id: str, terminal_id: str, cols: int, rows: int) -> None:
        with self._lock:
            session = self._sessions.get(self._session_key(instance_id, terminal_id))
        if session is None:
            return
        try:
            session["runner"].client.api.resize_exec(
                session["exec_id"],
                height=max(1, min(500, int(rows))),
                width=max(1, min(500, int(cols))),
            )
        except Exception:
            pass

    def close(self, instance_id: str, terminal_id: str | None = None) -> None:
        with self._lock:
            if terminal_id is None:
                keys = [key for key in self._sessions if key[0] == instance_id]
                sessions = [self._sessions.pop(key) for key in keys]
            else:
                sessions = [self._sessions.pop(self._session_key(instance_id, terminal_id), None)]
        for session in sessions:
            if session is None:
                continue
            session["stop"].set()
            for candidate in (session["socket"], session["wrapper"]):
                try:
                    candidate.close()
                except Exception:
                    pass

    def close_all(self) -> None:
        with self._lock:
            keys = list(self._sessions)
        for instance_id, terminal_id in keys:
            self.close(instance_id, terminal_id)
