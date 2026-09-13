"""HTTP API backing the Nodex UI.

Bound to loopback only. The browser is just the view layer - it never receives
game files, only the parsed metadata it needs to render. Folder selection uses
a native dialog on this machine rather than a browser upload, because a game
folder is routinely several gigabytes and does not need to move anywhere.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import appstate
from ..core.apphome import static_dir
from ..core.errors import NodexError, UnsupportedGame
from ..core.registry import detect
from ..engines.renpy import analyze as renpy_analyze
from ..engines.renpy import gallery as renpy_gallery
from ..engines.renpy import locate as renpy_locate
from ..engines.renpy import persistent as renpy_persistent
from ..engines.renpy import repair as renpy_repair
from ..engines.renpy import save as renpy_save
from ..engines.renpy import variables as renpy_vars
from ..engines.renpy import walkthrough as renpy_walkthrough
from ..engines.rpgmaker import codec as rpg_codec
from ..engines.rpgmaker import variables as rpg_vars
from ..engines.unity import codec as unity_codec
from ..engines.unity import variables as unity_vars
from . import cache
from .common import RPGMAKER_SUFFIXES, UNITY_SUFFIXES, engine_of, jsonable, load_save, require_path
from .editing import EditFailure, apply_edit, entries_for_diff, record

app = FastAPI(title="Nodex", version="0.1.0")

#: Set by the desktop shell; when present every API call must carry it, so other
#: local processes and web pages cannot drive a server that rewrites files.
API_TOKEN = os.environ.get("NODEX_TOKEN") or None
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host and host not in ALLOWED_HOSTS and host != "testserver":
        return JSONResponse({"detail": "Host not allowed"}, status_code=403)
    if API_TOKEN and request.url.path.startswith("/api/") and request.method != "OPTIONS":
        supplied = request.headers.get("x-nodex-token") or request.query_params.get("token") or ""
        if not secrets.compare_digest(supplied, API_TOKEN):
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)
    return await call_next(request)


# Added after the guard so it wraps it and answers preflights first.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Wire models
# --------------------------------------------------------------------------


class PathRequest(BaseModel):
    path: str


class Change(BaseModel):
    name: str
    value: Any


class EditRequest(BaseModel):
    path: str
    changes: list[Change]
    dest: str | None = None
    #: Off only for diagnostics; leaving it on is what makes editing safe.
    verify: bool = True


class DiffRequest(BaseModel):
    left: str
    right: str


class RepairRequest(BaseModel):
    path: str
    dest: str | None = None


class WalkthroughRequest(BaseModel):
    #: The game folder, or its `game/` subfolder.
    path: str
    colour: str = "#7fdc7f"
    #: Export only: write the document here (chosen in a native Save As dialog).
    dest: str | None = None


class SaveLocationResponse(BaseModel):
    path: str
    source: str
    save_count: int


class GameResponse(BaseModel):
    root: str
    engine: str
    version: str | None
    version_name: str | None
    python_major: int
    title: str
    game_dir: str | None = None
    save_dirs: list[str] = Field(default_factory=list)
    save_locations: list[SaveLocationResponse] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


_require_path = require_path
_jsonable = jsonable
_load_save = load_save
_engine_of = engine_of


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/browse")
def browse() -> dict[str, str | None]:
    """Open a native folder picker on the machine running the backend."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as exc:
        raise HTTPException(501, f"No native dialog available: {exc}") from exc

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(title="Select a game folder")
    finally:
        root.destroy()

    return {"path": chosen or None}


@app.post("/api/game/detect", response_model=GameResponse)
def game_detect(request: PathRequest) -> GameResponse:
    path = _require_path(request.path)
    try:
        info = detect(path)
    except UnsupportedGame as exc:
        raise HTTPException(422, str(exc)) from exc

    # A Ren'Py game normally saves into %APPDATA%/RenPy/<config.save_directory>,
    # not into its own folder, so the real location has to be recovered from the
    # scripts. This can take a moment when the scripts live inside an archive.
    locations = []
    if info.engine == "renpy" and info.game_dir is not None:
        try:
            locations = renpy_locate.find_save_locations(
                info.game_dir, info.title, info.python_major
            )
        except Exception:  # noqa: BLE001 - discovery is best-effort
            locations = []

    try:
        appstate.add_recent({
            "root": str(info.root),
            "title": info.title,
            "engine": info.engine,
            "game_id": appstate.game_id(info.root),
        })
    except OSError:
        pass

    return GameResponse(
        root=str(info.root),
        engine=info.engine,
        version=".".join(map(str, info.version)) if info.version else None,
        version_name=info.version_name,
        python_major=info.python_major,
        title=info.title,
        game_dir=str(info.game_dir) if info.game_dir else None,
        save_dirs=[str(p) for p in info.save_dirs],
        save_locations=[
            SaveLocationResponse(
                path=str(location.path),
                source=location.source,
                save_count=location.save_count,
            )
            for location in locations
        ],
    )


