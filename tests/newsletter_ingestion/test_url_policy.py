import pytest

from app.newsletter_ingestion.url_policy import (
    URLPolicy,
    is_safe_url,
    normalize_tracking_url,
)


def test_tracking_parameters_and_fragment_are_removed():
    assert normalize_tracking_url(
        "https://www.wsj.com/articles/a?utm_source=x&fbclid=y&foo=bar#top"
    ) == "https://www.wsj.com/articles/a?foo=bar"


def test_url_policy_preserves_case_sensitive_path():
    assert normalize_tracking_url(
        "https://www.example.com/News/ABC123?utm_source=test"
    ) == "https://www.example.com/News/ABC123"


def test_url_policy_is_https_only_and_rejects_credentials_and_local_networks():
    for value in (
        "http://www.example.com/a",
        "https://user:pass@example.com/a",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "javascript:alert(1)",
    ):
        with pytest.raises(ValueError):
            normalize_tracking_url(value)


def test_url_policy_rejects_nonstandard_ipv4_spellings_and_private_mapped_ipv6():
    for value in (
        "https://2130706433/a",       # integer form of 127.0.0.1
        "https://127.1/a",            # abbreviated IPv4
        "https://0177.0.0.1/a",       # octal-looking IPv4
        "https://0x7f000001/a",       # hexadecimal integer form
        "https://[::ffff:127.0.0.1]/a",
        "https://[::ffff:192.168.1.1]/a",
    ):
        with pytest.raises(ValueError):
            normalize_tracking_url(value)


def test_url_policy_keeps_standard_public_hosts_usable():
    assert normalize_tracking_url("https://8.8.8.8/a") == "https://8.8.8.8/a"
    assert normalize_tracking_url("https://[2001:4860:4860::8888]/a") == "https://[2001:4860:4860::8888]/a"
    assert normalize_tracking_url("https://www.example.com/a") == "https://www.example.com/a"


def test_article_host_allowlist_is_optional_but_enforced_when_configured():
    policy = URLPolicy(allowed_hosts={"wsj.com"})
    assert is_safe_url("https://www.wsj.com/a", policy=policy)
    assert not is_safe_url("https://www.example.com/a", policy=policy)


def test_redirects_are_disabled_by_default_and_resolver_is_injected():
    policy = URLPolicy(allowed_hosts={"wsj.com"})
    assert policy.allow_redirects is False
    with pytest.raises(ValueError):
        policy.resolve("https://click.wsj.com/a", resolver=lambda _: "https://www.wsj.com/a")


def test_redirect_resolver_obeys_depth_and_host_allowlist():
    policy = URLPolicy(allowed_hosts={"click.wsj.com", "wsj.com"}, allow_redirects=True, max_redirects=1)
    assert policy.resolve(
        "https://click.wsj.com/a", resolver=lambda _: "https://www.wsj.com/a"
    ) == "https://www.wsj.com/a"
    with pytest.raises(ValueError):
        policy.resolve(
            "https://click.wsj.com/a", resolver=lambda _: "https://evil.example/a"
        )
