"""GLiNER cascade adjudicator (ADR-0033 Mode A / "Position A", issue #138).

``GlinerCascadeAdjudicator`` is a new ``L3Adjudicator`` implementation that chains a
local GLiNER NER classifier before the existing LLM adjudicator. It lives entirely
behind the ``L3Adjudicator`` seam (l3.py) -- ``L3Detector.detect()``,
``select_candidate_spans``, and ``L3ContentCache`` are all unaffected.

Seam stubs: a recording GLiNER classifier and a recording inner adjudicator stand in
for the real ONNX model and the real LLM adjudicator, mirroring how
test_l3_detection.py's ``_RecordingAdjudicator`` stands in for Ollama.

Leak-audit clause analysis: N/A this slice -- this file exercises the
GlinerCascadeAdjudicator/GlinerClassifier seam in isolation, not the request path
(mirrors test_openai_compat_adjudicator.py's own N/A stance). GLiNER classification
never leaves the process -- there is no network client anywhere in this seam, so
clause A (no real entity egresses) is satisfied structurally rather than by a runtime
assertion: the only egress-capable collaborator remains the inner L3Adjudicator,
already covered by the existing L3Adjudicator-seam tests regardless of which concrete
adjudicator is plugged in.
"""

from __future__ import annotations

import sys
import types
import unicodedata
from dataclasses import dataclass

import pytest

from blindfold import l3_gliner
from blindfold.engine import blindfold_payload, resolution_gate, restore_response
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.l3_gliner import GlinerCascadeAdjudicator, GlinerOnnxClassifier
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


@dataclass
class _Call:
    text: str
    context: str


class _RecordingClassifier:
    """Stub for GLiNER -- records every classify() call, returns a scripted verdict."""

    def __init__(self, positives: frozenset[str] = frozenset()) -> None:
        self.calls: list[_Call] = []
        self._positives = positives

    def classify(self, candidate: CandidateSpan) -> bool:
        self.calls.append(_Call(text=candidate.text, context=candidate.context))
        return candidate.text in self._positives


class _LabelAwareStubGliNERClassifier:
    """Stub GLiNER classifier that also implements ``classify_type`` (issue #167) --
    exercises the cascade's richer type-carrying path, distinct from
    ``_RecordingClassifier`` above, which only implements the bool-only ``classify``
    seam (ADR-0033's original bool-only cascade contract, still supported for a
    classifier that never detects a type).
    """

    def __init__(self, tagged_hits: dict[str, str]) -> None:
        self._tagged_hits = tagged_hits

    def classify(self, candidate: CandidateSpan) -> bool:
        return candidate.text in self._tagged_hits

    def classify_type(self, candidate: CandidateSpan) -> str | None:
        return self._tagged_hits.get(candidate.text)


class _SpanAwareStubGlinerClassifier:
    """Stub GLiNER classifier that also implements ``classify_span`` (issue
    #170) -- the richer seam that also surfaces the absolute span extent, on
    top of the label ``_LabelAwareStubGliNERClassifier`` already carries.
    """

    def __init__(self, spans: dict[str, tuple[str, int, int]]) -> None:
        # candidate text -> (entity_type, span_start, span_end)
        self._spans = spans

    def classify(self, candidate: CandidateSpan) -> bool:
        return candidate.text in self._spans

    def classify_type(self, candidate: CandidateSpan) -> str | None:
        result = self._spans.get(candidate.text)
        return result[0] if result is not None else None

    def classify_span(self, candidate: CandidateSpan) -> tuple[str, int, int] | None:
        return self._spans.get(candidate.text)


class _RecordingAdjudicator:
    """Stub for the inner L3Adjudicator (Ollama/oMLX) -- records every call."""

    def __init__(self, decisions: dict[str, L3Adjudication] | None = None) -> None:
        self.calls: list[_Call] = []
        self._decisions = decisions or {}

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(_Call(text=candidate.text, context=candidate.context))
        return self._decisions.get(candidate.text, L3Adjudication(is_entity=False))


