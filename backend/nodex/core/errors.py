"""Exception hierarchy shared by every engine backend."""


class NodexError(Exception):
    """Base for all errors Nodex raises deliberately."""


class UnsupportedGame(NodexError):
    """The folder does not look like a game we know how to handle."""


class SaveFormatError(NodexError):
    """A save file could not be parsed as the format it claimed to be."""


class RoundTripError(NodexError):
    """A save failed the re-serialisation safety gate, so it was not written.

    This is the guard that stops Nodex from ever handing a game a save file it
    cannot load. See `nodex.engines.renpy.roundtrip`.
    """


class RepairFailed(NodexError):
    """Every recovery strategy in the ladder was exhausted."""
