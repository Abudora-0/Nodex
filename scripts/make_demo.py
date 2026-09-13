"""Build a small, original demo library so Nodex can be tried without any games.

    python scripts/make_demo.py demo
    python scripts/serve_demo.py demo      # runs Nodex against that library

Creates:
  demo/games/The Lighthouse Letter/     a Ren'Py game with compiled scripts
  demo/games/Lanterns of Vell/          an RPG Maker MV game with saves
  demo/appdata/RenPy/...                where the Ren'Py game keeps its saves
  demo/profile/AppData/LocalLow/...     a Unity (Naninovel) save folder
"""

from __future__ import annotations

import io
import json
import os
import pickle
import struct
import sys
import time
import types
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from nodex.engines.rpgmaker import codec as rpg_codec  # noqa: E402
from nodex.engines.rpgmaker import lzstring  # noqa: E402

SAVE_DIRECTORY = "LighthouseLetter-1790000000"
DAY = 86400


# ---------------------------------------------------------------------------
# Tiny PNG writer for save thumbnails
# ---------------------------------------------------------------------------


def png(width: int, height: int, pixel) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def scene(sky_top, sky_bottom, sea, glow: bool, moon: bool) -> bytes:
    w, h = 320, 200
    horizon = 128

    def pixel(x, y):
        if 150 <= x <= 166 and 70 <= y <= horizon + 6:
            return (236, 230, 214) if (y // 12) % 2 == 0 else (201, 67, 42)
        if 146 <= x <= 170 and 60 <= y < 70:
            return (40, 40, 44)
        if glow:
            d = ((x - 158) ** 2 + (y - 64) ** 2) ** 0.5
            if d < 34:
                base = lerp(sky_top, sky_bottom, y / horizon) if y < horizon else sea
                return lerp(base, (255, 214, 130), max(0.0, 1 - d / 34) ** 1.6)
        if moon and ((x - 262) ** 2 + (y - 38) ** 2) < 150:
            return (238, 232, 210)
        if y < horizon:
            return lerp(sky_top, sky_bottom, y / horizon)
        shade = 0.75 + 0.25 * ((x // 9 + y // 3) % 2)
        return tuple(int(c * shade) for c in sea)

    return png(w, h, pixel)


# ---------------------------------------------------------------------------
# Ren'Py: compiled scripts
# ---------------------------------------------------------------------------

ast = types.ModuleType("renpy.ast")
sys.modules.setdefault("renpy", types.ModuleType("renpy"))
sys.modules["renpy.ast"] = ast


def _node(name: str):
    cls = type(name, (), {"__module__": "renpy.ast"})
    cls.__qualname__ = name
    setattr(ast, name, cls)
    return cls


Label, Menu, Jump, Call, Python, Define, Say = (_node(n) for n in ("Label", "Menu", "Jump", "Call", "Python", "Define", "Say"))


def make(cls, **attrs):
    obj = cls()
    obj.__dict__.update(attrs)
    return obj


def label(name, *block):
    return make(Label, name=name, _name=name, block=list(block), parameters=None)


def menu(filename, line, *items):
    return make(Menu, filename=filename, linenumber=line, items=list(items), set=None, with_=None)


def option(caption, condition="True", *block):
    return (caption, condition, list(block))


def py(source):
    return make(Python, code=source, hide=False)


def jump(target):
    return make(Jump, target=target, expression=False)


def call(target):
    return make(Call, label=target, expression=False, arguments=None)


def say(who, what):
    return make(Say, who=who, what=what)


def rpyc_bytes(nodes) -> bytes:
    payload = zlib.compress(pickle.dumps(({"version": 5003000, "key": "unlocked"}, nodes), protocol=2))
    offset = 10 + 12 * 2
    return b"RENPY RPC2" + struct.pack("<III", 1, offset, len(payload)) + struct.pack("<III", 0, 0, 0) + payload


def write_renpy_game(games: Path) -> Path:
    root = games / "The Lighthouse Letter"
    game = root / "game"
    (root / "renpy").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "py3-windows-x86_64").mkdir(parents=True, exist_ok=True)
    game.mkdir(parents=True, exist_ok=True)
    (root / "renpy" / "vc_version.py").write_text("version = '8.3.4.24120703'\nversion_name = 'Demo build'\n", encoding="utf-8")
    (game / "options.rpy").write_text(f'define config.name = "The Lighthouse Letter"\ndefine config.save_directory = "{SAVE_DIRECTORY}"\n', encoding="utf-8")

    script = [
        label(
            "start",
            say("m", "The letter was addressed to the keeper. The keeper has been gone for a year."),
            py("chapter = 1"),
            menu(
                "game/script.rpy", 14,
                option("Climb the lighthouse stairs", "True", py("courage += 1"), jump("tower")),
                option("Wait at the harbour for the ferry", "True", py("trust_tomas += 1"), jump("harbour")),
                option("Read the letter again", "True", py("letters_found += 1")),
            ),
        ),
        label(
            "tower",
            menu(
                "game/script.rpy", 41,
                option("Light the great lantern", "has_key", py("lantern_lit = True"), jump("signal")),
                option("Search the keeper's desk", "True", py("has_key = True\nletters_found += 1"), jump("tower")),
                option("Go back down to the harbour", "True", jump("harbour")),
            ),
        ),
        label(
            "harbour",
            menu(
                "game/script.rpy", 73,
                option("Ask Tomas about the night of the storm", "True", py("trust_tomas += 2")),
                option("Show Tomas the letters", "letters_found >= 2", py("trust_tomas += 1\ntrust_mira -= 1"), jump("ferry_ending")),
                option("Return to the lighthouse", "True", jump("tower")),
            ),
        ),
        label(
            "signal",
            menu(
                "game/script.rpy", 104,
                option("Signal the passing ship", "True", py("trust_mira += 2"), call("mira_memory"), jump("good_ending")),
                option("Let the lantern burn down", "True", py("lantern_lit = False"), jump("quiet_ending")),
            ),
        ),
        label("good_ending", say("m", "The ship turned toward the light.")),
        label("ferry_ending", say("t", "We leave with the tide.")),
        label("quiet_ending", say("m", "Some letters are meant to stay unread.")),
    ]

    chapter2 = [
        label(
            "mira_memory",
            menu(
                "game/chapter2.rpy", 9,
                option("Tell Mira the truth about the letter", "True", py("trust_mira += 3"), jump("letters_end")),
                option("Keep the last page to yourself", "True", py("letters_found += 1\ntrust_mira -= 1")),
            ),
        ),
        label(
            "letters_end",
            menu(
                "game/chapter2.rpy", 31,
                option("Burn the letters", "trust_mira >= 3", py("lantern_lit = False"), jump("quiet_ending")),
                option("Keep them in the tower", "True", jump("good_ending")),
            ),
        ),
    ]

    (game / "script.rpyc").write_bytes(rpyc_bytes(script))
    (game / "chapter2.rpyc").write_bytes(rpyc_bytes(chapter2))
    options = [make(Define, store="store.config", varname="save_directory", code=f'"{SAVE_DIRECTORY}"')]
    (game / "options.rpyc").write_bytes(rpyc_bytes(options))
    return root


# ---------------------------------------------------------------------------
# Ren'Py: saves
# ---------------------------------------------------------------------------

RENPY_SAVES = [
    ("1-1-LT1", "The letter arrives", 9, dict(chapter=1, courage=0, trust_mira=0, trust_tomas=0, letters_found=1, has_key=False, lantern_lit=False),
     ((196, 214, 226), (236, 214, 188), (74, 110, 128)), False, False),
    ("1-2-LT1", "Top of the tower", 7, dict(chapter=1, courage=1, trust_mira=1, trust_tomas=0, letters_found=2, has_key=True, lantern_lit=False),
     ((120, 150, 190), (230, 176, 140), (60, 88, 110)), False, False),
    ("1-3-LT1", "Tomas at the harbour", 5, dict(chapter=1, courage=1, trust_mira=0, trust_tomas=3, letters_found=2, has_key=True, lantern_lit=False),
     ((90, 110, 150), (200, 140, 130), (44, 70, 92)), False, False),
    ("1-4-LT1", "The lantern is lit", 2, dict(chapter=2, courage=2, trust_mira=2, trust_tomas=3, letters_found=3, has_key=True, lantern_lit=True),
     ((22, 30, 54), (58, 60, 96), (20, 34, 52)), True, True),
    ("1-5-LT1", "Before the storm", 0, dict(chapter=2, courage=3, trust_mira=5, trust_tomas=3, letters_found=4, has_key=True, lantern_lit=True),
     ((16, 20, 30), (44, 48, 70), (14, 24, 36)), True, False),
]


def write_renpy_saves(appdata: Path) -> Path:
    folder = appdata / "RenPy" / SAVE_DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for stem, name, days_ago, variables, palette, glow, moon in RENPY_SAVES:
        roots = {f"store.{k}": v for k, v in variables.items()}
        roots.update({"store.player_name": "Wren", "store._window": True, "store._last_say_who": "m"})
        log = [{"filler": list(range(200))} for _ in range(8)]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("log", pickle.dumps((roots, log), protocol=2))
            archive.writestr("json", json.dumps({"_save_name": name, "_renpy_version": [8, 3, 4, 24120703]}))
            archive.writestr("screenshot.png", scene(*palette, glow, moon))
            archive.writestr("extra_info", name)
            archive.writestr("renpy_version", "8.3.4.24120703")
        path = folder / f"{stem}.save"
        path.write_bytes(buffer.getvalue())
        stamp = now - days_ago * DAY - 3600 * (len(stem) % 5)
        os.utime(path, (stamp, stamp))
    return folder


# ---------------------------------------------------------------------------
# RPG Maker MV
# ---------------------------------------------------------------------------


def write_rpgmaker_game(games: Path) -> Path:
    root = games / "Lanterns of Vell"
    www = root / "www"
    (www / "data").mkdir(parents=True, exist_ok=True)
    (www / "js").mkdir(parents=True, exist_ok=True)
    (www / "save").mkdir(parents=True, exist_ok=True)
    (www / "js" / "rpg_core.js").write_text("// demo\n", encoding="utf-8")
    system = {
        "gameTitle": "Lanterns of Vell",
        "switches": ["", "Met the archivist", "Bridge repaired", "Lighthouse key", "Heard the bell", "Secret door open"],
        "variables": ["", "Lantern oil", "Reputation", "Days in Vell", "Letters delivered"],
    }
    (www / "data" / "System.json").write_text(json.dumps(system), encoding="utf-8")

    now = time.time()
    for index, (switches, variables, gold, days_ago) in enumerate(
        [
            ([None, True, False, False, None, None], [None, 3, 10, 2, 0], 240, 12),
            ([None, True, True, False, True, None], [None, 7, 25, 6, 3], 910, 4),
            ([None, True, True, True, True, False], [None, 12, 40, 9, 6], 1830, 1),
        ],
        start=1,
    ):
        save = {
            "system": {"@c": "Game_System", "_saveCount": index * 3},
            "switches": {"@c": "Game_Switches", "_data": {"@c": "Array", "@a": switches}},
            "variables": {"@c": "Game_Variables", "_data": {"@c": "Array", "@a": variables}},
            "party": {"@c": "Game_Party", "_gold": gold, "_steps": 400 * index},
        }
        path = www / "save" / f"file{index}.rpgsave"
        path.write_bytes(lzstring.compress_to_base64(rpg_codec.to_json(save)).encode("utf-8"))
        stamp = now - days_ago * DAY
        os.utime(path, (stamp, stamp))
    return root


# ---------------------------------------------------------------------------
# Unity / Naninovel
# ---------------------------------------------------------------------------


def naninovel_bytes(local: dict, global_map: dict) -> bytes:
    payload = {
        "LocalVariableMap": {"keys": list(local), "values": list(local.values())},
        "GlobalVariableMap": {"keys": list(global_map), "values": list(global_map.values())},
    }
    save = {
        "objectJsonMap": {
            "keys": ["Naninovel.CustomVariableManager+GameState, Elringus.Naninovel.Runtime"],
            "values": [json.dumps(payload, separators=(",", ":"))],
        }
    }
    text = "﻿" + json.dumps(save, separators=(",", ":"))
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(text.encode("utf-8")) + compressor.flush()


def write_unity_saves(profile: Path) -> Path:
    folder = profile / "AppData" / "LocalLow" / "Quiet Harbour Games" / "Orchard of Glass" / "Saves"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for index, days_ago in enumerate((6, 3, 0), start=1):
        local = {"affection_ivo": str(index * 2), "orchard_visits": str(index + 1), "glass_flower": "True" if index > 1 else "False"}
        global_map = {"g_route_ivo_unlocked": "True" if index == 3 else "False", "g_endings_seen": str(index - 1)}
        path = folder / f"GameSave{index}.nson"
        path.write_bytes(naninovel_bytes(local, global_map))
        stamp = now - days_ago * DAY
        os.utime(path, (stamp, stamp))
    return folder


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo").resolve()
    games = target / "games"
    games.mkdir(parents=True, exist_ok=True)
    renpy_game = write_renpy_game(games)
    saves = write_renpy_saves(target / "appdata")
    rpg = write_rpgmaker_game(games)
    unity = write_unity_saves(target / "profile")
    (target / "home").mkdir(exist_ok=True)
    print("Demo library written:")
    for path in (renpy_game, saves, rpg, unity):
        print("  ", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
