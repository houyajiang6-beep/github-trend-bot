from __future__ import annotations

import logging
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import Settings, settings
from crawler import API_ROOT, GitHubTrendingCrawler


LOGGER = logging.getLogger("production-check")


def _check_secret_permissions(path: Path) -> bool:
    if os.name != "posix" or not path.exists():
        return True
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        LOGGER.error("敏感文件权限过宽：%s（当前 %03o，要求 600）", path, mode)
        return False
    LOGGER.info("敏感文件权限正常：%s (%03o)", path, mode)
    return True


def check_configuration(cfg: Settings) -> bool:
    ok = True
    for name, validator in (
        ("采集配置", cfg.validate_collection),
        ("DeepSeek 配置", cfg.validate_ai),
        ("Gmail 配置", cfg.validate_email),
    ):
        try:
            validator()
            LOGGER.info("%s：通过", name)
        except Exception as exc:
            LOGGER.error("%s：失败：%s", name, exc)
            ok = False

    if sys.version_info < (3, 11):
        LOGGER.warning(
            "Python %s.%s 低于生产建议版本 3.11",
            sys.version_info.major,
            sys.version_info.minor,
        )
    else:
        LOGGER.info("Python 版本：%s", sys.version.split()[0])

    for directory in (cfg.log_dir, cfg.report_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".production-write-check"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            LOGGER.info("目录可写：%s", directory)
        except OSError as exc:
            LOGGER.error("目录不可写：%s：%s", directory, exc)
            ok = False

    for path in (
        Path(__file__).resolve().parent / ".env",
        cfg.gmail_credentials_file,
        cfg.gmail_token_file,
    ):
        ok = _check_secret_permissions(path) and ok

    try:
        production_timeout = int(os.getenv("PRODUCTION_RUN_TIMEOUT", "1800"))
        if production_timeout <= 0:
            raise ValueError
        LOGGER.info("生产任务超时：%d 秒", production_timeout)
    except ValueError:
        LOGGER.error("PRODUCTION_RUN_TIMEOUT 必须是大于 0 的整数")
        ok = False
    alert_enabled = os.getenv("FAILURE_ALERT_ENABLED", "true").strip().lower()
    if alert_enabled not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
        LOGGER.error("FAILURE_ALERT_ENABLED 必须是 true 或 false")
        ok = False
    else:
        LOGGER.info("失败邮件提醒：%s", "启用" if alert_enabled in {"1", "true", "yes", "on"} else "关闭")
    return ok


def _log_anonymous_rate_limit(crawler: GitHubTrendingCrawler, cfg: Settings) -> None:
    headers = {
        key: value
        for key, value in crawler.api_headers.items()
        if key.lower() != "authorization"
    }
    try:
        response = crawler.session.get(
            f"{API_ROOT}/rate_limit",
            headers=headers,
            timeout=cfg.request_timeout,
        )
        if response.ok:
            core = response.json()["resources"]["core"]
            LOGGER.warning(
                "当前出口 IP 匿名 GitHub 额度：remaining=%s, used=%s, limit=%s",
                core.get("remaining", "<missing>"),
                core.get("used", "<missing>"),
                core.get("limit", "<missing>"),
            )
    except (requests.RequestException, KeyError, TypeError, ValueError):
        LOGGER.warning("匿名 GitHub rate limit 也无法查询")


def check_github_rate_limit(cfg: Settings) -> bool:
    crawler = GitHubTrendingCrawler(cfg)
    authorization = crawler.api_headers.get("Authorization", "")
    if cfg.github_token:
        if not authorization.startswith("Bearer "):
            LOGGER.error("GITHUB_TOKEN 已读取，但 Authorization Bearer header 未生成")
            return False
        LOGGER.info("GITHUB_TOKEN：已读取；API Authorization header：已启用")
    else:
        LOGGER.warning(
            "未配置 GITHUB_TOKEN；将使用匿名额度，容易在单次日报中耗尽"
        )

    try:
        response = crawler.session.get(
            f"{API_ROOT}/rate_limit",
            headers=crawler.api_headers,
            timeout=cfg.request_timeout,
        )
    except requests.RequestException as exc:
        LOGGER.error("GitHub rate limit 查询失败：%s", exc)
        return False

    if response.status_code == 401:
        LOGGER.error("GitHub Token 认证失败（HTTP 401），请重新创建或检查 GITHUB_TOKEN")
        _log_anonymous_rate_limit(crawler, cfg)
        return False
    if response.status_code == 403:
        LOGGER.error("GitHub API 拒绝请求或额度已耗尽（HTTP 403）")
        return False
    try:
        response.raise_for_status()
        core = response.json()["resources"]["core"]
        limit = int(core["limit"])
        remaining = int(core["remaining"])
        used = int(core["used"])
        reset_at = datetime.fromtimestamp(int(core["reset"]), timezone.utc)
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        LOGGER.error("GitHub rate limit 响应无法解析：%s", exc)
        return False

    LOGGER.info(
        "GitHub core rate limit：remaining=%d, used=%d, limit=%d, reset_utc=%s",
        remaining,
        used,
        limit,
        reset_at.isoformat(),
    )
    estimated_requests = cfg.trending_limit * 2 + 5
    if remaining < estimated_requests:
        LOGGER.warning(
            "GitHub 剩余额度 %d 低于单次完整采集预估 %d；程序仍可使用 Trending 页面数据降级",
            remaining,
            estimated_requests,
        )
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    configuration_ok = check_configuration(settings)
    github_ok = check_github_rate_limit(settings)
    if configuration_ok and github_ok:
        LOGGER.info("生产预检通过")
        return 0
    LOGGER.error("生产预检失败，请修复上述问题后再配置 cron")
    return 1


if __name__ == "__main__":
    sys.exit(main())
