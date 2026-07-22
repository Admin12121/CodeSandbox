from __future__ import annotations

import time


def _blkio_total(stats: dict, operation: str) -> int:
    rows = stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []
    return sum(
        int(row.get("value") or 0)
        for row in rows
        if str(row.get("op") or "").lower() == operation
    )


class DockerMetrics:
    def __init__(self, runner) -> None:
        self.runner = runner
        self._disk_checked_at = 0.0
        self._disk_bytes = 0

    def snapshot(self) -> dict:
        stats = self.runner.container.stats(stream=False)
        cpu = stats.get("cpu_stats", {})
        previous_cpu = stats.get("precpu_stats", {})
        cpu_delta = (
            int(cpu.get("cpu_usage", {}).get("total_usage") or 0)
            - int(previous_cpu.get("cpu_usage", {}).get("total_usage") or 0)
        )
        system_delta = (
            int(cpu.get("system_cpu_usage") or 0)
            - int(previous_cpu.get("system_cpu_usage") or 0)
        )
        online_cpus = int(cpu.get("online_cpus") or len(
            cpu.get("cpu_usage", {}).get("percpu_usage") or []
        ) or 1)
        cpu_percent = (
            (cpu_delta / system_delta) * online_cpus * 100
            if cpu_delta > 0 and system_delta > 0
            else 0.0
        )

        memory = stats.get("memory_stats", {})
        memory_stats = memory.get("stats", {})
        cache = int(memory_stats.get("inactive_file") or memory_stats.get("cache") or 0)
        memory_used = max(0, int(memory.get("usage") or 0) - cache)
        memory_limit = int(memory.get("limit") or self.runner.policy["ram_gb"] * 1024**3)

        network_rx = network_tx = 0
        for interface in (stats.get("networks") or {}).values():
            network_rx += int(interface.get("rx_bytes") or 0)
            network_tx += int(interface.get("tx_bytes") or 0)

        now = time.monotonic()
        if now - self._disk_checked_at >= 5:
            result = self.runner.container.exec_run(
                ["du", "-sk", self.runner.policy["working_dir"]]
            )
            if result.exit_code == 0:
                try:
                    self._disk_bytes = int(result.output.split()[0]) * 1024
                except (ValueError, IndexError):
                    pass
            self._disk_checked_at = now
        disk_limit = int(self.runner.policy["disk_gb"]) * 1024**3

        return {
            "type": "metrics",
            "instance_id": self.runner.instance_id,
            "ts": int(time.time()),
            "cpu_pct": round(cpu_percent, 2),
            "mem_mb": round(memory_used / 1024**2, 2),
            "memory_used_bytes": memory_used,
            "memory_limit_bytes": memory_limit,
            "net_rx_kb": round(network_rx / 1024, 2),
            "net_tx_kb": round(network_tx / 1024, 2),
            "network_rx_bytes": network_rx,
            "network_tx_bytes": network_tx,
            "block_read_bytes": _blkio_total(stats, "read"),
            "block_write_bytes": _blkio_total(stats, "write"),
            "disk_used_bytes": self._disk_bytes,
            "disk_limit_bytes": disk_limit,
            "disk_pct": round((self._disk_bytes / disk_limit) * 100, 2) if disk_limit else 0,
            "uptime_sec": int(now - self.runner.started_monotonic),
        }
