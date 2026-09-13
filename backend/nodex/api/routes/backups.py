"""Browse and restore the backup vault."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core import appstate, fsutil
from .. import cache
from ..common import require_path
from ..editing import sha256_of

router = APIRouter(prefix="/api")


class RestoreRequest(BaseModel):
    path: str
    backup: str


def _describe(backup: Path) -> dict[str, Any]:
    stat = backup.stat()
    stamp = fsutil.backup_stamp(backup)
    return {
        "path": str(backup),
        "original_name": fsutil.original_name(backup),
        "size": stat.st_size,
        "taken": stamp.timestamp() if stamp else stat.st_mtime,
    }


def inside_vault(candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(fsutil.vault_root().resolve())
    except ValueError:
        return False
    return True


@router.get("/backups")
def backups_for(path: str) -> dict[str, Any]:
    target = Path(path)
    return {"path": path, "backups": [_describe(b) for b in fsutil.list_backups(target)]}


@router.get("/backups/vault")
def vault() -> dict[str, Any]:
    root = fsutil.vault_root()
    folders = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            files = sorted((f for f in folder.iterdir() if f.is_file()), reverse=True)
            if not files:
                continue
            folders.append({
                "folder": folder.name,
                "count": len(files),
                "bytes": sum(f.stat().st_size for f in files),
                "backups": [_describe(f) for f in files[:200]],
            })
    return {"root": str(root), "folders": folders}


@router.post("/backups/restore")
def restore(request: RestoreRequest) -> dict[str, Any]:
    target = Path(request.path)
    backup = require_path(request.backup)
    if not inside_vault(backup):
        raise HTTPException(400, "Backup must live inside the Nodex vault")
    if fsutil.original_name(backup) != target.name:
        raise HTTPException(400, "That backup belongs to a different file")

    with fsutil.capture_backups() as taken:
        fsutil.restore(target, backup)
    cache.saves.invalidate(target)

    appstate.append_history([{
        "id": appstate.new_id(),
        "batch_id": appstate.new_id(),
        "ts": time.time(),
        "source": "restore",
        "path": str(target),
        "backup": str(taken[0]) if taken else None,
        "sha256_after": sha256_of(target),
        "changes": [],
        "restored_from": str(backup),
        "undone": False,
    }])
    return {"restored": str(target), "from": str(backup), "previous": str(taken[0]) if taken else None}
