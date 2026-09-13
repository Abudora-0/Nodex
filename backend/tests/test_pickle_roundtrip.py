"""Round-trip tests built on synthetic pickles.

These deliberately do not need a game installed. Each test constructs a pickle
that mimics a specific thing Ren'Py does, so a regression names its own cause
instead of showing up as one more failure in the corpus sweep.
"""

from __future__ import annotations

import pickle

import pytest

from nodex.engines.renpy import repickler, roundtrip
from nodex.engines.renpy.stubs import StubRegistry, infer_base_from_error
from nodex.engines.renpy.unpickler import loads


def cycle(data: bytes, python_major: int = 3):
    """Load, write back, and load again - returning both loaded graphs."""
    registry = StubRegistry()
    first, _ = loads(data, python_major=python_major, registry=registry)
    written = repickler.dumps(first, protocol=2)
    second, _ = loads(written, python_major=python_major, registry=StubRegistry())
    return first, second


def assert_stable(data: bytes, python_major: int = 3) -> None:
    first, second = cycle(data, python_major)
    roundtrip.verify(first, second)


# -- classes standing in for a game's own -----------------------------------


class Character:
    def __init__(self, name="", love=0):
        self.name = name
        self.love = love


class RevertableList(list):
    pass


class RevertableDict(dict):
    pass


class RevertableSet(set):
    def __reduce__(self):
        return (RevertableSet, (list(self),), None)


class SlottedNode:
    """Mimics a Ren'Py AST node, which uses __slots__."""

    __slots__ = ("name", "block")

    def __init__(self, name="", block=None):
        self.name = name
        self.block = block or []

    def __getstate__(self):
        return (None, {"name": self.name, "block": self.block})

    def __setstate__(self, state):
        _, slots = state
        for key, value in slots.items():
            setattr(self, key, value)


def test_scalar_roots_survive():
    roots = {"store.money": 10, "store.flag": True, "store.name": "Ada", "store.x": 1.5}
    assert_stable(pickle.dumps((roots, []), protocol=2))


def test_none_type_is_not_stubbed():
    """CPython pickles NoneType as `type(None)`; stubbing `type` broke loads."""
    assert_stable(pickle.dumps((type(None), None), protocol=2))


def test_empty_bytes_survives():
    """Protocol 2 writes empty bytes as `bytes()`, not via _codecs.encode."""
    assert_stable(pickle.dumps({"store.s": b"", "store.t": b"text"}, protocol=2))


def test_game_object_attributes_survive():
    character = Character("Mar", 5)
    first, second = cycle(pickle.dumps({"store.c": character}, protocol=2))
    roundtrip.verify(first, second)
    assert second["store.c"].love == 5
    assert type(second["store.c"])._nodex_origin[1] == "Character"


def test_revertable_containers_survive():
    payload = {
        "store.list": RevertableList([1, 2, 3]),
        "store.dict": RevertableDict({"a": 1}),
    }
    first, second = cycle(pickle.dumps(payload, protocol=2))
    roundtrip.verify(first, second)
    assert list(second["store.list"]) == [1, 2, 3]
    assert dict(second["store.dict"]) == {"a": 1}


def test_set_contents_are_not_dropped():
    """A set's members ride in its constructor args, not in APPENDS opcodes."""
    _, second = cycle(pickle.dumps({"store.seen": RevertableSet({1, 2, 3})}, protocol=2))
    assert set(second["store.seen"]) == {1, 2, 3}


def test_slotted_state_keeps_its_shape():
    node = SlottedNode("start", [SlottedNode("child")])
    first, second = cycle(pickle.dumps({"store.node": node}, protocol=2))
    roundtrip.verify(first, second)
    # Slots must be readable as plain attributes for the script analyser.
    assert second["store.node"].name == "start"
    assert second["store.node"].block[0].name == "child"


def test_edited_value_is_written_back():
    roots = {"store.money": 10}
    loaded, _ = loads(pickle.dumps((roots, []), protocol=2), registry=StubRegistry())
    loaded[0]["store.money"] = 9999
    again, _ = loads(repickler.dumps(loaded, protocol=2), registry=StubRegistry())
    assert again[0]["store.money"] == 9999


def test_cycles_do_not_hang():
    node = SlottedNode("loop")
    node.block = [node]
    assert_stable(pickle.dumps({"store.n": node}, protocol=2))


def test_roundtrip_verify_catches_a_real_difference():
    """The gate must actually fail when something changes."""
    first = {"store.money": 10}
    second = {"store.money": 11}
    with pytest.raises(roundtrip.Difference):
        roundtrip.verify(first, second)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("'StackSummary' object has no attribute 'append'", ("StackSummary", list)),
        ("'Thing' object has no attribute 'update'", ("Thing", dict)),
        ("'Other' object has no attribute 'add'", ("Other", set)),
        ("something else entirely", None),
    ],
)
def test_container_base_is_inferred_from_the_error(message, expected):
    assert infer_base_from_error(message) == expected
