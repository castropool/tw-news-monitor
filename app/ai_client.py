"""Model clients by tier, all through the budgeted, logged DeepSeekClient.

- ``fast``: routine high-volume work (summaries, translation, extraction).
  Model: ``AI_MODEL_FAST``, else ``SUMMARIZER_MODEL``, else ``DEEPSEEK_MODEL``.
- ``strong``: low-volume reasoning (weekly assessment drafting).
  Model: ``AI_MODEL_STRONG``; default ``deepseek-v4-pro``, written with the
  ``deepseek/`` prefix only when the fast model uses one (the endpoint's
  naming style).

Endpoint and key are shared with the summarizer (``SUMMARIZER_BASE_URL`` /
``SUMMARIZER_API_KEY``, falling back to ``DEEPSEEK_*``), so both tiers go
through the same local gateway by default.
"""

from __future__ import annotations

import os

from .deepseek_analysis import DeepSeekClient

DEFAULT_STRONG_MODEL = "deepseek/deepseek-v4-pro"
TIERS = ("fast", "strong")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def configured() -> bool:
    return bool((os.getenv("SUMMARIZER_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")).strip())


def model_for(tier: str) -> str:
    if tier not in TIERS:
        raise ValueError(f"unknown AI tier {tier!r}; expected one of {TIERS}")
    fast = (
        os.getenv("AI_MODEL_FAST", "").strip()
        or os.getenv("SUMMARIZER_MODEL", "").strip()
        or os.getenv("DEEPSEEK_MODEL", "").strip()
        or "deepseek-chat"
    )
    if tier == "strong":
        explicit = os.getenv("AI_MODEL_STRONG", "").strip()
        if explicit:
            return explicit
        # Follow the endpoint's naming, as shown by the working fast model:
        # the local Command Code gateway takes "deepseek/deepseek-v4-pro",
        # DeepSeek-style endpoints take the bare "deepseek-v4-pro".
        return DEFAULT_STRONG_MODEL if "/" in fast else DEFAULT_STRONG_MODEL.split("/", 1)[1]
    return fast


def make_client(task: str, tier: str = "fast") -> DeepSeekClient | None:
    """A client labelled ``task`` for the ledger; None when no endpoint is set."""
    if not configured():
        return None
    api_key = os.getenv("SUMMARIZER_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
    return DeepSeekClient(
        api_key=api_key.strip(),
        base_url=os.getenv("SUMMARIZER_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
        model=model_for(tier),
        # The strong model answers long prompts slowly (the live assistant timed out).
        timeout=(max(_int_env("AI_STRONG_TIMEOUT_SECONDS", 420), _int_env("SUMMARIZER_TIMEOUT_SECONDS", 180))
                 if tier == "strong" else _int_env("SUMMARIZER_TIMEOUT_SECONDS", 180)),
        max_retries=_int_env("SUMMARIZER_MAX_RETRIES", 2),
        task=task,
    )
