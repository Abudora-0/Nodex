"""Reading and writing Unity visual-novel saves.

Unity has no standard save format, so this is a small set of handlers chosen by
what games actually ship. Surveying the local library (47 companies under
`AppData/LocalLow`) gave a clear ranking:

* **Naninovel** (`.nson`) - 97 files, easily the most common. A *raw deflate*
  stream (no zlib header, `wbits=-15`) wrapping UTF-8 JSON with a BOM. The
  payload is Naninovel's `objectJsonMap`: parallel `keys`/`values` arrays where
  each key is a .NET type name and each value is *itself* a JSON string.
* **Plain JSON** - 32 files across `.json`, `.savefile`, `.dat` and
  unencrypted `.es3`.
* **Encrypted or opaque binary** - 69 files (`.bytes`, `.sav`, `.save`,
  encrypted `.es3`, `.ngp1`). Easy Save 3 encrypts with AES using a password
  baked into the game's own code, so opening these would mean extracting a key
  from each game's binary individually. They are detected and reported rather
  than guessed at.

Nested JSON strings are deliberately left as strings. Expanding them on load
and re-serialising on save would quietly reformat parts of the file the user
never touched, so `variables.py` parses only the one entry it needs.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import NodexError

#: Extensions worth trying at all.
SAVE_SUFFIXES = {
    ".nson",
    ".json",
    ".savefile",
    ".sav",
    ".save",
    ".dat",
    ".data",
    ".es3",
    ".bytes",
    ".ngp1",
}

NANINOVEL = "naninovel"
JSON = "json"
ENCRYPTED = "encrypted"

BOM = "﻿"


class UnityFormatError(NodexError):
    """The file is not a Unity save this module can open."""


class EncryptedSave(UnityFormatError):
    """Recognised as a save, but its contents are encrypted."""


@dataclass
class DecodedSave:
    """A Unity save's contents plus what is needed to write it back."""

    data: Any
    flavour: str
    original: bytes
    #: True when the text carried a byte-order mark, which must be preserved.
    had_bom: bool = False

    @property
    def is_naninovel(self) -> bool:
        return self.flavour == NANINOVEL


def _parse_json_text(raw: bytes) -> tuple[Any, bool] | None:
    """Parse UTF-8 JSON, reporting whether a BOM was present."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    stripped = text.lstrip()
    if not stripped[:1] in ("{", "["):
        return None
    try:
        return json.loads(text), raw.startswith(b"\xef\xbb\xbf")
    except json.JSONDecodeError:
        return None


def decode(raw: bytes, path: Path | None = None) -> DecodedSave:
    """Read a Unity save's bytes into plain Python data."""
    if not raw:
        raise UnityFormatError("The file is empty.")

    # Naninovel: raw deflate, so there is no header to check - just try it.
    try:
        inflated = zlib.decompress(raw, -15)
    except zlib.error:
        inflated = None

    if inflated is not None:
        parsed = _parse_json_text(inflated)
        if parsed is not None:
            data, had_bom = parsed
            flavour = NANINOVEL if _looks_like_naninovel(data) else JSON
            return DecodedSave(data, flavour, raw, had_bom)

    parsed = _parse_json_text(raw)
    if parsed is not None:
        data, had_bom = parsed
        flavour = NANINOVEL if _looks_like_naninovel(data) else JSON
        return DecodedSave(data, flavour, raw, had_bom)

    # zlib-wrapped JSON turns up occasionally.
    try:
        parsed = _parse_json_text(zlib.decompress(raw))
    except zlib.error:
        parsed = None
    if parsed is not None:
        data, had_bom = parsed
        return DecodedSave(data, JSON, raw, had_bom)

    raise EncryptedSave(
        "This save is encrypted or uses a format Nodex does not recognise. "
        "Easy Save 3 files are encrypted with a password stored inside the "
        "game itself, which has to be recovered per game."
    )


def _looks_like_naninovel(data: Any) -> bool:
    return isinstance(data, dict) and "objectJsonMap" in data


def to_json(data: Any, had_bom: bool) -> bytes:
    """Serialise the way Unity's own writers do: compact, UTF-8, BOM preserved."""
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    if had_bom:
        text = BOM + text
    return text.encode("utf-8")


def encode(decoded: DecodedSave) -> bytes:
    """Turn a decoded save back into file bytes."""
    payload = to_json(decoded.data, decoded.had_bom)

    if decoded.flavour == NANINOVEL:
        # Raw deflate, matching how Naninovel writes it.
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        return compressor.compress(payload) + compressor.flush()

    if decoded.flavour == JSON:
        return payload

    raise UnityFormatError(f"Cannot write a {decoded.flavour} save.")


def verify_roundtrip(decoded: DecodedSave, encoded: bytes) -> None:
    """Refuse to write anything that will not read back identically."""
    try:
        reread = decode(encoded)
    except NodexError as exc:
        raise UnityFormatError(
            f"The re-encoded save could not be read back: {exc}"
        ) from exc

    if reread.data != decoded.data:
        raise UnityFormatError(
            "The re-encoded save did not match the original structure, so it "
            "was not written. This is a bug in Nodex - please report it."
        )


def load(path: Path | str) -> DecodedSave:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UnityFormatError(f"Could not read {path.name}: {exc}") from exc
    return decode(raw, path)


def save_to(decoded: DecodedSave, path: Path | str) -> Path | None:
    """Write a save back, verifying first. Returns None if nothing changed."""
    from ...core.fsutil import atomic_write

    path = Path(path)
    encoded = encode(decoded)

    if encoded == decoded.original:
        return None

    verify_roundtrip(decoded, encoded)
    atomic_write(path, encoded)
    return path
