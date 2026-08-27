"""Test connection (issue #265) -- pure classification of the loopback exchange's own
HTTP response into one of the typed failure-taxonomy codes (Q4 of the trusted
maintainer comment). No network, no app, no store -- exercises
``blindfold.test_connection.classify_response`` directly against hand-built response
shapes mirroring what ``blindfold.app`` actually returns on each path.

Leak-audit clauses: N/A -- pure string/dict classification, no request-path code
touched, no real value anywhere in these fixtures (every "canary" here is the
already-defined non-colliding constant, never a real referent).
"""

import pytest

from blindfold.test_connection import (
    CODE_FAIL_CLOSED_BLOCK,
    CODE_LEAK_FLAGGED,
    CODE_UPSTREAM_AUTH_REJECTED,
    CODE_UPSTREAM_UNREACHABLE,
    CODE_WRONG_ENDPOINT,
    classify_response,
    is_loopback_base_url,
)


def test_a_blindfold_blocked_body_with_l3_unavailable_classifies_as_fail_closed_block():
    body = {
        "error": {
            "type": "blindfold_blocked",
            "code": "blindfold_fail_closed",
            "sub_reason": "l3_unavailable",
            "message": "Blindfold blocked this request: ...",
            "reason": "l3 unavailable for workspace default",
            "remedy": "...",
            "management_url": "http://127.0.0.1:25463/ui/status",
            "workspace": "default",
        }
    }

    verdict = classify_response(503, body)

    assert verdict is not None
    assert verdict.code == CODE_FAIL_CLOSED_BLOCK
    assert verdict.ref == "l3 unavailable for workspace default"


def test_a_blindfold_blocked_body_with_leak_detected_classifies_as_leak_flagged():
    body = {
        "error": {
            "type": "blindfold_blocked",
            "code": "blindfold_fail_closed",
            "sub_reason": "leak_detected",
            "message": "Blindfold blocked this request: ...",
            "reason": "known real value SURR-1234 present at egress",
            "remedy": "...",
            "management_url": "http://127.0.0.1:25463/ui/status",
            "workspace": "default",
        }
    }

    verdict = classify_response(503, body)

    assert verdict is not None
    assert verdict.code == CODE_LEAK_FLAGGED
    assert verdict.ref == "known real value SURR-1234 present at egress"


def test_a_blindfold_blocked_body_with_unresolved_surrogate_classifies_as_leak_flagged():
    body = {
        "error": {
            "type": "blindfold_blocked",
            "code": "blindfold_fail_closed",
            "sub_reason": "unresolved_surrogate",
            "message": "...",
            "reason": "surrogate SURR-5678 left unresolved",
            "remedy": "...",
            "management_url": "http://127.0.0.1:25463/ui/status",
            "workspace": "default",
        }
    }

    verdict = classify_response(503, body)

    assert verdict is not None
    assert verdict.code == CODE_LEAK_FLAGGED


def test_an_upstream_error_body_reporting_http_401_classifies_as_upstream_auth_rejected():
    body = {
        "error": {
            "type": "blindfold_upstream_error",
            "code": "blindfold_upstream_error",
            "sub_reason": "upstream_http_error",
            "message": "upstream returned HTTP 401",
            "workspace": "default",
        }
    }

    verdict = classify_response(502, body)

    assert verdict is not None
    assert verdict.code == CODE_UPSTREAM_AUTH_REJECTED


def test_an_upstream_error_body_reporting_http_500_classifies_as_upstream_unreachable():
    body = {
        "error": {
            "type": "blindfold_upstream_error",
            "code": "blindfold_upstream_error",
            "sub_reason": "upstream_http_error",
            "message": "upstream returned HTTP 500",
            "workspace": "default",
        }
    }

    verdict = classify_response(502, body)

    assert verdict is not None
    assert verdict.code == CODE_UPSTREAM_UNREACHABLE


def test_an_upstream_timeout_body_classifies_as_upstream_unreachable():
    body = {
        "error": {
            "type": "blindfold_upstream_error",
            "code": "blindfold_upstream_error",
            "sub_reason": "upstream_timeout",
            "message": "upstream did not respond in time",
            "workspace": "default",
        }
    }

    verdict = classify_response(504, body)

    assert verdict is not None
    assert verdict.code == CODE_UPSTREAM_UNREACHABLE


def test_a_response_body_that_is_not_json_classifies_as_wrong_endpoint():
    verdict = classify_response(200, None)

    assert verdict is not None
    assert verdict.code == CODE_WRONG_ENDPOINT


def test_a_json_body_with_neither_blindfold_error_nor_message_content_shape_is_wrong_endpoint():
    # Some other HTTP server answering on this host:port with an unrelated JSON body.
    verdict = classify_response(200, {"ok": True})

    assert verdict is not None
    assert verdict.code == CODE_WRONG_ENDPOINT


def test_a_recognizable_blindfold_success_body_returns_no_verdict_yet():
    # 200 + an Anthropic Messages-shaped body -- classify_response's job stops here;
    # the caller still has to trace-assert egress and check the restore echo.
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "m",
        "stop_reason": "end_turn",
    }

    verdict = classify_response(200, body)

    assert verdict is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:25463",
        "http://localhost:25463",
        "http://LOCALHOST:25463",
        "http://[::1]:25463",
    ],
)
def test_a_loopback_base_url_is_accepted(base_url):
    assert is_loopback_base_url(base_url) is True


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.invalid",
        "http://10.0.0.5:25463",
        "http://attacker.example.com",
        "not a url at all",
        "",
    ],
)
def test_a_non_loopback_base_url_is_rejected(base_url):
    assert is_loopback_base_url(base_url) is False
