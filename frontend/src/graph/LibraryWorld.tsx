import type { Edge, Node } from "@xyflow/react";
import { useMemo, useState } from "react";
import type { Engine, LibraryItem } from "../api/types";
import { pickFolder } from "../platform/pickFolder";
import { api } from "../api/client";
import { errorText, useStore } from "../state/store";
import { Icon } from "../ui/bits";
import { FlowCanvas } from "./FlowCanvas";

const COL = 256;
const ROW = 116;
const LANE_GAP = 120;
const ENGINES: Engine[] = ["renpy", "rpgmaker", "unity"];

const columnsFor = (count: number) => Math.max(2, Math.min(6, Math.ceil(Math.sqrt(count / 2))));

export function LibraryWorld() {
  const library = useStore((s) => s.library);
  const busy = useStore((s) => s.libraryBusy);
  const openPath = useStore((s) => s.openPath);
  const loadLibrary = useStore((s) => s.loadLibrary);
  const toast = useStore((s) => s.toast);
  const [manual, setManual] = useState("");
  const [filter, setFilter] = useState("");

  const { nodes, edges, total, fitNodes } = useMemo(() => {
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    const fitNodes: string[] = [];
    if (!library) return { nodes, edges, total: 0, fitNodes };

    const needle = filter.trim().toLowerCase();
    const matches = (title: string, path: string) => !needle || title.toLowerCase().includes(needle) || path.toLowerCase().includes(needle);

    const lanes: { key: Engine | "recent"; items: FolioItem[] }[] = [];
    const recents = library.recents
      .filter((r) => matches(r.title, r.root))
      .slice(0, 8)
      .map((r) => ({ kind: "recent" as const, engine: r.engine, title: r.title, path: r.root, modified: r.opened }));
    if (recents.length) lanes.push({ key: "recent", items: recents });
    for (const engine of ENGINES) {
      const items = library.discovered
        .filter((d) => d.engine === engine && matches(d.title, d.path))
        .sort((a, b) => Number(b.kind === "game") - Number(a.kind === "game") || b.modified - a.modified);
      if (items.length) lanes.push({ key: engine, items });
    }

    let x = 0;
    for (const lane of lanes) {
      const perRow = columnsFor(lane.items.length);
      const laneWidth = perRow * COL;
      const hubId = `lane:${lane.key}`;
      nodes.push({ id: hubId, type: "engine", position: { x: x + laneWidth / 2 - 110, y: 0 }, data: { engine: lane.key, count: lane.items.length } });
      if (fitNodes.length < 30) fitNodes.push(hubId);
      lane.items.forEach((item, i) => {
        const id = `item:${lane.key}:${item.path}`;
        nodes.push({
          id,
          type: "folio",
          position: { x: x + (i % perRow) * COL, y: 150 + Math.floor(i / perRow) * ROW },
          data: { item },
        });
        if (i < perRow * 3 && fitNodes.length < 30) fitNodes.push(id);
        if (i < perRow) edges.push({ id: `e:${hubId}>${id}`, source: hubId, target: id, type: "smoothstep" });
      });
      x += laneWidth + LANE_GAP;
    }

    return { nodes, edges, total: library.discovered.length, fitNodes };
  }, [library, filter]);

  async function addRoot() {
    const chosen = await pickFolder("Add a folder that contains games");
    if (!chosen) return;
    try {
      useStore.setState({ library: await api.addRoot(chosen) });
      toast("ok", "Library folder added and scanned.");
    } catch (caught) {
      toast("bad", errorText(caught));
    }
  }

  async function openFolder() {
    const chosen = await pickFolder();
    if (chosen) await openPath(chosen);
  }

  const isEmpty = library !== null && nodes.length === 0 && !filter;

  return (
    <>
      <div className="world-overlay">
        <div className="world-title">
          <h1>Library</h1>
          <p>
            {library ? `${total} discovered, ${library.recents.length} recent, ${library.roots.length} library folder(s)` : "Reading your library"}
          </p>
        </div>
        <div className="grow" />
        <div className="toolbar-card">
          <input
            className="field"
            type="search"
            placeholder="Filter the shelf"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ height: 26, width: 180 }}
          />
          <button className="btn ghost sm" onClick={() => void loadLibrary(true)} disabled={busy}>
            {busy ? <span className="spin" /> : <Icon name="refresh" />} Rescan
          </button>
          <button className="btn sm" onClick={() => void addRoot()}>
            <Icon name="plus" /> Library folder
          </button>
          <button className="btn solid sm" onClick={() => void openFolder()}>
            <Icon name="folder" /> Open game
          </button>
        </div>
      </div>

      {isEmpty && (
        <div className="canvas-empty">
          <div>
            <h2>Nothing on the shelf yet</h2>
            <p>
              Open a game folder, or add the folder where you keep games. Nodex also looks in the places Ren'Py and Unity keep
              saves on this machine.
            </p>
            <div className="row">
              <button className="btn solid" onClick={() => void openFolder()}>
                <Icon name="folder" /> Open a game folder
              </button>
              <button className="btn" onClick={() => void addRoot()}>
                <Icon name="plus" /> Add library folder
              </button>
            </div>
            <form
              className="path-entry"
              onSubmit={(event) => {
                event.preventDefault();
                void openPath(manual);
              }}
            >
              <input className="field" placeholder="or paste a game or save folder path" value={manual} onChange={(e) => setManual(e.target.value)} />
              <button className="btn" disabled={!manual.trim()}>
                Open
              </button>
            </form>
          </div>
        </div>
      )}

      <FlowCanvas
        nodes={nodes}
        edges={edges}
        fitKey={`library:${nodes.length}:${filter}`}
        fitNodes={fitNodes}
        minimap={nodes.length > 40}
        onlyRenderVisible
        onNodeClick={(_, node) => {
          if (node.type !== "folio") return;
          const item = (node.data as { item: FolioItem }).item;
          void openPath(item.path);
        }}
      />
    </>
  );
}

type FolioItem = LibraryItem | { kind: "recent"; engine: Engine; title: string; path: string; modified: number };
