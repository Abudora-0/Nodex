"""Load a Ren'Py pickle without the game's classes present.

Beyond substituting stub classes, this unpickler *records how every object was
constructed*. That is the part that makes writing the save back safe.

A plain re-pickle would emit whatever `object.__reduce_ex__` decides, which is
not necessarily what the game originally used. For a `set` subclass in
particular, the default protocol-2 reduction drops the contents entirely,
because set items travel as constructor arguments rather than as APPENDS
opcodes. Silently emptying a set in someone's save is exactly the class of bug
this project cannot afford.

So we subclass the pure-Python unpickler (the C one exposes no hooks) and note,
per object, whether it arrived via REDUCE or NEWOBJ and with what arguments.
`repickler` then replays precisely that.
"""

from __future__ import annotations

import io
import pickle
from typing import Any, BinaryIO

from ...core.errors import SaveFormatError
from .stubs import StubRegistry, infer_base_from_error


class RenpyUnpickler(pickle._Unpickler):  # type: ignore[misc]
    """Unpickler that synthesises missing classes and records construction."""

    def __init__(
        self,
        file: BinaryIO,
        registry: StubRegistry | None = None,
        *,
        encoding: str = "ASCII",
        errors: str = "strict",
    ) -> None:
        super().__init__(file, fix_imports=True, encoding=encoding, errors=errors)
        self.registry = registry if registry is not None else StubRegistry()
        #: (module, name) pairs the stream referenced, for diagnostics.
        self.seen_classes: set[tuple[str, str]] = set()
        #: Persistent ids encountered; Ren'Py saves normally have none.
        self.persistent_ids: list[Any] = []

    def find_class(self, module: str, name: str) -> Any:
        self.seen_classes.add((module, name))
        return self.registry.get(module, name)

    def persistent_load(self, pid: Any) -> Any:
        self.persistent_ids.append(pid)
        return _PersistentRef(pid)

    # -- construction recording -------------------------------------------
    #
    # Each override mirrors CPython's implementation and then annotates the
    # object it produced. Annotations are set through object.__setattr__ so
    # they survive classes that customise attribute setting.

    def load_reduce(self) -> None:
        stack = self.stack
        args = stack.pop()
        func = stack[-1]
        obj = func(*args)
        _tag(obj, "_nodex_reduce", (func, args))
        stack[-1] = obj

    dispatch = dict(pickle._Unpickler.dispatch)  # type: ignore[attr-defined]
    dispatch[pickle.REDUCE[0]] = load_reduce

    def load_newobj(self) -> None:
        args = self.stack.pop()
        cls = self.stack.pop()
        obj = cls.__new__(cls, *args)
        _tag(obj, "_nodex_newobj", (cls, args, None))
        self.append(obj)

    dispatch[pickle.NEWOBJ[0]] = load_newobj

    def load_newobj_ex(self) -> None:
        kwargs = self.stack.pop()
        args = self.stack.pop()
        cls = self.stack.pop()
        obj = cls.__new__(cls, *args, **kwargs)
        _tag(obj, "_nodex_newobj", (cls, args, kwargs))
        self.append(obj)

    dispatch[pickle.NEWOBJ_EX[0]] = load_newobj_ex


class _PersistentRef:
    """Placeholder for a persistent id we cannot resolve outside the game."""

    __slots__ = ("pid",)

    def __init__(self, pid: Any) -> None:
        self.pid = pid

    def __repr__(self) -> str:
        return f"<persistent {self.pid!r}>"


def _tag(obj: Any, attr: str, value: Any) -> None:
    try:
        object.__setattr__(obj, attr, value)
    except (AttributeError, TypeError):
        # Immutables (tuple, str, int) cannot carry annotations, and do not
        # need to - their default reduction is already exact.
        pass


def detect_protocol(data: bytes) -> int:
    """Read the protocol out of a pickle's PROTO opcode, defaulting to 2."""
    if data[:1] == b"\x80" and len(data) > 1:
        return data[1]
    return 2


