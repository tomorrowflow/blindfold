"""SC-2: branchTouchesSpa must be shell-safe — execFileSync with args array, not execSync."""

import pathlib
import re


def _get_function_body(source: str, func_name: str) -> str:
    start = source.find(f"function {func_name}(")
    if start == -1:
        return ""
    brace_start = source.index("{", start)
    depth = 0
    for i, ch in enumerate(source[brace_start:], brace_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : i + 1]
    return ""


def test_branch_touches_spa_no_bare_execsync():
    """SC-2: branchTouchesSpa must not use bare execSync (shell injection surface)."""
    source = (pathlib.Path(__file__).parent.parent / ".sandcastle" / "main.mts").read_text()
    body = _get_function_body(source, "branchTouchesSpa")
    assert body, "branchTouchesSpa not found in .sandcastle/main.mts"
    has_bare_execsync = bool(re.search(r"(?<!File)execSync\(", body))
    assert not has_bare_execsync, (
        "branchTouchesSpa uses bare execSync — SC-2 shell injection surface not fixed"
    )


def test_branch_touches_spa_execfilesync_with_args_array():
    """SC-2: branchTouchesSpa must call execFileSync with 'git' as first positional arg."""
    source = (pathlib.Path(__file__).parent.parent / ".sandcastle" / "main.mts").read_text()
    body = _get_function_body(source, "branchTouchesSpa")
    assert body, "branchTouchesSpa not found in .sandcastle/main.mts"
    assert "execFileSync(" in body, "branchTouchesSpa must use execFileSync"
    # The first arg must be the literal string "git" — not a shell command string
    assert bool(re.search(r'execFileSync\(\s*"git"', body)), (
        "execFileSync must be called with \"git\" as the first arg (no shell string)"
    )
