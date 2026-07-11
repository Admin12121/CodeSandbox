from __future__ import annotations

import os

import docker


class DockerBackendError(RuntimeError):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class DockerClientFactory:
    """Builds the Docker SDK client the worker uses to run sandboxes.

    Backend is selected via SANDBOX_DOCKER_BACKEND:

    - local_socket: connects to whatever Docker daemon `docker.from_env()`
      resolves to (typically the bind-mounted host socket in dev). This is
      development-only — refused at boot in production unless the operator
      explicitly opts in via ALLOW_UNSAFE_DOCKER_SOCKET=true, since it grants
      the worker container root-equivalent control of the host.
    - remote_tls: connects to a dedicated Docker host over mutual TLS
      (DOCKER_HOST/DOCKER_TLS_VERIFY/DOCKER_CERT_PATH), so the worker never
      needs the host's own socket mounted into it.

    podman/containerd/Firecracker are not implemented — see
    docs/runtime-architecture.md for the planned isolation roadmap. Adding
    one means adding a branch here and a matching RuntimeRunner, not
    touching any sandbox/template logic.
    """

    LOCAL_SOCKET = "local_socket"
    REMOTE_TLS = "remote_tls"
    SUPPORTED = (LOCAL_SOCKET, REMOTE_TLS)

    @classmethod
    def backend_name(cls) -> str:
        return os.environ.get("SANDBOX_DOCKER_BACKEND", cls.LOCAL_SOCKET).strip().lower()

    @classmethod
    def validate_production_safety(cls) -> None:
        """Refuse to boot with an unsafe backend in production.

        Called once at worker startup, before any job is processed — not on
        every client creation, so this is a hard boot-time gate rather than
        a per-request check.
        """
        environment = os.environ.get("ENVIRONMENT", "development").strip().lower()
        backend = cls.backend_name()
        if (
            environment == "production"
            and backend == cls.LOCAL_SOCKET
            and not _env_bool("ALLOW_UNSAFE_DOCKER_SOCKET", False)
        ):
            raise DockerBackendError(
                "SANDBOX_DOCKER_BACKEND=local_socket (a bind-mounted Docker "
                "socket) is development-only: it gives this container root-"
                "equivalent control of the host. Refusing to start in "
                "production. Use SANDBOX_DOCKER_BACKEND=remote_tls with a "
                "dedicated Docker host, or set ALLOW_UNSAFE_DOCKER_SOCKET=true "
                "if you have deliberately accepted this risk."
            )

    @classmethod
    def create(cls) -> "docker.DockerClient":
        backend = cls.backend_name()
        if backend == cls.LOCAL_SOCKET:
            return docker.from_env(timeout=60)
        if backend == cls.REMOTE_TLS:
            return cls._create_remote_tls()
        raise DockerBackendError(
            f"Unsupported SANDBOX_DOCKER_BACKEND: {backend!r}. "
            f"Supported values: {', '.join(cls.SUPPORTED)}."
        )

    @classmethod
    def _create_remote_tls(cls) -> "docker.DockerClient":
        docker_host = os.environ.get("DOCKER_HOST", "").strip()
        if not docker_host:
            raise DockerBackendError(
                "SANDBOX_DOCKER_BACKEND=remote_tls requires DOCKER_HOST "
                "(e.g. tcp://worker-docker:2376)."
            )
        tls_verify = _env_bool("DOCKER_TLS_VERIFY", True)
        cert_path = os.environ.get("DOCKER_CERT_PATH", "").strip()
        tls_config: docker.tls.TLSConfig | bool
        if cert_path:
            tls_config = docker.tls.TLSConfig(
                client_cert=(
                    os.path.join(cert_path, "cert.pem"),
                    os.path.join(cert_path, "key.pem"),
                ),
                ca_cert=os.path.join(cert_path, "ca.pem"),
                verify=tls_verify,
            )
        else:
            tls_config = tls_verify
        return docker.DockerClient(base_url=docker_host, tls=tls_config, timeout=60)