@app.get("/api/saves")
def list_saves(path: str) -> dict[str, Any]:
    """List the save files in a directory, cheaply - metadata only.

    For Ren'Py the zip's `json` member is read without touching the pickle, so
    a folder of a hundred saves lists instantly. RPG Maker saves are listed
    without decompressing at all, because decoding LZString just to show a
    filename would make a large folder crawl.
    """
    import json as jsonlib
    import zipfile

    directory = _require_path(path)
    if not directory.is_dir():
        raise HTTPException(400, "Not a directory")

    known = RPGMAKER_SUFFIXES | UNITY_SUFFIXES
    candidates = sorted(
        {p for p in directory.glob("*.save")}
        | {
            p
            for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in known
        }
    )

    entries = []
    for candidate in candidates:
        engine = _engine_of(candidate)
        entry: dict[str, Any] = {
            "path": str(candidate),
            "name": candidate.stem,
            "engine": engine,
            "size": candidate.stat().st_size,
            "modified": candidate.stat().st_mtime,
            "save_name": None,
            "renpy_version": None,
            "readable": True,
        }

        if engine == "renpy":
            try:
                with zipfile.ZipFile(candidate) as archive:
                    meta = jsonlib.loads(archive.read("json").decode("utf-8"))
                entry["save_name"] = meta.get("_save_name") or None
                version = meta.get("_renpy_version")
                if isinstance(version, list):
                    entry["renpy_version"] = ".".join(map(str, version))
            except Exception:  # noqa: BLE001 - a broken save still gets listed
                entry["readable"] = False
        elif engine == "rpgmaker":
            entry["save_name"] = (
                "MZ" if candidate.suffix.lower() == ".rmmzsave" else "MV"
            )
        else:
            # Unity has no standard format, so the only honest label comes from
            # actually opening the file. These are small, and a folder holds a
            # handful rather than hundreds.
            try:
                entry["save_name"] = unity_codec.load(candidate).flavour
            except unity_codec.EncryptedSave:
                entry["save_name"] = "encrypted"
                entry["readable"] = False
            except Exception:  # noqa: BLE001
                entry["readable"] = False

        entries.append(entry)

    return {"directory": str(directory), "saves": entries}


def _rpgmaker_variables(path: Path, include_internal: bool) -> dict[str, Any]:
    """The RPG Maker equivalent of the Ren'Py variable table.

    `include_internal` doubles as "show switches and variables the game has
    never set", which is the closest analogue - a project declares hundreds and
    uses a handful.
    """
    try:
        decoded = rpg_codec.load(path)
    except NodexError as exc:
        raise HTTPException(422, str(exc)) from exc

    names = None
    game_dir = _nearby_game_dir(path)
    if game_dir is not None:
        names = rpg_vars.load_names(game_dir)

    rows = rpg_vars.list_entries(
        decoded.data, names=names, include_unset=include_internal
    )

    return {
        "path": str(path),
        "engine": "rpgmaker",
        "save_name": decoded.flavour.upper(),
        "renpy_version": None,
        "python_major": None,
        "named": names is not None,
        "total": len(rows),
        "variables": [
            {
                "name": row.address,
                "short_name": row.display,
                "kind": row.kind,
                "value": _jsonable(row.value),
                "preview": str(row.value),
                "editable": True,
                "internal": False,
                "group": row.group,
            }
            for row in rows
        ],
    }


def _nearby_game_dir(save_path: Path) -> Path | None:
    """Find the game install a save belongs to, if it sits inside one.

    Saves live at `<game>/www/save/` or `<game>/save/`, so the game root is one
    or two levels up. Save collections kept away from their game simply return
    None and the table falls back to numeric labels.
    """
    for parent in list(save_path.parents)[:3]:
        for base in (parent / "www", parent):
            if (base / "data" / "System.json").is_file():
                return base
    return None


