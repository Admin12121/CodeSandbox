from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pyotp

from tests._context import TestCase, TestContext, unique
from tests.e2e._helpers import app_client, cleanup_user, csrf_headers


def test_user_lifecycle(ctx: TestContext) -> None:
    from codesandbox.features.identity import repository, service

    client = app_client()
    headers = csrf_headers(client)
    email = f"{unique('e2e-user')}@test.local"
    original_password = "OriginalPass123!"
    changed_password = "ChangedPass456!"

    signup = client.post(
        "/login",
        data={
            "mode": "signup",
            "name": "E2E User",
            "email": email,
            "password": original_password,
        },
        headers=headers,
    )
    assert signup.status_code == 303
    assert signup.headers["Location"] == "/dashboard"

    # Authentication rotates the session and its CSRF token.
    headers = csrf_headers(client)

    user = repository.find_user_by_email(email)
    assert user is not None
    user_id = str(user.id)
    ctx.defer(lambda: cleanup_user(user_id, email=email))

    secondary_token = service.create_session_for_user(
        user_id,
        ip_address="198.51.100.12",
        user_agent="Secondary-E2E/1",
    )
    assert secondary_token
    secondary_hash = hashlib.sha256(secondary_token.encode()).hexdigest()
    assert repository.find_active_session(secondary_hash) is not None

    changed = client.post(
        "/settings/change-password",
        json={
            "current_password": original_password,
            "new_password": changed_password,
            "confirm_password": changed_password,
        },
        headers=headers,
    )
    assert changed.status_code == 200 and changed.get_json() == {"ok": True}, (
        changed.get_data(as_text=True)
    )
    assert repository.find_active_session(secondary_hash) is None
    assert not service.sign_in(
        email=email,
        password=original_password,
        ip_address="127.0.0.1",
        user_agent="CodeSandbox-E2E/1",
    ).ok

    setup = client.post("/settings/2fa/setup-json", json={}, headers=headers)
    assert setup.status_code == 200
    setup_data = setup.get_json()
    assert setup_data["ok"] is True
    totp_code = pyotp.TOTP(setup_data["secret"]).now()
    confirmed = client.post(
        "/settings/2fa/confirm-json",
        json={"code": totp_code},
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["ok"] is True
    assert len(confirmed.get_json()["backup_codes"]) == 10

    begin = client.post(
        "/settings/passkeys/register/begin",
        json={"attachment": "platform"},
        headers=headers,
    )
    assert begin.status_code == 200 and begin.get_json()["ok"] is True

    credential_id_bytes = f"e2e-credential-{unique('pk')}".encode()
    with patch(
        "webauthn.verify_registration_response",
        return_value=SimpleNamespace(
            credential_id=credential_id_bytes,
            credential_public_key=b"e2e-public-key",
            sign_count=0,
            aaguid=None,
        ),
    ):
        completed = client.post(
            "/settings/passkeys/register/complete",
            json={"credential": {"type": "public-key"}, "name": "E2E Passkey"},
            headers=headers,
        )
    assert completed.status_code == 200 and completed.get_json()["ok"] is True

    passkeys = repository.get_user_passkeys(user_id, enabled_only=True)
    assert len(passkeys) == 1
    passkey = passkeys[0]

    signed_out = client.post("/logout", headers=headers)
    assert signed_out.status_code == 303

    headers = csrf_headers(client)
    login = client.post(
        "/login",
        data={"mode": "signin", "email": email, "password": changed_password},
        headers=headers,
    )
    assert login.status_code == 303 and login.headers["Location"] == "/two-factor"
    with client.session_transaction() as pending:
        assert pending["_2fa_pending_user_id"] == user_id
        assert pending["_2fa_method"] == "passkey"

    headers = csrf_headers(client)
    auth_begin = client.post("/two-factor/passkey/begin", json={}, headers=headers)
    assert auth_begin.status_code == 200 and auth_begin.get_json()["ok"] is True, (
        auth_begin.get_data(as_text=True)
    )

    with patch(
        "webauthn.verify_authentication_response",
        return_value=SimpleNamespace(new_sign_count=1),
    ), patch("codesandbox.shared.email.send_new_device_login_alert") as send_alert:
        verified = client.post(
            "/two-factor/passkey/verify",
            json={
                "credential": {
                    "id": passkey.credential_id,
                    "rawId": passkey.credential_id,
                    "type": "public-key",
                }
            },
            headers=headers,
        )
    assert verified.status_code == 200 and verified.get_json()["ok"] is True
    assert verified.get_json()["next"] == "/dashboard"
    send_alert.assert_called_once()

    refreshed_passkey = repository.find_passkey_by_id(str(passkey.id))
    assert refreshed_passkey is not None
    assert refreshed_passkey.sign_count == 1
    assert refreshed_passkey.last_used_at is not None

    revokable_token = service.create_session_for_user(
        user_id,
        ip_address="127.0.0.1",
        user_agent="Werkzeug/3.1.3",
    )
    assert revokable_token
    revokable_hash = hashlib.sha256(revokable_token.encode()).hexdigest()
    revokable = repository.find_active_session(revokable_hash)
    assert revokable is not None
    revoked = client.post(
        f"/settings/sessions/{revokable.id}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 303
    assert "Session+revoked" in revoked.headers["Location"]
    assert repository.find_active_session(revokable_hash) is None

    listed = client.get("/settings/passkeys")
    assert listed.status_code == 200
    assert listed.get_json()["passkeys"][0]["name"] == "E2E Passkey"

    removed = client.post(
        f"/settings/passkeys/{passkey.id}/delete",
        headers=headers,
    )
    assert removed.status_code == 200 and removed.get_json()["ok"] is True
    assert repository.get_user_passkeys(user_id) == []


TESTS: list[TestCase] = [
    TestCase("user lifecycle", "e2e_user_lifecycle", test_user_lifecycle),
]
