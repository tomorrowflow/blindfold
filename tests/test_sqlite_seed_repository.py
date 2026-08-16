"""SQLite-backed seed repository: the ETL seeded_pairs() read, synchronous sqlite3 on
the SQLite backend (ADR-0043 §3, issue #203).

The only async (asyncpg) store path is the ETL seeded_pairs() read
(``tests/support/postgres_seed_repository.py``'s ``PostgresSeedRepository`` -- moved out
of ``src/blindfold/store`` in issue #319, test-only since nothing shipped ever imported
it); asyncpg stays Postgres-only. On the SQLite backend the same read runs synchronous
stdlib ``sqlite3`` via the dialect seam (:mod:`blindfold.store.dialect`, issue #200)
instead -- a startup/Setup path, not the request hot path.

No SQLite ETL-loading path is introduced: the SQLite schema is populated the same way
production already populates it (``PostgresEntityGraphStore``, issue #200, which
dispatches onto ``sqlite:///`` DSNs), by seeding through
:func:`blindfold.bootstrap.seed_entity_graph_from_vendored_seed` -- the entity-graph
store duck-types the in-memory ``EntityGraph`` interface
(``add_entity``/``search_by_real_name``/``add_relationship``/``add_role_assignment``),
so the vendored-seed repository's ``seed_entity_graph`` writes through it unchanged.

Persons are now ciphertext-only (ADR-0045 §5, issue #229): seeding and reading persons
requires a mapping cipher.  Test 1 uses LocalKeyCipher; test 2 uses the Transit stub
and additionally writes terms ciphertext (the optional columns) to exercise the
COALESCE/mixed read path.

Leak-audit clauses: A/B/C/D/E/F -- N/A, no proxy request path touched (this is a
startup/Setup read, mirroring issue #200's analysis). G (mapping secrecy) -- exercised
for the read side: the mapping-cipher test asserts the SQLite reader decrypts persons
via the cipher; terms/variations use COALESCE so both ciphertext-set and plaintext-only
rows are returned correctly.
"""

from __future__ import annotations

import base64
import json
import os

import httpx


def _make_stub_transit():
    """Deterministic TransitClient stub: encrypt(v) -> vault:v1:enc:{v} (mirrors
    test_transit_ciphertext_columns.py's helper -- same fixture shape, new module
    since this one exercises the SQLite reader, not the Postgres/asyncpg ETL)."""
    from blindfold.transit import TransitClient

    def _b64(s: str) -> str:
        return base64.b64encode(s.encode()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path

        if "encrypt" in path:
            raw = base64.b64decode(body["plaintext"]).decode()
            return httpx.Response(200, json={"data": {"ciphertext": f"vault:v1:enc:{raw}"}})

        if "decrypt" in path:
            ct = body["ciphertext"]
            if ct.startswith("vault:v1:enc:"):
                plain = ct[len("vault:v1:enc:"):]
                return httpx.Response(200, json={"data": {"plaintext": _b64(plain)}})
            return httpx.Response(400, json={"errors": ["bad ciphertext"]})

        if "hmac" in path:
            raw = base64.b64decode(body["input"]).decode()
            return httpx.Response(200, json={"data": {"hmac": f"vault:v1:hmac:{raw}"}})

        return httpx.Response(404, json={"errors": ["not found"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _seed_sqlite_from_vendored(dsn: str, mapping_cipher=None) -> str:
    """Populate a fresh sqlite:/// DSN with the vendored seed via the entity-graph
    store seam (issue #200), the same path production uses -- NOT a new SQLite ETL.
    Returns the workspace slug used.

    ``mapping_cipher`` must be supplied to persist person entities (ADR-0045 §5,
    issue #229); without it persons are ephemeral and the seed will fail when
    ``seed_entity_graph`` tries to call ``add_role_assignment`` on an ephemeral
    person's UUID entity_id.
    """
    from blindfold.bootstrap import seed_entity_graph_from_vendored_seed
    from blindfold.store import vendored_seed_repository
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(dsn, mapping_cipher=mapping_cipher)
    repo = vendored_seed_repository()
    workspace = repo.workspace_slug()
    store.create_workspace(workspace, workspace)
    seed_entity_graph_from_vendored_seed(entity_graph=store, workspace=workspace, repo=repo)
    return workspace


def test_sqlite_repository_seeded_pairs_match_the_vendored_seam(tmp_path):
    """Seeded pairs from the SQLite store match the in-process vendored repository.

    Persons, terms, and both variation tables are ciphertext-only (issue #229/#230):
    a LocalKeyCipher is used to seed and read every real value.
    """
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store import vendored_seed_repository
    from blindfold.store.sqlite import SQLiteSeedRepository

    key = base64.b64encode(os.urandom(32)).decode()
    cipher = LocalKeyCipher(key)

    dsn = f"sqlite:///{tmp_path / 'entity_graph.sqlite3'}"
    _seed_sqlite_from_vendored(dsn, mapping_cipher=cipher)

    db_pairs = set(SQLiteSeedRepository(dsn, mapping_cipher=cipher).seeded_pairs())
    vendored_pairs = set(vendored_seed_repository().seeded_pairs())

    # Both implementations of the seeded_pairs() seam expose the same (real -> surrogate)
    # pairs, including every coreference variation -- the hermetic round-trip and the
    # persisted SQLite graph agree on every surrogate (same assertion shape as
    # test_entity_graph_postgres.py's Postgres counterpart).
    assert db_pairs == vendored_pairs
    assert ("Martin Bach", dict(vendored_pairs)["Martin Bach"]) in db_pairs
    assert dict(db_pairs)["Bach"] == dict(db_pairs)["Martin Bach"]
    assert dict(db_pairs)["Enerva"] == dict(db_pairs)["Enervia"]


def test_sqlite_repository_with_transit_decrypts_ciphertext_columns(tmp_path):
    """Transit-backed read decrypts all ciphertext columns and yields the same pairs.

    Every real value (persons, terms, both variation tables) is encrypted at insert
    time through the Transit stub used as the mapping cipher (issue #229/#230) -- the
    SQLiteSeedRepository decrypts them all on read.
    """
    from blindfold.store import vendored_seed_repository
    from blindfold.store.sqlite import SQLiteSeedRepository

    dsn = f"sqlite:///{tmp_path / 'entity_graph.sqlite3'}"

    transit = _make_stub_transit()
    _seed_sqlite_from_vendored(dsn, mapping_cipher=transit)

    db_pairs = set(SQLiteSeedRepository(dsn, transit=transit).seeded_pairs())
    vendored_pairs = set(vendored_seed_repository().seeded_pairs())

    # The mapping-cipher read path yields pairs identical to the plaintext path --
    # decrypted real values, paired with the same stable surrogates.
    assert db_pairs == vendored_pairs
    assert dict(db_pairs)["Bach"] == dict(db_pairs)["Martin Bach"]
