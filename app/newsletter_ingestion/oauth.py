"""Safe boundary for one-time Gmail OAuth authorization.

No OAuth browser flow is started by importing this module or by
``load_auth_context``.  Credentials and tokens are read only from paths
outside the project; the context deliberately contains no secret material.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAILBOX_AUTH_REQUIRED = "MAILBOX_AUTH_REQUIRED"
AUTHORIZED_READONLY = "AUTHORIZED_READONLY"
UNVERIFIED_SCOPE = "UNVERIFIED_SCOPE"
# Backward-compatible name for callers that only used the reason constant;
# the value is intentionally the fail-closed reason.
GMAIL_SCOPE_NOT_READONLY = UNVERIFIED_SCOPE
SCOPE_PROVENANCE_VALUES = frozenset({None, "authorized_user_file"})
_SCOPE_PROVENANCE = "authorized_user_file"


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Non-secret authorization metadata passed to mailbox code."""

    credentials_path: Path | None
    token_path: Path | None
    authorized: bool
    reason: str
    scope: str | None = None
    scope_provenance: str | None = None

    def __post_init__(self) -> None:
        """Enforce the non-secret authorization state machine at construction."""

        if type(self.authorized) is not bool:
            raise ValueError("AuthContext authorized must be a bool")
        if self.scope not in {None, GMAIL_READONLY_SCOPE}:
            raise ValueError("AuthContext scope must be Gmail readonly or None")
        if self.scope_provenance not in SCOPE_PROVENANCE_VALUES:
            raise ValueError("AuthContext scope provenance is not allowed")
        if not self.authorized:
            if self.scope is not None or self.scope_provenance is not None:
                raise ValueError("unauthorized AuthContext cannot carry scope metadata")
            return
        if (
            self.reason != AUTHORIZED_READONLY
            or self.scope != GMAIL_READONLY_SCOPE
            or self.scope_provenance != _SCOPE_PROVENANCE
        ):
            raise ValueError("authorized AuthContext must be verified Gmail readonly")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser().resolve()


def _outside_project(path: Path | None) -> bool:
    if path is None:
        return True
    try:
        path.relative_to(_project_root())
    except ValueError:
        return True
    return False


def load_auth_context(
    credentials_path: Path | str | None,
    token_path: Path | str | None,
) -> AuthContext:
    """Inspect local OAuth files without starting authorization.

    ``credentials_path`` and ``token_path`` are returned as metadata only.
    The files are never copied into the repository and their contents are
    never included in the return value.  A token is considered authorized only
    when Google Auth can parse it, it is not expired, and it includes the
    hard-coded readonly scope.
    """

    credentials = _safe_path(credentials_path)
    token = _safe_path(token_path)
    if not _outside_project(credentials) or not _outside_project(token):
        return AuthContext(credentials, token, False, "CREDENTIAL_PATH_NOT_ALLOWED")
    if token is None or not token.is_file():
        return AuthContext(credentials, token, False, MAILBOX_AUTH_REQUIRED)
    try:
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(token))
    except Exception:
        # Deliberately do not expose parser errors; token contents can appear
        # in provider exceptions and must not reach logs or evidence.
        return AuthContext(credentials, token, False, MAILBOX_AUTH_REQUIRED)
    if not creds or not getattr(creds, "valid", False):
        return AuthContext(credentials, token, False, MAILBOX_AUTH_REQUIRED)
    if not _has_only_readonly_scope(creds):
        return AuthContext(credentials, token, False, UNVERIFIED_SCOPE)
    return AuthContext(
        credentials,
        token,
        True,
        AUTHORIZED_READONLY,
        GMAIL_READONLY_SCOPE,
        _SCOPE_PROVENANCE,
    )


def load_credentials(auth: AuthContext):
    """Load Google credentials for a provider adapter, without OAuth flow.

    This helper is intentionally separate from :func:`load_auth_context` and
    is only called by an explicitly requested Gmail operation.  It never calls
    ``run_local_server`` or any other interactive authorization method.
    """

    if not isinstance(auth, AuthContext):
        return None
    credentials_path = _safe_path(auth.credentials_path)
    token_path = _safe_path(auth.token_path)
    if (
        not auth.authorized
        or auth.reason != AUTHORIZED_READONLY
        or auth.scope != GMAIL_READONLY_SCOPE
        or auth.scope_provenance not in SCOPE_PROVENANCE_VALUES
        or auth.scope_provenance != _SCOPE_PROVENANCE
        or credentials_path is None
        or token_path is None
        or not _outside_project(credentials_path)
        or not _outside_project(token_path)
        or not credentials_path.is_file()
        or not token_path.is_file()
    ):
        return None
    try:
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_file(str(token_path))
    except Exception:
        return None
    if not getattr(credentials, "valid", False):
        return None
    if not _has_only_readonly_scope(credentials):
        return None
    return credentials


def _has_only_readonly_scope(credentials: object) -> bool:
    """Return true only for an explicitly recorded Gmail readonly scope.

    An empty/unknown scope set is fail-closed.  Accepting it would make an
    otherwise valid token look readonly without evidence that it was limited
    to Gmail message reads.
    """

    scopes = getattr(credentials, "scopes", None)
    if not scopes:
        return False
    try:
        normalized = {str(scope).strip() for scope in scopes if str(scope).strip()}
    except TypeError:
        return False
    return normalized == {GMAIL_READONLY_SCOPE}
