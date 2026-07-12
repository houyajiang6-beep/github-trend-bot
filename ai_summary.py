from __future__ import annotations

import html
import json
import logging
import time
from datetime import date
from typing import Any

from config import Settings
from crawler import Repository


LOGGER = logging.getLogger(__name__)


class DeepSeekAPIError(RuntimeError):
    """A sanitized, user-facing DeepSeek request failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str = "DeepSeekAPIError",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def _error_details(exc: Exception) -> tuple[str, bool]:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return "DeepSeek 认证失败，请检查 DEEPSEEK_API_KEY", False
    if status_code == 402:
        return "DeepSeek API 余额不足，请充值或检查账户余额", False
    if status_code == 429:
        return "DeepSeek API 请求限流，请稍后重试", True
    if isinstance(status_code, int) and status_code >= 500:
        return f"DeepSeek 服务暂时不可用（HTTP {status_code}）", True
    if isinstance(exc, ValueError) and "DEEPSEEK_API_KEY" in str(exc):
        return "DeepSeek 配置缺少 DEEPSEEK_API_KEY", False
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "DeepSeek 返回的日报内容不是有效 JSON", True
    if isinstance(exc, (TimeoutError, ConnectionError)) or exc.__class__.__name__ in {
        "APITimeoutError",
        "APIConnectionError",
    }:
        return "连接 DeepSeek API 超时或网络不可用", True
    return "DeepSeek API 请求失败", True


def deepseek_error_summary(exc: Exception) -> str:
    """Return log-safe failure metadata without including API response bodies."""
    status_code = getattr(exc, "status_code", None)
    error_type = getattr(exc, "error_type", exc.__class__.__name__)
    message, _ = _error_details(exc)
    if isinstance(exc, DeepSeekAPIError):
        message = str(exc)
    status = status_code if isinstance(status_code, int) else "unknown"
    return f"type={error_type} http_status={status} message={message}"


def _parse_response_content(response: Any) -> dict[str, Any]:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("DeepSeek 响应缺少 message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek 响应内容为空")
    content = content.strip()
    if content.startswith("```json") and content.endswith("```"):
        content = content[7:-3].strip()
    elif content.startswith("```") and content.endswith("```"):
        content = content[3:-3].strip()
    analysis = json.loads(content)
    if not isinstance(analysis, dict):
        raise ValueError("DeepSeek 响应 JSON 顶层必须是对象")
    return analysis


def analyze_repositories(
    repositories: list[Repository], cfg: Settings,
    growth_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg.validate_ai()

    # Lazy import keeps `--skip-ai` independent from AI client initialization.
    from openai import OpenAI

    growth_metrics = growth_metrics or {}
    payload = [
        {**repo.to_dict(), "growth_metrics": growth_metrics.get(repo.full_name, {})}
        for repo in repositories
    ]
    prompt = f"""
你是资深开源技术分析师。根据下方 GitHub Trending 当日数据，生成中文日报分析。

规则：
1. 从输入项目中选出最值得关注的 10 个，兼顾热度、今日新增 Star、README 信息和以下重点：AI、LLM、Agent、开源工具、Python、Rust、自动化、数据科学。
2. full_name 必须逐字来自输入，不得编造项目、链接、数字或外部新闻。
3. “为什么突然火”只能根据今日新增 Star、项目定位、README 和 Trending 排名作合理推断，明确使用“可能/推测”等措辞，不能声称未提供的事件。
4. AI 观察应总结输入中可见的共同方向；如果 AI 项目样本不足，要直说。
5. 每个 TOP 项目补充 ai_category、growth_trend、star_velocity 和 learning_advice。AI 分类应使用明确赛道；非 AI 项目标注“非 AI / 通用技术”。成长趋势和 Star 速度必须依据 growth_metrics，不得把单日上涨断言为长期趋势。
6. 学习建议要给出可执行的 2 至 3 步，结合项目语言、README 和定位，避免泛泛而谈。
7. 值得收藏项目列 3 至 5 个，必须来自输入项目，说明未来潜力依据。
8. 语言简洁、具体，避免营销套话。

JSON 中 top10 每项必须包含：full_name、field、ai_category、growth_trend、star_velocity、sudden_reason、core_value、target_learners、learning_advice。

