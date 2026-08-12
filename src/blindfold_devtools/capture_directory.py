"""Capture directory: filename-as-id, count-bounded eviction (ADR-0047 §5).

The filename *is* the id -- a UTC timestamp plus a short discriminator, so
lexicographic sort order on the filename is chronological order, with no
separate index to keep in sync with what is actually on disk.

Eviction is count-bounded and runs at capture *start* (not lazily, e.g. at read
time), oldest first, and never removes a capture still being written --
unbounded growth here means plaintext prompts accumulating on disk forever.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .capture import CaptureWriter, DEFAULT_MAX_BYTES

CAPTURE_SUFFIX = ".jsonl"

DEFAULT_MAX_CAPTURES = 200


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_capture_id(*, now_iso: str | None = None, discriminator: str | None = None) -> str:
    """UTC timestamp + short discriminator. Sorting filenames sorts captures by
    time, ties broken by the discriminator -- which is also what keeps two
    captures started in the same instant from colliding.
    """
    ts = now_iso if now_iso is not None else _utc_now_iso()
    stamp = ts.replace(":", "").replace("-", "").replace(".", "")
    disc = discriminator if discriminator is not None else secrets.token_hex(4)
    return f"{stamp}-{disc}"


@dataclass
class CaptureDirectory:
    """Owns one capture directory: starts new captures, evicts old ones.

    ``max_captures`` bounds the directory: starting a new capture evicts the
    oldest existing ones (by filename order) until, once the new capture's
    file is added, at most ``max_captures`` remain -- but never a capture
    named in ``live_ids``, since that one is still being written.
    """

    path: Path
    max_captures: int = DEFAULT_MAX_CAPTURES
    max_bytes_per_capture: int = DEFAULT_MAX_BYTES

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def _existing_capture_paths(self) -> list[Path]:
        if not self.path.is_dir():
            return []
        return sorted(self.path.glob(f"*{CAPTURE_SUFFIX}"))

    def evict(self, *, live_ids: frozenset[str] = frozenset()) -> list[Path]:
        """Removes the oldest evictable captures so that at most
        ``max_captures`` remain on disk. Returns the paths removed. A capture
        whose id is in ``live_ids`` is never removed, even if that means the
        bound is temporarily exceeded.
        """
        existing = self._existing_capture_paths()
        evictable = [p for p in existing if p.stem not in live_ids]
        overflow = len(existing) - self.max_captures
        if overflow <= 0:
            return []
        to_remove = evictable[:overflow]
        for p in to_remove:
            p.unlink()
        return to_remove

    def start_capture(
        self,
        *,
        live_ids: frozenset[str] = frozenset(),
        now_iso: str | None = None,
        discriminator: str | None = None,
    ) -> tuple[str, CaptureWriter]:
        """Opens a fresh capture file whose name is its id, then evicts the
        oldest captures over the bound -- never this new one, and never
        anything in ``live_ids`` (ADR-0047 §5: "oldest evicted at capture
        start", timed to this moment rather than lazily at read time)."""
        self.path.mkdir(parents=True, exist_ok=True)
        capture_id = generate_capture_id(now_iso=now_iso, discriminator=discriminator)
        writer = CaptureWriter(
            self.path / f"{capture_id}{CAPTURE_SUFFIX}", max_bytes=self.max_bytes_per_capture
        )
        self.evict(live_ids=live_ids | {capture_id})
        return capture_id, writer
