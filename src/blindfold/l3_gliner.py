"""GLiNER cascade adjudicator (ADR-0033 Mode A, "Position A", issue #138).

A local NER confirmer chained *before* the inner LLM adjudicator, both living behind
the single ``L3Adjudicator`` seam (l3.py) -- CONTEXT.md's L3 definition: "L3 names the
role, not a model choice: any on-device implementation behind the adjudicator seam
(LLM via Ollama today; a small local classifier or a cascade tomorrow) is L3." This
class lives entirely behind that seam -- ``L3Detector.detect()``,
``select_candidate_spans``, and ``L3ContentCache`` are all unaffected.

Cascade logic (Position A):
- GLiNER positive (PER/ORG, see ``_GLINER_LABELS``) -> confirmed entity immediately,
  no inner-adjudicator call. A GLiNER false positive is over-redaction -- a quality
  bug, not a privacy bug -- so accepting it outright is safe.
- GLiNER negative -> always delegates to the inner adjudicator, which remains the
  sole arbiter of ``is_entity=False``. GLiNER's ~7-10% miss rate means a negative
  alone can't clear a candidate without risking a fail-closed violation (ADR-0009).
"""

from __future__ import annotations

import logging
from typing import Protocol

from .l3 import CandidateSpan, L3Adjudication, L3Adjudicator

logger = logging.getLogger(__name__)


class GlinerClassifier(Protocol):
    """The local-model boundary for GLiNER.

    Production wires a real ONNX-backed classifier; tests substitute a recording
    stub. GlinerCascadeAdjudicator depends only on this one-method interface --
    unlike the Ollama/oMLX adjudicator seam, there is no network client behind it at
    all: GLiNER inference is local-only by construction.
    """

    def classify(self, candidate: CandidateSpan) -> bool: ...


# Zero-shot labels for the GLiNER model (issue #138): specified at inference time,
# not baked into the model via retraining. "product"/"codename" were dropped
# (issue #163): on agentic system-prompt traffic they confirm generic technical
# boilerplate ("Tool Runner", "Managed Agents", "Artifacts", "VS Code") as entities
# -- a GLiNER positive skips the inner adjudicator entirely (ADR-0033 Mode A), so
# this flooded the review inbox with over-redactions. The privacy target is
# people/organizations; see ADR-0033's Update note for the recall/precision
# tradeoff this accepts (a genuine product codename now always escalates to the
# inner LLM adjudicator instead of being GLiNER-confirmed outright).
_GLINER_LABELS = ("person", "organization")


class GlinerExtraMissingError(RuntimeError):
    """Raised when the GLiNER cascade is activated but the ``blindfold[gliner]``
    extra (``gliner`` + ``onnxruntime``) is not installed (ADR-0034 §6).

    ``gliner``/``onnxruntime`` are opt-in weight (~197 MB), never a base
    dependency -- a bare ``ImportError`` from the deferred import below would
    otherwise surface as an unexplained crash with no actionable next step.
    """


# The ONNX artifact GLINER_MODEL_MANIFEST (gliner_provisioning.py) provisions --
# the loader must request this exact file so the artifact it loads and the artifact
# the manifest downloads never drift apart (issue #159). A bare
# ``GLiNER.from_pretrained(model_path)`` defaults to a PyTorch checkpoint
# (``pytorch_model.bin``), which the manifest never fetches -- in a real
# provisioned-only directory that silently falls through to whatever GLiNER's own
# fallback does, not the quantized ONNX weights the manifest actually pins.
GLINER_ONNX_MODEL_FILE = "onnx/model_quint8.onnx"


def _load_gliner_model(model_path: str):
    # Deferred import: the ``gliner`` package (ONNX/CPU inference) is an optional
    # dependency of this seam (``blindfold[gliner]``, ADR-0034 §6), not a base
    # package dependency. No network call: GLiNER model loading reads only from
    # ``model_path`` on local disk.
    try:
        from gliner import GLiNER
    except ImportError as exc:
        raise GlinerExtraMissingError(
            "the GLiNER cascade requires the 'blindfold[gliner]' extra "
            "(gliner + onnxruntime), which is not installed; run "
            "`uv pip install 'blindfold[gliner]'` (or `pip install "
            "'blindfold[gliner]'`) to enable it."
        ) from exc

    return GLiNER.from_pretrained(
        model_path,
        load_onnx_model=True,
        onnx_model_file=GLINER_ONNX_MODEL_FILE,
        local_files_only=True,
    )


