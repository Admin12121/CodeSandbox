from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pyotp
from werkzeug.security import check_password_hash, generate_password_hash

from codesandbox.config import get_settings

from . import repository


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    message: str
    token: str | None = None
    requires_2fa: bool = False
    user_id: str | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_up(
    *,
    name: str,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    if len(password) < 8:
        return AuthResult(False, "Password must be at least 8 characters.")
    password_hash = generate_password_hash(password)
    existing = repository.find_user_by_email(email)
    if existing:
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="email_taken",
        )
        return AuthResult(False, "An account with that email already exists.")
    try:
        repository.create_user(email=email, name=name, password_hash=password_hash)
    except Exception:
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="signup_failed",
        )
        return AuthResult(False, "Unable to create account.")
    return sign_in(
        email=email,
        password=password,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def sign_in(
    *,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    user = repository.find_user_by_email(email)
    if not user or not user.password_hash:
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="invalid_credentials",
        )
        return AuthResult(False, "Invalid email or password.")

    if user.status == "banned":
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="banned",
        )
        return AuthResult(False, "This account has been suspended.")

    if not check_password_hash(user.password_hash, password):
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="invalid_credentials",
        )
        return AuthResult(False, "Invalid email or password.")

    if user.two_factor_enabled:
        return AuthResult(True, "2FA required.", requires_2fa=True, user_id=user.id)

    token = create_session_for_user(
        user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return AuthResult(True, "Signed in.", token=token)


def create_session_for_user(
    user_id: str,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> str | None:
    user = repository.find_user_by_id(user_id)
    if not user or user.deleted_at is not None or user.status == "banned":
        return None

    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    repository.create_session(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    repository.update_user(user.id, last_login_at=datetime.now(timezone.utc))
    repository.record_login_attempt(
        email=user.email,
        ip_address=ip_address,
        succeeded=True,
    )
    return raw_token


def sign_out(token: str) -> None:
    repository.delete_session(hash_token(token))


# ── Email verification ────────────────────────────────────────────────────────

def _token_for_purpose(purpose: str, ttl_hours: int = 1) -> tuple[str, str]:
    """Return (raw_token, token_hash) for a new verification token."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def request_email_verification(user_id: str, email: str) -> str:
    """Create an email-verify token and return the raw token (caller sends email)."""
    raw, h = _token_for_purpose("email_verify")
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    repository.create_verification_token(
        user_id=user_id,
        identifier=email,
        token_hash=h,
        purpose="email_verify",
        expires_at=expires,
    )
    return raw


def verify_email(token: str) -> AuthResult:
    vt = repository.find_verification_token(hash_token(token))
    if not vt or vt.purpose != "email_verify":
        return AuthResult(False, "Invalid or expired verification link.")
    now = datetime.now(timezone.utc)
    exp = vt.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return AuthResult(False, "This verification link has expired.")
    if vt.used_at:
        return AuthResult(False, "This link has already been used.")
    repository.consume_verification_token(vt)
    if vt.user_id:
        repository.update_user(vt.user_id, email_verified=True)
    return AuthResult(True, "Email verified.")


# ── Password reset ────────────────────────────────────────────────────────────

def request_password_reset(email: str) -> tuple[bool, str]:
    """Create a reset token. Returns (found, raw_token). Caller sends email."""
    user = repository.find_user_by_email(email)
    if not user:
        return False, ""
    raw, h = _token_for_purpose("password_reset")
    expires = datetime.now(timezone.utc) + timedelta(hours=2)
    repository.create_verification_token(
        user_id=user.id,
        identifier=email,
        token_hash=h,
        purpose="password_reset",
        expires_at=expires,
    )
    return True, raw


def reset_password(token: str, new_password: str) -> AuthResult:
    if len(new_password) < 8:
        return AuthResult(False, "Password must be at least 8 characters.")
    vt = repository.find_verification_token(hash_token(token))
    if not vt or vt.purpose != "password_reset":
        return AuthResult(False, "Invalid or expired reset link.")
    now = datetime.now(timezone.utc)
    exp = vt.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return AuthResult(False, "This reset link has expired. Please request a new one.")
    if vt.used_at:
        return AuthResult(False, "This link has already been used.")
    repository.consume_verification_token(vt)
    pw_hash = generate_password_hash(new_password)
    repository.update_user(vt.user_id, password_hash=pw_hash)
    return AuthResult(True, "Password reset successfully.")


# ── TOTP / 2FA ────────────────────────────────────────────────────────────────

def _fernet() -> "Fernet":
    from cryptography.fernet import Fernet
    import hashlib, base64
    # Derive a 32-byte Fernet key from SECRET_KEY via SHA-256
    raw = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt_secret(secret: str) -> str:
    """AES-128 (Fernet) encryption of TOTP secret / backup codes."""
    return _fernet().encrypt(secret.encode()).decode()


def _decrypt_secret(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


def generate_totp_setup(user_id: str, email: str) -> dict:
    """Generate a new TOTP secret and provisioning URI for setup."""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=email, issuer_name="CodeSandbox")
    repository.upsert_totp_method(
        user_id=user_id,
        secret_encrypted=_encrypt_secret(secret),
        is_enabled=False,
    )
    return {"secret": secret, "uri": uri}


def confirm_totp_setup(user_id: str, code: str) -> AuthResult:
    """Verify the user's OTP code during setup and enable TOTP."""
    method = repository.get_totp_method(user_id)
    if not method or not method.secret_encrypted:
        return AuthResult(False, "No pending TOTP setup found.")
    secret = _decrypt_secret(method.secret_encrypted)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        return AuthResult(False, "Invalid code. Please try again.")
    backup_codes = [secrets.token_hex(5).upper() for _ in range(10)]
    repository.enable_totp(
        user_id,
        backup_codes_encrypted=_encrypt_secret(json.dumps(backup_codes)),
    )
    repository.update_user(user_id, two_factor_enabled=True)
    return AuthResult(True, "Two-factor authentication enabled.", token=",".join(backup_codes))


def verify_totp(user_id: str, code: str) -> bool:
    """Verify a TOTP code at login."""
    method = repository.get_totp_method(user_id)
    if not method or not method.is_enabled or not method.secret_encrypted:
        return False
    secret = _decrypt_secret(method.secret_encrypted)
    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):
        return True
    # Check backup codes
    if method.backup_codes_encrypted:
        try:
            codes: list[str] = json.loads(_decrypt_secret(method.backup_codes_encrypted))
            if code.upper() in codes:
                codes.remove(code.upper())
                repository.upsert_totp_method(
                    user_id=user_id,
                    secret_encrypted=method.secret_encrypted,
                    is_enabled=True,
                    backup_codes_encrypted=_encrypt_secret(json.dumps(codes)),
                )
                return True
        except Exception:
            pass
    return False


def disable_2fa(user_id: str) -> None:
    repository.disable_totp(user_id)
    repository.update_user(user_id, two_factor_enabled=False)


# ── Passkeys (WebAuthn) ────────────────────────────────────────────────────────

def _get_rp_config() -> tuple[str, str]:
    """Returns (rp_id, origin) derived from APP_URL."""
    from urllib.parse import urlparse
    parsed = urlparse(get_settings().app_url)
    rp_id = parsed.hostname or "localhost"
    port = parsed.port
    if port and port not in (80, 443):
        origin = f"{parsed.scheme}://{parsed.hostname}:{port}"
    else:
        origin = f"{parsed.scheme}://{parsed.hostname}"
    return rp_id, origin


def passkey_register_begin(user_id: str, attachment: str | None = None) -> dict:
    """Generate WebAuthn registration options. Returns JSON-serializable dict."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorAttachment,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    from webauthn.helpers import bytes_to_base64url
    import json

    user = repository.find_user_by_id(user_id)
    if not user:
        return {}

    rp_id, _ = _get_rp_config()
    existing = repository.get_user_passkeys(user_id)
    from webauthn.helpers import base64url_to_bytes
    exclude_credentials = []
    for pk in existing:
        try:
            exclude_credentials.append({"type": "public-key", "id": base64url_to_bytes(pk.credential_id)})
        except Exception:
            pass

    auth_attach = None
    if attachment == "platform":
        auth_attach = AuthenticatorAttachment.PLATFORM
    elif attachment == "cross-platform":
        auth_attach = AuthenticatorAttachment.CROSS_PLATFORM

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="CodeSandbox",
        user_id=user_id.encode("utf-8"),
        user_name=user.email,
        user_display_name=user.name,
        exclude_credentials=exclude_credentials,
        authenticator_attachment=auth_attach,
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    challenge_b64 = bytes_to_base64url(options.challenge)
    options_json = json.loads(options_to_json(options))
    return {"challenge": challenge_b64, "options": options_json}


def passkey_register_complete(user_id: str, challenge_b64: str, credential_json: str, passkey_name: str | None = None) -> AuthResult:
    """Verify WebAuthn registration response and save the passkey."""
    from webauthn import verify_registration_response
    from webauthn.helpers.structs import RegistrationCredential
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    import json

    rp_id, origin = _get_rp_config()

    try:
        challenge_bytes = base64url_to_bytes(challenge_b64)
        credential = RegistrationCredential.parse_raw(credential_json)
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge_bytes,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=False,
        )
    except Exception as exc:
        return AuthResult(False, f"Passkey registration failed: {exc}")

    credential_id = bytes_to_base64url(verification.credential_id)
    public_key = bytes_to_base64url(verification.credential_public_key)
    sign_count = verification.sign_count
    aaguid = str(verification.aaguid) if getattr(verification, "aaguid", None) else None

    existing = repository.find_passkey_by_credential_id(credential_id)
    if existing:
        return AuthResult(False, "This passkey is already registered.")

    name = passkey_name or "Passkey"
    repository.create_passkey(
        user_id=user_id,
        credential_id=credential_id,
        public_key=public_key,
        sign_count=sign_count,
        aaguid=aaguid,
        name=name,
    )
    return AuthResult(True, "Passkey registered successfully.")


def get_user_passkeys(user_id: str) -> list[dict]:
    passkeys = repository.get_user_passkeys(user_id)
    return [
        {
            "id": str(pk.id),
            "name": pk.name or "Passkey",
            "created_at": pk.created_at.isoformat() if pk.created_at else None,
            "last_used_at": pk.last_used_at.isoformat() if pk.last_used_at else None,
        }
        for pk in passkeys
    ]


# ── Social account linking ─────────────────────────────────────────────────────

def link_google_account(user_id: str, code: str, redirect_uri: str) -> AuthResult:
    token_data = google_exchange_code(code, redirect_uri)
    if not token_data or "access_token" not in token_data:
        return AuthResult(False, "Google authentication failed.")
    g_user = google_get_user(token_data["access_token"])
    if not g_user:
        return AuthResult(False, "Could not fetch Google profile.")
    g_id = str(g_user.get("sub", ""))
    existing = repository.find_auth_account("google", g_id)
    if existing and str(existing.user_id) != str(user_id):
        return AuthResult(False, "This Google account is already connected to another user.")
    repository.upsert_auth_account(
        user_id=user_id, provider="google",
        provider_account_id=g_id, access_token=token_data["access_token"],
    )
    return AuthResult(True, "Google account connected.")


def link_github_account(user_id: str, code: str) -> AuthResult:
    token_data = github_exchange_code(code)
    if not token_data or "access_token" not in token_data:
        return AuthResult(False, "GitHub authentication failed.")
    gh_user = github_get_user(token_data["access_token"])
    if not gh_user:
        return AuthResult(False, "Could not fetch GitHub profile.")
    gh_id = str(gh_user.get("id", ""))
    existing = repository.find_auth_account("github", gh_id)
    if existing and str(existing.user_id) != str(user_id):
        return AuthResult(False, "This GitHub account is already connected to another user.")
    repository.upsert_auth_account(
        user_id=user_id, provider="github",
        provider_account_id=gh_id, access_token=token_data["access_token"],
    )
    return AuthResult(True, "GitHub account connected.")


def unlink_account(user_id: str, provider: str) -> tuple[bool, str]:
    user = repository.find_user_by_id(user_id)
    if not user:
        return False, "User not found."
    accounts = repository.get_user_auth_accounts(user_id)
    other_providers = [a for a in accounts if a.provider != provider]
    if not other_providers and not user.password_hash:
        return False, "Cannot disconnect your only sign-in method. Add a password first."
    repository.delete_auth_account_by_provider(user_id, provider)
    return True, ""


# ── GitHub OAuth ──────────────────────────────────────────────────────────────

def github_exchange_code(code: str) -> dict | None:
    """Exchange GitHub OAuth code for access token. Returns token info dict."""
    settings = get_settings()
    client_id = getattr(settings, "github_client_id", None)
    client_secret = getattr(settings, "github_client_secret", None)
    if not client_id or not client_secret:
        return None
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }).encode()
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=data,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def github_get_user(access_token: str) -> dict | None:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def google_exchange_code(code: str, redirect_uri: str) -> dict | None:
    """Exchange Google OAuth code for access token."""
    settings = get_settings()
    data = urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def google_get_user(access_token: str) -> dict | None:
    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def sign_in_with_google(
    *,
    code: str,
    redirect_uri: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    token_data = google_exchange_code(code, redirect_uri)
    if not token_data or "access_token" not in token_data:
        return AuthResult(False, "Google authentication failed.")
    access_token = token_data["access_token"]
    g_user = google_get_user(access_token)
    if not g_user:
        return AuthResult(False, "Could not fetch Google profile.")

    g_id = str(g_user.get("sub", ""))
    g_email = g_user.get("email") or f"g_{g_id}@google.local"
    g_name = g_user.get("name") or g_user.get("given_name") or "Google User"

    account = repository.find_auth_account("google", g_id)
    if account:
        user = repository.find_user_by_id(account.user_id)
        if not user:
            return AuthResult(False, "Account not found.")
        repository.upsert_auth_account(
            user_id=user.id, provider="google",
            provider_account_id=g_id, access_token=access_token,
        )
    else:
        user = repository.find_user_by_email(g_email)
        if not user:
            user = repository.create_user(email=g_email, name=g_name, password_hash=None)
            repository.update_user(user.id, email_verified=True)
        repository.upsert_auth_account(
            user_id=user.id, provider="google",
            provider_account_id=g_id, access_token=access_token,
        )

    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    repository.create_session(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    repository.update_user(user.id, last_login_at=datetime.now(timezone.utc))
    return AuthResult(True, "Signed in with Google.", token=raw_token)


def sign_in_with_github(
    *,
    code: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    token_data = github_exchange_code(code)
    if not token_data or "access_token" not in token_data:
        return AuthResult(False, "GitHub authentication failed.")
    access_token = token_data["access_token"]
    gh_user = github_get_user(access_token)
    if not gh_user:
        return AuthResult(False, "Could not fetch GitHub profile.")

    gh_id = str(gh_user.get("id", ""))
    gh_email = gh_user.get("email") or f"gh_{gh_id}@github.local"
    gh_name = gh_user.get("name") or gh_user.get("login") or "GitHub User"

    account = repository.find_auth_account("github", gh_id)
    if account:
        user = repository.find_user_by_id(account.user_id)
        if not user:
            return AuthResult(False, "Account not found.")
        repository.upsert_auth_account(
            user_id=user.id, provider="github",
            provider_account_id=gh_id, access_token=access_token,
        )
    else:
        user = repository.find_user_by_email(gh_email)
        if not user:
            user = repository.create_user(
                email=gh_email, name=gh_name, password_hash=None
            )
            repository.update_user(user.id, email_verified=True)
        repository.upsert_auth_account(
            user_id=user.id, provider="github",
            provider_account_id=gh_id, access_token=access_token,
        )

    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    repository.create_session(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    repository.update_user(user.id, last_login_at=datetime.now(timezone.utc))
    return AuthResult(True, "Signed in with GitHub.", token=raw_token)
