"""Static import check (ADR-0047 §12 check 1): no module under src/blindfold/ may import
blindfold_devtools. Walks the AST rather than grepping, so a conditional, function-local, or
try/except import is caught too, not just a module-level one.
"""

import ast
import pathlib

FORBIDDEN_MODULE = "blindfold_devtools"


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


def test_check_catches_module_level_import(tmp_path):
    (tmp_path / "offender.py").write_text("import blindfold_devtools\n")

    violations = _find_forbidden_imports(tmp_path, FORBIDDEN_MODULE)

    assert len(violations) == 1
    assert "offender.py" in violations[0]
    assert "blindfold_devtools" in violations[0]


def test_check_catches_function_local_import(tmp_path):
    (tmp_path / "offender.py").write_text(
        "def helper():\n"
        "    import blindfold_devtools\n"
        "    return blindfold_devtools\n"
    )

    violations = _find_forbidden_imports(tmp_path, FORBIDDEN_MODULE)

    assert len(violations) == 1
    assert "offender.py" in violations[0]


def test_check_catches_try_except_import_error(tmp_path):
    (tmp_path / "offender.py").write_text(
        "try:\n"
        "    import blindfold_devtools\n"
        "except ImportError:\n"
        "    blindfold_devtools = None\n"
    )

    violations = _find_forbidden_imports(tmp_path, FORBIDDEN_MODULE)

    assert len(violations) == 1
    assert "offender.py" in violations[0]


def test_check_catches_from_import(tmp_path):
    (tmp_path / "offender.py").write_text("from blindfold_devtools import capture\n")

    violations = _find_forbidden_imports(tmp_path, FORBIDDEN_MODULE)

    assert len(violations) == 1
    assert "offender.py" in violations[0]
    assert "capture" in violations[0]


def test_check_ignores_unrelated_imports(tmp_path):
    (tmp_path / "clean.py").write_text("import os\nfrom pathlib import Path\n")

    violations = _find_forbidden_imports(tmp_path, FORBIDDEN_MODULE)

    assert violations == []


def test_no_blindfold_module_imports_devtools():
    src_blindfold = pathlib.Path(__file__).parent.parent / "src" / "blindfold"

    violations = _find_forbidden_imports(src_blindfold, FORBIDDEN_MODULE)

    assert not violations, (
        "no module under src/blindfold/ may import blindfold_devtools (ADR-0047 §2/§12), "
        "but found:\n" + "\n".join(violations)
    )