def _unity_variables(path: Path) -> dict[str, Any]:
    """The Unity equivalent of the Ren'Py variable table."""
    try:
        decoded = unity_codec.load(path)
    except unity_codec.EncryptedSave as exc:
        raise HTTPException(422, str(exc)) from exc
    except NodexError as exc:
        raise HTTPException(422, str(exc)) from exc

    rows = unity_vars.list_entries(decoded)

    return {
        "path": str(path),
        "engine": "unity",
        "save_name": decoded.flavour,
        "renpy_version": None,
        "python_major": None,
        "total": len(rows),
        "variables": [
            {
                "name": row.address,
                "short_name": row.label,
                "kind": row.kind,
                "value": _jsonable(row.value),
                "preview": str(row.value),
                "editable": row.editable,
                "internal": False,
                "group": row.group,
            }
            for row in rows
        ],
    }


@app.get("/api/save/variables")
def save_variables(path: str, include_internal: bool = False) -> dict[str, Any]:
    resolved = _require_path(path)
    engine = _engine_of(resolved)
    if engine == "rpgmaker":
        return _rpgmaker_variables(resolved, include_internal)
    if engine == "unity":
        return _unity_variables(resolved)

    save = _load_save(resolved)
    rows = renpy_vars.list_variables(save.roots, include_internal=include_internal)

    return {
        "path": str(save.path),
        "save_name": save.save_name,
        "renpy_version": ".".join(map(str, save.renpy_version)) if save.renpy_version else None,
        "python_major": save.python_major,
        "total": len(save.roots),
        "variables": [
            {
                "name": row.name,
                "short_name": row.short_name,
                "kind": row.kind,
                "value": _jsonable(row.value),
                "preview": row.preview,
                "editable": row.editable,
                "internal": row.internal,
            }
            for row in rows
        ],
    }


@app.post("/api/save/edit")
def save_edit(request: EditRequest) -> dict[str, Any]:
    """Apply edits and write the save, refusing if the round trip is unsafe."""
    path = _require_path(request.path)
    try:
        result = apply_edit(
            path,
            [(c.name, c.value) for c in request.changes],
            dest=Path(request.dest) if request.dest else None,
            verify=request.verify,
        )
    except EditFailure as exc:
        raise HTTPException(exc.code, str(exc)) from exc

    batch_id = record([result], source="edit")
    return {
        "written": str(result.written),
        "applied": [{"name": a["name"], "value": a["value"]} for a in result.applied],
        "verified": request.verify,
        "batch_id": batch_id,
        "backup": str(result.backups[0]) if result.backups else None,
    }


@app.post("/api/save/diagnose")
def save_diagnose(request: PathRequest) -> dict[str, Any]:
    path = _require_path(request.path)
    diagnosis = renpy_repair.diagnose(path)
    return _diagnosis_payload(diagnosis)


@app.post("/api/save/repair")
def save_repair(request: RepairRequest) -> dict[str, Any]:
    path = _require_path(request.path)
    try:
        written, diagnosis = renpy_repair.repair(path, request.dest)
    except NodexError as exc:
        raise HTTPException(422, str(exc)) from exc
    cache.saves.invalidate(path)
    payload = _diagnosis_payload(diagnosis)
    payload["written"] = str(written)
    return payload


@app.post("/api/save/diff")
def save_diff(request: DiffRequest) -> dict[str, Any]:
    left_path = _require_path(request.left)
    right_path = _require_path(request.right)
    left_engine, right_engine = _engine_of(left_path), _engine_of(right_path)
    if left_engine != right_engine:
        raise HTTPException(400, f"Cannot compare a {left_engine} save with a {right_engine} save")

    if left_engine != "renpy":
        try:
            changes = entries_for_diff(left_path, right_path)
        except EditFailure as exc:
            raise HTTPException(exc.code, str(exc)) from exc
        return {"left": str(left_path), "right": str(right_path), "engine": left_engine, "changes": changes}

    left = _load_save(left_path)
    right = _load_save(right_path)

    changes = renpy_vars.diff(left.roots, right.roots)
    return {
        "left": str(left.path),
        "right": str(right.path),
        "engine": "renpy",
        "changes": [
            {
                "name": name,
                "before": _jsonable(before) if not isinstance(before, renpy_vars._Absent) else None,
                "after": _jsonable(after) if not isinstance(after, renpy_vars._Absent) else None,
                "before_missing": isinstance(before, renpy_vars._Absent),
                "after_missing": isinstance(after, renpy_vars._Absent),
            }
            for name, before, after in changes
            if not renpy_vars.is_internal(name)
        ],
    }


