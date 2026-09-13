"""Work out where a game's saves actually live.

This is less obvious than it sounds. Ren'Py only keeps saves in
`game/saves/` for a game run from its own folder; a normally installed game
writes to `%APPDATA%/RenPy/<config.save_directory>` instead. Nothing in the
game folder's *name* points there - `ACCORD-0.4.2-pc` saves into
`ACCORD-1724460959` - so the link has to be recovered from the script.

Three sources, in order of reliability:

1. `game/options.rpy`, if the game shipped its source.
2. `game/options.rpyc`, read as an AST. Most games ship compiled scripts only,
   so this is the common path.
3. A fuzzy match against the folder names under `%APPDATA%/RenPy`, as a last
   resort when both of the above are unavailable.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import rpyc
from .rpyc import node_type, source_of, walk

SAVE_DIRECTORY_RE = re.compile(
    r"""config\.save_directory\s*=\s*(?:_\()?["'](?P<value>[^"']+)["']"""
)


@dataclass
class SaveLocation:
    path: Path
    source: str
    save_count: int

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


def renpy_appdata_root() -> Path | None:
    """The per-user directory Ren'Py keeps save folders in."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "RenPy"
        return Path.home() / "AppData/Roaming/RenPy"
    if sys.platform == "darwin":
        return Path.home() / "Library/RenPy"
    return Path.home() / ".renpy"


def save_directory_from_source(game_dir: Path) -> str | None:
    """Read `config.save_directory` out of options.rpy."""
    options = game_dir / "options.rpy"
    if not options.is_file():
        return None
    match = SAVE_DIRECTORY_RE.search(
        options.read_text(encoding="utf-8", errors="replace")
    )
    return match.group("value") if match else None


def save_directory_from_rpyc(game_dir: Path, python_major: int = 3) -> str | None:
    """Read `config.save_directory` out of the compiled options script.

    A `define config.save_directory = "..."` statement compiles to a Define
    node whose code carries the original expression, so the value survives
    compilation and can be pulled straight back out.

    Scripts are searched wherever they live - several games in the local
    library keep `options.rpyc` inside an `.rpa` archive with nothing loose on
    disk at all.
    """
    from . import scripts as script_module

    script_set = script_module.discover(game_dir, python_major)
    if not script_set.sources:
        return None

    # options.rpyc holds the define in every game that sets it; check it first
    # and only fall back to a full sweep if it is missing or says nothing.
    ordered = sorted(
        script_set.sources,
        key=lambda name: (Path(name).name != "options.rpyc", name),
    )

    for name in ordered:
        try:
            script = script_set.load(name)
        except Exception:  # noqa: BLE001 - a bad script must not stop the search
            continue

        for node in walk(script.nodes):
            if node_type(node) not in ("Define", "Default"):
                continue
            # `store` is usually 'store.config'; the variable name alone is the
            # reliable discriminator across games.
            if node.__dict__.get("varname") != "save_directory":
                continue
            source = source_of(node)
            if not source:
                continue
            match = re.search(r"""["']([^"']+)["']""", source)
            if match:
                return match.group(1)

    return None


def _fuzzy_appdata_match(title: str, root: Path) -> Path | None:
    """Match a game folder name against the AppData save folders."""
    if not root.is_dir():
        return None

    def normalise(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    # Strip the packaging suffix: "ACCORD-0.4.2-pc" -> "ACCORD".
    stem = re.split(r"[-_]\d", title)[0]
    target = normalise(stem)
    if len(target) < 3:
        return None

    best: tuple[int, Path] | None = None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        # AppData folders carry a numeric suffix: "ACCORD-1724460959".
        candidate = normalise(re.split(r"-\d{6,}$", child.name)[0])
        if not candidate:
            continue
        if candidate == target:
            return child
        if candidate.startswith(target) or target.startswith(candidate):
            score = min(len(candidate), len(target))
            if best is None or score > best[0]:
                best = (score, child)

    return best[1] if best else None


def find_save_locations(
    game_dir: Path, title: str, python_major: int = 3
) -> list[SaveLocation]:
    """All the places this game's saves might be, best first."""
    locations: list[SaveLocation] = []
    seen: set[Path] = set()

    def add(path: Path, source: str) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_dir():
            return
        seen.add(resolved)
        locations.append(
            SaveLocation(
                path=resolved,
                source=source,
                save_count=len(list(resolved.glob("*.save"))),
            )
        )

    appdata = renpy_appdata_root()

    name = save_directory_from_source(game_dir)
    if name and appdata:
        add(appdata / name, "config.save_directory (options.rpy)")

    if not name:
        name = save_directory_from_rpyc(game_dir, python_major)
        if name and appdata:
            add(appdata / name, "config.save_directory (compiled script)")

    # A game run in place keeps saves beside itself.
    add(game_dir / "saves", "game/saves")

    if not locations and appdata:
        guess = _fuzzy_appdata_match(title, appdata)
        if guess is not None:
            add(guess, "name match in %APPDATA%/RenPy")

    locations.sort(key=lambda location: location.save_count, reverse=True)
    return locations
