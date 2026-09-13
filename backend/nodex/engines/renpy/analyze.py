"""Work out what every choice in a game actually does.

The compiled AST carries this almost directly. A `menu` statement becomes a
Menu node whose `items` are `(caption, condition, block)` triples, and the
block holds the statements that run when that option is picked. Reading the
Python out of those blocks gives the effects; following the jumps gives the
route.

Checked against a game that ships its source: for ACCORD's colour choice the
analyser reports `Blue -> kira_choices += 1, path_choice = "kira", jump
kira_path`, which is exactly what `script.rpy` says at line 1286.

Two structural facts drive the implementation:

- A script's top-level node list is **flat and in source order**, containing
  every statement rather than only top-level ones. Enclosing labels are
  therefore found by scanning that list and remembering the most recent Label,
  not by descending into `Label.block` (which is only a partial view).
- The index Ren'Py passes to the menu screen is the item's original position in
  `Menu.items`, so annotations keyed by that index line up exactly at runtime.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import scripts as script_module
from .rpyc import node_type, source_of, text_of, walk

#: Statements to follow past a choice when working out where it leads.
MAX_DOWNSTREAM_NODES = 250
#: How many jumps deep to chase a route.
MAX_JUMP_DEPTH = 3

#: Names that are engine plumbing rather than story state.
NOISE_PREFIXES = ("config.", "gui.", "renpy.", "_", "style.", "preferences.")
NOISE_NAMES = {"ui", "store"}

MUTATING_METHODS = {"append", "add", "remove", "discard", "extend", "update", "insert"}


@dataclass
class Effect:
    """A single change a choice makes to game state."""

    variable: str
    operation: str
    value: str
    conditional: bool = False

    def describe(self) -> str:
        if self.operation == "=":
            text = f"{self.variable} = {self.value}"
        elif self.operation in ("+=", "-="):
            sign = "+" if self.operation == "+=" else "-"
            text = f"{sign}{self.value} {self.variable}"
        elif self.operation in MUTATING_METHODS:
            text = f"{self.variable}.{self.operation}({self.value})"
        else:
            text = f"{self.variable} {self.operation} {self.value}"
        return f"maybe {text}" if self.conditional else text


@dataclass
class Choice:
    """One option within a menu."""

    index: int
    caption: str
    condition: str | None
    effects: list[Effect] = field(default_factory=list)
    jumps: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    downstream: list[Effect] = field(default_factory=list)
    #: True when the option runs nothing at all - usually a pure flavour choice.
    inert: bool = False

    def summary(self, include_downstream: bool = True) -> str:
        """A short human-readable label, e.g. '+1 love · -> beach_route'."""
        parts = [effect.describe() for effect in self.effects]

        if include_downstream and not parts:
            parts.extend(effect.describe() for effect in self.downstream[:3])

        for target in self.jumps + self.calls:
            parts.append(f"→ {target}")

        if not parts:
            return "no effect"
        return " · ".join(parts[:4])


@dataclass
class MenuPoint:
    """One menu statement in the game."""

    script: str
    filename: str
    linenumber: int
    label: str | None
    choices: list[Choice] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Runtime identity, matching `renpy.get_filename_line()`."""
        return f"{self.filename}:{self.linenumber}"

    @property
    def meaningful(self) -> bool:
        """True if any option does something the player would care about."""
        return any(
            choice.effects or choice.jumps or choice.calls for choice in self.choices
        )


@dataclass
class Analysis:
    """Everything the walkthrough generator needs."""

    game_dir: Path
    menus: list[MenuPoint] = field(default_factory=list)
    variables: dict[str, int] = field(default_factory=dict)
    scripts_read: int = 0
    scripts_failed: list[str] = field(default_factory=list)

    @property
    def meaningful_menus(self) -> list[MenuPoint]:
        return [menu for menu in self.menus if menu.meaningful]

    @property
    def tracked_variables(self) -> list[tuple[str, int]]:
        """Story variables ordered by how often choices touch them."""
        return sorted(self.variables.items(), key=lambda item: (-item[1], item[0]))


# ---------------------------------------------------------------------------
# Reading Python out of script nodes
# ---------------------------------------------------------------------------


def _is_noise(name: str) -> bool:
    return name in NOISE_NAMES or name.startswith(NOISE_PREFIXES)


