"""Immutable public/Gmail/summary evidence for Newsletter sources.

The verifier is deliberately conservative: it never enables a source, never
updates production data, and never starts an OAuth browser flow.  Public mode
records an explicitly supplied official directory URL; Gmail mode requires an
already-authorized readonly context and an injectable mailbox client.  Summary
mode only reads the two prior evidence objects and records their SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from email.utils import parseaddr
from urllib.parse import urlparse

from .gmail_client import DEFAULT_LABEL, GmailMailboxClient, build_service, validate_since
from .oauth import (
    AUTHORIZED_READONLY,
    GMAIL_READONLY_SCOPE,
    UNVERIFIED_SCOPE,
    AuthContext,
    MAILBOX_AUTH_REQUIRED,
    load_auth_context,
)


SOURCE_PAGES = {
    "reuters_international": "https://www.reuters.com/",
    "ft_alphaville": "https://www.ft.com/alphaville?format=rss",
    "wsj_newsletter": "https://www.wsj.com/newsletters",
    "bloomberg_newsletter": "https://www.bloomberg.com/newsletters",
}
SOURCE_DOMAINS = {
    "reuters_international": {"reuters.com"},
    "ft_alphaville": {"ft.com"},
    "wsj_newsletter": {"wsj.com", "dowjones.com"},
    "bloomberg_newsletter": {"bloomberg.com"},
}
VALID_SOURCES = frozenset(SOURCE_PAGES)
OFFICIAL_URL_REGISTERED = "official_url_registered"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def run_verification(
    mode: str,
    source_id: str,
    output_path: str | Path,
    *,
    auth: AuthContext | None = None,
    public_page: str | None = None,
    label: str = DEFAULT_LABEL,
    since: str = "30d",
    mailbox=None,
    public_evidence: dict | str | Path | None = None,
    gmail_evidence: dict | str | Path | None = None,
    verification_date: str | None = None,
) -> dict:
    """Create one write-once evidence object for ``mode``.

    The output is never overwritten.  The caller may pass dictionaries or
    paths to summary mode; the source evidence files are opened read-only and
    are not rewritten.
    """

    mode = str(mode).strip().lower()
    _validate_source(source_id)
    output = Path(output_path)
    if mode not in {"public", "gmail", "summary"}:
        raise ValueError("mode must be public, gmail, or summary")
    as_of = _normalize_as_of(verification_date)
    _validate_output_path(output, source_id, mode, as_of)
    if output.exists():
        raise FileExistsError(f"verification output already exists: {output}")
    if mode == "public":
        evidence = _public_evidence(source_id, public_page or SOURCE_PAGES[source_id], as_of)
    elif mode == "gmail":
        evidence = _gmail_evidence(
            source_id, auth=auth, label=label, since=since, mailbox=mailbox, as_of=as_of
        )
    else:
        if public_evidence is None or gmail_evidence is None:
            raise ValueError("summary mode requires public_evidence and gmail_evidence")
        evidence = _summary_evidence(
            source_id, public_evidence, gmail_evidence, as_of=as_of
        )
    evidence = _add_hash(evidence)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence


def _public_evidence(source_id: str, page: str, as_of: str) -> dict:
    parsed = urlparse(str(page))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("public page must be an HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("public page must not contain URL userinfo")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("public page has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("public page must use the HTTPS default port")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not _host_allowed(host, SOURCE_DOMAINS[source_id]):
        raise ValueError("public page host is not in the source allowlist")
    # Public mode does not crawl an article or pretend that a directory page
    # is an email.  The explicitly supplied official URL is the auditable
    # input; live HTTP probing belongs to an isolated operator run.
    return {
        "schema_version": "newsletter-availability.v1",
        "mode": "public",
        "source_id": source_id,
        "verification_date": as_of,
        "status": OFFICIAL_URL_REGISTERED,
        "public_page_url": str(page),
        "observed_newsletter_names": [],
        "observed_sender_domains": sorted(SOURCE_DOMAINS[source_id]),
        "auth_state": "not_applicable",
        "allowlist_hit": False,
        "errors": [],
        "verifier": "app.newsletter_ingestion.verify_sources",
        "verification_scope": "official_directory_url_recorded_no_article_fetch",
    }


def _gmail_evidence(
    source_id: str,
    *,
    auth: AuthContext | None,
    label: str,
    since: str,
    mailbox,
    as_of: str,
) -> dict:
    base = {
        "schema_version": "newsletter-availability.v1",
        "mode": "gmail",
        "source_id": source_id,
        "verification_date": as_of,
        "label": label,
        "since": since,
        "verifier": "app.newsletter_ingestion.verify_sources",
    }
    if not _auth_ready(auth):
        reason = MAILBOX_AUTH_REQUIRED if auth is None or not auth.authorized else UNVERIFIED_SCOPE
        return {
            **base,
            "status": "operator_action_required",
            "reason": reason,
            "auth_state": auth.reason if auth else MAILBOX_AUTH_REQUIRED,
            "message_count": 0,
            "parsed_count": 0,
            "message_id_hashes": [],
            "allowlist_hits": 0,
            "sender_domains": [],
            "errors": [],
        }
    if label != DEFAULT_LABEL:
        return {
            **base,
            "status": "operator_action_required",
            "reason": "LABEL_NOT_ALLOWED",
            "auth_state": "authorized_readonly",
            "message_count": 0,
            "parsed_count": 0,
            "message_id_hashes": [],
            "allowlist_hits": 0,
            "sender_domains": [],
            "errors": [],
        }
    if not validate_since(since):
        return {
            **base,
            "status": "operator_action_required",
            "reason": "SINCE_INVALID",
            "auth_state": "authorized_readonly",
            "message_count": 0,
            "parsed_count": 0,
            "message_id_hashes": [],
            "allowlist_hits": 0,
            "sender_domains": [],
            "errors": [],
        }
    client = mailbox
    if client is None:
        service = build_service(auth)
        client = GmailMailboxClient(service=service, label=label, modify=False, auth=auth)
    messages = client.list_messages(label, set(SOURCE_DOMAINS[source_id]), since)
    allowed_messages = [
        message
        for message in messages
        if _sender_allowed(message.sender, SOURCE_DOMAINS[source_id])
    ]
    message_hashes = sorted(
        hashlib.sha256(str(message.message_id).encode("utf-8")).hexdigest()
        for message in allowed_messages
    )
    if not allowed_messages:
        return {
            **base,
            "status": "operator_action_required",
            "reason": "NO_NEWSLETTER_MESSAGES",
            "auth_state": "authorized_readonly",
            "message_count": len(messages),
            "parsed_count": 0,
            "message_id_hashes": [],
            "allowlist_hits": 0,
            "sender_domains": [],
            "errors": [],
        }
    domains = sorted(
        {
            str(message.sender).rsplit("@", 1)[-1].lower().rstrip(".")
            for message in allowed_messages
            if "@" in str(message.sender)
        }
    )
    return {
        **base,
        "status": "verified",
        "reason": "READONLY_MESSAGES_AVAILABLE",
        "auth_state": "authorized_readonly",
        "message_count": len(messages),
        "parsed_count": len(allowed_messages),
        "message_id_hashes": message_hashes,
        "allowlist_hits": len(allowed_messages),
        "sender_domains": domains,
        "errors": [],
    }


def _summary_evidence(
    source_id: str,
    public_evidence: dict | str | Path,
    gmail_evidence: dict | str | Path,
    *,
    as_of: str,
) -> dict:
    public, public_hash = _load_evidence(public_evidence)
    gmail, gmail_hash = _load_evidence(gmail_evidence)
    if public.get("source_id") != source_id or gmail.get("source_id") != source_id:
        raise ValueError("summary evidence source_id mismatch")
    if public.get("mode") != "public":
        raise ValueError("summary public evidence mode mismatch")
    if gmail.get("mode") != "gmail":
        raise ValueError("summary Gmail evidence mode mismatch")
    both_verified = (
        public.get("status") == OFFICIAL_URL_REGISTERED
        and gmail.get("status") == "verified"
    )
    return {
        "schema_version": "newsletter-availability.v1",
        "mode": "summary",
        "source_id": source_id,
        "verification_date": as_of,
        "status": "verified" if both_verified else "operator_action_required",
        "reason": "OFFICIAL_URL_AND_GMAIL_VERIFIED" if both_verified else str(gmail.get("reason") or "SOURCE_NOT_VERIFIED"),
        "public_status": public.get("status"),
        "gmail_status": gmail.get("status"),
        "public_evidence_sha256": public_hash,
        "gmail_evidence_sha256": gmail_hash,
        "merge_rule": "verified iff public.status == official_url_registered and gmail.status == verified",
        "auth_state": gmail.get("auth_state", "unknown"),
        "errors": [],
        "verifier": "app.newsletter_ingestion.verify_sources",
    }


def _load_evidence(value: dict | str | Path) -> tuple[dict, str]:
    if isinstance(value, dict):
        data = dict(value)
        raw = _canonical_json(data)
    else:
        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(path)
        raw_bytes = path.read_bytes()
        data = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("evidence must be a JSON object")
        raw = raw_bytes
    return data, hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode("utf-8")).hexdigest()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _add_hash(evidence: dict) -> dict:
    unsigned = dict(evidence)
    unsigned.pop("evidence_sha256", None)
    payload = _canonical_json(unsigned).encode("utf-8")
    return {**unsigned, "evidence_sha256": hashlib.sha256(payload).hexdigest()}


def _validate_source(source_id: str) -> None:
    if source_id not in VALID_SOURCES:
        raise ValueError(f"unknown Newsletter source: {source_id}")


def _normalize_as_of(value: str | None) -> str:
    if value is None:
        return date.today().isoformat()
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError("verification_date must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("verification_date must be a real calendar date") from exc


def _validate_output_path(output: Path, source_id: str, mode: str, as_of: str) -> None:
    expected = f"{source_id}_{mode}_{as_of}.json"
    if output.name != expected:
        raise ValueError(f"output basename must be {expected}")


def _auth_ready(auth: AuthContext | None) -> bool:
    return bool(
        auth is not None
        and auth.authorized
        and auth.reason == AUTHORIZED_READONLY
        and auth.scope == GMAIL_READONLY_SCOPE
        and auth.scope_provenance == "authorized_user_file"
    )


def _host_allowed(host: str, allowlist: set[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in allowlist)


def _sender_allowed(sender: str, allowlist: set[str]) -> bool:
    address = parseaddr(sender or "")[1].strip().lower()
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].rstrip(".")
    return _host_allowed(domain, allowlist)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("public", "gmail", "summary"), required=True)
    parser.add_argument("--source", dest="source_id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-page")
    parser.add_argument("--public-evidence", type=Path)
    parser.add_argument("--gmail-evidence", type=Path)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--token", type=Path)
    parser.add_argument("--mailbox", choices=("gmail",), default="gmail")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--since", default="30d")
    parser.add_argument("--as-of", dest="as_of")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        auth = None
        if args.mode == "gmail":
            auth = load_auth_context(args.credentials, args.token)
        result = run_verification(
            args.mode,
            args.source_id,
            args.output,
            auth=auth,
            public_page=args.public_page,
            label=args.label,
            since=args.since,
            public_evidence=args.public_evidence,
            gmail_evidence=args.gmail_evidence,
            verification_date=args.as_of,
        )
    except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
        print(f"verification failed: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
