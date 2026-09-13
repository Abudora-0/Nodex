"""Expose an RPG Maker save as a flat, editable table.

RPG Maker keeps almost everything a player wants to change in three places:
the switch array, the variable array, and the party. Both arrays are indexed by
the ID the game's editor shows, with index 0 unused.

The containers arrive wrapped in RPG Maker's own JSON extension. `JsonEx`
tags typed objects with `@c` (class name) and encodes arrays as
`{"@c": "Array", "@a": [...]}`. Unwrapping is done by reference so that writing
through a row mutates the save in place and the `@c` tags survive untouched -
losing them would stop the game reconstructing its objects.

Addresses are stable strings like `switch.12`, `variable.3`, `party.gold` or
`actor.1.level`, so the UI can round-trip an edit without holding onto live
objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Party fields worth surfacing, mapped to the label shown.
PARTY_FIELDS = {
    "_gold": "gold",
    "_steps": "steps",
}

#: Actor fields worth surfacing.
ACTOR_FIELDS = {
    "_name": "name",
    "_level": "level",
    "_exp": "exp",
    "_hp": "hp",
    "_mp": "mp",
    "_tp": "tp",
}

SCALAR_TYPES = (bool, int, float, str)


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
    #: Name from the game's data files, when a game folder was supplied.
    name: str | None = None

    @property
    def display(self) -> str:
        return self.name or self.label


def unwrap_array(container: Any) -> list | None:
    """Return the live list inside a JsonEx array wrapper, or None.

    Returned by reference on purpose: edits write straight through to the
    structure that will be re-encoded.
    """
    if isinstance(container, list):
        return container
    if isinstance(container, dict):
        inner = container.get("@a")
        if isinstance(inner, list):
            return inner
    return None


def _data_array(save: dict, key: str) -> list | None:
    """Fetch `save[key]._data` as a live list."""
    holder = save.get(key)
    if not isinstance(holder, dict):
        return None
    return unwrap_array(holder.get("_data"))


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


def list_entries(
    save: Any,
    names: "DataNames | None" = None,
    include_unset: bool = False,
) -> list[Entry]:
    """Build the editable table for a decoded save.

    Switches and variables the game has never touched read as None. They are
    hidden by default because a typical project declares hundreds and uses a
    handful, but they remain editable via `include_unset`.
    """
    if not isinstance(save, dict):
        return []

    entries: list[Entry] = []

    switches = _data_array(save, "switches") or []
    for index, value in enumerate(switches):
        if index == 0:
            continue  # RPG Maker leaves index 0 unused.
        if value is None and not include_unset:
            continue
        entries.append(
            Entry(
                address=f"switch.{index}",
                label=f"Switch {index}",
                group="switches",
                kind="bool",
                value=bool(value),
                name=names.switch(index) if names else None,
            )
        )

    variables = _data_array(save, "variables") or []
    for index, value in enumerate(variables):
        if index == 0:
            continue
        if value is None and not include_unset:
            continue
        entries.append(
            Entry(
                address=f"variable.{index}",
                label=f"Variable {index}",
                group="variables",
                kind=_kind_of(value),
                value=value,
                name=names.variable(index) if names else None,
            )
        )

    party = save.get("party")
    if isinstance(party, dict):
        for field, label in PARTY_FIELDS.items():
            if field in party and isinstance(party[field], SCALAR_TYPES):
                entries.append(
                    Entry(
                        address=f"party.{label}",
                        label=label,
                        group="party",
                        kind=_kind_of(party[field]),
                        value=party[field],
                    )
                )

    for actor_id, actor in _iter_actors(save):
        for field, label in ACTOR_FIELDS.items():
            if field in actor and isinstance(actor[field], SCALAR_TYPES):
                entries.append(
                    Entry(
                        address=f"actor.{actor_id}.{label}",
                        label=f"{_actor_name(actor, actor_id)} · {label}",
                        group="actors",
                        kind=_kind_of(actor[field]),
                        value=actor[field],
                    )
                )

    return entries


def _iter_actors(save: dict):
    """Yield (index, actor dict) for every actor present in the save."""
    actors = _data_array(save, "actors") or []
    for index, actor in enumerate(actors):
        if isinstance(actor, dict):
            yield index, actor


def _actor_name(actor: dict, actor_id: int) -> str:
    name = actor.get("_name")
    return name if isinstance(name, str) and name else f"Actor {actor_id}"


# ---------------------------------------------------------------------------
# Applying edits
# ---------------------------------------------------------------------------


def coerce(existing: Any, new_value: Any) -> Any:
    """Fit an incoming value to the shape the save already uses.

    The UI sends everything as text, and RPG Maker cares about the difference:
    a variable holding 5 must not become "5", or comparisons in the game's
    event scripts stop matching.
    """
    if isinstance(existing, bool) or isinstance(new_value, bool):
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

    if existing is None:
        # An untouched switch or variable: infer a sensible type.
        return _infer(new_value)

    raise EditError(
        f"Values of type {type(existing).__name__} cannot be edited directly."
    )


def _infer(value: Any) -> Any:
    if isinstance(value, (bool, int, float)):
        return value
    text = str(value).strip()
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def set_entry(save: Any, address: str, new_value: Any) -> Any:
    """Apply one edit by address. Returns the stored value."""
    if not isinstance(save, dict):
        raise EditError("This save has no editable structure.")

    parts = address.split(".")
    kind = parts[0]

    if kind in ("switch", "variable") and len(parts) == 2:
        return _set_indexed(save, kind, parts[1], new_value)

    if kind == "party" and len(parts) == 2:
        return _set_party(save, parts[1], new_value)

    if kind == "actor" and len(parts) == 3:
        return _set_actor(save, parts[1], parts[2], new_value)

    raise EditError(f"Unrecognised address: {address!r}")


def _set_indexed(save: dict, kind: str, raw_index: str, new_value: Any) -> Any:
    key = "switches" if kind == "switch" else "variables"
    array = _data_array(save, key)
    if array is None:
        raise EditError(f"This save has no {key} to edit.")

    try:
        index = int(raw_index)
    except ValueError as exc:
        raise EditError(f"{raw_index!r} is not a valid ID.") from exc

    if index < 1:
        raise EditError("IDs start at 1.")

    if index >= len(array):
        # RPG Maker grows these arrays lazily, so a switch the player has never
        # triggered may not exist yet. Extend with None, as the engine does.
        array.extend([None] * (index + 1 - len(array)))

    existing = array[index]
    if kind == "switch":
        value = coerce(True if existing is None else existing, new_value)
        value = bool(value)
    else:
        value = coerce(existing, new_value)

    array[index] = value
    return value


def _set_party(save: dict, field_label: str, new_value: Any) -> Any:
    party = save.get("party")
    if not isinstance(party, dict):
        raise EditError("This save has no party data.")

    field = next((k for k, v in PARTY_FIELDS.items() if v == field_label), None)
    if field is None or field not in party:
        raise EditError(f"Unknown party field: {field_label!r}")

    value = coerce(party[field], new_value)
    party[field] = value
    return value


def _set_actor(save: dict, raw_id: str, field_label: str, new_value: Any) -> Any:
    try:
        actor_id = int(raw_id)
    except ValueError as exc:
        raise EditError(f"{raw_id!r} is not a valid actor ID.") from exc

    field = next((k for k, v in ACTOR_FIELDS.items() if v == field_label), None)
    if field is None:
        raise EditError(f"Unknown actor field: {field_label!r}")

    for index, actor in _iter_actors(save):
        if index == actor_id:
            if field not in actor:
                raise EditError(f"This actor has no {field_label}.")
            value = coerce(actor[field], new_value)
            actor[field] = value
            return value

    raise EditError(f"No actor with ID {actor_id}.")


# ---------------------------------------------------------------------------
# Names from the game's data files
# ---------------------------------------------------------------------------


@dataclass
class DataNames:
    """Switch and variable names, read from a game's `System.json`.

    Without the game folder a save only offers numbers, which makes the table
    nearly unreadable - "Switch 42" tells nobody anything, while "Met Sarah"
    does. This is optional: everything works without it.
    """

    switches: list[str]
    variables: list[str]

    def switch(self, index: int) -> str | None:
        return _at(self.switches, index)

    def variable(self, index: int) -> str | None:
        return _at(self.variables, index)


def _at(names: list[str], index: int) -> str | None:
    if 0 <= index < len(names):
        value = names[index]
        return value.strip() or None if isinstance(value, str) else None
    return None


def load_names(game_dir) -> DataNames | None:
    """Read switch/variable names from a game folder, if one is available."""
    import json
    from pathlib import Path

    game_dir = Path(game_dir)
    candidates = [
        game_dir / "www" / "data" / "System.json",  # MV
        game_dir / "data" / "System.json",  # MZ, and MV without www
    ]

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            system = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        switches = system.get("switches")
        variables = system.get("variables")
        if isinstance(switches, list) and isinstance(variables, list):
            return DataNames(switches=switches, variables=variables)

    return None
