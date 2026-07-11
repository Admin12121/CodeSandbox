from __future__ import annotations

import logging
import signal
import time

from codesandbox.config import get_settings
from codesandbox.infrastructure.nexorm import configure_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("codesandbox-reconciler")


def main() -> None:
    settings = get_settings()
    configure_db(settings.database_url)
    import codesandbox.models  # noqa: F401
    from codesandbox.features.sandbox.reconciliation import reconcile_stuck_instances

    running = True

    def stop(_signal, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        try:
            result = reconcile_stuck_instances()
            if result["queued"] or result["finalized"]:
                log.info("reconciliation result=%s", result)
        except Exception:
            log.exception("reconciliation pass failed")
        for _ in range(max(1, settings.sandbox_reconcile_interval_seconds)):
            if not running:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
