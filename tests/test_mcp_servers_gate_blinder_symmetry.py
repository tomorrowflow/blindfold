"""Issue #408 (ADR-0060 §2, governed by ADR-0051/ADR-0050): the blinder's
traversed top-level regions and the leak gate's checked view must agree about
``mcp_servers``.

The mechanism: `blindfold_payload`/`blindfold_chat_completions_payload` visit
exactly three top-level regions -- `system`, `messages`, `tools` -- and carry
everything else through by the initial `copy.deepcopy`, unread. `leak_gate`
used to scan the whole serialized body regardless, so a real value confined to
`mcp_servers` was checked but never substituted: a deterministic, permanent
block, the same shape as #386, with no escape via retry.

The decision taken (stated per this issue's own acceptance criterion, with the
rejected alternatives named):

- **Chosen: narrow the gate.** `mcp_servers` joins `tools[].name` (ADR-0051
  #303/#307) and `mcp_tool_use.server_name`/`tool_use.name`/`server_tool_use.name`
  (#323) as a field the blinder is structurally forbidden to rewrite --
  `url` must resolve to the live connector endpoint the provider actually
  connects to, and `name` is a dispatch key a paired `tools[].mcp_server_name`
  entry correlates against, the same protocol-identifier class already
  excluded elsewhere. Per ADR-0051's own rule ("the field decides the
  direction of the fix"), a field the blinder cannot reach is removed from the
  gate's checked set rather than the gate narrowed as a convenience -- bounded
  the same way `tools[].name`'s residual already is: the identical real value
  occurring anywhere else in the payload (message text, tool descriptions)
  stays fully blinded and gate-checked; only the connector declaration's own
  literal is exempted, recorded as a declared collision, not silently dropped.
- **Rejected: extend the blinder to traverse `mcp_servers`.** Considered
  splitting `name` (prose) from `url` (protocol identifier), but `name` is
  itself a dispatch key correlated with a paired `tools[].mcp_server_name`
  tools-array entry (ADR-0060's own worked example) -- rewriting it without
  also rewriting that paired entry desyncs the two, the same dispatch-breaking
  shape ADR-0051 already declined for `tools[].name`. No test in this slice
  exercises that paired-entry correlation; widening the blinder here would add
  a new cross-field consistency requirement this issue does not need to ship
  the fix.
- **Rejected: prove no real value can occur in `mcp_servers` and exempt it as
  a convenience.** The issue's own body rules this out: "a server URL carrying
  a company or product name is the ordinary case, not an exotic one." The
  narrowing here is justified structurally (the field decides the direction),
  not by that impossible-to-prove claim.

Leak-audit clauses: A (no real entity value reaches the stub upstream) holds
for every surface *except* the now-excluded `mcp_servers` field itself, which
is an accepted residual bounded exactly like `tools[].name`'s -- proven below,
not assumed. F (fail-closed) is unchanged: the same real value anywhere else
in the payload still blocks. B/C/D/E/G N/A -- no restore/mint/store change.
"""

from blindfold.engine import (
    _BLINDER_TRAVERSED_TOP_LEVEL_FIELDS,
    _gate_excluded_view,
    blindfold_payload,
    leak_gate,
)
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([("Weber", "Müller")])


def test_mcp_servers_is_not_a_blinder_traversed_top_level_field():
    assert "mcp_servers" not in _BLINDER_TRAVERSED_TOP_LEVEL_FIELDS


def test_blindfold_payload_never_touches_mcp_servers():
    mapping = _mapping()
    mcp_servers = [
        {"type": "url", "url": "https://mcp.Weber.example.com/sse", "name": "Weber"}
    ]
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "mcp_servers": mcp_servers,
    }

    blinded, _session = blindfold_payload(payload, mapping)

    # Byte-identical: the blinder carries this field through by its own initial
    # deepcopy and never visits it, exactly as _BLINDER_TRAVERSED_TOP_LEVEL_FIELDS
    # declares.
    assert blinded["mcp_servers"] == mcp_servers


def test_gate_excluded_view_drops_mcp_servers_entirely_when_not_blinder_traversed():
    # Derived from the same constant the blinder's own module-level docstring
    # points at -- not a second, independently maintained literal.
    assert "mcp_servers" not in _BLINDER_TRAVERSED_TOP_LEVEL_FIELDS

    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "mcp_servers": [
            {"type": "url", "url": "https://mcp.Weber.example.com/sse", "name": "Weber"}
        ],
    }

    view, forbidden_text = _gate_excluded_view(payload)

    assert "mcp_servers" not in view
    assert "Weber" in forbidden_text


def test_a_real_value_confined_to_mcp_servers_no_longer_deterministically_blocks_on_retry():
    # Acceptance criterion: the same payload retried no longer blocks
    # indefinitely. Simulates the retry by running the gate twice against the
    # same blinded payload -- pre-fix this raised LeakError every single time,
    # with no state that could ever change the verdict (#386's shape).
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "mcp_servers": [
            {"type": "url", "url": "https://mcp.Weber.example.com/sse", "name": "tools"}
        ],
    }
    blinded, _session = blindfold_payload(payload, mapping)

    leak_gate(blinded, mapping)
    leak_gate(blinded, mapping)
