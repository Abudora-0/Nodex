"""Run Nodex against the demo library from make_demo.py, isolated from your real one.

    python scripts/make_demo.py demo
    python scripts/serve_demo.py demo          # then open http://127.0.0.1:8766
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    demo = Path(sys.argv[1] if len(sys.argv) > 1 else "demo").resolve()
    if not (demo / "games").is_dir():
        print("Run scripts/make_demo.py first.")
        return 1

    home = demo / "home"
    home.mkdir(exist_ok=True)
    state = home / "state.json"
    if not state.exists():
        state.write_text(json.dumps({"version": 1, "library_roots": [str(demo / "games")], "recents": [], "presets": [], "prefs": {}}))

    env = {
        **os.environ,
        "NODEX_HOME": str(home),
        "APPDATA": str(demo / "appdata"),
        "USERPROFILE": str(demo / "profile"),
    }
    port = sys.argv[2] if len(sys.argv) > 2 else "8766"
    print(f"Nodex demo on http://127.0.0.1:{port}  (build the UI first with: cd frontend && npm run build)")
    return subprocess.call([sys.executable, "-m", "nodex", "--port", port], cwd=ROOT / "backend", env=env)


if __name__ == "__main__":
    raise SystemExit(main())
