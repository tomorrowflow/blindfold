"""The **mapping cipher** seam (ADR-0045 §2/§3, issue #227/#228).

Two implementations satisfy :class:`MappingCipher`: the **Transit cipher**
(``transit.TransitClient``, unchanged) and the **Local key cipher**
(:class:`LocalKeyCipher`, this module) -- the seam is store/app code's entire
touch surface on either, so callers never branch on which cipher is active.

The Local key cipher is keyed by one **Store key** (32 random bytes, base64,
``BLINDFOLD_STORE_KEY``) with no server anywhere (ADR-0045 §1). Vetted
primitives only: AES-256-GCM for values, HMAC-SHA256 for the blind index, from
``cryptography``. Two subkeys are HKDF-derived from the one root Store key
with distinct info strings, so domain separation is cryptographic rather than
administrative -- the root key itself is never used directly.
"""

from __future__ import annotations

import base64
import binascii
import os
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PREFIX = "bf:v1:"
_STORE_KEY_LENGTH_BYTES = 32
_NONCE_LENGTH_BYTES = 12  # 96 bits (ADR-0045 §3)
_HKDF_INFO_ENCRYPTION = b"blindfold-mapping-cipher-encryption-v1"
_HKDF_INFO_BLIND_INDEX = b"blindfold-mapping-cipher-blind-index-v1"


@runtime_checkable
class MappingCipher(Protocol):
    """The three-method seam store/app code touch (ADR-0045 §2).

    ``TransitClient`` satisfies this unchanged -- ``encrypt``/``decrypt`` take
    only the value itself there, so the ``context`` parameter below is
    keyword-only with a default every existing call site already satisfies.
    """

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...

    def blind_index(self, value: str) -> str: ...


class InvalidStoreKeyError(ValueError):
    """Raised when a Store key is malformed, non-base64, or the wrong length.

    Never carries the offending key material -- the message names only the
    problem (missing, wrong length, not base64), never the value passed in.
    """


def _decode_store_key(store_key_b64: str) -> bytes:
    try:
        raw = base64.b64decode(store_key_b64, validate=True)
    except binascii.Error as exc:
        raise InvalidStoreKeyError(
            "BLINDFOLD_STORE_KEY is not valid base64"
        ) from exc
    if len(raw) != _STORE_KEY_LENGTH_BYTES:
        raise InvalidStoreKeyError(
            f"BLINDFOLD_STORE_KEY must decode to {_STORE_KEY_LENGTH_BYTES} bytes "
            f"(got {len(raw)})"
        )
    return raw


def _hkdf(root_key: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_STORE_KEY_LENGTH_BYTES,
        salt=None,
        info=info,
    ).derive(root_key)


class LocalKeyCipher:
    """The Local key cipher: AES-256-GCM + HMAC-SHA256 under one Store key.

    ``store_key_b64`` is the 32-byte root key, base64-encoded
    (``BLINDFOLD_STORE_KEY``). Construction validates and decodes it eagerly --
    a malformed/wrong-length/non-base64 key is a named refusal
    (:class:`InvalidStoreKeyError`), never a silent fallback.
    """

    def __init__(self, store_key_b64: str) -> None:
        root_key = _decode_store_key(store_key_b64)
        self._encryption_key = _hkdf(root_key, _HKDF_INFO_ENCRYPTION)
        self._index_key = _hkdf(root_key, _HKDF_INFO_BLIND_INDEX)

    def encrypt(self, plaintext: str, *, context: str = "") -> str:
        """Encrypt ``plaintext``; returns a ``bf:v1:``-prefixed ciphertext.

        A fresh random 96-bit nonce per call (ADR-0045 §3) -- the same value
        encrypted twice never produces the same ciphertext. ``context``
        (e.g. ``"persons:canonical_name"``) is authenticated as additional
        data, so a ciphertext cannot be relocated to a different column.
        """
        nonce = os.urandom(_NONCE_LENGTH_BYTES)
        aesgcm = AESGCM(self._encryption_key)
        sealed = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return _PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, ciphertext: str, *, context: str = "") -> str:
        """Decrypt a ``bf:v1:``-prefixed ciphertext produced by :meth:`encrypt`.

        Raises :class:`cryptography.exceptions.InvalidTag` if ``context``
        doesn't match the value passed to :meth:`encrypt` -- including a
        ciphertext relocated to a different table/column.
        """
        if not ciphertext.startswith(_PREFIX):
            raise InvalidTag("ciphertext does not carry the bf:v1: scheme prefix")
        raw = base64.b64decode(ciphertext[len(_PREFIX) :])
        nonce, sealed = raw[:_NONCE_LENGTH_BYTES], raw[_NONCE_LENGTH_BYTES:]
        aesgcm = AESGCM(self._encryption_key)
        plaintext = aesgcm.decrypt(nonce, sealed, context.encode("utf-8"))
        return plaintext.decode("utf-8")

    def blind_index(self, value: str) -> str:
        """Return a ``bf:v1:``-prefixed HMAC-SHA256 digest of the exact ``value``.

        No normalisation (no casefolding, no trimming, ADR-0045 §3): deterministic
        for the exact same string, so today's ``UNIQUE`` semantics on the exact
        value are preserved.
        """
        h = hmac.HMAC(self._index_key, hashes.SHA256())
        h.update(value.encode("utf-8"))
        return _PREFIX + h.finalize().hex()
