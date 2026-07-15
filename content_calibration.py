from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


DEFAULT_RUBRIC: dict[str, Any] = {
    "version": "daily-v1",
    "weights": {"creator_strategy": 0.7, "human_value": 0.3},
    "repetition_penalty": 20.0,
    "retro_window_days": 3,
    "min_samples_for_bump": 5,
    "calibration": {
        "completed_samples": 0,
        "rank_correlation": None,
        "bump_status": "collecting",
        "observations": [],
    },
}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_or_create_rubric(path: Path) -> dict[str, Any]:
    """Return the runtime ranking rubric, creating the small v1 file once."""
    if not path.exists():
        _atomic_json(path, DEFAULT_RUBRIC)
    payload = json.loads(path.read_text(encoding="utf-8"))
    weights = payload.get("weights") if isinstance(payload, dict) else None
    if not isinstance(weights, dict):
        raise ValueError(f"内容 rubric 缺少 weights：{path}")
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 0.001:
        raise ValueError("内容 rubric 权重之和必须为 1")
    return payload


def create_blind_prediction(
    *,
    selected: Mapping[str, Any],
    publish_file: Path,
    prediction_dir: Path,
    publish_date: str,
    rubric: Mapping[str, Any],
    created_at: datetime,
) -> Path:
    """Create an immutable pre-publish prediction for the final Top 1 draft."""
    content = publish_file.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    content_id = f"{publish_date}-{content_hash}"
    path = prediction_dir / f"{content_id}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("content_hash") != content_hash:
            raise ValueError(f"预测记录冲突，拒绝覆盖：{path}")
        return path
    dimensions = dict(selected.get("dimensions") or {})
    record = {
        "schema_version": "1.0",
        "content_id": content_id,
        "project_name": selected.get("project_name"),
        "category": selected.get("category"),
        "publish_date": publish_date,
        "created_at": created_at.isoformat(),
        "content_hash": content_hash,
        "publish_file": str(publish_file),
        "prediction_locked": True,
        "prediction": {
            "rubric_version": str(rubric.get("version") or "daily-v1"),
            "publish_score": float(selected.get("publish_score") or 0),
            "human_value_score": float(dimensions.get("human_value") or 0),
            "creator_strategy_score": float(
                dimensions.get("creator_strategy") or 0
            ),
            "confidence": "cold_start",
            "directional_bet": (
                "该候选应优于今天被拦截或账号匹配较低的项目；"
                "冷启动阶段不伪造播放量中枢。"
            ),
        },
        "publication": {"published_at": None, "url": None},
        "performance": None,
        "retro": None,
    }
    _atomic_json(path, record)
    return path


def register_publication(
    prediction_path: Path, *, published_at: datetime, url: str | None = None
) -> dict[str, Any]:
    record = json.loads(prediction_path.read_text(encoding="utf-8"))
    original_prediction = json.dumps(record["prediction"], sort_keys=True)
    record["publication"] = {
        "published_at": published_at.isoformat(),
        "url": url,
    }
    if json.dumps(record["prediction"], sort_keys=True) != original_prediction:
        raise RuntimeError("预测段发生变化，拒绝写入")
    _atomic_json(prediction_path, record)
    return record


