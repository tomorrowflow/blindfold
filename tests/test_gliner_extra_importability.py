"""GLiNER cascade extra-importability check (ADR-0049 #421 amendment, issue #429).

Root cause: `is_gliner_model_ready` (gliner_provisioning.py) is a purely on-disk
predicate -- it says nothing about whether the `gliner`/`onnxruntime` packages
(the `blindfold[gliner]` extra) are actually importable. A model directory
provisioned by an earlier source run, paired with the frozen `.app`'s deliberately
extra-less bundle (ADR-0034 §6), produced a live /v1/status that reported the L3
dependency healthy while every request 503'd with `detection_internal` (the extra's
import is deferred to adjudication time, `l3_gliner._load_gliner_model`).

This file covers the shared seam the startup guard, the /v1/status probe and the
detection/settings view all now consult:
:func:`~blindfold.l3_gliner.is_gliner_extra_importable`.

Leak-audit: N/A -- a boolean import-check and a static remedy string, no
request-path/egress involvement, no real-entity value anywhere in this file.
"""

from __future__ import annotations

import sys
import types

from blindfold import build_info, l3_gliner
from blindfold.l3_gliner import gliner_extra_missing_message, is_gliner_extra_importable


def test_is_gliner_extra_importable_is_false_when_gliner_is_absent(block_import):
    # Forces the absence explicitly (issue #284's block_import convention) so this
    # holds whether or not `blindfold[gliner]` happens to be installed in the
    # environment running the suite.
    block_import("gliner")

    assert is_gliner_extra_importable() is False


def test_is_gliner_extra_importable_is_false_when_onnxruntime_is_absent(
    block_import, monkeypatch
):
    # gliner itself importable, but its onnxruntime dependency is not -- both halves
    # of the extra (ADR-0034 §6) must be importable, not just the top-level package.
    monkeypatch.setitem(sys.modules, "gliner", types.ModuleType("gliner"))
    block_import("onnxruntime")

    assert is_gliner_extra_importable() is False


def test_is_gliner_extra_importable_is_true_when_both_packages_are_present(monkeypatch):
    # Inserted directly into sys.modules (mirrors test_l3_gliner_cascade.py's own
    # fake-module convention) rather than requiring the real ~197MB extra installed
    # in this sandbox.
    monkeypatch.setitem(sys.modules, "gliner", types.ModuleType("gliner"))
    monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))

    assert is_gliner_extra_importable() is True


def test_is_gliner_extra_importable_does_not_import_the_real_package(monkeypatch):
    # The brief's own constraint: this runs on the /v1/status ~5s polling cadence,
    # so it must be a location check (importlib.util.find_spec), never a real
    # `import gliner` -- that would eagerly load onnxruntime's native extension on
    # every poll. Pinning this by making a real import raise, so the assertion above
    # would fail loudly if this ever regresses into an eager import.
    import builtins

    real_import = builtins.__import__

    def _tripwire(name, *args, **kwargs):
        if name in ("gliner", "onnxruntime"):
            raise AssertionError(f"is_gliner_extra_importable must not import {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _tripwire)

    is_gliner_extra_importable()


def test_gliner_extra_missing_message_names_the_extra_to_install_for_a_source_run(
    monkeypatch,
):
    monkeypatch.setattr(
        l3_gliner,
        "get_build_identity",
        lambda: build_info.BuildIdentity(
            sha="deadbeef", dirty=False, source=build_info.SOURCE_SOURCE, path=None
        ),
    )

    message = gliner_extra_missing_message()

    assert "uv pip install" in message
    assert "blindfold[gliner]" in message


def test_gliner_extra_missing_message_is_frozen_aware_and_never_says_uv_pip_install(
    monkeypatch,
):
    # ADR-0049 #421 amendment: a frozen PyInstaller build deliberately does not
    # bundle the extra (rejected, with reasons) -- `uv pip install` can never work
    # against it, so the frozen remedy must name a working alternative instead.
    monkeypatch.setattr(
        l3_gliner,
        "get_build_identity",
        lambda: build_info.BuildIdentity(
            sha="deadbeef", dirty=None, source=build_info.SOURCE_FROZEN, path="/app/blindfold-proxy"
        ),
    )

    message = gliner_extra_missing_message()

    assert "uv pip install" not in message
    assert "pip install" not in message
    assert "omlx" in message.lower()
