from __future__ import annotations

import base64
import logging
import os
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import Settings


LOGGER = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _credentials(cfg: Settings) -> Credentials:
    creds = Credentials.from_authorized_user_file(str(cfg.gmail_token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        cfg.gmail_token_file.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(cfg.gmail_token_file, 0o600)
        except OSError:
            pass
    if not creds.valid:
        raise RuntimeError("Gmail OAuth 凭据无效或已撤销，请重新运行 auth_gmail.py")
    return creds


def send_email(subject: str, plain_text: str, html_body: str, cfg: Settings) -> str:
    cfg.validate_email()
    message = EmailMessage()
    message["To"] = cfg.email_to
    message["From"] = cfg.email_from
    message["Subject"] = subject
    message.set_content(plain_text)
    message.add_alternative(html_body, subtype="html")
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    service = build("gmail", "v1", credentials=_credentials(cfg), cache_discovery=False)
    result = service.users().messages().send(userId="me", body={"raw": encoded}).execute()
    message_id = str(result.get("id", ""))
    LOGGER.info("邮件发送成功，Gmail message id=%s", message_id)
    return message_id

