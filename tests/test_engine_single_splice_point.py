"""Issue #405: `_apply_spans` docstring (issue #325) claims to be "the one place a
span's `surrogate` actually gets spliced in" -- true for three of the four rewrite
paths, but `_apply_provisional_pairs` still mutated a cumulative result via
`re.subn`, one pattern per key, never surfacing the offsets it changed. This is the
static half of the acceptance criterion "no rewrite path in engine.py mutates leaf
text outside `_apply_spans`": a regex `.sub()`/`.subn()` call is exactly the shape a
mutate-in-place splice takes in this module (the pre-#325 pattern, and the one
`_apply_provisional_pairs` was still using), so its absence is a directly checkable
proxy for the invariant. Walks the AST rather than grepping so a call nested in a
comprehension or lambda is caught too.
"""

import ast
import pathlib

_ENGINE_PATH = pathlib.Path(__file__).parent.parent / "src" / "blindfold" / "engine.py"


def _find_sub_calls(source: str, filename: str = "<test>") -> list[str]:
    """One "lineno: <attr>" string per `.sub(`/`.subn(` method call found anywhere
    in `source`, at any nesting depth."""
    tree = ast.parse(source, filename=filename)
    violations = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("sub", "subn")
        ):
            violations.append(f"{node.lineno}: .{node.func.attr}(")
    return violations


def test_check_catches_a_subn_call():
    violations = _find_sub_calls("result = pattern.subn(target, result)\n")

    assert len(violations) == 1
    assert ".subn(" in violations[0]


def test_check_catches_a_sub_call_nested_in_a_comprehension():
    violations = _find_sub_calls(
        "results = [pattern.sub(target, text) for text in texts]\n"
    )

    assert len(violations) == 1
    assert ".sub(" in violations[0]


def test_check_ignores_unrelated_calls():
    violations = _find_sub_calls("result = _apply_spans(text, spans)\n")

    assert violations == []


def test_engine_has_no_regex_sub_or_subn_call():
    # `_apply_spans` (issue #325) is the one place a span's surrogate is spliced
    # into text; every rewrite path collects `ReplacementSpan`s against frozen
    # text and splices once through it. A `.sub()`/`.subn()` call anywhere else
    # in the module is exactly the mutate-in-place pattern that invariant rules
    # out.
    violations = _find_sub_calls(_ENGINE_PATH.read_text(), filename=str(_ENGINE_PATH))

    assert not violations, (
        "engine.py must splice replacement text only through _apply_spans (issue "
        "#325/#405), but found regex .sub()/.subn() call(s):\n"
        + "\n".join(violations)
    )
