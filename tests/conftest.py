import pytest


@pytest.fixture(autouse=True)
def _isolated_ai_ledger(tmp_path, monkeypatch):
    """Keep every test's AI usage ledger out of the project's data/ folder."""
    monkeypatch.setenv("AI_USAGE_DB_PATH", str(tmp_path / "ai_usage.db"))
    monkeypatch.delenv("AI_MONTHLY_BUDGET_USD", raising=False)
    # No real page downloads from end-to-end run tests.
    monkeypatch.setenv("ARTICLE_TEXT_INSCOPE_FETCH_MAX", "0")
