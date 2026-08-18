"""Issue #338 (ADR-0052's acknowledged separate work): the named plausible-surrogate
pools are only 6-9 entries each, so the opaque ``BFX`` fallback (ADR-0052) fires on
roughly the 9th provisional mint of a kind -- in real agentic traffic (run 7: 43 mints
against a pool of 8) the fallback is the common case, not the edge. Enlarging the pools
keeps ADR-0005's model-reasoning benefit (plausible names) for far more of the inbox.

Pure data + one test (issue's own scope discipline) -- no walk, matching, or recognizer
change; #330-#333 own those.

Leak-audit: N/A this slice. No request-path, restore, gate, or fail-closed logic
touched -- pool *contents* only. The pools are already guarded in both directions by
existing mechanisms (``pool_entry_collides_with_corpus`` on issue,
``surrogate_space_match`` on re-entry, ``collides_with_known_entity`` at mint time); this
slice does not change how those checks run, only how many named entries they have to
work with before falling back to the opaque ``BFX`` form.
"""

from __future__ import annotations

import pathlib
import re

from blindfold.l3 import _SENTENCE_STOPWORDS
from blindfold.review import _PROVISIONAL_ORG_POOL, _PROVISIONAL_POOL
from blindfold.review import _LEGAL_FORM_SUFFIXES
from blindfold.store._mint import (
    _ORG_POOL,
    _PERSON_POOL,
    _REPLACEMENT_POOL,
    _TERM_POOL,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MIN_POOL_SIZE = 32

# All six pools this issue enlarges, keyed by name for readable failure output.
_ALL_POOLS: dict[str, tuple[str, ...]] = {
    "review._PROVISIONAL_POOL": _PROVISIONAL_POOL,
    "review._PROVISIONAL_ORG_POOL": _PROVISIONAL_ORG_POOL,
    "store._mint._PERSON_POOL": _PERSON_POOL,
    "store._mint._TERM_POOL": _TERM_POOL,
    "store._mint._ORG_POOL": _ORG_POOL,
    "store._mint._REPLACEMENT_POOL": _REPLACEMENT_POOL,
}

# Person-shaped pools use exactly-two-capitalized-alphabetic-token entries
# (ADR-0036's positional component alignment assumes this shape).
_PERSON_SHAPED_POOLS = (
    "review._PROVISIONAL_POOL",
    "store._mint._PERSON_POOL",
    "store._mint._REPLACEMENT_POOL",
)

# Issue #338's own acceptance criteria call out one documented, pre-existing
# baseline exception: "Lorenz" is a surname in store._mint._PERSON_POOL
# ("Heinz Lorenz", position 6) and a given name in
# store._mint._REPLACEMENT_POOL ("Lorenz Bruckner", position 7) -- both
# already merged, both positions durable (ADR-0037), predating this issue.
# Renaming either would change a value at an already-issued position, which
# the issue's own "existing entries keep their positions" constraint (and the
# durable-cursor invariant it protects) forbids. This is a scoped, documented
# residual -- not a license for any *new* collision -- so it is the one pair
# excluded below, not a general loophole.
_KNOWN_BASELINE_EXCEPTIONS = {
    frozenset({"store._mint._PERSON_POOL", "store._mint._REPLACEMENT_POOL"}): {"Lorenz"},
}


def _distinctive_words(entry: str) -> set[str]:
    return {
        word
        for word in re.findall(r"\w+", entry)
        if word not in _SENTENCE_STOPWORDS
    }


def _pool_words(pool: tuple[str, ...]) -> set[str]:
    words: set[str] = set()
    for entry in pool:
        words |= _distinctive_words(entry)
    return words


def test_every_enlarged_pool_has_at_least_32_entries():
    undersized = {
        name: len(pool) for name, pool in _ALL_POOLS.items() if len(pool) < _MIN_POOL_SIZE
    }
    assert undersized == {}, f"pools below the {_MIN_POOL_SIZE}-entry floor: {undersized}"


def test_pools_are_pairwise_distinctive_word_disjoint():
    names = list(_ALL_POOLS)
    words_by_pool = {name: _pool_words(_ALL_POOLS[name]) for name in names}

    violations = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            shared = words_by_pool[a] & words_by_pool[b]
            exception = _KNOWN_BASELINE_EXCEPTIONS.get(frozenset({a, b}), set())
            unexpected = shared - exception
            if unexpected:
                violations.append((a, b, unexpected))

    assert violations == [], f"unexpected cross-pool word collisions: {violations}"


def test_person_shaped_entries_are_exactly_two_capitalized_alphabetic_tokens():
    malformed = []
    for name in _PERSON_SHAPED_POOLS:
        for entry in _ALL_POOLS[name]:
            tokens = entry.split(" ")
            if len(tokens) != 2 or not all(
                tok.isalpha() and tok[:1].isupper() for tok in tokens
            ):
                malformed.append((name, entry))
    assert malformed == []


def test_org_shaped_entries_are_two_or_three_tokens_optionally_with_legal_form():
    single_word_legal_forms = {s for s in _LEGAL_FORM_SUFFIXES if " " not in s}
    malformed = []
    for name in ("review._PROVISIONAL_ORG_POOL", "store._mint._ORG_POOL"):
        for entry in _ALL_POOLS[name]:
            tokens = entry.split(" ")
            if len(tokens) not in (2, 3):
                malformed.append((name, entry))
                continue
            core = tokens[:-1] if tokens[-1] in single_word_legal_forms else tokens
            if len(core) not in (2, 3):
                malformed.append((name, entry))
                continue
            if not all(tok[:1].isupper() and tok.replace(".", "").isalpha() for tok in core):
                malformed.append((name, entry))
    assert malformed == []


def test_no_pool_entry_or_entry_word_occurs_in_context_md():
    context_text = (_REPO_ROOT / "CONTEXT.md").read_text()
    context_words = set(re.findall(r"\w+", context_text))

    offenders = []
    for name, pool in _ALL_POOLS.items():
        for entry in pool:
            if entry in context_text:
                offenders.append((name, entry, "whole entry"))
                continue
            for word in _distinctive_words(entry):
                if word in context_words:
                    offenders.append((name, entry, word))

    assert offenders == []


def test_existing_entries_are_unchanged_and_kept_at_their_original_position():
    # Durability (ADR-0037): the cursor is monotonic and never rewound, so an
    # already-issued position's value must never change underneath a live
    # workspace. Pin the pre-issue-338 prefix of every pool verbatim.
    original_prefixes = {
        "review._PROVISIONAL_POOL": (
            "Alex Brenner",
            "Berta Falke",
            "Carla Distel",
            "Doris Engler",
            "Emil Fink",
            "Fritz Graf",
            "Greta Henning",
            "Hugo Imhoff",
        ),
        "review._PROVISIONAL_ORG_POOL": (
            "Nordkap Systeme GmbH",
            "Rheinblick Consulting",
            "Waldstein Industries",
            "Kupfertal Solutions",
            "Birkenhain Logistik",
            "Moosburg Analytics",
            "Feldmark Ventures",
            "Silberklang Media",
        ),
        "store._mint._PERSON_POOL": (
            "Bernhard Vogt",
            "Claudia Reinhardt",
            "Dieter Sommer",
            "Elena Fuchs",
            "Stefan Kaiser",
            "Gabriele Wirth",
            "Heinz Lorenz",
            "Iris Hartmann",
        ),
        "store._mint._TERM_POOL": (
            "Projekt Polarstern",
            "Vorgang Silberpfeil",
            "Initiative Tannwald",
            "Vorhaben Eichberg",
            "Programm Nordlicht",
            "Projekt Steinadler",
            "Verfahren Lindenhof",
            "Vorhaben Rabenstein",
            "Initiative Falkenberg",
        ),
        "store._mint._ORG_POOL": (
            "Brunnen Technik AG",
            "Abteilung Entwicklung Nord",
            "Team Atlas",
            "Gruppe Meridian",
            "Sparte Hofgarten",
            "Bereich Talblick",
        ),
        "store._mint._REPLACEMENT_POOL": (
            "Ruth Vollmer",
            "Wolfgang Ehrlich",
            "Sabine Krug",
            "Norbert Beckmann",
            "Ottilie Rathke",
            "Kurt Steinmetz",
            "Waltraud Nickel",
            "Lorenz Bruckner",
        ),
    }
    for name, prefix in original_prefixes.items():
        pool = _ALL_POOLS[name]
        assert pool[: len(prefix)] == prefix, name