def _dotted(node: ast.AST) -> str | None:
    """Render a Name or Attribute target as a dotted string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _render(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - unparse is best-effort
        return "?"


OPERATORS = {
    ast.Add: "+=",
    ast.Sub: "-=",
    ast.Mult: "*=",
    ast.Div: "/=",
}


def effects_from_source(source: str, conditional: bool = False) -> list[Effect]:
    """Parse a Python fragment from a script into the effects it applies."""
    if not source or not source.strip():
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Ren'Py allows a few constructs plain Python does not; a single
        # unparseable block should not abort the whole analysis.
        return []

    effects: list[Effect] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign):
            target = _dotted(node.target)
            if target and not _is_noise(target):
                effects.append(
                    Effect(
                        target,
                        OPERATORS.get(type(node.op), "op="),
                        _render(node.value),
                        conditional,
                    )
                )

        elif isinstance(node, ast.Assign):
            for element in node.targets:
                target = _dotted(element)
                if target and not _is_noise(target):
                    effects.append(
                        Effect(target, "=", _render(node.value), conditional)
                    )

        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in MUTATING_METHODS:
                target = _dotted(node.func.value)
                if target and not _is_noise(target):
                    arguments = ", ".join(_render(a) for a in node.args)
                    effects.append(
                        Effect(target, node.func.attr, arguments, conditional)
                    )

    return effects


def _block_of(node: Any) -> list[Any]:
    block = node.__dict__.get("block")
    return list(block) if isinstance(block, (list, tuple)) else []


def collect_effects(
    block: Iterable[Any], conditional: bool = False, depth: int = 0
) -> tuple[list[Effect], list[str], list[str]]:
    """Walk a statement block, gathering effects, jump targets and call targets."""
    effects: list[Effect] = []
    jumps: list[str] = []
    calls: list[str] = []

    if depth > 8:
        return effects, jumps, calls

    for node in block:
        kind = node_type(node)

        if kind in ("Python", "Define", "Default"):
            effects.extend(effects_from_source(source_of(node) or "", conditional))

        elif kind == "Jump":
            target = text_of(node.__dict__.get("target"))
            if target:
                jumps.append(target)

        elif kind == "Call":
            target = text_of(node.__dict__.get("label") or node.__dict__.get("target"))
            if target:
                calls.append(target)

        elif kind == "If":
            # Every branch is possible, so record them all as conditional.
            for entry in node.__dict__.get("entries") or []:
                if len(entry) >= 2 and entry[1]:
                    inner = collect_effects(entry[1], True, depth + 1)
                    effects.extend(inner[0])
                    jumps.extend(inner[1])
                    calls.extend(inner[2])

        else:
            nested = _block_of(node)
            if nested:
                inner = collect_effects(nested, conditional, depth + 1)
                effects.extend(inner[0])
                jumps.extend(inner[1])
                calls.extend(inner[2])

    return effects, jumps, calls


# ---------------------------------------------------------------------------
# Whole-game analysis
# ---------------------------------------------------------------------------


def _label_name(node: Any) -> str | None:
    raw = node.__dict__.get("_name") or node.__dict__.get("name")
    name = text_of(raw)
    if name:
        return name
    # Ren'Py names generated labels with a (filename, serial) tuple.
    if isinstance(raw, tuple) and raw:
        return None
    return None


def _menu_items(node: Any) -> list[tuple[str, str | None, list[Any] | None]]:
    items = node.__dict__.get("items") or []
    parsed = []
    for entry in items:
        if len(entry) < 3:
            continue
        caption = text_of(entry[0]) or ""
        condition = text_of(entry[1])
        block = entry[2] if isinstance(entry[2], (list, tuple)) else None
        parsed.append((caption, condition, list(block) if block else None))
    return parsed


def analyze(game_dir: Path | str, python_major: int = 3) -> Analysis:
    """Read every script in a game and map out its choices.

    The traversal has to cope with two different script layouts. Ren'Py 8 emits
    a flat, source-ordered list where a label's body follows it as siblings;
    Ren'Py 7 nests the body inside `Label.block`. Recursing while carrying the
    enclosing label handles both, and menus are de-duplicated by source
    location because Ren'Py 8 reachable through both routes.
    """
    game_dir = Path(game_dir)
    result = Analysis(game_dir=game_dir)

    script_set = script_module.discover(game_dir, python_major)

    #: label name -> the statements that make up its body
    label_bodies: dict[str, list[Any]] = {}
    seen_menus: set[tuple[str, int]] = set()

    for name in sorted(script_set.sources):
        try:
            script = script_set.load(name)
        except Exception:  # noqa: BLE001
            result.scripts_failed.append(name)
            continue

        result.scripts_read += 1
        nodes = list(script.nodes)

        # Ren'Py 8: a label's body is the flat run of statements after it.
        for position, node in enumerate(nodes):
            if node_type(node) != "Label":
                continue
            label = _label_name(node)
            if not label:
                continue
            body = _block_of(node)
            if not body:
                body = nodes[position + 1 : position + 1 + MAX_DOWNSTREAM_NODES]
            label_bodies.setdefault(label, body)

        _visit(nodes, name, None, result, seen_menus)

    for menu in result.menus:
        for choice in menu.choices:
            choice.downstream = _downstream_effects(choice, label_bodies)
            for effect in choice.effects:
                result.variables[effect.variable] = (
                    result.variables.get(effect.variable, 0) + 1
                )

    result.menus.sort(key=lambda menu: (menu.filename, menu.linenumber))
    return result


def _visit(
    nodes: Iterable[Any],
    script_name: str,
    label: str | None,
    result: Analysis,
    seen: set[tuple[str, int]],
    depth: int = 0,
) -> None:
    """Walk a statement list, recording menus with their enclosing label."""
    if depth > 24:
        return

    for node in nodes:
        kind = node_type(node)

        if kind == "Label":
            label = _label_name(node) or label
            _visit(_block_of(node), script_name, label, result, seen, depth + 1)
            continue

        if kind == "Menu":
            menu = _build_menu(node, script_name, label)
            location = (menu.filename, menu.linenumber)
            if location not in seen:
                seen.add(location)
                result.menus.append(menu)
            # An option's block may itself contain further menus.
            for _caption, _condition, block in _menu_items(node):
                if block:
                    _visit(block, script_name, label, result, seen, depth + 1)
            continue

        if kind == "If":
            for entry in node.__dict__.get("entries") or []:
                if len(entry) >= 2 and entry[1]:
                    _visit(entry[1], script_name, label, result, seen, depth + 1)
            continue

        nested = _block_of(node)
        if nested:
            _visit(nested, script_name, label, result, seen, depth + 1)


def _build_menu(node: Any, script_name: str, label: str | None) -> MenuPoint:
    filename = text_of(node.__dict__.get("filename")) or script_name
    linenumber = node.__dict__.get("linenumber") or 0

    menu = MenuPoint(
        script=script_name,
        filename=filename,
        linenumber=int(linenumber) if isinstance(linenumber, int) else 0,
        label=label,
    )

    for index, (caption, condition, block) in enumerate(_menu_items(node)):
        if block is None:
            # A caption-only entry is the menu's prompt, not a choice.
            continue

        effects, jumps, calls = collect_effects(block)
        menu.choices.append(
            Choice(
                index=index,
                caption=caption,
                condition=None if condition in (None, "True") else condition,
                effects=effects,
                jumps=jumps,
                calls=calls,
                inert=not (effects or jumps or calls),
            )
        )

    return menu


def _downstream_effects(
    choice: Choice, label_bodies: dict[str, list[Any]]
) -> list[Effect]:
    """Follow a choice's jumps to see what happens on the route it opens.

    Stops at the next menu: past that point the player makes another decision,
    so any further effects are no longer attributable to this choice.
    """
    collected: list[Effect] = []

    for target in choice.jumps + choice.calls:
        body = label_bodies.get(target)
        if not body:
            continue

        for node in body[:MAX_DOWNSTREAM_NODES]:
            kind = node_type(node)
            if kind in ("Menu", "Label"):
                break
            if kind == "Python":
                collected.extend(effects_from_source(source_of(node) or ""))

        if len(collected) >= 8:
            break

    return collected[:8]


#: Recognises label names that read like an ending.
ENDING_PATTERN = re.compile(r"(ending|end_|_end$|finale|game_over|bad_end|good_end)", re.I)


def looks_like_ending(label: str) -> bool:
    return bool(ENDING_PATTERN.search(label))