class _StubGlinerModel:
    """Stand-in for a loaded GLiNER model -- records predict_entities() calls."""

    def __init__(self, hits: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._hits = hits

    def predict_entities(self, text: str, labels: list[str]) -> list[dict]:
        self.calls.append((text, labels))
        entities = []
        for token in self._hits:
            start = text.find(token)
            if start != -1:
                entities.append(
                    {"text": token, "label": labels[0], "start": start, "end": start + len(token)}
                )
        return entities


def test_gliner_onnx_classifier_takes_a_model_path_and_loads_it_only_on_first_classify(
    monkeypatch,
):
    # Acceptance criterion: "GLiNER model path is a constructor parameter; model
    # loading is local-only (ONNX, CPU, no network call)." Loading is lazy (deferred
    # to first classify()) so constructing the classifier never touches disk/model
    # state, and the loader is the only seam capable of doing so -- there is no
    # httpx/network client anywhere in this class.
    stub_model = _StubGlinerModel(hits=frozenset({"Klaus"}))
    load_calls: list[str] = []

    def fake_loader(model_path: str):
        load_calls.append(model_path)
        return stub_model

    monkeypatch.setattr(l3_gliner, "_load_gliner_model", fake_loader)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-edge-v1.0")
    assert load_calls == []  # constructing the classifier loads nothing yet

    candidate = CandidateSpan(
        text="Klaus",
        start=11,
        end=16,
        context="We mention Klaus in passing.",
        context_offset=11,
    )
    result = classifier.classify(candidate)

    assert result is True
    assert load_calls == ["gliner-pii-edge-v1.0"]
    assert stub_model.calls == [(candidate.context, list(l3_gliner._GLINER_LABELS))]

    classifier.classify(candidate)
    assert load_calls == ["gliner-pii-edge-v1.0"]  # loaded once, reused after


def test_gliner_onnx_classifier_returns_false_when_the_span_is_not_among_the_hits(
    monkeypatch,
):
    stub_model = _StubGlinerModel(hits=frozenset())
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-edge-v1.0")
    candidate = CandidateSpan(
        text="Please", start=0, end=6, context="Please brief the team."
    )

    assert classifier.classify(candidate) is False


def test_gliner_onnx_classifier_confirms_a_single_token_candidate_within_a_multi_word_span(
    monkeypatch,
):
    # Issue #160: select_candidate_spans (l3.py) emits single capitalized tokens as
    # candidates, but GLiNER returns multi-word spans ("John Smith", not "John").
    # An exact string match between the candidate token and the GLiNER span text
    # never fires for a multi-word entity -- classify() must confirm when the
    # GLiNER span *covers* the candidate's character offsets, not only when the
    # strings are equal.
    context = "John Smith called from Acme Corporation about Project Falcon."
    stub_model = _StubGlinerModel(hits=frozenset({"John Smith"}))
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="John", start=0, end=4, context=context, context_offset=0
    )

    assert classifier.classify(candidate) is True


def test_gliner_onnx_classifier_confirms_the_second_token_of_a_multi_word_span(
    monkeypatch,
):
    # Acceptance criterion: both tokens of a multi-word span confirm, not just
    # the one that happens to start at the span's own start offset.
    context = "John Smith called from Acme Corporation about Project Falcon."
    stub_model = _StubGlinerModel(hits=frozenset({"John Smith"}))
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="Smith", start=5, end=10, context=context, context_offset=5
    )

    assert classifier.classify(candidate) is True


def test_gliner_onnx_classifier_does_not_confirm_a_candidate_outside_any_span(
    monkeypatch,
):
    # Acceptance criterion (fail-closed preserved): a candidate token that GLiNER
    # doesn't cover with any span still returns False and delegates to the inner
    # adjudicator, even when other, unrelated spans are present in the same context.
    context = "John Smith called from Acme Corporation about Project Falcon."
    stub_model = _StubGlinerModel(hits=frozenset({"John Smith", "Acme Corporation"}))
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="Falcon", start=54, end=60, context=context, context_offset=54
    )

    assert classifier.classify(candidate) is False


class _LabelAwareStubGlinerModel:
    """Stand-in that mirrors real zero-shot GLiNER label filtering: a seeded span
    only comes back from ``predict_entities`` when its own tagged label is among
    the labels requested for that call -- unlike ``_StubGlinerModel`` above, which
    returns every seeded hit regardless of the requested label set. Needed for
    issue #163: proving a span disappears once its label is dropped from
    ``_GLINER_LABELS`` requires a stub that actually respects the requested labels.
    """

    def __init__(self, tagged_hits: dict[str, str]) -> None:
        self._tagged_hits = tagged_hits

    def predict_entities(self, text: str, labels: list[str]) -> list[dict]:
        entities = []
        for span_text, label in self._tagged_hits.items():
            if label not in labels:
                continue
            start = text.find(span_text)
            if start != -1:
                entities.append(
                    {
                        "text": span_text,
                        "label": label,
                        "start": start,
                        "end": start + len(span_text),
                    }
                )
        return entities


