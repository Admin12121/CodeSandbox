from __future__ import annotations

import html as _html
import json
import urllib.request

from codesandbox.config import get_settings


def _esc(value: str) -> str:
    return _html.escape(str(value))


def _send(*, to: str, subject: str, html: str) -> bool:
    settings = get_settings()
    if not settings.resend_api_key:
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
    except Exception:
        return False


_BASE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>CodeSandbox</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f4f4f5;padding:40px 16px">
  <tr><td align="center">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e4e4e7;overflow:hidden">
      <tr>
        <td style="padding:24px 36px;border-bottom:1px solid #f4f4f5">
          <table cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td style="background:#18181b;border-radius:8px;width:36px;height:36px;text-align:center;vertical-align:middle">
                <span style="color:#ffffff;font-weight:700;font-size:13px;line-height:36px">CS</span>
              </td>
              <td style="padding-left:10px;vertical-align:middle">
                <span style="font-weight:600;font-size:16px;color:#18181b">CodeSandbox</span>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr>
        <td style="padding:32px 36px">{content}</td>
      </tr>
      <tr>
        <td style="padding:16px 36px 24px;border-top:1px solid #f4f4f5;text-align:center">
          <p style="margin:0;font-size:12px;color:#a1a1aa">You received this because an action was taken on your CodeSandbox account.<br>If you didn't request this, you can safely ignore this email.</p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _btn(href: str, label: str, bg: str = "#18181b") -> str:
    safe = _esc(href)
    safe_label = _esc(label)
    return (
        f'<a href="{safe}" style="display:inline-block;background:{bg};color:#ffffff;'
        f'text-decoration:none;padding:11px 24px;border-radius:8px;font-weight:500;'
        f'font-size:14px;line-height:1">{safe_label}</a>'
    )


def _fallback_link(url: str) -> str:
    safe = _esc(url)
    return (
        f'<p style="margin:0;font-size:13px;color:#a1a1aa">'
        f'Or copy and paste this URL into your browser:<br>'
        f'<a href="{safe}" style="color:#3b82f6;word-break:break-all">{safe}</a>'
        f'</p>'
    )


def _detail_row(label: str, value: str, border: bool = True) -> str:
    border_style = "border-bottom:1px solid #f4f4f5;" if border else ""
    return (
        f'<tr>'
        f'<td style="padding:10px 16px;{border_style}background:#fafafa;white-space:nowrap">'
        f'<span style="font-size:12px;font-weight:600;color:#71717a;text-transform:uppercase;letter-spacing:.05em">{_esc(label)}</span>'
        f'</td>'
        f'<td style="padding:10px 16px;{border_style}">'
        f'<span style="font-size:14px;color:#18181b">{_esc(value)}</span>'
        f'</td>'
        f'</tr>'
    )


# ── Public API ────────────────────────────────────────────────────────────────

def send_email_verification(*, to: str, verify_url: str) -> bool:
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">Verify your email address</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        Click the button below to verify your email address. This link expires in
        <strong>24 hours</strong>.
      </p>
      <p style="margin:0 0 28px">{_btn(verify_url, "Verify email address")}</p>
      {_fallback_link(verify_url)}"""
    return _send(to=to, subject="Verify your CodeSandbox email", html=_BASE.format(content=content))


def send_password_reset(*, to: str, reset_url: str) -> bool:
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">Reset your password</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        Someone requested a password reset for your CodeSandbox account. Click the button below
        to choose a new password. This link expires in <strong>2 hours</strong>.
      </p>
      <p style="margin:0 0 28px">{_btn(reset_url, "Reset password")}</p>
      {_fallback_link(reset_url)}
      <p style="margin:16px 0 0;font-size:13px;color:#a1a1aa">
        If you didn't request this, your password won't be changed.
      </p>"""
    return _send(to=to, subject="Reset your CodeSandbox password", html=_BASE.format(content=content))


