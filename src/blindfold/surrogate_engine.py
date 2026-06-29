"""Coherent surrogate engine (ADR-0005).

Mints locale-aware plausible surrogates for persons and orgs, with a coherent
surrogate world: a person's fake email domain equals their employer's fake domain.
Email domains are drawn from the .invalid reserved namespace (never routable).
Date-shift offsets are deterministic per entity (hash-based), preserving intervals.
Minting is idempotent within one engine instance.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta

_PERSON_POOL: tuple[str, ...] = (
    "Anton Brauer",
    "Bärbel Lorenz",
    "Christoph Kaufmann",
    "Dorothea Sommer",
    "Emil Hartmann",
    "Franziska Neumann",
    "Georg Richter",
    "Hildegard Vogt",
    "Ignaz Fuchs",
    "Johanna Werner",
    "Klaus Zimmermann",
    "Lena Hoffmann",
    "Manfred Becker",
    "Nora Schäfer",
    "Otto Braun",
    "Petra Koch",
    "Rainer Wolf",
    "Sabine Meyer",
    "Tobias Berger",
    "Ursula Schulz",
    "Viktor König",
    "Waltraud Müller",
    "Xavier Günther",
    "Yvonne Wagner",
    "Zacharias Baum",
)

_ORG_POOL: tuple[str, ...] = (
    "Brunnen Technik AG",
    "Silber Systeme GmbH",
    "Tannen Gruppe",
    "Eichen Werk AG",
    "Nordstern Industrie",
    "Talblick Verbund GmbH",
    "Falkenberg Konsortium",
    "Lindenhof Betrieb AG",
    "Rabenstein Services",
    "Steinadler Holding",
)

# Legal/structural suffixes to strip when deriving a domain slug from a surrogate name.
_SUFFIX_RE = re.compile(
    r"\s+(?:AG|GmbH|SE|KGaA|KG|OHG|UG|e\.V\.|Inc\.?|Ltd\.?|LLC|Corp\.?"
    r"|Gruppe|Holding|Verbund|Konsortium|Betrieb|Industrie|Services|Werk|Systeme|Technik)$",
    re.IGNORECASE,
)

_DATE_SHIFT_RANGE = 180  # ±180 days


def _stable_index(canonical: str, pool_size: int) -> int:
    digest = int(hashlib.sha256(canonical.encode("utf-8")).hexdigest(), 16)
    return digest % pool_size


def _domain_from_name(name: str) -> str:
    """Derive a .invalid reserved-namespace domain slug from a surrogate name."""
    clean = _SUFFIX_RE.sub("", name).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-")
    return f"{slug}.invalid"


@dataclass(frozen=True)
class EntitySurrogate:
    """A coherent surrogate for one entity (person or org)."""

    name: str
    email_domain: str  # reserved-namespace .invalid domain
    email: str | None  # present for persons with a known employer relationship


class SurrogateEngine:
    """Mints coherent, idempotent entity surrogates (ADR-0005).

    All minting is deterministic within one engine instance: the same canonical
    always returns the same EntitySurrogate. The stable date-shift offset is
    hash-derived from the canonical name.
    """

    def __init__(self) -> None:
        self._registry: dict[str, EntitySurrogate] = {}

    def mint(self, canonical: str, relationships: dict | None = None) -> EntitySurrogate:
        """Return the stable EntitySurrogate for ``canonical``, minting one if needed.

        ``relationships`` may carry an ``"employer"`` key whose value is the canonical
        name of the org the person works at — the engine ensures the person's email
        domain matches the org's fake domain (coherent surrogate world, ADR-0005).
        """
        if canonical in self._registry:
            return self._registry[canonical]

        employer = (relationships or {}).get("employer")

        if employer and employer not in self._registry:
            self.mint(employer)

        if employer:
            org_surrogate = self._registry[employer]
            name = self._mint_person_name(canonical)
            email_domain = org_surrogate.email_domain
            first = name.split()[0].lower()
            last = name.split()[-1].lower() if len(name.split()) > 1 else "user"
            email = f"{first}.{last}@{email_domain}"
        else:
            name = self._mint_org_name(canonical)
            email_domain = _domain_from_name(name)
            email = None

        surrogate = EntitySurrogate(name=name, email_domain=email_domain, email=email)
        self._registry[canonical] = surrogate
        return surrogate

    def date_shift_offset(self, canonical: str) -> int:
        """Return the stable integer day-offset for ``canonical`` (±180 days).

        Deterministic (hash-based, no randomness): same canonical always returns
        the same offset, so all dates for one entity are shifted by the same delta,
        preserving intervals between events.
        """
        digest = int(hashlib.sha256(canonical.encode("utf-8")).hexdigest(), 16)
        return (digest % (2 * _DATE_SHIFT_RANGE + 1)) - _DATE_SHIFT_RANGE

    def date_shift(self, canonical: str, d: date) -> date:
        """Apply the stable per-entity date-shift offset to ``d``."""
        return d + timedelta(days=self.date_shift_offset(canonical))

    def _mint_person_name(self, canonical: str) -> str:
        return _PERSON_POOL[_stable_index(canonical, len(_PERSON_POOL))]

    def _mint_org_name(self, canonical: str) -> str:
        return _ORG_POOL[_stable_index(canonical, len(_ORG_POOL))]
