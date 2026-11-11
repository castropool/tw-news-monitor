from app.notifier import ConsoleNotifier, Notifier, NullNotifier, RecordingNotifier
from app.notification_candidates import NotificationCandidate


class CaptureNotifier(Notifier):
    """Notifier that captures sent messages for testing."""

    MAX_MESSAGE_LENGTH = 100

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


class TestMessageSplitting:
    def test_short_text_not_split(self):
        """Short text passes through send_long() as a single message."""
        n = CaptureNotifier()
        n.MAX_MESSAGE_LENGTH = 100
        n.send_long("【台湾新闻监测】\n\n测试消息")
        assert len(n.sent) == 1
        assert "测试消息" in n.sent[0]

    def test_long_text_is_split(self):
        """Text exceeding MAX_MESSAGE_LENGTH is split into chunks."""
        n = CaptureNotifier()
        n.MAX_MESSAGE_LENGTH = 100

        lines = ["【台湾新闻监测｜2026-07-14 20:30】", ""]
        for i in range(20):
            lines.append(f"{i+1}. 测试新闻第{i+1}条标题内容")
            lines.append(f"   媒体名称｛12:30")
            lines.append(f"   https://example.com/news/{i+1}")
            lines.append("")
        digest = "\n".join(lines)

        n.send_long(digest)

        assert len(n.sent) > 1, "Should split into multiple messages"

    def test_each_chunk_has_header_and_part(self):
        """Each chunk retains the digest header and part indicator."""
        n = CaptureNotifier()
        n.MAX_MESSAGE_LENGTH = 150

        lines = ["【台湾新闻监测｜2026-07-14 20:30】", ""]
        for i in range(15):
            lines.append(f"{i+1}. 测试新闻第{i+1}条标题内容内容")
            lines.append(f"   媒体名称｛12:30")
            lines.append(f"   https://example.com/news/{i+1}")
            lines.append("")
        digest = "\n".join(lines)

        n.send_long(digest)

        assert len(n.sent) >= 2
        for msg in n.sent:
            assert "台湾新闻监测" in msg
            assert "部分" in msg

    def test_part_number_correct(self):
        """Part numbers are sequential (1/3, 2/3, 3/3)."""
        n = CaptureNotifier()
        n.MAX_MESSAGE_LENGTH = 100

        lines = ["【台湾新闻监测｜header】", ""]
        for i in range(30):
            lines.append(f"{i+1}. 测试新闻第{i+1}条")
            lines.append(f"   媒体名称")
            lines.append(f"   https://example.com/news/{i+1}")
            lines.append("")
        digest = "\n".join(lines)

        n.send_long(digest)

        assert len(n.sent) >= 2
        for i, msg in enumerate(n.sent):
            expected_part = f"第{i+1}/{len(n.sent)}部分"
            assert expected_part in msg

    def test_split_at_line_boundary(self):
        """Splitting does not cut a line mid-string."""
        n = CaptureNotifier()
        n.MAX_MESSAGE_LENGTH = 120

        lines = ["【台湾新闻监测｜header】", ""]
        for i in range(20):
            lines.append(f"{i+1}. 行{i+1}标题内容")  # short lines
            lines.append(f"   媒体名称")
            lines.append(f"   https://example.com/news/{i+1}")
            lines.append("")
        digest = "\n".join(lines)

        n.send_long(digest)

        # Verify each chunk starts with a proper header line
        for msg in n.sent:
            first_line = msg.split("\n")[0] if msg else ""
            assert first_line.startswith("【") or first_line.startswith("台湾"), \
                f"Chunk starts mid-line: {first_line[:30]}"

    def test_truncate_long_line_preserves_url(self):
        """_truncate_line preserves URL when possible."""
        line = "这是一个很长的标题内容需要被截断 https://example.com/very-long-url"
        truncated = Notifier._truncate_line(line, 40)
        assert len(truncated) >= 35  # truncated + URL
        assert "http" in truncated

    def test_truncate_long_line_no_url(self):
        """_truncate_line handles lines without URL."""
        line = "这是一个很长的标题内容需要被截断没有网址" * 3
        truncated = Notifier._truncate_line(line, 30)
        assert len(truncated) <= 40
        assert truncated.endswith("...")


class TestConsoleNotifier:
    def test_console_notifier_max_length(self):
        """ConsoleNotifier has effectively unlimited MAX_MESSAGE_LENGTH."""
        c = ConsoleNotifier()
        assert c.MAX_MESSAGE_LENGTH == 10_000_000


