"""Decode and re-encode every RPG Maker save we can find.

The RPG Maker counterpart to `corpus_check.py`. It answers one question: if a
user edits a save, will what we write back still be the save they had?

The bar is semantic rather than byte-for-byte. The LZString build inside MV
emits a few extra trailing zero bits that the decompressor ignores, so our
output is legitimately a handful of characters shorter while decoding to
identical JSON. Insisting on identical bytes would fail on most of the corpus
for no real reason - see the note in `codec.py`.

Usage::

    python tools/rpg_corpus_check.py
    python tools/rpg_corpus_check.py "D:/Games/Games Saves"
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodex.core.errors import NodexError  # noqa: E402
from nodex.engines.rpgmaker import codec  # noqa: E402

DEFAULT_ROOTS = [Path("D:/Games/Games Saves"), Path("D:/Games")]
SUFFIXES = ("*.rpgsave", "*.rmmzsave")


def main() -> int:
    roots = [Path(argument) for argument in sys.argv[1:]] or DEFAULT_ROOTS
    roots = [root for root in roots if root.is_dir()]
    if not roots:
        print("No save directories found.")
        return 2

    files: list[Path] = []
    for root in roots:
        for pattern in SUFFIXES:
            files.extend(root.rglob(pattern))
    files = sorted(set(files))

    print(f"Checking {len(files)} RPG Maker save files")

    passed = 0
    identical_bytes = 0
    flavours: Counter[str] = Counter()
    failures: list[tuple[Path, str]] = []

    for index, path in enumerate(files, start=1):
        try:
            decoded = codec.load(path)
            flavours[decoded.flavour] += 1

            encoded = codec.encode(decoded.data, decoded.flavour)
            codec.verify_roundtrip(decoded, encoded)

            if encoded == decoded.original:
                identical_bytes += 1
            passed += 1
        except NodexError as exc:
            failures.append((path, f"{type(exc).__name__}: {exc}"))
        except Exception as exc:  # noqa: BLE001
            failures.append((path, f"{type(exc).__name__}: {exc}"))

        if index % 200 == 0:
            print(f"  {index}/{len(files)}  ok={passed} failed={len(failures)}")

    print("\n" + "=" * 70)
    total = len(files)
    percent = (100.0 * passed / total) if total else 0.0
    print(f"RESULT  {passed}/{total} RPG Maker saves round-tripped ({percent:.1f}%)")
    print(f"        {identical_bytes} were also byte-identical")
    print(f"        flavours: {dict(flavours)}")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        grouped: Counter[str] = Counter(reason for _, reason in failures)
        for reason, count in grouped.most_common(10):
            print(f"  {count:5d}  {reason[:110]}")
        print("\nfirst few:")
        for path, reason in failures[:5]:
            print(f"  {path}\n      {reason[:150]}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
