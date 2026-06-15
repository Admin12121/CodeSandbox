from __future__ import annotations

from flask import current_app, redirect, request

from codesandbox.web.blueprint import web_bp

from .service import sign_in, sign_out, sign_up


@web_bp.post("/login")
def login_action():
    mode = request.form.get("mode", "signin")
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip() or email.split("@")[0]
    next_path = request.form.get("next", "/dashboard")

    if mode == "signup":
        result = sign_up(
            name=name,
            email=email,
            password=password,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
    else:
        result = sign_in(
            email=email,
            password=password,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

    if not result.ok or not result.token:
        return redirect(f"/login?error={result.message}&mode={mode}", code=303)

    response = redirect(next_path, code=303)
    response.set_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        result.token,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        max_age=current_app.config["SESSION_TTL_HOURS"] * 3600,
    )
    return response


@web_bp.post("/logout")
def logout_action():
    cookie_name = current_app.config["SESSION_COOKIE_NAME"]
    token = request.cookies.get(cookie_name)
    if token:
        sign_out(token)
    response = redirect("/login", code=303)
    response.delete_cookie(cookie_name)
    return response
