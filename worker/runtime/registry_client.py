from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

import requests

log = logging.getLogger("codesandbox-worker.registry")


def _sign(payload: dict) -> str:
    signing_key = os.environ.get("SANDBOX_JOB_SIGNING_KEY", "")
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    encoded = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hmac.new(signing_key.encode(), encoded, hashlib.sha256).hexdigest()


class WorkerRegistryClient:
    """Registers this worker process with the control plane and sends
    periodic fleet heartbeats — the persistent counterpart to the in-memory
    RuntimeRegistry, so a worker restart doesn't strand its instances (see
    docs/runtime-architecture.md)."""

    def __init__(self, control_plane_url: str, worker_id: str) -> None:
        self.base_url = control_plane_url.rstrip("/")
        self.worker_id = worker_id
        self._session = requests.Session()

    def _post(self, path: str, payload: dict) -> dict | None:
        body = dict(payload)
        body["worker_id"] = self.worker_id
        body["issued_at"] = int(time.time())
        body["signature"] = _sign(body)
        try:
            response = self._session.post(f"{self.base_url}{path}", json=body, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            log.warning("worker registry call failed path=%s error=%s", path, exc)
            return None

    def register(
        self,
        *,
        hostname: str,
        capabilities: dict,
        total_vcpu: int,
        total_ram_gb: int,
        total_disk_gb: int,
    ) -> bool:
        result = self._post(
            "/internal/worker/register",
            {
                "hostname": hostname,
                "capabilities_json": json.dumps(capabilities, separators=(",", ":")),
                "total_vcpu": total_vcpu,
                "total_ram_gb": total_ram_gb,
                "total_disk_gb": total_disk_gb,
            },
        )
        return bool(result and result.get("ok"))

    def heartbeat(
        self,
        *,
        used_vcpu: int,
        used_ram_gb: int,
        used_disk_gb: int,
        running_instances: int,
    ) -> bool:
        result = self._post(
            "/internal/worker/heartbeat",
            {
                "used_vcpu": used_vcpu,
                "used_ram_gb": used_ram_gb,
                "used_disk_gb": used_disk_gb,
                "running_instances": running_instances,
            },
        )
        return bool(result and result.get("ok"))

    def list_instances(self) -> list[dict]:
        """Everything the control plane's DB thinks this worker_id still
        owns (non-terminal status) — used at boot to rebuild the in-memory
        registry against whatever containers are still actually running."""
        result = self._post("/internal/worker/instances", {})
        if not result or not result.get("ok"):
            return []
        return list(result.get("instances") or [])
