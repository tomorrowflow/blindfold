"""Local key cipher: AES-256-GCM + HMAC blind index under one HKDF-derived Store
key (ADR-0045 §2/§3, issue #228).

Leak-audit clause analysis:
- A/B/C/D/E/F — N/A: this file tests the mapping-cipher seam directly, not the
  proxy request path.
- G (mapping secrecy) — covered by design and asserted directly: ciphertext and
  blind index are the only things this module returns; a malformed-key refusal
  message never carries the key material (see test_serve_entrypoint.py).
"""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from blindfold.mapping_cipher import InvalidStoreKeyError, LocalKeyCipher, MappingCipher

_STORE_KEY = base64.b64encode(b"0" * 32).decode()


def _cipher() -> LocalKeyCipher:
    return LocalKeyCipher(_STORE_KEY)


# ---------------------------------------------------------------------------
# 1. Round trip — decrypt(encrypt(v)) == v, for ASCII / non-ASCII / multi-line
# ---------------------------------------------------------------------------


def test_local_cipher_round_trips_an_ascii_value():
    cipher = _cipher()
    assert cipher.decrypt(cipher.encrypt("Martin Bach")) == "Martin Bach"


def test_local_cipher_round_trips_a_non_ascii_value():
    cipher = _cipher()
    value = "Müller Straße 42, München — 日本語"
    assert cipher.decrypt(cipher.encrypt(value)) == value


def test_local_cipher_round_trips_a_multiline_value():
    cipher = _cipher()
    value = "Line one\nLine two\r\nLine three"
    assert cipher.decrypt(cipher.encrypt(value)) == value


# ---------------------------------------------------------------------------
# 2. Fresh nonce per call; blind_index is deterministic and case-sensitive
# ---------------------------------------------------------------------------


def test_local_cipher_encrypting_the_same_value_twice_yields_different_ciphertext():
    cipher = _cipher()
    assert cipher.encrypt("Martin Bach") != cipher.encrypt("Martin Bach")


def test_local_cipher_blind_index_is_deterministic_for_the_same_value():
    cipher = _cipher()
    assert cipher.blind_index("Martin Bach") == cipher.blind_index("Martin Bach")


def test_local_cipher_blind_index_differs_for_values_differing_only_by_case():
    cipher = _cipher()
    assert cipher.blind_index("Martin Bach") != cipher.blind_index("martin bach")


# ---------------------------------------------------------------------------
# 3. bf:v1: scheme-version prefix -- identifiable without attempting decryption,
#    distinguishable from Transit's vault:v1: (ADR-0045 §3)
# ---------------------------------------------------------------------------


def test_local_cipher_ciphertext_carries_the_bf_v1_prefix():
    cipher = _cipher()
    assert cipher.encrypt("Martin Bach").startswith("bf:v1:")


def test_local_cipher_blind_index_carries_the_bf_v1_prefix():
    cipher = _cipher()
    assert cipher.blind_index("Martin Bach").startswith("bf:v1:")


def test_local_ciphertext_is_distinguishable_from_a_transit_ciphertext_by_prefix():
    cipher = _cipher()
    local_ciphertext = cipher.encrypt("Martin Bach")
    transit_ciphertext = "vault:v1:abc123"
    assert local_ciphertext.startswith("bf:v1:")
    assert not transit_ciphertext.startswith("bf:v1:")
    assert not local_ciphertext.startswith("vault:v1:")


# ---------------------------------------------------------------------------
# 4. table+column additional authenticated data -- a ciphertext relocated to a
#    different column fails authentication rather than decrypting (ADR-0045 §3)
# ---------------------------------------------------------------------------


def test_local_cipher_decrypts_with_the_matching_column_context():
    cipher = _cipher()
    ciphertext = cipher.encrypt("Martin Bach", context="persons:canonical_name")
    assert cipher.decrypt(ciphertext, context="persons:canonical_name") == "Martin Bach"


def test_local_cipher_relocated_ciphertext_fails_authentication():
    cipher = _cipher()
    ciphertext = cipher.encrypt("Martin Bach", context="persons:canonical_name")
    with pytest.raises(InvalidTag):
        cipher.decrypt(ciphertext, context="terms:canonical_name")


# ---------------------------------------------------------------------------
# 5. A malformed, wrong-length or non-base64 Store key is a named refusal,
#    never a silent fallback (ADR-0045 §3)
# ---------------------------------------------------------------------------


def test_local_cipher_rejects_non_base64_store_key():
    with pytest.raises(InvalidStoreKeyError):
        LocalKeyCipher("not-valid-base64!!!")


def test_local_cipher_rejects_a_wrong_length_store_key():
    short_key = base64.b64encode(b"too-short").decode()
    with pytest.raises(InvalidStoreKeyError):
        LocalKeyCipher(short_key)


def test_local_cipher_rejects_an_empty_store_key():
    with pytest.raises(InvalidStoreKeyError):
        LocalKeyCipher("")


# ---------------------------------------------------------------------------
# 6. LocalKeyCipher satisfies the MappingCipher seam (ADR-0045 §2, issue #228)
# ---------------------------------------------------------------------------


def test_local_cipher_satisfies_the_mapping_cipher_protocol():
    assert isinstance(_cipher(), MappingCipher)
