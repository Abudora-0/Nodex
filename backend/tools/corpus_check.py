"""Run the load -> repickle -> reload -> compare gate over a whole save library.

This is the project's primary correctness signal. Unit tests can only cover the
formats we thought of; the local library covers Ren'Py 7.3 through 8.6 across
hundreds of games that each pickle their own bespoke classes.

Usage::

    python tools/corpus_check.py                 # default roots, 2 saves each
    python tools/corpus_check.py --per-dir 5     # sample more per game
    python tools/corpus_check.py --verbose       # list every failure
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodex.engines.renpy import save as renpy_save  # noqa: E402

DEFAULT_ROOTS = [
    Path(os.path.expanduser("~")) / "AppData/Roaming/RenPy",
    Path("D:/Games/Games Saves/Saves"),
]


def find_saves(roots: list[Path], per_dir: int) -> list[Path]:
    """Collect up to `per_dir` save files from each directory containing any."""
    by_dir: dict[Path, list[Path]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.save"):
            by_dir.setdefault(path.parent, []).append(path)

    picked: list[Path] = []
    for _, paths in sorted(by_dir.items()):
        picked.extend(sorted(paths)[:per_dir])
    return picked


def check_one(path: Path) -> tuple[str, str]:
    """Return (outcome, detail) for a single save file."""
    try:
        save = renpy_save.load(path)
    except Exception as exc:  # noqa: BLE001
        # Other engines also use the .save extension - a Godot/Dialogic game in
        # the local library writes plain JSON settings to SettingsData.save.
        # Those are not Ren'Py failures and must not count against the rate.
        if not zipfile.is_zipfile(path):
            return "skipped_not_renpy", f"{path.name} is not a zip archive"
        return "load_failed", f"{type(exc).__name__}: {exc}"

    try:
        data = renpy_save.serialize_log(save)
    except Exception as exc:  # noqa: BLE001
        return "pickle_failed", f"{type(exc).__name__}: {exc}"

    try:
        renpy_save.verify_roundtrip(save, data)
    except Exception as exc:  # noqa: BLE001
        return "roundtrip_failed", str(exc)

    return "ok", f"py{save.python_major} roots={len(save.roots)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-dir", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="stop after N saves")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("roots", nargs="*", type=Path)
    args = parser.parse_args()

    roots = args.roots or DEFAULT_ROOTS
    saves = find_saves(roots, args.per_dir)
    if args.limit:
        saves = saves[: args.limit]

    print(f"Checking {len(saves)} save files from {len(roots)} root(s)\n")

    outcomes: Counter[str] = Counter()
    details: Counter[str] = Counter()
    failures: list[tuple[Path, str, str]] = []

    for index, path in enumerate(saves, 1):
        outcome, detail = check_one(path)
        outcomes[outcome] += 1
        if outcome not in ("ok", "skipped_not_renpy"):
            # Collapse to the first line so similar causes group together.
            details[detail.splitlines()[0][:120]] += 1
            failures.append((path, outcome, detail))
        if index % 100 == 0 or index == len(saves):
            failed = index - outcomes["ok"] - outcomes["skipped_not_renpy"]
            print(f"  {index}/{len(saves)}  ok={outcomes['ok']} failed={failed}")

    ok = outcomes["ok"]
    skipped = outcomes["skipped_not_renpy"]
    total = sum(outcomes.values()) - skipped

    print("\n" + "=" * 70)
    if total:
        print(f"RESULT  {ok}/{total} Ren'Py saves passed ({ok / total * 100:.1f}%)")
    else:
        print("no Ren'Py saves found")
    if skipped:
        print(f"  skipped (not Ren'Py saves): {skipped}")
    for name, count in outcomes.most_common():
        if name not in ("ok", "skipped_not_renpy"):
            print(f"  {name}: {count}")

    if details:
        print("\nMost common failure causes:")
        for detail, count in details.most_common(12):
            print(f"  {count:4d}  {detail}")

    if args.verbose and failures:
        print("\nFailing files:")
        for path, outcome, detail in failures[:60]:
            print(f"  [{outcome}] {path}\n        {detail.splitlines()[0][:150]}")

    return 0 if ok == total else 1





if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        traceback.print_exc()
        raise SystemExit(130)
