"""Read Ren'Py archive files (`.rpa`).

The header is a single ASCII line, verified against a real archive::

    RPA-3.0 000000006668b5d8 42424242

giving the version, the index's byte offset, and (from 3.0 onwards) an XOR key.
The index itself is a zlib-compressed pickle mapping each archived path to a
list of `(offset, length, prefix)` segments, with offset and length XORed
against the key.

Games routinely ship their scripts only inside an archive, so without this
Nodex cannot see the choices - or even find where a game keeps its saves.

This is read-only. Nodex extracts individual files into memory to analyse them
and never repackages or redistributes game assets.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import SaveFormatError
from .stubs import StubRegistry
from .unpickler import loads

MAX_HEADER = 64


@dataclass
class Archive:
    """An open archive's index."""

    path: Path
    version: str
    index: dict[str, list[tuple[int, int, bytes]]]

    def __contains__(self, name: str) -> bool:
        return _normalise(name) in self.index

    @property
    def names(self) -> list[str]:
        return sorted(self.index)

    def read(self, name: str) -> bytes:
        """Extract one archived file."""
        key = _normalise(name)
        segments = self.index.get(key)
        if segments is None:
            raise KeyError(name)

        with open(self.path, "rb") as handle:
            chunks = []
            for offset, length, prefix in segments:
                handle.seek(offset)
                chunks.append(prefix + handle.read(length - len(prefix)))
        return b"".join(chunks)

    def glob(self, suffix: str) -> list[str]:
        return [name for name in self.index if name.endswith(suffix)]


def _normalise(name: str) -> str:
    return name.replace("\\", "/").lstrip("./")


def _coerce_prefix(value: Any) -> bytes:
    """Index prefixes are bytes in Python 3 archives and str in Python 2 ones."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("latin-1", "replace")
    return b""


def load(path: Path | str) -> Archive:
    """Open an archive and decode its index."""
    path = Path(path)

    with open(path, "rb") as handle:
        header = handle.readline(MAX_HEADER).decode("ascii", "replace").strip()
        parts = header.split()

        if not parts or not parts[0].startswith("RPA-"):
            raise SaveFormatError(f"{path.name}: not a Ren'Py archive ({header!r})")

        version = parts[0]
        try:
            offset = int(parts[1], 16)
        except (IndexError, ValueError) as exc:
            raise SaveFormatError(f"{path.name}: unreadable index offset") from exc

        # 2.0 has no obfuscation key; 3.0 and later XOR the offsets with one.
        key = 0
        if len(parts) > 2:
            try:
                key = int(parts[2], 16)
            except ValueError:
                key = 0

        handle.seek(offset)
        blob = handle.read()

    try:
        raw_index, _ = loads(zlib.decompress(blob), python_major=3, registry=StubRegistry())
    except Exception as exc:  # noqa: BLE001
        raise SaveFormatError(f"{path.name}: unreadable index: {exc}") from exc

    if not isinstance(raw_index, dict):
        raise SaveFormatError(f"{path.name}: index is not a mapping")

    index: dict[str, list[tuple[int, int, bytes]]] = {}
    for name, segments in raw_index.items():
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        entries: list[tuple[int, int, bytes]] = []
        for segment in segments or []:
            if len(segment) >= 3:
                seg_offset, seg_length, prefix = segment[0], segment[1], segment[2]
            elif len(segment) == 2:
                seg_offset, seg_length, prefix = segment[0], segment[1], b""
            else:
                continue
            entries.append(
                (seg_offset ^ key, seg_length ^ key, _coerce_prefix(prefix))
            )
        if entries:
            index[_normalise(str(name))] = entries

    return Archive(path=path, version=version, index=index)


def open_all(game_dir: Path) -> list[Archive]:
    """Open every archive in a game directory, skipping unreadable ones."""
    archives = []
    for candidate in sorted(game_dir.glob("*.rpa")):
        try:
            archives.append(load(candidate))
        except SaveFormatError:
            continue
    return archives
