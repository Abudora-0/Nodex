"""Bulk edits, presets, edit history and undo."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core import appstate, fsutil
from .. import cache
from ..editing import EditFailure, apply_edit, record, sha256_of

router = APIRouter(prefix="/api")


class Change(BaseModel):
    name: str
    value: Any


class BulkEditRequest(BaseModel):
    paths: list[str]
    changes: list[Change]
    dry_run: bool = False


class UndoRequest(BaseModel):
    entry_id: str | None = None
    batch_id: str | None = None
    force: bool = False


class Preset(BaseModel):
    id: str | None = None
    game_id: str
    name: str
    changes: list[Change] = Field(default_factory=list)


class ApplyPresetRequest(BaseModel):
    preset_id: str
    paths: list[str]
    dry_run: bool = False


def run_bulk(paths: list[str], changes: list[tuple[str, Any]], dry_run: bool, source: str) -> dict[str, Any]:
    """Each file is edited on its own, through its own round-trip gate.

    Not atomic across files: a rejected file is left untouched while the
    others are written.
    """
    results = []
    report = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            report.append({"path": raw, "status": "missing", "detail": "File does not exist"})
            continue
        try:
            result = apply_edit(path, changes, dry_run=dry_run)
        except EditFailure as exc:
            report.append({"path": raw, "status": exc.status, "detail": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            report.append({"path": raw, "status": "error", "detail": str(exc)})
            continue
        results.append(result)
        report.append({
            "path": raw,
            "status": "ok",
            "detail": "verified" if dry_run else "written",
            "applied": result.applied,
        })

    batch_id = None if dry_run else record(results, source=source)
    return {
        "dry_run": dry_run,
        "batch_id": batch_id,
        "ok": sum(1 for r in report if r["status"] == "ok"),
        "failed": sum(1 for r in report if r["status"] != "ok"),
        "results": report,
    }


@router.post("/save/bulk-edit")
def bulk_edit(request: BulkEditRequest) -> dict[str, Any]:
    if not request.paths or not request.changes:
        raise HTTPException(400, "Nothing to do")
    return run_bulk(request.paths, [(c.name, c.value) for c in request.changes], request.dry_run, "bulk")


@router.get("/history")
def history(path: str | None = None, limit: int = 200) -> dict[str, Any]:
    entries = appstate.read_history()
    if path:
        key = str(Path(path)).lower()
        entries = [e for e in entries if str(Path(e.get("path", ""))).lower() == key]
    return {"entries": entries[: max(1, min(limit, 1000))]}


@router.post("/history/undo")
def undo(request: UndoRequest) -> dict[str, Any]:
    entries = appstate.read_history()
    if request.entry_id:
        targets = [e for e in entries if e.get("id") == request.entry_id]
    elif request.batch_id:
        targets = [e for e in entries if e.get("batch_id") == request.batch_id]
    else:
        latest = next((e for e in entries if not e.get("undone") and e.get("backup")), None)
        targets = [e for e in entries if latest and e.get("batch_id") == latest["batch_id"]]

    targets = [e for e in targets if not e.get("undone")]
    if not targets:
        raise HTTPException(404, "Nothing to undo")

    conflicts = []
    for entry in targets:
        path = Path(entry["path"])
        backup = Path(entry["backup"]) if entry.get("backup") else None
        if backup is None or not backup.is_file():
            conflicts.append({"path": entry["path"], "reason": "backup is gone"})
        elif not request.force and path.is_file() and sha256_of(path) != entry.get("sha256_after"):
            conflicts.append({"path": entry["path"], "reason": "file changed since this edit"})
    if conflicts and not (request.force and all(c["reason"] != "backup is gone" for c in conflicts)):
        raise HTTPException(409, {"message": "Undo would overwrite newer changes", "conflicts": conflicts})

    undone_ids = set()
    restored = []
    for entry in targets:
        path = Path(entry["path"])
        with fsutil.capture_backups() as taken:
            fsutil.restore(path, Path(entry["backup"]))
        cache.saves.invalidate(path)
        undone_ids.add(entry["id"])
        restored.append({"path": entry["path"], "from": entry["backup"], "previous": str(taken[0]) if taken else None})

    appstate.mark_undone(undone_ids)
    appstate.append_history([{
        "id": appstate.new_id(),
        "batch_id": appstate.new_id(),
        "ts": time.time(),
        "source": "undo",
        "path": item["path"],
        "backup": item["previous"],
        "sha256_after": sha256_of(Path(item["path"])),
        "changes": [],
        "undid": sorted(undone_ids),
        "undone": False,
    } for item in restored])
    return {"restored": restored}


@router.get("/presets")
def list_presets(game_id: str | None = None) -> dict[str, Any]:
    presets = appstate.load()["presets"]
    if game_id:
        presets = [p for p in presets if p.get("game_id") == game_id]
    return {"presets": presets}


@router.put("/presets")
def save_preset(preset: Preset) -> dict[str, Any]:
    if not preset.name.strip():
        raise HTTPException(400, "A preset needs a name")
    stored = preset.model_dump()
    stored["id"] = preset.id or appstate.new_id()
    stored["updated"] = time.time()

    def mutate(state: dict[str, Any]) -> None:
        state["presets"] = [p for p in state["presets"] if p.get("id") != stored["id"]] + [stored]

    appstate.update(mutate)
    return stored


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> None:
        state["presets"] = [p for p in state["presets"] if p.get("id") != preset_id]

    appstate.update(mutate)
    return {"deleted": preset_id}


@router.post("/presets/apply")
def apply_preset(request: ApplyPresetRequest) -> dict[str, Any]:
    preset = next((p for p in appstate.load()["presets"] if p.get("id") == request.preset_id), None)
    if preset is None:
        raise HTTPException(404, "No such preset")
    changes = [(c["name"], c["value"]) for c in preset["changes"]]
    if not changes:
        raise HTTPException(400, "Preset has no changes")
    return run_bulk(request.paths, changes, request.dry_run, f"preset:{preset['name']}")
