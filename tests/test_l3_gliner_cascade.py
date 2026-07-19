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
from dataclasses import dataclass

import pytest

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


@dataclass
class _BatchCall:
    texts: tuple[str, ...]


class _RecordingBatchInnerAdjudicator:
    """Stub for a batch-capable inner adjudicator (Ollama/oMLX, issue #157) --
    records every ``adjudicate_batch()`` call, never exposes single-candidate
    ``adjudicate()`` as a fallback (mirrors test_l3_detection.py's
    ``_RecordingBatchAdjudicator``, which asserts the batch path is genuinely taken
    rather than falling back unnoticed).
    """

    def __init__(self, decisions: dict[str, L3Adjudication] | None = None) -> None:
        self.batch_calls: list[_BatchCall] = []
        self._decisions = decisions or {}

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        self.batch_calls.append(_BatchCall(texts=tuple(c.text for c in candidates)))
        return [
            self._decisions.get(c.text, L3Adjudication(is_entity=False))
            for c in candidates
        ]


def test_gliner_positive_confirms_entity_without_calling_the_inner_adjudicator():
    # Position A (ADR-0033 Mode A): a GLiNER-positive span (PER/ORG, see _GLINER_LABELS)
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


def test_l3_detector_takes_the_batch_path_once_the_cascade_implements_adjudicate_batch():
    # Issue #157's own motivating bug: L3Detector.detect() duck-types the batch path
    # via hasattr(adjudicator, "adjudicate_batch") (l3.py). Before this slice, the
    # cascade lacked that method, so BLINDFOLD_L3_PROVIDER=gliner silently fell back
    # to one inner call per candidate (l3.py's per-candidate branch) regardless of
    # batch_size. Now hasattr is true, and an N-candidate pass collapses into ONE
    # inner.adjudicate_batch call carrying every GLiNER-negative -- not N inner calls.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))
    inner = _RecordingBatchInnerAdjudicator()
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)

    assert hasattr(cascade, "adjudicate_batch")

    detector = L3Detector(cascade, batch_size=10)
    text = "We met Klaus, Yasmin, Priya, and Boris at the offsite."

    results = detector.detect(text, known_entities=[])

    assert len(results) == 4
    # One inner round trip for the whole negative set (Yasmin, Priya, Boris) --
    # the collapse this issue exists for -- not one inner call per negative.
    assert len(inner.batch_calls) == 1
    assert inner.batch_calls[0].texts == ("Yasmin", "Priya", "Boris")


def test_adjudicate_batch_forwards_only_gliner_negatives_to_one_inner_batch_call():
    # Issue #157: GLiNER classification stays per-candidate (local), but negatives
    # collapse into ONE inner.adjudicate_batch call carrying exactly the negative
    # subset -- positives never reach the inner adjudicator, mirroring adjudicate()'s
    # own Position-A cascade semantics, just batched.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus", "Priya"}))
    inner = _RecordingBatchInnerAdjudicator({"Yasmin": L3Adjudication(is_entity=True)})
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidates = [
        CandidateSpan(text="Klaus", start=0, end=5, context="Klaus, Yasmin, Priya"),
        CandidateSpan(text="Yasmin", start=7, end=13, context="Klaus, Yasmin, Priya"),
        CandidateSpan(text="Priya", start=15, end=20, context="Klaus, Yasmin, Priya"),
    ]

    decisions = cascade.adjudicate_batch(candidates)

    assert decisions == [
        L3Adjudication(is_entity=True),
        L3Adjudication(is_entity=True),
        L3Adjudication(is_entity=True),
    ]
    assert len(inner.batch_calls) == 1
    assert inner.batch_calls[0].texts == ("Yasmin",)


def test_adjudicate_batch_all_positive_makes_no_inner_call():
    # M GLiNER-positives, zero negatives -- the inner adjudicator (batch or not)
    # is never invoked at all.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus", "Yasmin"}))
    inner = _RecordingBatchInnerAdjudicator()
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidates = [
        CandidateSpan(text="Klaus", start=0, end=5, context="Klaus and Yasmin"),
        CandidateSpan(text="Yasmin", start=10, end=16, context="Klaus and Yasmin"),
    ]

    decisions = cascade.adjudicate_batch(candidates)

    assert decisions == [
        L3Adjudication(is_entity=True),
        L3Adjudication(is_entity=True),
    ]
    assert inner.batch_calls == []