def encoding_for(python_major: int) -> str:
    """Pick the str-decoding policy for a pickle written by Ren'Py.

    Ren'Py 7 runs Python 2, where `str` is bytes. Decoding those as text would
    corrupt any non-UTF-8 payload, so we keep them as bytes and let the
    repickler emit them back as bytes.
    """
    return "bytes" if python_major == 2 else "ASCII"


#: How many times to retry a load while learning container base classes.
MAX_BASE_RETRIES = 8


def loads(
    data: bytes,
    *,
    python_major: int = 3,
    registry: StubRegistry | None = None,
) -> tuple[Any, RenpyUnpickler]:
    """Unpickle `data`, returning the object graph and the unpickler used.

    If a stub turns out to need a builtin base it did not get - because the
    game subclasses list or dict somewhere we have no table entry for - the
    load is retried with that base applied. Each retry learns one more class,
    so a handful of attempts covers even unusual games.

    The unpickler is returned as well because it carries the stub registry,
    which the repickler needs in order to write classes back out.
    """
    overrides: dict[str, type] = dict(registry.base_overrides) if registry else {}

    for _ in range(MAX_BASE_RETRIES):
        attempt_registry = StubRegistry(overrides)
        unpickler = RenpyUnpickler(
            io.BytesIO(data),
            registry=attempt_registry,
            encoding=encoding_for(python_major),
            errors="replace",
        )
        try:
            payload = unpickler.load()
        except AttributeError as exc:
            learned = infer_base_from_error(str(exc))
            if learned is None or overrides.get(learned[0]) is learned[1]:
                raise
            overrides[learned[0]] = learned[1]
            continue

        if registry is not None:
            # Hand the caller's registry the classes actually used, so the
            # repickler writes them back under the right names.
            registry._classes.update(attempt_registry._classes)
            registry.base_overrides.update(overrides)
        return payload, unpickler

    raise SaveFormatError(
        f"could not settle on container types after {MAX_BASE_RETRIES} attempts"
    )


def _looks_like_roots(candidate: Any) -> int:
    """Score a dict on how much it resembles a Ren'Py store mapping.

    The store dictionary is recognisable because nearly every key is a string
    of the form `store.<name>`. Returns the number of matching keys, or 0.
    """
    if not isinstance(candidate, dict) or not candidate:
        return 0
    matches = 0
    for key in candidate:
        if isinstance(key, str) and key.startswith("store."):
            matches += 1
        elif isinstance(key, bytes) and key.startswith(b"store."):
            matches += 1
    return matches if matches >= max(3, len(candidate) // 2) else 0


def load_partial(
    data: bytes, *, python_major: int = 3
) -> tuple[Any, dict[str, Any] | None, Exception | None]:
    """Unpickle as far as the stream allows, salvaging the store if it fails.

    Returns `(payload, salvaged_roots, error)`. On a clean load the payload is
    the full `(roots, log)` tuple and error is None. On a truncated stream the
    payload is None, and we sweep the unpickler's memo and stack for the store
    dictionary - which is usually fully built well before the end of the file,
    because the rollback log that follows it is far larger.
    """
    registry = StubRegistry()
    try:
        # Go through `loads` first so the container-base retries apply here too;
        # a save is not corrupt just because it uses an unfamiliar list subclass.
        return loads(data, python_major=python_major, registry=registry)[0], None, None
    except Exception as exc:  # noqa: BLE001
        error = exc

    # Replay once more without retries to get an unpickler we can sweep.
    unpickler = RenpyUnpickler(
        io.BytesIO(data),
        registry=StubRegistry(registry.base_overrides),
        encoding=encoding_for(python_major),
        errors="replace",
    )
    try:
        unpickler.load()
    except Exception:  # noqa: BLE001 - expected; we want the partial state
        pass

    best: dict[str, Any] | None = None
    best_score = 0

    pools: list[Any] = []
    pools.extend(getattr(unpickler, "memo", {}).values())
    pools.extend(getattr(unpickler, "stack", []) or [])
    for frame in getattr(unpickler, "metastack", []) or []:
        pools.extend(frame)

    for candidate in pools:
        score = _looks_like_roots(candidate)
        if score > best_score:
            best, best_score = candidate, score

    return None, best, error
