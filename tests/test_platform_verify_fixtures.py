"""``BLINDFOLD_STORE_KEY`` fixtures embedded in ``platform-verify.yml`` (issue #234).

The hosted platform-verify gate's ONE-HOP (Store key) assertion sets
``BLINDFOLD_STORE_KEY`` to a literal at PowerShell scope, with no tray involved,
specifically to isolate whether the Store key reaches ``config.mapping_cipher ==
"local"`` independent of the tray's TWO-HOP spawn. That isolation only works if
the literal itself is a well-formed Store key: ``serve.py``'s
``refuse_if_malformed_store_key`` runs at startup, before the assertion's poll
loop ever gets a chance to observe ``mapping_cipher`` -- a malformed literal
makes ``blindfold-proxy.exe`` refuse to start (fail-closed) and exit non-zero,
failing the assertion for an unrelated reason (a bad test fixture) instead of
actually testing the Store-key propagation path. Not itself PowerShell-
executable here, but the literal's shape is plain text this sandbox can check
directly against the same shape ``LocalKeyCipher`` construction enforces
(ADR-0045 §3, the exact check ``refuse_if_malformed_store_key`` performs),
catching this class of bug before it burns a hosted Windows run.

Leak-audit clause analysis: N/A -- this is a CI-fixture-shape check, not
request-path code; the literal itself is a throwaway smoke-test value, asserted
here only for its base64/length shape, never logged or compared against a real
secret.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from blindfold.mapping_cipher import InvalidStoreKeyError, LocalKeyCipher

WORKFLOW_PATH = (
    pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "platform-verify.yml"
)

_STORE_KEY_ASSIGNMENT_RE = re.compile(
    r'\$env:BLINDFOLD_STORE_KEY\s*=\s*"([^"]*)"'
)


def test_platform_verify_store_key_literals_decode_to_a_valid_store_key() -> None:
    text = WORKFLOW_PATH.read_text()
    literals = _STORE_KEY_ASSIGNMENT_RE.findall(text)
    assert literals, "expected at least one $env:BLINDFOLD_STORE_KEY literal in platform-verify.yml"

    for literal in literals:
        try:
            LocalKeyCipher(literal)
        except InvalidStoreKeyError as exc:
            pytest.fail(
                f"platform-verify.yml sets BLINDFOLD_STORE_KEY={literal!r}, which "
                f"refuse_if_malformed_store_key would reject at startup ({exc}) -- "
                "the hosted assertion would fail on a malformed fixture instead of "
                "testing Store-key propagation"
            )
