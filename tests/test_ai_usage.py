"""AI usage ledger and the monthly spending ceiling."""

from datetime import datetime, timezone

import pytest

import app.deepseek_analysis as deepseek_module
from app import ai_usage, summarizer
from app.deepseek_analysis import DeepSeekClient
from app.models import Article


class Resp:
    def __init__(self, content='{"ok": true}', usage=None):
        self.status_code = 200
        self._data = {"choices": [{"message": {"content": content}}], "usage": usage or {}}
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def posts(monkeypatch):
    import httpx

    sent = []

    def fake_post(url, **kwargs):
        sent.append(kwargs["json"])
        return Resp(usage={"prompt_tokens": 1000, "completion_tokens": 500})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(deepseek_module.time, "sleep", lambda s: None)
    return sent


def test_token_estimate_counts_han_characters_fully():
    assert ai_usage.estimate_tokens("國防部") == 3
    assert ai_usage.estimate_tokens("abcdef") == 2
    assert ai_usage.estimate_tokens("") == 0


def test_cost_uses_peak_rate_and_dearest_rate_for_unknown_models():
    flash = ai_usage.estimate_cost("deepseek/deepseek-v4-flash", 1_000_000, 1_000_000)
    assert flash == pytest.approx(0.44 + 1.32)
    unknown = ai_usage.estimate_cost("some-new-model", 1_000_000, 1_000_000)
    assert unknown == pytest.approx(1.32 + 3.96)


def test_each_call_is_logged_with_task_model_and_reported_tokens(posts):
    client = DeepSeekClient(api_key="k", base_url="http://127.0.0.1:8765/v1",
                            model="deepseek/deepseek-v4-flash", task="summary")
    assert client.analyze("sys", "user")["status"] == "success"
    rows = ai_usage.month_report()
    assert len(rows) == 1
    task, model, status, calls, tin, tout, cost = rows[0]
    assert (task, model, status, calls, tin, tout) == (
        "summary", "deepseek/deepseek-v4-flash", "success", 1, 1000, 500)
    assert cost == pytest.approx(ai_usage.estimate_cost(model, 1000, 500))


def test_zero_usage_from_gateway_is_estimated_from_text(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp(content='{"摘要": "國防部"}'))
    client = DeepSeekClient(api_key="k", model="deepseek/deepseek-v4-flash")
    client.analyze("系統", "使用者輸入")
    (_, _, _, _, tin, tout, cost), = ai_usage.month_report()
    assert tin == ai_usage.estimate_tokens("系統使用者輸入") and tout > 0 and cost > 0


def test_failed_calls_cost_nothing_unless_tokens_reported(monkeypatch):
    import httpx

    def down(url, **kw):
        raise httpx.ConnectError("gateway down")

    monkeypatch.setattr(httpx, "post", down)
    monkeypatch.setattr(deepseek_module.time, "sleep", lambda s: None)
    client = DeepSeekClient(api_key="k", max_retries=0)
    assert client.analyze("sys", "長" * 5000)["status"] == "error"
    assert ai_usage.month_spend() == 0.0
    assert ai_usage.month_report()[0][2] == "error"


def test_calls_are_refused_once_the_ceiling_is_reached(posts, monkeypatch):
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", "15")
    ai_usage.record_call(task="summary", provider="p", model="deepseek/deepseek-v4-pro",
                         status="success", input_tokens=2_000_000, output_tokens=3_200_000)
    assert ai_usage.month_spend() >= 15
    result = DeepSeekClient(api_key="k", task="summary").analyze("sys", "user")
    assert result == {"status": "error", "error": "budget_exceeded"}
    assert posts == []  # nothing was sent
    statuses = {row[2] for row in ai_usage.month_report()}
    assert "blocked" in statuses


def test_spend_resets_with_the_calendar_month(posts, monkeypatch):
    ai_usage.record_call(task="t", provider="p", model="deepseek/deepseek-v4-pro", status="success",
                         input_tokens=20_000_000, output_tokens=0,
                         now=datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
    assert ai_usage.month_spend("2026-10") > 15
    ai_usage.check_budget(now=datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc))  # no raise


def test_unreadable_ledger_never_blocks_calls(posts, monkeypatch, tmp_path):
    monkeypatch.setenv("AI_USAGE_DB_PATH", str(tmp_path))  # a directory, not a DB file
    assert DeepSeekClient(api_key="k").analyze("sys", "user")["status"] == "success"


def test_summaries_fall_back_when_budget_is_spent(monkeypatch):
    monkeypatch.setenv("SUMMARIZER_MODE", "llm")
    monkeypatch.setenv("SUMMARIZER_API_KEY", "cmd-local")
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", "0")
    article = Article(
        source_id="t", source_name="中央社", category="politics", title="國防部記者會",
        url="https://x/1", published_at=datetime(2026, 11, 14, 7), fetched_at=datetime(2026, 11, 14, 7),
        position=1,
    )
    summarizer.enrich_articles_with_summaries([article])
    assert article.summary_source != "llm"


def test_summarizer_and_translation_label_their_tasks(monkeypatch):
    monkeypatch.setenv("SUMMARIZER_API_KEY", "cmd-local")
    assert summarizer._load_deepseek_client().task == "summary"
    assert summarizer._load_deepseek_client(task="translation").task == "translation"


def test_report_cli(posts, capsys):
    DeepSeekClient(api_key="k", model="deepseek/deepseek-v4-flash", task="summary").analyze("s", "u")
    assert ai_usage.main([]) == 0
    out = capsys.readouterr().out
    assert "of $15.00 ceiling" in out and "summary" in out


def test_identical_requests_are_served_from_cache_at_zero_cost(posts):
    client = DeepSeekClient(api_key="k", model="deepseek/deepseek-v4-flash", task="summary")
    first = client.analyze("sys", "same prompt")
    second = client.analyze("sys", "same prompt")
    assert len(posts) == 1
    assert first["ok"] is True and second["ok"] is True and second.get("cached") is True
    statuses = {row[2]: row for row in ai_usage.month_report()}
    assert statuses["cache_hit"][6] == 0
    client.analyze("sys", "different prompt")
    assert len(posts) == 2


def test_cache_can_be_disabled_and_expires(posts, monkeypatch):
    monkeypatch.setenv("AI_CACHE_ENABLED", "0")
    client = DeepSeekClient(api_key="k", model="m")
    client.analyze("s", "u")
    client.analyze("s", "u")
    assert len(posts) == 2
    monkeypatch.setenv("AI_CACHE_ENABLED", "1")
    key = ai_usage.cache_key("m", "s", "u")
    ai_usage.cache_put(key, "m", '{"x": 1}', now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert ai_usage.cache_get(key) is None  # older than 30 days


def test_model_tiers(monkeypatch):
    from app import ai_client

    monkeypatch.setenv("SUMMARIZER_API_KEY", "cmd-local")
    monkeypatch.setenv("SUMMARIZER_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.delenv("AI_MODEL_STRONG", raising=False)
    assert ai_client.make_client("summary").model == "deepseek/deepseek-v4-flash"
    assert ai_client.make_client("assessment", "strong").model == "deepseek/deepseek-v4-pro"
    monkeypatch.setenv("AI_MODEL_FAST", "deepseek/x-fast")
    assert ai_client.make_client("extraction").model == "deepseek/x-fast"
    with pytest.raises(ValueError):
        ai_client.model_for("medium")
    monkeypatch.delenv("SUMMARIZER_API_KEY")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert ai_client.make_client("summary") is None
