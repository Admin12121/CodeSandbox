from __future__ import annotations

from functools import wraps

from flask import abort, jsonify, redirect, request

from codesandbox.shared.session import get_current_session


def _unauth() -> object:
    if request.is_json:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    return redirect(f"/login?next={request.path}")


def _forbidden(msg: str = "Permission denied.") -> object:
    if request.is_json:
        return jsonify({"ok": False, "error": msg}), 403
    abort(403)


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
                if request.is_json:
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
            abort(404)
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
                abort(404)
            if not get_member(org.id, str(cs.user.id)):
                return _forbidden("You are not a member of this organization.")
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
            abort(404)
        if not is_org_owner(org.id, str(cs.user.id)):
            return _forbidden("Only the organization owner can perform this action.")
        return f(*args, **kwargs)
    return wrapper
