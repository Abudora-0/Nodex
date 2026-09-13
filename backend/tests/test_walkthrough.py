"""Choice analysis, and the generated mod's runtime behaviour.

The mod is a `.rpy` file that only ever runs inside Ren'Py, so the interesting
question is whether its hook does the right thing when Ren'Py calls it. Rather
than assume, these tests build a stand-in `renpy` module with the same surface
the mod touches, execute the generated code against it, and check the captions
that come out.
"""

from __future__ import annotations

import textwrap
import types

import pytest

from nodex.engines.renpy import walkthrough
from nodex.engines.renpy.analyze import (
    Analysis,
    Choice,
    Effect,
    MenuPoint,
    effects_from_source,
    looks_like_ending,
)


# ---------------------------------------------------------------------------
# Reading effects out of script Python
# ---------------------------------------------------------------------------


def describe_all(source: str) -> list[str]:
    return [effect.describe() for effect in effects_from_source(source)]


def test_augmented_assignment():
    assert describe_all("love += 1") == ["+1 love"]
    assert describe_all("trust -= 2") == ["-2 trust"]


def test_plain_assignment():
    assert describe_all("path_choice = 'kira'") == ["path_choice = 'kira'"]


def test_attribute_targets_are_kept():
    assert describe_all("persistent.seen_ending = True") == [
        "persistent.seen_ending = True"
    ]


def test_engine_plumbing_is_ignored():
    assert describe_all("config.rollback_enabled = False") == []
    assert describe_all("_window_subtitle = 'x'") == []


def test_mutating_calls_are_reported():
    assert describe_all("inventory.append('key')") == ["inventory.append('key')"]


def test_multiple_statements():
    source = textwrap.dedent(
        """
        kira_choices += 1
        path_choice = "kira"
        """
    )
    assert describe_all(source) == ["+1 kira_choices", "path_choice = 'kira'"]


def test_unparseable_python_is_survivable():
    assert effects_from_source("this is ( not python") == []


def test_ending_labels_are_recognised():
    assert looks_like_ending("bad_ending")
    assert looks_like_ending("chapter_end")
    assert not looks_like_ending("kira_path")


# ---------------------------------------------------------------------------
# Mod generation and its runtime behaviour
# ---------------------------------------------------------------------------


def make_analysis() -> Analysis:
    menu = MenuPoint(
        script="script.rpyc", filename="game/script.rpy", linenumber=1285, label="start"
    )
    menu.choices = [
        Choice(
            index=0,
            caption="Blue",
            condition=None,
            effects=[Effect("kira_choices", "+=", "1")],
            jumps=["kira_path"],
        ),
        Choice(index=1, caption="Orange", condition=None, effects=[], inert=True),
    ]
    analysis = Analysis(game_dir="game")
    analysis.menus = [menu]
    analysis.scripts_read = 1
    return analysis


def run_mod(source: str):
    """Execute the mod's init block against a stand-in Ren'Py, return the module."""
    body = source.split("init 1999 python:", 1)[1]
    code = textwrap.dedent(body)

    exports = types.ModuleType("renpy.exports")

    def original_menu(items, *args, **kwargs):
        # Stands in for the real menu: hand back what it was given.
        return items

    exports.menu = original_menu

    renpy = types.ModuleType("renpy")
    renpy.exports = exports
    renpy.get_filename_line = lambda: ("game/script.rpy", 1285)

    namespace = {"renpy": renpy}
    exec(compile(code, "nodex_walkthrough.rpy", "exec"), namespace)
    return renpy


def test_generated_mod_is_valid_python():
    source = walkthrough.build_mod(make_analysis())
    body = textwrap.dedent(source.split("init 1999 python:", 1)[1])
    compile(body, "nodex_walkthrough.rpy", "exec")


def test_hook_annotates_the_right_choice():
    renpy = run_mod(walkthrough.build_mod(make_analysis()))

    # Ren'Py passes (caption, condition, index) triples.
    items = [("Blue", "True", 0), ("Orange", "True", 1)]
    result = renpy.exports.menu(items, None)

    assert "kira_choices" in result[0][0]
    assert result[0][0].startswith("Blue ")
    # The inert option gets no annotation at all.
    assert result[1][0] == "Orange"
    # Condition and index must survive untouched.
    assert result[0][1] == "True" and result[0][2] == 0


def test_hook_leaves_unknown_menus_alone():
    source = walkthrough.build_mod(make_analysis())
    renpy = run_mod(source)
    renpy.get_filename_line = lambda: ("game/other.rpy", 99)

    items = [("Blue", "True", 0)]
    assert renpy.exports.menu(items, None) == items


def test_hook_survives_a_broken_location_lookup():
    renpy = run_mod(walkthrough.build_mod(make_analysis()))

    def explode():
        raise RuntimeError("no current statement")

    renpy.get_filename_line = explode
    items = [("Blue", "True", 0)]
    assert renpy.exports.menu(items, None) == items


def test_patch_is_applied_once():
    renpy = run_mod(walkthrough.build_mod(make_analysis()))
    assert renpy.exports._nodex_patched is True


def test_markup_in_values_is_escaped():
    """A value containing [ or { would corrupt Ren'Py's text parser."""
    menu = MenuPoint(script="s", filename="game/script.rpy", linenumber=1, label=None)
    menu.choices = [
        Choice(
            index=0,
            caption="Pick",
            condition=None,
            effects=[Effect("name", "=", "'[player]'")],
        )
    ]
    analysis = Analysis(game_dir="game")
    analysis.menus = [menu]

    annotation = walkthrough.annotation_for(analysis.menus[0].choices[0])
    assert "[[player]" in annotation
    assert "[player]" not in annotation.replace("[[player]", "")


def test_non_ascii_is_escaped_for_python_2():
    menu = MenuPoint(script="s", filename="game/script.rpy", linenumber=1, label=None)
    menu.choices = [
        Choice(
            index=0,
            caption="Pick",
            condition=None,
            effects=[Effect("name", "=", "'ゆき'")],
        )
    ]
    analysis = Analysis(game_dir="game")
    analysis.menus = [menu]

    source = walkthrough.build_mod(analysis)
    assert source.isascii(), "the mod must stay ASCII so Ren'Py 7 can read it"
    assert "\\u" in source


def test_html_is_self_contained():
    document = walkthrough.build_html(make_analysis(), "Test Game")
    assert "<script" in document and "http://" not in document
    assert "kira_choices" in document
    assert "Blue" in document


def test_install_and_uninstall(tmp_path):
    analysis = make_analysis()
    written = walkthrough.install_mod(analysis, tmp_path)
    assert written.exists()

    # A stale compiled copy must go, or Ren'Py would keep running the old one.
    compiled = written.with_suffix(".rpyc")
    compiled.write_bytes(b"stale")
    walkthrough.install_mod(analysis, tmp_path)
    assert not compiled.exists()

    removed = walkthrough.uninstall_mod(tmp_path)
    assert written in removed and not written.exists()


@pytest.mark.parametrize(
    "effects,jumps,expected",
    [
        ([Effect("love", "+=", "1")], [], "+1 love"),
        ([], ["beach"], "-> beach"),
        ([], [], ""),
    ],
)
def test_annotation_text(effects, jumps, expected):
    choice = Choice(index=0, caption="x", condition=None, effects=effects, jumps=jumps)
    annotation = walkthrough.annotation_for(choice)
    if expected:
        assert expected in annotation
    else:
        assert annotation == ""
