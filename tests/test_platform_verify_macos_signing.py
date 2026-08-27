"""macOS Developer ID signing + notarization, secrets-conditional (issue #370, CI
half of #198 -- ADR-0042's deferred-signing note).

platform-verify.yml's macOS job today codesigns ad-hoc (`--sign -`, no identity)
unconditionally, documented there as the deferred v2 concern. This slice adds a
secrets-conditional real-signing + notarization path that runs only when the
maintainer wizard has provisioned MACOS_SIGNING_CERT_P12_BASE64 /
MACOS_SIGNING_CERT_PASSWORD / NOTARY_API_KEY_ID / NOTARY_API_ISSUER_ID /
NOTARY_API_KEY_P8_BASE64 (secrets) and MACOS_SIGN_IDENTITY (repo variable) --
forks and PRs from forks, which never see this repo's secrets, keep running the
existing ad-hoc path byte-identically.

None of this can be exercised by an actual `codesign`/`notarytool`/`spctl` run in
this sandbox (no macOS, no signing identity, no network path to Apple's notary
service) -- these tests assert the narrower, sandbox-checkable property: the
workflow's committed YAML/shell text has the right shape, gates on secret
presence, and never leaks secret material into a log line. The first
secrets-present hosted run is the real proof (issue's own "Constraints" note).

Leak-audit clause analysis: N/A -- CI signing/notarization pipeline, no request
path. This module instead asserts the CI-equivalent of a leak-audit clause:
signing/notary secrets never reach a log line un-redacted.
"""

from __future__ import annotations

import pathlib
import re

WORKFLOW_PATH = (
    pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "platform-verify.yml"
)

_REQUIRED_SECRETS = [
    "MACOS_SIGNING_CERT_P12_BASE64",
    "MACOS_SIGNING_CERT_PASSWORD",
    "NOTARY_API_KEY_ID",
    "NOTARY_API_ISSUER_ID",
    "NOTARY_API_KEY_P8_BASE64",
]


def _text() -> str:
    return WORKFLOW_PATH.read_text()


def test_signing_secrets_gate_checks_presence_of_every_required_secret() -> None:
    text = _text()
    gate_match = re.search(r"HAS_MACOS_SIGNING_SECRETS:(.*?)\n\s*steps:", text, re.DOTALL)
    assert gate_match, (
        "expected a HAS_MACOS_SIGNING_SECRETS gate expression computed from secret "
        "presence -- the conditional must key on secrets, not on branch names "
        "(issue #370's explicit constraint)"
    )
    gate_expr = gate_match.group(1)
    for secret_name in _REQUIRED_SECRETS:
        assert f"secrets.{secret_name}" in gate_expr, (
            f"HAS_MACOS_SIGNING_SECRETS does not check secrets.{secret_name} -- a "
            "partially-provisioned secret set would silently take the real-signing "
            "path with a missing credential"
        )
    assert "github.ref" not in gate_expr and "github.event_name" not in gate_expr, (
        "the signing gate must key on secret presence, not on branch/event name "
        "(issue #370's explicit constraint)"
    )


def test_adhoc_fallback_codesign_steps_are_gated_on_secrets_absent() -> None:
    text = _text()
    # The pre-existing ad-hoc `--sign -` steps must stay -- byte-identical behavior
    # for forks/PRs-from-forks -- but now only run when the real-signing secrets are
    # NOT present, so the two paths never both codesign the same artifact.
    assert "codesign --force --sign - " in text, (
        "the ad-hoc fallback codesign steps (no identity) must be preserved -- "
        "forks and PRs from forks never see this repo's signing secrets and must "
        "keep building byte-identically to today"
    )
    adhoc_step_blocks = re.findall(
        r"- name: Codesign[^\n]*\(ad-hoc[^\n]*\)\n(.*?)run: codesign --force --sign - ",
        text,
        re.DOTALL,
    )
    assert len(adhoc_step_blocks) == 2, (
        "expected exactly two ad-hoc codesign steps (embedded blindfold-proxy, then "
        "the .app bundle), each individually gated"
    )
    for block in adhoc_step_blocks:
        assert "HAS_MACOS_SIGNING_SECRETS != 'true'" in block, (
            "each ad-hoc codesign step's `if:` must require "
            "env.HAS_MACOS_SIGNING_SECRETS != 'true', so it never double-signs "
            "alongside the real-signing path"
        )


