"""Read Ren'Py's compiled script format.

Verified against real files, a `.rpyc` is::

    "RENPY RPC2"                     10 bytes
    repeated: slot, offset, length   3 x uint32, terminated by slot == 0
    ...payload blocks...

Slot 1 holds a zlib-compressed pickle of `(header, [ast_nodes])`. Games ship
either Ren'Py 7 (pickle protocol 2) or Ren'Py 8 (protocol 5); both load through
the same stub machinery used for saves.

Nodex deliberately does not decompile. Regenerating `.rpy` source would mean
depending on unrpyc, which is GPLv3 and would dictate this project's licence -
and we do not need source anyway. Reading the AST nodes directly gives better
answers about what a choice does than re-parsing generated text would.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ...core.errors import SaveFormatError
from .stubs import StubBase, StubRegistry
from .unpickler import loads

MAGIC = b"RENPY RPC2"
SLOT_HEADER = struct.Struct("<III")
#: Slot 1 is the AST; slot 2 repeats it for a different Python version.
AST_SLOT = 1


@dataclass
class Script:
    """One compiled script file."""

    path: Path
    header: dict[str, Any]
    nodes: list[Any]

    @property
    def version(self) -> int | None:
        value = self.header.get("version")
        return int(value) if isinstance(value, int) else None


def read_slots(data: bytes) -> dict[int, bytes]:
    """Split an RPC2 container into its numbered payloads."""
    if not data.startswith(MAGIC):
        raise SaveFormatError("not a RENPY RPC2 file")

    slots: dict[int, bytes] = {}
    offset = len(MAGIC)
    while offset + SLOT_HEADER.size <= len(data):
        slot, start, length = SLOT_HEADER.unpack_from(data, offset)
        offset += SLOT_HEADER.size
        if slot == 0:
            break
        slots[slot] = data[start : start + length]
    return slots


def load(path: Path | str, python_major: int = 3) -> Script:
    """Load a compiled script's AST from disk."""
    path = Path(path)
    return load_bytes(path.read_bytes(), name=str(path), python_major=python_major)


def load_bytes(data: bytes, name: str = "<memory>", python_major: int = 3) -> Script:
    """Load a compiled script's AST from bytes, for archived scripts."""
    path = Path(name)

    slots = read_slots(data)
    payload = slots.get(AST_SLOT) or next(iter(slots.values()), None)
    if payload is None:
        raise SaveFormatError(f"{path.name}: no payload slots")

    try:
        blob = zlib.decompress(payload)
    except zlib.error as exc:
        raise SaveFormatError(f"{path.name}: payload is not zlib data: {exc}") from exc

    try:
        obj, _ = loads(blob, python_major=python_major, registry=StubRegistry())
    except Exception as exc:  # noqa: BLE001
        raise SaveFormatError(f"{path.name}: could not unpickle AST: {exc}") from exc

    if not (isinstance(obj, tuple) and len(obj) == 2):
        raise SaveFormatError(f"{path.name}: unexpected AST payload shape")

    header, nodes = obj
    return Script(
        path=path,
        header=header if isinstance(header, dict) else {},
        nodes=list(nodes) if isinstance(nodes, (list, tuple)) else [],
    )


def node_type(node: Any) -> str:
    """The node's original Ren'Py class name, e.g. 'Menu' or 'Define'."""
    origin = getattr(type(node), "_nodex_origin", None)
    return origin[1] if origin else type(node).__name__


#: Attributes that hold child nodes, across every AST node type we care about.
CHILD_ATTRS = ("block", "children", "entries", "items")


def walk(nodes: list[Any], depth: int = 0, max_depth: int = 60) -> Iterator[Any]:
    """Yield every node in the tree, depth-first.

    Ren'Py nests blocks arbitrarily (init > python > if > menu > block), and
    menu items carry their own blocks as tuple members, so both attribute
    children and tuple/list members are followed.
    """
    if depth > max_depth:
        return

    for node in nodes:
        if not isinstance(node, StubBase):
            continue
        yield node

        for attr in CHILD_ATTRS:
            child = node.__dict__.get(attr)
            if child is None:
                continue
            yield from walk(_as_nodes(child), depth + 1, max_depth)


def _as_nodes(value: Any) -> list[Any]:
    """Flatten a child attribute into a list of candidate nodes."""
    if isinstance(value, StubBase):
        return [value]
    if isinstance(value, (list, tuple)):
        collected: list[Any] = []
        for item in value:
            collected.extend(_as_nodes(item))
        return collected
    return []


def text_of(value: Any) -> str | None:
    """Recover the text from a string-like value, stub or not.

    Ren'Py wraps script expressions in `PyExpr`, a `str` subclass that also
    records where it came from. A stub cannot subclass `str` usefully - the
    constructor arguments do not fit `str.__new__` - but the text is still
    right there in the reduce arguments we recorded, so read it from source.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")

    if isinstance(value, StubBase):
        record = value.__dict__.get("_nodex_reduce") or value.__dict__.get(
            "_nodex_newobj"
        )
        if record:
            for argument in record[1]:
                if isinstance(argument, (str, bytes)):
                    return text_of(argument)
    return None


def source_of(node: Any) -> str | None:
    """Best-effort source text for a node that carries Python code.

    `Define`, `Default` and `Python` nodes hold a `PyCode`, whose pickled state
    is a tuple laid out as `(version, source, location, mode, ...)`. Index 1 is
    taken specifically rather than scanning, because index 3 is the compile
    mode - the literal string 'eval' - which a looser search would pick up.
    """
    for attr in ("code", "expr", "expression"):
        value = node.__dict__.get(attr)
        if value is None:
            continue

        direct = text_of(value) if not isinstance(value, StubBase) else None
        if direct is not None:
            return direct

        if isinstance(value, StubBase):
            state = value.__dict__.get("_nodex_raw_state")
            if isinstance(state, tuple) and len(state) >= 2:
                source = text_of(state[1])
                if source is not None:
                    return source
            source = text_of(value.__dict__.get("source"))
            if source is not None:
                return source
    return None
