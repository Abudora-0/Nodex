"""Gallery detection and unlocking.

The riskiest behaviour here is destructive rather than merely wrong: several
games gate a gallery on a *collection* of unlocked scene names, and assigning
True to one of those would throw the gallery away instead of opening it. Most
of these tests exist to pin that down.
"""

from __future__ import annotations

import pickle
import zlib

import pytest

from nodex.engines.renpy import gallery, persistent as persistent_module


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("gallery_unlocked_ashley", "gallery"),
        ("image10_unlocked", "unlock"),
        ("anim1", "scene"),
        ("ending3", "ending"),
        ("img_resolution", "other"),
        ("name", "other"),
    ],
)
def test_classify(name, expected):
    assert gallery.classify(name) == expected


@pytest.mark.parametrize(
    "value,present,expected",
    [
        (True, True, "bool"),
        (None, False, "absent"),
        ({}, True, "dict"),
        (set(), True, "set"),
        ([], True, "list"),
        (0, True, "number"),
        ("x", True, "text"),
    ],
)
def test_value_kind(value, present, expected):
    assert gallery.value_kind_of(value, present) == expected


# ---------------------------------------------------------------------------
# What gets suggested
# ---------------------------------------------------------------------------


def make_flag(**kwargs) -> gallery.Flag:
    options = {"name": "gallery_x", "kind": "gallery"}
    options.update(kwargs)
    return gallery.Flag(**options)


def test_boolean_flag_is_suggested():
    assert make_flag(value_kind="bool", current=False).suggested


def test_absent_flag_is_suggested():
    assert make_flag(value_kind="absent").suggested


def test_unrelated_name_is_never_suggested():
    assert not make_flag(name="img_resolution", kind="other", value_kind="bool").suggested


def test_number_flag_is_not_suggested():
    """A counter is not a lock; flipping it to True could break arithmetic."""
    assert not make_flag(value_kind="number", current=3).suggested


def test_collection_without_known_members_is_not_suggested():
    """This is the destructive case: assigning True would wipe the gallery."""
    flag = make_flag(value_kind="dict", current={"a": True})
    assert not flag.suggested
    assert not flag.enumerable


def test_collection_with_known_members_is_suggested():
    flag = make_flag(value_kind="set", current=set(), members=["scene_a"])
    assert flag.suggested and flag.enumerable


def test_collection_is_locked_only_while_entries_are_missing():
    assert make_flag(value_kind="set", current={"a"}, members=["a", "b"]).locked
    assert not make_flag(value_kind="set", current={"a", "b"}, members=["a"]).locked


# ---------------------------------------------------------------------------
# Reading the scripts
# ---------------------------------------------------------------------------


def test_assigned_names_are_found():
    assert gallery._assigned_persistent_names(
        "persistent.gallery_unlocked_x = True"
    ) == {"gallery_unlocked_x"}


def test_reads_are_not_mistaken_for_writes():
    assert gallery._assigned_persistent_names("if persistent.foo:\n    pass") == set()


def test_collection_members_from_add_calls():
    found = gallery._collection_members('persistent.seen.add("beach_scene")')
    assert found == {"seen": ["beach_scene"]}


def test_collection_members_from_subscript_assignment():
    found = gallery._collection_members('persistent.gallery["scene_3"] = True')
    assert found == {"gallery": ["scene_3"]}


def test_computed_keys_yield_no_members():
    """Betrayal builds keys with an f-string; nothing literal to harvest."""
    found = gallery._collection_members(
        'persistent.gallery_unlocked[f"gallery_{name}_{item}"] = True'
    )
    assert found == {}


def test_malformed_python_is_survivable():
    assert gallery._collection_members("not ( python") == {}
    assert gallery._assigned_persistent_names("still ( not python") == set()


# ---------------------------------------------------------------------------
# Applying unlocks
# ---------------------------------------------------------------------------


class FakePersistent:
    """Stands in for a loaded persistent file."""

    def __init__(self, fields):
        self._fields = fields

    @property
    def fields(self):
        return self._fields


