"""L2 4-pass detection seam (ADR-0003): the curated entity-graph dictionary.

L2 runs four passes against the entity graph and flags **candidate spans**: exact →
normalized (unidecode) → fuzzy (Levenshtein ≤2) → first-name ambiguity. German-aware,
with stopwords + dedup. Variations of one entity resolve to one surrogate (coreference,
ADR-0004). L3 is stubbed/absent for this seam — the detector returns spans directly, no
candidate adjudication.
"""

from blindfold.detection import Entity, detect_l2


def _enervia() -> Entity:
    # Canonical seeded Term with variations (mirrors vendored_seed.json shape).
    return Entity(
        canonical="Enervia",
        variations=("enervia", "ENERVIA", "Enervia AG", "Enervia GmbH", "Enerva"),
        surrogate="Projekt Polarstern",
    )


def test_exact_pass_flags_canonical_name_with_its_surrogate():
    enervia = _enervia()
    text = "Please brief Enervia tomorrow."

    spans = detect_l2(text, [enervia])

    assert len(spans) == 1
    span = spans[0]
    assert text[span.start : span.end] == "Enervia"
    assert span.real == "Enervia"
    assert span.surrogate == "Projekt Polarstern"
    assert span.pass_name == "exact"


def test_exact_pass_respects_token_boundaries_and_does_not_flag_substrings():
    # "Enervia" must not be flagged inside "Enerviances" — that is over-redaction
    # (quality bug, ADR-0003 / CONTEXT.md invariant) caused by naive substring match.
    # 4-edit distance from any surface, so the fuzzy pass leaves it alone too.
    enervia = _enervia()
    text = "An Enerviances catalog is unrelated."

    spans = detect_l2(text, [enervia])

    assert spans == []


def test_normalized_pass_matches_german_umlaut_against_ascii_canonical():
    # ADR-0003: normalized via unidecode. "Müller" folds to "Muller"; a seeded
    # canonical "Muller" must match the umlaut form (and vice versa for German users
    # typing ASCII fallbacks).
    mueller = Entity(canonical="Muller", variations=(), surrogate="Bernhard Vogt")
    text = "Bitte Herrn Müller informieren."

    spans = detect_l2(text, [mueller])

    assert len(spans) == 1
    span = spans[0]
    assert text[span.start : span.end] == "Müller"
    assert span.real == "Muller"
    assert span.surrogate == "Bernhard Vogt"
    assert span.pass_name == "normalized"


def test_fuzzy_pass_matches_near_miss_within_levenshtein_two():
    # ADR-0003: fuzzy Levenshtein ≤2. "Wegnerr" is one insertion from "Wegner"
    # (a variation of "Stefan Wegner"); "Wegnre" is one transposition (≤2 edits).
    stefan = Entity(
        canonical="Stefan Wegner",
        variations=("Stefan", "Stef", "Wegner"),
        surrogate="Bernhard Vogt",
    )
    text = "Did Wegnerr or Wegnre send the patch?"

    spans = detect_l2(text, [stefan])

    flagged = {text[s.start : s.end] for s in spans}
    assert flagged == {"Wegnerr", "Wegnre"}
    assert {s.surrogate for s in spans} == {"Bernhard Vogt"}
    assert {s.pass_name for s in spans} == {"fuzzy"}


def test_fuzzy_pass_matches_a_near_miss_that_spans_two_tokens():
    # A typo can insert whitespace into a single-token surface ("Wegner" mistyped
    # as "We gner") without changing the Levenshtein distance from the surface by
    # more than the space itself: "we gner" -> "wegner" is one deletion. The fuzzy
    # pass must still catch this -- skipping every multi-token window would let a
    # known entity's misspelled real value cross the L2 boundary unblindfolded
    # (issue #83 regression: the window-length cap must not also narrow *which*
    # windows are fuzzy-eligible).
    stefan = Entity(
        canonical="Stefan Wegner",
        variations=("Stefan", "Stef", "Wegner"),
        surrogate="Bernhard Vogt",
    )
    text = "Did We gner send the patch?"

    spans = detect_l2(text, [stefan])

    flagged = {text[s.start : s.end] for s in spans}
    assert flagged == {"We gner"}
    assert {s.surrogate for s in spans} == {"Bernhard Vogt"}
    assert {s.pass_name for s in spans} == {"fuzzy"}


def test_fuzzy_pass_matches_a_near_miss_that_spans_two_tokens_against_a_single_token_surface():
    # Same inserted-whitespace typo as above, but the *only* known surface is
    # single-token ("Wegner", no multi-token canonical/variation in play at all).
    # The window-length cap (bounded by the longest known surface, issue #83) must
    # still be wide enough for the fuzzy pass to reach a 2-token window -- capping
    # it at the longest *exact-match* surface's token count (here: 1) would starve
    # the fuzzy pass entirely and let the real value leak unblindfolded.
    wegner = Entity(canonical="Wegner", variations=(), surrogate="Bernhard Vogt")
    text = "Did We gner send the patch?"

    spans = detect_l2(text, [wegner])

    flagged = {text[s.start : s.end] for s in spans}
    assert flagged == {"We gner"}
    assert {s.surrogate for s in spans} == {"Bernhard Vogt"}
    assert {s.pass_name for s in spans} == {"fuzzy"}


