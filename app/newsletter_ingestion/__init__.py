"""安全、离线优先的 Newsletter ingestion 核心域。

邮箱客户端由 Wave 2 提供；本包的 parser 不访问网络、不读取生产数据库，
只把经过来源策略检查的邮件 teaser 标准化为现有 Article。
"""

from .collector import NewsletterCollector
from .models import NewsletterItem, NewsletterMessage
from .oauth import SCOPE_PROVENANCE_VALUES
from .parser import parse_message
from .policy import PolicyDecision, SourcePolicy
from .url_policy import URLPolicy, normalize_tracking_url

__all__ = [
    "NewsletterCollector",
    "NewsletterItem",
    "NewsletterMessage",
    "PolicyDecision",
    "SourcePolicy",
    "URLPolicy",
    "normalize_tracking_url",
    "parse_message",
    "SCOPE_PROVENANCE_VALUES",
]
