from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo


WORKFLOW = "daily.yml"
WORKFLOW_NAME = "Daily GitHub Trend Report"
TIMEZONE = ZoneInfo("Asia/Shanghai")


def _run_gh(args: Sequence[str]) -> str:
    if shutil.which("gh") is None:
        raise RuntimeError(
            "未找到 GitHub CLI `gh`。请先安装并执行 `gh auth login`，"
            "无需把 GitHub token 写入代码。"
        )
    completed = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise RuntimeError(f"gh 命令失败：{detail}")
    return completed.stdout


def _local_date(created_at: str) -> str:
    value = created_at.replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(TIMEZONE).date().isoformat()


def select_run(
    runs: Sequence[dict[str, Any]],
    *,
    target_date: str | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    successful = [
        run
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    if run_id is not None:
        matches = [run for run in successful if int(run["databaseId"]) == run_id]
        if not matches:
            raise ValueError(f"没有找到成功的 workflow run：{run_id}")
        return matches[0]
    if target_date is not None:
        matches = [
            run for run in successful if _local_date(str(run["createdAt"])) == target_date
        ]
        if not matches:
            raise ValueError(f"没有找到北京时间 {target_date} 的成功 workflow run")
        return max(matches, key=lambda run: str(run["createdAt"]))
    if not successful:
        raise ValueError("没有找到成功的 Daily GitHub Trend Report run")
    return max(successful, key=lambda run: str(run["createdAt"]))


def list_successful_runs(limit: int = 100) -> list[dict[str, Any]]:
    _run_gh(["auth", "status"])
    payload = _run_gh(
        [
            "run",
            "list",
            "--workflow",
            WORKFLOW,
            "--limit",
            str(limit),
            "--json",
            "databaseId,createdAt,status,conclusion,headSha,event,workflowName",
        ]
    )
    runs = json.loads(payload)
    if not isinstance(runs, list):
        raise RuntimeError("gh run list 返回了无法识别的数据")
    return runs


def get_run(run_id: int) -> dict[str, Any]:
    _run_gh(["auth", "status"])
    payload = _run_gh(
        [
            "run",
            "view",
            str(run_id),
            "--json",
            "databaseId,createdAt,status,conclusion,headSha,event,workflowName",
        ]
    )
    run = json.loads(payload)
    if not isinstance(run, dict):
        raise RuntimeError(f"Run {run_id} 返回了无法识别的数据")
    if run.get("workflowName") != WORKFLOW_NAME:
        raise ValueError(f"Run {run_id} 不属于 {WORKFLOW_NAME}")
    return run


def _creator_roots(download_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in download_dir.rglob("creator_ready")
        if path.is_dir() and path.parent.name == "outputs"
    )


def _conflict_safe_target(target: Path, run_id: int) -> Path:
    candidate = target.with_name(f"{target.name}-run-{run_id}")
    suffix = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.name}-run-{run_id}-{suffix}")
        suffix += 1
    return candidate


def sync_run(
    run: dict[str, Any],
    *,
    repository_root: Path,
    target_date: str | None = None,
    overwrite: bool = False,
) -> list[Path]:
    run_id = int(run["databaseId"])
    target_root = repository_root / "outputs" / "creator_ready"
    target_root.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="creator-ready-") as directory:
        download_dir = Path(directory)
        _run_gh(["run", "download", str(run_id), "--dir", str(download_dir)])
        roots = _creator_roots(download_dir)
        if not roots:
            raise RuntimeError(
                f"Run {run_id} 的 Artifact 中没有 outputs/creator_ready/"
            )
        source_dates = [
            path
            for root in roots
            for path in root.iterdir()
            if path.is_dir() and (target_date is None or path.name == target_date)
        ]
        if not source_dates:
            requested = target_date or "任何日期"
            raise RuntimeError(f"Run {run_id} 中没有 Creator Ready 日期目录：{requested}")

        seen_dates: set[str] = set()
        for source in sorted(source_dates):
            if source.name in seen_dates:
                continue
            seen_dates.add(source.name)
            target = target_root / source.name
            if target.exists():
                if overwrite:
                    if target.parent.resolve() != target_root.resolve():
                        raise RuntimeError(f"拒绝覆盖目标目录之外的路径：{target}")
                    shutil.rmtree(target)
                else:
                    target = _conflict_safe_target(target, run_id)
            shutil.copytree(source, target)
            copied.append(target)
    return copied


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="下载最近成功的 Daily workflow Artifact，并同步 Creator Ready 产物。"
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--date", help="按北京时间选择运行，例如 2026-07-18")
    selector.add_argument("--run-id", type=int, help="指定 GitHub Actions Run ID")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="显式允许覆盖同日期目录；默认写入带 run ID 的冲突安全目录",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root_text = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        root = Path(root_text)
        runs = [get_run(args.run_id)] if args.run_id is not None else list_successful_runs()
        run = select_run(runs, target_date=args.date, run_id=args.run_id)
        copied = sync_run(
            run,
            repository_root=root,
            target_date=args.date,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1

    print(f"已从 Run {run['databaseId']} 同步 Creator Ready：")
    for path in copied:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
