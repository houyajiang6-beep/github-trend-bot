from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.sync_latest_creator_ready import select_run, sync_run


RUNS = [
    {
        "databaseId": 100,
        "createdAt": "2026-07-17T23:55:00Z",
        "status": "completed",
        "conclusion": "success",
    },
    {
        "databaseId": 101,
        "createdAt": "2026-07-18T00:07:00Z",
        "status": "completed",
        "conclusion": "success",
    },
    {
        "databaseId": 102,
        "createdAt": "2026-07-19T00:07:00Z",
        "status": "completed",
        "conclusion": "failure",
    },
]


class SyncCreatorReadyTests(unittest.TestCase):
    def test_date_and_run_id_select_the_correct_successful_run(self) -> None:
        self.assertEqual(select_run(RUNS, target_date="2026-07-18")["databaseId"], 101)
        self.assertEqual(select_run(RUNS, run_id=100)["databaseId"], 100)
        self.assertEqual(select_run(RUNS)["databaseId"], 101)
        with self.assertRaisesRegex(ValueError, "成功"):
            select_run(RUNS, run_id=102)

    def test_existing_date_is_not_silently_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outputs" / "creator_ready" / "2026-07-18"
            target.mkdir(parents=True)
            (target / "manual.txt").write_text("keep me", encoding="utf-8")

            def fake_gh(args: list[str]) -> str:
                download = Path(args[args.index("--dir") + 1])
                source = (
                    download
                    / "github-trend-bot-101"
                    / "outputs"
                    / "creator_ready"
                    / "2026-07-18"
                )
                source.mkdir(parents=True)
                (source / "publish.txt").write_text("artifact", encoding="utf-8")
                return ""

            with patch(
                "scripts.sync_latest_creator_ready._run_gh", side_effect=fake_gh
            ):
                copied = sync_run(
                    RUNS[1],
                    repository_root=root,
                    target_date="2026-07-18",
                )

            self.assertEqual((target / "manual.txt").read_text(), "keep me")
            self.assertNotEqual(copied[0], target)
            self.assertIn("run-101", copied[0].name)
            self.assertEqual((copied[0] / "publish.txt").read_text(), "artifact")


if __name__ == "__main__":
    unittest.main()
