"""Identify what engine a game folder uses, and which version of it.

The version matters more than it looks. Ren'Py 7 runs Python 2 and pickles
saves with byte strings; Ren'Py 8 runs Python 3 and pickles them as unicode.
Loading one with the other's assumptions produces silent mojibake, so the
detected major version drives the unpickler's encoding policy.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import UnsupportedGame

RENPY = "renpy"
RPGMAKER = "rpgmaker"
UNITY = "unity"


@dataclass
class GameInfo:
    """What we could work out about a game folder."""

    root: Path
    engine: str
    version: tuple[int, ...] | None = None
    version_name: str | None = None
    #: Python major version the engine embeds - drives pickle encoding.
    python_major: int = 3
    game_dir: Path | None = None
    save_dirs: list[Path] = field(default_factory=list)
    title: str = ""

    @property
    def engine_major(self) -> int | None:
        return self.version[0] if self.version else None


def _find_game_dir(root: Path) -> Path | None:
    """Locate the `game/` folder, allowing for one level of nesting.

    Archives commonly extract to `Foo-1.0-pc/Foo-1.0-pc/game`, which is exactly
    how every game in the local library is laid out.
    """
    direct = root / "game"
    if direct.is_dir():
        return direct
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "game").is_dir():
            return child / "game"
    return None


def _parse_vc_version(renpy_dir: Path) -> tuple[tuple[int, ...] | None, str | None]:
    """Read `renpy/vc_version.py`, which Ren'Py 8 stamps with the full version."""
    path = renpy_dir / "vc_version.py"
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")

    version = None
    match = re.search(r"^version\s*=\s*['\"]([\d.]+)['\"]", text, re.M)
    if match:
        version = tuple(int(p) for p in match.group(1).split("."))

    name = None
    match = re.search(r"^version_name\s*=\s*['\"](.*?)['\"]", text, re.M)
    if match:
        name = match.group(1)

    return version, name


def _parse_init_version(
    renpy_dir: Path, python_major: int | None = None
) -> tuple[tuple[int, ...] | None, str | None]:
    """Read the literal `version_tuple` that Ren'Py 7 hardcodes in __init__.py.

    Transitional builds declare it twice, once per interpreter::

        if PY2:
            version_tuple = (7, 4, 11, vc_version)
        else:
            version_tuple = (8, 0, 0, vc_version)

    so when there is more than one candidate we pick the branch matching the
    interpreter the game actually ships.
    """
    path = renpy_dir / "__init__.py"
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")

    candidates: list[tuple[tuple[int, ...], str | None]] = []
    for match in re.finditer(
        r"^[ \t]*version_tuple\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text, re.M
    ):
        version = tuple(int(g) for g in match.groups())
        # The matching version_name sits a few lines below in the same branch.
        tail = text[match.end() : match.end() + 600]
        name_match = re.search(r"^[ \t]*version_name\s*=\s*['\"](.*?)['\"]", tail, re.M)
        candidates.append((version, name_match.group(1) if name_match else None))

    if not candidates:
        return None, None

    if len(candidates) > 1 and python_major is not None:
        wanted_major = 7 if python_major == 2 else 8
        for version, name in candidates:
            if version[0] == wanted_major:
                return version, name

    return candidates[0]


def _python_major_from_lib(root: Path) -> int | None:
    """Ren'Py ships its interpreter in `lib/`; the folder name gives it away."""
    lib = root / "lib"
    if not lib.is_dir():
        return None
    names = {p.name for p in lib.iterdir()}
    if any(n.startswith("python3") or n.startswith("py3-") for n in names):
        return 3
    if any(n.startswith("pythonlib2") or n.startswith("py2-") for n in names):
        return 2
    return None


def detect_renpy(root: Path) -> GameInfo | None:
    """Return a GameInfo if `root` looks like a Ren'Py game, else None."""
    game_dir = _find_game_dir(root)
    if game_dir is None:
        return None

    base = game_dir.parent
    renpy_dir = base / "renpy"
    if not renpy_dir.is_dir():
        # Some releases strip the engine source but keep the layout; only treat
        # it as Ren'Py if there is script evidence.
        has_scripts = any(game_dir.glob("*.rpyc")) or any(game_dir.glob("*.rpy"))
        if not has_scripts:
            return None

    # Work the interpreter out first: it disambiguates dual-branch version
    # declarations in Ren'Py 7/8 transitional builds.
    python_major = _python_major_from_lib(base)

    version, name = _parse_vc_version(renpy_dir)
    if version is None or len(version) < 2:
        version, name = _parse_init_version(renpy_dir, python_major)

    if python_major is None:
        python_major = 2 if version and version[0] == 7 else 3

    save_dirs = []
    local_saves = game_dir / "saves"
    if local_saves.is_dir():
        save_dirs.append(local_saves)

    return GameInfo(
        root=base,
        engine=RENPY,
        version=version,
        version_name=name,
        python_major=python_major,
        game_dir=game_dir,
        save_dirs=save_dirs,
        title=base.name,
    )


#: MV writes `.rpgsave`; MZ writes `.rmmzsave`.
RPGMAKER_SAVE_SUFFIXES = (".rpgsave", ".rmmzsave")


