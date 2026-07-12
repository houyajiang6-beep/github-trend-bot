from __future__ import annotations

import os

from google_auth_oauthlib.flow import InstalledAppFlow

from config import settings
from email_sender import SCOPES


def main() -> None:
    if not settings.gmail_credentials_file.exists():
        raise SystemExit(f"找不到 OAuth 客户端文件：{settings.gmail_credentials_file}")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.gmail_credentials_file), SCOPES
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        success_message="Gmail 授权成功，可以关闭此页面。",
    )
    settings.gmail_token_file.write_text(credentials.to_json(), encoding="utf-8")
    try:
        os.chmod(settings.gmail_token_file, 0o600)
    except OSError:
        pass
    print(f"授权成功，令牌已保存到 {settings.gmail_token_file}")


if __name__ == "__main__":
    main()

