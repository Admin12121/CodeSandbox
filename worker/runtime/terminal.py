from __future__ import annotations

import logging
import threading

log = logging.getLogger("codesandbox-worker.terminal")


class DockerTerminalManager:
    def __init__(self, registry, publish, worker_id: str) -> None:
        self.registry = registry
        self.publish = publish
        self.worker_id = worker_id
        self._sessions: dict[str, dict] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _raw_socket(wrapper):
        return getattr(wrapper, "_sock", wrapper)

    def _subject(self, instance_id: str) -> str:
        return f"codesandbox.worker.{self.worker_id}.sandbox.{instance_id}.terminal.output"

    def open(self, instance_id: str) -> None:
        with self._lock:
            if instance_id in self._sessions:
                return
        runner = self.registry.get(instance_id)
        if runner is None or not getattr(runner, "is_running", False):
            self.publish(self._subject(instance_id), {
                "type": "error",
                "message": "Sandbox is not running.",
            })
            return
        try:
            exec_id, wrapper = runner.open_terminal()
            raw = self._raw_socket(wrapper)
        except Exception as exc:
            log.warning("terminal open failed instance=%s error=%s", instance_id[:8], exc)
            self.publish(self._subject(instance_id), {
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
            self._sessions[instance_id] = session
        threading.Thread(
            target=self._read,
            args=(instance_id, session),
            daemon=True,
        ).start()
        self.publish(self._subject(instance_id), {"type": "ready"})

    def _read(self, instance_id: str, session: dict) -> None:
        raw = session["socket"]
        try:
            while not session["stop"].is_set():
                if hasattr(raw, "recv"):
                    chunk = raw.recv(4096)
                else:
                    chunk = raw.read(4096)
                if not chunk:
                    break
                self.publish(self._subject(instance_id), {
                    "type": "output",
                    "data": chunk.decode("utf-8", errors="replace"),
                })
        except Exception:
            pass
        finally:
            with self._lock:
                if self._sessions.get(instance_id) is session:
                    self._sessions.pop(instance_id, None)
            self.publish(self._subject(instance_id), {"type": "closed"})

    def write(self, instance_id: str, data: str) -> None:
        with self._lock:
            session = self._sessions.get(instance_id)
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
            self.close(instance_id)

    def resize(self, instance_id: str, cols: int, rows: int) -> None:
        with self._lock:
            session = self._sessions.get(instance_id)
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

    def close(self, instance_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(instance_id, None)
        if session is None:
            return
        session["stop"].set()
        for candidate in (session["socket"], session["wrapper"]):
            try:
                candidate.close()
            except Exception:
                pass

    def close_all(self) -> None:
        with self._lock:
            instance_ids = list(self._sessions)
        for instance_id in instance_ids:
            self.close(instance_id)
