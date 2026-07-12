from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import production_check
import production_runner
from config import Settings


class FakeRateLimitResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "resources": {
                "core": {
                    "limit": 5000,
                    "remaining": 4900,
                    "used": 100,
                    "reset": 2_000_000_000,
                }
            }
        }


class ProductionCheckTests(unittest.TestCase):
    @patch("production_check.GitHubTrendingCrawler")
    def test_rate_limit_uses_authorization_header(self, crawler_class: Mock) -> None:
        crawler = crawler_class.return_value
        crawler.api_headers = {"Authorization": "Bearer test-token"}
        crawler.session.get.return_value = FakeRateLimitResponse()
        settings = Settings(github_token="test-token")

        self.assertTrue(production_check.check_github_rate_limit(settings))

        crawler.session.get.assert_called_once()
        self.assertEqual(
            crawler.session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-token",
        )

    @patch("production_check.GitHubTrendingCrawler")
    def test_missing_token_emits_warning(self, crawler_class: Mock) -> None:
        crawler = crawler_class.return_value
        crawler.api_headers = {}
        crawler.session.get.return_value = FakeRateLimitResponse()

        with self.assertLogs("production-check", level="WARNING") as logs:
            result = production_check.check_github_rate_limit(
                Settings(github_token="")
            )

        self.assertTrue(result)
        self.assertTrue(any("未配置 GITHUB_TOKEN" in line for line in logs.output))


class ProductionRunnerTests(unittest.TestCase):
    @patch("production_runner.send_failure_alert")
    @patch("production_runner.subprocess.run")
    def test_success_does_not_send_alert(
        self, subprocess_run: Mock, send_alert: Mock
    ) -> None:
        subprocess_run.return_value = SimpleNamespace(returncode=0)

        result = production_runner.run_production(["python", "main.py"])

        self.assertEqual(result, 0)
        send_alert.assert_not_called()

    @patch("production_runner.send_failure_alert")
    @patch("production_runner.subprocess.run")
    def test_failure_sends_alert_and_preserves_exit_code(
        self, subprocess_run: Mock, send_alert: Mock
    ) -> None:
        subprocess_run.return_value = SimpleNamespace(returncode=7)

        result = production_runner.run_production(["python", "main.py"])

        self.assertEqual(result, 7)
        send_alert.assert_called_once()
        self.assertEqual(send_alert.call_args.args[0], 7)

    @patch("production_runner.send_email")
    def test_failure_alert_uses_configured_recipient(self, send_email: Mock) -> None:
        settings = Settings(
            email_from="sender@gmail.com",
            email_to="recipient@gmail.com",
        )
        with (
            patch.object(production_runner, "settings", settings),
            patch.dict(
                production_runner.os.environ,
                {"FAILURE_ALERT_ENABLED": "true", "FAILURE_ALERT_TO": "ops@gmail.com"},
            ),
        ):
            sent = production_runner.send_failure_alert(1, 12.5, "test failure")

        self.assertTrue(sent)
        self.assertEqual(send_email.call_args.args[3].email_to, "ops@gmail.com")


if __name__ == "__main__":
    unittest.main()
