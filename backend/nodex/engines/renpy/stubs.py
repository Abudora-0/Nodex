"""Stand-in classes for the game code we do not have.

A Ren'Py save is a pickle of live game objects, so unpickling it normally
requires the game's own classes to be importable. They are not - they only
exist inside the running game. Instead we synthesise a throwaway class for
every `(module, name)` the pickle asks for, remember where it came from, and
hand that back to the unpickler.

The synthesised class has to be a subclass of the right builtin, because
Ren'Py's containers are list/dict/set subclasses and the pickle stream drives
them with APPENDS / SETITEMS opcodes that only work on the real thing.

Everything needed to write the object back out under its original name is kept
on the class (`_nodex_origin`) and on the instance (see `unpickler`).
"""

from __future__ import annotations

import codecs
import copyreg
import re
from typing import Any

#: Globals the unpickler must actually *call*, not merely name. Stubbing these
#: would silently break reconstruction: `_codecs.encode` is how protocol 2
#: rebuilds byte strings, so a stub there turns every Python 2 `str` in the
#: save into a meaningless placeholder object.
#:
#: Returning the genuine callable is also correct on the way back out - the
#: standard pickler knows how to name it, and fix_imports restores the Python 2
#: spelling for Ren'Py 7.
REAL_GLOBALS: dict[tuple[str, str], Any] = {
    ("_codecs", "encode"): codecs.encode,
    ("codecs", "encode"): codecs.encode,
    ("copy_reg", "_reconstructor"): copyreg._reconstructor,
    ("copyreg", "_reconstructor"): copyreg._reconstructor,
    ("__builtin__", "object"): object,
    ("builtins", "object"): object,
    ("__builtin__", "bytearray"): bytearray,
    ("builtins", "bytearray"): bytearray,
    # Scalar builtins appear as reduce callables for edge cases - notably an
    # empty Python 2 `str`, which protocol 2 writes as `bytes()` rather than
    # through _codecs.encode. Stubbing those produced placeholder objects where
    # the game expected an empty string.
    ("__builtin__", "bytes"): bytes,
    ("builtins", "bytes"): bytes,
    ("__builtin__", "str"): str,
    ("builtins", "str"): str,
    ("__builtin__", "unicode"): str,
    ("__builtin__", "int"): int,
    ("builtins", "int"): int,
    ("__builtin__", "long"): int,
    ("__builtin__", "float"): float,
    ("builtins", "float"): float,
    ("__builtin__", "bool"): bool,
    ("builtins", "bool"): bool,
    ("__builtin__", "complex"): complex,
    ("builtins", "complex"): complex,
    ("__builtin__", "tuple"): tuple,
    ("builtins", "tuple"): tuple,
    ("__builtin__", "frozenset"): frozenset,
    ("builtins", "frozenset"): frozenset,
    # `type` is how pickle spells NoneType: save_type emits `type(None)`.
    # A stub here produced a non-callable placeholder that later blew up as
    # "'type' object is not callable" when the stream tried to construct with it.
    ("__builtin__", "type"): type,
    ("builtins", "type"): type,
}

#: Attributes Nodex adds to instances; never written back into a pickle.
INTERNAL_ATTRS = frozenset(
    {
        "_nodex_reduce",
        "_nodex_newobj",
        "_nodex_raw_state",
        "_nodex_state_form",
        "_nodex_bytes_keys",
    }
)

_MISSING = object()


#: Ren'Py container types, mapped to the builtin they subclass. Getting this
#: wrong surfaces immediately as "object has no attribute 'append'" or
#: "state is not a dictionary" while loading.
CONTAINER_BASES: dict[str, type] = {
    "RevertableList": list,
    "RevertableDict": dict,
    "RevertableSet": set,
    "RevertableDefaultDict": dict,
    "RevertableOrderedDict": dict,
    "DetDict": dict,
    "DetSet": set,
    "Set": set,
    "OrderedSet": set,
    # Builtins are stubbed too rather than resolved to the real thing, so that
    # a Python 2 save's `__builtin__.set` is written back as `__builtin__.set`
    # and not as `builtins.set`, which Ren'Py 7 could not load.
    "set": set,
    "frozenset": frozenset,
    "list": list,
    "dict": dict,
    "OrderedDict": dict,
    "defaultdict": dict,
    "Counter": dict,
    # Ren'Py stores tracebacks in the rollback log; StackSummary is a stdlib
    # list subclass, so the stream drives it with APPENDS.
    "StackSummary": list,
}


def infer_base_from_error(message: str) -> tuple[str, type] | None:
    """Work out which builtin a stub should have subclassed, from a failure.

    The unpickler only discovers that a class is really a list or dict subclass
    when an opcode fails on it::

        'StackSummary' object has no attribute 'append'

    Rather than maintaining an ever-growing table of every game's container
    types, we read the class name and the missing method out of the error and
    retry the load with a better base. Returns (class_name, base) or None.
    """
    match = re.search(r"'(\w+)' object has no attribute '(\w+)'", message)
    if not match:
        return None
    name, attribute = match.group(1), match.group(2)

    for base, methods in (
        (list, {"append", "extend", "insert"}),
        (dict, {"update", "setdefault", "keys", "__setitem__"}),
        (set, {"add", "difference_update"}),
    ):
        if attribute in methods:
            return name, base
    return None


def _decode_key(key: Any) -> Any:
    """Turn a Python 2 byte-string attribute name into text."""
    if isinstance(key, bytes):
        try:
            return key.decode("utf-8")
        except UnicodeDecodeError:
            return key.decode("latin-1")
    return key


