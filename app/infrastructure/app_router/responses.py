"""Response helper objects for page loaders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedirectResult:
    """Server-side redirect that can become JSON during partial navigation."""

    url: str
    status_code: int = 303
    message: str | None = None


def router_redirect(
    url: str,
    *,
    status_code: int = 303,
    message: str | None = None,
) -> RedirectResult:
    """Return a redirect result from a page loader."""

    return RedirectResult(url=url, status_code=status_code, message=message)
