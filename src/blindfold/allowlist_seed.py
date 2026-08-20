"""Loader for the vendored seeded-allowlist artifact (ADR-0023, issue #71).

A curated data file shipped in the package, one token per line, loaded into the
process-global :class:`~blindfold.review.Allowlist` at startup with semantics
identical to a **learned** reject (ADR-0010): suppresses novelty discovery only,
never protection (see ``select_candidate_spans`` -- a known entity/Term always
wins before the allowlist is even consulted).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_SEED_PATH = Path(__file__).with_name("seeded_allowlist.txt")
_CONTEXT_MD_PATH = Path(__file__).parent.parent.parent / "CONTEXT.md"
_GLOSSARY_SECTION_RE = re.compile(r"^## Glossary\n(.*?)\n## ", re.DOTALL | re.MULTILINE)
_GLOSSARY_HEADING_RE = re.compile(r"^- \*\*([^*]+)\*\*", re.MULTILINE)


@lru_cache(maxsize=1)
def load_seeded_allowlist_tokens() -> frozenset[str]:
    """Return the curated seed tokens (cached; the artifact is immutable at runtime)."""
    tokens: set[str] = set()
    for line in _SEED_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.add(line)
    return frozenset(tokens)


def extract_glossary_terms() -> list[str]:
    """Mechanically enumerate CONTEXT.md's Glossary section headings (issue #353).

    Returns the term text of every top-level ``- **Term** — ...`` bullet directly
    under ``## Glossary``, in document order. Indented sub-bullets (e.g. Detection
    layers' L1/L2/L3) start with leading whitespace, not ``-``, so they never
    match; the ``(?s).*?`` body stops at the next ``## `` heading, so bullets in
    ``## Key invariants`` / ``## Controlled vocabulary`` (same ``- **...**``
    shape, different section) are never picked up either.
    """
    text = _CONTEXT_MD_PATH.read_text(encoding="utf-8")
    section_match = _GLOSSARY_SECTION_RE.search(text)
    return _GLOSSARY_HEADING_RE.findall(section_match.group(1))


# Glossary headings that mechanical enumeration finds but which deliberately do
# NOT get a seeded-allowlist entry, with the reason recorded (issue #353 asks
# that curation judgement be written down, not implicit).
GLOSSARY_EXCLUSIONS: dict[str, str] = {
    "Re-identify": (
        "Hyphenated with no internal whitespace, so it cannot become a working "
        "allowlist entry: Allowlist.phrases() only matches entries with internal "
        "whitespace (issue #294), and single-token matching requires an exact "
        "match against a `\\w+` candidate token, which never spans a hyphen. The "
        "only real candidate token this heading could ever produce is the bare "
        "two-letter 'Re' before the hyphen -- too small and generic to seed on "
        "its own outside this compound."
    ),
    "Fail-closed": (
        "Same mechanical reason as 'Re-identify': hyphenated with no internal "
        "whitespace, so neither a single-token nor a phrase allowlist entry can "
        "actually match it. The bare pre-hyphen fragment 'Fail' is a common "
        "English word, too generic to seed standalone."
    ),
    "Egress": (
        "Plausible organisation-name collision: 'Egress' (Egress Software "
        "Technologies) is a real, identifiable public vendor in the same "
        "privacy/security space Blindfold operates in, so a workspace could "
        "plausibly need it protected as a genuine, not-yet-registered Term for a "
        "counterparty -- unlike the self-referential AI-vendor batch "
        "(Claude/Anthropic) or this project's own documented dependency "
        "(OpenBao's Transit engine), 'Egress' names someone else's business, not "
        "Blindfold's own vocabulary."
    ),
}
