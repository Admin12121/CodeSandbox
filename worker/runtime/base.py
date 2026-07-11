from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RuntimeRunner(ABC):
    @abstractmethod
    def prepare(self) -> None: ...

    @abstractmethod
    def start(self) -> dict[str, Any]: ...

    @abstractmethod
    def stop(self) -> dict[str, Any]: ...

    @abstractmethod
    def kill(self) -> dict[str, Any]: ...

    @abstractmethod
    def exec(self, command: list[str]) -> tuple[int, bytes]: ...

    @abstractmethod
    def open_terminal(self): ...

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...

    @abstractmethod
    def collect_artifacts(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def cleanup(self) -> None: ...
