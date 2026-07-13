from __future__ import annotations

import os
import re
from docker.errors import APIError, ImageNotFound


_DOCKER_HUB_ALIASES = {
    "docker.io",
    "index.docker.io",
    "registry-1.docker.io",
}
_SUPPORTED_PULL_POLICIES = {"always", "if_not_present", "never"}
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+.-]*:[0-9a-fA-F]{32,}$")



def normalize_image_reference(reference: str) -> str:
    """Return one canonical registry/repository[:tag|@digest] reference.

    Examples:
      ubuntu:22.04 -> docker.io/library/ubuntu:22.04
      index.docker.io/library/ubuntu:22.04 -> docker.io/library/ubuntu:22.04
      ghcr.io/acme/tool -> ghcr.io/acme/tool:latest
    """

    value = str(reference or "").strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError("Invalid Docker image reference.")

    digest = ""
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

    repository_path = "/".join(part.lower() for part in path_parts)
    return f"{registry}/{repository_path}{suffix}"





def _normalize_registry(value: str) -> str:
    registry = value.strip().lower().removeprefix("https://").removeprefix("http://")
    registry = registry.rstrip("/")
    if registry in _DOCKER_HUB_ALIASES or registry == "index.docker.io/v1":
        return "docker.io"
    return registry


def registry_auth_for(reference: str) -> dict[str, str] | None:
    username = os.environ.get("SANDBOX_REGISTRY_USERNAME", "").strip()
    password = os.environ.get("SANDBOX_REGISTRY_PASSWORD", "")
    server = os.environ.get("SANDBOX_REGISTRY_SERVER", "").strip()

    if not username and not password and not server:
        return None
    if not username or not password or not server:
        raise ValueError(
            "SANDBOX_REGISTRY_SERVER, SANDBOX_REGISTRY_USERNAME and "
            "SANDBOX_REGISTRY_PASSWORD must be configured together."
        )

    image_registry = normalize_image_reference(reference).split("/", 1)[0]
    configured_registry = _normalize_registry(server)
    if image_registry != configured_registry:
        return None

    server_address = (
        "https://index.docker.io/v1/"
        if configured_registry == "docker.io"
        else server
    )
    auth = {
        "username": username,
        "password": password,
        "serveraddress": server_address,
    }
    email = os.environ.get("SANDBOX_REGISTRY_EMAIL", "").strip()
    if email:
        auth["email"] = email
    return auth


def _local_lookup_candidates(original: str, normalized: str) -> tuple[str, ...]:
    candidates = [original, normalized]
    if normalized.startswith("docker.io/"):
        short = normalized.removeprefix("docker.io/")
        candidates.append(short)
        if short.startswith("library/"):
            candidates.append(short.removeprefix("library/"))
    return tuple(dict.fromkeys(value for value in candidates if value))


def _get_local_image(client, original: str, normalized: str):
    last_error: Exception | None = None
    for candidate in _local_lookup_candidates(original, normalized):
        try:
            return client.images.get(candidate)
        except ImageNotFound as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ImageNotFound(normalized)


def ensure_image(client, reference: str, *, pull_policy: str | None = None):
    original = str(reference or "").strip()
    normalized = normalize_image_reference(original)
    # Runtime images receive their pull policy from the admin-authored
    # template policy. Internal callers that omit it use the safe, predictable
    # infrastructure default and never consult a worker-wide image policy.
    policy = pull_policy or "if_not_present"
    if policy not in _SUPPORTED_PULL_POLICIES:
        raise ValueError(f"Unsupported image pull policy: {policy}.")

    if policy == "never":
        try:
            return _get_local_image(client, original, normalized)
        except ImageNotFound as exc:
            raise RuntimeError(
                f"Required sandbox image is not cached and pull policy is 'never': {normalized}"
            ) from exc

    if policy == "if_not_present":
        try:
            return _get_local_image(client, original, normalized)
        except ImageNotFound:
            pass

    try:
        auth_config = registry_auth_for(normalized)
        if auth_config is None:
            return client.images.pull(normalized)
        return client.images.pull(normalized, auth_config=auth_config)
    except APIError as exc:
        raise RuntimeError(f"Could not pull sandbox image {normalized}: {exc}") from exc
