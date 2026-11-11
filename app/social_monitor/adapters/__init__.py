"""Adapter registry. Adding a platform must not require core model changes."""

from .base import SocialAdapter, SocialAdapterError
from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .threads import ThreadsAdapter
from .x import XAdapter
from .youtube import YouTubeAdapter

ADAPTERS = {
    "youtube": YouTubeAdapter,
    "x": XAdapter,
    "threads": ThreadsAdapter,
    "facebook": FacebookAdapter,
    "instagram": InstagramAdapter,
}

__all__ = [
    "SocialAdapter", "SocialAdapterError", "ADAPTERS",
    "YouTubeAdapter", "XAdapter", "ThreadsAdapter",
    "FacebookAdapter", "InstagramAdapter",
]