def test_identity_import_step_creates_an_ephemeral_keychain_with_a_random_password() -> None:
    text = _text()
    import_match = re.search(
        r"- name: Import[^\n]*signing identity[^\n]*\n(.*?)\n      - name:", text, re.DOTALL
    )
    assert import_match, (
        "expected an 'Import ... signing identity' step gated on the real-signing "
        "path that creates the throwaway keychain (AC: ephemeral keychain, random "
        "password, decode the p12)"
    )
    step = import_match.group(1)

    assert "HAS_MACOS_SIGNING_SECRETS == 'true'" in step, (
        "the identity-import step must only run on the real-signing path"
    )
    assert "security create-keychain" in step
    assert "openssl rand" in step, (
        "the keychain password must be randomly generated, not a fixed literal "
        "(issue's AC: 'random password')"
    )
    assert "security import" in step
    assert "security set-key-partition-list" in step, (
        "codesign needs a non-interactive partition-list grant on the imported "
        "identity, or the sign step will hang/fail waiting on a keychain prompt"
    )
    assert "base64 --decode" in step and "MACOS_SIGNING_CERT_P12_BASE64" in step


def test_developer_id_sign_steps_use_hardened_runtime_and_inside_out_order() -> None:
    text = _text()
    proxy_sign_idx = text.index("Codesign embedded blindfold-proxy (Developer ID)")
    app_sign_idx = text.index("Codesign BlindfoldMenuBar.app (Developer ID)")
    assert proxy_sign_idx < app_sign_idx, (
        "the embedded blindfold-proxy binary must be signed before the .app bundle "
        "that contains it (inside-out order) -- signing outside-in invalidates the "
        "bundle's seal over the nested binary"
    )

    sign_block = text[proxy_sign_idx:app_sign_idx]
    assert "vars.MACOS_SIGN_IDENTITY" in sign_block
    assert "--options runtime" in sign_block
    assert "--timestamp" in sign_block
    assert "--entitlements packaging/macos/blindfold-proxy.entitlements.plist" in sign_block

    app_sign_block = text[app_sign_idx : app_sign_idx + 600]
    assert "vars.MACOS_SIGN_IDENTITY" in app_sign_block
    assert "--options runtime" in app_sign_block
    assert "--timestamp" in app_sign_block


def test_signing_entitlements_plist_is_valid_and_documents_each_entry() -> None:
    import plistlib

    plist_path = (
        pathlib.Path(__file__).parent.parent
        / "packaging"
        / "macos"
        / "blindfold-proxy.entitlements.plist"
    )
    assert plist_path.exists(), "packaging/macos/blindfold-proxy.entitlements.plist is missing"

    raw_text = plist_path.read_text()
    with plist_path.open("rb") as fh:
        entitlements = plistlib.load(fh)
    assert entitlements, "entitlements plist parses but declares nothing"

    for key in entitlements:
        # AC: "Any entitlement added is individually justified in a comment beside it."
        # A closed <!-- ... --> comment with only whitespace between its close and the
        # <key> is the sandbox-checkable proxy for "justified beside it."
        key_idx = raw_text.index(f"<key>{key}</key>")
        preceding = raw_text[:key_idx]
        last_comment_close = preceding.rfind("-->")
        assert last_comment_close != -1, f"entitlement {key} has no preceding comment at all"
        gap = preceding[last_comment_close + len("-->") :]
        assert gap.strip() == "", (
            f"entitlement {key} is not immediately preceded by its justifying comment "
            f"(found other content in between: {gap!r})"
        )