def test_boolean_flag_is_set():
    subject = FakePersistent({"gallery_a": False})
    changed = gallery.set_flags(subject, ["gallery_a"])
    assert changed == ["gallery_a"]
    assert subject.fields["gallery_a"] is True


def test_absent_flag_is_added():
    subject = FakePersistent({})
    gallery.set_flags(subject, ["gallery_b"])
    assert subject.fields["gallery_b"] is True


def test_dict_collection_is_filled_not_replaced():
    subject = FakePersistent({"gallery": {"old_scene": True}})
    gallery.set_flags(subject, ["gallery"], members={"gallery": ["new_scene"]})

    stored = subject.fields["gallery"]
    assert isinstance(stored, dict), "the collection must survive as a collection"
    assert stored == {"old_scene": True, "new_scene": True}


def test_set_collection_is_filled_not_replaced():
    subject = FakePersistent({"seen": {"a"}})
    gallery.set_flags(subject, ["seen"], members={"seen": ["b"]})
    assert subject.fields["seen"] == {"a", "b"}


def test_list_collection_keeps_existing_entries():
    subject = FakePersistent({"seen": ["a"]})
    gallery.set_flags(subject, ["seen"], members={"seen": ["a", "b"]})
    assert subject.fields["seen"] == ["a", "b"]


def test_reserved_names_are_refused():
    """Writing Ren'Py's own bookkeeping would corrupt preferences."""
    subject = FakePersistent({"_preferences": "important"})
    assert gallery.set_flags(subject, ["_preferences"]) == []
    assert subject.fields["_preferences"] == "important"


def test_unchanged_flags_are_not_reported():
    subject = FakePersistent({"gallery_a": True})
    assert gallery.set_flags(subject, ["gallery_a"]) == []


def test_mark_images_seen_uses_tuple_keys():
    """renpy.seen_image splits on whitespace before the lookup."""
    subject = FakePersistent({"_seen_images": {}})
    added = gallery.mark_images_seen(subject, ["beach day", ("solo",)])
    assert added == 2
    assert ("beach", "day") in subject.fields["_seen_images"]
    assert ("solo",) in subject.fields["_seen_images"]


def test_mark_images_seen_skips_duplicates():
    subject = FakePersistent({"_seen_images": {("a",): True}})
    assert gallery.mark_images_seen(subject, ["a"]) == 0


# ---------------------------------------------------------------------------
# End to end against a real persistent file
# ---------------------------------------------------------------------------


def build_persistent(tmp_path, fields):
    """Write a persistent file in Ren'Py's own framing: zlib over a pickle."""
    path = tmp_path / "persistent"
    path.write_bytes(zlib.compress(pickle.dumps(fields, protocol=2), 3))
    return path


def test_unlock_writes_through_the_gate(tmp_path):
    path = build_persistent(
        tmp_path, {"gallery_a": False, "gallery_b": True, "_seen_images": {}}
    )

    result = gallery.unlock(path, ["gallery_a", "gallery_b"], images=["cg one"])
    assert result.flags_set == ["gallery_a"]
    assert result.images_marked == 1
    assert result.written is not None

    reloaded = persistent_module.load(path)
    assert reloaded.fields["gallery_a"] is True
    assert ("cg", "one") in reloaded.fields["_seen_images"]


def test_unlock_leaves_file_alone_when_nothing_changes(tmp_path):
    path = build_persistent(tmp_path, {"gallery_a": True})
    before = path.read_bytes()

    result = gallery.unlock(path, ["gallery_a"])
    assert result.written is None
    assert path.read_bytes() == before


def test_unlocker_mod_is_valid_python():
    source = gallery.build_unlocker_mod(["gallery_a", "gallery_b"])
    body = source.split("init 1999 python:", 1)[1]
    import textwrap

    compile(textwrap.dedent(body), "nodex_gallery_unlock.rpy", "exec")
    assert "gallery_a" in source


def test_unlocker_mod_install_and_remove(tmp_path):
    written = gallery.install_unlocker_mod(tmp_path, ["gallery_a"])
    assert written.exists()
    removed = gallery.uninstall_unlocker_mod(tmp_path)
    assert written in removed and not written.exists()