def _candidate_for_token(context: str, token: str) -> CandidateSpan:
    start = context.find(token)
    return CandidateSpan(
        text=token, start=start, end=start + len(token), context=context, context_offset=start
    )


def test_gliner_label_set_no_longer_confirms_system_prompt_product_boilerplate(
    monkeypatch,
):
    # Issue #163 live repro: GLiNER's zero-shot "product" label tags generic
    # agent/system-prompt vocabulary at high confidence (Tool Runner 0.69, Managed
    # Agents 0.62, Artifacts 0.60, VS Code 0.67). A GLiNER positive skips the inner
    # adjudicator entirely (ADR-0033 Mode A) -- over-detection here means this
    # boilerplate lands in the review inbox as a confirmed entity. The fix tunes
    # the requested label set for precision on agent traffic, so a model that would
    # still tag these spans "product" never gets asked for that label in the first
    # place.
    context = (
        "You have access to Tool Runner and Managed Agents and Artifacts. "
        "Available via claude.ai/code, and IDE extensions (VS Code, JetBrains)."
    )
    stub_model = _LabelAwareStubGlinerModel(
        tagged_hits={
            "Tool Runner": "product",
            "Managed Agents": "product",
            "Artifacts": "product",
            "VS Code": "product",
        }
    )
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")

    for token in ("Tool", "Managed", "Agents", "Artifacts", "Code"):
        candidate = _candidate_for_token(context, token)
        assert classifier.classify(candidate) is False


def test_gliner_still_confirms_genuine_person_and_organization_entities(monkeypatch):
    # Acceptance criterion: the tuned label set must not regress genuine PII
    # detection -- person/organization spans are the actual privacy target.
    context = "Sarah Bergmann called from Nordwind Logistik about the shipment."
    stub_model = _LabelAwareStubGlinerModel(
        tagged_hits={"Sarah Bergmann": "person", "Nordwind Logistik": "organization"}
    )
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")

    assert classifier.classify(_candidate_for_token(context, "Sarah")) is True
    assert classifier.classify(_candidate_for_token(context, "Nordwind")) is True


def test_gliner_onnx_classifier_classify_type_returns_the_matched_gliner_label(
    monkeypatch,
):
    # Issue #167 root cause: GLiNER already has the label (person/organization) in
    # predict_entities' own output, but classify() collapsed it to a bool before the
    # mint pass ever saw it. classify_type() is the new richer seam that surfaces
    # GLiNER's own label for a confirmed span, so the mint pass can pick a
    # type-appropriate surrogate pool (ADR-0005) instead of always defaulting to
    # person-shaped names.
    context = "Sarah Bergmann called from Nordwind Logistik about the shipment."
    stub_model = _LabelAwareStubGlinerModel(
        tagged_hits={"Sarah Bergmann": "person", "Nordwind Logistik": "organization"}
    )
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")

    assert classifier.classify_type(_candidate_for_token(context, "Sarah")) == "person"
    assert (
        classifier.classify_type(_candidate_for_token(context, "Nordwind"))
        == "organization"
    )


def test_gliner_onnx_classifier_classify_type_returns_none_outside_any_span(
    monkeypatch,
):
    context = "Sarah Bergmann called from Nordwind Logistik about the shipment."
    stub_model = _LabelAwareStubGlinerModel(
        tagged_hits={"Sarah Bergmann": "person", "Nordwind Logistik": "organization"}
    )
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="shipment", start=0, end=8, context=context, context_offset=57
    )

    assert classifier.classify_type(candidate) is None


def test_gliner_onnx_classifier_classify_span_returns_the_absolute_span_extent(
    monkeypatch,
):
    # Issue #170: classify_type() only ever surfaced GLiNER's label, discarding
    # the covering span's own boundaries -- so a sibling token inside the same
    # multi-word span that GLiNER doesn't independently confirm for its own
    # candidate call (or that the inner adjudicator dismisses standalone as a
    # common noun, #164/#165) has no way to inherit the wider extent at mint
    # time. classify_span() surfaces the absolute [start, end) of GLiNER's own
    # covering span in the coordinate space of the *full hop text* the
    # candidate's own ``start``/``context_offset`` are relative to -- not just
    # the narrower ``context`` window -- so engine.py's mint pass can widen the
    # entity to the whole span.
    full_text = "Hi, ich bin Sarah Bergmann von Nordwind Logistik heute."
    candidate_start = full_text.index("Nordwind")
    window_left = candidate_start - 5  # context starts a bit before the
    # candidate -- context_offset is then nonzero, proving the absolute-offset
    # math doesn't accidentally treat context_offset as if it were absolute.
    context = full_text[window_left:]
    stub_model = _LabelAwareStubGlinerModel(
        tagged_hits={"Nordwind Logistik": "organization"}
    )
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="Nordwind",
        start=candidate_start,
        end=candidate_start + len("Nordwind"),
        context=context,
        context_offset=candidate_start - window_left,
    )

    result = classifier.classify_span(candidate)

    assert result is not None
    label, span_start, span_end = result
    assert label == "organization"
    assert full_text[span_start:span_end] == "Nordwind Logistik"