def test_fuzzy_pass_ignores_distant_words_outside_levenshtein_two():
    # "Wagner" is 3 edits from any surface ("Wegner"/"Stefan"/"Stef"/"Stefan Wegner"
    # at the closest, 1 substitution; "Wagner" vs "Wegner" is actually 1, so use
    # something genuinely distant) — confirm we don't fire on unrelated words.
    stefan = Entity(
        canonical="Stefan Wegner",
        variations=("Stefan", "Stef", "Wegner"),
        surrogate="Bernhard Vogt",
    )
    text = "Eisenbahn requires careful planning."

    assert detect_l2(text, [stefan]) == []


def test_fuzzy_pass_ignores_lowercase_common_token_within_two_edits_of_a_capitalized_variation():
    # issue #85: "darwin" (env block token, Platform: darwin) is Levenshtein 2 from
    # the seeded Variation "Martin" (of "Martin Bach") and was fuzzy-flagged, so every
    # Claude Code request corrupted its own environment block on egress -- over-
    # masking, not a leak, but it broke context on every exchange. The fuzzy pass now
    # requires the candidate token's first character to case-match the surface's
    # first character: a lowercase mid-sentence token can never fuzzy-match a
    # capitalized name Variation, closing off this whole false-positive class.
    martin = Entity(
        canonical="Martin Bach",
        variations=("Martin", "Bach"),
        surrogate="Bernhard Vogt",
    )
    text = "Platform: darwin"

    assert detect_l2(text, [martin]) == []


def test_fuzzy_pass_ignores_martini_class_collisions_with_a_capitalized_variation():
    # issue #85: "martini"/"marlin" share their first letter with "Martin" case-
    # insensitively but not case-sensitively (lowercase 'm' vs capitalized 'M') --
    # the fuzzy pass must reject the whole class, not just first-letter mismatches
    # like "darwin".
    martin = Entity(
        canonical="Martin Bach",
        variations=("Martin", "Bach"),
        surrogate="Bernhard Vogt",
    )
    text = "charwin martini marlin"

    assert detect_l2(text, [martin]) == []


def test_german_stopwords_are_not_flagged_even_under_fuzzy_pass():
    # German-aware stopwords (CONTEXT.md / ADR-0003 "stopwords + dedup"). Without the
    # stoplist, the fuzzy pass would flag the German function word "wegen" ("because of"
    # — Levenshtein 2 from "Wegner") as a near-miss of the seeded variation. The
    # stoplist must shield common German function words from accidental flagging.
    stefan = Entity(
        canonical="Stefan Wegner",
        variations=("Wegner",),
        surrogate="Bernhard Vogt",
    )
    text = "Die Aufgabe wurde wegen des Sturms verschoben."

    spans = detect_l2(text, [stefan])

    assert spans == []


def test_first_name_ambiguity_flags_shared_first_name_as_candidate():
    # ADR-0003 pass 4: ambiguous first names go to L3 for disambiguation, but at the
    # L2 seam (with L3 stubbed/absent) the detector still flags the span — protection
    # over disambiguation — and tags it as ``first_name`` so the candidate-span seam
    # downstream can route it.
    anna_at_one = Entity(
        canonical="Anna Schmidt",
        variations=("Anna",),
        surrogate="Berta Vogel",
    )
    anna_at_two = Entity(
        canonical="Anna Becker",
        variations=("Anna",),
        surrogate="Carola Wolff",
    )
    text = "Anna sent the brief."

    spans = detect_l2(text, [anna_at_one, anna_at_two])

    assert len(spans) == 1
    span = spans[0]
    assert text[span.start : span.end] == "Anna"
    assert span.pass_name == "first_name"
    # Until L3 disambiguates we still inject A surrogate — under-redaction would leak.
    # The detector picks one deterministically (first declared) so the choice is stable.
    assert span.real == "Anna Schmidt"
    assert span.surrogate == "Berta Vogel"


def test_variations_of_one_entity_resolve_to_one_surrogate_coreference():
    # ADR-0004 coreference: canonical + every variation share the same surrogate.
    # Multi-token variations like "Enervia AG" are matched as one span.
    enervia = _enervia()
    text = "We met Enervia, then Enervia AG, then enervia again."

    spans = detect_l2(text, [enervia])

    surface_forms = sorted(span.text for span in spans)
    assert surface_forms == ["Enervia", "Enervia AG", "enervia"]
    # Every match resolves to the same surrogate (coreference).
    assert {span.surrogate for span in spans} == {"Projekt Polarstern"}
    assert {span.real for span in spans} == {"Enervia"}
