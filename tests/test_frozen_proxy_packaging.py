"""Frozen-proxy contract (ADR-0039, issue #184): the PyInstaller onefile spec.

Builds the proxy via ``packaging/blindfold-proxy.spec`` into a real,
self-contained binary and smoke-tests it directly -- with no venv/``uv``/
``PYTHONPATH`` on its ``PATH`` -- proving the ``.app ⊃ frozen-proxy ⊃
ui_dist`` layering ADR-0039 calls for. The macOS and Windows binaries
themselves are produced on the hosted ``platform-verify`` gate (issue
#192/#195, ADR-0042); this in-sandbox Linux build of the *same* spec is how
this slice proves the spec + wiring, per the issue's own scope carve-out
("the platform binary is produced on the runner; the spec + wiring are
what this slice owns").

Skip-guarded on PyInstaller being installed (the ``freeze`` dependency
group, ``pyproject.toml``) -- mirrors the Docker-skip pattern in
``tests/test_entity_graph_postgres.py``: building a frozen binary is heavy
dev/CI/release tooling, never a runtime dependency of ``blindfold serve``.

Leak-audit clause analysis: N/A this slice -- freezing/spawning a local
proxy binary touches no request-path detection/mint/restore logic; the
"Refused" scrubbed-reason test below asserts the *existing* fail-closed
startup guard (serve.py) survives freezing unchanged, not a new privacy
property.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
SPEC_PATH = REPO_ROOT / "packaging" / "blindfold-proxy.spec"
UI_DIST_DIR = REPO_ROOT / "src" / "blindfold" / "ui_dist"

# issue #428: a onefile PyInstaller binary re-extracts its whole archive to a
# fresh ``/tmp/_MEI*`` directory on every launch (this binary: a 94MB archive
# unpacking to 246MB on disk) before any application code runs -- a real,
# repeated-every-launch cost, not a one-time build cost. Measured in this
# sandbox with synthetic full-machine contention (all 6 cores busy-looped +
# six concurrent `dd` writers saturating disk, approximating the issue's
# "oMLX server + two Podman containers + a proxy" busy developer machine):
# a launch immediately after the binary was (re)built -- OS page cache for the
# binary's own bytes not yet warm -- took between 11.9s and 23.1s across
# repeated trials; every launch after that first one, even under the same
# continued contention, took 2.6s-4.8s. A flat 10s budget only ever covered
# the warm case. ``frozen_proxy_binary`` below pays one throwaway warm-up
# launch itself so every real test after it hits the warm-cache path in
# practice; ``_FROZEN_WARMUP_TIMEOUT`` covers the measured cold-launch worst
# case with headroom, ``_FROZEN_STARTUP_TIMEOUT`` covers the measured
# post-warm-up worst case with headroom.
_FROZEN_WARMUP_TIMEOUT = 45.0
_FROZEN_STARTUP_TIMEOUT = 20.0


def _pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pyinstaller_available(),
    reason="PyInstaller not installed -- run `uv sync --group freeze` to build the frozen proxy",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"nothing listening on 127.0.0.1:{port} after {timeout}s")


def _wait_for_port_or_die(proc: subprocess.Popen[str], port: int, *, timeout: float) -> None:
    """Like ``_wait_for_port``, but on timeout kills ``proc`` and folds its captured
    stdout/stderr into the raised error -- otherwise a genuine startup failure and a
    merely slow (but succeeding) one are indistinguishable from the test report
    (issue #428)."""
    try:
        _wait_for_port(port, timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise TimeoutError(
            f"{exc}\n--- child stdout ---\n{stdout}\n--- child stderr ---\n{stderr}"
        ) from exc


def test_wait_for_port_or_die_surfaces_child_stderr_on_timeout() -> None:
    """issue #428: the suite's own timeout must not discard the one thing that
    distinguishes a merely slow start from a genuinely failed one -- the
    child's captured stderr.

    Caught by this issue's own load-testing (a real failure the first time this
    test ran alongside four CPU-saturating busy-loops): a bare ``timeout=0.3``
    here raced the child's *process launch* itself under contention -- the
    child could still be waiting for its first scheduler slot when
    ``_wait_for_port_or_die`` gave up, so ``proc.communicate()`` came back with
    empty stderr and the match failed. The child now signals readiness on
    stdout only *after* its stderr write is flushed, and the test blocks on
    that line before starting the timed section -- so the 0.3s budget only
    ever has to cover ``_wait_for_port``'s own polling loop (no subprocess
    launch latency in it), immune to the exact machine-load sensitivity this
    issue is about.
    """
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stderr.write('boom: refusing to start\\n'); "
            "sys.stderr.flush(); sys.stdout.write('ready\\n'); sys.stdout.flush(); "
            "time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline() == "ready\n"
        with pytest.raises(TimeoutError, match="boom: refusing to start"):
            _wait_for_port_or_die(proc, port, timeout=0.3)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def _toolchain_free_env() -> dict[str, str]:
    """A bare environment with no venv/``uv``/``PYTHONPATH`` -- proves the frozen
    binary needs no Python toolchain on the target (ADR-0021/0026/0039)."""
    return {"PATH": "/usr/bin:/bin"}


@pytest.fixture(scope="module")
def frozen_proxy_binary(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build ``packaging/blindfold-proxy.spec`` once for this module's tests, then
    pay the onefile bootloader's cold-launch cost once too (issue #428) -- one
    throwaway launch, output discarded, so every real test below it launches
    against an OS page cache already warm for the binary's own bytes."""
    dist_dir = tmp_path_factory.mktemp("pi-dist")
    work_dir = tmp_path_factory.mktemp("pi-build")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--distpath", str(dist_dir),
            "--workpath", str(work_dir),
            "-y",
            str(SPEC_PATH),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    binary = dist_dir / "blindfold-proxy"
    assert binary.exists(), "pyinstaller reported success but produced no binary"

    warmup_port = _free_port()
    warmup_proc = subprocess.Popen(
        [str(binary), "serve", "--port", str(warmup_port)],
        env=_toolchain_free_env(),
        cwd=str(REPO_ROOT.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port_or_die(warmup_proc, warmup_port, timeout=_FROZEN_WARMUP_TIMEOUT)
    finally:
        warmup_proc.terminate()
        warmup_proc.wait(timeout=5)

    return binary


def test_frozen_binary_serves_vendored_ui_dist_shell(frozen_proxy_binary: pathlib.Path) -> None:
    import urllib.request

    port = _free_port()
    proc = subprocess.Popen(
        [str(frozen_proxy_binary), "serve", "--port", str(port)],
        env=_toolchain_free_env(),
        cwd=str(REPO_ROOT.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port_or_die(proc, port, timeout=_FROZEN_STARTUP_TIMEOUT)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ui/", timeout=5) as resp:
            assert resp.status == 200
            shell_html = resp.read().decode("utf-8")
        assert shell_html == (UI_DIST_DIR / "index.html").read_text(encoding="utf-8")

        asset_name = next((UI_DIST_DIR / "assets").glob("*.css")).name
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/ui/assets/{asset_name}", timeout=5
        ) as resp:
            assert resp.status == 200
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_frozen_binary_reaches_local_mapping_cipher_with_store_key_env(
    frozen_proxy_binary: pathlib.Path,
) -> None:
    """issue #234: the hosted windows-latest platform-verify gate's TWO-HOP
    smoke launch (tray spawns the frozen ``blindfold-proxy.exe`` onefile
    binary with ``BLINDFOLD_STORE_KEY`` injected into its launch environment,
    ``StoreKeyEnvironment.Build()``/``RealProxyProcessLauncher``, Program.cs)
    keeps reporting ``mapping_cipher == "none"`` instead of ``"local"`` --
    i.e. the frozen child never sees the key. Prior cycles (620102b, 6342427)
    ruled out every C#/Python seam by inspection and landed on two remaining
    hypotheses, both Windows-CreateProcess-specific and untestable in this
    Linux sandbox.

    This test closes a *third* possibility this sandbox CAN check: that
    PyInstaller's onefile bootloader itself (the ``ONEFILE`` extract-then-
    re-exec-itself dance every platform's onefile build shares, not the
    Windows-specific CreateProcess plumbing around it) drops a freshly
    injected env var before Python ever sees it. Same shape as the existing
    ``test_frozen_binary_refuses_cloud_l3_model_with_scrubbed_stderr`` above
    (a minimal, non-inherited env dict passed straight to ``subprocess.Popen``,
    exactly how the C# launcher hands a fresh dict to ``Process.Start``) --
    swapped to assert successful propagation of ``BLINDFOLD_STORE_KEY``
    through to ``settings.mapping_cipher`` via ``GET /v1/status`` instead of
    a refusal-path env var. A pass here rules out the onefile bootloader
    mechanism generally, narrowing the remaining gap to something
    Windows/CreateProcess-specific -- exactly the two hypotheses
    ProbeEnvironmentPropagation (Program.cs) already targets.
    """
    port = _free_port()
    env = _toolchain_free_env()
    env["BLINDFOLD_STORE_KEY"] = base64.b64encode(os.urandom(32)).decode()
    proc = subprocess.Popen(
        [str(frozen_proxy_binary), "serve", "--port", str(port)],
        env=env,
        cwd=str(REPO_ROOT.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port_or_die(proc, port, timeout=_FROZEN_STARTUP_TIMEOUT)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["config"]["mapping_cipher"] == "local"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_frozen_binary_reaches_local_mapping_cipher_with_ambient_store_key_cleared_after_spawn(
    frozen_proxy_binary: pathlib.Path,
) -> None:
    """issue #234: every sandbox-executable onefile check so far (this file's own
    ``test_frozen_binary_reaches_local_mapping_cipher_with_store_key_env`` included) hands
    ``BLINDFOLD_STORE_KEY`` to the child via an explicit ``env=`` dict passed straight to
    ``subprocess.Popen`` -- the mechanism ``RealProxyProcessLauncher`` used *before* 2fff155.
    The tray's current mechanism is different in two ways at once: it sets the key on its
    *own* ambient environment (``Environment.SetEnvironmentVariable``, never
    ``startInfo.Environment``) so the child inherits via ``lpEnvironment=NULL``, and it
    clears the key from its own environment immediately after ``Process.Start`` returns
    (c691584). No test has combined *both* against a real onefile-built binary -- only
    against plain ``/bin/sh`` (``ProcessEnvironmentPropagationTests``, Blindfold.Core.Tests)
    or an onefile binary via the *old* explicit-dict mechanism (this file). Reproduces both
    at once: ``os.environ`` (this test process's own ambient environment, the POSIX analog of
    the tray's) carries the key, ``subprocess.Popen(..., env=None)`` inherits it ambiently
    (the POSIX analog of ``lpEnvironment=NULL``), and the key is popped from ``os.environ``
    immediately after ``Popen`` returns -- matching ``RealProxyProcessLauncher.Launch``'s
    set-spawn-clear ordering exactly. A pass here would rule out "ambient-inherit +
    immediate-clear, combined, breaks a real onefile child's re-exec" as a *generalizable*
    (not Windows-CreateProcess-specific) mechanism, leaving Windows/CreateProcess and
    blindfold-proxy.exe's own *compiled* (not source) Windows bootloader binary as the only
    remaining untested candidates, exactly where 17569da's own new ONE-HOP (Store key)
    platform-verify.yml assertion is aimed. A failure here would be a new, generalizable
    finding no prior cycle on this issue considered.
    """
    port = _free_port()
    key = base64.b64encode(os.urandom(32)).decode()
    os.environ["BLINDFOLD_STORE_KEY"] = key
    try:
        proc = subprocess.Popen(
            [str(frozen_proxy_binary), "serve", "--port", str(port)],
            env=None,
            cwd=str(REPO_ROOT.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        del os.environ["BLINDFOLD_STORE_KEY"]

    try:
        _wait_for_port_or_die(proc, port, timeout=_FROZEN_STARTUP_TIMEOUT)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["config"]["mapping_cipher"] == "local"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_frozen_binary_reports_the_git_sha_it_was_built_from(
    frozen_proxy_binary: pathlib.Path,
) -> None:
    """Issue #291: a frozen build stamps the git SHA it was frozen from into the
    binary at freeze time (``packaging/blindfold-proxy.spec``), so a live-verify
    preflight can tell a stale binary from the repo's current HEAD without a
    privileged call -- just the existing, unauthenticated ``/v1/status``."""
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()

    port = _free_port()
    proc = subprocess.Popen(
        [str(frozen_proxy_binary), "serve", "--port", str(port)],
        env=_toolchain_free_env(),
        cwd=str(REPO_ROOT.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port_or_die(proc, port, timeout=_FROZEN_STARTUP_TIMEOUT)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["build"]["source"] == "frozen"
        assert payload["build"]["sha"] == expected_sha
        assert "dirty" not in payload["build"]
        assert payload["build"]["path"] == str(frozen_proxy_binary)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_frozen_binary_refuses_cloud_l3_model_with_scrubbed_stderr(
    frozen_proxy_binary: pathlib.Path,
) -> None:
    """The ADR-0022 startup guard (serve.py's ``refuse_if_cloud_model``) survives
    freezing: the child exits non-zero with the scrubbed one-line reason on
    stderr and no raw traceback -- the contract the Refused-state supervisor
    (BlindfoldCore, blocked on issue #181's Swift toolchain) will read."""
    env = _toolchain_free_env()
    env["BLINDFOLD_L3_MODEL"] = "llama3:cloud"
    result = subprocess.run(
        [str(frozen_proxy_binary), "serve", "--port", str(_free_port())],
        env=env,
        cwd=str(REPO_ROOT.parent),
        capture_output=True,
        text=True,
        timeout=_FROZEN_STARTUP_TIMEOUT,
    )
    assert result.returncode == 1
    assert "refusing to run L3 against a remotely-executing model" in result.stderr
    assert "Traceback" not in result.stderr
