from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default)).expanduser()
    return value if value.is_absolute() else BASE_DIR / value


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


def _daily_pipeline_enabled() -> bool:
    """Use the unified flag while honoring the old migration flag."""
    if "ENABLE_DAILY_CONTENT_PIPELINE" in os.environ:
        return _bool_env("ENABLE_DAILY_CONTENT_PIPELINE", True)
    return _bool_env("ENABLE_HUMAN_VALUE_AGENT", True)


@dataclass(frozen=True)
class Settings:
    ai_provider: str = os.getenv("AI_PROVIDER", "deepseek").strip().lower()
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_base_url: str = os.getenv(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
    ).strip().rstrip("/")
    deepseek_model: str = os.getenv(
        "DEEPSEEK_MODEL", "deepseek-v4-flash"
    ).strip()
    deepseek_thinking: bool = _bool_env("DEEPSEEK_THINKING", False)
    ai_request_timeout: int = _int_env("AI_REQUEST_TIMEOUT", 120)
    ai_max_retries: int = _int_env("AI_MAX_RETRIES", 3)
    enable_daily_content_pipeline: bool = _daily_pipeline_enabled()
    github_token: str = os.getenv("GITHUB_TOKEN", "").strip()
    email_from: str = os.getenv("EMAIL_FROM", "").strip()
    email_to: str = os.getenv("EMAIL_TO", "").strip()
    gmail_credentials_file: Path = _path_from_env(
        "GMAIL_CREDENTIALS_FILE", "credentials.json"
    )
    gmail_token_file: Path = _path_from_env("GMAIL_TOKEN_FILE", "token.json")
    trending_limit: int = _int_env("TRENDING_LIMIT", 25)
    readme_max_chars: int = _int_env("README_MAX_CHARS", 6000)
    request_timeout: int = _int_env("REQUEST_TIMEOUT", 25)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    report_timezone: str = os.getenv("REPORT_TIMEZONE", "Asia/Shanghai")
    log_dir: Path = _path_from_env("LOG_DIR", "logs")
    report_dir: Path = _path_from_env("REPORT_DIR", "reports")
    creator_output_dir: Path = _path_from_env("CREATOR_OUTPUT_DIR", "outputs")

    def validate_collection(self) -> None:
        if not 10 <= self.trending_limit <= 50:
            raise ValueError("TRENDING_LIMIT 必须在 10 到 50 之间")
        if self.readme_max_chars < 1000:
            raise ValueError("README_MAX_CHARS 不能小于 1000")

    def validate_ai(self) -> None:
        if self.ai_provider != "deepseek":
            raise ValueError("AI_PROVIDER 目前仅支持 deepseek")
        if not self.deepseek_api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置；或使用 --skip-ai")
        if not self.deepseek_base_url:
            raise ValueError("DEEPSEEK_BASE_URL 不能为空")
        if "api.openai.com" in self.deepseek_base_url.lower():
            raise ValueError("DEEPSEEK_BASE_URL 不得指向 OpenAI API")
        if not self.deepseek_model:
            raise ValueError("DEEPSEEK_MODEL 不能为空")
        if self.ai_request_timeout <= 0:
            raise ValueError("AI_REQUEST_TIMEOUT 必须大于 0")
        if not 0 <= self.ai_max_retries <= 3:
            raise ValueError("AI_MAX_RETRIES 必须在 0 到 3 之间")

    def validate_email(self) -> None:
        missing = []
        if not self.email_from:
            missing.append("EMAIL_FROM")
        if not self.email_to:
            missing.append("EMAIL_TO")
        if not self.gmail_credentials_file.exists():
            missing.append(str(self.gmail_credentials_file))
        if not self.gmail_token_file.exists():
            missing.append(str(self.gmail_token_file))
        if missing:
            raise ValueError("邮件配置缺失: " + ", ".join(missing))


settings = Settings()