日期：{date.today().isoformat()}
输入数据：
{json.dumps(payload, ensure_ascii=False)}
""".strip()

    client = OpenAI(
        api_key=cfg.deepseek_api_key,
        base_url=cfg.deepseek_base_url,
        timeout=cfg.ai_request_timeout,
        max_retries=0,
    )
    request = {
        "model": cfg.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": "只输出符合要求的 JSON 对象，不要输出 Markdown 或额外说明。",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 6000,
        "extra_body": {
            "thinking": {
                "type": "enabled" if cfg.deepseek_thinking else "disabled"
            }
        },
    }

    for attempt in range(cfg.ai_max_retries + 1):
        try:
            response = client.chat.completions.create(**request)
            analysis = _parse_response_content(response)
            return _normalize_analysis(analysis, repositories, growth_metrics)
        except Exception as exc:
            message, retryable = _error_details(exc)
            if not retryable or attempt >= cfg.ai_max_retries:
                raise DeepSeekAPIError(
                    message,
                    status_code=getattr(exc, "status_code", None),
                    error_type=exc.__class__.__name__,
                ) from None
            delay = 2**attempt
            LOGGER.warning(
                "%s；%d 秒后进行第 %d/%d 次重试",
                message,
                delay,
                attempt + 1,
                cfg.ai_max_retries,
            )
            time.sleep(delay)

    raise DeepSeekAPIError("DeepSeek API 请求失败")


def fallback_analysis(
    repositories: list[Repository],
    growth_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    growth_metrics = growth_metrics or {}
    ranked = sorted(
        repositories,
        key=lambda repo: (repo.focus_score, repo.stars_today, -repo.rank),
        reverse=True,
    )
    top = ranked[:10]
    return {
        "top10": [
            {
                "full_name": repo.full_name,
                "field": ", ".join(repo.topics[:3]) or repo.language or "开源工具",
                "ai_category": growth_metrics.get(repo.full_name, {}).get("ai_category", "待分类"),
                "growth_trend": growth_metrics.get(repo.full_name, {}).get("growth_trend", "仅有当日数据，暂无法判断持续趋势"),
                "star_velocity": _format_star_velocity(repo, growth_metrics),
                "sudden_reason": f"推测与其当日新增 {repo.stars_today} Star 及 Trending 排名有关；AI 分析暂不可用。",
                "core_value": repo.description or "请查看项目 README 了解核心能力。",
                "target_learners": f"关注 {repo.language} 与开源工具的开发者",
                "learning_advice": f"先阅读 README 并运行最小示例，再用 {repo.language or '项目主要语言'} 完成一个小型真实场景验证。",
            }
            for repo in top
        ],
        "ai_observation": "DeepSeek API 本次不可用，邮件已降级为基于 GitHub 数据的摘要，请结合项目 README 判断趋势。",
        "watchlist": [
            {"full_name": repo.full_name, "reason": f"当日新增 {repo.stars_today} Star，且与关注领域匹配度较高。"}
            for repo in top[:5]
        ],
    }


def _format_star_velocity(
    repo: Repository, growth_metrics: dict[str, dict[str, Any]]
) -> str:
    metric = growth_metrics.get(repo.full_name, {})
    rate = metric.get("daily_growth_rate")
    level = metric.get("velocity_level", "未知")
    if isinstance(rate, (int, float)):
        return f"今日 +{repo.stars_today:,} Star，日增幅 {rate:.2f}%，速度{level}"
    return f"今日 +{repo.stars_today:,} Star"


def _normalize_analysis(
    analysis: dict[str, Any], repositories: list[Repository],
    growth_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    growth_metrics = growth_metrics or {}
    valid = {repo.full_name: repo for repo in repositories}
    seen: set[str] = set()
    normalized_top = []
    top_items = analysis.get("top10", [])
    if not isinstance(top_items, list):
        top_items = []
    for item in top_items:
        if not isinstance(item, dict):
            continue
        name = item.get("full_name", "")
        if name in valid and name not in seen:
            repo = valid[name]
            normalized_top.append(
                {
                    "full_name": name,
                    "field": str(item.get("field") or ", ".join(repo.topics[:3]) or repo.language),
                    "ai_category": str(item.get("ai_category") or growth_metrics.get(name, {}).get("ai_category") or "待分类"),
                    "growth_trend": str(item.get("growth_trend") or growth_metrics.get(name, {}).get("growth_trend") or "仅有当日数据，暂无法判断持续趋势"),
                    "star_velocity": str(item.get("star_velocity") or _format_star_velocity(repo, growth_metrics)),
                    "sudden_reason": str(item.get("sudden_reason") or f"可能与当日新增 {repo.stars_today} Star 及项目定位有关。"),
                    "core_value": str(item.get("core_value") or repo.description or "详见 README。"),
                    "target_learners": str(item.get("target_learners") or f"{repo.language} 开发者与开源技术学习者"),
                    "learning_advice": str(item.get("learning_advice") or f"先运行 README 最小示例，再用 {repo.language or '项目主要语言'} 完成一个小型场景验证。"),
                }
            )
            seen.add(name)
    for repo in repositories:
        if len(normalized_top) >= min(10, len(repositories)):
            break
        if repo.full_name not in seen:
            normalized_top.append(
                {
                    "full_name": repo.full_name,
                    "field": ", ".join(repo.topics[:3]) or repo.language,
                    "ai_category": growth_metrics.get(repo.full_name, {}).get("ai_category", "待分类"),
                    "growth_trend": growth_metrics.get(repo.full_name, {}).get("growth_trend", "仅有当日数据，暂无法判断持续趋势"),
                    "star_velocity": _format_star_velocity(repo, growth_metrics),
                    "sudden_reason": f"可能与当日新增 {repo.stars_today} Star 及项目定位有关。",
                    "core_value": repo.description or "详见 README。",
                    "target_learners": f"{repo.language} 开发者与开源技术学习者",
                    "learning_advice": f"先运行 README 最小示例，再用 {repo.language or '项目主要语言'} 完成一个小型场景验证。",
                }
            )
            seen.add(repo.full_name)
    watchlist = analysis.get("watchlist", [])
    if not isinstance(watchlist, list):
        watchlist = []
    normalized_watchlist = []
    watchlist_names: set[str] = set()
    for item in watchlist:
        if not isinstance(item, dict) or item.get("full_name") not in valid:
            continue
        name = item["full_name"]
        if name in watchlist_names:
            continue
        repo = valid[name]
        normalized_watchlist.append(
            {
                "full_name": name,
                "reason": str(item.get("reason") or f"当日新增 {repo.stars_today} Star，值得持续关注。"),
            }
        )
        watchlist_names.add(name)
    watchlist_target = min(5, len(repositories))
    for item in normalized_top:
        if len(normalized_watchlist) >= watchlist_target:
            break
        name = item["full_name"]
        if name in watchlist_names:
            continue
        repo = valid[name]
        normalized_watchlist.append(
            {
                "full_name": name,
                "reason": f"当日新增 {repo.stars_today} Star，且已进入今日重点项目，值得持续关注。",
            }
        )
        watchlist_names.add(name)
    observation = analysis.get("ai_observation")
    if not isinstance(observation, str) or not observation.strip():
        observation = "DeepSeek 未返回有效的 AI 趋势观察，请结合项目数据与 README 判断。"
    return {
        "top10": normalized_top[:10],
        "ai_observation": observation,
        "watchlist": normalized_watchlist[:5],
    }


def render_report(
    report_date: str, repositories: list[Repository], analysis: dict[str, Any],
    market_insight: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    market_insight = market_insight or {}
    repo_map = {repo.full_name: repo for repo in repositories}
    text_lines = ["《GitHub每日趋势日报》", "", f"日期：{report_date}", "", "今日最值得关注TOP10：", ""]
    cards = []
    for index, item in enumerate(analysis["top10"], start=1):
        repo = repo_map[item["full_name"]]
        text_lines.extend(
            [
                f"{index}. 项目：{repo.full_name}",
                f"Github：{repo.url}",
                f"Star：{repo.stars:,}（今日 +{repo.stars_today:,}）",
                f"语言：{repo.language}",
                f"领域：{item['field']}",
                f"AI 分类：{item.get('ai_category', '待分类')}",
                f"Star 增长速度：{item.get('star_velocity', f'今日 +{repo.stars_today:,} Star')}",
                f"项目成长趋势：{item.get('growth_trend', '仅有当日数据，暂无法判断持续趋势')}",
                f"为什么突然火：{item['sudden_reason']}",
                f"核心价值：{item['core_value']}",
                f"适合什么人学习：{item['target_learners']}",
                f"学习建议：{item.get('learning_advice', '先阅读 README 并运行最小示例。')}",
                "",
            ]
        )
        cards.append(
            f"""<section class="card"><h2>{index}. {html.escape(repo.full_name)}</h2>
