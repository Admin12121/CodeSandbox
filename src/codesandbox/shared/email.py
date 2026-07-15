from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from codesandbox.config import get_settings

_logger = logging.getLogger(__name__)
_TMPL_DIR = os.path.join(os.path.dirname(__file__), "emails")
_env = Environment(
    loader=FileSystemLoader(_TMPL_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(name: str, **ctx: object) -> str:
    return _env.get_template(name).render(now=datetime.now(timezone.utc), **ctx)


def _send(*, to: str, subject: str, html: str) -> bool:
    settings = get_settings()
    if not settings.resend_api_key:
        _logger.warning("Email to %s not sent: RESEND_APIKEY is not configured.", to)
        return False
    payload = json.dumps({
        "from": settings.from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        _logger.error("Resend API rejected email to %s (HTTP %s): %s", to, exc.code, body)
        return False
    except Exception as exc:
        _logger.error("Failed to send email to %s: %s", to, exc)
        return False


# ── Identity ──────────────────────────────────────────────────────────────────

def send_email_verification(*, to: str, verify_url: str) -> bool:
    return _send(
        to=to,
        subject="Verify your CodeSandbox email",
        html=_render("verify_email.html", verify_url=verify_url),
    )


def send_password_reset(*, to: str, reset_url: str) -> bool:
    return _send(
        to=to,
        subject="Reset your CodeSandbox password",
        html=_render("password_reset.html", reset_url=reset_url),
    )


def send_new_device_login_alert(
    *,
    to: str,
    ip_address: str,
    user_agent: str,
    login_time: str,
    settings_url: str,
) -> bool:
    ua = (user_agent[:100] + "…") if len(user_agent) > 100 else user_agent
    return _send(
        to=to,
        subject="New sign-in to your CodeSandbox account",
        html=_render(
            "new_device_login.html",
            ip_address=ip_address,
            user_agent=ua,
            login_time=login_time,
            settings_url=settings_url,
        ),
    )


def send_otp(*, to: str, otp_code: str, expires_minutes: int = 10) -> bool:
    return _send(
        to=to,
        subject=f"Your CodeSandbox verification code: {otp_code}",
        html=_render("otp.html", otp_code=otp_code, expires_minutes=expires_minutes),
    )


# ── Organizations ─────────────────────────────────────────────────────────────

def send_org_invitation(
    *,
    to: str,
    org_name: str,
    invite_url: str,
    invited_by_name: str,
) -> bool:
    return _send(
        to=to,
        subject=f"You've been invited to join {org_name} on CodeSandbox",
        html=_render(
            "org_invitation.html",
            org_name=org_name,
            invite_url=invite_url,
            invited_by_name=invited_by_name,
        ),
    )


def send_org_approved(*, to: str, org_name: str, dashboard_url: str) -> bool:
    return _send(
        to=to,
        subject=f"{org_name} has been approved on CodeSandbox",
        html=_render("org_approved.html", org_name=org_name, dashboard_url=dashboard_url),
    )


def send_org_rejected(
    *,
    to: str,
    org_name: str,
    reason: str | None = None,
    support_url: str,
) -> bool:
    return _send(
        to=to,
        subject="Your CodeSandbox organization application was not approved",
        html=_render(
            "org_rejected.html",
            org_name=org_name,
            reason=reason,
            support_url=support_url,
        ),
    )


def send_org_suspended(
    *,
    to: str,
    org_name: str,
    reason: str | None = None,
    support_url: str,
) -> bool:
    return _send(
        to=to,
        subject=f"Your CodeSandbox organization {org_name} has been suspended",
        html=_render(
            "org_suspended.html",
            org_name=org_name,
            reason=reason,
            support_url=support_url,
        ),
    )


def send_member_added(
    *,
    to: str,
    org_name: str,
    added_by_name: str,
    role: str,
    dashboard_url: str,
) -> bool:
    return _send(
        to=to,
        subject=f"You've been added to {org_name} on CodeSandbox",
        html=_render(
            "member_added.html",
            org_name=org_name,
            added_by_name=added_by_name,
            role=role,
            dashboard_url=dashboard_url,
        ),
    )


def send_member_removed(*, to: str, org_name: str, removed_by_name: str) -> bool:
    return _send(
        to=to,
        subject=f"You've been removed from {org_name} on CodeSandbox",
        html=_render(
            "member_removed.html",
            org_name=org_name,
            removed_by_name=removed_by_name,
        ),
    )


# ── Platform admin ────────────────────────────────────────────────────────────

def send_staff_account_created(
    *,
    to: str,
    name: str,
    login_url: str,
    temp_password: str | None = None,
) -> bool:
    return _send(
        to=to,
        subject="Your CodeSandbox staff account is ready",
        html=_render(
            "staff_account_created.html",
            name=name,
            login_url=login_url,
            temp_password=temp_password,
        ),
    )


def send_platform_role_changed(
    *,
    to: str,
    name: str,
    new_role: str,
    changed_by_name: str,
    dashboard_url: str,
) -> bool:
    return _send(
        to=to,
        subject="Your CodeSandbox platform role has been updated",
        html=_render(
            "platform_role_changed.html",
            name=name,
            new_role=new_role,
            changed_by_name=changed_by_name,
            dashboard_url=dashboard_url,
        ),
    )


def send_account_status_changed(
    *,
    to: str,
    name: str,
    new_status: str,
    reason: str | None = None,
    support_url: str | None = None,
) -> bool:
    _subjects = {
        "active":   "Your CodeSandbox account has been reactivated",
        "inactive": "Your CodeSandbox account has been deactivated",
        "banned":   "Your CodeSandbox account has been suspended",
    }
    subject = _subjects.get(new_status, "Your CodeSandbox account status has changed")
    return _send(
        to=to,
        subject=subject,
        html=_render(
            "account_status_changed.html",
            name=name,
            new_status=new_status,
            reason=reason,
            support_url=support_url,
        ),
    )
