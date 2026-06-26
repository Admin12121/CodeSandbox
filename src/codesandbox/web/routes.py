from __future__ import annotations

import os

from flask import abort, request, send_from_directory

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.organizations import repository as org_repo
from codesandbox.shared.session import build_nav, require_session
from codesandbox.web.blueprint import router, web_bp
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx

_TEMPLATES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../templates"))
_PUBLIC_DIR = os.path.join(_TEMPLATES_DIR, "public")
_FAVICON = os.path.join(_TEMPLATES_DIR, "favicon.ico")


@web_bp.get("/favicon.ico")
def _favicon():
    if os.path.isfile(_FAVICON):
        return send_from_directory(_TEMPLATES_DIR, "favicon.ico", mimetype="image/x-icon")
    abort(404)


@web_bp.get("/<path:filename>")
def _public_static(filename):
    target = os.path.normpath(os.path.join(_PUBLIC_DIR, filename))
    if target.startswith(_PUBLIC_DIR) and os.path.isfile(target):
        return send_from_directory(_PUBLIC_DIR, filename)
    abort(404)


@router.page("/dashboard")
def dashboard():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/dashboard", user)

    try:
        _, user_count = identity_repo.list_users()
    except Exception:
        user_count = 0
    try:
        _, org_count = org_repo.list_organizations()
    except Exception:
        org_count = 0

    metrics = [
        {"label": "Total Users",       "value": str(user_count), "change": "Platform accounts"},
        {"label": "Organizations",      "value": str(org_count),  "change": "Active tenants"},
        {"label": "Running Sandboxes",  "value": "0",             "change": "Runtime worker pending"},
        {"label": "Open Cases",         "value": "0",             "change": "Case workflow pending"},
    ]
    return {
        "_meta": {"title": "Dashboard — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Dashboard",
        "page_description": "Platform overview — users, orgs, runtime, cases",
        "metrics": metrics,
        **_workspaces_ctx(user),
    }

@router.page("/hub")
def hub():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/hub", user)
    return {
        "_meta": {"title": "Hub — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Hub",
        **_workspaces_ctx(user),
    }


_HUB_INSTANCES = {
    "decompiler": {
        "title": "Decompiler",
        "img": "/decompile.jpg",
        "description": "Decompiler is the best reverse engineering tool — disassemble binaries, recover source structure, and step through control flow in an isolated sandbox.",
        "vcpu": 2,
        "ram_gb": 4,
        "disk_gb": 80,
        "cost_month": 24,
    },
    "venom": {
        "title": "Venom",
        "img": "/malware.jpg",
        "description": "Venom is a secure, network-isolated environment to analyze and test malware samples without putting your host or network at risk.",
        "vcpu": 4,
        "ram_gb": 8,
        "disk_gb": 160,
        "cost_month": 48,
    },
    "athena": {
        "title": "Athena",
        "img": "/attack.jpg",
        "description": "Athena is a perfect simulation environment to perform attack simulations against realistic infrastructure targets.",
        "vcpu": 4,
        "ram_gb": 16,
        "disk_gb": 200,
        "cost_month": 64,
    },
    "phenox": {
        "title": "Phenox",
        "img": "/def.jpg",
        "description": "Phenox is the best place to learn about defence tooling — blue-team drills, log analysis, and detection engineering.",
        "vcpu": 2,
        "ram_gb": 4,
        "disk_gb": 100,
        "cost_month": 20,
    },
}


@router.page("/hub/<instance>/<slug>")
def hub_sandbox(instance: str, slug: str):
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user

    if _HUB_INSTANCES.get(instance) is None:
        return {"_redirect": "/hub"}

    nav = build_nav("/hub", user)
    return {
        "_meta": {"title": f"{instance.title()} Sandbox — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        **_workspaces_ctx(user),
    }


@router.page("/hub/<instance>")
def hub_instance(instance: str):
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user

    data = _HUB_INSTANCES.get(instance)
    if data is None:
        return {"_redirect": "/hub"}

    nav = build_nav("/hub", user)
    return {
        "_meta": {"title": f"{data['title']} — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": data["title"],
        "instance": {"slug": instance, **data},
        **_workspaces_ctx(user),
    }

@router.page("/laboratory/sandbox")
def sandbox():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/laboratory/sandbox", user)

    try:
        _, user_count = identity_repo.list_users()
    except Exception:
        user_count = 0
    try:
        _, org_count = org_repo.list_organizations()
    except Exception:
        org_count = 0

    metrics = [
        {"label": "Total Users",       "value": str(user_count), "change": "Platform accounts"},
        {"label": "Organizations",      "value": str(org_count),  "change": "Active tenants"},
        {"label": "Running Sandboxes",  "value": "0",             "change": "Runtime worker pending"},
        {"label": "Open Cases",         "value": "0",             "change": "Case workflow pending"},
    ]
    return {
        "_meta": {"title": "Dashboard — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Dashboard",
        "page_description": "Platform overview — users, orgs, runtime, cases",
        "metrics": metrics,
        **_workspaces_ctx(user),
    }
