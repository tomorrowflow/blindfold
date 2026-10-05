"""Sliding-window streaming restore (ADR-0006, issue #6).

The streaming restorer holds back a tail buffer at least as long as the longest known
surrogate, so a surrogate split across stream chunks is restored before emission —
preserving the streaming UX while still returning real values to the client.

Closed-world (ADR-0006): only surrogates injected for this exchange are reversed; a
coincidental surrogate-shaped token the provider emitted is left untouched.
"""

import pytest

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


def test_streaming_restore_reassembles_a_surrogate_component_split_across_chunks():
    # ADR-0036: a surrogate COMPONENT ("Carla" from injected "Carla Distel"),
    # not just the full surrogate, can itself straddle a chunk boundary. The
    # straddle-detection logic must also account for derived component keys —
    # otherwise the split component is truncated and permanently lost from the
    # buffer (emitted un-restored) before the rest of it arrives.
    session = _session_with({"Carla Distel": "Sarah Bergmann"})
    restorer = StreamingRestorer(session)

    emitted: list[str] = []
    emitted.append(restorer.feed("Well then, Car"))
    emitted.append(restorer.feed("la is here!!!"))
    emitted.append(restorer.flush())

    joined = "".join(emitted)
    assert joined == "Well then, Sarah is here!!!"
    assert "Carla" not in joined


@pytest.mark.parametrize("chunk_size", range(1, 16))
def test_streaming_restore_withholds_a_bare_first_name_followed_by_a_different_surname(
    chunk_size,
):
    # issue #441 cycle 2 regression: the non-streaming guard (#441's own fix)
    # withholds "Carla Fischer" (a stranger sharing the seeded person's
    # surrogate first name) from restoring to "Sarah Fischer" -- but
    # StreamingRestorer._restore_prefix could cut the safe prefix right after
    # the bare first-name match, before the following surname had fully
    # arrived in the buffer. At that instant the guard saw no following token
    # at all and let the match straight through, splicing the real referent's
    # first name onto the stranger's surname one chunk early. Every chunk size
    # must reproduce the non-streaming (whole-text) outcome: unchanged text.
    session = _session_with({"Carla Distel": "Sarah Bergmann"})
    restorer = StreamingRestorer(session)
    text = (
        "Die Physikerin Carla Fischer ist eine bekannte Forscherin aus "
        "Berlin und lehrt dort."
    )

    out = []
    for i in range(0, len(text), chunk_size):
        out.append(restorer.feed(text[i : i + chunk_size]))
    out.append(restorer.flush())
    joined = "".join(out)

    assert joined == text
    assert "Sarah Fischer" not in joined


@pytest.mark.parametrize("chunk_size", range(1, 16))
def test_streaming_restore_still_restores_the_referent_despite_the_441_guard(
    chunk_size,
):
    # The streaming hold-back introduced for the test above must not regress
    # the legitimate case it protects: a bare first name with no following
    # surname at all is still the surrogate's own referent and must still
    # restore, at every chunk size.
    session = _session_with({"Carla Distel": "Sarah Bergmann"})
    restorer = StreamingRestorer(session)
    text = "Carla called the office this morning to say she'd be late."

    out = []
    for i in range(0, len(text), chunk_size):
        out.append(restorer.feed(text[i : i + chunk_size]))
    out.append(restorer.flush())
    joined = "".join(out)

    assert joined == "Sarah called the office this morning to say she'd be late."


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
