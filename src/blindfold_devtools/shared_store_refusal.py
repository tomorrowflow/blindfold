"""``refuse_if_shared_store`` (ADR-0047 §7): the refusal every devtools entry
point carries, mirroring ``blindfold.serve``'s own ``refuse_if_*`` family. A
Diagnostic session runs against a local SQLite store with the Local key
cipher only -- a shared Postgres store or a configured Transit token would
mean the diagnostic proxy (or ``explain``, issue #255) touches other
people's data, not just the operator's own opted-in machine.

The refusal is a property of the *capability*, not of the process: ordinary
``blindfold serve`` against a shared store captures nothing (devtools is
never in that loop), so this lives in ``blindfold_devtools``, never in
``blindfold.serve``.
"""

from __future__ import annotations

from blindfold.config import Settings

_SQLITE_DSN_PREFIX = "sqlite:///"


class SharedStoreRefusalError(RuntimeError):
    """Raised when a devtools entry point is configured against a shared store."""


def refuse_if_shared_store(settings: Settings) -> None:
    """Fail fast if ``settings`` names a shared Postgres store or Transit token.

    A no-op for the Diagnostic session's own posture: an unset/``memory://``
    database URL (falsy), or an explicit ``sqlite:///`` DSN, plus no OpenBao
    Transit token configured (the Local key cipher, or no cipher at all, are
    both fine -- Transit specifically implies a *shared* secret store).
    """
    if settings.database_url and not settings.database_url.startswith(_SQLITE_DSN_PREFIX):
        raise SharedStoreRefusalError(
            "refusing to start a Diagnostic session: BLINDFOLD_DATABASE_URL names "
            "a shared store; a Diagnostic session may only run against a local "
            "SQLite store (ADR-0047 §7). Unset it, or point it at a "
            "sqlite:/// path."
        )
    if settings.openbao_token:
        raise SharedStoreRefusalError(
            "refusing to start a Diagnostic session: BLINDFOLD_OPENBAO_TOKEN "
            "configures OpenBao Transit, a shared mapping cipher; a Diagnostic "
            "session may only use the Local key cipher (ADR-0047 §7). Unset it."
        )
