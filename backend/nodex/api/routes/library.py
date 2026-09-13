"""The game library: discovered save folders, user-added roots, recents, thumbnails."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ...core import appstate
from ...core.errors import UnsupportedGame
from ...core.registry import holds_unity_saves, localow_root, detect
from ...engines.renpy import locate as renpy_locate
from ...engines.renpy import save as renpy_save
from ..common import require_path

router = APIRouter(prefix="/api")

#: Deep enough for `Games/<Title>/<Version>`, shallow enough to stay fast.
ROOT_SCAN_DEPTH = 2
_discovered: list[dict[str, Any]] = []


class RootRequest(BaseModel):
    path: str


def _normalise(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.split(r"[-_]\d", title)[0].lower())


def _renpy_save_folders() -> list[dict[str, Any]]:
    base = renpy_locate.renpy_appdata_root()
    if base is None or not base.is_dir():
        return []
    found = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name in {"tokens", "persistent"}:
            continue
        try:
            saves = [p for p in child.glob("*.save")]
        except OSError:
            continue
        if not saves:
            continue
        found.append({
            "kind": "save_folder",
            "engine": "renpy",
            "title": child.name.rsplit("-", 1)[0] if child.name.rsplit("-", 1)[-1].isdigit() else child.name,
            "path": str(child),
            "save_count": len(saves),
            "modified": max(p.stat().st_mtime for p in saves),
            "source": "appdata",
        })
    return found


def _unity_save_folders() -> list[dict[str, Any]]:
    base = localow_root()
    if not base.is_dir():
        return []
    found = []
    for company in sorted(base.iterdir()):
        if not company.is_dir():
            continue
        try:
            products = [p for p in company.iterdir() if p.is_dir()]
        except OSError:
            continue
        for product in products:
            try:
                candidates = [product] + [c for c in product.iterdir() if c.is_dir()]
                holder = next((c for c in candidates if holds_unity_saves(c)), None)
            except OSError:
                continue
            if holder is None:
                continue
            found.append({
                "kind": "save_folder",
                "engine": "unity",
                "title": product.name,
                "path": str(holder),
                "save_count": None,
                "modified": holder.stat().st_mtime,
                "source": "locallow",
            })
    return found


def _games_under(root: Path, depth: int = ROOT_SCAN_DEPTH) -> list[dict[str, Any]]:
    try:
        info = detect(root)
    except (UnsupportedGame, OSError):
        info = None
    # Detection tolerates one level of nesting (`Foo/Foo/game`), so a folder of
    # games can "detect" as its first child. Only accept a match for this folder
    # itself, or for a lone wrapper folder around exactly one game.
    if info is not None and info.root.resolve() != root.resolve():
        children = [c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")]
        if len(children) != 1:
            info = None
    if info is not None and (info.game_dir is not None or info.engine != "unity"):
        return [{
            "kind": "game",
            "engine": info.engine,
            "title": info.title,
            "path": str(info.root),
            "game_id": appstate.game_id(info.root),
            "version": ".".join(map(str, info.version)) if info.version else None,
            "modified": root.stat().st_mtime,
            "source": "library",
        }]
    if depth <= 0:
        return []
    games = []
    try:
        children = sorted(c for c in root.iterdir() if c.is_dir() and not c.name.startswith("."))
    except OSError:
        return []
    for child in children:
        games.extend(_games_under(child, depth - 1))
    return games


@router.get("/library")
def library() -> dict[str, Any]:
    state = appstate.load()
    return {
        "roots": state["library_roots"],
        "recents": state["recents"],
        "discovered": _discovered,
    }


@router.post("/library/scan")
def library_scan() -> dict[str, Any]:
    state = appstate.load()
    found: list[dict[str, Any]] = []
    for root in state["library_roots"]:
        path = Path(root)
        if path.is_dir():
            found.extend(_games_under(path))

    # A game found in a library folder already leads to its AppData saves.
    game_keys = {_normalise(item["title"]) for item in found if item["kind"] == "game"}
    for folder in _renpy_save_folders():
        key = _normalise(folder["title"])
        if not any(len(key) >= 4 and (key in g or g in key) for g in game_keys if len(g) >= 4):
            found.append(folder)
    found.extend(_unity_save_folders())

    seen: set[str] = set()
    unique = []
    for item in found:
        key = item["path"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    _discovered[:] = unique
    return library()


@router.post("/library/roots")
def add_root(request: RootRequest) -> dict[str, Any]:
    path = require_path(request.path)
    if not path.is_dir():
        raise HTTPException(400, "Not a directory")
    resolved = str(path.resolve())

    def mutate(state: dict[str, Any]) -> None:
        if resolved not in state["library_roots"]:
            state["library_roots"].append(resolved)

    appstate.update(mutate)
    return library_scan()


@router.delete("/library/roots")
def remove_root(request: RootRequest) -> dict[str, Any]:
    target = request.path.lower()

    def mutate(state: dict[str, Any]) -> None:
        state["library_roots"] = [r for r in state["library_roots"] if r.lower() != target]

    appstate.update(mutate)
    return library_scan()


@router.delete("/library/recents")
def clear_recents() -> dict[str, Any]:
    appstate.update(lambda state: state.update(recents=[]))
    return library()


@router.get("/save/thumbnail")
def save_thumbnail(path: str, request: Request) -> Response:
    resolved = require_path(path)
    etag = f'"{int(resolved.stat().st_mtime_ns)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    try:
        with zipfile.ZipFile(resolved) as archive:
            data = archive.read(renpy_save.SCREENSHOT_MEMBER)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise HTTPException(404, "No thumbnail in this save") from exc
    return Response(
        content=data,
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": "private, max-age=60"},
    )
