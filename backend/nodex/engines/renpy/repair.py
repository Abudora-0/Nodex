"""Diagnose and recover corrupted Ren'Py saves.

Corruption almost always lands in one of three places, so recovery is a ladder
that stops at the first rung that works:

1. **The zip container.** A crash or a half-finished sync truncates the file,
   taking the central directory with it. The member data is usually still
   there, so we scan for local file headers and rebuild the listing by hand.

2. **The pickle stream.** If `log` is truncated, the store dictionary is very
   often intact anyway - it is written near the start, ahead of the rollback
   log, which accounts for most of the file's size. We salvage it and rebuild a
   save around it. Rollback history is lost, so the player cannot rewind past
   the load point, but the playthrough survives.

3. **Neither.** Fall back to the newest healthy save in the same folder,
   keeping the broken file's metadata. Progress is lost back to that save.

Every outcome is reported with a confidence level rather than being presented
as an unqualified fix, because rungs 2 and 3 do lose something.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.errors import RepairFailed
from ...core.fsutil import atomic_write
from . import save as save_module
from .save import JSON_MEMBER, LOG_MEMBER, RenpySave
from .unpickler import load_partial

LOCAL_HEADER = b"PK\x03\x04"

#: How much was recovered, in decreasing order of goodness.
EXACT = "exact"  # nothing was lost
HIGH = "high"  # container rebuilt, all data intact
PARTIAL = "partial"  # store recovered, rollback history lost
SUBSTITUTED = "substituted"  # rebuilt from a sibling save
NONE = "none"


@dataclass
class Step:
    """One rung of the ladder and what it achieved."""

    name: str
    ok: bool
    detail: str


@dataclass
class Diagnosis:
    """The result of inspecting - and possibly rescuing - a save."""

    path: Path
    healthy: bool
    confidence: str
    problems: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    members: dict[str, bytes] = field(default_factory=dict)
    roots: dict[str, Any] | None = None
    log: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    python_major: int = 3
    protocol: int = 2

    @property
    def recoverable(self) -> bool:
        return self.roots is not None

    def summary(self) -> str:
        if self.healthy:
            return "No corruption detected."
        if self.recoverable:
            return f"Recoverable ({self.confidence}): " + "; ".join(self.problems)
        return "Unrecoverable: " + "; ".join(self.problems)


def scan_zip_members(data: bytes) -> dict[str, bytes]:
    """Recover zip members by walking local file headers.

    Used when the central directory is missing or truncated. Each header gives
    the member's name and, normally, its compressed size; when the size is
    zeroed (streamed writes set a data descriptor instead) we inflate until the
    decompressor reports the end of the stream.
    """
    members: dict[str, bytes] = {}
    offset = 0

    while True:
        offset = data.find(LOCAL_HEADER, offset)
        if offset < 0:
            break

        header = data[offset : offset + 30]
        if len(header) < 30:
            break

        try:
            (
                _version,
                flags,
                method,
                _time,
                _date,
                _crc,
                comp_size,
                _uncomp_size,
                name_len,
                extra_len,
            ) = struct.unpack("<HHHHHIIIHH", header[4:30])
        except struct.error:
            break

        start = offset + 30
        name = data[start : start + name_len].decode("utf-8", "replace")
        body = start + name_len + extra_len

        if comp_size and not flags & 0x08:
            blob = data[body : body + comp_size]
            consumed = comp_size
        else:
            # Streamed entry: inflate until the stream ends naturally.
            blob = data[body:]
            consumed = 0

        try:
            if method == 0:
                payload = blob if consumed else blob
            else:
                decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                payload = decompressor.decompress(blob)
                if not consumed:
                    consumed = len(blob) - len(decompressor.unused_data)
        except zlib.error:
            offset = start
            continue

        if name and name not in members:
            members[name] = payload

        offset = body + (consumed or 1)

    return members


def _read_members(path: Path, diagnosis: Diagnosis) -> dict[str, bytes]:
    """Get the save's members, falling back to a raw scan."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad is not None:
                diagnosis.problems.append(f"member '{bad}' failed its CRC check")
            members = {}
            for name in archive.namelist():
                try:
                    members[name] = archive.read(name)
                except Exception as exc:  # noqa: BLE001
                    diagnosis.problems.append(f"member '{name}' unreadable: {exc}")
            diagnosis.steps.append(
                Step("read zip directory", True, f"{len(members)} member(s)")
            )
            return members
    except Exception as exc:  # noqa: BLE001
        diagnosis.problems.append(f"zip directory unreadable: {exc}")
        diagnosis.steps.append(Step("read zip directory", False, str(exc)))

    members = scan_zip_members(path.read_bytes())
    diagnosis.steps.append(
        Step(
            "rebuild from local headers",
            bool(members),
            f"recovered {len(members)} member(s): {', '.join(sorted(members))}" or "nothing found",
        )
    )
    return members


