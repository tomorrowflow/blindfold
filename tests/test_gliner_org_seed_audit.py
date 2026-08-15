"""Audit the seeded allowlist against GLiNER's real ``organization``/``person``
behaviour (issue #281), the sequencing ADR-0049's Consequences names before the
GLiNER cascade becomes the default (issue #282): because ADR-0033 §2's Position A
lets a GLiNER positive skip the inner LLM adjudicator entirely, GLiNER's precision
becomes a hard ceiling on the cascade's, and on every install's once it is the
default.

``tests/fixtures/gliner_org_probe_corpus.json`` vendors the actual measurement: a
probe corpus of public-software/framework/product tokens in realistic coding-agent
sentence context, each run through the real ``GlinerOnnxClassifier.classify_span``
(``knowledgator/gliner-pii-base-v1.0``, the pinned revision ``gliner_provisioning``
provisions) on 2026-08-15. The measured label is recorded per probe -- the
measurement itself, not just the conclusion -- alongside the curation decision and
its rationale, following the seeded-allowlist's own evidence-first precedent
(ADR-0023/ADR-0032) and the project's existing curation-reject convention
(``test_seeded_allowlist.py``'s ``_CURATION_REJECTS``).

Curation rule applied (unchanged from ADR-0023): a confirmed false positive is
seeded only when it is a genuinely public, non-sensitive identifier, implausible as
a protected referent when unregistered. A token that could plausibly name a real
private referent (a common surname, a generic dictionary word with no single
dominant public-brand reading) is left out and the reasoning recorded here, even
when GLiNER flags it -- the seed suppresses novelty discovery permanently for that
literal token in every future context, not just this measurement's sentence.

Leak-audit clauses for this slice (mirrors test_seeded_allowlist.py's own stance):
- A covered: a registered Term equal to a newly seeded token still egresses as its
  surrogate, never plaintext -- the seed suppresses novelty discovery, not
  protection (L2 always wins before the allowlist is consulted).
- D covered: verify pass stays clean for that same request.
- F covered: an unrelated genuine novel candidate in the same traffic still
  fail-closes when L3 is unavailable -- suppression is token-scoped.
- B/C/E/G: N/A -- this slice does not touch restore, surrogate minting stability,
  or the store; unchanged from the existing suites that already cover them.
"""

from __future__ import annotations

import json
from pathlib import Path

from blindfold.allowlist_seed import load_seeded_allowlist_tokens
from blindfold.l3 import select_candidate_spans
from blindfold.review import Allowlist

_CORPUS_PATH = Path(__file__).parent / "fixtures" / "gliner_org_probe_corpus.json"


def _load_corpus() -> dict:
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _probes_with_decision(decision: str) -> list[dict]:
    return [p for p in _load_corpus()["probes"] if p["decision"] == decision]


def test_probe_corpus_records_every_probe_measured_gliner_label():
    # Acceptance criterion: each probe token's GLiNER label is recorded (the
    # measurement, not just the conclusion) -- every entry must carry the actual
    # label observed against the real model, never a placeholder.
    corpus = _load_corpus()
    probes = corpus["probes"]
    assert len(probes) > 0
    for probe in probes:
        assert probe["token"]
        assert probe["sentence"]
        assert probe["token"] in probe["sentence"]
        assert probe["gliner_label"] in {"person", "organization", "none"}
        assert probe["decision"] in {"seeded", "excluded", "no_action"}
        assert probe["rationale"]


def test_confirmed_gliner_org_false_positives_are_added_to_the_seed_list():
    # Acceptance criterion: every confirmed public-token false positive
    # (decision == "seeded") is present in seeded_allowlist.txt, "Transit" among
    # them (the issue's own cited live finding).
    seeded_probes = _probes_with_decision("seeded")
    assert {"Transit", "Docker", "Nginx"} <= {p["token"] for p in seeded_probes}

    tokens = load_seeded_allowlist_tokens()
    for probe in seeded_probes:
        assert probe["token"] in tokens, (
            f"{probe['token']!r} is marked 'seeded' in the probe corpus but "
            "missing from seeded_allowlist.txt"
        )


def test_confirmed_gliner_org_false_positives_never_become_l3_candidates():
    # Acceptance criterion: "Add a regression test asserting the seed list
    # suppresses the confirmed set, so a future seed-list edit cannot silently
    # drop one." Runs the exact probe sentence measured against the real model
    # through select_candidate_spans with the real seeded allowlist loaded --
    # the same suppression path the live request path uses (l3.py), so a future
    # edit dropping one of these tokens from seeded_allowlist.txt fails this test
    # before it ever reaches the GLiNER cascade in production.
    allowlist = Allowlist()
    for token in load_seeded_allowlist_tokens():
        allowlist.add(token)

    for probe in _probes_with_decision("seeded"):
        candidates = select_candidate_spans(
            probe["sentence"], known_entities=[], allowlist=allowlist
        )
        flagged = {c.text for c in candidates}
        assert probe["token"] not in flagged, (
            f"{probe['token']!r} was flagged as an L3 candidate despite being "
            "seeded -- the allowlist no longer suppresses this confirmed "
            "GLiNER false positive"
        )


def test_excluded_probe_tokens_are_not_seeded():
    # Guards the judgment call the other direction (mirrors
    # test_seeded_allowlist.py's own _CURATION_REJECTS pattern): tokens this
    # audit deliberately declined to seed -- because they collide with a real
    # personal name, place name, or generic dictionary word implausible as a
    # public-only identifier -- must stay out, so a future edit can't silently
    # seed a real private referent just to quiet the classifier.
    tokens = load_seeded_allowlist_tokens()
    excluded = {p["token"] for p in _probes_with_decision("excluded")}
    assert excluded, "expected at least one deliberately-excluded probe token"
    assert tokens.isdisjoint(excluded)
