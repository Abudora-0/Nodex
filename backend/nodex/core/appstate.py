"""Nodex's own persistent state: library roots, recents, presets, edit history.

`state.json` is small and rewritten whole; `history.jsonl` is append-only.
Request handlers run in a thread pool, so every access goes through one lock.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .apphome import nodex_home
from .fsutil import atomic_write

SCHEMA_VERSION = 1
MAX_RECENTS = 20
_lock = threading.RLock()


def _state_path() -> Path:
    return nodex_home() / "state.json"


def _history_path() -> Path:
    return nodex_home() / "history.jsonl"


def _empty() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "library_roots": [], "recents": [], "presets": [], "prefs": {}}


def game_id(root: Path | str) -> str:
    resolved = str(Path(root).resolve()).lower()
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def load() -> dict[str, Any]:
    with _lock:
        path = _state_path()
        if not path.is_file():
            return _empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state is not an object")
        except (ValueError, OSError):
            path.replace(path.with_name(f"state.corrupt-{int(time.time())}.json"))
            return _empty()
        merged = _empty()
        merged.update(data)
        return merged


def save(state: dict[str, Any]) -> None:
    with _lock:
        payload = json.dumps(state, indent=2, ensure_ascii=False).encode("utf-8")
        atomic_write(_state_path(), payload, make_backup=False)


def update(mutator) -> dict[str, Any]:
    with _lock:
        state = load()
        mutator(state)
        save(state)
        return state


def add_recent(entry: dict[str, Any]) -> None:
    def mutate(state: dict[str, Any]) -> None:
        recents = [r for r in state["recents"] if r.get("root") != entry["root"]]
        recents.insert(0, {**entry, "opened": time.time()})
        state["recents"] = recents[:MAX_RECENTS]

    update(mutate)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def append_history(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    with _lock:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history() -> list[dict[str, Any]]:
    """All history entries, newest first. Unparseable lines are skipped."""
    with _lock:
        path = _history_path()
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        entries.reverse()
        return entries


def mark_undone(entry_ids: set[str]) -> None:
    with _lock:
        path = _history_path()
        if not path.is_file():
            return
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                lines.append(line)
                continue
            if entry.get("id") in entry_ids:
                entry["undone"] = True
            lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"), make_backup=False)
