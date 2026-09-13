"""Keep recently opened saves in memory.

Loading a save means running a pure-Python unpickler over several hundred
kilobytes, which takes long enough to be noticeable in a UI. Since the browser
fetches the variable list, edits it, and writes it back as separate requests,
we hold the parsed object graph between them.

Entries are keyed on the file's modification time, so a save the game rewrites
underneath us is reloaded rather than served stale.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

MAX_ENTRIES = 8


class SaveCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max_entries

    def get(self, path: Path) -> Any | None:
        key = str(path)
        entry = self._entries.get(key)
        if entry is None:
            return None
        stamp, value = entry
        try:
            if path.stat().st_mtime != stamp:
                del self._entries[key]
                return None
        except OSError:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, path: Path, value: Any) -> None:
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return
        key = str(path)
        self._entries[key] = (stamp, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def invalidate(self, path: Path) -> None:
        self._entries.pop(str(path), None)

    def clear(self) -> None:
        self._entries.clear()


saves = SaveCache()
