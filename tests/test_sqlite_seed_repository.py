"""SQLite-backed seed repository: the ETL seeded_pairs() read, synchronous sqlite3 on
the SQLite backend (ADR-0043 §3, issue #203).

The only async (asyncpg) store path is the ETL seeded_pairs() read
(:mod:`blindfold.store.postgres`'s ``PostgresSeedRepository``); asyncpg stays
Postgres-only. On the SQLite backend the same read runs synchronous stdlib ``sqlite3``
via the dialect seam (:mod:`blindfold.store.dialect`, issue #200) instead -- a
startup/Setup path, not the request hot path.

No SQLite ETL-loading path is introduced: the SQLite schema is populated the same way
production already populates it (``PostgresEntityGraphStore``, issue #200, which
dispatches onto ``sqlite:///`` DSNs), by seeding through
:func:`blindfold.bootstrap.seed_entity_graph_from_vendored_seed` -- the entity-graph
store duck-types the in-memory ``EntityGraph`` interface
(``add_entity``/``search_by_real_name``/``add_relationship``/``add_role_assignment``),
so the vendored-seed repository's ``seed_entity_graph`` writes through it unchanged.

Leak-audit clauses: A/B/C/D/E/F -- N/A, no proxy request path touched (this is a
startup/Setup read, mirroring issue #200's analysis). G (mapping secrecy) -- exercised
for the read side only: the Transit-ciphertext test asserts the SQLite reader decrypts
via Transit rather than reading the plaintext column when Transit is wired; this slice
adds no new SQLite write-side encryption (that stays #10/ADR-0008 scope).
"""

from __future__ import annotations

import base64
import json

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

        return httpx.Response(404, json={"errors": ["not found"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _write_ciphertext_columns(dsn: str, transit) -> None:
    """Synchronously encrypt every real-value column already written by
    _seed_sqlite_from_vendored and write the ciphertext (test scaffolding only --
    production SQLite Transit-write wiring is out of scope for this slice / #10)."""
    from blindfold.store.dialect import connect

    with connect(dsn) as conn:
        for table, col, ct_col in (
            ("persons", "canonical_name", "canonical_name_ciphertext"),
            ("terms", "canonical_name", "canonical_name_ciphertext"),
            ("person_variations", "value", "value_ciphertext"),
            ("term_variations", "value", "value_ciphertext"),
        ):
            rows = conn.execute(f"SELECT id, {col} FROM {table}").fetchall()
            for row_id, value in rows:
                ciphertext = transit.encrypt(value)
                conn.execute(
                    f"UPDATE {table} SET {ct_col} = %s WHERE id = %s",
                    (ciphertext, row_id),
                )
        conn.commit()


def _seed_sqlite_from_vendored(dsn: str) -> str:
    """Populate a fresh sqlite:/// DSN with the vendored seed via the entity-graph
    store seam (issue #200), the same path production uses -- NOT a new SQLite ETL.
    Returns the workspace slug used.
    """
    from blindfold.bootstrap import seed_entity_graph_from_vendored_seed
    from blindfold.store import vendored_seed_repository
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(dsn)
    repo = vendored_seed_repository()
    workspace = repo.workspace_slug()
    store.create_workspace(workspace, workspace)
    seed_entity_graph_from_vendored_seed(entity_graph=store, workspace=workspace, repo=repo)
    return workspace


def test_sqlite_repository_seeded_pairs_match_the_vendored_seam(tmp_path):
    from blindfold.store import vendored_seed_repository
    from blindfold.store.sqlite import SQLiteSeedRepository

    dsn = f"sqlite:///{tmp_path / 'entity_graph.sqlite3'}"
    _seed_sqlite_from_vendored(dsn)

    db_pairs = set(SQLiteSeedRepository(dsn).seeded_pairs())
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
    from blindfold.store import vendored_seed_repository
    from blindfold.store.sqlite import SQLiteSeedRepository

    dsn = f"sqlite:///{tmp_path / 'entity_graph.sqlite3'}"
    _seed_sqlite_from_vendored(dsn)

    transit = _make_stub_transit()
    _write_ciphertext_columns(dsn, transit)

    db_pairs = set(SQLiteSeedRepository(dsn, transit=transit).seeded_pairs())
    vendored_pairs = set(vendored_seed_repository().seeded_pairs())

    # The Transit-ciphertext read path yields pairs identical to the plaintext path --
    # decrypted real values, paired with the same stable surrogates.
    assert db_pairs == vendored_pairs
    assert dict(db_pairs)["Bach"] == dict(db_pairs)["Martin Bach"]
