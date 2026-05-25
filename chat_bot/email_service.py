import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

_SMTP_HOST = os.environ.get("SMTP_HOST", "")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASS = os.environ.get("SMTP_PASS", "")
_EMAIL_FROM = os.environ.get("EMAIL_FROM", "") or _SMTP_USER
_APP_URL = os.environ.get("APP_URL", "https://your-app.com").rstrip("/")


def _send_email(to: str, subject: str, html_body: str) -> bool:
    if not (_SMTP_HOST and _SMTP_USER and _SMTP_PASS):
        logger.warning("[email] SMTP not configured — skipping send to %s: %s", to, subject)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _EMAIL_FROM
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as srv:
            srv.ehlo()
            srv.starttls(context=ctx)
            srv.login(_SMTP_USER, _SMTP_PASS)
            srv.sendmail(_EMAIL_FROM, [to], msg.as_string())
        logger.info("[email] sent to %s: %s", to, subject)
        return True
    except Exception:
        logger.exception("[email] failed to send to %s", to)
        return False


def send_password_reset_email(to: str, username: str, raw_token: str) -> bool:
    link = f"{_APP_URL}/reset-password?token={raw_token}"
    subject = "【紫微AI命理】密碼重置"
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:40px auto;padding:0 20px">
<div style="background:#0a1628;padding:24px;border-radius:12px;text-align:center;margin-bottom:24px">
  <h1 style="color:#c9a227;margin:0;font-size:1.4rem">紫微AI命理</h1>
</div>
<h2 style="color:#1a2a4a">密碼重置</h2>
<p>您好，<strong>{username}</strong>，</p>
<p>我們收到您的密碼重置請求。請點擊以下按鈕重置密碼（<strong>1小時內有效</strong>）：</p>
<p style="text-align:center;margin:32px 0">
  <a href="{link}" style="background:#c9a227;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block">重置密碼</a>
</p>
<p>若按鈕無法點擊，請複製以下網址至瀏覽器：</p>
<p style="word-break:break-all;color:#666;font-size:0.85rem">{link}</p>
<p>如果您沒有申請重置密碼，請忽略此郵件，您的帳號不會有任何變更。</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0">
<p style="color:#999;font-size:0.8rem">紫微AI命理 | 此為系統自動發送，請勿回覆</p>
</body></html>"""
    return _send_email(to, subject, html)


def send_email_verification(to: str, username: str, verify_token: str) -> bool:
    link = f"{_APP_URL}/api/auth/verify-email?token={verify_token}"
    subject = "【紫微AI命理】驗證您的電子郵件"
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:40px auto;padding:0 20px">
<div style="background:#0a1628;padding:24px;border-radius:12px;text-align:center;margin-bottom:24px">
  <h1 style="color:#c9a227;margin:0;font-size:1.4rem">紫微AI命理</h1>
</div>
<h2 style="color:#1a2a4a">電子郵件驗證</h2>
<p>您好，<strong>{username}</strong>，</p>
<p>請點擊以下按鈕驗證您的電子郵件地址，以便接收密碼重置通知：</p>
<p style="text-align:center;margin:32px 0">
  <a href="{link}" style="background:#c9a227;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block">驗證電子郵件</a>
</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0">
<p style="color:#999;font-size:0.8rem">紫微AI命理 | 此為系統自動發送，請勿回覆</p>
</body></html>"""
    return _send_email(to, subject, html)


def send_payment_receipt(to: str, username: str, plan: str, amount: int, expires_at: str) -> bool:
    plan_names = {"standard": "標準月費方案", "pro": "專業年費方案"}
    plan_name = plan_names.get(plan, plan)
    subject = f"【紫微AI命理】訂閱成功 — {plan_name}"
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:40px auto;padding:0 20px">
<div style="background:#0a1628;padding:24px;border-radius:12px;text-align:center;margin-bottom:24px">
  <h1 style="color:#c9a227;margin:0;font-size:1.4rem">紫微AI命理</h1>
</div>
<h2 style="color:#1a2a4a">訂閱確認</h2>
<p>您好，<strong>{username}</strong>，</p>
<p>感謝您訂閱 <strong>{plan_name}</strong>！您的方案詳情如下：</p>
<table style="border-collapse:collapse;width:100%;margin:16px 0">
  <tr style="background:#f8f6f0"><td style="padding:10px 16px;border:1px solid #ddd;color:#666">方案</td><td style="padding:10px 16px;border:1px solid #ddd;font-weight:bold">{plan_name}</td></tr>
  <tr><td style="padding:10px 16px;border:1px solid #ddd;color:#666">金額</td><td style="padding:10px 16px;border:1px solid #ddd">NT$ {amount}</td></tr>
  <tr style="background:#f8f6f0"><td style="padding:10px 16px;border:1px solid #ddd;color:#666">有效期限</td><td style="padding:10px 16px;border:1px solid #ddd">{expires_at[:10]}</td></tr>
</table>
<p>即日起您可享受更多AI命理諮詢服務，感謝您的支持！</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0">
<p style="color:#999;font-size:0.8rem">紫微AI命理 | 此為系統自動發送，請勿回覆</p>
</body></html>"""
    return _send_email(to, subject, html)
