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
from runtime.docker_client import DockerClientFactory
from runtime.docker_runner import DockerRunner
from runtime.process import RuntimeRegistry
from runtime.registry_client import WorkerRegistryClient
from runtime.terminal import DockerTerminalManager

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [worker] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("codesandbox-worker")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
NATS_USER = os.environ.get("NATS_USER", "")
NATS_PASSWORD = os.environ.get("NATS_PASSWORD", "")
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "http://app:5000")

if "WORKER_ID" in os.environ:
    WORKER_ID = os.environ["WORKER_ID"]
else:
    WORKER_ID = platform.node()
    log.warning(
        "WORKER_ID is not set — falling back to the container hostname (%s), "
        "which is not guaranteed stable across restarts. Set WORKER_ID "
        "explicitly for any multi-worker or production deployment.",
        WORKER_ID,
    )

QUEUE_KEY = f"codesandbox:sandbox-jobs:{WORKER_ID}"
_TERMINAL_CTL_SUBJECT = f"codesandbox.worker.{WORKER_ID}.sandbox.*.terminal.ctl"
_TERMINAL_INPUT_SUBJECT = f"codesandbox.worker.{WORKER_ID}.sandbox.*.terminal.input"
_FS_REQUEST_SUBJECT = f"codesandbox.worker.{WORKER_ID}.sandbox.*.fs.request"
_TOTAL_VCPU = int(os.environ.get("SANDBOX_WORKER_TOTAL_VCPU", "8"))
_TOTAL_RAM_GB = int(os.environ.get("SANDBOX_WORKER_TOTAL_RAM_GB", "16"))
_TOTAL_DISK_GB = int(os.environ.get("SANDBOX_WORKER_TOTAL_DISK_GB", "100"))
_HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("SANDBOX_WORKER_HEARTBEAT_SECONDS", "10"))