class StubBase:
    """Shared behaviour for every synthesised class.

    `__setstate__` / `__getstate__` are deliberately symmetric so that an
    untouched object pickles back to something the game reconstructs
    identically.

    Ren'Py 7 runs Python 2, where attribute names in a pickled state are byte
    strings - a node arrives with `b'block'` and `b'name'` rather than `'block'`
    and `'name'`. Every attribute lookup in the analysis code would silently
    miss. Keys are therefore decoded on the way in and re-encoded on the way
    out, with the original spelling recorded per object so nothing changes in
    the file.
    """

    _nodex_origin: tuple[str, str] = ("", "")

    def _nodex_merge(self, mapping: dict) -> tuple[str, ...]:
        """Copy a state mapping into __dict__, decoding byte keys."""
        names: list[str] = []
        for key, value in mapping.items():
            name = _decode_key(key)
            if isinstance(key, bytes):
                encoded = self.__dict__.get("_nodex_bytes_keys")
                if encoded is None:
                    encoded = set()
                    self.__dict__["_nodex_bytes_keys"] = encoded
                encoded.add(name)
            self.__dict__[name] = value
            names.append(name)
        return tuple(names)

    def _nodex_restore(self, names: tuple[str, ...] | None, public: dict) -> dict | None:
        """Rebuild a state mapping with its original key spellings."""
        if names is None:
            return None
        encoded = self.__dict__.get("_nodex_bytes_keys") or frozenset()
        restored: dict = {}
        for name in names:
            if name not in public:
                continue
            key = name.encode("utf-8") if name in encoded else name
            restored[key] = public[name]
        return restored

    def __setstate__(self, state: Any) -> None:
        # The common case is a plain attribute dict, which we merge so the UI
        # can read and edit attributes naturally.
        if isinstance(state, dict):
            names = self._nodex_merge(state)
            object.__setattr__(self, "_nodex_state_form", ("dict", names, None))
            return

        # Classes with __slots__ - which every Ren'Py AST node uses - hand over
        # a (attributes, slots) pair. Flattening both into __dict__ is what lets
        # the script analyser read `node.block` and `node.name` directly; the
        # original split is recorded so the pair is rebuilt exactly on the way
        # back out.
        if (
            isinstance(state, tuple)
            and len(state) == 2
            and (state[0] is None or isinstance(state[0], dict))
            and (state[1] is None or isinstance(state[1], dict))
        ):
            attributes, slots = state
            attribute_names = self._nodex_merge(attributes) if attributes else (
                () if attributes is not None else None
            )
            slot_names = self._nodex_merge(slots) if slots else (
                () if slots is not None else None
            )
            object.__setattr__(
                self, "_nodex_state_form", ("slots", attribute_names, slot_names)
            )
            return

        # Anything else is opaque; keep it verbatim so it survives untouched.
        object.__setattr__(self, "_nodex_raw_state", state)

    def __getstate__(self) -> Any:
        raw = self.__dict__.get("_nodex_raw_state", _MISSING)
        if raw is not _MISSING:
            return raw

        public = {k: v for k, v in self.__dict__.items() if k not in INTERNAL_ATTRS}
        form = self.__dict__.get("_nodex_state_form")

        if form is None:
            return public

        kind, attribute_keys, slot_keys = form

        if kind == "slots":
            return (
                self._nodex_restore(attribute_keys, public),
                self._nodex_restore(slot_keys, public),
            )

        restored = self._nodex_restore(attribute_keys, public)
        if restored is None:
            return public

        # Attributes added after loading (there should be none) still ship.
        for name, value in public.items():
            if name not in attribute_keys:
                restored[name] = value
        return restored

    def __repr__(self) -> str:
        module, name = self._nodex_origin
        return f"<{module}.{name}>"


def _make_new(base: type):
    """`__new__` that tolerates whatever arguments the pickle supplies.

    Ren'Py classes often take required constructor arguments. The pickle may
    call them via REDUCE with the original arguments, which we cannot honour
    because we have no real implementation - so we accept and ignore them.
    """

    def __new__(cls, *args: Any, **kwargs: Any):
        if base is object:
            return object.__new__(cls)
        try:
            # Immutable bases such as frozenset take their contents here; if we
            # dropped the arguments the object would come back empty.
            return base.__new__(cls, *args, **kwargs)
        except TypeError:
            return base.__new__(cls)

    return __new__


def _make_init(base: type):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Sets are built by REDUCE with their contents as the first argument
        # rather than by a SETITEMS-style opcode, so seed them here.
        if base is set and args:
            try:
                set.update(self, args[0])
            except TypeError:
                pass

    return __init__


def synthesize(module: str, name: str, base: type | None = None) -> type:
    """Build a stand-in class for `module.name`."""
    if base is None:
        base = CONTAINER_BASES.get(name, object)

    namespace: dict[str, Any] = {
        "__module__": module,
        "__qualname__": name,
        "_nodex_origin": (module, name),
        "__new__": _make_new(base),
        "__init__": _make_init(base),
    }

    bases: tuple[type, ...] = (StubBase,) if base is object else (StubBase, base)
    return type(name, bases, namespace)


class StubRegistry:
    """Caches synthesised classes so identity is stable within a load."""

    def __init__(self, base_overrides: dict[str, type] | None = None) -> None:
        self._classes: dict[tuple[str, str], type] = {}
        #: Class name -> builtin base, learned from a previous failed attempt.
        self.base_overrides: dict[str, type] = dict(base_overrides or {})

    def get(self, module: str, name: str) -> Any:
        key = (module, name)
        real = REAL_GLOBALS.get(key)
        if real is not None:
            return real
        cls = self._classes.get(key)
        if cls is None:
            cls = synthesize(module, name, self.base_overrides.get(name))
            self._classes[key] = cls
        return cls

    @property
    def classes(self) -> dict[tuple[str, str], type]:
        return dict(self._classes)

    def __len__(self) -> int:
        return len(self._classes)
