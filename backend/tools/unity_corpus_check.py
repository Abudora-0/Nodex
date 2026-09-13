"""Decode and re-encode every Unity save under `AppData/LocalLow`.

The Unity counterpart to the Ren'Py and RPG Maker corpus checkers. It also
reports how much of the library is *not* openable, because Unity has no
standard save format and a sizeable share of games encrypt theirs - an honest
coverage number matters more here than a pass rate.

Usage::

    python tools/unity_corpus_check.py
    python tools/unity_corpus_check.py "C:/path/to/LocalLow"
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodex.core.errors import NodexError  # noqa: E402
from nodex.engines.unity import codec  # noqa: E402

#: Unity's own asset cache lives here and holds no saves.
SKIP_PARTS = {"Unity"}


def default_root() -> Path:
    return Path(os.environ.get("USERPROFILE", "~")).expanduser() / "AppData" / "LocalLow"


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else default_root()
    if not root.is_dir():
        print(f"No such directory: {root}")
        return 2

    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in codec.SAVE_SUFFIXES
        and not SKIP_PARTS & set(path.parts)
    ]
    files.sort()

    print(f"Checking {len(files)} candidate Unity save files under {root}")

    passed = 0
    identical = 0
    encrypted = 0
    flavours: Counter[str] = Counter()
    failures: list[tuple[Path, str]] = []

    for index, path in enumerate(files, start=1):
        try:
            decoded = codec.load(path)
        except codec.EncryptedSave:
            encrypted += 1
            continue
        except NodexError as exc:
            failures.append((path, f"{type(exc).__name__}: {exc}"))
            continue

        flavours[decoded.flavour] += 1
        try:
            encoded = codec.encode(decoded)
            codec.verify_roundtrip(decoded, encoded)
            if encoded == decoded.original:
                identical += 1
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failures.append((path, f"{type(exc).__name__}: {exc}"))

        if index % 100 == 0:
            print(f"  {index}/{len(files)}  ok={passed} encrypted={encrypted}")

    openable = passed + len(failures)
    print("\n" + "=" * 70)
    percent = (100.0 * passed / openable) if openable else 0.0
    print(f"RESULT  {passed}/{openable} openable Unity saves round-tripped ({percent:.1f}%)")
    print(f"        {identical} were byte-identical")
    print(f"        flavours: {dict(flavours)}")
    print(
        f"        {encrypted} of {len(files)} files are encrypted or unrecognised "
        f"({100.0 * encrypted / len(files):.0f}% of the library)"
    )

    if failures:
        print(f"\n{len(failures)} failure(s):")
        grouped: Counter[str] = Counter(reason for _, reason in failures)
        for reason, count in grouped.most_common(10):
            print(f"  {count:5d}  {reason[:110]}")
        for path, reason in failures[:5]:
            print(f"  {path}\n      {reason[:150]}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
