# How Nodex reads each engine

This document records how each save and script format actually works, what was
measured against a real local library, and the wrong turns worth remembering.

## Ren'Py

### Saves are pickles of live game objects

A `.save` is a ZIP containing `log`, `json`, `screenshot.png`, `extra_info`,
`renpy_version` and (on Ren'Py 8) `signatures`. `log` is a **raw, uncompressed**
pickle of `(roots, rollback_log)`; `roots` maps `store.<name>` to its value.
The `persistent` file, by contrast, *is* zlib-compressed.

Unpickling normally needs the game's own classes, which exist only inside the
running game. Nodex synthesises a stand-in class for each one
(`engines/renpy/stubs.py`) and records where it came from, so it can be written
back under its original name.

Two details caused most of the early failures, and both are covered by tests:

- Ren'Py containers subclass `list`/`dict`/`set`, and the pickle drives them
  with opcodes that only work on the real builtin. Unknown subclasses are
  discovered from the failure message and the load is retried
  (`infer_base_from_error`).
- A few globals must resolve to the *genuine* callable rather than a stub,
  because the stream calls them: `_codecs.encode` rebuilds byte strings, and
  `type` is how pickle spells `NoneType`.

### Nothing is written unless it survives a round trip

Every write goes through `unpickle -> repickle -> unpickle -> structural compare`.
If the result differs anywhere, the write is refused and the file on disk is
left alone. Writes are atomic and every overwritten file is first copied to a
timestamped vault (`~/.nodex/backups`).

### Edited saves are re-signed

Ren'Py 8 signs the `log` with an ECDSA P-256 key (SHA-1 digest, raw 64-byte
signature, determined by verifying a real save rather than from documentation).
Editing invalidates that signature, which would make the game warn about the
save on every load. Nodex re-signs using the player's own key from
`%APPDATA%/RenPy/tokens/security_keys.txt`.

### Walkthroughs come from the AST, not from playing

A `menu` statement compiles to a Menu node whose items are
`(caption, condition, block)`. Reading the Python in each block gives the
effects; following its jumps gives the route. For ACCORD (which ships its
source, so the answer is checkable), Nodex reports `Blue → kira_choices += 1,
path_choice = "kira", jump kira_path`, matching `script.rpy` line 1286 exactly.

Two layouts have to be handled, and getting this wrong is silent rather than
loud:

- **Ren'Py 8** emits a flat, source-ordered statement list where a label's body
  follows it as siblings. **Ren'Py 7** nests the body inside `Label.block`.
  The traversal recurses while carrying the enclosing label, so both work.
- **Ren'Py 7 stores attribute names as byte strings** (`b'block'`, `b'name'`),
  because it pickles under Python 2. Every attribute lookup missed until keys
  were normalised on load. Deliverance went from 0 detected menus to 336.

The generated mod wraps `renpy.exports.menu`, which `Menu.execute` calls with
an identical signature on both Ren'Py 7 and 8. Menus are identified by
`renpy.get_filename_line()` matched against the AST node's own location, rather
than by caption text, which breaks the moment a game reuses "Yes" and "No".
The mod is ASCII-only and free of f-strings so Ren'Py 7's Python 2 can run it.

Because the mod only ever executes inside a game, its tests build a stand-in
`renpy` module, execute the generated code against it, and assert on the
captions that come back.

### Unlocking a gallery depends on how it was locked

Ren'Py's built-in `Gallery` unlocks on a persistent flag, on
`renpy.seen_image`, or on "all prior images seen". But surveying the installed
library showed only two of ten games use that class at all. The rest write
their own gallery screens gated on plain persistent flags like
`gallery_unlocked_ashley` or `image10_unlocked`. So flags are the primary path
here and `_seen_images` the secondary one.

The dangerous case is a flag that isn't a flag. Some games store a
`gallery_unlocked` **dict of unlocked scene names**, or a set of unlocked
scenes. Assigning `True` to either would *erase* the gallery rather than open
it. So the unlocker decides what to do from the stored value's shape: booleans
are set, collections are filled, and numbers and text are left alone.

Filling a collection needs to know what belongs in it, which is harvested from
calls like `persistent.seen.add("beach_scene")`. When a game composes its keys
at runtime instead (for example `f"gallery_{character}_{item_id}"` built from a
catalogue of objects), no literal exists to harvest. Rather than invent keys
and write them into someone's persistent file, those are reported as needing a
manual decision, with the entries already present shown so the naming scheme is
visible.

`_seen_images` is keyed by the image name split into a **tuple**, because
`renpy.seen_image` does `tuple(name.split())` before the lookup, which matches
the `imgname` tuple on an `Image` node exactly.

### Scripts are read, not decompiled

