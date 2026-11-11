"""instagram adapter placeholder.

Registered so the adapter framework is complete; collection fails closed until
an owner-approved API/feed method is implemented in a later phase.
"""

from __future__ import annotations

from .base import SocialAdapter, SocialAdapterError


class InstagramAdapter(SocialAdapter):
    platform = "instagram"

    def _unavailable(self):
        raise SocialAdapterError(
            "instagram adapter is not implemented in this phase",
            code="adapter_not_implemented",
        )

    def fetch_account(self):
        self._unavailable()

    def fetch_recent_posts(self, *, since=None, limit=30):
        self._unavailable()

    def fetch_post(self, platform_post_id):
        self._unavailable()

    def normalize_post(self, raw):
        self._unavailable()
