import os

from codesandbox.shared.session import build_nav, require_platform_role
from codesandbox.web.blueprint import router
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx


@router.page("/laboratory/sandbox")
def sandbox():
    if os.environ.get("ENVIRONMENT", "development").strip().lower() == "production":
        return {"_redirect": "/dashboard"}
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    nav = build_nav("/laboratory/sandbox", user, ws_ctx.get("active_workspace"))
    return {
        "_meta": {"title": "Lab UI Preview — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Lab UI Preview",
        "page_description": "Development-only preview. Real instances render through /instances/<instance_id>.",
        "lab_preview": True,
        **ws_ctx,
    }
