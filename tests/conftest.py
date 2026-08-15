import os
import sys

import pytest

# Pin the whole suite to the explicit in-memory sentinel by default (ADR-0043 §1,
# issue #204), set at conftest import time -- before pytest collection imports any
# test module's `from blindfold.app import app`, which runs app.py's module-level
# bootstrap (get_rbac()/get_reidentify_store()) against whatever BLINDFOLD_DATABASE_URL
# resolves to right then. A per-test fixture would be too late for that one-time
# import-time read. An unset BLINDFOLD_DATABASE_URL no longer means ephemeral
# in-memory -- it now resolves to a durable SQLite store at a computed default path
# under the real OS Store directory (blindfold.config.resolve_store_dir()). Almost
# none of the suite sets this var (it relied on the old accidental unset default),
# so without this, nearly every test that touches a store-backed endpoint would
# create/reuse a real SQLite file on the machine running the tests -- cross-test
# pollution, not hermetic isolation. A test that cares about the store-selection
# contract itself (this issue's own tests) overrides the var with its own
# monkeypatch.setenv/delenv call, which -- being scoped to that test -- reverts to
# this default at teardown.
os.environ["BLINDFOLD_DATABASE_URL"] = "memory://"


@pytest.fixture
def anyio_backend():
    # Run anyio-marked ASGI tests on asyncio only (no trio dependency).
    return "asyncio"


@pytest.fixture
def block_import(monkeypatch):
    """Make ``import <name>`` raise ``ImportError`` for the rest of the test,
    regardless of whether the package is actually installed in this environment
    (issue #284) -- so a test asserting "this optional dependency is absent"
    behavior doesn't depend on ambient venv state. Shared here rather than
    re-improvised per test file: setting ``sys.modules[name] = None`` is the
    documented way to force the next ``import name`` to fail closed, and
    ``monkeypatch.setitem`` reverts it (to whatever was there before, present or
    absent) at teardown.
    """

    def _block(name: str) -> None:
        monkeypatch.setitem(sys.modules, name, None)

    return _block
