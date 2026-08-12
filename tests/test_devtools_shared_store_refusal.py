"""``refuse_if_shared_store`` (ADR-0047 §7, issue #254): every devtools entry
point's own named refusal, mirroring ``serve.py``'s ``refuse_if_*`` family. A
Diagnostic session runs against a local SQLite store with the Local key
cipher only -- a shared Postgres store or a configured Transit token would
mean ``explain``/the diagnostic proxy touch other people's data.
"""

from blindfold.config import Settings
from blindfold_devtools.shared_store_refusal import (
    SharedStoreRefusalError,
    refuse_if_shared_store,
)


def test_refuses_a_shared_postgres_dsn_naming_it():
    settings = Settings(database_url="postgresql://user:pw@db.internal/blindfold")

    try:
        refuse_if_shared_store(settings)
        raised = False
    except SharedStoreRefusalError as exc:
        raised = True
        message = str(exc)

    assert raised
    assert "BLINDFOLD_DATABASE_URL" in message


def test_refuses_a_configured_transit_token_naming_it():
    settings = Settings(database_url="", openbao_token="s.some-vault-token")

    try:
        refuse_if_shared_store(settings)
        raised = False
    except SharedStoreRefusalError as exc:
        raised = True
        message = str(exc)

    assert raised
    assert "BLINDFOLD_OPENBAO_TOKEN" in message


def test_local_sqlite_dsn_with_no_transit_token_is_a_no_op():
    settings = Settings(database_url="sqlite:///tmp/blindfold.sqlite3", openbao_token="")

    refuse_if_shared_store(settings)  # must not raise


def test_unset_database_url_and_no_transit_token_is_a_no_op():
    settings = Settings(database_url="", openbao_token="")

    refuse_if_shared_store(settings)  # must not raise