def _reanchor_entity_span(
    context: str, entity: dict, candidate_start: int, candidate_end: int
) -> tuple[int, int] | None:
    """Locate ``entity``'s true ``[start, end)`` extent in ``context``'s own
    Python ``str`` coordinate space (issue #179), instead of trusting
    ``entity["start"]``/``entity["end"]`` -- which may live in a different
    coordinate space (e.g. a tokenizer's own NFD-decomposed offsets) that
    silently drifts from ``context``'s actual indices once non-ASCII
    characters precede the span.

    Re-anchors on ``entity["text"]`` (GLiNER's own reported span text, always
    present in its output) via ``str.find`` over ``context`` itself -- the
    same string ``candidate.start``/``.end`` are already correct offsets
    into -- picking the occurrence that actually covers ``[candidate_start,
    candidate_end)`` (the candidate token's own always-correct position) to
    disambiguate a repeated ``entity["text"]`` elsewhere in the window.
    Returns ``None`` (fail closed on this entity, ADR-0009) when no occurrence
    covers the candidate -- never a raw, unverified offset.
    """
    text = entity.get("text")
    if not text:
        return None
    start = context.find(text)
    while start != -1:
        end = start + len(text)
        if start <= candidate_start and candidate_end <= end:
            return start, end
        start = context.find(text, start + 1)
    return None


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
        return self.classify_type(candidate) is not None

    def classify_type(self, candidate: CandidateSpan) -> str | None:
        """GLiNER's own label (``"person"``/``"organization"``) for the span
        covering ``candidate``, or ``None`` when nothing covers it (issue #167).

        The richer counterpart to :meth:`classify`: GLiNER already carries the
        type in ``predict_entities``' own output, so the mint pass can pick a
        type-appropriate surrogate pool (ADR-0005) instead of discarding it.
        """
        result = self.classify_span(candidate)
        return result[0] if result is not None else None

    def classify_span(self, candidate: CandidateSpan) -> tuple[str, int, int] | None:
        """GLiNER's own label plus the covering span's absolute ``[start, end)``
        extent, or ``None`` when nothing covers ``candidate`` (issue #170).

        The extent is in the coordinate space of the *full hop text*
        ``candidate.start``/``.end`` are themselves offsets into -- not the
        narrower ``candidate.context`` window ``predict_entities`` actually
        sees -- so it can widen a multi-word entity (e.g. an ORG span whose
        trailing common-noun token, "Logistik", is dismissed on its own by the
        inner adjudicator) past the single token this candidate names.

        Issue #179: ``entity["start"]``/``entity["end"]`` are NOT trusted as
        Python ``str`` indices verbatim -- a tokenizer that normalizes/
        decomposes text before tagging (precomposed umlauts, e.g. "Vörösmarty",
        becoming multiple codepoints) reports offsets in its own coordinate
        space, which silently drifts from ``candidate.context``'s actual
        indices once non-ASCII characters precede the span. Trusting the raw
        offset mis-slices the entity at mint time, egressing a real-value
        fragment and gluing a placeholder onto it. Instead, the span is
        re-anchored against ``candidate.context`` itself using GLiNER's own
        reported ``entity["text"]``, located and validated to actually cover
        the candidate -- never derived from ``entity["start"]``/``["end"]``.
        """
        if self._model is None:
            self._model = _load_gliner_model(self._model_path)
        entities = self._model.predict_entities(candidate.context, list(_GLINER_LABELS))
        candidate_start = candidate.context_offset
        candidate_end = candidate_start + len(candidate.text)
        window_left = candidate.start - candidate.context_offset
        for entity in entities:
            span = _reanchor_entity_span(
                candidate.context, entity, candidate_start, candidate_end
            )
            if span is None:
                continue
            local_start, local_end = span
            return (
                entity["label"],
                window_left + local_start,
                window_left + local_end,
            )
        return None


# Fixed canned sentence for the post-provision activation smoke test (issue #159).
# Synthetic, not real user data -- a single-token person span the classify() logic
# above can confirm unambiguously, mirroring the single-token names ("Klaus",
# "Yasmin", ...) this cascade's own tests already use.
GLINER_SMOKE_TEST_TEXT = "We met Klaus at the offsite; Acme confirmed the contract."
_GLINER_SMOKE_TEST_CANDIDATE = CandidateSpan(
    text="Klaus", start=7, end=12, context=GLINER_SMOKE_TEST_TEXT, context_offset=7
)


class GlinerActivationSmokeTestFailedError(RuntimeError):
    """Raised when a provisioned GLiNER model loads but detects zero entities on
    the fixed canned smoke-test sentence (issue #159) -- refused, not activated,
    mirroring :class:`~blindfold.gliner_provisioning.GlinerDigestMismatchError`. A
    checksum proves the downloaded bytes match the pinned revision; it says nothing
    about whether the model actually detects anything under the installed
    ``gliner``/``onnxruntime``/``transformers`` versions -- the exact silent-failure
    mode this issue exists to catch.
    """


def run_gliner_activation_smoke_test(classifier: GlinerClassifier) -> None:
    """Refuse activation if ``classifier`` detects nothing on the canned sentence
    (issue #159). Called after digest verification, before a model is considered
    ready to activate -- see :func:`~blindfold.gliner_provisioning.provision_gliner_model`.
    """
    if not classifier.classify(_GLINER_SMOKE_TEST_CANDIDATE):
        raise GlinerActivationSmokeTestFailedError(
            "the provisioned GLiNER model detected zero entities on the "
            "activation smoke test sentence -- refusing to activate; the model "
            "may be incompatible with the installed gliner/onnxruntime/"
            "transformers versions (ADR-0034)"
        )


