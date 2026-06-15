from __future__ import annotations

from datetime import datetime, timezone

from nexorm.exceptions import DoesNotExist

from .models import ApiKey, LoginAttempt, Session, User


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
