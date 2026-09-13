import type { Edge, Node } from "@xyflow/react";
import { useMemo } from "react";
import { useStore } from "../state/store";
import { Icon, Kbd } from "../ui/bits";
import { FlowCanvas } from "./FlowCanvas";

const PER_ROW = 5;
const SAVE_W = 196;
const SAVE_H = 210;
const SAVES_X = 780;

export function GameWorld() {
  const game = useStore((s) => s.game);
  const saves = useStore((s) => s.saves);
  const locations = useStore((s) => s.locations);
  const activeLocation = useStore((s) => s.activeLocation);
  const selection = useStore((s) => s.selection);
  const edits = useStore((s) => s.edits);
  const select = useStore((s) => s.select);
  const openLocation = useStore((s) => s.openLocation);
  const clearSelection = useStore((s) => s.clearSelection);
  const setWorld = useStore((s) => s.setWorld);
  const setDock = useStore((s) => s.setDock);
  const loadGraph = useStore((s) => s.loadGraph);

  const dirty = Object.keys(edits).length > 0;

  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    if (!game) return { nodes, edges };

    const facts = [game.version ? `v${game.version}` : null, game.version_name, `${saves.length} saves here`].filter(Boolean) as string[];
    nodes.push({ id: "hub", type: "hub", position: { x: 0, y: 40 }, data: { title: game.title, engine: game.engine, facts } });

    if (game.engine === "renpy" && game.game_dir) {
      const tools = [
        { id: "tool:map", glyph: "◇", title: "Choice map", hint: "every branch, as a graph" },
        { id: "tool:walkthrough", glyph: "¶", title: "Walkthrough", hint: "list, mod, HTML export" },
        { id: "tool:gallery", glyph: "✦", title: "Gallery", hint: "find and open locks", tone: "gilt" as const },
      ];
      tools.forEach((tool, i) => {
        nodes.push({ id: tool.id, type: "tool", position: { x: 70, y: 250 + i * 70 }, data: tool });
        edges.push({ id: `e:hub>${tool.id}`, source: "hub", sourceHandle: "below", target: tool.id, type: "smoothstep", pathOptions: { offset: 0, borderRadius: 14 } } as Edge);
      });
    }

    locations.forEach((location, i) => {
      const id = `loc:${location.path}`;
      nodes.push({
        id,
        type: "location",
        position: { x: 400, y: 40 + i * 92 },
        data: { location, active: location.path === activeLocation },
      });
      edges.push({ id: `e:hub>${id}`, source: "hub", target: id, type: "default", animated: location.path === activeLocation });
    });

    const ordered = [...saves].sort((a, b) => a.modified - b.modified);
    let lastDay = "";
    ordered.forEach((save, i) => {
      const col = i % PER_ROW;
      const row = Math.floor(i / PER_ROW);
      const x = SAVES_X + col * SAVE_W;
      const y = 40 + row * SAVE_H;
      const day = new Date(save.modified * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
      if (day !== lastDay) {
        lastDay = day;
        nodes.push({ id: `tick:${i}`, type: "tick", position: { x, y: y - 26 }, data: { text: day }, selectable: false, draggable: false });
      }
      const pickedIndex = selection.indexOf(save.path);
      nodes.push({
        id: `save:${save.path}`,
        type: "save",
        position: { x, y },
        data: {
          save,
          order: selection.length > 1 && pickedIndex >= 0 ? pickedIndex + 1 : null,
          tilt: ((i * 37) % 5) - 2,
          dirty: dirty && pickedIndex === 0,
          picked: pickedIndex >= 0,
        },
      });
    });

    if (selection.length === 2) {
      edges.push({
        id: "e:compare",
        source: `save:${selection[0]}`,
        target: `save:${selection[1]}`,
        type: "straight",
        className: "compare",
        label: "compare",
        labelStyle: { fill: "var(--save)", fontFamily: "var(--font-mono)", fontSize: 11 },
        labelBgStyle: { fill: "var(--canvas)" },
      });
    }

    return { nodes, edges };
  }, [game, saves, locations, activeLocation, selection, dirty]);

  if (!game) return null;

  return (
    <>
      <div className="world-overlay">
        <div className="world-title">
          <h1>{game.title}</h1>
          <p>
            Click a save to open it. <Kbd>Shift</Kbd> click a second to compare, <Kbd>Ctrl</Kbd> click to gather several for a bulk edit.
          </p>
        </div>
        <div className="grow" />
        <div className="toolbar-card">
          <button className="btn ghost sm" onClick={() => setDock({ dockOpen: true, dockTab: "vault" })}>
            Backups vault
          </button>
          {game.engine === "renpy" && game.game_dir && (
            <button
              className="btn sm"
              onClick={() => {
                void loadGraph();
                setWorld("map");
              }}
            >
              <Icon name="map" /> Choice map
            </button>
          )}
        </div>
      </div>

      {saves.length === 0 && (
        <div className="canvas-empty">
          <div>
            <h2>No saves found here</h2>
            <p>
              {locations.length
                ? "This location is empty. Play the game and save once, or pick another location on the canvas."
                : "Nodex could not find where this game keeps its saves. Try opening the save folder directly."}
            </p>
          </div>
        </div>
      )}

      <FlowCanvas
        nodes={nodes}
        edges={edges}
        minimap={saves.length > 15}
        fitKey={`game:${game.root}:${activeLocation}:${saves.length}`}
        onPaneClick={() => clearSelection()}
        onNodeClick={(event, node) => {
          if (node.type === "save") {
            const path = node.id.slice("save:".length);
            select(path, event.shiftKey ? "pair" : event.ctrlKey || event.metaKey ? "toggle" : "replace");
          } else if (node.type === "location") {
            void openLocation(node.id.slice("loc:".length));
          } else if (node.id === "tool:map") {
            void loadGraph();
            setWorld("map");
          } else if (node.id === "tool:walkthrough") {
            setDock({ dockOpen: true, dockTab: "walkthrough" });
          } else if (node.id === "tool:gallery") {
            setDock({ dockOpen: true, dockTab: "gallery" });
          }
        }}
        onNodeDoubleClick={(_, node) => {
          if (node.type === "save") setDock({ focusMode: true, dockOpen: true, dockTab: "variables" });
        }}
      />
    </>
  );
}
