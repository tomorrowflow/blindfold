"""Offline leak check (ADR-0047 §10, issue #256).

Scans an Exchange capture's ``observed`` outbound payload for every real value in
``session.injected`` (the footer's witnessed pair table) and every real value the
entity graph knows (``mapping.real_values()``) -- the offline twin of the pre-egress
leak gate (``blindfold.engine.leak_gate``): exhaustive where the inline gate must be
fast, and it validates that gate rather than trusting it. Deliberately distinct from
#61 (inline structural re-check) and #78 (residual-content leakage).

A hit is a **leak** and outranks every other severity classification this module's
sibling ``capture_comparison`` produces. The offending value is referenced by
surrogate or hash -- never plaintext -- in any finding, mirroring
``engine.scrub_entity_reference``'s own scrubbed-reason rule (SEC-3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from blindfold.engine import walk_string_leaves
from blindfold.surrogates import SurrogateMapping

from .capture import FooterRecord, OutboundRecord

SEVERITY_LEAK = "leak"


def _collect_text(payload: Any) -> str:
    parts: list[str] = []
    walk_string_leaves(payload, parts.append)
    return "\x00".join(parts)


def _scrubbed_ref(real: str, mapping: SurrogateMapping) -> str:
    surrogate = mapping.surrogate_for(real)
    if surrogate is not None:
        return surrogate
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
    return f"hash:{digest}"


@dataclass(frozen=True)
class LeakFinding:
    """One real value found crossing egress in the observed outbound payload.

    ``ref`` is a surrogate or a scrubbed hash -- never the plaintext real value
    (SEC-3, the scrubbed-reason rule).
    """

    severity: str
    ref: str


@dataclass(frozen=True)
class LeakCheckResult:
    findings: tuple[LeakFinding, ...]
    checked_count: int

    def summary(self) -> str:
        if not self.findings:
            return f"leak check: no real value found, {self.checked_count} value(s) checked"
        refs = ", ".join(finding.ref for finding in self.findings)
        return (
            f"leak check: LEAK -- {len(self.findings)} real value(s) found in the "
            f"outbound payload (ref: {refs})"
        )


def leak_check(records: Iterable, mapping: SurrogateMapping) -> LeakCheckResult:
    """Scan every observed ``OutboundRecord`` payload in ``records`` for a real value
    in the footer's ``injected`` pair table or in ``mapping.real_values()`` (the
    entity graph, read-only). Returns a result even when clean -- absence of output
    is not a pass.
    """
    real_values: set[str] = set(mapping.real_values())
    for record in records:
        if isinstance(record, FooterRecord):
            real_values.update(record.injected.values())

    findings: list[LeakFinding] = []
    for record in records:
        if not isinstance(record, OutboundRecord):
            continue
        outbound_text = _collect_text(record.payload)
        for real in real_values:
            if real and real in outbound_text:
                findings.append(
                    LeakFinding(severity=SEVERITY_LEAK, ref=_scrubbed_ref(real, mapping))
                )

    return LeakCheckResult(findings=tuple(findings), checked_count=len(real_values))
