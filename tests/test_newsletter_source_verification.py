import hashlib
import json

import pytest

from app.newsletter_ingestion.models import NewsletterMessage
from app.newsletter_ingestion.oauth import AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, AuthContext, MAILBOX_AUTH_REQUIRED
from app.newsletter_ingestion.verify_sources import run_verification


class FakeMailbox:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def list_messages(self, label, sender_allowlist, since):
        self.calls.append((label, set(sender_allowlist), since))
        return self.messages


def _message():
    return NewsletterMessage(
        "<fake-message@example.test>",
        "news@wsj.com",
        None,
        "What's News",
        "<h1>China and Taiwan</h1>",
        None,
        "InternationalNews",
    )


def test_public_gmail_summary_are_independent_and_never_overwrite(tmp_path):
    public_path = tmp_path / "wsj_newsletter_public_2026-08-14.json"
    gmail_path = tmp_path / "wsj_newsletter_gmail_2026-08-14.json"
    summary_path = tmp_path / "wsj_newsletter_summary_2026-08-14.json"
    public = run_verification(
        "public", "wsj_newsletter", public_path,
        public_page="https://www.wsj.com/newsletters", verification_date="2026-08-14",
    )
    assert public["status"] == "official_url_registered"
    gmail = run_verification(
        "gmail", "wsj_newsletter", gmail_path, auth=None,
        verification_date="2026-08-14",
    )
    assert gmail["reason"] == MAILBOX_AUTH_REQUIRED
    summary = run_verification(
        "summary", "wsj_newsletter", summary_path,
        public_evidence=public, gmail_evidence=gmail,
        verification_date="2026-08-14",
    )
    assert summary["status"] == "operator_action_required"
    with pytest.raises(FileExistsError):
        run_verification("public", "wsj_newsletter", public_path, verification_date="2026-08-14")


def test_gmail_evidence_requires_auth_and_does_not_call_mailbox(tmp_path):
    mailbox = FakeMailbox([_message()])
    evidence = run_verification(
        "gmail", "wsj_newsletter", tmp_path / "wsj_newsletter_gmail_2026-08-15.json",
        auth=None, mailbox=mailbox, verification_date="2026-08-15",
    )
    assert evidence["status"] == "operator_action_required"
    assert evidence["reason"] == MAILBOX_AUTH_REQUIRED
    assert mailbox.calls == []


def test_authorized_gmail_and_verified_summary_record_hashes(tmp_path):
    public_path = tmp_path / "wsj_newsletter_public_2026-08-14.json"
    gmail_path = tmp_path / "wsj_newsletter_gmail_2026-08-14.json"
    summary_path = tmp_path / "wsj_newsletter_summary_2026-08-14.json"
    public = run_verification("public", "wsj_newsletter", public_path, verification_date="2026-08-14")
    mailbox = FakeMailbox([_message()])
    auth = AuthContext(None, None, True, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "authorized_user_file")
    gmail = run_verification(
        "gmail", "wsj_newsletter", gmail_path, auth=auth, mailbox=mailbox,
        verification_date="2026-08-14",
    )
    assert gmail["status"] == "verified"
    assert mailbox.calls == [("InternationalNews", {"wsj.com", "dowjones.com"}, "30d")]
    summary = run_verification(
        "summary", "wsj_newsletter", summary_path,
        public_evidence=public_path, gmail_evidence=gmail_path,
        verification_date="2026-08-14",
    )
    assert summary["status"] == "verified"
    assert summary["public_evidence_sha256"] == hashlib.sha256(public_path.read_bytes()).hexdigest()
    assert summary["gmail_evidence_sha256"] == hashlib.sha256(gmail_path.read_bytes()).hexdigest()
    stored = json.loads(summary_path.read_text(encoding="utf-8"))
    assert stored["evidence_sha256"] == summary["evidence_sha256"]


def test_summary_does_not_overwrite_inputs_or_output(tmp_path):
    public_path = tmp_path / "bloomberg_newsletter_public_2026-08-15.json"
    gmail_path = tmp_path / "bloomberg_newsletter_gmail_2026-08-15.json"
    summary_path = tmp_path / "bloomberg_newsletter_summary_2026-08-15.json"
    run_verification("public", "bloomberg_newsletter", public_path, verification_date="2026-08-15")
    run_verification(
        "gmail", "bloomberg_newsletter", gmail_path,
        auth=None, verification_date="2026-08-15",
    )
    public_before = public_path.read_bytes()
    gmail_before = gmail_path.read_bytes()
    run_verification(
        "summary", "bloomberg_newsletter", summary_path,
        public_evidence=public_path, gmail_evidence=gmail_path,
        verification_date="2026-08-15",
    )
    assert public_path.read_bytes() == public_before
    assert gmail_path.read_bytes() == gmail_before
    with pytest.raises(FileExistsError):
        run_verification(
            "summary", "bloomberg_newsletter", summary_path,
            public_evidence=public_path, gmail_evidence=gmail_path,
            verification_date="2026-08-15",
        )


