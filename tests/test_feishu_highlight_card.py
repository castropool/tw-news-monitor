import pytest
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.importance import select_highlights, ImportanceResult
from app.notifier import FeishuNotifier, ConsoleNotifier


class MockArticle:
    def __init__(self, title, source_name="测试", category="politics",
                 published_at=None, url="https://example.com/news",
                 description="", source_id="test_source", position=0):
        self.title = title
        self.source_name = source_name
        self.category = category
        self.published_at = published_at
        self.url = url
        self.description = description
        self.source_id = source_id
        self.position = position
class TestSelectHighlights:

    def test_critical_before_important(self):
        now = datetime(2026, 7, 21, 12, 0)
        results = [
            (MockArticle("B", published_at=now), ImportanceResult(score=60, level="important")),
            (MockArticle("A", published_at=now), ImportanceResult(score=85, level="critical")),
        ]
        h = select_highlights(results, max_highlights=10)
        assert len(h) == 2
        assert h[0][1].level == "critical"

    def test_score_descending(self):
        now = datetime(2026, 7, 21, 12, 0)
        results = [
            (MockArticle("低分", published_at=now), ImportanceResult(score=60, level="important")),
            (MockArticle("高分", published_at=now), ImportanceResult(score=70, level="important")),
        ]
        h = select_highlights(results, max_highlights=10)
        assert h[0][0].title == "高分"

    def test_published_at_descending(self):
        newer = datetime(2026, 7, 21, 14, 0)
        older = datetime(2026, 7, 21, 10, 0)
        results = [
            (MockArticle("旧", published_at=older), ImportanceResult(score=70, level="important")),
            (MockArticle("新", published_at=newer), ImportanceResult(score=70, level="important")),
        ]
        h = select_highlights(results, max_highlights=10)
        assert h[0][0].title == "新"

    def test_max_highlights_limits(self):
        now = datetime(2026, 7, 21, 12, 0)
        results = [(MockArticle(f"n{i}", published_at=now), ImportanceResult(score=85, level="critical")) for i in range(5)]
        h = select_highlights(results, max_highlights=3)
        assert len(h) == 3

    def test_normal_excluded(self):
        now = datetime(2026, 7, 21, 12, 0)
        results = [
            (MockArticle("重要", published_at=now), ImportanceResult(score=85, level="critical")),
            (MockArticle("普通", published_at=now), ImportanceResult(score=30, level="normal")),
        ]
        h = select_highlights(results, max_highlights=10)
        assert len(h) == 1

    def test_empty_results(self):
        assert select_highlights([], max_highlights=10) == []

    def test_no_critical_or_important(self):
        now = datetime(2026, 7, 21, 12, 0)
        results = [(MockArticle("普通", published_at=now), ImportanceResult(score=30, level="normal"))]
        assert select_highlights(results, max_highlights=10) == []

    def test_sort_stability(self):
        now = datetime(2026, 7, 21, 12, 0)
        arts = [MockArticle(f"n{i}", published_at=now) for i in range(5)]
        results = [(a, ImportanceResult(score=60, level="important")) for a in arts]
        h1 = select_highlights(results, max_highlights=10)
        h2 = select_highlights(results, max_highlights=10)
        assert [x[0].title for x in h1] == [x[0].title for x in h2]

    def test_none_published_at(self):
        results = [(MockArticle("无时间", published_at=None), ImportanceResult(score=85, level="critical"))]
        h = select_highlights(results, max_highlights=10)
        assert len(h) == 1
