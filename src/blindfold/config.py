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

Dev mode (SEC-2 / issue #44):
  BLINDFOLD_DEV_MODE       — explicit opt-in that lets ``blindfold serve`` start
                             against a root Transit token; refused otherwise.

Dedicated OpenAI upstream (transport sliver of #37 / issue #76):
  BLINDFOLD_OPENAI_UPSTREAM_BASE_URL — where ``POST /v1/chat/completions`` egresses.
                              Empty (default) means "not set": that path falls back to
                              the shared ``BLINDFOLD_UPSTREAM_BASE_URL``, i.e. today's
                              behavior. ``/v1/messages`` always uses the shared var.

L3 adjudicator (ADR-0022 / ADR-0031 / issue #57, #121):
  BLINDFOLD_L3_BASE_URL    — local L3 adjudicator daemon address (default:
                             http://localhost:11434 -- still Ollama's default; the
                             provider-agnostic rename (ADR-0031) only renames the
                             variable, the provider stays Ollama today).
  BLINDFOLD_L3_MODEL       — model tag to adjudicate with; empty means L3 is
                             unconfigured (fails closed, ADR-0009). A `:cloud`-suffixed
                             tag names a remotely-executing model and is refused at
                             startup with no override (the local-only invariant).

Serve bind address (ADR-0021 / ADR-0027, issue #91):
  BLINDFOLD_HOST           — bind host `blindfold serve` reports itself at (default:
                             127.0.0.1, matching the loopback-only default). Read by
                             the request path to build a block response's
                             `management_url` deep link -- never hardcoded.
  BLINDFOLD_PORT           — bind port, same purpose (default: 8000).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_UPSTREAM_BASE_URL = "https://api.anthropic.com"
DEFAULT_OPENBAO_ADDR = "http://localhost:8200"
DEFAULT_L3_BASE_URL = "http://localhost:11434"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class Settings:
    upstream_base_url: str = DEFAULT_UPSTREAM_BASE_URL
    openbao_addr: str = DEFAULT_OPENBAO_ADDR
    openbao_token: str = ""
    bootstrap_admin_identity: str = ""
    dev_mode: bool = False
    l3_base_url: str = DEFAULT_L3_BASE_URL
    l3_model: str = ""
    openai_upstream_base_url: str = ""
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    database_url: str = ""

    @property
    def effective_openai_upstream_base_url(self) -> str:
        """Where ``POST /v1/chat/completions`` egresses (issue #76).

        The dedicated var when set; the shared upstream var otherwise, so an
        unconfigured dedicated var reproduces today's behavior exactly.
        """
        return self.openai_upstream_base_url or self.upstream_base_url


def get_settings() -> Settings:
    return Settings(
        upstream_base_url=os.environ.get(
            "BLINDFOLD_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM_BASE_URL
        ),
        openbao_addr=os.environ.get("BLINDFOLD_OPENBAO_ADDR", DEFAULT_OPENBAO_ADDR),
        openbao_token=os.environ.get("BLINDFOLD_OPENBAO_TOKEN", ""),
        bootstrap_admin_identity=os.environ.get("BLINDFOLD_BOOTSTRAP_ADMIN", ""),
        dev_mode=os.environ.get("BLINDFOLD_DEV_MODE", "") not in ("", "0", "false", "False"),
        l3_base_url=os.environ.get("BLINDFOLD_L3_BASE_URL", DEFAULT_L3_BASE_URL),
        l3_model=os.environ.get("BLINDFOLD_L3_MODEL", ""),
        openai_upstream_base_url=os.environ.get("BLINDFOLD_OPENAI_UPSTREAM_BASE_URL", ""),
        host=os.environ.get("BLINDFOLD_HOST", DEFAULT_HOST),
        port=int(os.environ.get("BLINDFOLD_PORT", DEFAULT_PORT)),
        database_url=os.environ.get("BLINDFOLD_DATABASE_URL", ""),
    )