def test_invalid_public_page_and_source_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        run_verification("public", "wsj_newsletter", tmp_path / "wsj_newsletter_public_2026-08-14.json", public_page="http://example.test")
    with pytest.raises(ValueError):
        run_verification("public", "unknown", tmp_path / "unknown_public_2026-08-14.json")
    with pytest.raises(ValueError):
        run_verification(
            "public", "wsj_newsletter", tmp_path / "wsj_newsletter_public_2026-08-14.json",
            public_page="https://example.test/newsletters",
        )


def test_gmail_summary_rechecks_sender_allowlist(tmp_path):
    class UntrustedMailbox(FakeMailbox):
        pass

    untrusted = NewsletterMessage(
        "<untrusted@example.test>", "alerts@evil.example", None,
        "Newsletter", "<p>not allowed</p>", None, "InternationalNews",
    )
    evidence = run_verification(
        "gmail", "wsj_newsletter", tmp_path / "wsj_newsletter_gmail_2026-08-15.json",
        auth=AuthContext(None, None, True, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "authorized_user_file"),
        mailbox=UntrustedMailbox([untrusted]),
        verification_date="2026-08-15",
    )
    assert evidence["status"] == "operator_action_required"
    assert evidence["reason"] == "NO_NEWSLETTER_MESSAGES"
    assert evidence["message_count"] == 1
    assert evidence["allowlist_hits"] == 0
    assert evidence["sender_domains"] == []


def test_gmail_invalid_label_fails_closed_before_building_service(tmp_path):
    auth = AuthContext(None, None, True, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "authorized_user_file")
    evidence = run_verification(
        "gmail", "wsj_newsletter", tmp_path / "wsj_newsletter_gmail_2026-08-15.json",
        auth=auth, label="Other", mailbox=None,
        verification_date="2026-08-15",
    )
    assert evidence["status"] == "operator_action_required"
    assert evidence["reason"] == "LABEL_NOT_ALLOWED"


def test_summary_rejects_swapped_or_forged_modes(tmp_path):
    public = {"mode": "gmail", "source_id": "wsj_newsletter", "status": "official_url_registered"}
    gmail = {"mode": "gmail", "source_id": "wsj_newsletter", "status": "verified"}
    with pytest.raises(ValueError):
        run_verification(
            "summary", "wsj_newsletter",
            tmp_path / "wsj_newsletter_summary_2026-08-14.json",
            public_evidence=public, gmail_evidence=gmail,
        )


def test_output_basename_must_bind_mode_source_and_date(tmp_path):
    with pytest.raises(ValueError):
        run_verification("public", "wsj_newsletter", tmp_path / "public.json")
    with pytest.raises(ValueError):
        run_verification(
            "public", "wsj_newsletter", tmp_path / "wsj_newsletter_gmail_2026-08-14.json",
        )
    with pytest.raises(ValueError):
        run_verification(
            "public", "wsj_newsletter", tmp_path / "wsj_newsletter_public_2026-08-14.json",
            verification_date="2026-08-15",
        )


def test_public_url_rejects_userinfo_and_non_default_port(tmp_path):
    with pytest.raises(ValueError):
        run_verification(
            "public", "wsj_newsletter", tmp_path / "wsj_newsletter_public_2026-08-14.json",
            public_page="https://user:secret@www.wsj.com/newsletters",
        )
    with pytest.raises(ValueError):
        run_verification(
            "public", "wsj_newsletter", tmp_path / "wsj_newsletter_public_2026-08-14.json",
            public_page="https://www.wsj.com:8443/newsletters",
        )


def test_verifier_rejects_unverified_scope_and_invalid_since_without_mailbox_call(tmp_path):
    mailbox = FakeMailbox([_message()])
    with pytest.raises(ValueError):
        AuthContext(
            None, None, True, "UNVERIFIED_SCOPE", GMAIL_READONLY_SCOPE, "authorized_user_file",
        )

    authorized = AuthContext(
        None, None, True, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "authorized_user_file",
    )
    evidence = run_verification(
        "gmail", "wsj_newsletter", tmp_path / "wsj_newsletter_gmail_2026-08-16.json",
        auth=authorized, since="not-a-since", mailbox=mailbox, verification_date="2026-08-16",
    )
    assert evidence["reason"] == "SINCE_INVALID"
    assert mailbox.calls == []
