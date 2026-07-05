"""Proxy configuration.

The upstream base URL (where the real provider lives) is configurable so the proxy can
be pointed at Anthropic in production and at a stub upstream in tests. Clients (e.g.
Claude Code) point at this proxy via ``ANTHROPIC_BASE_URL`` and authenticate with
``ANTHROPIC_AUTH_TOKEN``, whose value the proxy forwards upstream.

OpenBao Transit (ADR-0008 / issue #10):
  BLINDFOLD_OPENBAO_ADDR   — OpenBao server address (e.g. http://localhost:8200)
  BLINDFOLD_OPENBAO_TOKEN  — token with blindfold-proxy policy rights

Bootstrap admin (issue #43 / UX-1):
  BLINDFOLD_BOOTSTRAP_ADMIN — identity granted every role on the vendored seed's
                              workspace at startup, so a fresh single-user install
                              isn't RBAC-locked-out of its own workspace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_UPSTREAM_BASE_URL = "https://api.anthropic.com"
DEFAULT_OPENBAO_ADDR = "http://localhost:8200"


@dataclass(frozen=True)
class Settings:
    upstream_base_url: str = DEFAULT_UPSTREAM_BASE_URL
    openbao_addr: str = DEFAULT_OPENBAO_ADDR
    openbao_token: str = ""
    bootstrap_admin_identity: str = ""


def get_settings() -> Settings:
    return Settings(
        upstream_base_url=os.environ.get(
            "BLINDFOLD_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM_BASE_URL
        ),
        openbao_addr=os.environ.get("BLINDFOLD_OPENBAO_ADDR", DEFAULT_OPENBAO_ADDR),
        openbao_token=os.environ.get("BLINDFOLD_OPENBAO_TOKEN", ""),
        bootstrap_admin_identity=os.environ.get("BLINDFOLD_BOOTSTRAP_ADMIN", ""),
    )