class GlinerCascadeAdjudicator:
    """``L3Adjudicator`` that cascades a local GLiNER classifier ahead of an inner
    ``L3Adjudicator`` (ADR-0033 Mode A, Position A).
    """

    def __init__(self, classifier: GlinerClassifier, inner: L3Adjudicator) -> None:
        self._classifier = classifier
        self._inner = inner

    def _classify(
        self, candidate: CandidateSpan
    ) -> tuple[bool, str | None, int | None, int | None]:
        """Confirm/type a candidate through whichever seam ``self._classifier``
        implements, richest first: ``classify_span`` (issue #170 -- GLiNER's own
        label plus its covering span's absolute extent, wider than the
        candidate's own token when a sibling token in the same span wasn't
        independently confirmed), else ``classify_type`` (issue #167 -- label
        only, no extent), else the original bool-only ``classify`` (ADR-0033)
        with no type or extent at all. All three are valid ``GlinerClassifier``
        implementations, duck-typed like every other optional seam extension in
        this codebase (mirrors ``BatchL3Adjudicator``).
        """
        classify_span = getattr(self._classifier, "classify_span", None)
        if classify_span is not None:
            result = classify_span(candidate)
            if result is None:
                return False, None, None, None
            entity_type, span_start, span_end = result
            return True, entity_type, span_start, span_end
        classify_type = getattr(self._classifier, "classify_type", None)
        if classify_type is not None:
            entity_type = classify_type(candidate)
            return entity_type is not None, entity_type, None, None
        return self._classifier.classify(candidate), None, None, None

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        confirmed, entity_type, span_start, span_end = self._classify(candidate)
        if confirmed:
            return L3Adjudication(
                is_entity=True,
                entity_type=entity_type,
                span_start=span_start,
                span_end=span_end,
            )
        return self._inner.adjudicate(candidate)

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        """Batch counterpart to :meth:`adjudicate` (issue #157): GLiNER
        classification stays per-candidate (local, cheap) -- only the
        GLiNER-negatives are forwarded to the inner adjudicator, in one
        ``adjudicate_batch`` call when it exposes one (duck-typed, mirroring
        ``L3Detector``'s own ``BatchL3Adjudicator`` check), else per-candidate
        through ``inner.adjudicate``. Position-preserving: returns exactly
        ``len(candidates)`` results, in the same order.
        """
        decisions: list[L3Adjudication | None] = [None] * len(candidates)
        negative_indices: list[int] = []
        negatives: list[CandidateSpan] = []
        for index, candidate in enumerate(candidates):
            confirmed, entity_type, span_start, span_end = self._classify(candidate)
            if confirmed:
                decisions[index] = L3Adjudication(
                    is_entity=True,
                    entity_type=entity_type,
                    span_start=span_start,
                    span_end=span_end,
                )
            else:
                negative_indices.append(index)
                negatives.append(candidate)

        if negatives:
            if hasattr(self._inner, "adjudicate_batch"):
                negative_decisions = self._adjudicate_negatives_batch(negatives)
            else:
                negative_decisions = [
                    self._inner.adjudicate(candidate) for candidate in negatives
                ]
            for index, decision in zip(negative_indices, negative_decisions):
                decisions[index] = decision

        return decisions  # type: ignore[return-value]

    def _adjudicate_negatives_batch(
        self, negatives: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        """Mirrors ``L3Detector._adjudicate_batch``'s own recovery shape (issue
        #148), nested one level down: the inner adjudicator's ``adjudicate_batch``
        call itself failing (network/daemon down) propagates unhandled -- the
        caller (``L3Detector._adjudicate_batch``, wrapping this whole
        ``adjudicate_batch`` call) already converts that into ``L3Unavailable``
        (ADR-0009 fail-closed), so there's no need to duplicate that handling
        here. A short/malformed response (fewer verdicts than negatives) is
        this method's own job: retry the missing negatives one at a time
        through ``inner.adjudicate()``, and only a candidate still unresolved
        after that retry falls back to ``is_entity=True`` (over-redact, never a
        silent dismiss).
        """
        decisions = list(self._inner.adjudicate_batch(negatives))
        if len(decisions) < len(negatives):
            missing = negatives[len(decisions):]
            recovered, still_missing = self._retry_missing(missing)
            decisions = decisions + recovered
            if still_missing:
                logger.warning(
                    "gliner_cascade_inner_batch_short_response: "
                    "expected=%d received=%d missing=%d",
                    len(negatives),
                    len(negatives) - still_missing,
                    still_missing,
                )
        return decisions

    def _retry_missing(
        self, missing_candidates: list[CandidateSpan]
    ) -> tuple[list[L3Adjudication], int]:
        """Best-effort per-candidate recovery for an inner-batch shortfall,
        position-preserving: returns exactly ``len(missing_candidates)``
        verdicts, in the same order. A retry that itself raises fails closed
        for just that candidate (``is_entity=True``) rather than aborting the
        whole batch.
        """
        resolved: list[L3Adjudication] = []
        still_missing = 0
        for candidate in missing_candidates:
            try:
                resolved.append(self._inner.adjudicate(candidate))
            except Exception:
                resolved.append(L3Adjudication(is_entity=True))
                still_missing += 1
        return resolved, still_missing
