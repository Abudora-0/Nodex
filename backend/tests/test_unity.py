"""Unity saves: the Naninovel container, plain JSON, and editing.

The behaviour most worth pinning down is that editing one variable does not
disturb anything else. A Naninovel save holds a dozen serialised subsystem
states as opaque JSON strings, and re-serialising all of them would silently
reformat parts of the file the user never touched.
"""

from __future__ import annotations

import json
import zlib

import pytest

from nodex.core.errors import NodexError
from nodex.engines.unity import codec
from nodex.engines.unity import variables as unity_vars


def naninovel_bytes(local=None, global_map=None, extra=None) -> bytes:
    """Build a save shaped the way Naninovel writes them."""
    payload = {}
    if local is not None:
        payload["LocalVariableMap"] = {
            "keys": list(local.keys()),
            "values": list(local.values()),
        }
    if global_map is not None:
        payload["GlobalVariableMap"] = {
            "keys": list(global_map.keys()),
            "values": list(global_map.values()),
        }

    keys = ["Naninovel.CustomVariableManager+GameState, Elringus.Naninovel.Runtime"]
    values = [json.dumps(payload, separators=(",", ":"))]

    if extra is not None:
        keys.append("Naninovel.ScriptPlayer+GameState, Elringus.Naninovel.Runtime")
        values.append(extra)

    save = {"objectJsonMap": {"keys": keys, "values": values}}
    text = "﻿" + json.dumps(save, separators=(",", ":"), ensure_ascii=False)
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(text.encode("utf-8")) + compressor.flush()


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


def test_naninovel_decodes():
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50"}))
    assert decoded.flavour == "naninovel"
    assert decoded.had_bom is True


def test_plain_json_decodes():
    raw = json.dumps({"gold": 5, "name": "x"}).encode("utf-8")
    decoded = codec.decode(raw)
    assert decoded.flavour == "json"
    assert decoded.data["gold"] == 5


def test_json_with_bom_keeps_its_bom():
    raw = "﻿" + json.dumps({"a": 1})
    decoded = codec.decode(raw.encode("utf-8"))
    assert decoded.had_bom is True
    assert codec.encode(decoded).startswith(b"\xef\xbb\xbf")


def test_json_without_bom_gains_none():
    decoded = codec.decode(json.dumps({"a": 1}).encode("utf-8"))
    assert decoded.had_bom is False
    assert not codec.encode(decoded).startswith(b"\xef\xbb\xbf")


def test_encrypted_save_is_reported_clearly():
    with pytest.raises(codec.EncryptedSave) as caught:
        codec.decode(bytes(range(256)) * 4)
    assert "encrypted" in str(caught.value).lower()


def test_empty_file_is_rejected():
    with pytest.raises(NodexError):
        codec.decode(b"")


@pytest.mark.parametrize("payload", [{"a": 1}, {"nested": {"b": [1, 2, 3]}}])
def test_json_round_trip(payload):
    decoded = codec.decode(json.dumps(payload).encode("utf-8"))
    assert codec.decode(codec.encode(decoded)).data == payload


def test_naninovel_round_trip():
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50", "Name": "Ada"}))
    assert codec.decode(codec.encode(decoded)).data == decoded.data


def test_verify_roundtrip_accepts_a_faithful_encode():
    decoded = codec.decode(naninovel_bytes(local={"A": "1"}))
    codec.verify_roundtrip(decoded, codec.encode(decoded))


def test_unchanged_save_is_not_rewritten(tmp_path):
    path = tmp_path / "GameSave001.nson"
    path.write_bytes(naninovel_bytes(local={"A": "1"}))
    before = path.read_bytes()

    decoded = codec.load(path)
    assert codec.save_to(decoded, path) is None
    assert path.read_bytes() == before


def test_edited_save_is_written(tmp_path):
    path = tmp_path / "GameSave001.nson"
    path.write_bytes(naninovel_bytes(local={"Gold": "50"}))

    decoded = codec.load(path)
    unity_vars.set_entry(decoded, "LocalVariableMap.Gold", "9999")
    assert codec.save_to(decoded, path) is not None

    reloaded = codec.load(path)
    values = {e.address: e.value for e in unity_vars.list_entries(reloaded)}
    assert values["LocalVariableMap.Gold"] == "9999"


# ---------------------------------------------------------------------------
# The editable table
# ---------------------------------------------------------------------------


def test_naninovel_entries_are_listed():
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50", "AllCG": "1"}))
    entries = {e.address: e.value for e in unity_vars.list_entries(decoded)}
    assert entries == {
        "LocalVariableMap.Gold": "50",
        "LocalVariableMap.AllCG": "1",
    }


def test_global_variables_are_grouped_separately():
    decoded = codec.decode(naninovel_bytes(global_map={"G_CG": "1"}))
    entry = unity_vars.list_entries(decoded)[0]
    assert entry.group == "global"
    assert entry.address == "GlobalVariableMap.G_CG"


def test_values_stay_strings():
    """Naninovel stores every custom variable as a string, numbers included."""
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50"}))
    stored = unity_vars.set_entry(decoded, "LocalVariableMap.Gold", 9999)
    assert stored == "9999" and isinstance(stored, str)


def test_booleans_are_written_dotnet_style():
    decoded = codec.decode(naninovel_bytes(local={"Flag": "False"}))
    assert unity_vars.set_entry(decoded, "LocalVariableMap.Flag", True) == "True"


def test_other_payloads_are_left_untouched():
    """The decisive test: editing one variable must not reformat the rest."""
    other = '{"playedScripts":["a","b"],"spacing":   3}'
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50"}, extra=other))

    unity_vars.set_entry(decoded, "LocalVariableMap.Gold", "1")

    # Byte-for-byte identical, odd whitespace and all.
    assert decoded.data["objectJsonMap"]["values"][1] == other


def test_unknown_variable_is_refused():
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50"}))
    with pytest.raises(unity_vars.EditError):
        unity_vars.set_entry(decoded, "LocalVariableMap.Nope", "1")


def test_unknown_map_is_refused():
    decoded = codec.decode(naninovel_bytes(local={"Gold": "50"}))
    with pytest.raises(unity_vars.EditError):
        unity_vars.set_entry(decoded, "Nonsense.Gold", "1")


def test_json_entries_use_dotted_paths():
    raw = json.dumps({"player": {"gold": 5, "name": "Ada"}, "day": 3}).encode()
    entries = {e.address: e.value for e in unity_vars.list_entries(codec.decode(raw))}
    assert entries["player.gold"] == 5
    assert entries["player.name"] == "Ada"
    assert entries["day"] == 3


def test_json_edit_preserves_type():
    raw = json.dumps({"player": {"gold": 5}}).encode()
    decoded = codec.decode(raw)
    stored = unity_vars.set_entry(decoded, "player.gold", "500")
    assert stored == 500 and isinstance(stored, int)


def test_json_bad_value_is_refused():
    decoded = codec.decode(json.dumps({"gold": 5}).encode())
    with pytest.raises(unity_vars.EditError):
        unity_vars.set_entry(decoded, "gold", "lots")


def test_json_missing_path_is_refused():
    decoded = codec.decode(json.dumps({"gold": 5}).encode())
    with pytest.raises(unity_vars.EditError):
        unity_vars.set_entry(decoded, "player.gold", 1)


def test_json_lists_are_addressable():
    raw = json.dumps({"flags": [True, False]}).encode()
    decoded = codec.decode(raw)
    assert unity_vars.set_entry(decoded, "flags.1", "true") is True
    assert decoded.data["flags"] == [True, True]