def record_t3_performance(
    prediction_path: Path,
    *,
    as_of: datetime,
    views: int,
    likes: int,
    saves: int,
    comments: int,
    followers_gained: int,
    rubric_path: Path,
) -> dict[str, Any]:
    """Append T+3 metrics while preserving the immutable prediction object."""
    record = json.loads(prediction_path.read_text(encoding="utf-8"))
    original_prediction = json.dumps(record["prediction"], sort_keys=True)
    published_raw = (record.get("publication") or {}).get("published_at")
    if not published_raw:
        raise ValueError("请先登记 published_at，再录入 T+3 表现")
    published_at = datetime.fromisoformat(published_raw)
    rubric = load_or_create_rubric(rubric_path)
    window = int(rubric.get("retro_window_days", 3))
    if (as_of - published_at).total_seconds() < window * 86400:
        raise ValueError(f"尚未达到 T+{window}，拒绝提前复盘")
    if min(views, likes, saves, comments, followers_gained) < 0:
        raise ValueError("表现数据不能为负数")
    denominator = max(views, 1)
    performance = {
        "as_of": as_of.isoformat(),
        "views": views,
        "likes": likes,
        "saves": saves,
        "comments": comments,
        "followers_gained": followers_gained,
        "save_rate": round(saves / denominator, 6),
        "comment_rate": round(comments / denominator, 6),
        "follow_rate": round(followers_gained / denominator, 6),
    }
    quality_signal = round(
        performance["save_rate"] * 0.5
        + performance["follow_rate"] * 0.3
        + performance["comment_rate"] * 0.2,
        6,
    )
    record["performance"] = performance
    record["retro"] = {
        "quality_signal": quality_signal,
        "note": "收藏率优先，其次关注转化和评论率；播放量保留为曝光背景。",
    }
    if json.dumps(record["prediction"], sort_keys=True) != original_prediction:
        raise RuntimeError("预测段发生变化，拒绝写入")
    _atomic_json(prediction_path, record)
    update_rubric_observations(rubric_path, prediction_path.parent)
    return record


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        rank = (index + end) / 2 + 1
        for cursor in range(index, end + 1):
            ranks[order[cursor]] = rank
        index = end + 1
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    rank_left, rank_right = _rank(left), _rank(right)
    mean_left = sum(rank_left) / len(rank_left)
    mean_right = sum(rank_right) / len(rank_right)
    numerator = sum(
        (a - mean_left) * (b - mean_right)
        for a, b in zip(rank_left, rank_right)
    )
    denominator = (
        sum((a - mean_left) ** 2 for a in rank_left)
        * sum((b - mean_right) ** 2 for b in rank_right)
    ) ** 0.5
    return None if denominator == 0 else round(numerator / denominator, 4)


def update_rubric_observations(
    rubric_path: Path, prediction_dir: Path
) -> dict[str, Any]:
    """Refresh evidence only; never auto-change weights from a tiny sample."""
    rubric = load_or_create_rubric(rubric_path)
    completed: list[dict[str, Any]] = []
    for path in sorted(prediction_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("performance") and payload.get("retro"):
            completed.append(payload)
    predicted = [float(item["prediction"]["publish_score"]) for item in completed]
    actual = [float(item["retro"]["quality_signal"]) for item in completed]
    correlation = _spearman(predicted, actual)
    minimum = int(rubric.get("min_samples_for_bump", 5))
    observations: list[str] = []
    if not completed:
        observations.append("尚无 T+3 样本，保持当前权重。")
    elif len(completed) < minimum:
        observations.append(
            f"当前只有 {len(completed)} 个完整样本，只记录误差，不调整权重。"
        )
    elif correlation is None or correlation < 0.8:
        observations.append(
            "预测排序与 T+3 质量信号尚未达到 0.8 一致性；需要人工提出新公式并全量重评分。"
        )
    else:
        observations.append("当前排序与 T+3 质量信号一致，暂不调整权重。")
    bump_status = (
        "collecting"
        if len(completed) < minimum
        else "review_required"
        if correlation is None or correlation < 0.8
        else "stable"
    )
    rubric["calibration"] = {
        "completed_samples": len(completed),
        "rank_correlation": correlation,
        "bump_status": bump_status,
        "observations": observations,
    }
    _atomic_json(rubric_path, rubric)
    notes = [
        "# Content Rubric 当前状态",
        "",
        f"- 当前版本：{rubric['version']}",
        f"- 完整 T+3 样本：{len(completed)}",
        f"- 排序相关性：{correlation if correlation is not None else 'N/A'}",
        f"- 状态：{bump_status}",
        "",
        "## 当前观察",
        "",
        *(f"- {item}" for item in observations),
        "",
        "> 本文件只保留当前有效观察。权重不会自动修改；正式 bump 必须全量重评分并独立审核。",
        "",
    ]
    rubric_path.with_name("rubric_notes.md").write_text(
        "\n".join(notes), encoding="utf-8"
    )
    return rubric
