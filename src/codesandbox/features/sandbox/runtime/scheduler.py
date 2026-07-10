from __future__ import annotations

from .drivers.android import AndroidRuntimeDriver
from .drivers.base import RuntimeDriver, UnsupportedRuntimeError
from .drivers.docker import DockerRuntimeDriver
from .drivers.firecracker import FirecrackerRuntimeDriver
from .drivers.qemu import QemuRuntimeDriver


_DRIVERS: dict[str, type[RuntimeDriver]] = {
    "container": DockerRuntimeDriver,
    "tool_job": DockerRuntimeDriver,
    "microvm": FirecrackerRuntimeDriver,
    "firecracker_microvm": FirecrackerRuntimeDriver,
    "fullvm": QemuRuntimeDriver,
    "qemu_vm": QemuRuntimeDriver,
    "android": AndroidRuntimeDriver,
    "android_emulator": AndroidRuntimeDriver,
}


def get_runtime_driver(runtime_class: str) -> RuntimeDriver:
    driver = _DRIVERS.get(runtime_class)
    if driver is None:
        raise UnsupportedRuntimeError(f"Unknown runtime class: {runtime_class}")
    return driver()
