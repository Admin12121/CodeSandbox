from __future__ import annotations

import re


_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+.-]*:[0-9a-fA-F]{32,}$")

_DOCKER_HUB_ALIASES = {
    "docker.io",
    "index.docker.io",
    "registry-1.docker.io",
}


def normalize_image_reference(reference: str) -> str:
    value = str(reference or "").strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError("Invalid Docker image reference.")

    if "@" in value:
        repository, digest = value.rsplit("@", 1)
        if not repository or not _DIGEST_RE.fullmatch(digest):
            raise ValueError("Invalid Docker image digest reference.")
        suffix = f"@{digest.lower()}"
    else:
        repository = value
        last_component = repository.rsplit("/", 1)[-1]
        if ":" in last_component:
            repository, tag = repository.rsplit(":", 1)
            if not _TAG_RE.fullmatch(tag):
                raise ValueError("Invalid Docker image tag.")
        else:
            tag = "latest"
        suffix = f":{tag}"

    parts = repository.split("/")
    first = parts[0]
    has_explicit_registry = (
        len(parts) > 1
        and ("." in first or ":" in first or first.lower() == "localhost")
    )
    if has_explicit_registry:
        registry = first.lower()
        path_parts = parts[1:]
    else:
        registry = "docker.io"
        path_parts = parts

    if registry in _DOCKER_HUB_ALIASES:
        registry = "docker.io"
        if len(path_parts) == 1:
            path_parts.insert(0, "library")

    if not path_parts or any(not part or part in {".", ".."} for part in path_parts):
        raise ValueError("Invalid Docker image repository.")

    return f"{registry}/{'/'.join(part.lower() for part in path_parts)}{suffix}"

