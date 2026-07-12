from __future__ import annotations

import html
import logging
import logging.handlers
import os
import platform
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import BASE_DIR, settings
from email_sender import send_email


LOGGER = logging.getLogger("production-runner")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "on"}


def setup_logging() -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    LOGGER.setLevel(getattr(logging, settings.log_level, logging.INFO))
    LOGGER.handlers.clear()
    LOGGER.propagate = False
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOGGER.addHandler(console)
    rotating = logging.handlers.RotatingFileHandler(
        settings.log_dir / "production-runner.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=14,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)
    LOGGER.addHandler(rotating)


def send_failure_alert(exit_code: int, elapsed_seconds: float, reason: str) -> bool:
    if not _env_bool("FAILURE_ALERT_ENABLED", True):
        LOGGER.warning("失败提醒已通过 FAILURE_ALERT_ENABLED 关闭")
        return False
    recipient = os.getenv("FAILURE_ALERT_TO", "").strip() or settings.email_to
    alert_settings = replace(settings, email_to=recipient)
    now = datetime.now(ZoneInfo(settings.report_timezone))
    subject = f"【GitHub趋势日报】生产任务失败 {now:%Y-%m-%d %H:%M}"
    plain_text = (
        "GitHub 趋势日报生产任务执行失败。\n"
        f"主机：{platform.node()}\n"
        f"退出码：{exit_code}\n"
        f"耗时：{elapsed_seconds:.1f} 秒\n"
        f"原因：{reason}\n"
        f"应用日志：{settings.log_dir / 'github-trend-bot.log'}\n"
        f"运行器日志：{settings.log_dir / 'production-runner.log'}\n"
    )
    html_body = (
        "<h1>GitHub 趋势日报生产任务失败</h1>"
        f"<p><b>主机：</b>{html.escape(platform.node())}</p>"
        f"<p><b>退出码：</b>{exit_code}</p>"
        f"<p><b>耗时：</b>{elapsed_seconds:.1f} 秒</p>"
        f"<p><b>原因：</b>{html.escape(reason)}</p>"
        "<p>请登录服务器检查应用日志和运行器日志。</p>"
    )
    try:
        send_email(subject, plain_text, html_body, alert_settings)
        LOGGER.info("失败提醒已发送至 %s", recipient)
        return True
    except Exception as exc:
        LOGGER.exception("失败提醒发送失败：%s", exc)
        return False


def run_production(command: list[str] | None = None) -> int:
    started = time.monotonic()
    try:
        timeout = int(os.getenv("PRODUCTION_RUN_TIMEOUT", "1800"))
        if timeout <= 0:
            raise ValueError
    except ValueError:
        reason = "PRODUCTION_RUN_TIMEOUT 必须是大于 0 的整数"
        LOGGER.error(reason)
        send_failure_alert(2, 0, reason)
        return 2
    command = command or [sys.executable, str(BASE_DIR / "main.py")]
    LOGGER.info(
        "生产任务开始：host=%s pid=%d timeout=%ds",
        platform.node(),
        os.getpid(),
        timeout,
    )
    try:
        result = subprocess.run(
            command,
            cwd=BASE_DIR,
            check=False,
            timeout=timeout,
        )
        elapsed = time.monotonic() - started
        if result.returncode == 0:
            LOGGER.info("生产任务成功：elapsed=%.1fs", elapsed)
            return 0
        reason = f"main.py 返回非零退出码 {result.returncode}"
        LOGGER.error("生产任务失败：%s，elapsed=%.1fs", reason, elapsed)
        send_failure_alert(result.returncode, elapsed, reason)
        return result.returncode
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        reason = f"运行超过 {timeout} 秒，已终止"
        LOGGER.error("生产任务超时：%s", reason)
        send_failure_alert(124, elapsed, reason)
        return 124
    except Exception as exc:
        elapsed = time.monotonic() - started
        reason = f"运行器异常：{exc.__class__.__name__}"
        LOGGER.exception("生产运行器异常")
        send_failure_alert(1, elapsed, reason)
        return 1


def main() -> int:
    setup_logging()
    return run_production()


if __name__ == "__main__":
    sys.exit(main())
