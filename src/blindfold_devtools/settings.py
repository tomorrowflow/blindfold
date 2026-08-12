"""blindfold_devtools' own settings (ADR-0047 §5).

Deliberately separate from ``blindfold.config``: ``BLINDFOLD_EXCHANGE_CAPTURE_DIR``
must not appear there, so a release binary -- which never imports this package
(ADR-0047 §2) -- has never heard of the variable and cannot parse it. That closes
the "recognised but inert" hole at the root, rather than relying on the binary
simply choosing not to act on a name it still understands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DevtoolsSettings:
    exchange_capture_dir: str | None


def load_devtools_settings() -> DevtoolsSettings:
    raw = os.environ.get("BLINDFOLD_EXCHANGE_CAPTURE_DIR", "")
    return DevtoolsSettings(exchange_capture_dir=raw or None)
