"""Sliding-window streaming restore (ADR-0006, issue #6).

The streaming restorer holds back a tail buffer at least as long as the longest known
surrogate, so a surrogate split across stream chunks is restored before emission —
preserving the streaming UX while still returning real values to the client.

Closed-world (ADR-0006): only surrogates injected for this exchange are reversed; a
coincidental surrogate-shaped token the provider emitted is left untouched.
"""

import pytest

from blindfold.engine import ExchangeSession, StreamingRestorer, _restore_text


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


# --- issue #457 (re-file of #441): streaming == whole-text with several guarded matches ---

_PARITY_SESSION = {"Carla Distel": "Sarah Bergmann"}


def _stream(text: str, chunk_size: int, injected: dict[str, str] = _PARITY_SESSION) -> str:
    restorer = StreamingRestorer(_session_with(injected))
    out = [restorer.feed(text[i : i + chunk_size]) for i in range(0, len(text), chunk_size)]
    out.append(restorer.flush())
    return "".join(out)


@pytest.mark.parametrize(
    "text",
    [
        "Carla Carla Fischer",
        "Heute sah ich Carla Carla Fischer und Carla Carla Fischer; danach kam Carla.",
        "Carla Carla Müller-Lüdenscheidt-Hohenzollern und seine ganze Familie kam mit.",
    ],
    ids=["repeated-first-name", "repeated-with-trailing-bare", "repeated-long-compound"],
)
@pytest.mark.parametrize("chunk_size", range(1, 41))
def test_streaming_restore_matches_whole_text_restore_with_several_guarded_first_names(
    text, chunk_size
):
    # issue #457: the cut is a fixpoint -- it never lands inside a guarded match's
    # decision window and every guarded match before the cut is checked -- so the
    # joined stream equals _restore_text at every chunk size.
    session = _session_with(_PARITY_SESSION)

    assert _stream(text, chunk_size) == _restore_text(text, session)


_PARITY_FRAGMENTS = (
    "Carla",  # bare surrogate first name
    "Carla",
    "Distel",  # the surrogate's own surname
    "Fischer",  # other surnames
    "Weber",
    "Müller-Lüdenscheidt-Hohenzollern",
    "Carlas",  # ADR-0024 suffix forms
    "Carla's",
    "Distels",
    "Distel's",
    "Carla Distel",
    "Carla Distels",
    "Carla Distel's",
    "und",
    "sagte",
    "Berta Vogel",
    "Berta",
    "Vogel",
    "Berta Vogelen",
    "Teststraße 5",
)
_PARITY_INJECTED = {"Carla Distel": "Sarah Bergmann", "Berta Vogel": "Anna Schmidt"}


def _generated_parity_text(rng) -> str:
    pieces: list[str] = []
    for _ in range(rng.randint(1, 14)):
        pieces.append(rng.choice(_PARITY_FRAGMENTS))
        pieces.append(rng.choice((" ", " ", " ", ", ", ". ", "  ", "\n")))
    return "".join(pieces).rstrip(" ") if rng.random() < 0.5 else "".join(pieces)


def test_streaming_restore_matches_whole_text_restore_over_generated_texts():
    # issue #457: property-style parity over generated mixes of bare first names, the
    # own surname, other surnames and ADR-0024 suffix forms, at several chunk sizes
    # each -- the streamed output must equal _restore_text on the whole text.
    import random

    rng = random.Random(457)
    session = _session_with(_PARITY_INJECTED)
    for _ in range(400):
        text = _generated_parity_text(rng)
        expected = _restore_text(text, session)
        for chunk_size in (1, 2, 3, 5, 7, 11, 16, 25, 40, rng.randint(1, 60)):
            assert _stream(text, chunk_size, _PARITY_INJECTED) == expected, (
                text,
                chunk_size,
            )


def test_first_name_guard_does_not_withhold_a_full_single_word_surrogate():
    # issue #457 (quality): "Carla" is itself a full single-word surrogate here, so
    # restoring it is exact -- the bare-first-name guard (for the *component* of
    # "Carla Distel") must not withhold it before a different surname.
    injected = {"Carla Distel": "Sarah Bergmann", "Carla": "Anna"}
    text = "Carla Fischer kam."

    assert _restore_text(text, _session_with(injected)) == "Anna Fischer kam."
    for chunk_size in (1, 3, 7, 40):
        assert _stream(text, chunk_size, injected) == "Anna Fischer kam."


@pytest.mark.parametrize("token", ["Distel", "Distels", "Distel's", "Distelen", "Distel'"])
def test_first_name_guard_treats_own_surname_plus_adr_0024_suffix_as_own_surname(token):
    from blindfold.engine import _blocked_by_a_different_surname

    text = f"Carla {token} kam."

    assert _blocked_by_a_different_surname(text, len("Carla"), "Distel") is False
    assert _blocked_by_a_different_surname("Carla Distelx kam.", len("Carla"), "Distel") is True
