from __future__ import annotations

import posixpath


def safe_artifact_name(value: str) -> str:
    name = posixpath.normpath("/" + str(value or "")).lstrip("/")
    if not name or name.startswith("../") or "\x00" in name:
        raise ValueError("Invalid artifact name.")
    return name[:500]
