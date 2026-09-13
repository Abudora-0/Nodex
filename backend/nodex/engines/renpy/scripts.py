"""Unified access to a game's scripts, wherever they happen to live.

A game may ship its scripts as loose `.rpy` source, as compiled `.rpyc`
alongside it, or with everything sealed inside one or more `.rpa` archives.
Across the local library all three arrangements occur, sometimes in the same
game. Every analysis feature needs the same thing regardless: the AST for each
script. This module hides the difference.

Loose `.rpyc` is preferred over an archived copy of the same path, because a
game patched after release leaves the newer file on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from . import rpa, rpyc
from .rpyc import Script


@dataclass
class ScriptSource:
    """Where a script came from, and how to read it."""

    name: str
    origin: str  # "loose" or the archive filename
    path: Path | None = None
    archive: rpa.Archive | None = None

    def read(self) -> bytes:
        if self.path is not None:
            return self.path.read_bytes()
        if self.archive is not None:
            return self.archive.read(self.name)
        raise FileNotFoundError(self.name)


@dataclass
class ScriptSet:
    """Every compiled script belonging to one game."""

    game_dir: Path
    python_major: int = 3
    sources: dict[str, ScriptSource] = field(default_factory=dict)
    archives: list[rpa.Archive] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.sources)

    def load(self, name: str) -> Script:
        """Parse one script to its AST."""
        source = self.sources[name]
        data = source.read()
        return rpyc.load_bytes(data, name=name, python_major=self.python_major)

    def iter_scripts(self, skip_errors: bool = True) -> Iterator[Script]:
        """Parse every script, in a stable order."""
        for name in sorted(self.sources):
            try:
                yield self.load(name)
            except Exception:  # noqa: BLE001
                if not skip_errors:
                    raise


def discover(game_dir: Path, python_major: int = 3) -> ScriptSet:
    """Find every compiled script for a game."""
    game_dir = Path(game_dir)
    result = ScriptSet(game_dir=game_dir, python_major=python_major)

    for path in sorted(game_dir.rglob("*.rpyc")):
        name = path.relative_to(game_dir).as_posix()
        result.sources[name] = ScriptSource(name=name, origin="loose", path=path)

    for archive in rpa.open_all(game_dir):
        result.archives.append(archive)
        for name in archive.glob(".rpyc"):
            # Loose files win: a patch drops updated scripts next to the archive.
            if name not in result.sources:
                result.sources[name] = ScriptSource(
                    name=name, origin=archive.path.name, archive=archive
                )

    return result
