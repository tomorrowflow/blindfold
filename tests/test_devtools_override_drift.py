"""``check_override_targets`` (ADR-0047 §4, issue #254): devtools resolves its
override targets (``get_upstream_client``, ``get_mapping``, ``get_l3_detector``)
at startup and fails loudly if any is missing or has changed shape. A capture
that silently omits the surrogate table is worse than no capture, because the
reader would conclude the exchange was clean.
"""

import types

from blindfold_devtools.override_targets import (
    OverrideDriftError,
    check_override_targets,
)


def _fake_app_module(**overrides) -> types.ModuleType:
    module = types.ModuleType("fake_blindfold_app")
    module.get_upstream_client = lambda: None
    module.get_mapping = lambda: None
    module.get_l3_detector = lambda: None
    for name, value in overrides.items():
        setattr(module, name, value)
    return module


def test_a_renamed_or_removed_target_refuses_naming_it():
    module = _fake_app_module()
    del module.get_upstream_client

    try:
        check_override_targets(module)
        raised = False
    except OverrideDriftError as exc:
        raised = True
        message = str(exc)

    assert raised
    assert "get_upstream_client" in message


def test_every_present_unchanged_target_is_a_no_op():
    module = _fake_app_module()

    check_override_targets(module)  # must not raise


def test_a_target_that_now_requires_an_argument_refuses_naming_it():
    module = _fake_app_module(get_mapping=lambda workspace: None)

    try:
        check_override_targets(module)
        raised = False
    except OverrideDriftError as exc:
        raised = True
        message = str(exc)

    assert raised
    assert "get_mapping" in message


def test_the_real_blindfold_app_module_passes_today():
    import blindfold.app

    check_override_targets(blindfold.app)  # must not raise
