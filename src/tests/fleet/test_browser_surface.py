from __future__ import annotations

import json
import re
from importlib.resources import files

from tests._context import TestCase, TestContext


_PARTIAL_HEADER = "X-Flask-Router"
_CURRENT_PATH_HEADER = "X-Flask-Current-Path"
_CURRENT_TREE_HEADER = "X-Flask-Current-Tree"
_CURRENT_LAYOUT_STATE_HEADER = "X-Flask-Current-Layout-State"


def _router_client_source() -> str:
    return files("app_router").joinpath("static/router.js").read_text(encoding="utf-8")


def _meta(html: str, name: str) -> str:
    match = re.search(
        rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"',
        html,
        flags=re.IGNORECASE,
    )
    assert match is not None, f"missing {name} meta tag"
    return match.group(1)


def _script_nonce(html: str, key: str) -> str:
    match = re.search(
        rf'<script\b[^>]*\bdata-app-router-inline-script="{re.escape(key)}"[^>]*>',
        html,
        flags=re.IGNORECASE,
    )
    assert match is not None, f"missing inline app script {key}"
    tag = match.group(0)
    nonce_match = re.search(r'\bnonce="([^"]+)"', tag)
    assert nonce_match is not None, f"inline app script {key} has no CSP nonce"
    return nonce_match.group(1)


def _client():
    from tests import _context

    _context.boot()
    assert _context._flask_app is not None, "app context did not boot"
    return _context._flask_app.test_client()


def test_navigation_client_contract(ctx: TestContext) -> None:
    source = _router_client_source()
    assert source.count("\n") < 260, "navigation client should remain small and auditable"
    assert 'const PARTIAL_HEADER = "X-Flask-Router"' in source
    assert '[PARTIAL_HEADER]: "partial"' in source
    assert '[CURRENT_PATH_HEADER]: current.path' in source
    assert '[CURRENT_TREE_HEADER]: current.tree.join(",")' in source
    assert '[CURRENT_LAYOUT_STATE_HEADER]: current.layoutState' in source
    assert 'credentials: "same-origin"' in source
    assert 'redirect: "manual"' in source
    assert 'document.addEventListener("click"' in source
    assert 'window.addEventListener("popstate"' in source
    assert 'window.history.pushState' in source
    assert 'dispatchRouterEvent("app-router:patch"' in source
    assert 'dispatchRouterEvent("app-router:navigate"' in source
    assert 'meta[name="app-router-script-nonce"]' in source
    assert "script.nonce = nonce" in source


def test_full_page_injects_router_state_and_csp_nonce(ctx: TestContext) -> None:
    with _client() as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    csp = response.headers.get("Content-Security-Policy", "")
    nonce = _meta(html, "app-router-script-nonce")

    assert "script-src" in csp
    assert f"'nonce-{nonce}'" in csp
    assert '<meta name="app-router-path" content="/">' in html
    assert 'data-app-router-client' in html
    assert 'src="/_app/router.js"' in html

    inline_script_keys = re.findall(r'data-app-router-inline-script="([^"]+)"', html)
    assert inline_script_keys, "expected at least one app_script on the landing page"
    for key in inline_script_keys:
        assert _script_nonce(html, key) == nonce


def test_router_client_endpoint_has_browser_safe_headers(ctx: TestContext) -> None:
    with _client() as client:
        response = client.get("/_app/router.js")

    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Cache-Control") == "no-store"
    assert "text/javascript" in response.headers.get("Content-Type", "")
    body = response.get_data(as_text=True)
    assert 'document.addEventListener("click"' in body
    assert '[PARTIAL_HEADER]: "partial"' in body


def test_partial_navigation_returns_patch_payload(ctx: TestContext) -> None:
    with _client() as client:
        full = client.get("/")
        assert full.status_code == 200
        html = full.get_data(as_text=True)
        path = _meta(html, "app-router-path")
        tree = _meta(html, "app-router-tree")
        layout_state = _meta(html, "app-router-layout-state")

        partial = client.get(
            "/",
            headers={
                _PARTIAL_HEADER: "partial",
                _CURRENT_PATH_HEADER: path,
                _CURRENT_TREE_HEADER: tree,
                _CURRENT_LAYOUT_STATE_HEADER: layout_state,
                "Accept": "application/json",
            },
        )

    assert partial.status_code == 200
    assert "application/json" in partial.headers.get("Content-Type", "")
    assert partial.headers.get("Cache-Control") == "no-store"
    assert _PARTIAL_HEADER in partial.headers.get("Vary", "")

    payload = json.loads(partial.get_data(as_text=True))
    assert payload["mode"] == "patch"
    assert payload["url"] == "/"
    assert payload["boundary"]
    assert isinstance(payload["html"], str) and payload["html"].strip()
    assert isinstance(payload["tree"], list) and payload["tree"]
    assert "inlineScripts" in payload


TESTS = [
    TestCase("navigation client contract", "browser", test_navigation_client_contract),
    TestCase("full page CSP nonce and router state", "browser", test_full_page_injects_router_state_and_csp_nonce),
    TestCase("router client safe headers", "browser", test_router_client_endpoint_has_browser_safe_headers),
    TestCase("partial navigation patch payload", "browser", test_partial_navigation_returns_patch_payload),
]
