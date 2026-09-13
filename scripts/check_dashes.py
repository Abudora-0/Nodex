"""Fail if any project text file contains an em dash (U+2014)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "target", "__pycache__",
    ".pytest_cache", ".venv", "venv", "binaries", "gen", ".claude",
}
SKIP_NAMES = {"package-lock.json", "Cargo.lock"}
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".css", ".html", ".md",
    ".json", ".toml", ".yml", ".yaml", ".rs", ".ps1", ".sh", ".spec", ".txt",
    ".rpy", ".cfg", ".ini", "",
}


def candidate_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        files = [ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    return [
        p for p in files
        if p.is_file()
        and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts)
        and p.name not in SKIP_NAMES
        and p.suffix.lower() in TEXT_SUFFIXES
    ]


def find_dashes() -> list[str]:
    hits = []
    for path in candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if EM_DASH in line:
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    return hits


def main() -> int:
    hits = find_dashes()
    for hit in hits:
        print(hit)
    if hits:
        print(f"\n{len(hits)} line(s) contain an em dash (U+2014).")
        return 1
    print("No em dashes found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
