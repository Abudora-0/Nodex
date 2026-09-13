"""RPG Maker MV/MZ: the LZString port, the containers, and editing.

The LZString implementation is the part most likely to be subtly wrong, so it
is tested both against known vectors and (when the reference PyPI package
happens to be installed) against that package directly. The corpus checker
(`tools/rpg_corpus_check.py`) covers the same ground across 836 real files.
"""

from __future__ import annotations

import json
import zlib

import pytest

from nodex.core.errors import NodexError
from nodex.engines.rpgmaker import codec, lzstring
from nodex.engines.rpgmaker import variables as rpg_vars

try:  # pragma: no cover - only present while cross-checking
    import lzstring as reference_lzstring

    REFERENCE = reference_lzstring.LZString()
except Exception:  # noqa: BLE001
    REFERENCE = None


# ---------------------------------------------------------------------------
# LZString
# ---------------------------------------------------------------------------


ROUND_TRIP_SAMPLES = [
    "",
    "a",
    "hello world",
    "{}",
    '{"gold":5000,"steps":243}',
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "The quick brown fox jumps over the lazy dog. " * 40,
    '{"name":"ゆき","note":"日本語テキスト"}',
    # Code points either side of the 8-bit boundary, where the compressor
    # switches between its 8-bit and 16-bit literal encodings.
    "".join(chr(c) for c in (0, 1, 0x7F, 0x80, 0xFF, 0x100)),
    json.dumps({"switches": [None, True, False] * 100}),
]


@pytest.mark.parametrize("text", ROUND_TRIP_SAMPLES)
def test_round_trip(text):
    encoded = lzstring.compress_to_base64(text)
    assert lzstring.decompress_from_base64(encoded) == (text if text else None or text)


def test_empty_string_compresses_and_decompresses():
    encoded = lzstring.compress_to_base64("")
    assert lzstring.decompress_from_base64(encoded) == ""


def test_decompressing_empty_input_is_none():
    assert lzstring.decompress_from_base64("") is None


def test_output_is_base64_alphabet_only():
    encoded = lzstring.compress_to_base64("some sample text to compress")
    assert set(encoded) <= set(lzstring.KEY_BASE64)


def test_output_length_is_a_multiple_of_four():
    for text in ("a", "ab", "abc", "abcd", "hello world"):
        assert len(lzstring.compress_to_base64(text)) % 4 == 0


@pytest.mark.skipif(REFERENCE is None, reason="reference lzstring not installed")
@pytest.mark.parametrize("text", ROUND_TRIP_SAMPLES)
def test_matches_reference_implementation(text):
    assert lzstring.compress_to_base64(text) == REFERENCE.compressToBase64(text)


@pytest.mark.skipif(REFERENCE is None, reason="reference lzstring not installed")
def test_decodes_what_the_reference_encodes():
    text = json.dumps({"party": {"_gold": 12345}, "switches": [None, True]})
    assert lzstring.decompress_from_base64(REFERENCE.compressToBase64(text)) == text


def test_unicode_above_the_basic_plane_survives():
    text = "emoji 🎮 and accents éàü"
    assert lzstring.decompress_from_base64(lzstring.compress_to_base64(text)) == text


# ---------------------------------------------------------------------------
# Container formats
# ---------------------------------------------------------------------------


SAMPLE_SAVE = {
    "switches": {"@c": "Game_Switches", "_data": {"@c": "Array", "@a": [None, True]}},
    "variables": {"@c": "Game_Variables", "_data": {"@c": "Array", "@a": [None, 7]}},
    "party": {"@c": "Game_Party", "_gold": 500, "_steps": 12},
}


def make_mv(data) -> bytes:
    return lzstring.compress_to_base64(codec.to_json(data)).encode("utf-8")


def make_mz(data) -> bytes:
    raw = zlib.compress(codec.to_json(data).encode("utf-8"), 1)
    return raw.decode("latin-1").encode("utf-8")


def test_mv_decodes():
    decoded = codec.decode(make_mv(SAMPLE_SAVE))
    assert decoded.flavour == "mv"
    assert decoded.data == SAMPLE_SAVE


def test_mz_decodes():
    """MZ stores a JS binary string, so the bytes come back UTF-8 inflated."""
    decoded = codec.decode(make_mz(SAMPLE_SAVE))
    assert decoded.flavour == "mz"
    assert decoded.data == SAMPLE_SAVE


def test_mz_plain_json_decodes():
    raw = codec.to_json(SAMPLE_SAVE).encode("utf-8")
    decoded = codec.decode(raw)
    assert decoded.flavour == "mz-plain"
    assert decoded.data == SAMPLE_SAVE


@pytest.mark.parametrize("flavour", ["mv", "mz", "mz-plain"])
def test_encode_decode_round_trip(flavour):
    encoded = codec.encode(SAMPLE_SAVE, flavour)
    assert codec.decode(encoded).data == SAMPLE_SAVE


def test_jsonex_tags_survive_a_round_trip():
    """Losing @c would stop the game rebuilding its objects."""
    decoded = codec.decode(codec.encode(SAMPLE_SAVE, "mv"))
    assert decoded.data["switches"]["@c"] == "Game_Switches"
    assert decoded.data["switches"]["_data"]["@c"] == "Array"