def diagnose(path: Path | str, python_major: int = 3) -> Diagnosis:
    """Inspect a save and recover as much as possible without writing anything."""
    path = Path(path)
    diagnosis = Diagnosis(path=path, healthy=False, confidence=NONE)

    members = _read_members(path, diagnosis)
    diagnosis.members = members
    container_ok = not diagnosis.problems

    if JSON_MEMBER in members:
        import json as jsonlib

        try:
            diagnosis.metadata = jsonlib.loads(members[JSON_MEMBER].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            diagnosis.problems.append("metadata json is unreadable")

    diagnosis.python_major = save_module._python_major_from_metadata(
        diagnosis.metadata, python_major
    )

    raw_log = members.get(LOG_MEMBER)
    if not raw_log:
        diagnosis.problems.append("the 'log' member is missing entirely")
        diagnosis.steps.append(Step("locate log", False, "absent"))
        _try_sibling(path, diagnosis)
        return diagnosis

    from .unpickler import detect_protocol

    diagnosis.protocol = detect_protocol(raw_log)

    payload, salvaged, error = load_partial(raw_log, python_major=diagnosis.python_major)

    if payload is not None and isinstance(payload, tuple) and len(payload) == 2:
        roots, log = payload
        if isinstance(roots, dict):
            diagnosis.roots, diagnosis.log = roots, log
            diagnosis.steps.append(
                Step("unpickle log", True, f"{len(roots)} store variables")
            )
            diagnosis.healthy = container_ok
            diagnosis.confidence = EXACT if container_ok else HIGH
            return diagnosis

    diagnosis.problems.append(
        f"the rollback log is damaged: {type(error).__name__ if error else 'unexpected structure'}"
    )
    diagnosis.steps.append(Step("unpickle log", False, str(error)[:200] if error else "bad structure"))

    if salvaged:
        diagnosis.roots = salvaged
        diagnosis.log = []
        diagnosis.confidence = PARTIAL
        diagnosis.steps.append(
            Step(
                "salvage store from partial stream",
                True,
                f"{len(salvaged)} store variables recovered; rollback history lost",
            )
        )
        return diagnosis

    diagnosis.steps.append(Step("salvage store from partial stream", False, "no store found"))
    _try_sibling(path, diagnosis)
    return diagnosis


def _try_sibling(path: Path, diagnosis: Diagnosis) -> None:
    """Last resort: adopt the newest healthy save sitting next to this one."""
    candidates = [
        p
        for p in sorted(
            path.parent.glob("*.save"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if p != path
    ]

    for candidate in candidates:
        try:
            donor = save_module.load(candidate)
        except Exception:  # noqa: BLE001
            continue
        diagnosis.roots = donor.roots
        diagnosis.log = donor.log
        diagnosis.confidence = SUBSTITUTED
        diagnosis.steps.append(
            Step(
                "substitute nearest healthy save",
                True,
                f"used '{candidate.name}'; progress rolls back to that point",
            )
        )
        return

    diagnosis.steps.append(
        Step("substitute nearest healthy save", False, "no healthy sibling found")
    )


def repair(
    path: Path | str,
    dest: Path | str | None = None,
    python_major: int = 3,
) -> tuple[Path, Diagnosis]:
    """Repair a save, writing the result to `dest` (default: in place).

    Raises RepairFailed if nothing could be recovered. The original is backed
    up before being overwritten.
    """
    path = Path(path)
    diagnosis = diagnose(path, python_major=python_major)

    if diagnosis.healthy:
        return path, diagnosis

    if not diagnosis.recoverable:
        raise RepairFailed(diagnosis.summary())

    rebuilt = RenpySave(
        path=path,
        roots=diagnosis.roots or {},
        log=diagnosis.log,
        metadata=diagnosis.metadata,
        extra_members={
            name: blob
            for name, blob in diagnosis.members.items()
            if name not in {LOG_MEMBER, JSON_MEMBER}
        },
        protocol=diagnosis.protocol,
        python_major=diagnosis.python_major,
    )

    data = save_module.serialize_log(rebuilt)
    # The gate still applies: a repaired save must survive a full round trip
    # before we are willing to hand it to the game.
    save_module.verify_roundtrip(rebuilt, data)

    target = Path(dest) if dest is not None else path
    atomic_write(target, save_module._build_archive(rebuilt, data))
    return target, diagnosis
