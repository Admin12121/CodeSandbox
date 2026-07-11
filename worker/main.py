from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import platform
import signal
import threading
import time

import nats
import redis

from runtime.artifacts import ObjectStore
from runtime.callbacks import CallbackClient
from runtime.docker_runner import DockerRunner
from runtime.process import RuntimeRegistry
from runtime.terminal import DockerTerminalManager

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [worker] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("codesandbox-worker")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "http://app:5000")
QUEUE_KEY = "codesandbox:sandbox-jobs"


def verify_job(job: dict) -> None:
    signature = str(job.get("job_signature") or "")
    signing_key = os.environ.get("SANDBOX_JOB_SIGNING_KEY", "")
    if not signing_key:
        raise ValueError("SANDBOX_JOB_SIGNING_KEY is required.")
    unsigned = {key: value for key, value in job.items() if key != "job_signature"}
    encoded = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    expected = hmac.new(signing_key.encode(), encoded, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid job signature.")
    issued_at = int(job.get("issued_at") or 0)
    max_age = int(os.environ.get("SANDBOX_JOB_MAX_AGE_SECONDS", "300"))
    now = int(time.time())
    if issued_at > now + 30 or issued_at < now - max_age:
        raise ValueError("Runtime job has expired.")
    if str(job.get("action") or "") not in {"start", "stop", "kill", "reconcile"}:
        raise ValueError("Unknown runtime action.")


class WorkerApp:
    def __init__(self) -> None:
        self.registry = RuntimeRegistry()
        self.running = True
        self.loop: asyncio.AbstractEventLoop | None = None
        self.nc = None
        self._store = None
        self._store_lock = threading.Lock()
        self.terminal = DockerTerminalManager(self.registry, self.publish)

    @property
    def store(self) -> ObjectStore:
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    self._store = ObjectStore()
        return self._store

    def publish(self, subject: str, payload: dict) -> None:
        if self.loop is None or self.nc is None or self.nc.is_closed:
            return
        future = asyncio.run_coroutine_threadsafe(
            self.nc.publish(subject, json.dumps(payload, separators=(",", ":")).encode()),
            self.loop,
        )
        try:
            future.result(timeout=2)
        except Exception:
            pass

    @staticmethod
    def _instance_id(subject: str) -> str | None:
        parts = subject.split(".")
        return parts[3] if len(parts) >= 5 else None

    async def _terminal_control(self, message) -> None:
        instance_id = self._instance_id(message.subject)
        if not instance_id:
            return
        try:
            body = json.loads(message.data)
        except Exception:
            return
        action = body.get("action")
        if action == "open":
            threading.Thread(target=self.terminal.open, args=(instance_id,), daemon=True).start()
        elif action == "close":
            self.terminal.close(instance_id)
        elif action == "resize":
            self.terminal.resize(
                instance_id,
                int(body.get("cols") or 80),
                int(body.get("rows") or 24),
            )

    async def _terminal_input(self, message) -> None:
        instance_id = self._instance_id(message.subject)
        if not instance_id:
            return
        try:
            body = json.loads(message.data)
        except Exception:
            return
        data = body.get("data")
        if isinstance(data, str) and data:
            self.terminal.write(instance_id, data)

    async def _filesystem(self, message) -> None:
        instance_id = self._instance_id(message.subject)
        runner = self.registry.get(instance_id or "") if instance_id else None
        if runner is None or not getattr(runner, "is_running", False):
            result = {"ok": False, "error": "Sandbox is not running."}
        else:
            try:
                result = runner.filesystem.handle(json.loads(message.data))
            except Exception:
                result = {"ok": False, "error": "Invalid filesystem request."}
        if message.reply:
            await message.respond(json.dumps(result, separators=(",", ":")).encode())

    def _start_nats(self) -> None:
        async def run() -> None:
            while self.running:
                try:
                    self.nc = await nats.connect(NATS_URL, name="codesandbox-docker-worker")
                    await self.nc.subscribe(
                        "codesandbox.sandbox.terminal.*.ctl", cb=self._terminal_control
                    )
                    await self.nc.subscribe(
                        "codesandbox.sandbox.terminal.*.input", cb=self._terminal_input
                    )
                    await self.nc.subscribe(
                        "codesandbox.sandbox.fs.*.request", cb=self._filesystem
                    )
                    log.info("NATS connected url=%s", NATS_URL)
                    return
                except Exception as exc:
                    log.warning("NATS connection failed error=%s", exc)
                    await asyncio.sleep(3)

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(run())
        self.loop.run_forever()

    @staticmethod
    def _callback(job: dict) -> CallbackClient:
        return CallbackClient(
            str(job.get("callback_url") or CONTROL_PLANE_URL + "/internal/worker/callback"),
            str(job["callback_token"]),
            str(job["job_id"]),
            str(job["instance_id"]),
        )

    def _publish_event(self, instance_id: str, event: str, data: dict | None = None) -> None:
        self.publish(
            f"codesandbox.sandbox.events.{instance_id}",
            {"type": "event", "instance_id": instance_id, "event": event, "data": data or {}},
        )

    def _publish_artifacts(self, callback: CallbackClient, artifacts: list[dict]) -> None:
        for artifact in artifacts:
            callback.try_send("artifact_ready", artifact)
            self._publish_event(callback.instance_id, "artifact_ready", artifact)

    def _stream_logs(self, runner: DockerRunner) -> None:
        try:
            for chunk in runner.container.logs(stream=True, follow=True, stdout=True, stderr=True):
                if runner.external_control.is_set():
                    break
                self.publish(
                    f"codesandbox.sandbox.events.{runner.instance_id}",
                    {
                        "type": "log",
                        "instance_id": runner.instance_id,
                        "data": chunk.decode("utf-8", errors="replace"),
                    },
                )
        except Exception:
            pass

    def _finish(
        self,
        runner: DockerRunner,
        callback: CallbackClient,
        event: str,
        reason: str,
        exit_code=None,
    ) -> None:
        callback.try_send("cleanup_started", {"reason": reason})
        self.terminal.close(runner.instance_id)
        try:
            self._publish_artifacts(callback, runner.collect_artifacts())
        except Exception as exc:
            log.warning("artifact collection failed instance=%s error=%s", runner.instance_id[:8], exc)
        runner.cleanup()
        callback.try_send(event, {"reason": reason, "exit_code": exit_code})
        self._publish_event(runner.instance_id, event, {"reason": reason, "exit_code": exit_code})

    def _start_job(self, job: dict) -> None:
        callback = self._callback(job)
        instance_id = str(job["instance_id"])
        runner = None
        try:
            runner = DockerRunner(job, self.publish, self.store)
            self.registry.register(instance_id, runner)
            self._publish_event(instance_id, "provisioning")
            with runner._operation_lock:
                runner.prepare()
                if runner.external_control.is_set():
                    return
                runtime_data = runner.start()
            callback.send("started", runtime_data)
            self._publish_event(instance_id, "started", runtime_data)
            threading.Thread(target=self._stream_logs, args=(runner,), daemon=True).start()

            timeout_at = time.monotonic() + int(runner.policy["max_timeout_sec"])
            next_heartbeat = 0.0
            while not runner.external_control.wait(1):
                if not runner.is_running:
                    runner.container.reload()
                    exit_code = runner.container.attrs.get("State", {}).get("ExitCode")
                    self._finish(runner, callback, "stopped", "process_exited", exit_code)
                    return
                metrics = runner.stats()
                self.publish(f"codesandbox.sandbox.metrics.{instance_id}", metrics)
                if metrics["disk_used_bytes"] > metrics["disk_limit_bytes"]:
                    runner.kill()
                    self._finish(runner, callback, "failed", "workspace_limit_exceeded", 137)
                    return
                now = time.monotonic()
                if now >= timeout_at:
                    result = runner.stop()
                    self._finish(runner, callback, "expired", "timeout", result.get("exit_code"))
                    return
                if now >= next_heartbeat:
                    directive = callback.try_send("heartbeat", {
                        "runtime_id": runner.container.id,
                        "worker_id": os.environ.get("WORKER_ID", platform.node()),
                    })
                    next_heartbeat = now + 10
                    if directive.get("command") == "stop":
                        result = runner.stop()
                        self._finish(
                            runner,
                            callback,
                            "stopped",
                            str(directive.get("reason") or "control_plane_stop"),
                            result.get("exit_code"),
                        )
                        return
        except Exception as exc:
            log.exception("start failed instance=%s", instance_id[:8])
            if runner is not None:
                try:
                    runner.cleanup()
                except Exception:
                    pass
            callback.try_send("failed", {"error": str(exc), "reason": "start_failed"})
            self._publish_event(instance_id, "failed", {"error": str(exc)})
        finally:
            if runner is not None and not runner.external_control.is_set():
                self.registry.remove(instance_id, runner)

    def _control_job(self, job: dict) -> None:
        callback = self._callback(job)
        instance_id = str(job["instance_id"])
        action = str(job["action"])
        runner = self.registry.get(instance_id)
        try:
            if runner is None:
                runner = DockerRunner.recover(job, self.publish, self.store)
            runner.external_control.set()
            callback.try_send("cleanup_started", {"reason": job.get("reason")})
            self.terminal.close(instance_id)
            result = runner.kill() if action in {"kill", "reconcile"} else runner.stop()
            try:
                self._publish_artifacts(callback, runner.collect_artifacts())
            except Exception as exc:
                log.warning("artifact collection failed instance=%s error=%s", instance_id[:8], exc)
            runner.cleanup()
            if action == "kill":
                event = "killed"
            elif action == "reconcile":
                event = str(job.get("final_status") or "failed")
                if event not in {"stopped", "expired", "failed", "killed"}:
                    event = "failed"
            else:
                event = "stopped"
            callback.try_send(event, {
                "reason": str(job.get("reason") or action),
                "exit_code": result.get("exit_code"),
            })
            self._publish_event(instance_id, event, {"reason": job.get("reason")})
        except Exception as exc:
            log.exception("control action failed instance=%s action=%s", instance_id[:8], action)
            callback.try_send("failed", {"error": str(exc), "reason": f"{action}_failed"})
        finally:
            if runner is not None:
                self.registry.remove(instance_id, runner)

    def process_job(self, raw: str) -> None:
        try:
            job = json.loads(raw)
            if not isinstance(job, dict):
                raise ValueError("Job must be an object.")
            verify_job(job)
        except Exception as exc:
            log.warning("rejected runtime job error=%s", exc)
            return
        target = self._start_job if job["action"] == "start" else self._control_job
        threading.Thread(target=target, args=(job,), daemon=True).start()

    def run(self) -> None:
        threading.Thread(target=self._start_nats, daemon=True).start()
        client = redis.from_url(REDIS_URL, decode_responses=True)

        def shutdown(_signal, _frame) -> None:
            self.running = False

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        log.info("worker starting redis=%s nats=%s", REDIS_URL, NATS_URL)
        while self.running:
            try:
                result = client.brpop(QUEUE_KEY, timeout=2)
                if result:
                    self.process_job(result[1])
            except redis.exceptions.ConnectionError as exc:
                log.warning("Redis unavailable error=%s", exc)
                time.sleep(3)
            except Exception:
                log.exception("worker loop failed")
                time.sleep(1)
        self.terminal.close_all()
        for _, runner in self.registry.all():
            try:
                runner.kill()
                runner.cleanup()
            except Exception:
                pass
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)


def main() -> None:
    WorkerApp().run()


if __name__ == "__main__":
    main()
