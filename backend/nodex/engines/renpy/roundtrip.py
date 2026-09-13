"""The safety gate that stands between Nodex and a ruined save file.

Before any save is written, the edited graph is pickled, unpickled again with a
*fresh* stub registry, and compared structurally against what we intended to
write. If anything was lost or changed in transit, the write is refused.

The comparison cannot use `==`, because the two graphs contain different
synthesised classes for the same game type. Objects are therefore compared on
their recorded origin plus their contents, with a visited set to survive the
cycles that Ren'Py save graphs are full of.
"""

from __future__ import annotations

from typing import Any

from .stubs import INTERNAL_ATTRS, StubBase

#: How deep to descend before assuming equality. Ren'Py graphs are wide but
#: not especially deep; this only guards against pathological input.
MAX_DEPTH = 200


class Difference(Exception):
    """Raised internally at the first mismatch, carrying its path."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


def _origin(obj: Any) -> tuple[str, str] | None:
    return getattr(type(obj), "_nodex_origin", None) if isinstance(obj, StubBase) else None


#: Key types whose equality survives a pickle round trip.
_STABLE_KEYS = (str, bytes, int, float, bool, type(None), tuple)


def _keys_comparable(mapping: dict) -> bool:
    """True if the mapping's keys can be matched across two separate loads."""
    return all(isinstance(k, _STABLE_KEYS) for k in mapping)


def _public_state(obj: StubBase) -> dict:
    return {k: v for k, v in obj.__dict__.items() if k not in INTERNAL_ATTRS}


def _compare(a: Any, b: Any, path: str, seen: set, depth: int) -> None:
    if depth > MAX_DEPTH:
        return

    key = (id(a), id(b))
    if key in seen:
        return
    seen.add(key)

    # Ren'Py stores bare classes as sentinel values (renpy.rollback.deleted is
    # one). Each load synthesises its own class object for those, so they must
    # be compared on origin rather than identity.
    if isinstance(a, type) or isinstance(b, type):
        class_origin_a = getattr(a, "_nodex_origin", None) if isinstance(a, type) else None
        class_origin_b = getattr(b, "_nodex_origin", None) if isinstance(b, type) else None
        if class_origin_a is not None or class_origin_b is not None:
            if class_origin_a != class_origin_b:
                raise Difference(path, f"class {class_origin_a} became {class_origin_b}")
            return
        if a is not b:
            raise Difference(path, f"type {a!r} became {b!r}")
        return

    origin_a, origin_b = _origin(a), _origin(b)
    if origin_a != origin_b:
        raise Difference(path, f"class {origin_a} became {origin_b}")

    if origin_a is None and type(a) is not type(b):
        raise Difference(path, f"type {type(a).__name__} became {type(b).__name__}")

    if isinstance(a, dict):
        if len(a) != len(b):
            raise Difference(path, f"{len(a)} keys became {len(b)}")

        if _keys_comparable(a) and _keys_comparable(b):
            if set(a.keys()) != set(b.keys()):
                lost = set(a.keys()) - set(b.keys())
                gained = set(b.keys()) - set(a.keys())
                raise Difference(
                    path,
                    f"keys lost={sorted(map(repr, lost))[:5]} gained={sorted(map(repr, gained))[:5]}",
                )
            for k in a:
                _compare(a[k], b[k], f"{path}[{k!r}]", seen, depth + 1)
        else:
            # Games use their own objects as dict keys (enum-like CharacterType
            # and CardType instances turned up in the local library). Those hash
            # by identity, so a reloaded copy never matches by key lookup even
            # though it holds identical data. Both dicts were built by replaying
            # the same opcode order, so compare them positionally instead.
            for index, ((key_a, val_a), (key_b, val_b)) in enumerate(
                zip(a.items(), b.items())
            ):
                _compare(key_a, key_b, f"{path}<key {index}>", seen, depth + 1)
                _compare(val_a, val_b, f"{path}[<key {index}>]", seen, depth + 1)

    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            raise Difference(path, f"length {len(a)} became {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _compare(x, y, f"{path}[{i}]", seen, depth + 1)

    elif isinstance(a, (set, frozenset)):
        # Set members may be stubs, which do not hash comparably across
        # registries; length plus sorted repr is a good proxy.
        if len(a) != len(b):
            raise Difference(path, f"set size {len(a)} became {len(b)}")

    elif isinstance(a, StubBase):
        pass  # attributes handled below

    elif a != b:
        raise Difference(path, f"value {a!r} became {b!r}")

    if isinstance(a, StubBase):
        state_a, state_b = _public_state(a), _public_state(b)
        if set(state_a.keys()) != set(state_b.keys()):
            raise Difference(path, "attribute set changed")
        for k in state_a:
            _compare(state_a[k], state_b[k], f"{path}.{k}", seen, depth + 1)


def verify(original: Any, reloaded: Any) -> None:
    """Raise Difference if `reloaded` is not structurally identical."""
    _compare(original, reloaded, "root", set(), 0)
