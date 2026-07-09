"""L2 performance seam (issue #83): the token-window walk must not scale cubically.

ADR-0003 promises detection latency bounded by candidate-span count, not payload
size -- coding agents send large system prompts (tens of thousands of tokens) and
must not peg a core. `_walk_token_windows` previously yielded a window for every
`(i, j)` token pair with no cap on window length, making `detect_l2` O(n^3)-ish in
token count and computationally unreachable on a Claude Code-sized payload.
"""

import time

from blindfold.detection import Entity, detect_l2

_FILLER_WORDS = (
    "def", "return", "import", "class", "async", "await", "config", "session",
    "request", "response", "handler", "module", "assert", "candidate", "surface",
    "entity", "window", "token", "payload", "detector",
)


def _synthetic_coding_agent_payload(token_count: int) -> str:
    return " ".join(_FILLER_WORDS[i % len(_FILLER_WORDS)] for i in range(token_count))


def _realistic_entity_set() -> list[Entity]:
    # Mirrors the shapes already exercised in test_l2_detection.py: multi-token
    # variations, umlaut folding, and a shared first name -- the kinds of surfaces a
    # real entity graph carries.
    return [
        Entity(
            canonical="Enervia",
            variations=("enervia", "Enervia AG", "Enervia GmbH", "Enerva"),
            surrogate="Projekt Polarstern",
        ),
        Entity(
            canonical="Stefan Wegner",
            variations=("Stefan", "Stef", "Wegner"),
            surrogate="Bernhard Vogt",
        ),
        Entity(canonical="Anna Schmidt", variations=("Anna",), surrogate="Berta Vogel"),
        Entity(canonical="Muller", variations=("Müller",), surrogate="Carola Wolff"),
    ]


def test_detect_l2_completes_within_seconds_over_a_20k_token_coding_agent_payload():
    text = _synthetic_coding_agent_payload(20_000)

    start = time.monotonic()
    detect_l2(text, _realistic_entity_set())
    elapsed = time.monotonic() - start

    assert elapsed < 10.0
