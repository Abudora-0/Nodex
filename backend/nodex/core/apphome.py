"""Where Nodex keeps its own files, and where bundled resources live."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def nodex_home() -> Path:
    base = os.environ.get("NODEX_HOME")
    return Path(base) if base else Path.home() / ".nodex"


def resource_root() -> Path:
    """Base folder for bundled resources: the PyInstaller unpack dir when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[3]


def static_dir() -> Path | None:
    override = os.environ.get("NODEX_STATIC")
    if override:
        candidate = Path(override)
    elif getattr(sys, "frozen", False):
        candidate = resource_root() / "ui"
    else:
        candidate = resource_root() / "frontend" / "dist"
    return candidate if candidate.is_dir() else None
