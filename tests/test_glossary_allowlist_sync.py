"""Keep the seeded allowlist and CONTEXT.md's Glossary in sync (issue #353).

Before this issue, ``src/blindfold/seeded_allowlist.txt`` contained no Blindfold
domain term at all -- ``Surrogate``, ``Term``, ``Store`` (as ``Store directory`` /
``Store key``), and every other glossary heading were absent, despite being
exactly the vocabulary a Blindfold-development session reads Title-Case in its
own tool results. ``Surrogate`` is a measured false positive in #74 runs 10 and
11.

``extract_glossary_terms`` mechanically enumerates CONTEXT.md's Glossary section
(the 63 top-level ``- **Term** -- ...`` headings, ADR-0023 curation input for this
slice; ADR-0059 §4 added "Payload inspection" and "Retained payload" to the
Glossary, moving the count from 57 to 59, ADR-0060 added "Executed argument"
and "World-acting tool", moving the count from 59 to 61, and ADR-0051's #406
amendment added "Blinder-written range", moving it from 61 to 62, and ADR-0057's
#390 amendment added "Block retryability", moving it from 62 to 63). Every one of those 63
terms must appear either in the seeded allowlist (``load_seeded_allowlist_tokens``)
or in ``GLOSSARY_EXCLUSIONS`` with a recorded reason -- so a new glossary term
added later can't silently drift out of sync with the allowlist the way the whole
domain-term category did until now.
"""

from __future__ import annotations

from blindfold.allowlist_seed import (
    GLOSSARY_EXCLUSIONS,
    extract_glossary_terms,
    load_seeded_allowlist_tokens,
)


def test_extract_glossary_terms_finds_exactly_the_63_glossary_headings():
    terms = extract_glossary_terms()

    assert len(terms) == 63
    assert "Surrogate" in terms
    assert "Blindfold" in terms
    assert "Executed argument" in terms
    assert "World-acting tool" in terms
    assert "Blinder-written range" in terms
    assert "Block retryability" in terms
    # Nested Detection-layers sub-bullets (L1/L2/L3) are indented, not top-level
    # Glossary headings, so they must not be picked up.
    assert "L1" not in terms
    assert "L2" not in terms
    assert "L3" not in terms
    # "## Key invariants" and "## Controlled vocabulary" bullets use the same
    # "- **...**" shape but are a different section -- must not be picked up.
    assert "Relation" not in terms
    assert "A surrogate issued without a corpus-disjointness check is opaque by construction." not in terms


def test_every_glossary_term_is_seeded_or_explicitly_excluded_with_a_reason():
    terms = extract_glossary_terms()
    seeded = load_seeded_allowlist_tokens()

    missing = [
        term
        for term in terms
        if term not in seeded and term not in GLOSSARY_EXCLUSIONS
    ]
    assert missing == [], (
        f"glossary terms neither seeded nor excluded with a reason: {missing}"
    )


def test_glossary_exclusions_do_not_overlap_the_seeded_allowlist():
    seeded = load_seeded_allowlist_tokens()

    assert seeded.isdisjoint(GLOSSARY_EXCLUSIONS), (
        "a term can't be both seeded and deliberately excluded"
    )


def test_glossary_exclusions_each_carry_a_non_empty_reason():
    for term, reason in GLOSSARY_EXCLUSIONS.items():
        assert reason.strip(), f"{term!r} is excluded with an empty reason"
