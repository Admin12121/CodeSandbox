from __future__ import annotations

import json
import re

from tests._context import TestContext, unique


def delete_rows(model, **filters) -> None:
    for row in model.objects.filter(**filters).all():
        try:
            row.delete()
        except Exception:
            pass


def cleanup_user(user_id: str, *, email: str | None = None) -> None:
    from codesandbox.features.identity.models import (
        AuthAccount,
        LoginAttempt,
        Session,
        TwoFactorMethod,
        User,
        UserPasskey,
        VerificationToken,
    )

    delete_rows(Session, user_id=user_id)
    delete_rows(UserPasskey, user_id=user_id)
    delete_rows(TwoFactorMethod, user_id=user_id)
    delete_rows(AuthAccount, user_id=user_id)
    delete_rows(VerificationToken, user_id=user_id)
    if email:
        delete_rows(LoginAttempt, email=email)
    user = User.objects.filter(id=user_id).first()
    if user is not None:
        try:
            user.delete()
        except Exception:
            pass


def make_user(
    ctx: TestContext,
    prefix: str,
    *,
    password: str = "password123",
):
    from codesandbox.features.identity import repository, service

    email = f"{unique(prefix)}@test.local"
    result = service.sign_up(
        name=prefix,
        email=email,
        password=password,
        ip_address="127.0.0.1",
        user_agent="CodeSandbox-E2E/1",
    )
    assert result.ok, result.message
    user = repository.find_user_by_email(email)
    assert user is not None
    ctx.defer(lambda uid=str(user.id), mail=email: cleanup_user(uid, email=mail))
    return user, result.token


def app_client():
    from flask import current_app

    app = current_app._get_current_object()
    app.testing = True
    return app.test_client()


def csrf_headers(client) -> dict[str, str]:
    response = client.get("/csrf-fetch.js")
    assert response.status_code == 200
    match = re.search(r"var CSRF_TOKEN = (\"(?:[^\"\\]|\\.)*\");", response.get_data(as_text=True))
    assert match is not None, "CSRF bootstrap did not expose a token"
    return {"X-CSRF-Token": json.loads(match.group(1))}

