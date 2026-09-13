"""Expose a Unity save as a flat, editable table.

For **Naninovel**, the interesting state is buried two layers down: the
`objectJsonMap` entry keyed by `Naninovel.CustomVariableManager+GameState`
holds a JSON *string*, which parses to a `LocalVariableMap` (per-save state) or
`GlobalVariableMap` (persistent unlocks). Both are parallel `keys`/`values`
arrays. Naninovel stores every custom variable as a string, including numbers -
`"1"`, `"0.15625"` - so values are kept as strings on write rather than being
helpfully converted into types the game would not recognise.

Only that one entry is parsed and re-serialised. The other entries stay as the
strings they arrived as, so nothing the user did not edit gets reformatted.

For **plain JSON** saves there is no schema to rely on, so scalars are surfaced
by walking the object and addressing them with a dotted path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .codec import DecodedSave

#: The .NET type whose payload holds a game's custom variables.
CUSTOM_VARIABLE_MARKER = "CustomVariableManager"

LOCAL_MAP = "LocalVariableMap"
GLOBAL_MAP = "GlobalVariableMap"

#: Deeper than this and a plain-JSON save is structure, not settings.
MAX_JSON_DEPTH = 6

SCALARS = (bool, int, float, str)


class EditError(ValueError):
    """The requested edit cannot be applied."""


@dataclass
class Entry:
    """One editable row."""

    address: str
    label: str
    group: str
    kind: str
    value: Any
    editable: bool = True


@dataclass
class _VariableMap:
    """A located keys/values pair, plus how to write it back."""

    index: int
    payload: dict
    map_name: str
    keys: list
    values: list


def _find_custom_variables(save: dict) -> list[_VariableMap]:
    """Locate every custom-variable map inside a Naninovel save."""
    holder = save.get("objectJsonMap")
    if not isinstance(holder, dict):
        return []

    keys = holder.get("keys")
    values = holder.get("values")
    if not isinstance(keys, list) or not isinstance(values, list):
        return []

    found = []
    for index, key in enumerate(keys):
        if not isinstance(key, str) or CUSTOM_VARIABLE_MARKER not in key:
            continue
        if index >= len(values) or not isinstance(values[index], str):
            continue
        try:
            payload = json.loads(values[index])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        for map_name in (LOCAL_MAP, GLOBAL_MAP):
            inner = payload.get(map_name)
            if not isinstance(inner, dict):
                continue
            map_keys = inner.get("keys")
            map_values = inner.get("values")
            if isinstance(map_keys, list) and isinstance(map_values, list):
                found.append(
                    _VariableMap(index, payload, map_name, map_keys, map_values)
                )

    return found


def _kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "none"
    return type(value).__name__


def list_entries(decoded: DecodedSave) -> list[Entry]:
    """Build the editable table for a decoded Unity save."""
    if decoded.is_naninovel:
        return _naninovel_entries(decoded.data)
    return _json_entries(decoded.data)


def _naninovel_entries(save: Any) -> list[Entry]:
    if not isinstance(save, dict):
        return []

    entries: list[Entry] = []
    for found in _find_custom_variables(save):
        group = "global" if found.map_name == GLOBAL_MAP else "variables"
        for name, value in zip(found.keys, found.values):
            if not isinstance(name, str):
                continue
            entries.append(
                Entry(
                    address=f"{found.map_name}.{name}",
                    label=name,
                    group=group,
                    kind=_kind_of(value),
                    value=value,
                )
            )
    return entries


def _json_entries(data: Any, prefix: str = "", depth: int = 0) -> list[Entry]:
    """Surface every scalar in a plain-JSON save, addressed by dotted path."""
    entries: list[Entry] = []
    if depth > MAX_JSON_DEPTH:
        return entries

    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        items = ((str(index), value) for index, value in enumerate(data))
    else:
        return entries

    for key, value in items:
        address = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, SCALARS) or value is None:
            entries.append(
                Entry(
                    address=address,
                    label=address,
                    group="save",
                    kind=_kind_of(value),
                    value=value,
                    editable=value is not None,
                )
            )
        else:
            entries.extend(_json_entries(value, address, depth + 1))

    return entries


# ---------------------------------------------------------------------------
# Applying edits
# ---------------------------------------------------------------------------


def set_entry(decoded: DecodedSave, address: str, new_value: Any) -> Any:
    """Apply one edit by address. Returns the stored value."""
    if decoded.is_naninovel:
        return _set_naninovel(decoded.data, address, new_value)
    return _set_json(decoded.data, address, new_value)


def _set_naninovel(save: Any, address: str, new_value: Any) -> Any:
    if not isinstance(save, dict):
        raise EditError("This save has no editable structure.")

    map_name, _, name = address.partition(".")
    if not name or map_name not in (LOCAL_MAP, GLOBAL_MAP):
        raise EditError(f"Unrecognised address: {address!r}")

    for found in _find_custom_variables(save):
        if found.map_name != map_name:
            continue
        try:
            position = found.keys.index(name)
        except ValueError:
            continue

        # Naninovel keeps every custom variable as a string, so a value written
        # back as a number would not match what the game expects to read.
        stored = new_value if isinstance(new_value, str) else _to_naninovel_text(
            new_value
        )
        found.values[position] = stored

        _rewrite_payload(save, found)
        return stored

    raise EditError(f"No variable named {name!r} in this save.")


def _to_naninovel_text(value: Any) -> str:
    if isinstance(value, bool):
        # Naninovel writes booleans as True/False, matching .NET's ToString.
        return "True" if value else "False"
    return str(value)


def _rewrite_payload(save: dict, found: _VariableMap) -> None:
    """Re-serialise only the entry we edited, leaving the rest untouched."""
    holder = save["objectJsonMap"]
    holder["values"][found.index] = json.dumps(
        found.payload, separators=(",", ":"), ensure_ascii=False
    )


def _set_json(data: Any, address: str, new_value: Any) -> Any:
    parts = address.split(".")
    container = data

    for part in parts[:-1]:
        container = _descend(container, part, address)

    last = parts[-1]
    existing = _fetch(container, last, address)
    value = coerce(existing, new_value)
    _store(container, last, value, address)
    return value


def _descend(container: Any, part: str, address: str) -> Any:
    if isinstance(container, dict) and part in container:
        return container[part]
    if isinstance(container, list) and part.isdigit() and int(part) < len(container):
        return container[int(part)]
    raise EditError(f"Path not found: {address!r}")


def _fetch(container: Any, part: str, address: str) -> Any:
    if isinstance(container, dict) and part in container:
        return container[part]
    if isinstance(container, list) and part.isdigit() and int(part) < len(container):
        return container[int(part)]
    raise EditError(f"Path not found: {address!r}")


def _store(container: Any, part: str, value: Any, address: str) -> None:
    if isinstance(container, dict):
        container[part] = value
    elif isinstance(container, list) and part.isdigit():
        container[int(part)] = value
    else:
        raise EditError(f"Cannot write to {address!r}")


def coerce(existing: Any, new_value: Any) -> Any:
    """Fit an incoming value to the type the save already uses."""
    if isinstance(existing, bool):
        if isinstance(new_value, bool):
            return new_value
        text = str(new_value).strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
        raise EditError(f"{new_value!r} is not a true/false value.")

    if isinstance(existing, int) and not isinstance(existing, bool):
        try:
            return int(str(new_value).strip())
        except ValueError as exc:
            raise EditError(f"{new_value!r} is not a whole number.") from exc

    if isinstance(existing, float):
        try:
            return float(str(new_value).strip())
        except ValueError as exc:
            raise EditError(f"{new_value!r} is not a number.") from exc

    if isinstance(existing, str):
        return str(new_value)

    raise EditError(
        f"Values of type {type(existing).__name__} cannot be edited directly."
    )
