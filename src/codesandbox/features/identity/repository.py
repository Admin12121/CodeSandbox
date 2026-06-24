from __future__ import annotations

from datetime import datetime, timezone

from nexorm.exceptions import DoesNotExist

from .models import ApiKey, AuthAccount, LoginAttempt, Session, TwoFactorMethod, User, VerificationToken


def find_user_by_email(email: str) -> User | None:
    try:
        return User.objects.filter(email=email, deleted_at__isnull=True).first()
    except Exception:
        return None


def find_user_by_id(user_id: str) -> User | None:
    try:
        return User.objects.get(id=user_id)
    except DoesNotExist:
        return None


def create_user(email: str, name: str, password_hash: str) -> User:
    user = User(email=email, name=name, password_hash=password_hash)
    user.save()
    return user


def update_user(user_id: str, **kwargs) -> User | None:
    user = find_user_by_id(user_id)
    if user is None:
        return None
    for key, value in kwargs.items():
        setattr(user, key, value)
    user.updated_at = datetime.now(timezone.utc)
    user.save()
    return user


def find_active_session(token_hash: str) -> Session | None:
    try:
        now = datetime.now(timezone.utc)
        session = Session.objects.filter(token_hash=token_hash).first()
        if session is None:
            return None
        expires = session.expires_at
        if expires.tzinfo is None:
            from datetime import timezone as tz
            expires = expires.replace(tzinfo=tz.utc)
        if expires < now:
            return None
        return session
    except Exception:
        return None


def create_session(
    *,
    user_id: str,
    token_hash: str,
    expires_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
) -> Session:
    session = Session(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.save()
    return session


def delete_session(token_hash: str) -> None:
    session = Session.objects.filter(token_hash=token_hash).first()
    if session:
        session.delete()


def record_login_attempt(
    *,
    email: str | None,
    ip_address: str | None,
    succeeded: bool,
    failure_reason: str | None = None,
) -> None:
    attempt = LoginAttempt(
        email=email,
        ip_address=ip_address,
        succeeded=succeeded,
        failure_reason=failure_reason,
    )
    attempt.save()


# ── Verification tokens ──────────────────────────────────────────────────────

def create_verification_token(
    *,
    user_id: str | None,
    identifier: str,
    token_hash: str,
    purpose: str,
    expires_at: datetime,
) -> VerificationToken:
    VerificationToken.objects.filter(identifier=identifier, purpose=purpose, used_at__isnull=True).delete() if False else None
    # expire old tokens for this identifier+purpose
    for old in VerificationToken.objects.filter(identifier=identifier, purpose=purpose).all():
        old.delete()
    vt = VerificationToken(
        user_id=user_id,
        identifier=identifier,
        token_hash=token_hash,
        purpose=purpose,
        expires_at=expires_at,
    )
    vt.save()
    return vt


def find_verification_token(token_hash: str) -> VerificationToken | None:
    try:
        return VerificationToken.objects.filter(token_hash=token_hash).first()
    except Exception:
        return None


def consume_verification_token(token: VerificationToken) -> None:
    token.used_at = datetime.now(timezone.utc)
    token.save()


# ── Auth accounts (OAuth) ─────────────────────────────────────────────────────

def find_auth_account(provider: str, provider_account_id: str) -> AuthAccount | None:
    try:
        return AuthAccount.objects.filter(provider=provider, provider_account_id=provider_account_id).first()
    except Exception:
        return None


def upsert_auth_account(
    *,
    user_id: str,
    provider: str,
    provider_account_id: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> AuthAccount:
    existing = find_auth_account(provider, provider_account_id)
    if existing:
        existing.access_token = access_token
        existing.refresh_token = refresh_token
        existing.updated_at = datetime.now(timezone.utc)
        existing.save()
        return existing
    account = AuthAccount(
        user_id=user_id,
        provider=provider,
        provider_account_id=provider_account_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    account.save()
    return account


# ── Two-factor methods ────────────────────────────────────────────────────────

def get_totp_method(user_id: str) -> TwoFactorMethod | None:
    try:
        return TwoFactorMethod.objects.filter(user_id=user_id, method_type="totp").first()
    except Exception:
        return None


def upsert_totp_method(
    *,
    user_id: str,
    secret_encrypted: str,
    is_enabled: bool = False,
    backup_codes_encrypted: str | None = None,
) -> TwoFactorMethod:
    existing = get_totp_method(user_id)
    if existing:
        existing.secret_encrypted = secret_encrypted
        existing.is_enabled = is_enabled
        if backup_codes_encrypted is not None:
            existing.backup_codes_encrypted = backup_codes_encrypted
        existing.updated_at = datetime.now(timezone.utc)
        existing.save()
        return existing
    method = TwoFactorMethod(
        user_id=user_id,
        method_type="totp",
        secret_encrypted=secret_encrypted,
        is_enabled=is_enabled,
        backup_codes_encrypted=backup_codes_encrypted,
    )
    method.save()
    return method


def enable_totp(user_id: str, backup_codes_encrypted: str | None = None) -> None:
    method = get_totp_method(user_id)
    if method:
        method.is_enabled = True
        method.verified_at = datetime.now(timezone.utc)
        if backup_codes_encrypted:
            method.backup_codes_encrypted = backup_codes_encrypted
        method.updated_at = datetime.now(timezone.utc)
        method.save()


def disable_totp(user_id: str) -> None:
    method = get_totp_method(user_id)
    if method:
        method.is_enabled = False
        method.updated_at = datetime.now(timezone.utc)
        method.save()


def get_user_auth_accounts(user_id: str) -> list[AuthAccount]:
    return AuthAccount.objects.filter(user_id=user_id).all()


def delete_auth_account_by_provider(user_id: str, provider: str) -> None:
    for acc in AuthAccount.objects.filter(user_id=user_id, provider=provider).all():
        acc.delete()


def list_user_sessions(user_id: str) -> list[Session]:
    try:
        return Session.objects.filter(user_id=user_id).order_by("-created_at").all()
    except Exception:
        return []


def find_session_by_id(session_id: str) -> Session | None:
    try:
        return Session.objects.filter(id=session_id).first()
    except Exception:
        return None


def delete_session_by_id(session_id: str) -> None:
    try:
        s = Session.objects.filter(id=session_id).first()
        if s:
            s.delete()
    except Exception:
        pass


def list_users(
    *,
    search: str | None = None,
    role: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[User], int]:
    qs = User.objects.filter(deleted_at__isnull=True)
    if role and role != "all":
        qs = qs.filter(platform_role=role)
    if status and status not in ("all", ""):
        if status == "active":
            qs = qs.filter(status="active")
        elif status == "inactive":
            qs = qs.filter(status="inactive")
        elif status == "banned":
            qs = qs.filter(status="banned")
    all_users = qs.order_by("-created_at").all()
    if search:
        q = search.lower()
        all_users = [
            u for u in all_users
            if q in (u.name or "").lower()
            or q in (u.email or "").lower()
        ]
    total = len(all_users)
    offset = (page - 1) * page_size
    return all_users[offset : offset + page_size], total
