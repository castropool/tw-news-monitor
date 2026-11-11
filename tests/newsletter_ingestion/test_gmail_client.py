import base64
import logging
from datetime import datetime, timedelta, timezone

from app.newsletter_ingestion.gmail_client import GmailMailboxClient
from app.newsletter_ingestion.oauth import AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, AuthContext, MAILBOX_AUTH_REQUIRED


class _Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Messages:
    def __init__(self, owner):
        self.owner = owner

    def list(self, **kwargs):
        self.owner.list_calls.append(kwargs)
        return _Request({"messages": [{"id": "m1"}, {"id": "m2"}]})

    def get(self, **kwargs):
        self.owner.get_calls.append(kwargs)
        return _Request(self.owner.payloads[kwargs["id"]])


class _Labels:
    def __init__(self, owner):
        self.owner = owner

    def list(self, **kwargs):
        self.owner.label_calls.append(kwargs)
        return _Request({"labels": [{"id": "int-news", "name": "InternationalNews"}, {"id": "other", "name": "Other"}]})


class _User:
    def __init__(self, owner):
        self.owner = owner

    def labels(self):
        return _Labels(self.owner)

    def messages(self):
        return _Messages(self.owner)


class FakeService:
    def __init__(self, payloads=None):
        self.payloads = payloads or {}
        self.label_calls = []
        self.list_calls = []
        self.get_calls = []
        self.modify_calls = []
        self.delete_calls = []

    def users(self):
        return _User(self)


def _payload(sender="news@reuters.com", title="Taiwan Strait update", body="<h1>" + "x" * 20 + "</h1>"):
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return {
        "id": "m1",
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": "Newsletter"},
                {"name": "Message-ID", "value": "<m1@example.test>"},
                {"name": "Date", "value": recent_date},
            ],
            "body": {"data": encoded},
        },
    }


def _authorized():
    return AuthContext(None, None, True, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "authorized_user_file")


def test_gmail_client_reads_only_label_and_never_mutates_mailbox():
    auth = AuthContext(None, None, False, MAILBOX_AUTH_REQUIRED)
    fake = FakeService({"m1": _payload()})
    client = GmailMailboxClient(service=fake, label="InternationalNews", modify=False, auth=auth)
    messages = client.list_messages(label="InternationalNews", sender_allowlist={"reuters.com"}, since="30d")
    assert messages == []
    assert auth.reason == MAILBOX_AUTH_REQUIRED
    assert fake.label_calls == []
    assert fake.list_calls == []
    assert fake.get_calls == []
    assert fake.modify_calls == []
    assert fake.delete_calls == []


def test_gmail_client_enforces_exact_label_sender_and_readonly_queries():
    fake = FakeService({"m1": _payload(), "m2": _payload(sender="alerts@untrusted.example")})
    client = GmailMailboxClient(service=fake, label="InternationalNews", auth=_authorized())
    messages = client.list_messages("InternationalNews", {"reuters.com"}, "30d")
    assert len(messages) == 1
    assert messages[0].sender == "news@reuters.com"
    assert fake.list_calls[0]["labelIds"] == ["int-news"]
    assert fake.list_calls[0]["q"].startswith("after:")
    assert fake.get_calls[0]["format"] == "full"
    assert not hasattr(fake.users().messages(), "modify")
    assert not fake.modify_calls and not fake.delete_calls


def test_gmail_client_applies_size_and_count_limits():
    fake = FakeService({"m1": _payload(body="<p>123456</p>"), "m2": _payload(body="<p>2</p>")})
    client = GmailMailboxClient(service=fake, label="InternationalNews", auth=_authorized(), max_message_bytes=10, max_messages=1)
    assert client.list_messages("InternationalNews", {"reuters.com"}, "30d") == []
    assert len(fake.get_calls) == 1


def test_invalid_modify_flag_is_rejected():
    try:
        GmailMailboxClient(service=FakeService(), label="InternationalNews", modify=True, auth=_authorized())
    except ValueError as exc:
        assert "readonly" in str(exc)
    else:
        raise AssertionError("modify=True must be rejected")


def test_provider_error_is_logged_without_exception_or_secret(caplog):
    class BrokenService(FakeService):
        def users(self):
            raise RuntimeError("provider-secret-sentinel message-body-secret")

    client = GmailMailboxClient(
        service=BrokenService(), label="InternationalNews", auth=_authorized()
    )
    with caplog.at_level(logging.WARNING):
        assert client.list_messages("InternationalNews", {"reuters.com"}, "30d") == []
    assert "provider_error" in caplog.text
    assert "provider-secret-sentinel" not in caplog.text
    assert "message-body-secret" not in caplog.text


def test_invalid_since_never_scans_the_label():
    fake = FakeService({"m1": _payload()})
    client = GmailMailboxClient(service=fake, label="InternationalNews", auth=_authorized())
    for invalid in (None, "", "bad", " 30d", "2026-08-14T08:00:00"):
        assert client.list_messages("InternationalNews", {"reuters.com"}, invalid) == []
    assert fake.label_calls == []
    assert fake.list_calls == []


def test_since_contract_accepts_timezone_aware_rfc3339():
    fake = FakeService({"m1": _payload()})
    client = GmailMailboxClient(service=fake, label="InternationalNews", auth=_authorized())
    client.list_messages("InternationalNews", {"reuters.com"}, "2026-08-14T00:00:00Z")
    assert fake.list_calls[0]["q"] == "after:2026/08/14"
