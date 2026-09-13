"""Read and write Ren'Py `.save` files.

A save is a ZIP archive. Verified against real files, its members are:

    screenshot.png   thumbnail shown in the load menu
    extra_info       save slot caption (often empty)
    json             {"_save_name", "_renpy_version", "_version", ...}
    renpy_version    engine version as text
    log              the rollback log - a raw, *uncompressed* pickle

`log` unpickles to a 2-tuple of `(roots, rollback_log)`, where `roots` maps
fully-qualified store variable names (`store.arousal`) to their values. That
mapping is what the variable editor exposes.
"""

from __future__ import annotations

import json as jsonlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.errors import RoundTripError, SaveFormatError
from ...core.fsutil import atomic_write
from . import repickler, roundtrip, signing
from .stubs import StubRegistry
from .unpickler import detect_protocol, loads

LOG_MEMBER = "log"
JSON_MEMBER = "json"
SCREENSHOT_MEMBER = "screenshot.png"
EXTRA_INFO_MEMBER = "extra_info"
VERSION_MEMBER = "renpy_version"
SIGNATURES_MEMBER = "signatures"


@dataclass
class RenpySave:
    """A loaded save, with everything needed to write it back."""

    path: Path
    roots: dict[str, Any]
    log: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Verbatim copies of the members we do not interpret.
    extra_members: dict[str, bytes] = field(default_factory=dict)
    protocol: int = 2
    python_major: int = 3
    registry: StubRegistry | None = None

    @property
    def save_name(self) -> str:
        return str(self.metadata.get("_save_name", ""))

    @property
    def renpy_version(self) -> tuple[int, ...] | None:
        raw = self.metadata.get("_renpy_version")
        if isinstance(raw, list) and raw:
            return tuple(int(x) for x in raw)
        return None


def _python_major_from_metadata(metadata: dict[str, Any], fallback: int) -> int:
    """Ren'Py 7 saves are Python 2 pickles; the json says which engine wrote it."""
    raw = metadata.get("_renpy_version")
    if isinstance(raw, list) and raw:
        return 2 if int(raw[0]) <= 7 else 3
    return fallback


def load(path: Path | str, python_major: int = 3) -> RenpySave:
    """Load a save file. `python_major` is only a fallback - the save's own
    metadata is preferred when present."""
    path = Path(path)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise SaveFormatError(f"{path.name} is not a readable zip: {exc}") from exc

    with archive:
        names = set(archive.namelist())
        if LOG_MEMBER not in names:
            raise SaveFormatError(f"{path.name} has no '{LOG_MEMBER}' member")

        metadata: dict[str, Any] = {}
        if JSON_MEMBER in names:
            try:
                metadata = jsonlib.loads(archive.read(JSON_MEMBER).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                metadata = {}

        raw_log = archive.read(LOG_MEMBER)
        extra = {
            name: archive.read(name)
            for name in names
            if name not in {LOG_MEMBER, JSON_MEMBER}
        }

    effective_major = _python_major_from_metadata(metadata, python_major)
    protocol = detect_protocol(raw_log)

    registry = StubRegistry()
    try:
        payload, _ = loads(raw_log, python_major=effective_major, registry=registry)
    except Exception as exc:  # noqa: BLE001 - surfaced to the repair ladder
        raise SaveFormatError(f"{path.name}: could not unpickle log: {exc}") from exc

    if not isinstance(payload, tuple) or len(payload) != 2:
        raise SaveFormatError(
            f"{path.name}: expected log to be (roots, rollback_log), got {type(payload).__name__}"
        )

    roots, log = payload
    if not isinstance(roots, dict):
        raise SaveFormatError(f"{path.name}: roots is {type(roots).__name__}, not a mapping")

    return RenpySave(
        path=path,
        roots=roots,
        log=log,
        metadata=metadata,
        extra_members=extra,
        protocol=protocol,
        python_major=effective_major,
        registry=registry,
    )


def serialize_log(save: RenpySave) -> bytes:
    """Pickle the (roots, log) pair back to bytes."""
    return repickler.dumps((save.roots, save.log), protocol=save.protocol)


def verify_roundtrip(save: RenpySave, data: bytes) -> None:
    """Reload `data` from scratch and confirm it matches `save`.

    Raises RoundTripError if it does not. Callers must treat that as fatal and
    leave the original file alone.
    """
    try:
        payload, _ = loads(data, python_major=save.python_major, registry=StubRegistry())
    except Exception as exc:  # noqa: BLE001
        raise RoundTripError(f"re-pickled log could not be read back: {exc}") from exc

    try:
        roundtrip.verify((save.roots, save.log), payload)
    except roundtrip.Difference as exc:
        raise RoundTripError(f"re-pickled log differs at {exc.path}: {exc.detail}") from exc


def save_to(save: RenpySave, dest: Path | str | None = None, *, verify: bool = True) -> Path:
    """Write the save back out, through the round-trip gate and a backup.

    Returns the path written. The original file is untouched unless `dest` is
    None, and even then a timestamped backup is taken first.
    """
    dest = Path(dest) if dest is not None else save.path

    data = serialize_log(save)
    if verify:
        verify_roundtrip(save, data)

    buf = _build_archive(save, data)
    atomic_write(dest, buf)
    return dest


def _build_archive(save: RenpySave, log_bytes: bytes) -> bytes:
    """Rebuild the zip, preserving members we never interpreted.

    The `signatures` member is regenerated rather than copied: it signs the log
    we just rewrote, so the original is stale and would make Ren'Py warn that
    the save came from another computer every time it is loaded. If no signing
    key is available the member is dropped, which produces the same warning but
    is at least honest about being unsigned.
    """
    import io

    members = dict(save.extra_members)

    if SIGNATURES_MEMBER in members:
        try:
            members[SIGNATURES_MEMBER] = signing.sign_data(log_bytes).encode("utf-8")
        except signing.SigningUnavailable:
            del members[SIGNATURES_MEMBER]

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(LOG_MEMBER, log_bytes)
        archive.writestr(
            JSON_MEMBER, jsonlib.dumps(save.metadata, separators=(", ", ": "))
        )
        for name, blob in members.items():
            archive.writestr(name, blob)
    return out.getvalue()
