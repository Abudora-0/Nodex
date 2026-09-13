"""Find a game's gallery locks and open them.

Ren'Py ships a `Gallery` class, and its images unlock through one of three
conditions (see `renpy/common/00gallery.rpy`):

* an arbitrary expression, in practice almost always `persistent.<flag>`;
* `renpy.seen_image(name)`, backed by `persistent._seen_images`;
* "all prior images unlocked".

Surveying the installed library showed which of those actually matters. Only
two of ten games use the built-in `Gallery` class at all - the rest roll their
own gallery screens gated on plain persistent flags with names like
`gallery_unlocked_ashley`, `image10_unlocked` or `anim1`. So flags are the
primary mechanism here and `_seen_images` is the secondary one, rather than the
other way round.

`_seen_images` is a dict keyed by the image name split into a **tuple** -
`renpy.seen_image` does `tuple(name.split())` before looking it up - which
matches the `imgname` tuple on an `Image` node exactly.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import persistent as persistent_module
from . import scripts as script_module
from .rpyc import node_type, source_of, text_of, walk

PERSISTENT_REFERENCE = re.compile(r"persistent\.(\w+)")

#: Name fragments that mark a flag as gallery-ish. Ordered: the first match
#: wins, so a name containing both "gallery" and "scene" reads as a gallery.
NAME_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gallery", ("gallery", "album", "artbook", "cg_", "_cg")),
    ("scene", ("scene", "replay", "anim", "video", "movie")),
    ("ending", ("ending", "_end", "end_", "finale", "route_complete")),
    ("unlock", ("unlock", "unlocked", "seen", "viewed", "watched")),
)

#: Ren'Py's own persistent bookkeeping - never offered as an unlock target.
RESERVED_PREFIX = "_"


#: Methods a game calls to record something as unlocked in a collection.
COLLECTION_ADDERS = {"add", "append", "extend", "update", "insert"}


@dataclass
class Flag:
    """A `persistent.<name>` the game reads or writes."""

    name: str
    kind: str
    references: int = 0
    assigned: bool = False
    current: Any = None
    present: bool = False
    #: Shape of the value already stored: bool, collection, number, text...
    value_kind: str = "absent"
    #: Literal values the scripts ever add to this collection, if it is one.
    members: list[str] = field(default_factory=list)

    #: Entries already present in the collection, if it is one.
    existing_members: list[str] = field(default_factory=list)

    @property
    def is_collection(self) -> bool:
        return self.value_kind in ("set", "list", "dict")

    @property
    def enumerable(self) -> bool:
        """Whether we know which entries would open this collection.

        Some games build their keys at runtime - Betrayal composes
        `f"gallery_{character}_{item_id}"` from a catalogue of objects - so no
        literal ever appears in the source for us to harvest. Guessing would
        mean writing invented keys into the player's persistent file, so those
        are reported instead of unlocked.
        """
        return not self.is_collection or bool(self.members)

    @property
    def suggested(self) -> bool:
        """Whether Nodex would tick this by default.

        The name has to read like a lock, and we have to know how to open it
        safely. The value's shape decides that, and getting it wrong is
        destructive: Betrayal's `gallery_unlocked` is a *set* of unlocked scene
        names, not a boolean, so assigning True would wipe the gallery rather
        than fill it. Collections are only suggested when the scripts told us
        what belongs inside them.
        """
        if self.kind == "other":
            return False
        if self.is_collection:
            return bool(self.members)
        if self.value_kind == "absent":
            # Ren'Py returns None for unset persistent attributes, so adding
            # a boolean where nothing existed is harmless.
            return True
        return self.value_kind == "bool"

    @property
    def locked(self) -> bool:
        """True when there is still something here to open."""
        if self.is_collection:
            existing = self.current if hasattr(self.current, "__contains__") else ()
            return any(member not in existing for member in self.members)
        return not bool(self.current)


@dataclass
class GalleryReport:
    """What the scripts say about this game's galleries."""

    game_dir: Path
    flags: list[Flag] = field(default_factory=list)
    #: Image names declared with an `image` statement, as tuples.
    images: list[tuple[str, ...]] = field(default_factory=list)
    #: True if the game uses Ren'Py's built-in Gallery class.
    uses_builtin_gallery: bool = False
    #: Images named in Gallery().unlock(...) / .image(...) calls.
    gallery_images: list[str] = field(default_factory=list)
    scripts_read: int = 0
    seen_images_recorded: int = 0

    @property
    def suggested(self) -> list[Flag]:
        return [flag for flag in self.flags if flag.suggested]

    @property
    def locked_suggested(self) -> list[Flag]:
        return [flag for flag in self.suggested if flag.locked]

    @property
    def needs_manual(self) -> list[Flag]:
        """Gallery-ish collections whose entries we could not enumerate."""
        return [
            flag
            for flag in self.flags
            if flag.kind != "other" and flag.is_collection and not flag.enumerable
        ]


