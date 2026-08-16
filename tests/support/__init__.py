"""Test-only support modules (issue #319): Docker-gated-test helpers that speak the
async ``asyncpg`` Postgres driver, kept out of ``src/blindfold`` because nothing shipped
ever imports them -- the shipped Postgres path is synchronous ``psycopg`` throughout
(``blindfold.store.dialect``, ``blindfold.store.entity_graph_store``).
"""
