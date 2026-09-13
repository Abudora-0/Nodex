import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Engine, GraphNode, LibraryItem, SaveEntry, SaveLocation } from "../api/types";
import { baseName, timeAgo } from "../ui/bits";

export const ENGINE_NAME: Record<Engine, string> = { renpy: "Ren'Py", rpgmaker: "RPG Maker", unity: "Unity" };
export const engineColor = (engine: Engine) => `var(--eng-${engine})`;

function Ports({ horizontal = true }: { horizontal?: boolean }) {
  return (
    <>
      <Handle type="target" position={horizontal ? Position.Left : Position.Top} isConnectable={false} />
      <Handle type="source" position={horizontal ? Position.Right : Position.Bottom} isConnectable={false} />
    </>
  );
}

export type EngineNodeData = { engine: Engine | "recent"; count: number };
export const EngineNode = memo(({ data }: NodeProps<Node<EngineNodeData>>) => {
  const color = data.engine === "recent" ? "var(--save)" : engineColor(data.engine);
  return (
    <div className="node node-engine" style={{ ["--engine-color" as string]: color }}>
      <span className="count">{data.count} {data.engine === "recent" ? "opened lately" : "found"}</span>
      <h3 style={{ color }}>{data.engine === "recent" ? "Recently opened" : ENGINE_NAME[data.engine]}</h3>
      <Ports horizontal={false} />
    </div>
  );
});

export type FolioNodeData = { item: LibraryItem | { kind: "recent"; engine: Engine; title: string; path: string; modified: number } };
export const FolioNode = memo(({ data }: NodeProps<Node<FolioNodeData>>) => {
  const { item } = data;
  const kind =
    item.kind === "game" ? "game folder" : item.kind === "recent" ? "recent" : item.source === "appdata" ? "saves only" : "save folder";
  return (
    <div
      className={`node folio ${item.kind === "save_folder" ? "dashed" : ""}`}
      style={{ ["--engine-color" as string]: engineColor(item.engine) }}
      title={item.path}
    >
      <div className="kicker">
        <span>{ENGINE_NAME[item.engine]}</span>
        <span>{kind}</span>
      </div>
      <h4>{item.title}</h4>
      <div className="meta">
        {"save_count" in item && item.save_count ? `${item.save_count} saves, ` : ""}
        {timeAgo(item.modified)}
      </div>
      <Ports horizontal={false} />
    </div>
  );
});

export type HubNodeData = { title: string; engine: Engine; facts: string[] };
export const HubNode = memo(({ data }: NodeProps<Node<HubNodeData>>) => (
  <div className="node hub" style={{ ["--engine-color" as string]: engineColor(data.engine) }}>
    <div className="kicker">{ENGINE_NAME[data.engine]}</div>
    <h2>{data.title}</h2>
    <div className="facts">
      {data.facts.map((fact) => (
        <span className="chip" key={fact}>
          {fact}
        </span>
      ))}
    </div>
    <Ports />
    <Handle id="below" type="source" position={Position.Bottom} isConnectable={false} />
  </div>
));

export type ToolNodeData = { glyph: string; title: string; hint: string; tone?: "gilt" };
export const ToolNode = memo(({ data }: NodeProps<Node<ToolNodeData>>) => (
  <div className={`node tool-node ${data.tone ?? ""}`}>
    <span className="glyph">{data.glyph}</span>
    <div>
      <b>{data.title}</b>
      <span>{data.hint}</span>
    </div>
    <Handle type="target" position={Position.Left} isConnectable={false} />
  </div>
));

export type LocationNodeData = { location: SaveLocation; active: boolean };
export const LocationNode = memo(({ data }: NodeProps<Node<LocationNodeData>>) => (
  <div className={`node location-node ${data.active ? "active" : ""}`} title={data.location.path}>
    <span className="source">{data.location.source}</span>
    <span className="where">{data.location.path}</span>
    <span className="faint" style={{ fontSize: 12 }}>
      {data.location.save_count ? `${data.location.save_count} saves` : "open"}
    </span>
    <Ports />
  </div>
));

export type SaveNodeData = { save: SaveEntry; order: number | null; tilt: number; dirty: boolean; picked: boolean };
export const SaveNode = memo(({ data }: NodeProps<Node<SaveNodeData>>) => {
  const { save } = data;
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    if (save.engine === "renpy" && save.readable) {
      void api.thumbnailUrl(save.path).then((url) => alive && setSrc(`${url}&v=${save.modified}`));
    }
    return () => {
      alive = false;
    };
  }, [save.path, save.engine, save.readable, save.modified]);

  return (
    <div
      className={`node polaroid ${save.readable ? "" : "broken"} ${data.picked ? "picked" : ""} ${data.dirty ? "dirty" : ""}`}
      style={{ ["--tilt" as string]: `${data.tilt}deg` }}
      title={save.path}
    >
      <span className="pin" />
      {data.order !== null && <span className="order">{data.order}</span>}
      <div className="shot">
        {src && !failed ? (
          <img src={src} alt="" loading="lazy" draggable={false} onError={() => setFailed(true)} />
        ) : save.readable ? (
          <span>{save.save_name ?? save.engine}</span>
        ) : (
          <span>needs repair</span>
        )}
      </div>
      <div className="caption">{save.save_name && save.engine === "renpy" ? save.save_name : baseName(save.name)}</div>
      <div className="when">
        {save.name} / {timeAgo(save.modified)}
      </div>
      <Ports />
    </div>
  );
});

export type TickNodeData = { text: string };
export const TickNode = memo(({ data }: NodeProps<Node<TickNodeData>>) => (
  <div className="timeline-tick">
    {data.text}
    <Ports />
  </div>
));

export type MapNodeData = { node: GraphNode };
export const MapNode = memo(({ data }: NodeProps<Node<MapNodeData>>) => {
  const { node } = data;
  if (node.type === "label") {
    return (
      <div className="m-label" title={node.label}>
        {node.label}
        <Ports />
      </div>
    );
  }
  if (node.type === "ending") {
    return (
      <div className="m-ending" title={node.label}>
        {node.label.replace(/_/g, " ")}
        <Ports />
      </div>
    );
  }
  if (node.type === "menu") {
    return (
      <div className="m-menu">
        <span className="kicker">choice point</span>
        <b>{node.label}</b>
        <span className="loc">
          {node.file}:{node.line}
        </span>
        <Ports />
      </div>
    );
  }
  const effects = node.effects ?? [];
  return (
    <div className={`m-choice ${node.inert ? "inert" : ""}`}>
      <div className="cap">{node.label || "(blank)"}</div>
      {node.condition && <span className="cond">if {node.condition}</span>}
      {effects.length > 0 && (
        <div className="effects">
          {effects.slice(0, 3).map((effect) => (
            <span className="chip effect" key={effect}>
              {effect}
            </span>
          ))}
          {effects.length > 3 && <span className="chip">+{effects.length - 3}</span>}
        </div>
      )}
      <Ports />
    </div>
  );
});

export const nodeTypes = {
  engine: EngineNode,
  folio: FolioNode,
  hub: HubNode,
  tool: ToolNode,
  location: LocationNode,
  save: SaveNode,
  tick: TickNode,
  map: MapNode,
};
