"""App-level encryption for PII.

- Envelope encryption: each object/record gets a random data key (DEK); the DEK is
  wrapped with the master key (KEK) and stored next to the ciphertext. Rotating the KEK
  only re-wraps DEKs. The KEK comes from KHATTI_DATA_KEY (base64, 32 bytes); in
  production it would live in a KMS.
- Keyed HMAC for lookup and dedupe of identifiers (e.g. ID numbers) without storing them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .arabic import normalize

_VERSION = b"k1"


@dataclass(frozen=True)
class Keyring:
    kek: bytes  # 32 bytes
    hmac_key: bytes

    @classmethod
    def from_env_value(cls, value: str) -> "Keyring":
        raw = base64.b64decode(value)
        if len(raw) != 32:
            raise ValueError("KHATTI_DATA_KEY must be 32 bytes, base64-encoded")
        # Separate subkeys so the HMAC key never equals the encryption key.
        return cls(
            kek=hmac.new(raw, b"khatti-kek", hashlib.sha256).digest(),
            hmac_key=hmac.new(raw, b"khatti-hmac", hashlib.sha256).digest(),
        )

    @classmethod
    def generate(cls) -> tuple["Keyring", str]:
        value = base64.b64encode(os.urandom(32)).decode()
        return cls.from_env_value(value), value

    # ------------------------------------------------------------ envelope

    def seal(self, plaintext: bytes, context: str = "") -> bytes:
        """Returns version | wrapped_dek_len | wrapped_dek | nonce | ciphertext."""
        dek = AESGCM.generate_key(bit_length=256)
        wrap_nonce, nonce = os.urandom(12), os.urandom(12)
        wrapped = wrap_nonce + AESGCM(self.kek).encrypt(wrap_nonce, dek, context.encode())
        ct = AESGCM(dek).encrypt(nonce, plaintext, context.encode())
        return _VERSION + len(wrapped).to_bytes(2, "big") + wrapped + nonce + ct

    def open(self, blob: bytes, context: str = "") -> bytes:
        if blob[:2] != _VERSION:
            raise ValueError("unknown ciphertext version")
        n = int.from_bytes(blob[2:4], "big")
        wrapped, rest = blob[4 : 4 + n], blob[4 + n :]
        dek = AESGCM(self.kek).decrypt(wrapped[:12], wrapped[12:], context.encode())
        return AESGCM(dek).decrypt(rest[:12], rest[12:], context.encode())

    def seal_json(self, obj, context: str = "") -> bytes:
        return self.seal(json.dumps(obj, ensure_ascii=False, default=str).encode(), context)

    def open_json(self, blob: bytes, context: str = ""):
        return json.loads(self.open(blob, context))

    # ------------------------------------------------------------ lookup

    def lookup_hmac(self, kind: str, value: str) -> str:
        """Deterministic keyed hash of a normalised identifier, for dedupe/lookup."""
        return hmac.new(self.hmac_key, f"{kind}:{normalize(value)}".encode(), hashlib.sha256).hexdigest()