def _rpgmaker_save_dirs(root: Path) -> list[Path]:
    """Find folders holding RPG Maker saves, for installs and loose backups.

    Save collections are frequently kept away from the game - the local library
    stores them as `<game>/save/*.rpgsave` with no install anywhere - so a
    folder of saves has to be recognised on its own.
    """
    found: list[Path] = []
    for candidate in (root, root / "save", root / "www" / "save"):
        if not candidate.is_dir():
            continue
        if any(
            child.suffix.lower() in RPGMAKER_SAVE_SUFFIXES
            for child in candidate.iterdir()
            if child.is_file()
        ):
            found.append(candidate)
    return found


def _rpgmaker_flavour(save_dirs: list[Path], game_dir: Path | None) -> str | None:
    """Tell MV and MZ apart, preferring the engine files over the saves."""
    if game_dir is not None:
        js = game_dir / "js"
        if (js / "rmmz_core.js").is_file():
            return "MZ"
        if (js / "rpg_core.js").is_file():
            return "MV"

    for directory in save_dirs:
        for child in directory.iterdir():
            if not child.is_file():
                continue
            suffix = child.suffix.lower()
            if suffix == ".rmmzsave":
                return "MZ"
            if suffix == ".rpgsave":
                return "MV"
    return None


def detect_rpgmaker(root: Path) -> GameInfo | None:
    """RPG Maker MV/MZ: `www/` or a top-level `data/` + `js/`, or just saves."""
    game_dir = None
    for base in (root / "www", root):
        if (base / "data").is_dir() and (base / "js").is_dir():
            game_dir = base
            break

    save_dirs = _rpgmaker_save_dirs(root)
    if game_dir is None and not save_dirs:
        return None

    flavour = _rpgmaker_flavour(save_dirs, game_dir)

    return GameInfo(
        root=root,
        engine=RPGMAKER,
        version=(2,) if flavour == "MZ" else (1,) if flavour == "MV" else None,
        version_name=flavour,
        game_dir=game_dir,
        save_dirs=save_dirs,
        title=root.name,
    )


#: Extensions Unity visual novels save under.
UNITY_SAVE_SUFFIXES = (
    ".nson",
    ".savefile",
    ".sav",
    ".save",
    ".es3",
    ".ngp1",
)


def localow_root() -> Path:
    return Path(os.environ.get("USERPROFILE", "~")).expanduser() / "AppData" / "LocalLow"


def _unity_save_dirs(data_dir: Path) -> list[Path]:
    """Resolve where a Unity game actually writes its saves.

    Unity's `persistentDataPath` is `AppData/LocalLow/<Company>/<Product>`, and
    `<Game>_Data/app.info` holds exactly those two lines - so the save location
    can be resolved precisely rather than guessed, the same way Ren'Py's
    `config.save_directory` is read out of the compiled script.
    """
    info = data_dir / "app.info"
    if not info.is_file():
        return []

    try:
        lines = info.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if len(lines) < 2:
        return []

    base = localow_root() / lines[0].strip() / lines[1].strip()
    if not base.is_dir():
        return []

    found = [base]
    # Saves usually sit in a subfolder; Naninovel defaults to "Saves".
    for child in sorted(base.iterdir()):
        if child.is_dir() and any(
            grand.suffix.lower() in UNITY_SAVE_SUFFIXES
            for grand in child.rglob("*")
            if grand.is_file()
        ):
            found.append(child)
    return found


def _is_renpy_save(path: Path) -> bool:
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            return "log" in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def holds_unity_saves(directory: Path) -> bool:
    for child in directory.iterdir():
        if not child.is_file() or child.suffix.lower() not in UNITY_SAVE_SUFFIXES:
            continue
        # Ren'Py also writes `.save` files; those are zips with a pickled log.
        if child.suffix.lower() == ".save" and _is_renpy_save(child):
            continue
        return True
    return False


def detect_renpy_save_folder(root: Path) -> GameInfo | None:
    """A folder of Ren'Py saves kept away from its game (e.g. %APPDATA%/RenPy/<id>)."""
    saves = list(root.glob("*.save"))
    if saves and any(_is_renpy_save(p) for p in saves[:3]):
        return GameInfo(root=root, engine=RENPY, save_dirs=[root], title=root.name)
    return None


def detect_unity(root: Path) -> GameInfo | None:
    """Unity games have a `*_Data` folder; save folders are matched directly."""
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.endswith("_Data"):
            return GameInfo(
                root=root,
                engine=UNITY,
                game_dir=child,
                save_dirs=_unity_save_dirs(child),
                title=root.name,
            )

    # A folder of Unity saves kept away from its game.
    try:
        if holds_unity_saves(root):
            return GameInfo(
                root=root, engine=UNITY, save_dirs=[root], title=root.name
            )
    except OSError:
        pass

    return None


#: Ordered because Ren'Py is the most specific signature and Unity the least.
DETECTORS = (detect_renpy, detect_rpgmaker, detect_renpy_save_folder, detect_unity)


def detect(root: Path | str) -> GameInfo:
    """Identify the game at `root`, raising UnsupportedGame if we cannot."""
    root = Path(root)
    if not root.is_dir():
        raise UnsupportedGame(f"Not a directory: {root}")

    for detector in DETECTORS:
        try:
            info = detector(root)
        except (OSError, PermissionError):
            continue
        if info is not None:
            return info

    raise UnsupportedGame(f"No supported engine detected in {root}")
