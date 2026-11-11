from dataclasses import fields
from pathlib import Path

import pytest

from app.newsletter_ingestion.oauth import (
    AUTHORIZED_READONLY,
    GMAIL_READONLY_SCOPE,
    MAILBOX_AUTH_REQUIRED,
    SCOPE_PROVENANCE_VALUES,
    UNVERIFIED_SCOPE,
    AuthContext,
    load_credentials,
    load_auth_context,
)


def test_auth_context_contains_only_safe_fields():
    assert {field.name for field in fields(AuthContext)} == {
        "credentials_path", "token_path", "authorized", "reason", "scope", "scope_provenance"
    }
    context = AuthContext(None, None, False, MAILBOX_AUTH_REQUIRED)
    assert not context.authorized
    assert context.scope is None and context.scope_provenance is None
    assert GMAIL_READONLY_SCOPE.endswith("gmail.readonly")


def test_missing_auth_is_operator_action_required_without_oauth(tmp_path):
    context = load_auth_context(tmp_path / "client.json", tmp_path / "token.json")
    assert context.authorized is False
    assert context.reason == MAILBOX_AUTH_REQUIRED
    assert context.credentials_path == (tmp_path / "client.json").resolve()


def test_project_internal_secret_paths_fail_closed():
    project = Path(__file__).resolve().parents[2]
    context = load_auth_context(project / "client.json", project / "token.json")
    assert context.authorized is False
    assert context.reason == "CREDENTIAL_PATH_NOT_ALLOWED"


def test_scope_policy_requires_exact_readonly_scope():
    from app.newsletter_ingestion.oauth import _has_only_readonly_scope

    assert _has_only_readonly_scope(type("Creds", (), {"scopes": [GMAIL_READONLY_SCOPE]})())
    assert not _has_only_readonly_scope(type("Creds", (), {"scopes": []})())
    assert not _has_only_readonly_scope(type("Creds", (), {"scopes": None})())
    assert not _has_only_readonly_scope(
        type("Creds", (), {"scopes": [GMAIL_READONLY_SCOPE, "https://mail.google.com/"]})()
    )


def test_manual_auth_context_cannot_load_project_or_relative_credentials(tmp_path):
    project = Path(__file__).resolve().parents[2]
    kwargs = {
        "authorized": True,
        "reason": AUTHORIZED_READONLY,
        "scope": GMAIL_READONLY_SCOPE,
        "scope_provenance": "authorized_user_file",
    }
    assert load_credentials(AuthContext(project / "client.json", tmp_path / "token.json", **kwargs)) is None
    assert load_credentials(AuthContext(tmp_path / "client.json", project / "token.json", **kwargs)) is None
    assert load_credentials(AuthContext(Path("client.json"), Path("token.json"), **kwargs)) is None


def test_unverified_scope_reason_is_not_authorized():
    with pytest.raises(ValueError):
        AuthContext(
            None, None, True, UNVERIFIED_SCOPE,
            GMAIL_READONLY_SCOPE, "authorized_user_file",
        )


def test_auth_context_rejects_unknown_scope_and_provenance():
    with pytest.raises(ValueError):
        AuthContext(None, None, False, MAILBOX_AUTH_REQUIRED, "https://mail.google.com/", None)
    with pytest.raises(ValueError):
        AuthContext(None, None, False, MAILBOX_AUTH_REQUIRED, None, "unknown")
    with pytest.raises(ValueError):
        AuthContext(None, None, True, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "unknown")


def test_auth_context_rejects_inconsistent_authorized_state():
    with pytest.raises(ValueError):
        AuthContext(None, None, True, UNVERIFIED_SCOPE, None, None)
    with pytest.raises(ValueError):
        AuthContext(None, None, False, AUTHORIZED_READONLY, GMAIL_READONLY_SCOPE, "authorized_user_file")


def test_scope_provenance_values_is_public_immutable_and_used_by_validation():
    from app.newsletter_ingestion import SCOPE_PROVENANCE_VALUES as package_values

    assert package_values is SCOPE_PROVENANCE_VALUES
    assert SCOPE_PROVENANCE_VALUES == frozenset({None, "authorized_user_file"})
    assert isinstance(SCOPE_PROVENANCE_VALUES, frozenset)
    with pytest.raises(AttributeError):
        SCOPE_PROVENANCE_VALUES.add("unknown")
    with pytest.raises(ValueError):
        AuthContext(None, None, False, MAILBOX_AUTH_REQUIRED, None, "unknown")


def test_auth_context_rejects_non_bool_authorized_values():
    for value in (1, 0, "true", "false", None, [], object()):
        with pytest.raises(ValueError):
            AuthContext(None, None, value, MAILBOX_AUTH_REQUIRED)
