"""Filesystem safety net.

Nothing in Nodex writes to a game file directly. Everything goes through
`atomic_write`, which backs the original up first and then swaps the new
content in via a temporary file in the same directory. A crash mid-write can
therefore leave either the old file or the new one, never a half-written one.

Backups live in a per-user vault rather than beside the original, so repeatedly
editing a save does not litter the game's save folder with `.bak` files.
"""

from __future__ import annotations

import os
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from .apphome import nodex_home

_captured: ContextVar[list[Path] | None] = ContextVar("nodex_captured_backups", default=None)

BACKUP_SUFFIX_RE = re.compile(r"\.(\d{8}-\d{6}-\d{3})\.bak$")


def vault_root() -> Path:
    """Directory holding every backup Nodex has ever taken."""
    base = os.environ.get("NODEX_VAULT")
    if base:
        return Path(base)
    return nodex_home() / "backups"


@contextmanager
def capture_backups():
    """Collect every backup path taken inside the block, without changing callers."""
    taken: list[Path] = []
    token = _captured.set(taken)
    try:
        yield taken
    finally:
        _captured.reset(token)


def original_name(path: Path | str) -> str:
    """`name.save.20260101-120000-000.bak` -> `name.save`; other names unchanged."""
    name = Path(path).name
    return BACKUP_SUFFIX_RE.sub("", name)


def backup_stamp(path: Path | str) -> datetime | None:
    match = BACKUP_SUFFIX_RE.search(Path(path).name)
    if not match:
        return None
    return datetime.strptime(match.group(1) + "000", "%Y%m%d-%H%M%S-%f")


def _slugify(path: Path) -> str:
    """Turn an absolute path into a flat, filesystem-safe directory name."""
    drive, tail = os.path.splitdrive(str(path))
    parts = [p for p in Path(tail).parts if p not in ("\\", "/")]
    slug = "_".join(parts[-3:]) if parts else "root"
    if drive:
        slug = drive.rstrip(":") + "_" + slug
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in slug)


def backup(path: Path) -> Path | None:
    """Copy `path` into the vault, timestamped. Returns the backup location.

    Returns None if the file does not exist yet - creating a new file needs no
    backup.
    """
    path = Path(path)
    if not path.exists():
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    dest_dir = vault_root() / _slugify(path.parent)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, dest)
    taken = _captured.get()
    if taken is not None:
        taken.append(dest)
    return dest


def atomic_write(path: Path, data: bytes, make_backup: bool = True) -> Path | None:
    """Write `data` to `path`, backing up any existing file first.

    Returns the path of the backup that was taken, or None if there was
    nothing to back up.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = backup(path) if make_backup else None

    tmp = path.with_name(f".{path.name}.nodex-tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    return backup_path


def list_backups(path: Path) -> list[Path]:
    """Every backup Nodex holds for `path`, newest first."""
    path = Path(path)
    dest_dir = vault_root() / _slugify(path.parent)
    if not dest_dir.is_dir():
        return []
    matches = [
        p for p in dest_dir.iterdir()
        if p.name.startswith(path.name + ".") and original_name(p) == path.name
    ]
    return sorted(matches, reverse=True)


def restore(path: Path, backup_path: Path) -> Path | None:
    """Put a backup back, taking a fresh backup of the current file first.

    Returns the backup of the file that was replaced.
    """
    previous = backup(path)
    shutil.copy2(backup_path, path)
    return previous
