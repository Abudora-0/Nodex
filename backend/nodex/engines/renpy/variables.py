"""Expose a save's store variables as a flat, editable table.

`roots` maps qualified names (`store.arousal`) to live values. Most of what a
player wants to change is a scalar sitting directly in there - flags, affection
counters, money, route booleans - so those are surfaced as editable rows and
everything else is shown read-only for context.

Python 2 saves complicate this: their strings arrive as `bytes`. We decode them
for display and re-encode on write, so the value the game sees keeps its
original type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .stubs import StubBase

#: Types the editor will let you change in place.
SCALAR_TYPES = (bool, int, float, str, bytes)

#: Ren'Py's own bookkeeping. Hidden by default - editing these breaks saves.
INTERNAL_PREFIXES = ("store._", "store.config", "store.gui", "store.persistent")


@dataclass
class Variable:
    """One row in the variable table."""

    name: str
    kind: str
    value: Any
    editable: bool
    internal: bool
    #: Human-readable summary for non-scalars (e.g. "list[12]").
    preview: str

    @property
    def short_name(self) -> str:
        return self.name.split(".", 1)[1] if "." in self.name else self.name


def _describe(value: Any) -> tuple[str, str]:
    """Return (kind, preview) for a value."""
    if isinstance(value, bool):
        return "bool", str(value)
    if isinstance(value, int):
        return "int", str(value)
    if isinstance(value, float):
        return "float", repr(value)
    if isinstance(value, bytes):
        return "str", _decode(value)
    if isinstance(value, str):
        return "str", value
    if value is None:
        return "none", "None"
    if isinstance(value, dict):
        return "dict", f"dict[{len(value)}]"
    if isinstance(value, (list, tuple)):
        return type(value).__name__, f"{type(value).__name__}[{len(value)}]"
    if isinstance(value, (set, frozenset)):
        return "set", f"set[{len(value)}]"
    if isinstance(value, StubBase):
        module, name = type(value)._nodex_origin
        return "object", f"{module}.{name}"
    return type(value).__name__, repr(value)[:80]


def _decode(raw: bytes) -> str:
    """Best-effort text for a Python 2 byte string."""
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return repr(raw)


def is_internal(name: str) -> bool:
    return name.startswith(INTERNAL_PREFIXES)


def list_variables(roots: dict[str, Any], include_internal: bool = False) -> list[Variable]:
    """Build the variable table, scalars first and alphabetical within kind."""
    rows: list[Variable] = []
    for name, value in roots.items():
        internal = is_internal(name)
        if internal and not include_internal:
            continue
        kind, preview = _describe(value)
        rows.append(
            Variable(
                name=name,
                kind=kind,
                value=value,
                editable=isinstance(value, SCALAR_TYPES) or value is None,
                internal=internal,
                preview=preview,
            )
        )

    rows.sort(key=lambda r: (not r.editable, r.name))
    return rows


class CoercionError(ValueError):
    """The supplied value does not fit the variable's existing type."""


def coerce(existing: Any, new_value: Any) -> Any:
    """Convert `new_value` to match the type `existing` already has.

    Ren'Py game logic frequently does `if flag is True` or arithmetic on
    counters, so preserving the exact type matters more than being permissive.
    """
    if existing is None:
        return new_value

    if isinstance(existing, bool):
        if isinstance(new_value, bool):
            return new_value
        if isinstance(new_value, str):
            lowered = new_value.strip().lower()
            if lowered in ("true", "1", "yes"):
                return True
            if lowered in ("false", "0", "no"):
                return False
            raise CoercionError(f"{new_value!r} is not a boolean")
        return bool(new_value)

    if isinstance(existing, int):
        try:
            return int(new_value)
        except (TypeError, ValueError) as exc:
            raise CoercionError(f"{new_value!r} is not an integer") from exc

    if isinstance(existing, float):
        try:
            return float(new_value)
        except (TypeError, ValueError) as exc:
            raise CoercionError(f"{new_value!r} is not a number") from exc

    if isinstance(existing, bytes):
        # Python 2 save: keep it bytes so the game's own type checks still hold.
        if isinstance(new_value, bytes):
            return new_value
        return str(new_value).encode("utf-8")

    if isinstance(existing, str):
        return str(new_value)

    raise CoercionError(
        f"editing {type(existing).__name__} values is not supported"
    )


def set_variable(roots: dict[str, Any], name: str, new_value: Any) -> Any:
    """Set `name`, coercing to the existing type. Returns the stored value."""
    if name not in roots:
        raise KeyError(f"no such variable: {name}")
    coerced = coerce(roots[name], new_value)
    roots[name] = coerced
    return coerced


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Compare two saves' roots, returning (name, old, new) for scalars.

    This is how you work out what a choice actually did: save either side of a
    decision, then diff. Non-scalars are compared by their preview string,
    which is enough to spot a list growing or an object being swapped.
    """
    changes: list[tuple[str, Any, Any]] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name, _ABSENT)
        new = after.get(name, _ABSENT)

        if old is _ABSENT or new is _ABSENT:
            changes.append((name, old, new))
            continue

        if isinstance(old, SCALAR_TYPES) and isinstance(new, SCALAR_TYPES):
            if type(old) is type(new) and old == new:
                continue
            changes.append((name, old, new))
        else:
            if _describe(old)[1] != _describe(new)[1]:
                changes.append((name, old, new))

    return changes


class _Absent:
    def __repr__(self) -> str:
        return "<absent>"


_ABSENT = _Absent()