def test_empty_file_is_rejected():
    with pytest.raises(NodexError):
        codec.decode(b"")


def test_truncated_mz_is_rejected():
    raw = make_mz(SAMPLE_SAVE)[:20]
    with pytest.raises(NodexError):
        codec.decode(raw)


def test_garbage_is_rejected():
    with pytest.raises(NodexError):
        codec.decode(b"this is not a save file at all")


def test_verify_roundtrip_accepts_a_faithful_encode():
    decoded = codec.decode(make_mv(SAMPLE_SAVE))
    codec.verify_roundtrip(decoded, codec.encode(decoded.data, "mv"))


def test_unchanged_save_is_not_rewritten(tmp_path):
    path = tmp_path / "file1.rpgsave"
    path.write_bytes(make_mv(SAMPLE_SAVE))
    before = path.read_bytes()

    decoded = codec.load(path)
    assert codec.save_to(decoded, path) is None
    assert path.read_bytes() == before


def test_edited_save_is_written(tmp_path):
    path = tmp_path / "file1.rpgsave"
    path.write_bytes(make_mv(SAMPLE_SAVE))

    decoded = codec.load(path)
    decoded.data["party"]["_gold"] = 999
    assert codec.save_to(decoded, path) is not None
    assert codec.load(path).data["party"]["_gold"] == 999


# ---------------------------------------------------------------------------
# The editable table
# ---------------------------------------------------------------------------


def fresh() -> dict:
    return json.loads(json.dumps(SAMPLE_SAVE))


def test_entries_skip_index_zero():
    """RPG Maker leaves index 0 unused, and it is not a real switch."""
    addresses = [e.address for e in rpg_vars.list_entries(fresh())]
    assert "switch.0" not in addresses
    assert "switch.1" in addresses


def test_unset_entries_are_hidden_by_default():
    save = fresh()
    save["switches"]["_data"]["@a"] = [None, None, True]
    visible = [e.address for e in rpg_vars.list_entries(save)]
    assert visible == ["switch.2", "variable.1", "party.gold", "party.steps"]

    everything = [e.address for e in rpg_vars.list_entries(save, include_unset=True)]
    assert "switch.1" in everything


def test_party_fields_are_listed():
    entries = {e.address: e.value for e in rpg_vars.list_entries(fresh())}
    assert entries["party.gold"] == 500
    assert entries["party.steps"] == 12


def test_setting_a_switch():
    save = fresh()
    assert rpg_vars.set_entry(save, "switch.1", "false") is False
    assert save["switches"]["_data"]["@a"][1] is False


def test_setting_a_variable_keeps_it_numeric():
    """A variable holding 5 must not become "5" or event comparisons break."""
    save = fresh()
    stored = rpg_vars.set_entry(save, "variable.1", "42")
    assert stored == 42 and isinstance(stored, int)


def test_setting_gold():
    save = fresh()
    assert rpg_vars.set_entry(save, "party.gold", "99999") == 99999


def test_switch_array_grows_when_needed():
    """A switch the player never triggered may not exist in the array yet."""
    save = fresh()
    rpg_vars.set_entry(save, "switch.20", True)
    assert save["switches"]["_data"]["@a"][20] is True
    assert len(save["switches"]["_data"]["@a"]) == 21


def test_edits_write_through_to_the_encoded_save():
    save = fresh()
    rpg_vars.set_entry(save, "party.gold", 1234)
    assert codec.decode(codec.encode(save, "mv")).data["party"]["_gold"] == 1234


def test_bad_number_is_refused():
    save = fresh()
    with pytest.raises(rpg_vars.EditError):
        rpg_vars.set_entry(save, "variable.1", "not a number")


def test_unknown_address_is_refused():
    with pytest.raises(rpg_vars.EditError):
        rpg_vars.set_entry(fresh(), "nonsense.1", 1)


def test_id_zero_is_refused():
    with pytest.raises(rpg_vars.EditError):
        rpg_vars.set_entry(fresh(), "switch.0", True)


@pytest.mark.parametrize(
    "text,expected",
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False)],
)
def test_boolean_coercion(text, expected):
    assert rpg_vars.coerce(True, text) is expected


def test_names_are_used_when_available(tmp_path):
    data = tmp_path / "www" / "data"
    data.mkdir(parents=True)
    (data / "System.json").write_text(
        json.dumps({"switches": ["", "Met Sarah"], "variables": ["", "Affection"]}),
        encoding="utf-8",
    )

    names = rpg_vars.load_names(tmp_path)
    assert names is not None
    entries = {e.address: e.display for e in rpg_vars.list_entries(fresh(), names)}
    assert entries["switch.1"] == "Met Sarah"
    assert entries["variable.1"] == "Affection"


def test_missing_game_folder_falls_back_to_numbers(tmp_path):
    assert rpg_vars.load_names(tmp_path) is None
    entries = {e.address: e.display for e in rpg_vars.list_entries(fresh())}
    assert entries["switch.1"] == "Switch 1"