def test_notarization_step_submits_and_waits_then_staples() -> None:
    text = _text()
    notarize_match = re.search(
        r"- name: Notarize[^\n]*\n(.*?)\n      - name:", text, re.DOTALL
    )
    assert notarize_match, "expected a Notarize step gated on the real-signing path"
    step = notarize_match.group(1)

    assert "HAS_MACOS_SIGNING_SECRETS == 'true'" in step
    assert "NOTARY_API_KEY_P8_BASE64" in step
    assert "NOTARY_API_KEY_ID" in step
    assert "NOTARY_API_ISSUER_ID" in step
    assert "base64 --decode" in step, (
        "the notary API key (.p8) arrives base64-encoded per the issue's secret "
        "contract and must be decoded to a temp file before notarytool can use it"
    )
    assert "xcrun notarytool submit" in step
    assert "--wait" in step
    assert "xcrun stapler staple" in step

    staple_idx = step.index("xcrun stapler staple")
    submit_idx = step.index("xcrun notarytool submit")
    assert submit_idx < staple_idx, "must submit (and wait for) notarization before stapling"


def test_gatekeeper_assessment_gate_runs_after_stapling_on_secrets_present() -> None:
    text = _text()
    spctl_idx = text.index("spctl --assess --type execute")
    notarize_idx = text.index("- name: Notarize")
    assert notarize_idx < spctl_idx, "spctl --assess must run after notarization/stapling"

    # Find the enclosing step's `if:` (nearest preceding "if:" line before the run line).
    preceding = text[:spctl_idx]
    if_idx = preceding.rfind("if:")
    if_line = text[if_idx : text.index("\n", if_idx)]
    assert "HAS_MACOS_SIGNING_SECRETS == 'true'" in if_line, (
        "the spctl gate only makes sense (and should only run) on the real-signing "
        "path -- an ad-hoc-signed bundle is expected to fail spctl --assess"
    )


def test_signed_app_artifact_is_uploaded_on_secrets_present() -> None:
    text = _text()
    upload_match = re.search(
        r"- name: Upload[^\n]*\n(.*?)\n\n", text, re.DOTALL
    )
    assert upload_match, "expected an artifact-upload step for the stapled .app"
    step = upload_match.group(1)
    assert "actions/upload-artifact@v4" in step, (
        "match the version already used elsewhere in this repo's workflows "
        "(web-verify.yml)"
    )
    assert "BlindfoldMenuBar.app" in step


def test_cleanup_step_removes_keychain_and_key_material_in_always() -> None:
    text = _text()
    cleanup_match = re.search(
        r"- name: Clean up[^\n]*signing[^\n]*\n(.*?)(?:\n      - name:|\Z)", text, re.DOTALL
    )
    assert cleanup_match, (
        "expected a cleanup step removing the ephemeral keychain and decoded key "
        "material (AC: 'removed in an always() cleanup step')"
    )
    step = cleanup_match.group(1)
    assert "always()" in step, "cleanup must run even if an earlier signing step failed"
    assert "security delete-keychain" in step
    assert ".p12" in step and ".p8" in step, (
        "cleanup must remove both decoded secret files (the p12 cert and the p8 "
        "notary key), not just the keychain"
    )


def test_no_secret_value_is_echoed_or_traced_in_signing_steps() -> None:
    text = _text()
    assert "set -x" not in text, (
        "set -x would trace every command including secret-bearing ones into the "
        "job log"
    )
    # The only place a secret name may appear directly after `echo` is piped into
    # `base64 --decode` (never printed on its own to stdout).
    for line in text.splitlines():
        if "echo" in line and "secrets." not in line and any(
            s in line for s in _REQUIRED_SECRETS
        ):
            assert "base64 --decode" in line or "|" in line, (
                f"possible secret exposure via bare echo: {line!r}"
            )


def test_beta_md_unsigned_app_row_reflects_shipped_state() -> None:
    beta_md = (
        pathlib.Path(__file__).parent.parent / "docs" / "BETA.md"
    ).read_text()
    assert (
        "Until code signing and notarization land, the menu-bar app is ad-hoc signed"
        not in beta_md
    ), "BETA.md's Unsigned app row still describes signing as not-yet-landed"
    assert "#370" in beta_md, (
        "BETA.md's known-limitations row should reference issue #370, the CI half "
        "that shipped signing/notarization"
    )


def test_adr_0042_deferred_signing_note_is_updated() -> None:
    adr = (
        pathlib.Path(__file__).parent.parent / "docs" / "adr" / "0042-platform-verification-gate-all-hosted-github-actions.md"
    ).read_text()
    assert "#370" in adr, (
        "ADR-0042 should record the amendment that closes its own deferred-signing "
        "note (issue #370)"
    )
