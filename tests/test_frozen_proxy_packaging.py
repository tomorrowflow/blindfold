"""Frozen-proxy contract (ADR-0039, issue #184): the PyInstaller onefile spec.

Builds the proxy via ``packaging/blindfold-proxy.spec`` into a real,
self-contained binary and smoke-tests it directly -- with no venv/``uv``/
``PYTHONPATH`` on its ``PATH`` -- proving the ``.app ⊃ frozen-proxy ⊃
ui_dist`` layering ADR-0039 calls for. The signed macOS binary itself is
produced on the self-hosted runner (issue #182, not yet online); this
in-sandbox Linux build of the *same* spec is how this slice proves the spec
+ wiring, per the issue's own scope carve-out ("the mac binary is produced
on the runner; the spec + wiring are what this slice owns").

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

import pathlib
import socket
import subprocess
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
SPEC_PATH = REPO_ROOT / "packaging" / "blindfold-proxy.spec"
UI_DIST_DIR = REPO_ROOT / "src" / "blindfold" / "ui_dist"


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


@pytest.fixture(scope="module")
def frozen_proxy_binary(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build ``packaging/blindfold-proxy.spec`` once for this module's tests."""
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
    return binary


def _toolchain_free_env() -> dict[str, str]:
    """A bare environment with no venv/``uv``/``PYTHONPATH`` -- proves the frozen
    binary needs no Python toolchain on the target (ADR-0021/0026/0039)."""
    return {"PATH": "/usr/bin:/bin"}


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
        _wait_for_port(port)
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
        timeout=10,
    )
    assert result.returncode == 1
    assert "refusing to run L3 against a remotely-executing model" in result.stderr
    assert "Traceback" not in result.stderr
