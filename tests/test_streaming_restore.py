"""Sliding-window streaming restore (ADR-0006, issue #6).

The streaming restorer holds back a tail buffer at least as long as the longest known
surrogate, so a surrogate split across stream chunks is restored before emission —
preserving the streaming UX while still returning real values to the client.

Closed-world (ADR-0006): only surrogates injected for this exchange are reversed; a
coincidental surrogate-shaped token the provider emitted is left untouched.
"""

from blindfold.engine import ExchangeSession, StreamingRestorer


def _session_with(injected: dict[str, str]) -> ExchangeSession:
    session = ExchangeSession()
    for surrogate, real in injected.items():
        session.record(surrogate, real)
    return session


def test_streaming_restore_reassembles_a_surrogate_split_across_two_chunks():
    # Injected this exchange: surrogate "Berta Vogel" -> real "Anna Schmidt".
    session = _session_with({"Berta Vogel": "Anna Schmidt"})
    restorer = StreamingRestorer(session)

    # Upstream emits the surrogate split across two stream chunks. A naive
    # chunk-at-a-time replace would emit "Bert" un-restored and leak the surrogate.
    emitted: list[str] = []
    emitted.append(restorer.feed("Hello Bert"))
    emitted.append(restorer.feed("a Vogel, welcome."))
    emitted.append(restorer.flush())

    joined = "".join(emitted)
    assert joined == "Hello Anna Schmidt, welcome."
    # And the half-surrogate prefix was never emitted before the rest arrived.
    assert "Bert" not in emitted[0]


def test_streaming_restore_is_closed_world_for_coincidental_lookalikes():
    # Only "Berta Vogel" was injected this exchange. A surrogate-shaped token the
    # provider emits on its own ("Tobias Lehmann") must NOT be restored.
    session = _session_with({"Berta Vogel": "Anna Schmidt"})
    restorer = StreamingRestorer(session)

    out = restorer.feed("Co-author: Tobias Lehm")
    out += restorer.feed("ann replied.")
    out += restorer.flush()

    assert "Tobias Lehmann" in out
    assert "Markus Wagner" not in out  # real value never appears
