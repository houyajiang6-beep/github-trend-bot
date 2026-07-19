from __future__ import annotations

import html
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Mapping


SENSITIVE_NAMES = {
    ".env",
    "credentials.json",
    "token.json",
    "cookies.json",
    "cookie.txt",
}
SENSITIVE_FRAGMENTS = ("credential", "token", "cookie")


def public_generation_mode(mode: str | None) -> str:
    return {
        "llm_and_templates": "full_llm",
        "partial_fallback": "partial_fallback",
        "templates_fallback": "rules_fallback",
        "templates_only": "rules_fallback",
        "full_llm": "full_llm",
        "rules_fallback": "rules_fallback",
    }.get(str(mode or ""), "unknown")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return payload


def _labeled_sections(text: str, labels: tuple[str, ...]) -> dict[str, str]:
    sections = {label: [] for label in labels}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line in labels:
            current = line
            continue
        if current is not None:
            sections[current].append(raw_line.rstrip())
    return {
        label: "\n".join(lines).strip()
        for label, lines in sections.items()
    }


def load_creator_delivery(
    creator_status: Mapping[str, Any],
    report_date: str,
    *,
    github_run_id: str | None = None,
) -> dict[str, Any]:
    """Read existing Creator Ready files without generating or scoring anything."""
    status = str(creator_status.get("status") or "failed").lower()
    result: dict[str, Any] = {
        "available": False,
        "status": status,
        "reason": creator_status.get("degraded_reason")
        or creator_status.get("reason_code")
        or "CREATOR_OUTPUT_UNAVAILABLE",
        "degraded_reason": creator_status.get("degraded_reason")
        or creator_status.get("reason_code"),
        "generation_date": str(creator_status.get("publish_date") or report_date),
        "github_run_id": github_run_id or os.getenv("GITHUB_RUN_ID") or "未提供",
        "generation_mode": public_generation_mode(
            str(creator_status.get("content_generation_mode") or "")
        ),
        "fallback_projects": list(creator_status.get("fallback_projects") or []),
        "candidate_count": int(creator_status.get("candidate_count") or 0),
        "output_directory": creator_status.get("output"),
    }
    output = creator_status.get("output")
    if not output:
        return result

    root = Path(str(output))
    required = {
        "daily_selection": root / "daily_selection.json",
        "publish": root / "publish.txt",
        "cover": root / "cover.txt",
        "prediction": root / "prediction.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        result["reason"] = "MISSING_CREATOR_READY_FILES: " + ", ".join(missing)
        return result

    try:
        selection = _read_json(required["daily_selection"])
        prediction = _read_json(required["prediction"])
        publish = _labeled_sections(
            required["publish"].read_text(encoding="utf-8"),
            ("标题：", "正文：", "标签："),
        )
        cover = _labeled_sections(
            required["cover"].read_text(encoding="utf-8"),
            ("封面主标题：", "副标题：", "视觉建议："),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        result["reason"] = f"INVALID_CREATOR_READY_OUTPUT: {exc}"
        return result

    candidates = list(selection.get("top_candidates") or [])
    selected = candidates[0] if candidates else {}
    score = selected.get("publish_score")
    if score is None:
        score = (prediction.get("prediction") or {}).get("publish_score")

    fallback_projects = result["fallback_projects"]
    if not fallback_projects:
        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = _read_json(manifest_path)
                fallback_projects = [
                    str(item.get("project"))
                    for item in manifest.get("packages") or []
                    if item.get("generation_mode") == "rules_fallback"
                ]
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                fallback_projects = []

    result.update(
        {
            "available": True,
            "project": selected.get("project_name")
            or selection.get("selected_project")
            or prediction.get("project_name"),
            "score": score,
            "reason": creator_status.get("degraded_reason") if status == "degraded" else None,
            "degraded_reason": creator_status.get("degraded_reason")
            or creator_status.get("reason_code"),
            "recommendation_reason": selected.get("why_now"),
            "title": publish.get("标题：")
            or selected.get("recommended_title")
            or selection.get("selected_title"),
            "cover_main": cover.get("封面主标题："),
            "cover_subtitle": cover.get("副标题："),
            "body": publish.get("正文："),
            "tags": publish.get("标签："),
            "other_candidates": [
                {
                    "project": item.get("project_name"),
                    "title": item.get("recommended_title"),
                    "score": item.get("publish_score"),
                }
                for item in candidates[1:3]
            ],
            "candidate_count": len(candidates),
            "fallback_projects": fallback_projects,
        }
    )
    return result


def render_creator_delivery_plain(delivery: Mapping[str, Any]) -> str:
    lines = ["今日小红书首选", ""]
    if delivery.get("available"):
        lines.extend(
            [
                f"项目：{delivery.get('project') or '未提供'}",
                f"推荐分数：{delivery.get('score') if delivery.get('score') is not None else '未提供'}",
                f"推荐原因：{delivery.get('recommendation_reason') or '按今日 Publish Score 排名首选'}",
                "",
                f"标题：{delivery.get('title') or '未提供'}",
                f"封面主标题：{delivery.get('cover_main') or '未提供'}",
                f"封面副标题：{delivery.get('cover_subtitle') or '未提供'}",
                "",
                "直接发布正文：",
                str(delivery.get("body") or "未提供"),
                "",
                "标签：",
                str(delivery.get("tags") or "未提供"),
                "",
                "另外两个候选：",
            ]
        )
        others = list(delivery.get("other_candidates") or [])
        if others:
            for index, item in enumerate(others, 1):
                lines.append(
                    f"{index}. {item.get('project') or '未提供'} + "
                    f"{item.get('title') or '未提供'} + {item.get('score', '未提供')}"
                )
        else:
            lines.append("无")
    else:
        lines.extend(
            [
                "今日 Creator Ready 内容不可用，GitHub 趋势日报不受影响。",
                f"原因：{delivery.get('reason') or '未知'}",
            ]
        )

    mode = str(delivery.get("generation_mode") or "unknown")
    fallback_projects = list(delivery.get("fallback_projects") or [])
    lines.extend(
        [
            "",
            f"Creator Pipeline 状态：{str(delivery.get('status') or 'failed').upper()}",
            f"Content Generator 是否 fallback：{'是' if mode in {'partial_fallback', 'rules_fallback'} else '否'}",
            f"Content Generator 模式：{mode}",
            "fallback 项目：" + ("、".join(fallback_projects) if fallback_projects else "无"),
            f"生成日期：{delivery.get('generation_date') or '未知'}",
            f"GitHub Actions Run：{delivery.get('github_run_id') or '未提供'}",
        ]
    )
    if delivery.get("status") == "degraded":
        lines.append(
            f"degraded 原因：{delivery.get('degraded_reason') or '未提供'}"
        )
    return "\n".join(lines)


def render_creator_delivery_html(delivery: Mapping[str, Any]) -> str:
    plain = render_creator_delivery_plain(delivery)
    escaped = html.escape(plain).replace("\n", "<br>\n")
    return (
        '<section style="margin-top:24px;padding:18px;border:1px solid #e5e7eb;'
        'border-radius:10px;background:#fafafa">'
        f'<div style="white-space:normal;line-height:1.65">{escaped}</div>'
        "</section>"
    )


def append_creator_delivery(
    plain_text: str, html_body: str, delivery: Mapping[str, Any]
) -> tuple[str, str]:
    section = render_creator_delivery_html(delivery)
    lower_html = html_body.lower()
    closing_body = lower_html.rfind("</body>")
    combined_html = (
        html_body[:closing_body] + section + html_body[closing_body:]
        if closing_body >= 0
        else html_body.rstrip() + section
    )
    return (
        plain_text.rstrip() + "\n\n" + render_creator_delivery_plain(delivery) + "\n",
        combined_html,
    )


def _is_sensitive(relative: Path) -> bool:
    for part in relative.parts:
        lowered = part.lower()
        if (
            lowered in SENSITIVE_NAMES
            or lowered.startswith(".env.")
            or lowered == "logs"
        ):
            return True
        if any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS):
            return True
    return False


def create_creator_ready_zip(
    creator_dir: Path, destination_dir: Path, generation_date: str
) -> Path:
    """Create a secret-filtered archive from one Creator Ready date directory."""
    if not creator_dir.is_dir():
        raise FileNotFoundError(f"Creator Ready 目录不存在：{creator_dir}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    archive = destination_dir / f"creator-ready-{generation_date}.zip"
    temporary = archive.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(creator_dir.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(creator_dir)
                if _is_sensitive(relative):
                    continue
                bundle.write(
                    path,
                    Path("outputs") / "creator_ready" / generation_date / relative,
                )
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive
