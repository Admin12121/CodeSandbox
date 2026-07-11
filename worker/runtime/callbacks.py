from __future__ import annotations

import logging
import threading

import requests

log = logging.getLogger("codesandbox-worker.callbacks")


class CallbackClient:
    def __init__(
        self,
        callback_url: str,
        callback_token: str,
        job_id: str,
        instance_id: str,
    ) -> None:
        self.callback_url = callback_url
        self.callback_token = callback_token
        self.job_id = job_id
        self.instance_id = instance_id
        self._session = requests.Session()
        self._lock = threading.Lock()

    def send(self, event: str, data: dict | None = None) -> dict:
        payload = {
            "job_id": self.job_id,
            "instance_id": self.instance_id,
            "event": event,
            "data": data or {},
        }
        with self._lock:
            response = self._session.post(
                self.callback_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.callback_token}"},
                timeout=15,
            )
            response.raise_for_status()
            body = response.json()
            refreshed = body.get("callback_token")
            if refreshed:
                self.callback_token = str(refreshed)
            return body

    def try_send(self, event: str, data: dict | None = None) -> dict:
        try:
            return self.send(event, data)
        except Exception as exc:
            log.warning(
                "callback failed instance=%s event=%s error=%s",
                self.instance_id[:8],
                event,
                exc,
            )
            return {}
