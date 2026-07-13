from __future__ import annotations

import os
import uuid

from runtime.docker_client import DockerClientFactory
from runtime.image_policy import ensure_image, normalize_image_reference


def _run_probe(
    client, image: str, network_name: str, target_url: str
) -> tuple[int, str]:
    container = client.containers.create(
        image=image,
        command=[
            "sh",
            "-c",
            'wget -q -T "$TIMEOUT" -O /dev/null "$TARGET_URL"',
        ],
        environment={"TARGET_URL": target_url, "TIMEOUT": "10"},
        network=network_name,
        detach=True,
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=1777"},
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        pids_limit=64,
        mem_limit=128 * 1024 * 1024,
        labels={"com.codesandbox.smoke-test": "true"},
    )
    try:
        container.start()
        result = container.wait(timeout=30)
        output = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        return int(result.get("StatusCode", 1)), output.strip()
    finally:
        container.remove(force=True)


def main() -> None:
    client = DockerClientFactory.preflight()
    restricted = None
    internet = None
    try:
        image = normalize_image_reference(
            os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36")
        )
        target_url = os.environ.get(
            "SANDBOX_SMOKE_TEST_URL", "http://example.com"
        ).strip()
        if not target_url:
            raise RuntimeError("SANDBOX_SMOKE_TEST_URL cannot be empty.")
        ensure_image(client, image)

        suffix = uuid.uuid4().hex[:12]
        restricted = client.networks.create(
            f"cs-smoke-restricted-{suffix}",
            driver="bridge",
            internal=True,
            check_duplicate=True,
        )
        internet = client.networks.create(
            f"cs-smoke-internet-{suffix}",
            driver="bridge",
            internal=False,
            check_duplicate=True,
        )

        restricted_code, _ = _run_probe(
            client, image, restricted.name, target_url
        )
        if restricted_code == 0:
            raise RuntimeError(
                "Restricted sandbox network unexpectedly reached the Internet."
            )

        internet_code, internet_output = _run_probe(
            client, image, internet.name, target_url
        )
        if internet_code != 0:
            raise RuntimeError(
                "Full-Internet sandbox network could not reach the test URL. "
                f"exit={internet_code} output={internet_output!r}"
            )

        print("Docker TLS: OK")
        print(f"Image availability: OK ({image})")
        print("Restricted per-instance network: Internet blocked")
        print(f"Full-Internet per-instance network: OK ({target_url})")
    finally:
        for network in (restricted, internet):
            if network is None:
                continue
            try:
                network.remove()
            except Exception:
                pass
        client.close()


if __name__ == "__main__":
    main()
