from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from content_calibration import (
    create_blind_prediction,
    load_or_create_rubric,
    record_t3_performance,
    register_publication,
)


class ContentCalibrationTests(unittest.TestCase):
    def _prediction(self, root: Path) -> tuple[Path, Path, datetime]:
        publish = root / "publish.txt"
        publish.write_text("标题：测试\n正文：真实内容", encoding="utf-8")
        rubric_path = root / "calibration" / "rubric.json"
        rubric = load_or_create_rubric(rubric_path)
        now = datetime(2026, 7, 20, 8, tzinfo=timezone.utc)
        path = create_blind_prediction(
            selected={
                "project_name": "example/tool",
                "category": "learning",
                "publish_score": 80,
                "dimensions": {"human_value": 78, "creator_strategy": 82},
            },
            publish_file=publish,
            prediction_dir=root / "calibration" / "predictions",
            publish_date="2026-07-20",
            rubric=rubric,
            created_at=now,
        )
        return path, rubric_path, now

    def test_prediction_is_reused_not_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path, rubric_path, now = self._prediction(root)
            before = path.read_text(encoding="utf-8")
            payload = json.loads(before)
            same = create_blind_prediction(
                selected={
                    "project_name": "example/tool",
                    "publish_score": 1,
                    "dimensions": {},
                },
                publish_file=root / "publish.txt",
                prediction_dir=path.parent,
                publish_date="2026-07-20",
                rubric=load_or_create_rubric(rubric_path),
                created_at=now + timedelta(hours=1),
            )
            self.assertEqual(path, same)
            self.assertEqual(before, path.read_text(encoding="utf-8"))
            self.assertEqual(payload["prediction"]["publish_score"], 80)

    def test_t3_is_enforced_and_updates_observations_without_weights(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path, rubric_path, now = self._prediction(root)
            register_publication(path, published_at=now, url="https://example.com")
            with self.assertRaisesRegex(ValueError, "尚未达到 T\+3"):
                record_t3_performance(
                    path,
                    as_of=now + timedelta(days=2),
                    views=10,
                    likes=1,
                    saves=1,
                    comments=0,
                    followers_gained=0,
                    rubric_path=rubric_path,
                )
            original_prediction = json.loads(path.read_text(encoding="utf-8"))[
                "prediction"
            ]
            original_weights = load_or_create_rubric(rubric_path)["weights"]
            record_t3_performance(
                path,
                as_of=now + timedelta(days=3),
                views=100,
                likes=10,
                saves=8,
                comments=2,
                followers_gained=1,
                rubric_path=rubric_path,
            )
            updated = json.loads(path.read_text(encoding="utf-8"))
            rubric = load_or_create_rubric(rubric_path)
        self.assertEqual(updated["prediction"], original_prediction)
        self.assertEqual(rubric["weights"], original_weights)
        self.assertEqual(rubric["calibration"]["completed_samples"], 1)
        self.assertEqual(rubric["calibration"]["bump_status"], "collecting")


if __name__ == "__main__":
    unittest.main()