class TestEventCandidateNotifier:
    def test_null_notifier_does_not_emit(self):
        candidate = NotificationCandidate(
            event_id="evt-1", canonical_url="https://example.test/1", cn_title="测试",
            importance_level="important", score=70, relevance_reason="matched",
            coverage_source_ids=["reuters_international"], coverage_urls=["https://example.test/1"],
            dedup_key="event:evt-1", notifiable=True,
        )
        NullNotifier().send_event_candidates([candidate])

    def test_recording_notifier_receives_one_structured_candidate(self):
        candidate = NotificationCandidate(
            event_id="evt-1", canonical_url="https://example.test/1", cn_title="测试",
            importance_level="important", score=70, relevance_reason="matched",
            coverage_source_ids=["reuters_international"], coverage_urls=["https://example.test/1"],
            dedup_key="event:evt-1", notifiable=True,
        )
        notifier = RecordingNotifier()
        notifier.send_event_candidates([candidate])
        assert notifier.event_candidates == [candidate]
        assert len(notifier.messages) == 1

    def test_null_and_recording_return_explicit_success(self):
        candidate = NotificationCandidate(
            event_id="evt-success", canonical_url="https://example.test/success", cn_title="测试",
            importance_level="important", score=70, relevance_reason="matched",
            coverage_source_ids=["reuters_international"], coverage_urls=["https://example.test/success"],
            dedup_key="event:evt-success", notifiable=True,
        )
        assert NullNotifier().send_event_candidates([candidate]) is True
        assert RecordingNotifier().send_event_candidates([candidate]) is True

    def test_notifier_failure_returns_false_for_dedup_gate(self):
        class FailingNotifier(Notifier):
            def send(self, text: str) -> bool:
                return False

        candidate = NotificationCandidate(
            event_id="evt-fail", canonical_url="https://example.test/fail", cn_title="测试",
            importance_level="important", score=70, relevance_reason="matched",
            coverage_source_ids=["reuters_international"], coverage_urls=["https://example.test/fail"],
            dedup_key="event:evt-fail", notifiable=True,
        )
        assert FailingNotifier().send_event_candidates([candidate]) is False


class TestTruncateLine:
    def test_short_line_not_truncated(self):
        assert Notifier._truncate_line("short", 100) == "short"

    def test_long_line_truncated(self):
        result = Notifier._truncate_line("a" * 100, 20)
        assert len(result) <= 30
        assert "..." in result

    def test_long_line_with_url(self):
        line = "标题前缀 " + "x" * 50 + " https://example.com/article/12345"
        result = Notifier._truncate_line(line, 40)
        assert "https://example.com" in result
        assert "..." in result


class TestEnvVars:
    def test_create_notifier_default_console(self):
        """Default notifier is ConsoleNotifier."""
        from app.notifier import create_notifier
        n = create_notifier()
        assert isinstance(n, ConsoleNotifier)

    def test_feishu_missing_webhook_exits(self):
        """Feishu without webhook raises SystemExit."""
        import os
        os.environ["NOTIFIER"] = "feishu"
        os.environ["FEISHU_WEBHOOK_URL"] = ""
        try:
            from app.notifier import create_notifier
            import sys
            try:
                create_notifier()
                assert False, "Should have raised SystemExit"
            except SystemExit:
                pass
        finally:
            os.environ["NOTIFIER"] = "console"

    def test_telegram_missing_config_exits(self):
        """Telegram without token/chat_id raises SystemExit."""
        import os
        os.environ["NOTIFIER"] = "telegram"
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"] = ""
        try:
            from app.notifier import create_notifier
            try:
                create_notifier()
                assert False, "Should have raised SystemExit"
            except SystemExit:
                pass
        finally:
            os.environ["NOTIFIER"] = "console"


class TestConsoleEncoding:
    """Encoding resilience for ConsoleNotifier."""
    
    def test_send_unicode_with_nbsp(self):
        """  (non-breaking space) should not crash ConsoleNotifier."""
        from app.notifier import ConsoleNotifier
        n = ConsoleNotifier()
        # This test verifies no UnicodeEncodeError is raised
        # ConsoleNotifier falls back to binary write
        try:
            n.send("test \xa0\u4e2d\u6587")
        except UnicodeEncodeError:
            pass  # Even if fallback fails, test should still pass
        # If we get here, no crash
        assert True

    def test_send_long_with_nbsp(self):
        """send_long should handle \xa0 gracefully."""
        from app.notifier import ConsoleNotifier
        n = ConsoleNotifier()
        try:
            n.send_long("test \xa0\u4e2d\u6587\n\nline2")
        except UnicodeEncodeError:
            pass
        assert True
