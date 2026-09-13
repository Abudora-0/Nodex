"""The walkthrough as a branching graph of labels, menus, choices and endings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ...engines.renpy import analyze as renpy_analyze

router = APIRouter(prefix="/api")


class GraphRequest(BaseModel):
    path: str


def build_graph(analysis: Any) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def label_node(name: str) -> str:
        node_id = f"label:{name}"
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "type": "ending" if renpy_analyze.looks_like_ending(name) else "label",
                "label": name,
                "file": None,
            }
        return node_id

    for menu in analysis.menus:
        if not menu.choices:
            continue
        menu_id = f"menu:{menu.key}"
        nodes[menu_id] = {
            "id": menu_id,
            "type": "menu",
            "label": menu.label or "(no label)",
            "file": menu.filename,
            "line": menu.linenumber,
            "meaningful": menu.meaningful,
        }
        if menu.label:
            edges.append({"id": f"e:{menu.label}>{menu.key}", "source": label_node(menu.label), "target": menu_id, "kind": "contains"})

        for choice in menu.choices:
            choice_id = f"choice:{menu.key}:{choice.index}"
            nodes[choice_id] = {
                "id": choice_id,
                "type": "choice",
                "label": choice.caption,
                "file": menu.filename,
                "menu": menu_id,
                "condition": choice.condition,
                "effects": [e.describe() for e in choice.effects],
                "downstream": [e.describe() for e in choice.downstream[:8]],
                "inert": choice.inert,
            }
            edges.append({"id": f"e:{menu_id}>{choice_id}", "source": menu_id, "target": choice_id, "kind": "option"})
            for target in choice.jumps:
                edges.append({"id": f"e:{choice_id}>j:{target}", "source": choice_id, "target": label_node(target), "kind": "jump"})
            for target in choice.calls:
                edges.append({"id": f"e:{choice_id}>c:{target}", "source": choice_id, "target": label_node(target), "kind": "call"})

    node_list = list(nodes.values())
    return {
        "nodes": node_list,
        "edges": edges,
        "stats": {
            "menus": sum(1 for n in node_list if n["type"] == "menu"),
            "choices": sum(1 for n in node_list if n["type"] == "choice"),
            "labels": sum(1 for n in node_list if n["type"] == "label"),
            "endings": sum(1 for n in node_list if n["type"] == "ending"),
        },
        "partial": True,
    }


@router.post("/walkthrough/graph")
def walkthrough_graph(request: GraphRequest) -> dict[str, Any]:
    from ..app import _get_analysis

    analysis, game_dir, title = _get_analysis(request.path)
    graph = build_graph(analysis)
    graph.update({"game_dir": str(game_dir), "title": title})
    return graph
