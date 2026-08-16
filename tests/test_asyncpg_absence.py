"""Static import check (issue #319): no module under src/blindfold/ may import
asyncpg. asyncpg's only two importers (``store/etl.py``, ``store/postgres.py``) are
Docker-gated-test-only ETL/repository helpers -- nothing in the shipped package (the
proxy's actual Postgres path is synchronous ``psycopg`` via ``store/dialect.py``) ever
reaches them. Mirrors ``tests/test_devtools_absence.py``'s AST-walk pattern (issue #252)
so a conditional, function-local, or try/except import is caught too, not just a
module-level one.
"""

import ast
import pathlib

FORBIDDEN_MODULE = "asyncpg"


def _find_forbidden_imports(root: pathlib.Path, forbidden: str) -> list[str]:
    """One "path:lineno: <import statement>" string per import of `forbidden` found
    anywhere in any .py file under `root`, at any nesting depth."""
    violations = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                        violations.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == forbidden or module.startswith(forbidden + "."):
                    names = ", ".join(alias.name for alias in node.names)
                    violations.append(f"{path}:{node.lineno}: from {module} import {names}")
    return violations


def test_no_blindfold_module_imports_asyncpg():
    src_blindfold = pathlib.Path(__file__).parent.parent / "src" / "blindfold"

    violations = _find_forbidden_imports(src_blindfold, FORBIDDEN_MODULE)

    assert not violations, (
        "no module under src/blindfold/ may import asyncpg (issue #319) -- it is a "
        "dev/test-only dependency now, but found:\n" + "\n".join(violations)
    )
