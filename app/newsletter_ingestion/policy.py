"""Fail-closed policy for newsletter messages and article hosts."""

from dataclasses import dataclass, field
from email.utils import parseaddr


DEFAULT_MAX_MESSAGE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_PARTS = 64
DEFAULT_MAX_ITEMS = 20


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    accepted: bool
    reason: str
    source_id: str | None = None


@dataclass(slots=True, frozen=True)
class SourcePolicy:
    label: str
    allowed_domains: set[str] = field(default_factory=set)
    source_id: str | None = None
    allowed_article_domains: set[str] = field(default_factory=set)
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    max_parts: int = DEFAULT_MAX_PARTS
    max_items: int = DEFAULT_MAX_ITEMS

    def __post_init__(self) -> None:
        # Normalize at construction so subsequent checks are deterministic.
        object.__setattr__(self, "allowed_domains", _normalize_domains(self.allowed_domains))
        object.__setattr__(self, "allowed_article_domains", _normalize_domains(self.allowed_article_domains))
        if not self.label or not self.label.strip():
            raise ValueError("newsletter label must not be empty")
        if self.max_message_bytes <= 0 or self.max_parts <= 0 or self.max_items <= 0:
            raise ValueError("newsletter limits must be positive")

    def check(self, label: str, sender: str) -> PolicyDecision:
        if label != self.label:
            return PolicyDecision(False, "LABEL_NOT_ALLOWED", self.source_id)
        address = parseaddr(sender or "")[1].strip().lower()
        if "@" not in address:
            return PolicyDecision(False, "SENDER_NOT_ALLOWED", self.source_id)
        domain = address.rsplit("@", 1)[1].rstrip(".")
        if not domain or not any(_domain_matches(domain, allowed) for allowed in self.allowed_domains):
            return PolicyDecision(False, "SENDER_NOT_ALLOWED", self.source_id)
        return PolicyDecision(True, "ACCEPTED", self.source_id)

    def article_host_allowed(self, host: str) -> bool:
        """Check article host if an explicit article allowlist was configured."""
        # In the common one-publisher case, reusing the sender allowlist is the
        # safe default.  A mailbox subdomain plus a different article host can
        # opt into the latter explicitly via ``allowed_article_domains``.
        allowed = self.allowed_article_domains or self.allowed_domains
        if not allowed:
            return False
        host = (host or "").lower().rstrip(".")
        return any(_domain_matches(host, domain) for domain in allowed)


def _normalize_domains(domains: set[str] | frozenset[str] | list[str] | tuple[str, ...]) -> set[str]:
    return {
        str(domain).strip().lower().lstrip("@").rstrip(".")
        for domain in domains
        if str(domain).strip()
    }


def _domain_matches(actual: str, allowed: str) -> bool:
    return actual == allowed or actual.endswith("." + allowed)
