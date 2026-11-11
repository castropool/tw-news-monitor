import logging
import sys
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """Base class for notifiers."""

    MAX_MESSAGE_LENGTH = 4000

    @abstractmethod
    def send(self, text: str) -> bool:
        ...

    def send_highlight_card(self, highlights: list) -> bool:
        return False

    def send_event_candidates(self, candidates: list) -> bool:
        """Send one compact message for each eligible international event.

        Event clustering and idempotency are handled before this boundary.  A
        notifier only renders the already-deduplicated candidates; it never
        calls the Feishu app module directly.
        """
        eligible = [
            candidate
            for candidate in (candidates or [])
            if getattr(candidate, "notifiable", False)
        ]
        if not eligible:
            return True
        lines = ["【国际重大新闻】", ""]
        for index, candidate in enumerate(eligible, 1):
            level = "重大" if getattr(candidate, "importance_level", "") == "critical" else "重点"
            lines.append(f"{index}. 【{level}】{getattr(candidate, 'cn_title', '')}")
            lines.append(
                f"   评分：{getattr(candidate, 'score', 0)}｜事件：{getattr(candidate, 'event_id', '')}"
            )
            source_ids = getattr(candidate, "coverage_source_ids", []) or []
            if source_ids:
                source_names = {
                    "reuters_international": "Reuters",
                    "ft_alphaville": "Financial Times",
                    "wsj_newsletter": "Wall Street Journal",
                    "bloomberg_newsletter": "Bloomberg",
                }
                names = [source_names.get(str(item), str(item)) for item in source_ids]
                lines.append(f"   来源：{'、'.join(names)}")
            reason = str(getattr(candidate, "relevance_reason", "") or "").strip()
            if reason:
                lines.append(f"   相关性：{reason}")
            lines.append(f"   {getattr(candidate, 'canonical_url', '')}")
            lines.append("")
        result = self.send_long("\n".join(lines).rstrip())
        # Legacy custom notifiers may return None; reaching this point still
        # means the send call completed without raising.
        return True if result is None else bool(result)

    def send_long(self, text: str) -> bool:
        """Send text, splitting into multiple messages if too long.

        Each chunk retains the digest header and a X/Y part indicator.
        Splitting occurs at line boundaries to avoid cutting titles
        or URLs mid-string.
        """
        if len(text) <= self.MAX_MESSAGE_LENGTH:
            result = self.send(text)
            return True if result is None else bool(result)

        lines = text.split("\n")
        header = lines[0] if lines else "【台湾新闻监测】"

        # Split body lines into chunks
        chunks: list[list[str]] = []
        current: list[str] = []
        cur_len = 0
        # Reserve ~60 chars for the part header
        reserve = len(header) + 60

        for line in lines[1:]:
            line_len = len(line) + 1
            if cur_len + line_len > self.MAX_MESSAGE_LENGTH - reserve and current:
                chunks.append(current)
                current = []
                cur_len = 0
            # If a single line exceeds the limit on its own, truncate it
            if line_len > self.MAX_MESSAGE_LENGTH - reserve:
                truncated = self._truncate_line(line, self.MAX_MESSAGE_LENGTH - reserve)
                current.append(truncated)
                cur_len += len(truncated) + 1
            else:
                current.append(line)
                cur_len += line_len

        if current:
            chunks.append(current)

        delivered = True
        for i, chunk_lines in enumerate(chunks):
            prefix = f"{header}\n第{i + 1}/{len(chunks)}部分\n\n"
            result = self.send(prefix + "\n".join(chunk_lines))
            if result is not None:
                delivered = delivered and bool(result)
        return delivered

    @staticmethod
    def _truncate_line(line: str, max_len: int) -> str:
        """Truncate a single long line, preserving the URL if present."""
        if len(line) <= max_len:
            return line
        url_pos = line.find("http")
        if url_pos >= 0:
            url_part = line[url_pos:]
            # If URL alone fits within max_len, prefix it with indicator
            if len(url_part) < max_len - 10:
                prefix_len = max_len - len(url_part) - 4
                if prefix_len > 0:
                    return line[:prefix_len] + "..." + url_part
            # URL too long, truncate it too
            return url_part[:max_len - 3] + "..."
        return line[:max_len - 3] + "..."


