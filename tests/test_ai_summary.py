from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_summary import DeepSeekAPIError, analyze_repositories, deepseek_error_summary
from config import Settings
from crawler import Repository


class FakeHTTPError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def make_repository(rank: int, name: str) -> Repository:
    return Repository(
        rank=rank,
        full_name=name,
        url=f"https://github.com/{name}",
        stars=1000,
        stars_today=100,
        language="Python",
        description="Test repository",
        readme="Test README",
        topics=["ai", "python"],
        focus_score=2,
    )


def make_response() -> SimpleNamespace:
    content = json.dumps(
        {
            "top10": [
                {
                    "full_name": "example/one",
                    "field": "AI 工具",
                    "sudden_reason": "可能与当日热度增长有关。",
                    "core_value": "帮助开发者完成测试。",
                    "target_learners": "Python 开发者",
                }
            ],
            "ai_observation": "AI 工具仍是主要趋势。",
            "watchlist": [
                {"full_name": "example/one", "reason": "增长较快。"}
            ],
        },
        ensure_ascii=False,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class DeepSeekSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repositories = [
            make_repository(1, "example/one"),
            make_repository(2, "example/two"),
        ]
        self.settings = Settings(
            deepseek_api_key="test-secret-key",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-v4-flash",
            deepseek_thinking=False,
            ai_request_timeout=30,
            ai_max_retries=3,
        )

    @patch("openai.OpenAI")
    def test_chat_completion_request_and_json_parsing(self, openai_class: Mock) -> None:
        client = openai_class.return_value
        client.chat.completions.create.return_value = make_response()

        result = analyze_repositories(self.repositories, self.settings)

        self.assertEqual(result["top10"][0]["full_name"], "example/one")
        self.assertEqual(len(result["top10"]), 2)
        self.assertEqual(len(result["watchlist"]), 2)
        openai_class.assert_called_once_with(
            api_key="test-secret-key",
            base_url="https://api.deepseek.com",
            timeout=30,
            max_retries=0,
        )
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "deepseek-v4-flash")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(
            request["extra_body"], {"thinking": {"type": "disabled"}}
        )
        self.assertNotIn("text", request)
        self.assertNotIn("instructions", request)

    @patch("ai_summary.time.sleep")
    @patch("openai.OpenAI")
    def test_rate_limit_retries_with_exponential_backoff(
        self, openai_class: Mock, sleep: Mock
    ) -> None:
        create = openai_class.return_value.chat.completions.create
        create.side_effect = [FakeHTTPError(429), make_response()]

        analyze_repositories(self.repositories, self.settings)

        self.assertEqual(create.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("openai.OpenAI")
    def test_insufficient_balance_has_clear_error_without_retry(
        self, openai_class: Mock
    ) -> None:
        create = openai_class.return_value.chat.completions.create
        create.side_effect = FakeHTTPError(402)

        with self.assertRaisesRegex(DeepSeekAPIError, "余额不足"):
            analyze_repositories(self.repositories, self.settings)

        self.assertEqual(create.call_count, 1)

    @patch("openai.OpenAI")
    def test_authentication_error_summary_does_not_include_response_body(
        self, openai_class: Mock
    ) -> None:
        create = openai_class.return_value.chat.completions.create
        secret_fragment = "must-not-appear"
        create.side_effect = FakeHTTPError(401)
        create.side_effect.args = (f"invalid key {secret_fragment}",)

        with self.assertRaises(DeepSeekAPIError) as raised:
            analyze_repositories(self.repositories, self.settings)

        summary = deepseek_error_summary(raised.exception)
        self.assertIn("http_status=401", summary)
        self.assertNotIn(secret_fragment, summary)

    def test_missing_api_key_has_clear_message(self) -> None:
        settings = Settings(deepseek_api_key="")
        with self.assertRaisesRegex(ValueError, "缺少 DEEPSEEK_API_KEY"):
            settings.validate_ai()


if __name__ == "__main__":
    unittest.main()
