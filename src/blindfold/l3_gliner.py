"""GLiNER cascade adjudicator (ADR-0033 Mode A, "Position A", issue #138).

A local NER confirmer chained *before* the inner LLM adjudicator, both living behind
the single ``L3Adjudicator`` seam (l3.py) -- CONTEXT.md's L3 definition: "L3 names the
role, not a model choice: any on-device implementation behind the adjudicator seam
(LLM via Ollama today; a small local classifier or a cascade tomorrow) is L3." This
class lives entirely behind that seam -- ``L3Detector.detect()``,
``select_candidate_spans``, and ``L3ContentCache`` are all unaffected.

Cascade logic (Position A):
- GLiNER positive (PER/ORG/product/codename) -> confirmed entity immediately, no
  inner-adjudicator call. A GLiNER false positive is over-redaction -- a quality bug,
  not a privacy bug -- so accepting it outright is safe.
- GLiNER negative -> always delegates to the inner adjudicator, which remains the
  sole arbiter of ``is_entity=False``. GLiNER's ~7-10% miss rate means a negative
  alone can't clear a candidate without risking a fail-closed violation (ADR-0009).
"""

from __future__ import annotations

from typing import Protocol

from .l3 import CandidateSpan, L3Adjudication, L3Adjudicator


class GlinerClassifier(Protocol):
    """The local-model boundary for GLiNER.

    Production wires a real ONNX-backed classifier; tests substitute a recording
    stub. GlinerCascadeAdjudicator depends only on this one-method interface --
    unlike the Ollama/oMLX adjudicator seam, there is no network client behind it at
    all: GLiNER inference is local-only by construction.
    """

    def classify(self, candidate: CandidateSpan) -> bool: ...


# Zero-shot labels for the GLiNER model (issue #138): PER/ORG/product/codename are
# specified at inference time, not baked into the model via retraining.
_GLINER_LABELS = ("person", "organization", "product", "codename")


def _load_gliner_model(model_path: str):
    # Deferred import: the ``gliner`` package (ONNX/CPU inference) is an optional
    # dependency of this seam, not a hard package dependency -- config/deployment
    # wiring for it is a separate slice. No network call: GLiNER model loading reads
    # only from ``model_path`` on local disk.
    from gliner import GLiNER

    return GLiNER.from_pretrained(model_path)


class GlinerOnnxClassifier:
    """Real ``GlinerClassifier`` behind a local, CPU-only ONNX GLiNER model.

    ``model_path`` is a constructor parameter (issue #138 acceptance criterion); the
    model itself is loaded lazily on first :meth:`classify` call, not at construction
    time, and reused afterward. There is no network client anywhere in this class --
    GLiNER inference never leaves the process.
    """

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._model = None

    def classify(self, candidate: CandidateSpan) -> bool:
        if self._model is None:
            self._model = _load_gliner_model(self._model_path)
        entities = self._model.predict_entities(candidate.context, list(_GLINER_LABELS))
        return any(entity["text"] == candidate.text for entity in entities)


class GlinerCascadeAdjudicator:
    """``L3Adjudicator`` that cascades a local GLiNER classifier ahead of an inner
    ``L3Adjudicator`` (ADR-0033 Mode A, Position A).
    """

    def __init__(self, classifier: GlinerClassifier, inner: L3Adjudicator) -> None:
        self._classifier = classifier
        self._inner = inner

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if self._classifier.classify(candidate):
            return L3Adjudication(is_entity=True)
        return self._inner.adjudicate(candidate)
