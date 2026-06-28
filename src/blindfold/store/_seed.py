"""Loader for the vendored cold-start seed artifact (no DB).

The seed is a standalone JSON artifact vendored into this repo (ADR-0012): real entity
data (persons + variations, terms + variations, org_units hierarchy, relationships, role
assignments) used to protect known entities from request #1.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SEED_PATH = Path(__file__).with_name("vendored_seed.json")


@lru_cache(maxsize=1)
def load_vendored_seed() -> dict[str, Any]:
    """Return the parsed vendored seed (cached; the artifact is immutable at runtime)."""
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))
