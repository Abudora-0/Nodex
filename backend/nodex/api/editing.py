"""One edit path for every engine, so single edits, bulk edits, presets and undo
all go through the same round-trip gate and leave the same history trail."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import appstate
from ..core.errors import NodexError
from ..core.fsutil import capture_backups
from ..engines.renpy import save as renpy_save
from ..engines.renpy import variables as renpy_vars
from ..engines.rpgmaker import codec as rpg_codec
from ..engines.rpgmaker import variables as rpg_vars
from ..engines.unity import codec as unity_codec
from ..engines.unity import variables as unity_vars
from . import cache
from .common import engine_of, jsonable


class EditFailure(Exception):
    """An edit that did not happen. `status` names why, `code` is the HTTP code."""

    def __init__(self, status: str, code: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class EditResult:
    path: Path
    written: Path
    applied: list[dict[str, Any]] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    sha256_after: str | None = None
    dry_run: bool = False


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_renpy_fresh(path: Path, use_cache: bool) -> renpy_save.RenpySave:
    if use_cache:
        cached = cache.saves.get(path)
        if cached is not None:
            return cached
    try:
        return renpy_save.load(path)
    except NodexError as exc:
        raise EditFailure("error", 422, str(exc)) from exc


def apply_edit(
    path: Path,
    changes: list[tuple[str, Any]],
    *,
    dest: Path | None = None,
    verify: bool = True,
    dry_run: bool = False,
) -> EditResult:
    """Apply `changes` to one save and write it (unless `dry_run`)."""
    engine = engine_of(path)
    destination = dest or path
    applied: list[dict[str, Any]] = []

    if engine == "renpy":
        # Dry runs and non-cached loads must not mutate the shared cache entry.
        save = _load_renpy_fresh(path, use_cache=not dry_run)
        try:
            for name, value in changes:
                before = save.roots.get(name)
                try:
                    stored = renpy_vars.set_variable(save.roots, name, value)
                except KeyError as exc:
                    raise EditFailure("missing", 404, str(exc)) from exc
                except renpy_vars.CoercionError as exc:
                    raise EditFailure("invalid", 400, f"{name}: {exc}") from exc
                applied.append({"name": name, "before": jsonable(before), "value": jsonable(stored)})

            if dry_run:
                data = renpy_save.serialize_log(save)
                renpy_save.verify_roundtrip(save, data)
                return EditResult(path, destination, applied, dry_run=True)

            with capture_backups() as taken:
                renpy_save.save_to(save, destination, verify=verify)
        except NodexError as exc:
            cache.saves.invalidate(path)
            raise EditFailure("rejected", 409, str(exc)) from exc
        except EditFailure:
            cache.saves.invalidate(path)
            raise
        cache.saves.invalidate(path)
    else:
        codec, vars_mod = (rpg_codec, rpg_vars) if engine == "rpgmaker" else (unity_codec, unity_vars)
        try:
            decoded = codec.load(path)
        except NodexError as exc:
            raise EditFailure("error", 422, str(exc)) from exc

        target = decoded.data if engine == "rpgmaker" else decoded
        current = {e.address: e.value for e in _entries(engine, decoded, include_unset=True)}
        for name, value in changes:
            try:
                stored = vars_mod.set_entry(target, name, value)
            except KeyError as exc:
                raise EditFailure("missing", 404, str(exc)) from exc
            except vars_mod.EditError as exc:
                raise EditFailure("invalid", 400, f"{name}: {exc}") from exc
            applied.append({"name": name, "before": jsonable(current.get(name)), "value": jsonable(stored)})

        try:
            if dry_run:
                encoded = codec.encode(decoded.data, decoded.flavour) if engine == "rpgmaker" else codec.encode(decoded)
                codec.verify_roundtrip(decoded, encoded)
                return EditResult(path, destination, applied, dry_run=True)
            with capture_backups() as taken:
                codec.save_to(decoded, destination)
        except NodexError as exc:
            raise EditFailure("rejected", 409, str(exc)) from exc

    return EditResult(
        path=path,
        written=destination,
        applied=applied,
        backups=list(taken),
        sha256_after=sha256_of(destination) if destination.exists() else None,
    )


def _entries(engine: str, decoded: Any, include_unset: bool = False) -> list[Any]:
    if engine == "rpgmaker":
        return rpg_vars.list_entries(decoded.data, include_unset=include_unset)
    return unity_vars.list_entries(decoded)


def load_rows(path: Path) -> dict[str, Any]:
    """`{address: Entry}` for a non-Ren'Py save, unset entries included."""
    engine = engine_of(path)
    codec = rpg_codec if engine == "rpgmaker" else unity_codec
    try:
        decoded = codec.load(path)
    except NodexError as exc:
        raise EditFailure("error", 422, str(exc)) from exc
    return {e.address: e for e in _entries(engine, decoded, include_unset=True)}


def entries_for_diff(left: Path, right: Path) -> list[dict[str, Any]]:
    before, after = load_rows(left), load_rows(right)
    changes = []
    for address in sorted(set(before) | set(after), key=_natural_key):
        old, new = before.get(address), after.get(address)
        if old is not None and new is not None and type(old.value) is type(new.value) and old.value == new.value:
            continue
        label_source = new or old
        changes.append({
            "name": address,
            "label": getattr(label_source, "display", None) or label_source.label,
            "group": label_source.group,
            "before": jsonable(old.value) if old is not None else None,
            "after": jsonable(new.value) if new is not None else None,
            "before_missing": old is None,
            "after_missing": new is None,
        })
    return changes


def _natural_key(text: str) -> list[Any]:
    import re

    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def history_entries(results: list[EditResult], batch_id: str, source: str) -> list[dict[str, Any]]:
    now = time.time()
    entries = []
    for result in results:
        if result.dry_run or not result.backups:
            continue
        entries.append({
            "id": appstate.new_id(),
            "batch_id": batch_id,
            "ts": now,
            "source": source,
            "path": str(result.written),
            "backup": str(result.backups[0]) if result.backups else None,
            "sha256_after": result.sha256_after,
            "changes": [
                {"name": a["name"], "before": a.get("before"), "after": a["value"]}
                for a in result.applied
            ],
            "undone": False,
        })
    return entries


def record(results: list[EditResult], source: str, batch_id: str | None = None) -> str:
    batch = batch_id or appstate.new_id()
    appstate.append_history(history_entries(results, batch, source))
    return batch