def value_kind_of(value: Any, present: bool) -> str:
    """Describe a stored value's shape, so we know how to unlock it.

    Ren'Py's revertable containers are `list`/`dict`/`set` subclasses, so the
    ordinary isinstance checks identify them correctly even though they arrive
    as synthesised stub classes.
    """
    if not present or value is None:
        return "absent"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, (set, frozenset)):
        return "set"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (str, bytes)):
        return "text"
    return "object"


def classify(name: str) -> str:
    lowered = name.lower()
    for kind, fragments in NAME_HINTS:
        if any(fragment in lowered for fragment in fragments):
            return kind
    return "other"


def _assigned_persistent_names(source: str) -> set[str]:
    """Names the fragment *writes* to, e.g. `persistent.foo = True`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    assigned: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]

        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "persistent"
            ):
                assigned.add(target.attr)
    return assigned


def _collection_members(source: str) -> dict[str, list[str]]:
    """Literal values the fragment adds to a persistent collection.

    Games that gate a gallery on a set rather than a flag record progress with
    calls like `persistent.gallery_unlocked.add("beach_scene")`. Harvesting
    those literals is what lets the unlocker fill the collection in instead of
    blindly overwriting it.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    found: dict[str, list[str]] = {}

    def record(name: str, value: Any) -> None:
        if isinstance(value, str):
            found.setdefault(name, []).append(value)

    for node in ast.walk(tree):
        # persistent.flag.add("x")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in COLLECTION_ADDERS:
                target = node.func.value
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "persistent"
                ):
                    for argument in node.args:
                        if isinstance(argument, ast.Constant):
                            record(target.attr, argument.value)
                        elif isinstance(argument, (ast.List, ast.Set, ast.Tuple)):
                            for element in argument.elts:
                                if isinstance(element, ast.Constant):
                                    record(target.attr, element.value)

        # persistent.flag["x"] = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "persistent"
                    and isinstance(target.slice, ast.Constant)
                ):
                    record(target.value.attr, target.slice.value)

    return found