def send_new_device_login_alert(
    *,
    to: str,
    ip_address: str,
    user_agent: str,
    login_time: str,
    settings_url: str,
) -> bool:
    rows = (
        _detail_row("Time", login_time)
        + _detail_row("IP address", ip_address)
        + _detail_row(
            "Device",
            (user_agent[:100] + "…") if len(user_agent) > 100 else user_agent,
            border=False,
        )
    )
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">New sign-in detected</h1>
      <p style="margin:0 0 20px;font-size:14px;line-height:1.6;color:#71717a">
        We noticed a new sign-in to your CodeSandbox account. Here are the details:
      </p>
      <table cellpadding="0" cellspacing="0" role="presentation"
        style="width:100%;border:1px solid #e4e4e7;border-radius:8px;overflow:hidden;margin:0 0 24px">
        {rows}
      </table>
      <p style="margin:0 0 20px;font-size:14px;line-height:1.6;color:#71717a">
        If this was you, no action is needed. If you don't recognize this activity,
        secure your account immediately.
      </p>
      <p style="margin:0">{_btn(settings_url, "Review account security", "#dc2626")}</p>"""
    return _send(
        to=to,
        subject="New sign-in to your CodeSandbox account",
        html=_BASE.format(content=content),
    )


def send_otp(*, to: str, otp_code: str, expires_minutes: int = 10) -> bool:
    safe_code = _esc(otp_code)
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">Your verification code</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        Use the code below to complete verification. It expires in
        <strong>{expires_minutes} minutes</strong>.
      </p>
      <div style="margin:0 0 28px;padding:24px;background:#f4f4f5;border-radius:10px;text-align:center">
        <span style="font-family:'Courier New',Courier,monospace;font-size:36px;font-weight:700;letter-spacing:.25em;color:#18181b">{safe_code}</span>
      </div>
      <p style="margin:0;font-size:13px;color:#a1a1aa">
        Never share this code. CodeSandbox staff will never ask for it.
      </p>"""
    return _send(
        to=to,
        subject=f"Your CodeSandbox code: {otp_code}",
        html=_BASE.format(content=content),
    )


def send_org_invitation(
    *,
    to: str,
    org_name: str,
    invite_url: str,
    invited_by_name: str,
) -> bool:
    safe_by = _esc(invited_by_name)
    safe_org = _esc(org_name)
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">You've been invited</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        <strong>{safe_by}</strong> invited you to join <strong>{safe_org}</strong> on CodeSandbox.
        This invitation expires in <strong>7 days</strong>.
      </p>
      <p style="margin:0 0 28px">{_btn(invite_url, f"Join {safe_org}")}</p>
      {_fallback_link(invite_url)}"""
    return _send(
        to=to,
        subject=f"You've been invited to join {org_name} on CodeSandbox",
        html=_BASE.format(content=content),
    )


def send_org_verified(*, to: str, org_name: str, dashboard_url: str) -> bool:
    safe_org = _esc(org_name)
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">Organization verified</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        <strong>{safe_org}</strong> has been verified on CodeSandbox. You now have access
        to all verified organization features.
      </p>
      <p style="margin:0">{_btn(dashboard_url, "Go to dashboard")}</p>"""
    return _send(
        to=to,
        subject=f"{org_name} is now verified on CodeSandbox",
        html=_BASE.format(content=content),
    )


def send_member_added(
    *,
    to: str,
    org_name: str,
    added_by_name: str,
    role: str,
    dashboard_url: str,
) -> bool:
    safe_org = _esc(org_name)
    safe_by = _esc(added_by_name)
    safe_role = _esc(role)
    content = f"""\
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">You've been added to {safe_org}</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        <strong>{safe_by}</strong> added you to <strong>{safe_org}</strong> as a
        <strong>{safe_role}</strong>. Head to your dashboard to get started.
      </p>
      <p style="margin:0">{_btn(dashboard_url, f"View {safe_org}")}</p>"""
    return _send(
        to=to,
        subject=f"You've been added to {org_name} on CodeSandbox",
        html=_BASE.format(content=content),
    )
