"""`blindfold serve` CLI dispatch (issue #44, UX-2/SEC-11/SEC-2).

Leak-audit clause analysis: N/A this slice — process entry point / argv parsing only,
no request-path change.
"""

from __future__ import annotations

from blindfold.__main__ import main
from blindfold.serve import DEFAULT_HOST, DEFAULT_PORT, DevModeRequiredError


def test_main_serve_defaults_to_loopback_host_and_port(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "blindfold.__main__.run_server",
        lambda **kwargs: calls.append(kwargs),
    )

    exit_code = main(["serve"])

    assert exit_code == 0
    assert calls == [{"host": DEFAULT_HOST, "port": DEFAULT_PORT}]


def test_main_serve_passes_through_explicit_host_and_port(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "blindfold.__main__.run_server",
        lambda **kwargs: calls.append(kwargs),
    )

    exit_code = main(["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert exit_code == 0
    assert calls == [{"host": "0.0.0.0", "port": 9000}]


def test_main_serve_reports_dev_mode_required_error_and_exits_nonzero(monkeypatch, capsys):
    def _raise(**kwargs):
        raise DevModeRequiredError("refusing to start against a root OpenBao Transit token")

    monkeypatch.setattr("blindfold.__main__.run_server", _raise)

    exit_code = main(["serve"])

    assert exit_code == 1
    assert "refusing to start against a root OpenBao Transit token" in capsys.readouterr().err
