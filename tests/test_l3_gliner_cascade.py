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

from dataclasses import dataclass

from blindfold import l3_gliner
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.l3_gliner import GlinerCascadeAdjudicator, GlinerOnnxClassifier


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


class _RecordingAdjudicator:
    """Stub for the inner L3Adjudicator (Ollama/oMLX) -- records every call."""

    def __init__(self, decisions: dict[str, L3Adjudication] | None = None) -> None:
        self.calls: list[_Call] = []
        self._decisions = decisions or {}

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(_Call(text=candidate.text, context=candidate.context))
        return self._decisions.get(candidate.text, L3Adjudication(is_entity=False))


def test_gliner_positive_confirms_entity_without_calling_the_inner_adjudicator():
    # Position A (ADR-0033 Mode A): a GLiNER-positive span (PER/ORG/product/codename)
    # is accepted outright -- a false positive here is over-redaction (a quality bug,
    # not a privacy bug), so there's no need to spend an inner-adjudicator call on it.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))
    inner = _RecordingAdjudicator()
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidate = CandidateSpan(
        text="Klaus", start=11, end=16, context="We mention Klaus in passing."
    )

    decision = cascade.adjudicate(candidate)

    assert decision == L3Adjudication(is_entity=True)
    assert inner.calls == []


def test_gliner_negative_always_delegates_to_the_inner_adjudicator():
    # GLiNER's ~7-10% miss rate means a negative alone can't clear a candidate --
    # the inner adjudicator remains the sole arbiter of is_entity=False (ADR-0009
    # fail-closed). Delegation happens whether the inner adjudicator confirms or
    # rejects, so this asserts both directions of its returned verdict.
    classifier = _RecordingClassifier(positives=frozenset())
    inner = _RecordingAdjudicator(
        decisions={"Yasmin": L3Adjudication(is_entity=True)}
    )
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    confirmed = CandidateSpan(
        text="Yasmin", start=0, end=6, context="Yasmin signed the contract."
    )
    dismissed = CandidateSpan(
        text="Please", start=0, end=6, context="Please brief the team."
    )

    assert cascade.adjudicate(confirmed) == L3Adjudication(is_entity=True)
    assert cascade.adjudicate(dismissed) == L3Adjudication(is_entity=False)
    assert sorted(call.text for call in inner.calls) == ["Please", "Yasmin"]


def test_l3_content_cache_caches_the_cascades_final_verdict_transparently():
    # L3ContentCache is keyed on (span_text, context) regardless of which layer
    # (GLiNER or the inner adjudicator) produced the verdict -- L3Detector.detect()
    # must not re-classify or re-adjudicate a repeated candidate in the same context.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))
    inner = _RecordingAdjudicator()
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    detector = L3Detector(cascade)
    text = "We mention Klaus in passing."

    detector.detect(text, known_entities=[])
    detector.detect(text, known_entities=[])

    assert len(classifier.calls) == 1
    assert inner.calls == []


class _StubGlinerModel:
    """Stand-in for a loaded GLiNER model -- records predict_entities() calls."""

    def __init__(self, hits: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._hits = hits

    def predict_entities(self, text: str, labels: list[str]) -> list[dict]:
        self.calls.append((text, labels))
        return [
            {"text": token, "label": labels[0]}
            for token in self._hits
            if token in text
        ]


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
        text="Klaus", start=11, end=16, context="We mention Klaus in passing."
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