def _gallery_image_names(source: str) -> list[str]:
    """String arguments to `.unlock(...)` and `.image(...)` calls."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("unlock", "image", "unlock_image", "button"):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                names.append(argument.value)
    return names


def scan(game_dir: Path | str, python_major: int = 3) -> GalleryReport:
    """Read the scripts and report every gallery lock we can find."""
    game_dir = Path(game_dir)
    report = GalleryReport(game_dir=game_dir)

    references: dict[str, int] = {}
    assigned: set[str] = set()
    members: dict[str, list[str]] = {}

    script_set = script_module.discover(game_dir, python_major)

    for name in sorted(script_set.sources):
        try:
            script = script_set.load(name)
        except Exception:  # noqa: BLE001 - one bad script must not stop the scan
            continue
        report.scripts_read += 1

        for node in walk(script.nodes):
            kind = node_type(node)

            if kind == "Image":
                imgname = node.__dict__.get("imgname")
                if isinstance(imgname, (tuple, list)) and imgname:
                    parts = tuple(text_of(part) or str(part) for part in imgname)
                    report.images.append(parts)

            source = source_of(node)
            if not source:
                continue

            for match in PERSISTENT_REFERENCE.finditer(source):
                flag_name = match.group(1)
                if flag_name.startswith(RESERVED_PREFIX):
                    continue
                references[flag_name] = references.get(flag_name, 0) + 1

            if "persistent." in source:
                assigned |= _assigned_persistent_names(source)
                for flag_name, values in _collection_members(source).items():
                    members.setdefault(flag_name, []).extend(values)

            if "Gallery(" in source:
                report.uses_builtin_gallery = True
                report.gallery_images.extend(_gallery_image_names(source))

    report.flags = [
        Flag(
            name=name,
            kind=classify(name),
            references=count,
            assigned=name in assigned,
            members=sorted(set(members.get(name, []))),
        )
        for name, count in sorted(references.items())
    ]
    report.images = sorted(set(report.images))
    report.gallery_images = sorted(set(report.gallery_images))
    return report


def find_persistent(game_dir: Path, title: str, python_major: int = 3) -> Path | None:
    """Locate the persistent file belonging to a game.

    It lives beside the saves, wherever those turned out to be - which for most
    games is `%APPDATA%/RenPy/<config.save_directory>` rather than the game
    folder. See `locate.find_save_locations`.
    """
    from . import locate

    for location in locate.find_save_locations(game_dir, title, python_major):
        candidate = location.path / "persistent"
        if candidate.is_file():
            return candidate
    return None


def attach_current_values(
    report: GalleryReport, persistent: persistent_module.RenpyPersistent
) -> GalleryReport:
    """Fill in each flag's value from the player's persistent file."""
    fields = persistent.fields
    for flag in report.flags:
        flag.present = flag.name in fields
        flag.current = fields.get(flag.name)
        flag.value_kind = value_kind_of(flag.current, flag.present)
        if flag.is_collection and hasattr(flag.current, "__iter__"):
            # Showing what is already inside tells the user the naming scheme
            # when we could not work it out from the scripts.
            flag.existing_members = sorted(
                str(entry) for entry in list(flag.current)[:200]
            )

    seen = fields.get("_seen_images")
    report.seen_images_recorded = len(seen) if hasattr(seen, "__len__") else 0
    return report


# ---------------------------------------------------------------------------
# Applying unlocks
# ---------------------------------------------------------------------------


@dataclass
class UnlockResult:
    flags_set: list[str] = field(default_factory=list)
    images_marked: int = 0
    written: Path | None = None


def set_flags(
    persistent: persistent_module.RenpyPersistent,
    names: list[str],
    value: Any = True,
    members: dict[str, list[str]] | None = None,
) -> list[str]:
    """Open the named locks. Returns the names actually changed.

    A flag holding a collection is *filled*, not replaced: those hold the set
    of scenes the player has unlocked, and assigning True to one would throw
    the gallery away instead of opening it.
    """
    fields = persistent.fields
    members = members or {}
    changed = []

    for name in names:
        if name.startswith(RESERVED_PREFIX):
            # Ren'Py's own bookkeeping; writing here breaks preferences.
            continue

        existing = fields.get(name)
        wanted = members.get(name) or []

        if isinstance(existing, dict):
            added = [key for key in wanted if key not in existing]
            for key in added:
                existing[key] = True
            if added:
                changed.append(name)

        elif isinstance(existing, (set, frozenset)):
            added = [item for item in wanted if item not in existing]
            if added:
                # frozenset cannot be extended; swap in a mutable copy that
                # keeps the game's own class.
                if isinstance(existing, frozenset):
                    fields[name] = set(existing) | set(added)
                else:
                    existing.update(added)
                changed.append(name)

        elif isinstance(existing, (list, tuple)):
            added = [item for item in wanted if item not in existing]
            if added:
                if isinstance(existing, tuple):
                    fields[name] = existing + tuple(added)
                else:
                    existing.extend(added)
                changed.append(name)

        elif existing != value:
            fields[name] = value
            changed.append(name)

    return changed


def mark_images_seen(
    persistent: persistent_module.RenpyPersistent,
    images: list[tuple[str, ...] | str],
) -> int:
    """Record images as seen, satisfying Gallery unlock conditions.

    `renpy.seen_image` splits a string name on whitespace and looks the tuple
    up in `persistent._seen_images`, so keys are stored in that exact shape.
    """
    fields = persistent.fields
    seen = fields.get("_seen_images")
    if seen is None or not hasattr(seen, "__setitem__"):
        return 0

    added = 0
    for image in images:
        key = tuple(image.split()) if isinstance(image, str) else tuple(image)
        if not key:
            continue
        if key not in seen:
            seen[key] = True
            added += 1
    return added


def unlock(
    persistent_path: Path | str,
    flags: list[str],
    images: list[tuple[str, ...] | str] | None = None,
    python_major: int = 3,
    members: dict[str, list[str]] | None = None,
) -> UnlockResult:
    """Apply unlocks to a persistent file, through the round-trip gate."""
    loaded = persistent_module.load(persistent_path, python_major=python_major)

    result = UnlockResult()
    result.flags_set = set_flags(loaded, flags, members=members)
    if images:
        result.images_marked = mark_images_seen(loaded, images)

    if result.flags_set or result.images_marked:
        result.written = persistent_module.save_to(loaded)

    return result


# ---------------------------------------------------------------------------
# Mod alternative
# ---------------------------------------------------------------------------

MOD_FILENAME = "nodex_gallery_unlock.rpy"


def build_unlocker_mod(flags: list[str]) -> str:
    """A mod that sets the flags at startup, instead of editing persistent.

    Useful when a game rewrites its persistent file on launch, which would
    undo a direct edit. Kept Python 2 compatible for Ren'Py 7.
    """
    lines = [
        "# Generated by Nodex.",
        "#",
        "# Unlocks gallery content by setting persistent flags when the game",
        "# starts. Delete this file to remove it - nothing else is changed.",
        "",
        "init 1999 python:",
        "",
        "    _nodex_unlock_flags = [",
    ]
    for name in sorted(flags):
        lines.append('        "%s",' % name)
    lines.extend(
        [
            "    ]",
            "",
            "    for _nodex_flag in _nodex_unlock_flags:",
            "        try:",
            "            setattr(persistent, _nodex_flag, True)",
            "        except Exception:",
            "            pass",
            "",
        ]
    )
    return "\n".join(lines)


def install_unlocker_mod(game_dir: Path | str, flags: list[str]) -> Path:
    from ...core.fsutil import atomic_write

    destination = Path(game_dir) / MOD_FILENAME
    atomic_write(destination, build_unlocker_mod(flags).encode("utf-8"))
    compiled = destination.with_suffix(".rpyc")
    if compiled.exists():
        try:
            compiled.unlink()
        except OSError:
            pass
    return destination


def uninstall_unlocker_mod(game_dir: Path | str) -> list[Path]:
    game_dir = Path(game_dir)
    removed = []
    for candidate in (game_dir / MOD_FILENAME, (game_dir / MOD_FILENAME).with_suffix(".rpyc")):
        if candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                pass
    return removed