def _require_nats_auth_in_production() -> None:
    if os.environ.get("ENVIRONMENT", "development").strip().lower() == "production" and not (
        NATS_USER and NATS_PASSWORD
    ):
        raise RuntimeError(
            "NATS_USER/NATS_PASSWORD are required when ENVIRONMENT=production — "
            "refusing to start the worker unauthenticated against NATS."
        )


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
        self.terminal = DockerTerminalManager(self.registry, self.publish, WORKER_ID)
        self.registry_client = WorkerRegistryClient(CONTROL_PLANE_URL, WORKER_ID)

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
        # codesandbox.worker.<worker_id>.sandbox.<instance_id>.terminal.ctl (etc)
        parts = subject.split(".")
        if len(parts) >= 7 and parts[1] == "worker" and parts[3] == "sandbox":
            return parts[4]
        return None

    async def _terminal_control(self, message) -> None:
        instance_id = self._instance_id(message.subject)
        if not instance_id:
            return
        try:
            body = json.loads(message.data)
        except Exception:
            return
        action = body.get("action")
        terminal_id = str(body.get("terminal_id") or "terminal-1")[:40] or "terminal-1"
        if action == "open":
            threading.Thread(target=self.terminal.open, args=(instance_id, terminal_id), daemon=True).start()
        elif action == "close":
            self.terminal.close(instance_id, terminal_id)
        elif action == "resize":
            self.terminal.resize(
                instance_id,
                terminal_id,
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
        terminal_id = str(body.get("terminal_id") or "terminal-1")[:40] or "terminal-1"
        if isinstance(data, str) and data:
            self.terminal.write(instance_id, terminal_id, data)

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
                    self.nc = await nats.connect(
                        NATS_URL,
                        name="codesandbox-docker-worker",
                        user=NATS_USER or None,
                        password=NATS_PASSWORD or None,
                    )
                    # Worker-scoped subjects (not a global wildcard): this worker
                    # process can only ever receive terminal/fs traffic addressed
                    # to its own worker_id — a structural fix, not just the old
                    # self-filter-via-registry-lookup pattern.
                    await self.nc.subscribe(
                        _TERMINAL_CTL_SUBJECT, cb=self._terminal_control
                    )
                    await self.nc.subscribe(
                        _TERMINAL_INPUT_SUBJECT, cb=self._terminal_input
                    )
                    await self.nc.subscribe(
                        _FS_REQUEST_SUBJECT, cb=self._filesystem
                    )
                    log.info("NATS connected url=%s worker_id=%s", NATS_URL, WORKER_ID)
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

    # Test Launch only: buffered so a `log_contains` success_condition can be
    # checked once the run finishes — a bounded ring buffer, not the full
    # stream, so an interactive/background_run instance without that
    # condition configured pays zero extra memory cost (see the `if` guard).
    _TEST_LOG_BUFFER_MAX_BYTES = 256 * 1024

    def _stream_logs(self, runner: DockerRunner) -> None:
        test_config = runner.policy.get("test_config") or {}
        buffer_logs = str(test_config.get("success_condition") or "") == "log_contains"
        try:
            for chunk in runner.container.logs(stream=True, follow=True, stdout=True, stderr=True):
                if runner.external_control.is_set():
                    break
                text = chunk.decode("utf-8", errors="replace")
                if buffer_logs:
                    runner.test_log_buffer = (runner.test_log_buffer + text)[-self._TEST_LOG_BUFFER_MAX_BYTES:]
                self.publish(
                    f"codesandbox.sandbox.events.{runner.instance_id}",
                    {
                        "type": "log",
                        "instance_id": runner.instance_id,
                        "data": text,
                    },
                )
        except Exception:
            pass

    def _evaluate_test_success(
        self, runner: DockerRunner, exit_code, artifacts: list[dict]
    ) -> tuple[bool | None, str | None]:
        """Mode-specific Test Launch pass/fail, evaluated where the real
        signal (exit code, produced artifacts, buffered logs) actually lives
        — not "did the container start" (see docs/plan.md Phase 10.4).
        Returns (None, None) for a normal (non-test) run, where the control
        plane's own "started implies passed" fallback for terminal_only/lab_ui
        still applies."""
        test_config = runner.policy.get("test_config") or {}
        success_condition = str(test_config.get("success_condition") or "").strip()
        if not success_condition:
            return None, None
        if success_condition == "exit_zero":
            ok = exit_code == 0
            return ok, (None if ok else f"Process exited with code {exit_code}.")
        if success_condition == "artifact_exists":
            required = [str(p).lstrip("/") for p in test_config.get("required_artifacts") or []]
            produced = {str(a.get("name") or "").lstrip("/") for a in artifacts}
            missing = [p for p in required if p not in produced]
            return (not missing), (
                None if not missing else f"Missing required artifacts: {', '.join(missing)}"
            )
        if success_condition == "log_contains":
            patterns = [str(p) for p in test_config.get("log_contains") or []]
            buffer = getattr(runner, "test_log_buffer", "")
            missing = [p for p in patterns if p not in buffer]
            return (not missing), (
                None if not missing else f"Log output did not contain: {', '.join(missing)}"
            )
        if success_condition == "healthcheck":
            # No worker-side GUI/emulator proxy exists yet (Phase 10.6/10.7) —
            # report an honest failure with the real reason instead of a
            # fabricated pass.
            return False, "Healthcheck-based test success requires Desktop GUI/Android UI real connection support, which is not yet available."
        return None, f"Unknown success_condition: {success_condition!r}"

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
        artifacts: list[dict] = []
        try:
            artifacts = runner.collect_artifacts()
            self._publish_artifacts(callback, artifacts)
        except Exception as exc:
            log.warning("artifact collection failed instance=%s error=%s", runner.instance_id[:8], exc)
        runner.cleanup()
        payload = {"reason": reason, "exit_code": exit_code}
        test_success, test_reason = self._evaluate_test_success(runner, exit_code, artifacts)
        if test_success is not None:
            payload["test_success"] = test_success
            payload["test_reason"] = test_reason
        callback.try_send(event, payload)
        self._publish_event(runner.instance_id, event, payload)

    def _monitor_loop(self, runner: DockerRunner, callback: CallbackClient, instance_id: str) -> None:
        """Poll a running container until it exits, times out, breaches its
        disk quota, or is told to stop — shared by freshly-started jobs and
        containers reattached at worker boot (see _reattach_running)."""
        timeout_at = runner.started_monotonic + int(runner.policy["max_timeout_sec"])
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
                    "worker_id": WORKER_ID,
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
            self._monitor_loop(runner, callback, instance_id)
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

    def _current_load(self) -> tuple[int, int, int]:
        runners = [runner for _, runner in self.registry.all()]
        used_vcpu = sum(int(runner.policy.get("vcpu") or 0) for runner in runners)
        used_ram_gb = sum(int(runner.policy.get("ram_gb") or 0) for runner in runners)
        return used_vcpu, used_ram_gb, len(runners)

    def _reattach_running(self) -> None:
        """Boot-time registry rebuild (Phase 5): discover containers this
        worker_id created (by Docker label) and cross-reference them against
        what the control plane's DB thinks is still running, so a worker
        restart doesn't strand terminal/filesystem attachment or leave a
        container's lifecycle unmonitored."""
        try:
            docker_client = DockerClientFactory.create()
            containers = docker_client.containers.list(
                all=True, filters={"label": f"codesandbox.worker_id={WORKER_ID}"}
            )
        except Exception as exc:
            log.warning("container discovery failed error=%s", exc)
            return
        if not containers:
            return

        candidates = {
            str(item["instance_id"]): item for item in self.registry_client.list_instances()
        }
        for container in containers:
            instance_id = container.labels.get("codesandbox.instance_id")
            if not instance_id:
                continue
            candidate = candidates.get(instance_id)
            if candidate is None:
                log.warning(
                    "orphaned container found instance=%s container=%s — no matching "
                    "live DB record for this worker; leaving it for manual/reconciler cleanup",
                    instance_id[:8], container.name,
                )
                continue
            job = {
                "instance_id": instance_id,
                "job_id": candidate["job_id"],
                "runtime_id": candidate["runtime_id"],
                "runtime_provider": candidate.get("runtime_provider") or "docker",
                "workspace_volume_id": candidate.get("workspace_volume_id"),
                "runtime_policy": candidate.get("runtime_policy") or {},
                "callback_token": candidate["callback_token"],
            }
            try:
                runner = DockerRunner.recover(job, self.publish, self.store)
            except Exception as exc:
                log.warning("reattach failed instance=%s error=%s", instance_id[:8], exc)
                continue
            callback = self._callback(job)
            if not runner.is_running:
                runner.container.reload()
                exit_code = runner.container.attrs.get("State", {}).get("ExitCode")
                self._finish(runner, callback, "stopped", "process_exited_while_worker_offline", exit_code)
                continue
            self.registry.register(instance_id, runner)
            self._publish_event(instance_id, "started", {"reason": "worker_restart_reattach"})
            log.info("reattached instance=%s runtime_id=%s", instance_id[:8], job["runtime_id"][:12])
            threading.Thread(target=self._stream_logs, args=(runner,), daemon=True).start()
            threading.Thread(
                target=self._monitor_loop, args=(runner, callback, instance_id), daemon=True
            ).start()

    def _register_loop(self) -> None:
        capabilities = {"runtime_class": ["container", "tool_job"]}
        while self.running:
            ok = self.registry_client.register(
                hostname=platform.node(),
                capabilities=capabilities,
                total_vcpu=_TOTAL_VCPU,
                total_ram_gb=_TOTAL_RAM_GB,
                total_disk_gb=_TOTAL_DISK_GB,
            )
            if ok:
                log.info("registered with control plane worker_id=%s", WORKER_ID)
                return
            time.sleep(5)

    def _heartbeat_loop(self) -> None:
        while self.running:
            used_vcpu, used_ram_gb, running_instances = self._current_load()
            self.registry_client.heartbeat(
                used_vcpu=used_vcpu,
                used_ram_gb=used_ram_gb,
                running_instances=running_instances,
            )
            time.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    def run(self) -> None:
        def shutdown(_signal, _frame) -> None:
            self.running = False

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

        threading.Thread(target=self._start_nats, daemon=True).start()
        self._register_loop()
        self._reattach_running()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        client = redis.from_url(REDIS_URL, decode_responses=True)
        log.info("worker starting redis=%s nats=%s worker_id=%s", REDIS_URL, NATS_URL, WORKER_ID)
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
    DockerClientFactory.validate_production_safety()
    _require_nats_auth_in_production()
    WorkerApp().run()


if __name__ == "__main__":
    main()
