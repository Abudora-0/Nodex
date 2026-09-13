"""Save writing and corruption recovery, on saves this test builds itself."""

from __future__ import annotations

import io
import pickle
import zipfile

import pytest

from nodex.core.errors import RepairFailed
from nodex.engines.renpy import repair, save as save_module, variables


def build_save(tmp_path, name="1-1-LT1.save", roots=None, log=None):
    """Write a minimal but structurally faithful Ren'Py save."""
    roots = roots if roots is not None else {
        "store.money": 10,
        "store.love": 0,
        "store.flag": False,
        "store.name": "Ada",
        "store._internal": 1,
    }
    # The rollback log is padded so it dominates the pickle, exactly as in a
    # real save - that is what makes truncation recovery possible.
    log = log if log is not None else [{"filler": list(range(400))} for _ in range(20)]

    payload = pickle.dumps((roots, log), protocol=2)

    path = tmp_path / name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("log", payload)
        archive.writestr("json", '{"_save_name": "", "_renpy_version": [8, 2, 0, 1]}')
        archive.writestr("screenshot.png", b"\x89PNG\r\n\x1a\n")
        archive.writestr("extra_info", b"")
        archive.writestr("renpy_version", b"8.2.0.1")
    path.write_bytes(buffer.getvalue())
    return path


def test_load_reads_roots(tmp_path):
    save = save_module.load(build_save(tmp_path))
    assert save.roots["store.money"] == 10
    assert save.renpy_version == (8, 2, 0, 1)


def test_edit_and_write_survives_reload(tmp_path):
    path = build_save(tmp_path)
    save = save_module.load(path)
    variables.set_variable(save.roots, "store.money", "9999")
    save_module.save_to(save)

    assert save_module.load(path).roots["store.money"] == 9999


def test_write_preserves_unread_members(tmp_path):
    path = build_save(tmp_path)
    save_module.save_to(save_module.load(path))

    with zipfile.ZipFile(path) as archive:
        assert archive.read("screenshot.png").startswith(b"\x89PNG")
        assert "renpy_version" in archive.namelist()


def test_type_is_preserved_on_edit(tmp_path):
    """Ren'Py compares flags with `is True`, so a bool must stay a bool."""
    path = build_save(tmp_path)
    save = save_module.load(path)
    variables.set_variable(save.roots, "store.flag", "true")
    save_module.save_to(save)

    assert save_module.load(path).roots["store.flag"] is True


def test_rejects_a_value_of_the_wrong_type(tmp_path):
    save = save_module.load(build_save(tmp_path))
    with pytest.raises(variables.CoercionError):
        variables.set_variable(save.roots, "store.money", "not a number")


def test_internal_variables_are_hidden_by_default(tmp_path):
    save = save_module.load(build_save(tmp_path))
    names = {row.name for row in variables.list_variables(save.roots)}
    assert "store.money" in names
    assert "store._internal" not in names


def test_healthy_save_diagnoses_clean(tmp_path):
    diagnosis = repair.diagnose(build_save(tmp_path))
    assert diagnosis.healthy
    assert diagnosis.confidence == repair.EXACT


def test_recovers_from_a_destroyed_zip_directory(tmp_path):
    path = build_save(tmp_path)
    raw = path.read_bytes()
    path.write_bytes(raw[: int(len(raw) * 0.75)])

    diagnosis = repair.diagnose(path)
    assert not diagnosis.healthy
    assert diagnosis.recoverable
    assert diagnosis.roots["store.money"] == 10


def test_recovers_store_from_a_truncated_pickle(tmp_path):
    """The store is written before the rollback log, so it usually survives."""
    path = build_save(tmp_path)
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in members.items():
            archive.writestr(name, blob[: len(blob) // 2] if name == "log" else blob)
    path.write_bytes(buffer.getvalue())

    diagnosis = repair.diagnose(path)
    assert diagnosis.confidence == repair.PARTIAL
    assert diagnosis.roots["store.money"] == 10


def test_repaired_save_reloads(tmp_path):
    path = build_save(tmp_path)
    raw = path.read_bytes()
    path.write_bytes(raw[: int(len(raw) * 0.75)])

    written, diagnosis = repair.repair(path)
    assert diagnosis.recoverable
    assert save_module.load(written).roots["store.money"] == 10


def test_falls_back_to_a_sibling_save(tmp_path):
    build_save(tmp_path, name="good.save")
    broken = build_save(tmp_path, name="broken.save")
    # Destroy the log beyond any hope of salvage.
    with zipfile.ZipFile(broken) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, blob in members.items():
            archive.writestr(name, b"garbage" if name == "log" else blob)
    broken.write_bytes(buffer.getvalue())

    diagnosis = repair.diagnose(broken)
    assert diagnosis.confidence == repair.SUBSTITUTED
    assert diagnosis.roots["store.money"] == 10


def test_unrecoverable_save_raises(tmp_path):
    path = tmp_path / "hopeless.save"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(RepairFailed):
        repair.repair(path)


def test_backup_is_taken_before_writing(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("NODEX_VAULT", str(vault))

    from nodex.core import fsutil

    path = build_save(tmp_path)
    save = save_module.load(path)
    variables.set_variable(save.roots, "store.money", 1)
    save_module.save_to(save)

    assert fsutil.list_backups(path), "editing a save must leave a backup behind"


def test_diff_reports_changed_variables(tmp_path):
    before = {"store.money": 10, "store.love": 0}
    after = {"store.money": 10, "store.love": 5}
    changes = variables.diff(before, after)
    assert changes == [("store.love", 0, 5)]
