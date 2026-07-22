from __future__ import annotations

import html as _html
import json
import urllib.request

from codesandbox.config import get_settings


def _esc(value: str) -> str:
    """HTML-escape user-controlled values before embedding in email bodies."""
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


_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f4f4f5;padding:40px 16px">
  <tr><td align="center">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e4e4e7;overflow:hidden">
      <tr>
        <td style="padding:28px 36px 20px;border-bottom:1px solid #f4f4f5">
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


def _btn(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;background:#18181b;color:#ffffff;'
        f'text-decoration:none;padding:11px 24px;border-radius:8px;font-weight:500;'
        f'font-size:14px;line-height:1">{label}</a>'
    )


def send_password_reset(*, to: str, reset_url: str) -> bool:
    content = f"""
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">Reset your password</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        Someone requested a password reset for your CodeSandbox account.<br>
        Click the button below to choose a new password. This link expires in <strong>2 hours</strong>.
      </p>
      <p style="margin:0 0 28px">{_btn(reset_url, 'Reset password')}</p>
      <p style="margin:0;font-size:13px;color:#a1a1aa">
        Or copy and paste this URL into your browser:<br>
        <a href="{reset_url}" style="color:#3b82f6;word-break:break-all">{reset_url}</a>
      </p>
    """
    return _send(to=to, subject="Reset your CodeSandbox password", html=_BASE.format(content=content))


def send_email_verification(*, to: str, verify_url: str) -> bool:
    content = f"""
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">Verify your email</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        Thanks for signing up for CodeSandbox. Click the button below to verify your email address.
        This link expires in <strong>24 hours</strong>.
      </p>
      <p style="margin:0 0 28px">{_btn(verify_url, 'Verify email address')}</p>
      <p style="margin:0;font-size:13px;color:#a1a1aa">
        Or copy and paste this URL into your browser:<br>
        <a href="{verify_url}" style="color:#3b82f6;word-break:break-all">{verify_url}</a>
      </p>
    """
    return _send(to=to, subject="Verify your CodeSandbox email", html=_BASE.format(content=content))


def send_org_invitation(
    *,
    to: str,
    org_name: str,
    invite_url: str,
    invited_by_name: str,
) -> bool:
    safe_by = _esc(invited_by_name)
    safe_org = _esc(org_name)
    content = f"""
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#18181b">You've been invited</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#71717a">
        <strong>{safe_by}</strong> has invited you to join the organization
        <strong>{safe_org}</strong> on CodeSandbox.<br>
        Click the button below to accept the invitation. This link expires in <strong>7 days</strong>.
      </p>
      <p style="margin:0 0 28px">{_btn(invite_url, 'Accept invitation')}</p>
      <p style="margin:0;font-size:13px;color:#a1a1aa">
        Or copy and paste this URL into your browser:<br>
        <a href="{invite_url}" style="color:#3b82f6;word-break:break-all">{_esc(invite_url)}</a>
      </p>
    """
    return _send(
        to=to,
        subject=f"You've been invited to {safe_org} on CodeSandbox",
        html=_BASE.format(content=content),
    )
