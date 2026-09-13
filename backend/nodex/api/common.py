"""Helpers shared by every route module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..core.errors import NodexError
from ..core.fsutil import original_name
from ..engines.renpy import save as renpy_save
from ..engines.renpy import variables as renpy_vars
from . import cache

RPGMAKER_SUFFIXES = {".rpgsave", ".rmmzsave"}

UNITY_SUFFIXES = {
    ".nson",
    ".savefile",
    ".sav",
    ".es3",
    ".ngp1",
    ".json",
    ".dat",
    ".data",
    ".bytes",
}


def require_path(raw: str) -> Path:
    path = Path(raw)
    if not path.exists():
        raise HTTPException(404, f"Path does not exist: {raw}")
    return path


def jsonable(value: Any) -> Any:
    """Reduce a store value to something JSON can carry."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, bytes):
        return renpy_vars._decode(value)
    return None


def engine_of(path: Path) -> str:
    """Which engine wrote this save, judged by its extension (backups included)."""
    suffix = Path(original_name(path)).suffix.lower()
    if suffix in RPGMAKER_SUFFIXES:
        return "rpgmaker"
    if suffix in UNITY_SUFFIXES:
        return "unity"
    return "renpy"


def load_save(path: Path) -> renpy_save.RenpySave:
    cached = cache.saves.get(path)
    if cached is not None:
        return cached
    try:
        loaded = renpy_save.load(path)
    except NodexError as exc:
        raise HTTPException(422, str(exc)) from exc
    cache.saves.put(path, loaded)
    return loaded
