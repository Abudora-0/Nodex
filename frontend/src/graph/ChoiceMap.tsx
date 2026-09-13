import type { Edge, Node } from "@xyflow/react";
import ELK from "elkjs/lib/elk-api.js";
import elkWorkerUrl from "elkjs/lib/elk-worker.min.js?url";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChoiceGraph, GraphNode } from "../api/types";
import { useStore } from "../state/store";
import { Icon } from "../ui/bits";
import { Select } from "../ui/Select";
import { FlowCanvas } from "./FlowCanvas";

const ALL = "__all__";
const LARGE = 450;

const SIZE: Record<GraphNode["type"], { width: number; height: number }> = {
  label: { width: 170, height: 30 },
  menu: { width: 240, height: 62 },
  choice: { width: 230, height: 88 },
  ending: { width: 96, height: 96 },
};

type Positions = Record<string, { x: number; y: number }>;

// The layout runs in elkjs's own worker script, so large maps never block the UI thread.
let elk: InstanceType<typeof ELK> | null = null;
const layoutCache = new Map<string, Positions>();

async function layout(request: {
  nodes: { id: string; width: number; height: number }[];
  edges: { id: string; source: string; target: string }[];
}): Promise<Positions> {
  elk ??= new ELK({ workerFactory: () => new Worker(elkWorkerUrl) as never });
  const result = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "90",
      "elk.spacing.nodeNode": "26",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.cycleBreaking.strategy": "GREEDY",
      "elk.separateConnectedComponents": "true",
      "elk.spacing.componentComponent": "70",
    },
    children: request.nodes,
    edges: request.edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  const positions: Positions = {};
  for (const child of result.children ?? []) positions[child.id] = { x: child.x ?? 0, y: child.y ?? 0 };
  return positions;
}

export function scopeGraph(graph: ChoiceGraph, file: string, meaningfulOnly: boolean) {
  const menus = graph.nodes.filter(
    (n) => n.type === "menu" && (file === ALL || n.file === file) && (!meaningfulOnly || n.meaningful),
  );
  const menuIds = new Set(menus.map((m) => m.id));
  const keep = new Set(menuIds);
  for (const node of graph.nodes) if (node.type === "choice" && node.menu && menuIds.has(node.menu)) keep.add(node.id);
  for (const edge of graph.edges) {
    if (edge.kind === "contains" && keep.has(edge.target)) keep.add(edge.source);
    if ((edge.kind === "jump" || edge.kind === "call") && keep.has(edge.source)) keep.add(edge.target);
  }
  const nodes = graph.nodes.filter((n) => keep.has(n.id));
  const edges = graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target));
  return { nodes, edges };
}

/** Where a node leads (deep) and how the player got there (shallow). */
function reachable(start: string, edges: { source: string; target: string; id: string }[]) {
  const forward = new Map<string, { next: string; id: string }[]>();
  const backward = new Map<string, { next: string; id: string }[]>();
  for (const e of edges) {
    if (!forward.has(e.source)) forward.set(e.source, []);
    forward.get(e.source)!.push({ next: e.target, id: e.id });
    if (!backward.has(e.target)) backward.set(e.target, []);
    backward.get(e.target)!.push({ next: e.source, id: e.id });
  }
  const nodes = new Set([start]);
  const litEdges = new Set<string>();

  const walk = (links: typeof forward, maxDepth: number) => {
    const seen = new Set([start]);
    let frontier = [start];
    for (let depth = 0; depth < maxDepth && frontier.length; depth++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const link of links.get(id) ?? []) {
          litEdges.add(link.id);
          nodes.add(link.next);
          if (!seen.has(link.next)) {
            seen.add(link.next);
            next.push(link.next);
          }
        }
      }
      frontier = next;
    }
  };
  walk(forward, 8);
  walk(backward, 2);
  return { nodes, litEdges };
}