class ConsoleNotifier(Notifier):
    """Print digest to console (no size limit, no splitting)."""

    MAX_MESSAGE_LENGTH = 10_000_000  # effectively unlimited

    def send(self, text: str) -> bool:
        try:
            print(text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(text.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
            sys.stdout.buffer.flush()
            logger.warning(
                "Console UTF-8 fallback (stdout encoding=%s)",
                sys.stdout.encoding,
            )
        return True


class NullNotifier(Notifier):
    """No-op notifier for isolated validation and safety tests."""

    def send(self, text: str) -> bool:
        return True


class RecordingNotifier(Notifier):
    """In-memory notifier for deterministic delivery assertions."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.event_candidates: list = []

    def send(self, text: str) -> bool:
        self.messages.append(text)
        return True

    def send_event_candidates(self, candidates: list) -> bool:
        self.event_candidates.extend(candidates)
        return super().send_event_candidates(candidates)


class FeishuNotifier(Notifier):
    """Send digest to Feishu webhook."""

    MAX_MESSAGE_LENGTH = 30000

    def __init__(self, webhook_url: str, app_id: str = "", app_secret: str = "", chat_id: str = ""):
        if not webhook_url:
            raise ValueError("FEISHU_WEBHOOK_URL is not set")
        self._webhook_url = webhook_url
        self._app_id = app_id
        self._app_secret = app_secret
        self._chat_id = chat_id

    def send(self, text: str) -> bool:
        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }
        try:
            resp = httpx.post(
                self._webhook_url,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Feishu notification sent (length=%d)", len(text))
            return True
        except httpx.HTTPError as e:
            logger.error("Feishu send failed: %s", e)
            return False

    def send_highlight_card(self, highlights: list) -> bool:
        """Send an interactive highlight card to Feishu group chat.

        Args:
            highlights: list of (article, ImportanceResult) tuples sorted
                        by the shared select_highlights() function.

        Returns:
            True if card was sent successfully, False otherwise.
        """
        if not self._app_id or not self._app_secret or not self._chat_id:
            logger.warning("Feishu credentials not configured, skipping highlight card")
            return False

        try:
            from .feishu import build_highlight_card, send_card as _send_card
            critical_count = sum(1 for _, r in highlights if r.level == "critical")
            important_count = sum(1 for _, r in highlights if r.level == "important")
            card = build_highlight_card(highlights)
            _send_card(card, self._app_id, self._app_secret, self._chat_id)
            logger.info("Highlight card sent (critical=%d, important=%d)", critical_count, important_count)
            return True
        except Exception as e:
            logger.error("Highlight card send failed: %s", e)
            return False


class TelegramNotifier(Notifier):
    """Send digest to Telegram bot."""

    MAX_MESSAGE_LENGTH = 4000

    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set")
        self._chat_id = chat_id
        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, text: str) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            resp = httpx.post(
                self._api_url,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Telegram notification sent (length=%d)", len(text))
            return True
        except httpx.HTTPError as e:
            logger.error("Telegram send failed: %s", e)
            return False


def create_notifier() -> Notifier:
    """Factory to create notifier based on env config.

    Raises SystemExit with a clear message if feishu or telegram is
    selected but required env vars are missing.
    """
    import os

    notifier_type = os.getenv("NOTIFIER", "console").strip().lower()

    if notifier_type == "feishu":
        webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not webhook:
            logger.error(
                "NOTIFIER=feishu but FEISHU_WEBHOOK_URL is not set. "
                "Set FEISHU_WEBHOOK_URL in .env or change NOTIFIER=console."
            )
            raise SystemExit(1)
        fs_id = os.getenv("FEISHU_APP_ID", "").strip()
        fs_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        fs_chat = os.getenv("FEISHU_CHAT_ID", "").strip()
        return FeishuNotifier(webhook, app_id=fs_id, app_secret=fs_secret, chat_id=fs_chat)

    if notifier_type == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            logger.error(
                "NOTIFIER=telegram but TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_CHAT_ID is not set. "
                "Set both in .env or change NOTIFIER=console."
            )
            raise SystemExit(1)
        return TelegramNotifier(token, chat_id)

    # Default: console
    return ConsoleNotifier()
