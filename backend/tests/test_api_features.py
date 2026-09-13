"""Library, backups, diff, bulk edit, history/undo, presets and the choice graph."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nodex.api import app as app_module
from nodex.api.routes.graph import build_graph
from nodex.core import apphome, appstate, fsutil
from nodex.engines.renpy import analyze
from nodex.engines.renpy import save as renpy_save
from nodex.engines.rpgmaker import codec as rpg_codec
from nodex.engines.rpgmaker import lzstring
from tests.test_save_and_repair import build_save

BACKEND = Path(__file__).resolve().parents[1]

RPG_SAVE = {
    "switches": {"@c": "Game_Switches", "_data": {"@c": "Array", "@a": [None, True]}},
    "variables": {"@c": "Game_Variables", "_data": {"@c": "Array", "@a": [None, 7]}},
    "party": {"@c": "Game_Party", "_gold": 500, "_steps": 12},
}


def make_mv(path: Path, data=RPG_SAVE) -> Path:
    path.write_bytes(lzstring.compress_to_base64(rpg_codec.to_json(data)).encode("utf-8"))
    return path


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_edit_records_history_and_backup(client, tmp_path):
    save = build_save(tmp_path)
    response = client.post("/api/save/edit", json={"path": str(save), "changes": [{"name": "store.money", "value": 99}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backup"] and Path(body["backup"]).is_file()

    history = client.get("/api/history", params={"path": str(save)}).json()["entries"]
    assert history[0]["changes"] == [{"name": "store.money", "before": 10, "after": 99}]


def test_undo_restores_previous_bytes(client, tmp_path):
    save = build_save(tmp_path)
    original = save.read_bytes()
    client.post("/api/save/edit", json={"path": str(save), "changes": [{"name": "store.love", "value": 5}]})
    assert renpy_save.load(save).roots["store.love"] == 5

    response = client.post("/api/history/undo", json={})
    assert response.status_code == 200, response.text
    assert save.read_bytes() == original
    assert client.post("/api/history/undo", json={"batch_id": appstate.read_history()[-1]["batch_id"]}).status_code == 404


def test_undo_refuses_when_file_changed_since(client, tmp_path):
    save = build_save(tmp_path)
    client.post("/api/save/edit", json={"path": str(save), "changes": [{"name": "store.love", "value": 5}]})
    save.write_bytes(save.read_bytes() + b"")  # same bytes: allowed
    build_save(tmp_path, roots={"store.money": 1, "store.love": 3, "store.flag": True, "store.name": "B"})

    assert client.post("/api/history/undo", json={}).status_code == 409
    assert client.post("/api/history/undo", json={"force": True}).status_code == 200


def test_bulk_edit_isolates_a_rejected_file(client, tmp_path, monkeypatch):
    good = build_save(tmp_path, name="1-1-LT1.save")
    bad = build_save(tmp_path, name="1-2-LT1.save")
    bad_before = bad.read_bytes()

    real_verify = renpy_save.verify_roundtrip

    def picky(save, data):
        if save.path.name == bad.name:
            from nodex.core.errors import RoundTripError
            raise RoundTripError("forced")
        return real_verify(save, data)

    monkeypatch.setattr(renpy_save, "verify_roundtrip", picky)
    response = client.post("/api/save/bulk-edit", json={
        "paths": [str(good), str(bad), str(tmp_path / "gone.save")],
        "changes": [{"name": "store.money", "value": 42}],
    })
    body = response.json()
    statuses = {Path(r["path"]).name: r["status"] for r in body["results"]}
    assert statuses == {"1-1-LT1.save": "ok", "1-2-LT1.save": "rejected", "gone.save": "missing"}
    assert renpy_save.load(good).roots["store.money"] == 42
    assert bad.read_bytes() == bad_before


def test_bulk_dry_run_writes_nothing(client, tmp_path):
    save = build_save(tmp_path)
    before = save.read_bytes()
    body = client.post("/api/save/bulk-edit", json={
        "paths": [str(save)], "changes": [{"name": "store.money", "value": 1}], "dry_run": True,
    }).json()
    assert body["ok"] == 1 and body["batch_id"] is None
    assert save.read_bytes() == before
    assert appstate.read_history() == []


def test_rpgmaker_diff_and_bulk_edit(client, tmp_path):
    left = make_mv(tmp_path / "file1.rpgsave")
    right = make_mv(tmp_path / "file2.rpgsave")
    client.post("/api/save/edit", json={"path": str(right), "changes": [{"name": "party.gold", "value": 900}]})

    changes = client.post("/api/save/diff", json={"left": str(left), "right": str(right)}).json()["changes"]
    assert [(c["name"], c["before"], c["after"]) for c in changes] == [("party.gold", 500, 900)]


def test_diff_against_backup_resolves_engine(client, tmp_path):
    save = make_mv(tmp_path / "file1.rpgsave")
    body = client.post("/api/save/edit", json={"path": str(save), "changes": [{"name": "variable.1", "value": 8}]}).json()
    diff = client.post("/api/save/diff", json={"left": body["backup"], "right": str(save)}).json()
    assert diff["engine"] == "rpgmaker"
    assert diff["changes"][0]["name"] == "variable.1"


def test_backups_listing_and_restore(client, tmp_path):
    save = make_mv(tmp_path / "file1.rpgsave")
    original = save.read_bytes()
    body = client.post("/api/save/edit", json={"path": str(save), "changes": [{"name": "party.gold", "value": 1}]}).json()

    listed = client.get("/api/backups", params={"path": str(save)}).json()["backups"]
    assert listed[0]["path"] == body["backup"]
    assert client.get("/api/backups/vault").json()["folders"]

    restored = client.post("/api/backups/restore", json={"path": str(save), "backup": body["backup"]})
    assert restored.status_code == 200
    assert save.read_bytes() == original


def test_restore_rejects_files_outside_the_vault(client, tmp_path):
    save = make_mv(tmp_path / "file1.rpgsave")
    outsider = make_mv(tmp_path / "file1.rpgsave.20260101-000000-000.bak")
    response = client.post("/api/backups/restore", json={"path": str(save), "backup": str(outsider)})
    assert response.status_code == 400


def test_presets_round_trip_and_apply(client, tmp_path):
    save = make_mv(tmp_path / "file1.rpgsave")
    preset = client.put("/api/presets", json={
        "game_id": "g1", "name": "Rich", "changes": [{"name": "party.gold", "value": 99999}],
    }).json()
    assert client.get("/api/presets", params={"game_id": "g1"}).json()["presets"][0]["name"] == "Rich"

    applied = client.post("/api/presets/apply", json={"preset_id": preset["id"], "paths": [str(save)]}).json()
    assert applied["ok"] == 1
    assert rpg_codec.load(save).data["party"]["_gold"] == 99999

    client.delete(f"/api/presets/{preset['id']}")
    assert client.get("/api/presets").json()["presets"] == []


def test_library_scan_finds_appdata_and_user_roots(client, tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    (appdata / "RenPy" / "MyGame-1700000000").mkdir(parents=True)
    build_save(appdata / "RenPy" / "MyGame-1700000000")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))

    games = tmp_path / "games" / "Some Game"
    (games / "game").mkdir(parents=True)
    (games / "renpy").mkdir()
    (games / "game" / "script.rpy").write_text("label start:\n    return\n")

    second = tmp_path / "games" / "Other Game" / "www"
    (second / "data").mkdir(parents=True)
    (second / "js").mkdir()

    body = client.post("/api/library/roots", json={"path": str(tmp_path / "games")}).json()
    kinds = {(d["kind"], d["title"]) for d in body["discovered"]}
    assert ("save_folder", "MyGame") in kinds
    assert ("game", "Some Game") in kinds
    assert ("game", "Other Game") in kinds


def test_state_file_recovers_from_corruption(isolated_home):
    (isolated_home / "state.json").write_text("{not json")
    assert appstate.load()["library_roots"] == []
    assert list(isolated_home.glob("state.corrupt-*.json"))


def test_thumbnail_is_served(client, tmp_path):
    save = build_save(tmp_path)
    response = client.get("/api/save/thumbnail", params={"path": str(save)})
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


def test_graph_links_choices_to_labels_and_endings():
    menu = analyze.MenuPoint(script="script", filename="script.rpy", linenumber=10, label="start", choices=[
        analyze.Choice(index=0, caption="Stay", condition=None, jumps=["good_ending"]),
        analyze.Choice(index=1, caption="Leave", condition="love > 2", calls=["side"],
                       effects=[analyze.Effect("love", "+=", "1")]),
    ])
    graph = build_graph(SimpleNamespace(menus=[menu]))
    types = {n["id"]: n["type"] for n in graph["nodes"]}
    assert types["label:good_ending"] == "ending"
    assert types["label:side"] == "label"
    kinds = {(e["source"], e["kind"]) for e in graph["edges"]}
    assert ("label:start", "contains") in kinds
    assert ("choice:script.rpy:10:1", "call") in kinds
    assert graph["stats"] == {"menus": 1, "choices": 2, "labels": 2, "endings": 1}


def test_backup_names_resolve():
    assert fsutil.original_name("a/file1.rpgsave.20260102-030405-678.bak") == "file1.rpgsave"
    assert fsutil.backup_stamp("x.save.20260102-030405-678.bak").year == 2026


def test_static_dir_uses_meipass_when_frozen(tmp_path, monkeypatch):
    (tmp_path / "ui").mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("NODEX_STATIC", raising=False)
    assert apphome.static_dir() == tmp_path / "ui"


def test_sidecar_handshake_and_token(tmp_path):
    env = {**os.environ, "NODEX_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "nodex", "--port", "0", "--token", "s3cret"],
        cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        deadline = time.time() + 30
        line = ""
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line.startswith("NODEX_READY "):
                break
        ready = json.loads(line[len("NODEX_READY "):])
        import httpx

        base = f"http://127.0.0.1:{ready['port']}"
        assert httpx.get(base + "/api/health").status_code == 401
        assert httpx.get(base + "/api/health", headers={"X-Nodex-Token": "s3cret"}).json() == {"status": "ok"}
    finally:
        proc.kill()
        proc.wait()
