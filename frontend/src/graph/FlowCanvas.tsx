import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import { useEffect, useRef } from "react";
import { nodeTypes } from "./nodes";

interface Props {
  nodes: Node[];
  edges: Edge[];
  onNodeClick?: NodeMouseHandler;
  onNodeDoubleClick?: NodeMouseHandler;
  onPaneClick?(): void;
  fitKey: string;
  /** Frame only these nodes on first fit, so a huge world opens on its most relevant corner. */
  fitNodes?: string[];
  flyTo?: string | null;
  minimap?: boolean;
  onlyRenderVisible?: boolean;
}

function Camera({ fitKey, fitNodes, flyTo, nodes }: { fitKey: string; fitNodes?: string[]; flyTo?: string | null; nodes: Node[] }) {
  const flow = useReactFlow();
  const lastFit = useRef("");

  useEffect(() => {
    if (!nodes.length || lastFit.current === fitKey) return;
    lastFit.current = fitKey;
    const subset = fitNodes?.length ? fitNodes.map((id) => ({ id })) : undefined;
    requestAnimationFrame(() => flow.fitView({ padding: 0.18, duration: 240, maxZoom: 1.1, minZoom: 0.35, nodes: subset }));
  }, [fitKey, fitNodes, nodes.length, flow]);

  useEffect(() => {
    if (!flyTo) return;
    const node = flow.getNode(flyTo);
    if (!node) return;
    const width = node.measured?.width ?? 200;
    const height = node.measured?.height ?? 60;
    void flow.setCenter(node.position.x + width / 2, node.position.y + height / 2, { zoom: 1.05, duration: 380 });
  }, [flyTo, flow]);

  return null;
}

export function FlowCanvas({ nodes, edges, onNodeClick, onNodeDoubleClick, onPaneClick, fitKey, fitNodes, flyTo, minimap = true, onlyRenderVisible }: Props) {
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={onNodeClick}
      onNodeDoubleClick={onNodeDoubleClick}
      onPaneClick={onPaneClick}
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable
      selectNodesOnDrag={false}
      multiSelectionKeyCode={null}
      selectionKeyCode={null}
      deleteKeyCode={null}
      zoomOnDoubleClick={false}
      minZoom={0.08}
      maxZoom={2}
      onlyRenderVisibleElements={onlyRenderVisible}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1.3} color="var(--canvas-dot)" />
      <Controls showInteractive={false} position="bottom-left" />
      {minimap && (
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          nodeStrokeWidth={0}
          nodeColor=""
          nodeClassName={(node) => {
            const data = node.data as { node?: { type?: string } };
            if (node.type === "save") return "minimap-save";
            if (data.node?.type === "choice" || data.node?.type === "menu") return "minimap-choice";
            if (data.node?.type === "ending") return "minimap-ending";
            return "";
          }}
        />
      )}
      <Camera fitKey={fitKey} fitNodes={fitNodes} flyTo={flyTo} nodes={nodes} />
    </ReactFlow>
  );
}