def test_gliner_onnx_classifier_classify_span_returns_none_outside_any_span(
    monkeypatch,
):
    context = "Sarah Bergmann called from Nordwind Logistik about the shipment."
    stub_model = _LabelAwareStubGlinerModel(
        tagged_hits={"Sarah Bergmann": "person", "Nordwind Logistik": "organization"}
    )
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="shipment", start=57, end=65, context=context, context_offset=57
    )

    assert classifier.classify_span(candidate) is None


class _OffsetDriftStubGlinerModel:
    """Stand-in for a loaded GLiNER model whose reported offsets live in a
    *different* Unicode coordinate space than ``candidate.context``'s own
    Python ``str`` indices -- issue #179's root cause. A real tokenizer that
    normalizes/decomposes text before tagging spans (each precomposed umlaut
    becomes two codepoints under NFD) reports ``start``/``end`` in ITS OWN
    coordinate space; every position after such a character drifts from the
    caller's Python ``str`` index by one unit per precomposed character. This
    is deliberately NOT ``_StubGlinerModel``'s ``text.find`` (which computes
    offsets over the exact string ``predict_entities`` was called with --
    always the same coordinate space by construction, hiding this bug): here
    offsets are computed over the NFD-decomposed form, then handed back
    unconverted, exactly like the real drift the issue describes.
    """

    def __init__(self, hits: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._hits = hits

    def predict_entities(self, text: str, labels: list[str]) -> list[dict]:
        self.calls.append((text, labels))
        decomposed = unicodedata.normalize("NFD", text)
        entities = []
        for token in self._hits:
            decomposed_token = unicodedata.normalize("NFD", token)
            start = decomposed.find(decomposed_token)
            if start != -1:
                entities.append(
                    {
                        "text": token,
                        "label": "organization",
                        "start": start,
                        "end": start + len(decomposed_token),
                    }
                )
        return entities


def test_gliner_onnx_classifier_classify_span_reanchors_when_umlauts_precede_the_entity(
    monkeypatch,
):
    # Issue #179 live repro: two precomposed umlauts ("Vörösmarty") precede the
    # novel org candidate inside GLiNER's context window. A tokenizer whose own
    # offsets are computed over an NFD-decomposed form of the text drifts +1
    # Python `str` position per such umlaut ahead of the true start/end.
    # classify_span must not trust entity["start"]/["end"] verbatim -- it must
    # re-anchor against candidate.context's own (Python str) coordinate space
    # using the adjudicator's own reported entity text, or a real-value
    # fragment mis-slices into the clear (the privacy bug this issue is about).
    context = "Sabine Vörösmarty proposed a deal with Ostwind Datentechnik today."
    true_start = context.index("Ostwind Datentechnik")
    true_end = true_start + len("Ostwind Datentechnik")
    stub_model = _OffsetDriftStubGlinerModel(hits=frozenset({"Ostwind Datentechnik"}))
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    candidate = CandidateSpan(
        text="Ostwind",
        start=true_start,
        end=true_start + len("Ostwind"),
        context=context,
        context_offset=true_start,
    )

    result = classifier.classify_span(candidate)

    assert result is not None
    label, span_start, span_end = result
    assert label == "organization"
    assert (span_start, span_end) == (true_start, true_end)
    assert context[span_start:span_end] == "Ostwind Datentechnik"


class _DismissAllInner:
    """Inner adjudicator that dismisses every candidate GLiNER doesn't confirm --
    keeps a request-path test focused on the GLiNER-confirmed org entity alone,
    mirroring ``_CommonNounDismissingInner`` in test_l3_surrogate_coalescing.py.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=False)


def test_umlaut_preceding_novel_org_mints_one_intact_surrogate_no_fragment_leak(
    monkeypatch,
):
    # Issue #179 end-to-end live repro, driven through the actual mint pass
    # (engine.blindfold_payload) with the real GlinerOnnxClassifier -- not a
    # hand-fabricated span_start/span_end stub (test_l3_surrogate_coalescing.py's
    # _SpanAwareGlinerStub) -- so this proves the fix holds at the seam where the
    # leak actually happened: two precomposed umlauts ("Vörösmarty") precede the
    # novel org entity inside GLiNER's own context window, and the underlying
    # model's offsets drift out of Python `str` coordinate space.
    #
    # Leak-audit clause A: the outbound (blindfolded) text carries neither the
    # real org name nor any fragment of it -- no partial "Ostwind Da" glued to a
    # placeholder.
    # Leak-audit clause B: closed-world restore hands the client back the full
    # real value, with no raw/glued placeholder left over.
    # Leak-audit clause D: the post-restore resolution gate stays clean.
    text = "Hi, ich bin Sabine Vörösmarty von Ostwind Datentechnik heute."
    stub_model = _OffsetDriftStubGlinerModel(hits=frozenset({"Ostwind Datentechnik"}))
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    detector = L3Detector(
        GlinerCascadeAdjudicator(classifier=classifier, inner=_DismissAllInner())
    )

    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    payload = {"model": "m", "messages": [{"role": "user", "content": text}]}

    blinded, session = blindfold_payload(payload, mapping, detector, inbox)

    blinded_text = blinded["messages"][0]["content"]
    assert "Ostwind" not in blinded_text
    assert "Datentechnik" not in blinded_text
    assert "Da" not in blinded_text  # no leftover fragment glued to the placeholder

    org_items = [item for item in inbox.list() if item.entity_type == "organization"]
    assert len(org_items) == 1
    assert org_items[0].real == "Ostwind Datentechnik"
    assert org_items[0].provisional_surrogate in blinded_text

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": blinded_text}],
    }
    restored = restore_response(response, session)
    restored_text = restored["content"][0]["text"]
    assert "Ostwind Datentechnik" in restored_text
    assert org_items[0].provisional_surrogate not in restored_text

    resolution_gate(restored, session)  # must not raise: nothing left unresolved


def test_recurring_org_mention_reanchors_per_hop_no_stale_span_fragment_leak(
    monkeypatch,
):
    # Issue #207, residual of #179. Confirmed root cause (NOT the primary "NFC vs
    # NFD tokenizer offset" hypothesis in the issue body -- see this test file's
    # own #179 tests above, which prove that path already sound): L3ContentCache
    # (l3.py) is keyed only on (span_text, context) -- content, not position -- but
    # before this fix it stored/replayed L3Adjudication.span_start/span_end
    # *verbatim*, which are absolute offsets into whichever hop's text first
    # populated the entry. An agentic transcript re-quotes the same sentence turn
    # after turn (ADR-0003's own stated reason this cache exists), so the identical
    # local (candidate.text, candidate.context) pair recurs at a *different*
    # absolute position on a later hop. Issue #170's span widening (an
    # authoritative span may extend past the confirming candidate's own token,
    # e.g. "Kestrel" confirms but the org span widens to "Kestrel LLC") gives the
    # stale span just enough slack to still pass engine.py's #179 containment
    # backstop for the new position -- so instead of raising L3Unavailable, the
    # mint pass silently slices whatever real characters sit at the *first*
    # occurrence's stale coordinates in the *second* hop's text.
    #
    # Leak-audit clause A: neither hop's outbound text may carry a real-value
    # fragment glued to a placeholder (the #179 signature: no separating word
    # boundary). Leak-audit clause B: closed-world restore hands back the exact
    # real org name on both hops, with no raw placeholder left over.
    org_text = "Kestrel LLC"
    # "LLC" alone doesn't match `_CAPITALIZED_RE` (all-caps after the first
    # letter) -- only "Kestrel" is its own candidate, so GLiNER's confirmed span
    # (widened to cover "Kestrel LLC") has slack past "Kestrel"'s own end, the
    # exact condition that lets a stale cached span coincidentally satisfy the
    # containment backstop instead of tripping it.
    filler_before = "Annika Brückner mentioned that " * 2
    filler_after = " signed the agreement yesterday afternoon"
    block = filler_before + org_text + filler_after

    stub_model = _LabelAwareStubGlinerModel(tagged_hits={org_text: "organization"})
    monkeypatch.setattr(l3_gliner, "_load_gliner_model", lambda model_path: stub_model)
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-base-v1.0")
    detector = L3Detector(
        GlinerCascadeAdjudicator(classifier=classifier, inner=_DismissAllInner())
    )
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()

    # Hop one: mints "Kestrel LLC" as a fresh novel org, populating the content
    # cache's "Kestrel" entry.
    turn_one = "Notes: " + block
    payload_one = {"model": "m", "messages": [{"role": "user", "content": turn_one}]}
    blinded_one, session_one = blindfold_payload(
        payload_one, mapping, detector, inbox
    )
    text_one = blinded_one["messages"][0]["content"]
    assert org_text not in text_one
    org_item = next(item for item in inbox.list() if item.entity_type == "organization")
    assert org_item.real == org_text
    assert org_item.provisional_surrogate in text_one

    # Hop two: the identical sentence recurs (an agent re-quoting earlier
    # context), shifted a few characters later -- still within the widened
    # span's own slack -- with unrelated trailing prose after it.
    turn_two_prefix = "Notes: " + "p" * 3 + " "
    trailing = " the deal closed quickly and the client seemed satisfied with pricing"
    turn_two = turn_two_prefix + block + trailing
    payload_two = {"model": "m", "messages": [{"role": "user", "content": turn_two}]}
    blinded_two, session_two = blindfold_payload(
        payload_two, mapping, detector, inbox
    )
    text_two = blinded_two["messages"][0]["content"]

    # Same real value -> same provisional surrogate (inbox.upsert reuses by
    # `real`, ADR-0037) -- so hop two must blindfold to the exact same surrogate
    # hop one used, with clean word boundaries on both sides: no real-value
    # fragment glued to a placeholder (the #179 signature), and the unrelated
    # trailing prose completely intact -- never partially consumed by a
    # mis-anchored mint.
    assert "mentioned that " + org_item.provisional_surrogate + " signed" in text_two
    assert trailing in text_two
    assert org_text not in text_two

    response_two = {
        "id": "msg_2",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text_two}],
    }
    restored_two = restore_response(response_two, session_two)
    restored_text_two = restored_two["content"][0]["text"]
    assert org_text in restored_text_two

    resolution_gate(restored_two, session_two)  # fail-closed: nothing unresolved


class _FakeGLiNERClass:
    """Stand-in for the real ``gliner.GLiNER`` class -- records the kwargs
    ``from_pretrained`` receives, so a test can assert the loader requests the
    exact artifact ``GLINER_MODEL_MANIFEST`` (gliner_provisioning.py) provisions,
    never a PyTorch default that needs files outside the manifest (issue #159).
    """

    calls: list[dict] = []

    @classmethod
    def from_pretrained(cls, model_path, **kwargs):
        cls.calls.append({"model_path": model_path, **kwargs})
        return _StubGlinerModel(hits=frozenset({"Klaus"}))


def test_load_gliner_model_loads_the_onnx_artifact_the_manifest_provisions(monkeypatch):
    # Issue #159's loader/manifest artifact-alignment defect: the manifest downloads
    # only onnx/model_quint8.onnx + configs (never pytorch_model.bin), so the loader
    # must request that same ONNX artifact by name -- a bare `from_pretrained(path)`
    # defaults to PyTorch and would find no weights in a real provisioned dir.
    fake_module = types.ModuleType("gliner")
    fake_module.GLiNER = _FakeGLiNERClass
    monkeypatch.setitem(sys.modules, "gliner", fake_module)
    _FakeGLiNERClass.calls = []

    l3_gliner._load_gliner_model("/data/models/gliner-pii-base-v1.0")

    assert len(_FakeGLiNERClass.calls) == 1
    call = _FakeGLiNERClass.calls[0]
    assert call["model_path"] == "/data/models/gliner-pii-base-v1.0"
    assert call["load_onnx_model"] is True
    assert call["onnx_model_file"] == l3_gliner.GLINER_ONNX_MODEL_FILE
    assert call["local_files_only"] is True


def test_run_gliner_activation_smoke_test_passes_when_the_canned_sentence_detects():
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))

    l3_gliner.run_gliner_activation_smoke_test(classifier)  # does not raise


def test_run_gliner_activation_smoke_test_refuses_when_nothing_is_detected():
    # Issue #159 acceptance criterion: a model that loads but detects zero entities
    # on the fixed canned sentence must refuse activation -- a checksum proves
    # identity, not function.
    classifier = _RecordingClassifier(positives=frozenset())

    with pytest.raises(l3_gliner.GlinerActivationSmokeTestFailedError):
        l3_gliner.run_gliner_activation_smoke_test(classifier)