`.rpyc` is a `RENPY RPC2` container whose first slot is a zlib-compressed
pickle of the AST. Nodex reads those nodes directly rather than regenerating
`.rpy` source. That avoids depending on `unrpyc` (GPLv3, which would dictate
this project's licence) and gives more reliable answers about what a choice
does than re-parsing generated text would. Scripts sealed inside `.rpa`
archives are read in place.

This is what lets Nodex find a game's saves at all: Ren'Py stores them in
`%APPDATA%/RenPy/<config.save_directory>`, and nothing in the folder name
points there. `ACCORD-0.4.2-pc` saves into `ACCORD-1724460959`.

## RPG Maker MV and MZ

### The same data, stored two different ways

**MV** (`.rpgsave`) is `LZString.compressToBase64(JSON.stringify(save))`.
**MZ** (`.rmmzsave`) is zlib, but stored oddly. MZ compresses with
`pako.deflate(json, {to: "string"})`, which produces a JavaScript *binary
string* of one character per byte; writing that to disk encodes it as UTF-8, so
every byte above 0x7F becomes two. Reading one means undoing that (decode
UTF-8, re-encode latin-1) before inflating.

LZString is implemented here rather than taken from PyPI, because importing
that package calls `future.standard_library.install_aliases()`, which
monkey-patches the standard library process-wide. That is not acceptable inside
a long-running server. The package is still used as a **test oracle** when
installed, and this implementation matches it byte-for-byte on ordinary text.

It also fixes a bug that package has. LZString stores literals via
`charCodeAt`, which yields UTF-16 code units, so to JavaScript an emoji is
*two* characters. Python strings index by code point, so U+1F3AE is one
character that does not fit the 16-bit literal encoding and silently truncates
to U+F3AE. Astral characters (emoji in a player-entered name) are therefore
split into surrogate pairs before compressing. The reference package fails this
round-trip; this one passes.

Fidelity is semantic rather than byte-for-byte, and deliberately so: the
LZString build inside MV emits a few extra trailing zero bits that the
decompressor ignores, because the stream already ended at its end-of-stream
marker. Our output is a handful of characters shorter and decodes to identical
JSON. Insisting on identical bytes would fail on most of the corpus for no real
reason, so the gate is `decode(encode(decode(f))) == decode(f)`, the same
structural comparison the Ren'Py layer uses. Unmodified saves are never
rewritten, so the difference only ever reaches files the user actually edited.

Switch and variable names live in the game's `data/System.json`, not the save.
When a game folder is found the table shows "Met Sarah" instead of "Switch 42";
without one it falls back to numeric IDs and says so.

## Unity

### No standard save format, so handlers were chosen from evidence

Unity ships nothing standard, so rather than guessing, the local library was
surveyed first: 47 companies under `AppData/LocalLow`, 200 candidate files.
That produced a clear ranking:

| Format | Files | Status |
| --- | --- | --- |
| **Naninovel** (`.nson`) | 109 | Supported |
| **Plain JSON** (`.json`, `.savefile`, `.dat`, unencrypted `.es3`) | 20 | Supported |
| Encrypted / opaque (`.bytes`, `.sav`, `.save`, encrypted `.es3`, `.ngp1`) | 71 | Detected, not opened |

Naninovel is a *raw deflate* stream (no zlib header, `wbits=-15`) wrapping
UTF-8 JSON with a BOM. That cost a wrong turn worth recording: the first probe
tested `lstrip()[:1] in "{["` and reported the format unknown, because
`lstrip()` does not strip a BOM. The compression was being decoded correctly
the whole time; the check was wrong.

Inside is Naninovel's `objectJsonMap`: parallel `keys`/`values` arrays where
each key is a .NET type name and **each value is itself a JSON string**. The
custom variables live in one of those entries, so only that entry is parsed and
re-serialised. The other subsystem states are left as the exact strings they
arrived as. A test asserts this by planting deliberately odd whitespace in a
neighbouring payload and checking it survives an edit byte-for-byte.

Naninovel stores every custom variable as a **string**, numbers included
(`"1"`, `"0.15625"`), so values are written back as strings rather than
helpfully converted into types the game would not read. Booleans are written
`True`/`False`, matching .NET's `ToString`.

Unity's `persistentDataPath` is `LocalLow/<Company>/<Product>`, and a game's
`<Game>_Data/app.info` holds exactly those two lines, so save locations are
resolved precisely, the same way `config.save_directory` is read out of a
compiled Ren'Py script.

The encrypted third is genuinely out of reach: Easy Save 3 uses AES with a
password compiled into each game, so opening those would mean extracting a key
from every game's binary individually. They are reported as encrypted rather
than guessed at.

## Verification against a real library

```bash
cd backend
python -m pytest tests -q          # fast, builds its own fixtures
python tools/corpus_check.py       # sweeps your whole Ren'Py save library
python tools/analyze_check.py      # maps choices in every installed game
python tools/rpg_corpus_check.py   # sweeps your RPG Maker saves
python tools/unity_corpus_check.py # sweeps AppData/LocalLow
```

`corpus_check.py` is the primary correctness signal for the save layer: it
loads every save it can find, writes it back, reloads it, and compares the two
graphs structurally. `analyze_check.py` is the equivalent for script reading.
It prints per-game menu counts and flags games where nothing was found, which
is how the Ren'Py 7 byte-key bug surfaced: a game with 229 `menu:` statements
in its source was reporting zero.
