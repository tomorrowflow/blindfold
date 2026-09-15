"""`blindfold connect claude-desktop [--restore]` -- writes the Claude Desktop 3P
Gateway profile (ADR-0057 D7, issue #377).

Claude Desktop reads its 3P profile once at launch from a flat-key JSON file under
``configLibrary/``, applied by ``_meta.json``. This module builds that profile and
writes it into the on-disk layout the live contract spike (#372) confirmed, using the
Ollama precedent's backup-then-write shape so ``--restore`` can put a prior install back
exactly.

The profile constant (:data:`DESKTOP_INFERENCE_MODELS`) and :func:`build_desktop_profile`
are the single source the Connect page's own rendering (#376, sibling issue) shares --
one model list, not two.

Corrections applied here per the #372 live contract spike (trusted-maintainer comment on
this issue), superseding this issue's original brief:
  - ``inferenceGatewayAuthScheme`` is ``"x-api-key"``, never ``"bearer"`` -- Desktop's own
    validation rule warns against ``bearer`` for a loopback-shaped gateway URL, and
    ``x-api-key`` is what was confirmed live to reach ``/v1/messages`` with a 200
    (ADR-0057 Amendment, D7).
  - ``claude_desktop_config.json`` in the profile directory carries
    ``deploymentMode: "3p"`` unconditionally (confirmed needed, not conditional).
  - The base directory honours ``CLAUDE_USER_DATA_DIR`` -- the app's own env var --
    rather than a temp ``HOME``.
  - ``inferenceModels`` must never be empty (decides whether ``GET /v1/models``, which
    stays unimplemented per ADR-0057 D5, is probed at all -- measured 20 probes while
    unset, zero once pinned).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .policy import DEFAULT_WORKSPACE

CLAUDE_USER_DATA_DIR_ENV = "CLAUDE_USER_DATA_DIR"
BACKUP_DIR_NAME = "blindfold-backup"

PROFILE_ENTRY_NAME = "Blindfold"

# Corrected by the #372 live contract spike -- see module docstring. ADR-0019 forwards
# the inbound credential verbatim, so a Console API key sent as a Bearer token fails
# upstream; Desktop's own profile validation warns against exactly this combination.
AUTH_SCHEME = "x-api-key"

DEPLOYMENT_MODE = "3p"

WORKSPACE_HEADER = "x-blindfold-workspace"

# Full model IDs, not aliases: a hardcoded alias goes stale, and a full ID is what skips
# Desktop's GET /v1/models discovery call entirely (ADR-0057 D5). Opus, Sonnet, Haiku so
# Cowork sub-agents resolve (ADR-0057 D7).
DESKTOP_INFERENCE_MODELS: tuple[str, ...] = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)


def build_desktop_profile(
    *, host: str, port: int, workspace: str = DEFAULT_WORKSPACE
) -> dict:
    """The flat-key Desktop 3P profile (ADR-0057 D7), never carrying a credential.

    Shared with the Connect page's own rendering (#376) -- callers that need to write a
    credential add ``inferenceGatewayApiKey`` themselves, from an environment variable,
    never from this function.
    """
    return {
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": f"http://{host}:{port}",
        "inferenceGatewayAuthScheme": AUTH_SCHEME,
        "inferenceCredentialKind": "static",
        "inferenceCustomHeaders": {WORKSPACE_HEADER: workspace},
        "inferenceModels": list(DESKTOP_INFERENCE_MODELS),
        "chatTabEnabled": True,
    }


def resolve_claude_desktop_base_dir() -> Path:
    """Where Desktop's 3P profile directory lives -- macOS only this slice.

    ``CLAUDE_USER_DATA_DIR`` overrides when set (the app's own env var, and the test
    seam the #372 spike confirmed). Otherwise the real macOS default:
    ``~/Library/Application Support/Claude-3p`` -- a separate Electron profile
    directory from ``Claude/`` (confirmed live by the spike).
    """
    override = os.environ.get(CLAUDE_USER_DATA_DIR_ENV, "")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "Claude-3p"


_MANAGED_PLIST_REAL_PATH = Path(
    "/Library/Managed Preferences/com.anthropic.claudefordesktop.plist"
)


def resolve_managed_plist_path() -> Path:
    """Where a managed (MDM) profile, if any, would lock the endpoint.

    The real macOS location is root-owned system state, never present on a fresh
    single-user install (confirmed by the #372 spike) -- so the refusal this guards
    can't be constructed by a test against the real path. When the
    ``CLAUDE_USER_DATA_DIR`` test seam is active, this resolves under the same override
    root instead, so a test can create the condition; production (no override) always
    resolves to the real system path.
    """
    override = os.environ.get(CLAUDE_USER_DATA_DIR_ENV, "")
    if override:
        return Path(override).parent / "Managed Preferences" / _MANAGED_PLIST_REAL_PATH.name
    return _MANAGED_PLIST_REAL_PATH


class ManagedProfileError(Exception):
    """Raised when a managed (MDM) plist is present -- a managed device is the
    operator's MDM's job, not ours (ADR-0057 D7)."""


@dataclass(frozen=True)
class ConnectResult:
    profile_id: str
    profile_path: Path
    meta_path: Path
    config_path: Path
    backup_dir: Path
    wrote_key: bool


_META_FILENAME = "_meta.json"
_CONFIG_FILENAME = "claude_desktop_config.json"
_MANIFEST_FILENAME = "manifest.json"


def connect_claude_desktop(
    *,
    base_dir: Path,
    managed_plist_path: Path,
    host: str,
    port: int,
    workspace: str = DEFAULT_WORKSPACE,
    api_key_env: str | None = None,
) -> ConnectResult:
    """Write the Blindfold Desktop 3P profile into ``base_dir`` (ADR-0057 D7).

    Refuses (:class:`ManagedProfileError`) if ``managed_plist_path`` exists, before any
    write. Backs up whatever ``_meta.json`` / ``claude_desktop_config.json`` / the
    previously-applied profile held, under ``blindfold-backup/``, so :func:`restore_claude_desktop`
    can put a prior install back exactly.
    """
    if managed_plist_path.exists():
        raise ManagedProfileError(
            f"a managed profile is present at {managed_plist_path} -- a managed device "
            "is the operator's MDM's job; refusing to write."
        )

    config_library = base_dir / "configLibrary"
    meta_path = base_dir / _META_FILENAME
    config_path = base_dir / _CONFIG_FILENAME
    backup_dir = base_dir / BACKUP_DIR_NAME

    config_library.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    previous_meta: dict = {}
    if meta_path.exists():
        meta_text = meta_path.read_text()
        previous_meta = json.loads(meta_text)
        (backup_dir / _META_FILENAME).write_text(meta_text)
        previous_applied_id = previous_meta.get("appliedId")
        if previous_applied_id:
            previous_profile_path = config_library / f"{previous_applied_id}.json"
            if previous_profile_path.exists():
                (backup_dir / f"{previous_applied_id}.json").write_text(
                    previous_profile_path.read_text()
                )

    if config_path.exists():
        (backup_dir / _CONFIG_FILENAME).write_text(config_path.read_text())

    profile_id = str(uuid.uuid4())
    profile = build_desktop_profile(host=host, port=port, workspace=workspace)
    wrote_key = False
    if api_key_env:
        key_value = os.environ.get(api_key_env, "")
        if not key_value:
            raise ValueError(
                f"--api-key-from-env {api_key_env!r} was given but that environment "
                "variable is empty or unset."
            )
        profile["inferenceGatewayApiKey"] = key_value
        wrote_key = True

    profile_path = config_library / f"{profile_id}.json"
    profile_path.write_text(json.dumps(profile, indent=2))

    entries = list(previous_meta.get("entries", []))
    entries.append({"id": profile_id, "name": PROFILE_ENTRY_NAME})
    meta_path.write_text(
        json.dumps({"appliedId": profile_id, "entries": entries}, indent=2)
    )

    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())
    config["deploymentMode"] = DEPLOYMENT_MODE
    config_path.write_text(json.dumps(config, indent=2))

    manifest = {
        "meta_existed": bool(previous_meta),
        "config_existed": (backup_dir / _CONFIG_FILENAME).exists(),
        "written_profile_id": profile_id,
    }
    (backup_dir / _MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2))

    return ConnectResult(
        profile_id=profile_id,
        profile_path=profile_path,
        meta_path=meta_path,
        config_path=config_path,
        backup_dir=backup_dir,
        wrote_key=wrote_key,
    )


class NoBackupError(Exception):
    """Raised by :func:`restore_claude_desktop` when ``base_dir`` carries no
    ``blindfold-backup/`` -- nothing to restore from."""


@dataclass(frozen=True)
class RestoreResult:
    restored_profile_id: str
    meta_restored: bool
    config_restored: bool


def restore_claude_desktop(*, base_dir: Path) -> RestoreResult:
    """Reverse everything :func:`connect_claude_desktop` did, from its backup.

    Returns ``base_dir`` to a byte-identical state to before the write: the profile
    file Blindfold added is removed, ``_meta.json`` / ``claude_desktop_config.json``
    are restored from backup (or removed, if they didn't exist before), and the
    backup directory itself is removed.
    """
    backup_dir = base_dir / BACKUP_DIR_NAME
    manifest_path = backup_dir / _MANIFEST_FILENAME
    if not backup_dir.exists() or not manifest_path.exists():
        raise NoBackupError(f"no blindfold-backup/ under {base_dir} -- nothing to restore.")

    manifest = json.loads(manifest_path.read_text())
    written_profile_id = manifest["written_profile_id"]
    meta_existed = manifest["meta_existed"]
    config_existed = manifest["config_existed"]

    meta_path = base_dir / _META_FILENAME
    config_path = base_dir / _CONFIG_FILENAME
    profile_path = base_dir / "configLibrary" / f"{written_profile_id}.json"

    if meta_existed:
        meta_path.write_text((backup_dir / _META_FILENAME).read_text())
    elif meta_path.exists():
        meta_path.unlink()

    if config_existed:
        config_path.write_text((backup_dir / _CONFIG_FILENAME).read_text())
    elif config_path.exists():
        config_path.unlink()

    if profile_path.exists():
        profile_path.unlink()

    shutil.rmtree(backup_dir)

    return RestoreResult(
        restored_profile_id=written_profile_id,
        meta_restored=meta_existed,
        config_restored=config_existed,
    )


_QUIT_APPLESCRIPT = 'tell application "Claude" to quit'
_OPEN_COMMAND = ("open", "-a", "Claude")


def relaunch_claude_desktop(*, wait_seconds: float = 2.0) -> None:
    """Quit, wait, and relaunch Claude Desktop, so the profile just written is picked
    up (config is read once at launch -- ADR-0057 D7, the Ollama precedent).

    Desktop rewrites the applied profile during its own shutdown (trusted-maintainer
    comment #7 on issue #377), so this must run strictly AFTER the write, never
    before -- ``connect_claude_desktop`` never calls this itself; the caller (the CLI,
    behind ``--relaunch``) sequences it.
    """
    subprocess.run(["osascript", "-e", _QUIT_APPLESCRIPT], check=False)
    time.sleep(wait_seconds)
    subprocess.run(list(_OPEN_COMMAND), check=False)
