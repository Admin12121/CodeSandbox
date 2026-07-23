from __future__ import annotations

from functools import wraps
import urllib.parse

from flask import abort, jsonify, redirect, request

from codesandbox.shared.session import get_current_session


def _wants_json_response() -> bool:
    if request.is_json or request.headers.get("X-Requested-With") == "fetch":
        return True
    accepted_json = request.accept_mimetypes["application/json"]
    accepted_html = request.accept_mimetypes["text/html"]
    return accepted_json > 0 and accepted_json >= accepted_html


def _unauth() -> object:
    if _wants_json_response():
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    return redirect(f"/login?next={request.path}")


def _forbidden(msg: str = "Permission denied.") -> object:
    if _wants_json_response():
        return jsonify({"ok": False, "error": msg}), 403
    abort(403)


def _not_found(msg: str = "Not found.") -> object:
    if _wants_json_response():
        return jsonify({"ok": False, "error": msg}), 404
    abort(404)


# ── Guard 1: any logged-in user ───────────────────────────────────────────────

def authenticated(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not get_current_session():
            return _unauth()
        return f(*args, **kwargs)
    return wrapper


# ── Guard 2: logged-in + NOT platform staff/admin ─────────────────────────────

def no_staff(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        cs = get_current_session()
        if not cs:
            return _unauth()
        if cs.user.platform_role in ("system_staff", "system_admin"):
            return _forbidden("Platform staff cannot access this page.")
        return f(*args, **kwargs)
    return wrapper


def verified_email(action: str = "continuing"):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cs = get_current_session()
            if not cs:
                return _unauth()
            if not cs.user.email_verified:
                message = f"Verify your email before {action}."
                if _wants_json_response():
                    return jsonify({"ok": False, "error": message}), 403
                return redirect(f"/settings?tab=security&error={urllib.parse.quote(message)}")
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── Guard 3: platform staff/admin + permission keys ───────────────────────────

def platform_perm(*keys: str):
    """Requires platform_role in (system_staff, system_admin) and all listed keys."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from codesandbox.shared.permissions import has_platform_permission
            cs = get_current_session()
            if not cs:
                return _unauth()
            if cs.user.platform_role not in ("system_staff", "system_admin"):
                if _wants_json_response():
                    return _forbidden()
                return redirect("/dashboard")
            for key in keys:
                if not has_platform_permission(cs.user, key):
                    return _forbidden()
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── Guard 4: any org member ───────────────────────────────────────────────────

def org_member(f):
    """Requires authentication + membership in the org from the 'slug' URL kwarg."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        from codesandbox.features.organizations.repository import (
            get_organization_by_slug,
            get_member,
        )
        cs = get_current_session()
        if not cs:
            return _unauth()
        slug = kwargs.get("slug")
        org = get_organization_by_slug(slug) if slug else None
        if not org:
            return _not_found("Organization not found.")
        if not get_member(org.id, str(cs.user.id)):
            return _forbidden("You are not a member of this organization.")
        return f(*args, **kwargs)
    return wrapper


# ── Guard 5: org member + permission keys (owner bypass) ──────────────────────

def org_perm(*keys: str):
    """Requires membership + all listed org permission keys. Owner bypasses key checks."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from codesandbox.features.organizations.repository import (
                get_organization_by_slug,
                get_member,
                is_org_owner,
            )
            from codesandbox.shared.permissions import has_org_permission
            cs = get_current_session()
            if not cs:
                return _unauth()
            slug = kwargs.get("slug")
            org = get_organization_by_slug(slug) if slug else None
            if not org:
                return _not_found("Organization not found.")
            if not get_member(org.id, str(cs.user.id)):
                return _forbidden("You are not a member of this organization.")
            if org.status != "active" and not set(keys).issubset({"org.settings.edit"}):
                return _forbidden("This organization is not active.")
            if not is_org_owner(org.id, str(cs.user.id)):
                for key in keys:
                    if not has_org_permission(org.id, cs.user, key):
                        return _forbidden("You don't have permission to perform this action.")
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── Guard 6: org owner only — non-delegatable ─────────────────────────────────

def org_owner(f):
    """Requires the requesting user to be the current org owner. Never delegatable."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        from codesandbox.features.organizations.repository import (
            get_organization_by_slug,
            is_org_owner,
        )
        cs = get_current_session()
        if not cs:
            return _unauth()
        slug = kwargs.get("slug")
        org = get_organization_by_slug(slug) if slug else None
        if not org:
            return _not_found("Organization not found.")
        if not is_org_owner(org.id, str(cs.user.id)):
            return _forbidden("Only the organization owner can perform this action.")
        return f(*args, **kwargs)
    return wrapper