export function ChoiceMap() {
  const game = useStore((s) => s.game);
  const graph = useStore((s) => s.graph);
  const busy = useStore((s) => s.graphBusy);
  const error = useStore((s) => s.graphError);
  const mapNode = useStore((s) => s.mapNode);
  const flyTo = useStore((s) => s.flyTo);
  const focusMapNode = useStore((s) => s.focusMapNode);
  const loadGraph = useStore((s) => s.loadGraph);
  const setWorld = useStore((s) => s.setWorld);

  const [file, setFile] = useState<string | null>(null);
  const [meaningfulOnly, setMeaningfulOnly] = useState(false);
  const [positions, setPositions] = useState<Positions | null>(null);
  const [laying, setLaying] = useState(false);
  const current = useRef("");

  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);

  const files = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of graph?.nodes ?? []) if (n.type === "menu" && n.file) counts.set(n.file, (counts.get(n.file) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [graph]);

  useEffect(() => {
    if (!graph) return;
    setFile(graph.nodes.length > LARGE && files[0] ? files[0][0] : ALL);
  }, [graph, files]);

  // Flying to a node outside the current file scope switches scope first.
  useEffect(() => {
    if (!graph || !flyTo || file === null) return;
    const target = graph.nodes.find((n) => n.id === flyTo);
    if (target?.file && file !== ALL && target.file !== file) setFile(target.file);
  }, [flyTo, graph, file]);

  const scoped = useMemo(() => (graph && file !== null ? scopeGraph(graph, file, meaningfulOnly) : null), [graph, file, meaningfulOnly]);

  useEffect(() => {
    if (!scoped || !graph) return;
    const key = `${graph.game_dir}|${file}|${meaningfulOnly}`;
    current.current = key;
    const cached = layoutCache.get(key);
    if (cached) {
      setPositions(cached);
      return;
    }
    setLaying(true);
    setPositions(null);
    layout({
      nodes: scoped.nodes.map((n) => ({ id: n.id, ...SIZE[n.type] })),
      edges: scoped.edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
    })
      .then((result) => {
        layoutCache.set(key, result);
        if (current.current === key) setPositions(result);
      })
      .catch((caught) => {
        if (current.current !== key) return;
        setPositions({});
        useStore.getState().toast("bad", `The map layout failed: ${String(caught)}`);
      })
      .finally(() => {
        if (current.current === key) setLaying(false);
      });
  }, [scoped, graph, file, meaningfulOnly]);

  const highlight = useMemo(() => (mapNode && scoped ? reachable(mapNode, scoped.edges) : null), [mapNode, scoped]);

  const { nodes, edges } = useMemo(() => {
    if (!scoped || !positions) return { nodes: [] as Node[], edges: [] as Edge[] };
    const nodes: Node[] = scoped.nodes.map((n) => ({
      id: n.id,
      type: "map",
      position: positions[n.id] ?? { x: 0, y: 0 },
      data: { node: n },
      selected: n.id === mapNode,
      className: highlight && !highlight.nodes.has(n.id) ? "dim" : undefined,
    }));
    const edges: Edge[] = scoped.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      className: [e.kind, highlight ? (highlight.litEdges.has(e.id) ? "lit" : "dim") : ""].join(" "),
    }));
    return { nodes, edges };
  }, [scoped, positions, highlight, mapNode]);

  if (!game) return null;

  const options = [
    { value: ALL, label: "All script files", hint: graph ? String(graph.stats.menus) : undefined },
    ...files.map(([name, count]) => ({ value: name, label: name, hint: String(count) })),
  ];

  return (
    <>
      <div className="world-overlay">
        <div className="world-title">
          <h1>Choice map</h1>
          <p>
            {graph
              ? `${graph.stats.menus} choice points, ${graph.stats.choices} options, ${graph.stats.endings} endings. Click an option to trace where it leads.`
              : "Reading every script in the game"}
          </p>
        </div>
        <div className="grow" />
        <div className="toolbar-card">
          <button className="btn ghost sm" onClick={() => setWorld("game")}>
            <Icon name="back" /> Saves
          </button>
          {graph && file !== null && (
            <Select ariaLabel="Script file" value={file} options={options} onChange={setFile} searchable={files.length > 8} />
          )}
          <div className="seg" role="group" aria-label="Which choices">
            <button aria-pressed={!meaningfulOnly} onClick={() => setMeaningfulOnly(false)}>
              All
            </button>
            <button aria-pressed={meaningfulOnly} onClick={() => setMeaningfulOnly(true)}>
              Consequential
            </button>
          </div>
        </div>
      </div>

      {(busy || laying || error || (graph && scoped && scoped.nodes.length === 0)) && (
        <div className="canvas-empty">
          <div>
            {error ? (
              <>
                <h2>The map could not be drawn</h2>
                <p>{error}</p>
              </>
            ) : busy || laying ? (
              <>
                <span className="spin" />
                <p style={{ marginTop: 12 }}>{busy ? "Reading scripts" : `Laying out ${scoped?.nodes.length ?? 0} nodes`}</p>
              </>
            ) : (
              <>
                <h2>No choices here</h2>
                <p>This scope has no menu statements. Try another script file.</p>
              </>
            )}
          </div>
        </div>
      )}

      <FlowCanvas
        nodes={nodes}
        edges={edges}
        onlyRenderVisible
        fitKey={`map:${graph?.game_dir}:${file}:${meaningfulOnly}:${positions ? "ready" : "wait"}`}
        flyTo={positions ? flyTo : null}
        onPaneClick={() => focusMapNode(null)}
        onNodeClick={(_, node) => focusMapNode(node.id)}
        onNodeDoubleClick={(_, node) => focusMapNode(node.id, true)}
      />
    </>
  );
}
