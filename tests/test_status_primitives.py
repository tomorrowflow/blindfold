"""Unit tests for the GET /v1/status primitives (issue #92).

These are the small seams `/v1/status` (app.py) composes: `DependencyHealth`
(scrubbed-by-construction dependency shape), `compute_state` (the single
Protected/Degraded rule), `CachedHealthProbe` (TTL collapse so polling never
becomes a probe storm), and `BlockHistory` (the rolling window backing
`blocks.recent`, sharing the #91 scrubbed-reason + management_url shape).

Leak-audit: N/A for this file -- pure data/window-management primitives, no
request path, no entity content. The leak-audit-style assertion for this slice
lives in test_status_endpoint.py (blocks.recent never carries entity plaintext).
"""

from __future__ import annotations

from blindfold.status import (
    BlockHistory,
    CachedHealthProbe,
    DependencyHealth,
    RecentFailureHealth,
    compute_state,
)


def test_dependency_health_to_dict_omits_detail_when_none():
    health = DependencyHealth(healthy=True)
    assert health.to_dict() == {"healthy": True}


def test_dependency_health_to_dict_includes_detail_when_set():
    health = DependencyHealth(healthy=False, detail="ollama unreachable")
    assert health.to_dict() == {"healthy": False, "detail": "ollama unreachable"}


def test_dependency_health_to_dict_includes_latency_ms_when_set():
    health = DependencyHealth(healthy=True, latency_ms=12.3)
    assert health.to_dict() == {"healthy": True, "latency_ms": 12.3}


def test_dependency_health_to_dict_omits_latency_ms_when_none():
    health = DependencyHealth(healthy=True)
    assert health.to_dict() == {"healthy": True}


def test_compute_state_is_protected_when_every_dependency_is_healthy():
    dependencies = {
        "upstream": DependencyHealth(healthy=True),
        "l3": DependencyHealth(healthy=True),
        "transit": DependencyHealth(healthy=True),
        "store": DependencyHealth(healthy=True),
    }
    assert compute_state(dependencies) == "protected"


def test_compute_state_is_degraded_when_any_single_dependency_is_unhealthy():
    dependencies = {
        "upstream": DependencyHealth(healthy=True),
        "l3": DependencyHealth(healthy=False, detail="ollama unreachable"),
        "transit": DependencyHealth(healthy=True),
        "store": DependencyHealth(healthy=True),
    }
    assert compute_state(dependencies) == "degraded"


def test_cached_health_probe_collapses_repeated_checks_within_the_ttl():
    calls = []
    clock = {"now": 0.0}

    def probe() -> DependencyHealth:
        calls.append(1)
        return DependencyHealth(healthy=True)

    cached = CachedHealthProbe(probe, ttl_seconds=5.0, clock=lambda: clock["now"])
    cached.check()
    clock["now"] = 2.0
    cached.check()
    clock["now"] = 4.9
    cached.check()

    assert len(calls) == 1


def test_cached_health_probe_measures_latency_of_a_fresh_probe_call():
    clock = {"now": 0.0}

    def probe() -> DependencyHealth:
        # Simulate the probe itself taking 250ms of wall-clock time.
        clock["now"] = 0.25
        return DependencyHealth(healthy=True)

    cached = CachedHealthProbe(probe, ttl_seconds=5.0, clock=lambda: clock["now"])
    result = cached.check()

    assert result.latency_ms == 250.0


def test_cached_health_probe_returns_the_measured_latency_on_a_cache_hit_too():
    clock = {"now": 0.0}

    def probe() -> DependencyHealth:
        clock["now"] = 0.1
        return DependencyHealth(healthy=True)

    cached = CachedHealthProbe(probe, ttl_seconds=5.0, clock=lambda: clock["now"])
    first = cached.check()
    clock["now"] = 2.0
    cached_hit = cached.check()

    assert cached_hit.latency_ms == first.latency_ms == 100.0


def test_cached_health_probe_never_overwrites_a_latency_the_probe_already_set():
    def probe() -> DependencyHealth:
        return DependencyHealth(healthy=True, latency_ms=42.0)

    cached = CachedHealthProbe(probe, ttl_seconds=5.0, clock=lambda: 0.0)
    assert cached.check().latency_ms == 42.0


def test_cached_health_probe_re_probes_once_the_ttl_has_elapsed():
    calls = []
    clock = {"now": 0.0}

    def probe() -> DependencyHealth:
        calls.append(1)
        return DependencyHealth(healthy=True)

    cached = CachedHealthProbe(probe, ttl_seconds=5.0, clock=lambda: clock["now"])
    cached.check()
    clock["now"] = 5.1
    cached.check()

    assert len(calls) == 2


def test_recent_failure_health_is_healthy_before_any_failure_observed():
    health = RecentFailureHealth(unhealthy_window_seconds=60.0)
    assert health.check() == DependencyHealth(healthy=True)


def test_recent_failure_health_is_unhealthy_within_the_window_after_a_failure():
    clock = {"now": 0.0}
    health = RecentFailureHealth(unhealthy_window_seconds=60.0, clock=lambda: clock["now"])
    health.mark_unhealthy("upstream_unreachable")
    clock["now"] = 30.0
    assert health.check() == DependencyHealth(healthy=False, detail="upstream_unreachable")


def test_recent_failure_health_recovers_once_the_window_elapses():
    clock = {"now": 0.0}
    health = RecentFailureHealth(unhealthy_window_seconds=60.0, clock=lambda: clock["now"])
    health.mark_unhealthy("upstream_unreachable")
    clock["now"] = 60.1
    assert health.check() == DependencyHealth(healthy=True)


def test_block_history_recent_returns_recorded_block_verbatim():
    clock = {"now": 0.0}
    history = BlockHistory(window_minutes=15, clock=lambda: clock["now"], now_iso=lambda: "2026-07-11T00:00:00+00:00")
    history.record(
        sub_reason="l3_unavailable",
        scrubbed_reason="hash:abc123",
        management_url="http://127.0.0.1:25463/ui/status",
    )
    recent = history.recent()
    assert len(recent) == 1
    assert recent[0].to_dict() == {
        "ts": "2026-07-11T00:00:00+00:00",
        "sub_reason": "l3_unavailable",
        "scrubbed_reason": "hash:abc123",
        "management_url": "http://127.0.0.1:25463/ui/status",
    }


def test_block_history_prunes_entries_older_than_the_window():
    clock = {"now": 0.0}
    history = BlockHistory(window_minutes=15, clock=lambda: clock["now"])
    history.record("l3_unavailable", "hash:abc123", "http://127.0.0.1:25463/ui/status")
    clock["now"] = 15 * 60 + 1
    assert history.recent() == []


def test_block_history_window_minutes_is_exposed_for_the_response_body():
    history = BlockHistory(window_minutes=15)
    assert history.window_minutes == 15