<p><a href="{html.escape(repo.url)}">{html.escape(repo.url)}</a></p>
<p class="meta">⭐ {repo.stars:,} · 今日 +{repo.stars_today:,} · {html.escape(repo.language)}</p>
<p><b>领域：</b>{html.escape(item['field'])}</p>
<p><b>AI 分类：</b>{html.escape(item.get('ai_category', '待分类'))}</p>
<p><b>Star 增长速度：</b>{html.escape(item.get('star_velocity', f'今日 +{repo.stars_today:,} Star'))}</p>
<p><b>项目成长趋势：</b>{html.escape(item.get('growth_trend', '仅有当日数据，暂无法判断持续趋势'))}</p>
<p><b>为什么突然火：</b>{html.escape(item['sudden_reason'])}</p>
<p><b>核心价值：</b>{html.escape(item['core_value'])}</p>
<p><b>适合什么人学习：</b>{html.escape(item['target_learners'])}</p>
<p><b>学习建议：</b>{html.escape(item.get('learning_advice', '先阅读 README 并运行最小示例。'))}</p></section>"""
        )

    text_lines.extend(["---", "", "今日AI领域观察：", "", analysis["ai_observation"], "", "---", "", "值得收藏项目：", ""])
    watch_items = []
    for item in analysis["watchlist"]:
        repo = repo_map[item["full_name"]]
        text_lines.append(f"- {repo.full_name}：{item['reason']} ({repo.url})")
        watch_items.append(
            f'<li><a href="{html.escape(repo.url)}">{html.escape(repo.full_name)}</a>：{html.escape(item["reason"])}</li>'
        )

    insight_labels = (
        ("technical_trends", "技术趋势"),
        ("business_opportunities", "商业机会"),
        ("affected_companies_industries", "可能影响的公司 / 行业"),
        ("long_term_watch", "长期关注方向"),
    )
    insight_text: list[str] = []
    insight_html: list[str] = []
    if market_insight:
        text_lines.extend(["", "---", "", "市场与技术洞察：", ""])
        for key, label in insight_labels:
            values = market_insight.get(key, [])
            text_lines.append(f"{label}：")
            text_lines.extend(f"- {value}" for value in values)
            text_lines.append("")
            insight_text.extend(values)
            insight_html.append(
                f"<h2>{html.escape(label)}</h2><ul>"
                + "".join(f"<li>{html.escape(value)}</li>" for value in values)
                + "</ul>"
            )

    html_body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>body{{margin:0;background:#f5f7fb;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}.wrap{{max-width:760px;margin:auto;padding:24px}}.hero{{background:#111827;color:#fff;padding:28px;border-radius:14px}}.hero h1{{margin:0 0 8px}}.card{{background:#fff;margin:16px 0;padding:20px;border-radius:12px;border:1px solid #e5e7eb}}.card h2{{margin-top:0}}.meta{{color:#6b7280}}a{{color:#2563eb}}.observe{{background:#ecfeff;border-left:4px solid #0891b2;padding:18px;border-radius:8px}}li{{margin:10px 0}}</style></head>
<body><div class="wrap"><header class="hero"><h1>GitHub 每日趋势日报</h1><div>{html.escape(report_date)}</div></header>
<h1>今日最值得关注 TOP10</h1>{''.join(cards)}
<h1>今日 AI 领域观察</h1><div class="observe">{html.escape(analysis['ai_observation'])}</div>
<h1>值得收藏项目</h1><ul>{''.join(watch_items)}</ul>
{('<h1>市场与技术洞察</h1>' + ''.join(insight_html)) if insight_html else ''}</div></body></html>"""
    return "\n".join(text_lines), html_body
