/** Controversy Map.
 *
 * The product centre. Two decisions carry most of the weight:
 *
 * 1. **Refuted nodes are dimmed, never hidden.** CLAUDE.md 4 forbids a rebutted
 *    position from disappearing, and a filter that removes them would make the
 *    map look like a consensus that was never reached. The toggle changes
 *    opacity; it cannot delete.
 * 2. **Edge type is drawn, not just coloured.** SUPPORTS and REFUTES must be
 *    distinguishable without colour vision, so refuting and contradicting
 *    relations are dashed as well as red.
 *
 * Panning and zooming are the only gestures in the product, so they are the
 * only place bounce is allowed -- and React Flow already handles the momentum.
 */

import { useCallback, useMemo, useState } from "react";
import {
  Background,
  Controls,
  type Edge,
  MarkerType,
  type Node,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";
import "./MapView.css";

import type { EvidenceGraph, GraphNode } from "../api/types";
import { EDGE_TYPE_LABELS, NODE_TYPE_LABELS } from "../api/types";
import { Badge, Empty, STATUS_LABELS, toneForStatus } from "../components/primitives";

/** Relations that mean "this weakens that". Dashed and red so the meaning
 * survives both greyscale printing and colour-blindness. */
const OPPOSING = new Set(["REFUTES", "CONTRADICTS", "CONFOUNDS"]);

/** Column per node type: the map reads left to right from question to evidence,
 * which is the direction a reader checks provenance in. */
const COLUMNS: Record<string, number> = {
  ResearchQuestion: 0,
  Claim: 1,
  StudyFinding: 2,
  Source: 3,
  Construct: 2,
  Context: 2,
  Blindspot: 1,
  DebateCapsule: 1,
  DiscriminatingStudy: 3,
};

const COLUMN_WIDTH = 260;
const ROW_HEIGHT = 96;

function labelOf(node: GraphNode): string {
  for (const key of ["statement", "question", "title", "summary"]) {
    const value = node.payload[key];
    if (typeof value === "string" && value) {
      return value.length > 90 ? `${value.slice(0, 88)}…` : value;
    }
  }
  return NODE_TYPE_LABELS[node.node_type] ?? node.node_type;
}

function layout(graph: EvidenceGraph): Node[] {
  const perColumn = new Map<number, number>();
  return graph.nodes.map((node) => {
    const column = COLUMNS[node.node_type] ?? 2;
    const row = perColumn.get(column) ?? 0;
    perColumn.set(column, row + 1);
    const tone = toneForStatus(node.status);
    return {
      id: node.id,
      position: { x: column * COLUMN_WIDTH, y: row * ROW_HEIGHT },
      data: { label: labelOf(node), node },
      type: "default",
      className: `map-node map-node--${tone}`,
      draggable: true,
    } satisfies Node;
  });
}

function toEdges(graph: EvidenceGraph): Edge[] {
  return graph.edges.map((edge) => {
    const opposing = OPPOSING.has(edge.edge_type);
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: EDGE_TYPE_LABELS[edge.edge_type] ?? edge.edge_type,
      animated: false,
      className: opposing ? "map-edge map-edge--opposing" : "map-edge",
      style: opposing ? { strokeDasharray: "6 4" } : undefined,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    } satisfies Edge;
  });
}

export function MapView({ graph }: { graph: EvidenceGraph }) {
  const initialNodes = useMemo(() => layout(graph), [graph]);
  const initialEdges = useMemo(() => toEdges(graph), [graph]);
  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [dimRefuted, setDimRefuted] = useState(true);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) =>
      setSelected((node.data as { node: GraphNode }).node),
    [],
  );

  if (graph.nodes.length === 0) {
    return (
      <div className="map map--empty">
        <Empty>
          证据图为空。任务尚未产出任何被采纳的证据节点——这不等于「没有争议」，
          而是「还没有可展示的证据」。
        </Empty>
      </div>
    );
  }

  return (
    <div className={`map${dimRefuted ? " map--dim-refuted" : ""}`}>
      <div className="map__canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={() => setSelected(null)}
          fitView
          minZoom={0.2}
          maxZoom={2}
          proOptions={{ hideAttribution: false }}
        >
          <Background gap={24} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <aside className="map__side">
        <div className="map__legend">
          <h3>图例</h3>
          <ul>
            <li>
              <Badge tone="admitted">已采纳</Badge> 全文可得，证据等级 A
            </li>
            <li>
              <Badge tone="provisional">仅元数据</Badge> 等级 B，未读原文
            </li>
            <li>
              <Badge tone="refuted">已反驳/隔离</Badge> 保留可审计，不删除
            </li>
            <li>
              <span className="map__legend-edge map__legend-edge--opposing" />
              虚线红边 = 反驳、冲突、混杂
            </li>
          </ul>
          <label className="map__toggle">
            <input
              type="checkbox"
              checked={dimRefuted}
              onChange={(event) => setDimRefuted(event.target.checked)}
            />
            淡化已反驳节点
            <span className="map__toggle-note">
              仅改变不透明度。被反驳的节点永远留在图上。
            </span>
          </label>
        </div>

        <div className="map__inspector">
          <h3>节点详情</h3>
          {selected === null ? (
            <Empty>点击任一节点查看其类型、状态与原始载荷。</Empty>
          ) : (
            <>
              <p className="map__inspector-type">
                {NODE_TYPE_LABELS[selected.node_type] ?? selected.node_type}
                <Badge tone={toneForStatus(selected.status)}>
                  {STATUS_LABELS[selected.status] ?? selected.status}
                </Badge>
              </p>
              <p className="map__inspector-id mono">{selected.id}</p>
              {/* Raw payload, unformatted on purpose: CLAUDE.md 2 separates the
                  original record from any AI rendering of it. */}
              <pre className="map__payload">
                {JSON.stringify(selected.payload, null, 2)}
              </pre>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
