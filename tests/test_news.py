from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quantlab.news import format_news_note, research_news


class NewsResearchTests(unittest.TestCase):
    def test_missing_key_skips_client_construction(self) -> None:
        with patch("quantlab.news.settings", SimpleNamespace(openai_api_key=None, openai_model="mock")):
            with patch("quantlab.news.OpenAI") as client:
                with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                    research_news("000001.SZ")
                client.assert_not_called()

    def test_mocked_response_records_sources_calls_and_tokens(self) -> None:
        citation = SimpleNamespace(type="url_citation", title="Issuer announcement", url="https://example.com/notice")
        content = SimpleNamespace(type="output_text", text="News summary", annotations=[citation])
        message = SimpleNamespace(type="message", content=[content])
        response = SimpleNamespace(
            output_text="News summary",
            output=[message],
            usage=SimpleNamespace(input_tokens=20, output_tokens=8, total_tokens=28),
        )
        client_instance = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))
        settings_mock = SimpleNamespace(openai_api_key="mock-test-key", openai_model="mock-model")
        with patch("quantlab.news.settings", settings_mock), patch("quantlab.news.OpenAI", return_value=client_instance):
            result = research_news("000001.SZ")

        note = format_news_note("000001.SZ", result)
        self.assertIn("https://example.com/notice", note)
        self.assertIn("API 调用次数：1", note)
        self.assertIn("输入 20", note)
        self.assertEqual(result.total_tokens, 28)


if __name__ == "__main__":
    unittest.main()
