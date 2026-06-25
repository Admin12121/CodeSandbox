from codesandbox.web.blueprint import router
from codesandbox.shared.session import build_nav, require_platform_role
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx

@router.page("/laboratory/sandbox")
def sandbox():
    session, redirect = require_platform_role("system_admin", "system_staff")
    user = session.user
    nav = build_nav("/platform/users", user)
    return {
        "_meta": {"title": "Users — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
    }