# --------------------------------------------------------------------------
# Walkthrough
# --------------------------------------------------------------------------

#: Analysis reads every script in a game, which takes seconds on a large one.
#: The UI asks for it repeatedly (view, install, export), so hold the result.
_analysis_cache: dict[str, tuple[float, Any]] = {}


def _resolve_game_dir(raw: str) -> tuple[Path, int, str]:
    """Accept either a game folder or its `game/` subfolder."""
    path = _require_path(raw)
    if (path / "game").is_dir() or not (path.name == "game"):
        try:
            info = detect(path)
        except UnsupportedGame as exc:
            raise HTTPException(422, str(exc)) from exc
        if info.engine != "renpy":
            raise HTTPException(422, f"{info.engine} games are not supported yet")
        if info.game_dir is None:
            raise HTTPException(422, "Could not find the game's script folder")
        return info.game_dir, info.python_major, info.title
    return path, 3, path.parent.name


def _get_analysis(raw: str):
    game_dir, python_major, title = _resolve_game_dir(raw)
    key = str(game_dir)

    cached = _analysis_cache.get(key)
    stamp = game_dir.stat().st_mtime
    if cached and cached[0] == stamp:
        return cached[1], game_dir, title

    analysis = renpy_analyze.analyze(game_dir, python_major)
    _analysis_cache[key] = (stamp, analysis)
    return analysis, game_dir, title


@app.post("/api/walkthrough/analyze")
def walkthrough_analyze(request: WalkthroughRequest) -> dict[str, Any]:
    """Map every choice in the game. Nothing is written."""
    analysis, game_dir, title = _get_analysis(request.path)

    return {
        "game_dir": str(game_dir),
        "title": title,
        "scripts_read": analysis.scripts_read,
        "scripts_failed": analysis.scripts_failed,
        "total_menus": len(analysis.menus),
        "meaningful_menus": len(analysis.meaningful_menus),
        "variables": [
            {"name": name, "count": count}
            for name, count in analysis.tracked_variables[:60]
        ],
        # Every menu is returned, not only the ones that touch a variable. A
        # choice that merely leads to different dialogue is still a choice the
        # player is making, and hiding it would drop most of some games.
        "menus": [
            {
                "key": menu.key,
                "label": menu.label,
                "filename": menu.filename,
                "line": menu.linenumber,
                "meaningful": menu.meaningful,
                "choices": [
                    {
                        "index": choice.index,
                        "caption": choice.caption,
                        "condition": choice.condition,
                        "effects": [e.describe() for e in choice.effects],
                        "routes": choice.jumps + choice.calls,
                        "downstream": [e.describe() for e in choice.downstream],
                        "inert": choice.inert,
                    }
                    for choice in menu.choices
                ],
            }
            for menu in analysis.menus
            if menu.choices
        ],
        "mod_installed": (game_dir / renpy_walkthrough.MOD_FILENAME).exists(),
    }


@app.post("/api/walkthrough/install")
def walkthrough_install(request: WalkthroughRequest) -> dict[str, Any]:
    """Write the annotating mod into the game folder."""
    analysis, game_dir, _ = _get_analysis(request.path)
    written = renpy_walkthrough.install_mod(analysis, game_dir, request.colour)
    return {
        "written": str(written),
        "annotated_menus": len(renpy_walkthrough.build_annotations(analysis)),
    }


@app.post("/api/walkthrough/uninstall")
def walkthrough_uninstall(request: WalkthroughRequest) -> dict[str, Any]:
    game_dir, _, _ = _resolve_game_dir(request.path)
    removed = renpy_walkthrough.uninstall_mod(game_dir)
    return {"removed": [str(path) for path in removed]}


@app.post("/api/walkthrough/export")
def walkthrough_export(request: WalkthroughRequest) -> Response:
    """Return the standalone HTML walkthrough as a download."""
    analysis, _, title = _get_analysis(request.path)
    document = renpy_walkthrough.build_html(analysis, title)
    if request.dest:
        destination = Path(request.dest)
        if destination.suffix.lower() not in {".html", ".htm"}:
            raise HTTPException(400, "Walkthrough exports must be .html files")
        destination.write_text(document, encoding="utf-8")
        return JSONResponse({"written": str(destination)})
    return Response(
        content=document,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="walkthrough.html"',
        },
    )


