"""URL canonicalization and redirect safety for newsletter links.

The parser never performs a redirect.  ``URLPolicy.resolve`` only permits an
explicitly injected resolver, HTTPS URLs, an allowlisted host, a small hop
limit, and public-looking literal hosts.  This keeps tests offline and makes
the security boundary visible to the eventual mailbox collector.
"""

from dataclasses import dataclass, field
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_KEYS = frozenset({
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "mkt_tok",
    "vero_id", "oly_anon_id", "oly_enc_id", "sr_share", "ncid", "ocid",
    "ref", "ref_", "source", "campaign", "trk", "tracking_id", "from",
})
REDIRECT_KEYS = frozenset({"url", "u", "redirect", "redirect_url", "target", "dest", "destination", "link"})


@dataclass(slots=True, frozen=True)
class URLPolicy:
    allowed_hosts: set[str] = field(default_factory=set)
    allow_redirects: bool = False
    max_redirects: int = 2
    timeout: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "allowed_hosts",
            {str(host).strip().lower().lstrip(".").rstrip(".") for host in self.allowed_hosts if str(host).strip()},
        )
        if self.max_redirects < 0 or self.max_redirects > 5:
            raise ValueError("max_redirects must be between 0 and 5")
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be > 0 and <= 30 seconds")

    def resolve(self, url: str, resolver=None) -> str:
        if not self.allow_redirects:
            raise ValueError("redirect resolution is disabled")
        if resolver is None:
            raise ValueError("redirect resolution requires an injected resolver")
        if not self.allowed_hosts:
            raise ValueError("redirect resolution requires a non-empty host allowlist")
        current = normalize_tracking_url(url, policy=self)
        for _ in range(self.max_redirects):
            candidate = _call_resolver(resolver, current, self.timeout)
            if not candidate or candidate == current:
                return current
            current = normalize_tracking_url(candidate, policy=self)
        # The last returned location is accepted as the bounded result; no
        # additional resolver call is made, so the configured depth is a hard
        # upper bound on external work.  A subsequent run must explicitly opt
        # into another bounded resolution.
        return current


def normalize_tracking_url(url: str, *, policy: URLPolicy | None = None) -> str:
    """Return a canonical HTTPS URL with known tracking query fields removed.

    Invalid, non-HTTPS, credential-bearing, local/private, or disallowed-host
    links raise ``ValueError``.  Keeping rejection explicit lets callers count
    parse errors without ever saving an unsafe link.
    """
    if not isinstance(url, str):
        raise ValueError("URL must be text")
    value = url.strip().strip("<>\"'")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("only absolute HTTPS URLs are accepted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are forbidden")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or _is_private_or_local_host(host):
        raise ValueError("local/private URL host is forbidden")
    if policy is not None and policy.allowed_hosts and not _host_allowed(host, policy.allowed_hosts):
        raise ValueError("URL host is not allowlisted")
    params = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_KEYS:
            continue
        params.append((key, val))
    # Host names are case-insensitive, but URL paths may be case-sensitive.
    # Keep the user-facing path intact while still removing a trailing slash.
    path = parsed.path.rstrip("/") or "/"
    netloc_host = f"[{host}]" if ":" in host else host
    normalized = parsed._replace(
        scheme="https", netloc=netloc_host + ((":" + str(parsed.port)) if parsed.port else ""),
        path=path, query=urlencode(sorted(params), doseq=True), fragment="",
    )
    return urlunparse(normalized)


def is_safe_url(url: str, *, policy: URLPolicy | None = None) -> bool:
    try:
        normalize_tracking_url(url, policy=policy)
    except (TypeError, ValueError):
        return False
    return True


def _call_resolver(resolver, url: str, timeout: float):
    try:
        return resolver(url, timeout=timeout)
    except TypeError:
        return resolver(url)


def _host_allowed(host: str, allowed: set[str]) -> bool:
    return any(host == item or host.endswith("." + item) for item in allowed)


def _is_private_or_local_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain", "0.0.0.0", "::1"} or host.endswith((".local", ".internal", ".localhost")):
        return True
    if _looks_like_nonstandard_ipv4(host) or "%" in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A colon cannot occur in a DNS hostname.  Treat malformed IPv6-like
        # values as unsafe instead of letting a URL library reinterpret them.
        if ":" in host and re.fullmatch(r"[0-9A-Fa-f:.]+", host):
            return True
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        mapped = ip.ipv4_mapped
        return bool(mapped.is_private or mapped.is_loopback or mapped.is_link_local or mapped.is_reserved or mapped.is_unspecified)
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified)


def _looks_like_nonstandard_ipv4(host: str) -> bool:
    """Reject numeric IPv4 spellings that URL libraries may reinterpret.

    A normal four-component decimal IPv4 literal remains valid (including
    public addresses such as 8.8.8.8).  Any all-numeric abbreviation,
    integer/hex form, octal-looking component, or malformed numeric dotted
    form is rejected before DNS or redirect handling can reinterpret it.
    """
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", host):
        return True
    if "." in host:
        components = host.split(".")
        if any(re.fullmatch(r"0[xX][0-9a-fA-F]+", component or "") for component in components):
            return True
    if not re.fullmatch(r"[0-9.]+", host):
        return False
    components = host.split(".")
    if len(components) != 4:
        return True
    if any(not component or (len(component) > 1 and component.startswith("0")) for component in components):
        return True
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return True
    return False