def test_adjudicate_batch_all_negative_sends_the_whole_set_in_one_inner_call():
    # K GLiNER-negatives, zero positives -- the entire candidate set forwards to
    # the inner adjudicator in one call.
    classifier = _RecordingClassifier(positives=frozenset())
    inner = _RecordingBatchInnerAdjudicator({"Yasmin": L3Adjudication(is_entity=True)})
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidates = [
        CandidateSpan(text="Klaus", start=0, end=5, context="Klaus and Yasmin"),
        CandidateSpan(text="Yasmin", start=10, end=16, context="Klaus and Yasmin"),
    ]

    decisions = cascade.adjudicate_batch(candidates)

    assert decisions == [
        L3Adjudication(is_entity=False),
        L3Adjudication(is_entity=True),
    ]
    assert len(inner.batch_calls) == 1
    assert inner.batch_calls[0].texts == ("Klaus", "Yasmin")


def test_adjudicate_batch_falls_back_to_per_candidate_when_inner_is_not_batch_capable():
    # Not every inner adjudicator implements adjudicate_batch (issue #142's own
    # duck-typed contract) -- adjudicate_batch() must still return a correct,
    # position-preserving result set by falling back to inner.adjudicate() per
    # negative, one call per negative candidate.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))
    inner = _RecordingAdjudicator(decisions={"Yasmin": L3Adjudication(is_entity=True)})
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidates = [
        CandidateSpan(text="Klaus", start=0, end=5, context="Klaus and Yasmin"),
        CandidateSpan(text="Yasmin", start=10, end=16, context="Klaus and Yasmin"),
        CandidateSpan(text="Please", start=21, end=27, context="Klaus and Yasmin. Please."),
    ]

    decisions = cascade.adjudicate_batch(candidates)

    assert decisions == [
        L3Adjudication(is_entity=True),
        L3Adjudication(is_entity=True),
        L3Adjudication(is_entity=False),
    ]
    assert sorted(call.text for call in inner.calls) == ["Please", "Yasmin"]


class _ShortResponseBatchInnerAdjudicator:
    """Stub for a malformed/short inner batch response (issue #157, mirrors
    test_l3_detection.py's own short-response stubs): returns fewer verdicts than
    negatives it was handed, and also exposes single-candidate ``adjudicate()`` so
    the per-candidate retry recovery has a seam to recover through.
    """

    def __init__(
        self, verdict_count: int, single_decisions: dict[str, L3Adjudication]
    ) -> None:
        self._verdict_count = verdict_count
        self._single_decisions = single_decisions
        self.single_calls: list[str] = []

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        return [L3Adjudication(is_entity=False)] * self._verdict_count

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.single_calls.append(candidate.text)
        return self._single_decisions[candidate.text]


def test_adjudicate_batch_short_inner_response_retries_then_fails_closed():
    # ADR-0009 fail-closed (issue #148's own regression shape, mirrored here for
    # the cascade's nested inner batch call): a short/malformed inner batch
    # response first retries the missing negatives one at a time through
    # inner.adjudicate(); only a candidate still unresolved after that falls back
    # to is_entity=True (over-redact), never a silent dismiss.
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))
    inner = _ShortResponseBatchInnerAdjudicator(
        verdict_count=0, single_decisions={"Priya": L3Adjudication(is_entity=False)}
    )
    # The batch call returns zero verdicts, so both negatives (Yasmin, Priya) are
    # retried one at a time. Yasmin is missing from single_decisions -- its retry
    # call raises KeyError, so it must fail closed (is_entity=True), not silently
    # vanish. Priya's retry recovers normally.
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidates = [
        CandidateSpan(text="Klaus", start=0, end=5, context="ctx"),
        CandidateSpan(text="Yasmin", start=7, end=13, context="ctx"),
        CandidateSpan(text="Priya", start=15, end=20, context="ctx"),
    ]

    decisions = cascade.adjudicate_batch(candidates)

    assert len(decisions) == 3
    assert decisions[0] == L3Adjudication(is_entity=True)  # Klaus: GLiNER-positive
    assert decisions[1] == L3Adjudication(is_entity=True)  # Yasmin: retry raised, over-redact
    assert decisions[2] == L3Adjudication(is_entity=False)  # Priya: retry recovered
    assert sorted(inner.single_calls) == ["Priya", "Yasmin"]


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
