"""Re-sign edited saves with the player's own Ren'Py token.

Ren'Py 8 signs the `log` member of every save and stores the result in a
`signatures` member. On load it verifies that signature, and if it does not
match a known key the player gets a warning that the save came from another
computer and may be dangerous.

Editing the log naturally invalidates the old signature. Leaving it stale - or
dropping it - means that warning appears every single time an edited save is
loaded, which would make the whole tool unpleasant to use. Since the player's
own signing key is sitting in `%APPDATA%/RenPy/tokens/security_keys.txt`, the
honest fix is to re-sign with it: the save really is theirs, on their machine.

The scheme was determined by verifying a real save rather than guessed:
ECDSA on NIST P-256, **SHA-1** digest, signature stored as a raw 64-byte
`r || s` pair, with the public key as a DER SubjectPublicKeyInfo blob. Both
values are base64-encoded into the line::

    signature <public-key-b64> <signature-b64>
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from .locate import renpy_appdata_root

TOKEN_FILE = "security_keys.txt"
SIGNING_KEY_FIELD = "signing-key"
SIGNATURE_FIELD = "signature"
#: Half the length of a P-256 raw signature; r and s are 32 bytes each.
COORDINATE_BYTES = 32


@dataclass
class SigningKey:
    """One key pair the player signs their saves with."""

    private_der: bytes
    public_der: bytes


class SigningUnavailable(Exception):
    """No key, or no crypto backend - callers should carry on unsigned."""


def token_path() -> Path | None:
    root = renpy_appdata_root()
    return (root / "tokens" / TOKEN_FILE) if root else None


def load_signing_keys(path: Path | None = None) -> list[SigningKey]:
    """Read the player's signing keys. Returns [] when there are none."""
    path = path or token_path()
    if path is None or not path.is_file():
        return []

    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return []

    keys: list[SigningKey] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != SIGNING_KEY_FIELD:
            continue
        try:
            private_der = base64.b64decode(parts[1])
            private = serialization.load_der_private_key(private_der, password=None)
            public_der = private.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        except Exception:  # noqa: BLE001 - a malformed key is simply skipped
            continue
        keys.append(SigningKey(private_der=private_der, public_der=public_der))

    return keys


def sign_data(data: bytes, keys: list[SigningKey] | None = None) -> str:
    """Produce the `signatures` member for `data`.

    Raises SigningUnavailable if there is nothing to sign with.
    """
    keys = keys if keys is not None else load_signing_keys()
    if not keys:
        raise SigningUnavailable("no Ren'Py signing key found")

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils
    except ImportError as exc:
        raise SigningUnavailable("the cryptography package is not installed") from exc

    lines = []
    for key in keys:
        private = serialization.load_der_private_key(key.private_der, password=None)
        # Ren'Py hashes with SHA-1 - confirmed by verifying an untouched save.
        der_signature = private.sign(data, ec.ECDSA(hashes.SHA1()))
        r, s = utils.decode_dss_signature(der_signature)
        raw = r.to_bytes(COORDINATE_BYTES, "big") + s.to_bytes(COORDINATE_BYTES, "big")

        lines.append(
            "{} {} {}\n".format(
                SIGNATURE_FIELD,
                base64.b64encode(key.public_der).decode("ascii"),
                base64.b64encode(raw).decode("ascii"),
            )
        )

    return "".join(lines)


def verify_data(data: bytes, signatures: str) -> bool:
    """Check `data` against a `signatures` block. Used by the test suite."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils
    except ImportError:
        return False

    for line in signatures.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0] != SIGNATURE_FIELD:
            continue
        try:
            public = serialization.load_der_public_key(base64.b64decode(parts[1]))
            raw = base64.b64decode(parts[2])
            if len(raw) != COORDINATE_BYTES * 2:
                continue
            der = utils.encode_dss_signature(
                int.from_bytes(raw[:COORDINATE_BYTES], "big"),
                int.from_bytes(raw[COORDINATE_BYTES:], "big"),
            )
            public.verify(der, data, ec.ECDSA(hashes.SHA1()))
            return True
        except Exception:  # noqa: BLE001
            continue

    return False
