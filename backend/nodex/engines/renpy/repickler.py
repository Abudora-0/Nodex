"""Write a stubbed object graph back out as a pickle the game can load.

Two problems have to be solved here.

**Naming.** The standard pickler verifies every class it writes by importing
the module and checking the attribute matches. Our stub classes claim to live
in the game's modules, which do not exist in this process, so verification
would fail. We therefore emit the GLOBAL opcode directly from the origin we
recorded at load time.

**Construction.** We replay the exact REDUCE / NEWOBJ shape the original pickle
used, rather than letting `__reduce_ex__` invent a new one. See `unpickler` for
why that matters.
"""

from __future__ import annotations

import copyreg
import io
import pickle
from typing import Any

from .stubs import StubBase


class RenpyPickler(pickle._Pickler):  # type: ignore[misc]
    """Pickler that honours recorded origins and construction shapes."""

    def reducer_override(self, obj: Any) -> Any:
        # Classes are handled by save_global below.
        if isinstance(obj, type) or not isinstance(obj, StubBase):
            return NotImplemented

        recorded = obj.__dict__
        state = obj.__getstate__()
        if isinstance(state, dict) and not state:
            state = None

        listitems = iter(obj) if isinstance(obj, list) else None
        dictitems = iter(obj.items()) if isinstance(obj, dict) else None

        reduce_rec = recorded.get("_nodex_reduce")
        if reduce_rec is not None:
            func, args = reduce_rec
            if isinstance(obj, (set, frozenset)) and len(args) == 1:
                # Set contents ride along as a constructor argument, so rebuild
                # them from the live object - otherwise edits would be lost.
                args = (list(obj),)
            return (func, args, state, listitems, dictitems)

        newobj_rec = recorded.get("_nodex_newobj")
        if newobj_rec is not None:
            cls, args, kwargs = newobj_rec
            if kwargs:
                return (
                    copyreg.__newobj_ex__,
                    (cls, tuple(args), kwargs),
                    state,
                    listitems,
                    dictitems,
                )
            return (
                copyreg.__newobj__,
                (cls, *tuple(args)),
                state,
                listitems,
                dictitems,
            )

        return NotImplemented

    def save_global(self, obj: Any, name: str | None = None) -> None:
        origin = getattr(obj, "_nodex_origin", None)
        if not origin or not origin[0]:
            super().save_global(obj, name)
            return

        module_name, qualname = origin
        write = self.write
        if self.proto >= 4:
            self.save(module_name)
            self.save(qualname)
            write(pickle.STACK_GLOBAL)
        else:
            write(
                pickle.GLOBAL
                + _encode_name(module_name)
                + b"\n"
                + _encode_name(qualname)
                + b"\n"
            )
        self.memoize(obj)


def _encode_name(name: str) -> bytes:
    try:
        return name.encode("ascii")
    except UnicodeEncodeError:
        return name.encode("utf-8")


def dumps(obj: Any, protocol: int = 2) -> bytes:
    """Serialise a stubbed object graph.

    `fix_imports` stays on so that Python 3 module names in `REAL_GLOBALS` are
    rewritten to their Python 2 spellings when targeting Ren'Py 7.
    """
    buf = io.BytesIO()
    RenpyPickler(buf, protocol=protocol, fix_imports=True).dump(obj)
    return buf.getvalue()
