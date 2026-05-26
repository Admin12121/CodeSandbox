"""Small helpers exposed to Jinja templates and router internals."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape as html_escape
from typing import Any
from urllib.parse import urlsplit

from markupsafe import Markup, escape

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def cn(*values: object) -> str:
    """Join CSS class names from strings and nested iterables."""

    classes: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            classes.extend(part for part in value.split() if part)
            continue
        if isinstance(value, Mapping):
            classes.extend(str(name) for name, enabled in value.items() if enabled)
            continue
        if isinstance(value, Iterable):
            classes.extend(cn(*value).split())
            continue
        classes.append(str(value))
    return " ".join(dict.fromkeys(classes))


def html_attrs(**attrs: Any) -> Markup:
    """Render safe HTML attributes."""

    rendered: list[Markup] = []
    for raw_name, value in attrs.items():
        if value is False or value is None:
            continue
        name = raw_name.rstrip("_").replace("_", "-")
        if value is True:
            rendered.append(escape(name))
            continue
        rendered.append(Markup(f'{escape(name)}="{escape(value)}"'))
    return Markup(" ".join(rendered))


def is_unsafe_method(method: str) -> bool:
    return method.upper() in UNSAFE_METHODS


def parse_tree_header(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def quote_attr(value: object) -> str:
    return html_escape(str(value), quote=True)


def is_safe_redirect_url(url: str, host_url: str) -> bool:
    """Allow local redirects and same-origin absolute redirects only."""

    if not url:
        return False
    target = urlsplit(url)
    if not target.netloc and not target.scheme:
        return url.startswith("/") and not url.startswith("//")

    current = urlsplit(host_url)
    return target.scheme in {"http", "https"} and (
        target.scheme,
        target.netloc,
    ) == (
        current.scheme,
        current.netloc,
    )
