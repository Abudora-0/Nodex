"""Read and write Ren'Py's `persistent` file.

Unlike a save, this one *is* zlib-compressed: the file begins with a zlib
header and inflates to a protocol-2 pickle of `renpy.persistent.Persistent`.
Verified against a real file, which inflated to::

    \\x80\\x02crenpy.persistent\\nPersistent\\nq\\x00)\\x81q\\x01}...

Persistent data is where Ren'Py records things that outlive any single
playthrough - which images the player has seen, which endings they reached, and
almost always the gallery unlock flags. That makes this module the foundation
of the gallery unlocker.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import RoundTripError, SaveFormatError
from ...core.fsutil import atomic_write
from . import repickler, roundtrip
from .stubs import StubBase, StubRegistry
from .unpickler import detect_protocol, loads

#: Ren'Py's own bookkeeping attributes on the Persistent object.
SEEN_IMAGES = "_seen_images"
SEEN_EVER = "_seen_ever"
SEEN_AUDIO = "_seen_audio"
CHOSEN = "_chosen"

#: zlib compression level Ren'Py itself uses when writing persistent data.
COMPRESS_LEVEL = 3


@dataclass
class RenpyPersistent:
    """A loaded persistent file."""

    path: Path
    data: Any
    protocol: int = 2
    python_major: int = 3
    registry: StubRegistry | None = None

    @property
    def fields(self) -> dict[str, Any]:
        """The persistent object's attribute dictionary."""
        if isinstance(self.data, StubBase):
            return self.data.__dict__
        if isinstance(self.data, dict):
            return self.data
        return {}


def load(path: Path | str, python_major: int = 3) -> RenpyPersistent:
    """Load a persistent file, transparently handling the zlib wrapper."""
    path = Path(path)
    raw = path.read_bytes()

    try:
        blob = zlib.decompress(raw)
    except zlib.error as exc:
        raise SaveFormatError(f"{path.name}: not zlib-compressed: {exc}") from exc

    protocol = detect_protocol(blob)
    registry = StubRegistry()
    try:
        data, _ = loads(blob, python_major=python_major, registry=registry)
    except Exception as exc:  # noqa: BLE001
        raise SaveFormatError(f"{path.name}: could not unpickle: {exc}") from exc

    return RenpyPersistent(
        path=path,
        data=data,
        protocol=protocol,
        python_major=python_major,
        registry=registry,
    )


def serialize(persistent: RenpyPersistent) -> bytes:
    """Pickle and compress, matching Ren'Py's own framing."""
    blob = repickler.dumps(persistent.data, protocol=persistent.protocol)
    return zlib.compress(blob, COMPRESS_LEVEL)


def verify_roundtrip(persistent: RenpyPersistent, payload: bytes) -> None:
    """Reload `payload` from scratch and confirm nothing changed."""
    try:
        blob = zlib.decompress(payload)
        reloaded, _ = loads(
            blob, python_major=persistent.python_major, registry=StubRegistry()
        )
    except Exception as exc:  # noqa: BLE001
        raise RoundTripError(f"re-written persistent could not be read back: {exc}") from exc

    try:
        roundtrip.verify(persistent.data, reloaded)
    except roundtrip.Difference as exc:
        raise RoundTripError(
            f"re-written persistent differs at {exc.path}: {exc.detail}"
        ) from exc


def save_to(
    persistent: RenpyPersistent,
    dest: Path | str | None = None,
    *,
    verify: bool = True,
) -> Path:
    """Write the persistent file back, through the gate and a backup."""
    dest = Path(dest) if dest is not None else persistent.path
    payload = serialize(persistent)
    if verify:
        verify_roundtrip(persistent, payload)
    atomic_write(dest, payload)
    return dest
