"""The email that gives a new customer their login details."""

# ruff: noqa: E501 - inline-styled HTML (mail clients ignore <style>) reads best unwrapped.

from html import escape

from app.services.mailer import Mail


def credentials_mail(
    *, to: str, first_name: str, user_id: str, password: str, sign_in_url: str
) -> Mail:
    """User id, temporary password and where to sign in.

    Every value is escaped in the HTML part: the name comes from the BD's form.
    """
    text = f"""Hello {first_name},

Your LAS account has been created by your Business Dealer.

Sign in at: {sign_in_url}
User ID: {user_id}
Temporary password: {password}

If you want, you can change your password after you sign in, using
"Reset password" on the sign-in page.

Please keep these details private. LAS will never ask you for your password.

Regards,
Team LAS
"""
    e = {
        "name": escape(first_name),
        "user": escape(user_id),
        "password": escape(password),
        "url": escape(sign_in_url, quote=True),
    }
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a">
  <table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px">
    <tr><td>
      <p style="margin:0 0 16px;font-size:16px">Hello {e["name"]},</p>
      <p style="margin:0 0 20px;font-size:15px;line-height:1.5">Your LAS account has been created by your Business Associate. Use these details to sign in:</p>
      <table role="presentation" style="width:100%;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;font-size:15px">
        <tr><td style="padding:4px 0;color:#475569;width:160px">User ID</td><td style="padding:4px 0;font-weight:bold">{e["user"]}</td></tr>
        <tr><td style="padding:4px 0;color:#475569">Temporary password</td><td style="padding:4px 0;font-weight:bold;font-family:Consolas,monospace">{e["password"]}</td></tr>
      </table>
      <p style="margin:24px 0">
        <a href="{e["url"]}" style="display:inline-block;background:#1f6b55;color:#ffffff;text-decoration:none;font-weight:bold;padding:12px 22px;border-radius:10px">Sign in to LAS</a>
      </p>
      <p style="margin:0 0 12px;font-size:14px;line-height:1.5">If you want, you can change your password after you sign in, using <b>Reset password</b> on the sign-in page.</p>
      <p style="margin:0 0 20px;font-size:13px;line-height:1.5;color:#475569">Please keep these details private. LAS will never ask you for your password.</p>
      <p style="margin:0;font-size:14px">Regards,<br>Team LAS</p>
    </td></tr>
  </table>
</body></html>
"""
    return Mail(to=to, subject="Your LAS login details", text=text, html=html)