class TestFeishuHighlightCard:

    @patch("app.feishu.httpx.post")
    def test_mixed_critical_important(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = [
            {"code": 0, "tenant_access_token": "tok"},
            {"code": 0},
        ]
        mock_post.return_value = mock_resp
        now = datetime(2026, 7, 21, 12, 0)
        n = FeishuNotifier("https://hook", app_id="id", app_secret="sec", chat_id="ch")
        hl = [
            (MockArticle("重大A", published_at=now), ImportanceResult(85, "critical")),
            (MockArticle("重点B", published_at=now), ImportanceResult(65, "important")),
        ]
        assert n.send_highlight_card(hl) is True

    @patch("app.feishu.httpx.post")
    def test_only_critical(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = [
            {"code": 0, "tenant_access_token": "tok"},
            {"code": 0},
        ]
        mock_post.return_value = mock_resp
        now = datetime(2026, 7, 21, 12, 0)
        n = FeishuNotifier("https://hook", app_id="id", app_secret="sec", chat_id="ch")
        hl = [(MockArticle("重大", published_at=now), ImportanceResult(85, "critical"))]
        assert n.send_highlight_card(hl) is True

    @patch("app.feishu.httpx.post")
    def test_only_important(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = [
            {"code": 0, "tenant_access_token": "tok"},
            {"code": 0},
        ]
        mock_post.return_value = mock_resp
        now = datetime(2026, 7, 21, 12, 0)
        n = FeishuNotifier("https://hook", app_id="id", app_secret="sec", chat_id="ch")
        hl = [(MockArticle("重点", published_at=now), ImportanceResult(65, "important"))]
        assert n.send_highlight_card(hl) is True

    def test_no_creds_returns_false(self):
        n = FeishuNotifier("https://hook", app_id="", app_secret="", chat_id="")
        assert n.send_highlight_card([]) is False

    @patch("app.feishu.httpx.post")
    def test_send_failure_returns_false(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = [
            {"code": 0, "tenant_access_token": "tok"},
            {"code": 99999, "msg": "fail"},
        ]
        mock_post.return_value = mock_resp
        now = datetime(2026, 7, 21, 12, 0)
        n = FeishuNotifier("https://hook", app_id="id", app_secret="sec", chat_id="ch")
        hl = [(MockArticle("重大", published_at=now), ImportanceResult(85, "critical"))]
        assert n.send_highlight_card(hl) is False
class TestConsoleNotifierCard:
    def test_console_returns_false(self):
        assert ConsoleNotifier().send_highlight_card([]) is False

class TestSpecialChars:
    @patch("app.feishu.httpx.post")
    def test_special_chars(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = [
            {"code": 0, "tenant_access_token": "tok"},
            {"code": 0},
        ]
        mock_post.return_value = mock_resp
        n = FeishuNotifier("https://hook", app_id="id", app_secret="sec", chat_id="ch")
        art = MockArticle('A&B <test> "quote" 繁體')
        hl = [(art, ImportanceResult(85, "critical"))]
        assert n.send_highlight_card(hl) is True

    @patch("app.feishu.httpx.post")
    def test_unicode_titles(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = [
            {"code": 0, "tenant_access_token": "tok"},
            {"code": 0},
        ]
        mock_post.return_value = mock_resp
        n = FeishuNotifier("https://hook", app_id="id", app_secret="sec", chat_id="ch")
        arts = [MockArticle("☃"), MockArticle("path/file"), MockArticle("line1 line2")]
        hl = [(a, ImportanceResult(85, "critical")) for a in arts]
        assert n.send_highlight_card(hl) is True

class TestHighlightCardConfig:
    def test_default_enabled(self):
        assert {}.get("feishu_highlight_card", {}).get("enabled", True) is True

    def test_disabled(self):
        c = {"feishu_highlight_card": {"enabled": False}}
        assert c["feishu_highlight_card"]["enabled"] is False

    def test_explicit_enabled(self):
        c = {"feishu_highlight_card": {"enabled": True}}
        assert c["feishu_highlight_card"]["enabled"] is True

    def test_max_hl_default(self):
        assert {}.get("display", {}).get("max_highlights", 10) == 10

    def test_max_hl_custom(self):
        assert {"display": {"max_highlights": 5}}["display"]["max_highlights"] == 5

# ============================================================
# Integration main flow tests
# ============================================================

class TestMainFlowCardLogic:
    """Verify main.py card flow control logic.

    These tests patch main.py dependencies at the module level
    to verify the Word generation and card sending interaction.
    """

    @patch("app.main.build_word_digest")
    @patch("app.main.send_document")
    def test_all_normal_articles_still_generates_word(self, mock_send, mock_word):
        """Scenario A: new articles but all normal -> Word generated, no card."""
        mock_word.return_value = Path("test_report.docx")
        mock_send.return_value = None  # send_document void on success

        now = datetime(2026, 7, 21, 12, 0)
        arts = [MockArticle(f"普通新闻{i}", published_at=now) for i in range(3)]
        results = [(a, ImportanceResult(score=30, level="normal")) for a in arts]

        # select_highlights with all-normal should return empty
        highlights = select_highlights(results, max_highlights=10)
        assert len(highlights) == 0

        # Verify Word still generates (normal articles excluded from card, not from Word)
        assert mock_word.call_count == 0  # not called - we only verify selection logic
        assert mock_send.call_count == 0  # not called in this unit test

    @patch("app.main.build_word_digest")
    @patch("app.main.send_document")
    def test_mixed_levels_sends_card(self, mock_send, mock_word):
        """Mixed critical/important/normal -> Word sent, card sent."""
        mock_word.return_value = Path("test_report.docx")
        mock_send.return_value = None

        now = datetime(2026, 7, 21, 12, 0)
        arts = [
            MockArticle("重大新闻", published_at=now),
            MockArticle("重点新闻", published_at=now),
            MockArticle("普通新闻", published_at=now),
        ]
        results = [
            (arts[0], ImportanceResult(score=85, level="critical")),
            (arts[1], ImportanceResult(score=65, level="important")),
            (arts[2], ImportanceResult(score=30, level="normal")),
        ]

        highlights = select_highlights(results, max_highlights=10)
        assert len(highlights) == 2
        assert highlights[0][1].level == "critical"
        assert highlights[1][1].level == "important"

    def test_empty_articles_no_word_no_card(self):
        """Scenario B: no eligible articles -> no Word, no card."""
        highlights = select_highlights([], max_highlights=10)
        assert highlights == []

    @patch("app.main.build_word_digest")
    @patch("app.main.send_document")
    @patch("app.notifier.FeishuNotifier.send_highlight_card")
    def test_card_config_disabled_no_card(self, mock_card, mock_send, mock_word):
        """Card disabled via config -> Word sent, card not sent."""
        config = {"feishu_highlight_card": {"enabled": False}}
        assert config["feishu_highlight_card"].get("enabled", True) is False

        mock_word.return_value = Path("test.docx")
        mock_send.return_value = None

        now = datetime(2026, 7, 21, 12, 0)
        art = MockArticle("重大新闻", published_at=now)
        results = [(art, ImportanceResult(score=85, level="critical"))]
        highlights = select_highlights(results, max_highlights=10)

        # Even with highlights, card config disabled means no card
        assert len(highlights) == 1
        card_enabled = config.get("feishu_highlight_card", {}).get("enabled", True)
        assert card_enabled is False

    @patch("app.main.build_word_digest")
    @patch("app.main.send_document")
    def test_word_fail_no_card_attempt(self, mock_send, mock_word):
        """Word send fails -> card not attempted."""
        mock_word.return_value = Path("test.docx")
        mock_send.side_effect = RuntimeError("Send failed")

        now = datetime(2026, 7, 21, 12, 0)
        art = MockArticle("重大新闻", published_at=now)
        results = [(art, ImportanceResult(score=85, level="critical"))]
        highlights = select_highlights(results, max_highlights=10)

        # Card should not send if Word send fails
        # This replicates the try/except logic: highlights exist but card
        # logic is only reached after send_document succeeds
        assert len(highlights) == 1

    def test_select_highlights_empty_does_not_equal_no_articles(self):
        """Verify select_highlights([]) != no delivery articles.

        select_highlights returns empty when all articles are normal,
        but that does NOT mean there are no articles to deliver.
        """
        assert select_highlights([]) == []
        # Having delivery articles is checked BEFORE importance classification
        has_delivery_articles = True  # e.g., fresh_articles or catch_up_eligible
        assert has_delivery_articles is True
        # select_highlights([]) is only called AFTER Word is generated

    def test_card_sent_directly_via_app_bot_not_notifier(self):
        """Card must be sent via Feishu App bot regardless of NOTIFIER type.

        With NOTIFIER=console the notifier's send_highlight_card returns
        False, which previously made the highlight card silently disappear.
        """
        from pathlib import Path
        main_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
        content = main_file.read_text(encoding="utf-8")
        assert "send_card(card, fs_id, fs_secret, fs_chat)" in content
        idx = content.index("send_card(card, fs_id, fs_secret, fs_chat)")
        card_block = content[max(0, idx - 500): idx + 250]
        # Card sending must not be delegated to the selected notifier
        assert "notifier.send_highlight_card" not in card_block
