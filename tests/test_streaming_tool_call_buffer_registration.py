"""Issue #409: the streaming restore path opened a tool-call hold-back buffer only
for a block literally typed ``tool_use`` (``app.py``'s own second, independently
literal check), while the buffered path's ``_restore_block`` dispatches on
``_TOOL_CALL_BLOCK_TYPES`` = ``{tool_use, server_tool_use, mcp_tool_use}``
(``engine.py:1110``). A streamed ``server_tool_use``/``mcp_tool_use`` block's
``input_json_delta`` therefore fell through to the raw pass-through branch
unrestored -- an availability defect (the terminal ``resolution_gate`` fail-closes
on the unrestored surrogate), not a leak.

This is the static half of the acceptance criterion "the block-type set the
streaming path registers is derived from the same constant the buffered path
dispatches on, not a second literal" -- a directly checkable proxy for the
invariant, mirroring ``test_engine_single_splice_point.py``'s own AST-based
drift guard for the sibling "one splice point" invariant. The behavioral half
(every member of that set actually restores end to end, streamed) is covered by
``tests/test_proxy_tool_call_round_trip.py``'s ``tool_use``/``server_tool_use``/
``mcp_tool_use`` streaming tests.
"""

import ast
import pathlib

_APP_PATH = pathlib.Path(__file__).parent.parent / "src" / "blindfold" / "app.py"


def _source_of(function_name: str) -> str:
    source = _APP_PATH.read_text()
    tree = ast.parse(source, filename=str(_APP_PATH))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"{function_name} not found in {_APP_PATH}")


def test_content_block_start_handler_registers_buffers_from_the_shared_accessor():
    source = _source_of("_process_sse_event")

    assert "tool_call_block_types()" in source


def test_content_block_start_handler_has_no_second_literal_tool_use_check():
    source = _source_of("_process_sse_event")

    # The exact regression this issue fixed: a bare `== "tool_use"` comparison is
    # a second, independently-maintained literal that can silently drift from the
    # buffered path's `_TOOL_CALL_BLOCK_TYPES` set (and did).
    assert '"tool_use"' not in source