# --------------------------------------------------------------------------
# Gallery
# --------------------------------------------------------------------------


class GalleryRequest(BaseModel):
    path: str


class UnlockRequest(BaseModel):
    path: str
    #: Flags to open. Empty means "everything Nodex suggested".
    flags: list[str] = Field(default_factory=list)
    #: Also record every image the game declares as seen. Satisfies Ren'Py's
    #: built-in Gallery unlock conditions, but writes a lot of entries.
    mark_images: bool = False


def _gallery_report(raw: str):
    game_dir, python_major, title = _resolve_game_dir(raw)
    report = renpy_gallery.scan(game_dir, python_major)
    persistent_path = renpy_gallery.find_persistent(game_dir, title, python_major)

    if persistent_path is not None:
        try:
            renpy_gallery.attach_current_values(
                report, renpy_persistent.load(persistent_path, python_major)
            )
        except NodexError:
            persistent_path = None

    return report, persistent_path, game_dir, python_major


def _flag_payload(flag: renpy_gallery.Flag) -> dict[str, Any]:
    return {
        "name": flag.name,
        "kind": flag.kind,
        "value_kind": flag.value_kind,
        "references": flag.references,
        "present": flag.present,
        "locked": flag.locked,
        "suggested": flag.suggested,
        "enumerable": flag.enumerable,
        "is_collection": flag.is_collection,
        "members": flag.members[:50],
        "existing_members": flag.existing_members[:50],
        "current": _jsonable(flag.current),
    }


@app.post("/api/gallery/scan")
def gallery_scan(request: GalleryRequest) -> dict[str, Any]:
    """Report every gallery lock found in the game. Nothing is written."""
    report, persistent_path, _, _ = _gallery_report(request.path)

    return {
        "persistent": str(persistent_path) if persistent_path else None,
        "scripts_read": report.scripts_read,
        "uses_builtin_gallery": report.uses_builtin_gallery,
        "image_count": len(report.images),
        "seen_images_recorded": report.seen_images_recorded,
        "flags": [_flag_payload(flag) for flag in report.flags],
        "suggested": [flag.name for flag in report.suggested],
        "locked": [flag.name for flag in report.locked_suggested],
        "needs_manual": [_flag_payload(flag) for flag in report.needs_manual],
    }


@app.post("/api/gallery/unlock")
def gallery_unlock(request: UnlockRequest) -> dict[str, Any]:
    """Open the selected locks in the player's persistent file."""
    report, persistent_path, _, python_major = _gallery_report(request.path)

    if persistent_path is None:
        raise HTTPException(
            422,
            "No persistent file found for this game - launch it once so Ren'Py creates one.",
        )

    chosen = request.flags or [flag.name for flag in report.suggested]
    members = {flag.name: flag.members for flag in report.flags if flag.members}
    images = report.images if request.mark_images else None

    try:
        result = renpy_gallery.unlock(
            persistent_path,
            chosen,
            images=images,
            python_major=python_major,
            members=members,
        )
    except NodexError as exc:
        raise HTTPException(409, str(exc)) from exc

    return {
        "flags_set": result.flags_set,
        "images_marked": result.images_marked,
        "written": str(result.written) if result.written else None,
    }


def _diagnosis_payload(diagnosis: renpy_repair.Diagnosis) -> dict[str, Any]:
    return {
        "path": str(diagnosis.path),
        "healthy": diagnosis.healthy,
        "recoverable": diagnosis.recoverable,
        "confidence": diagnosis.confidence,
        "summary": diagnosis.summary(),
        "problems": diagnosis.problems,
        "steps": [
            {"name": step.name, "ok": step.ok, "detail": step.detail}
            for step in diagnosis.steps
        ],
        "variables_recovered": len(diagnosis.roots) if diagnosis.roots else 0,
    }


def mount_frontend(app: FastAPI) -> None:
    """Serve the built UI from this origin, when a build exists."""
    dist = static_dir()
    if dist is None:
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")


from .routes import backups, graph, history, library  # noqa: E402

for module in (library, backups, history, graph):
    app.include_router(module.router)

mount_frontend(app)
