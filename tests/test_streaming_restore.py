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


def test_streaming_restore_transfers_a_closed_set_suffix_split_across_chunks():
    # ADR-0024: the suffix can itself straddle a chunk boundary; the sliding window
    # must hold back enough tail to see it before deciding there's no suffix at all.
    session = _session_with({"Müller": "Weber"})
    restorer = StreamingRestorer(session)

    emitted: list[str] = []
    emitted.append(restorer.feed("Report by Müll"))
    emitted.append(restorer.feed("ers, filed today."))
    emitted.append(restorer.flush())

    joined = "".join(emitted)
    assert joined == "Report by Webers, filed today."


def test_streaming_restore_holds_back_enough_tail_for_a_suffix_split_mid_suffix():
    # ADR-0024: the chunk boundary lands *inside* the two-character "en" suffix
    # itself (after the bare surrogate, before the "n" arrives). A tail sized to only
    # the surrogate's own length would "confirm" and resolve this occurrence one
    # character too early, conclude there's no suffix, and leak the bare surrogate
    # unrestored in this chunk's emitted output.
    session = _session_with({"Müller": "Weber"})
    restorer = StreamingRestorer(session)

    emitted: list[str] = []
    emitted.append(restorer.feed("Report by Müllere"))
    emitted.append(restorer.feed("n, filed today."))
    emitted.append(restorer.flush())

    joined = "".join(emitted)
    assert joined == "Report by Weberen, filed today."
    assert "Müller" not in joined


def test_streaming_restore_leaves_a_sub_token_containment_untouched_across_chunks():
    # ADR-0024 / DESIGN.md Top Risk #2: "Müller" is a sub-token of the unrelated word
    # "Müllerei" — must stay untouched even when the word arrives split across chunks.
    session = _session_with({"Müller": "Weber"})
    restorer = StreamingRestorer(session)

    emitted: list[str] = []
    emitted.append(restorer.feed("Die Müll"))
    emitted.append(restorer.feed("erei war geschlossen."))
    emitted.append(restorer.flush())

    joined = "".join(emitted)
    assert joined == "Die Müllerei war geschlossen."


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
