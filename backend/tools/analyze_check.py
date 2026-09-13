"""Run the choice analyser over every installed game and report what it found.

The unit tests cover the parsing rules; this covers the shapes real games
actually take. It is how the Ren'Py 7 byte-key bug was caught - Deliverance
reported 0 menus while its source clearly contained 229 `menu:` statements.

Usage::

    python tools/analyze_check.py
    python tools/analyze_check.py "D:/Games/Unfinished"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodex.core.errors import UnsupportedGame  # noqa: E402
from nodex.core.registry import detect  # noqa: E402
from nodex.engines.renpy import analyze, walkthrough  # noqa: E402

DEFAULT_ROOT = Path("D:/Games/Unfinished")


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        print(f"No such directory: {root}")
        return 2

    print(f"{'game':<36}{'menus':>7}{'useful':>8}{'labels':>8}{'vars':>7}  mod")
    print("-" * 74)

    total_menus = 0
    silent: list[str] = []

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            info = detect(child)
        except UnsupportedGame:
            continue
        if info.engine != "renpy" or info.game_dir is None:
            continue

        try:
            result = analyze.analyze(info.game_dir, info.python_major)
        except Exception as exc:  # noqa: BLE001
            print(f"{info.title[:36]:<36}  FAILED: {type(exc).__name__}: {exc}")
            continue

        labelled = sum(1 for menu in result.menus if menu.label)
        annotations = walkthrough.build_annotations(result)
        total_menus += len(result.menus)

        if not result.menus:
            silent.append(info.title)

        print(
            f"{info.title[:36]:<36}{len(result.menus):>7}"
            f"{len(result.meaningful_menus):>8}{labelled:>8}"
            f"{len(result.variables):>7}  {len(annotations)} annotated"
        )

    print("-" * 74)
    print(f"{total_menus} menus across all games")

    if silent:
        # Not necessarily wrong - a kinetic novel genuinely has no choices -
        # but worth eyeballing, because it is also what a parsing bug looks like.
        print("\nGames with no menus at all (verify these are kinetic novels):")
        for title in silent:
            print(f"  {title}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
