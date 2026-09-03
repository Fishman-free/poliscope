/** Controversy Map.
 *
 * The product centre. Decisions that carry the weight:
 *
 * 1. **Refuted nodes are dimmed, never hidden.** AGENTS.md 5.3 forbids a
 *    rebutted position from disappearing, and a filter that removes them
 *    would make the map look like a consensus that was never reached. The
 *    toggle changes opacity; it cannot delete.
 * 2. **Edge type is drawn, not just coloured.** SUPPORTS and REFUTES must be
 *    distinguishable without colour vision, so refuting/contradicting
 *    relations are dashed as well as red.
 * 3. **Live graph updates merge into the researcher's arrangement.** A new
 *    SSE frame adds/moves formal nodes without wiping the positions the
 *    researcher dragged; only a manual "重置布局" does that.
 * 4. **The researcher can create, but only on the personal overlay.** Private
 *    node notes, draggable sticky notes and the saved layout live in
 *    localStorage (see map/overlayStore.ts); they never enter the formal
 *    Evidence Graph and are labelled as the researcher's own material.
 * 5. **Export is offline and dependency-free** (map/exportGraph.ts): PNG /
 *    SVG snapshot of the current canvas, plus JSON carrying graph + overlay.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";
import "./MapView.css";

import type { EvidenceGraph, GraphNode } from "../api/types";
import { EDGE_TYPE_LABELS, NODE_TYPE_LABELS } from "../api/types";
import { Badge, Empty, toneForStatus } from "../components/primitives";
import { t } from "../i18n";
import {
  type ExportEdge,
  type ExportNode,
  type NodeTone,
  exportJsonFile,
  exportPngFile,
  exportSvgFile,
} from "./map/exportGraph";
import {
  type MapOverlay,
  loadOverlay,
  newStickyId,
  saveOverlay,
} from "./map/overlayStore";
import {
  type EvidenceNodeData,
  type MapFlowNode,
  type StickyNodeData,
  nodeTypes,
} from "./map/MapNodes";
import { MapNodeDetail } from "./MapNodeDetail";

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

const COLUMN_WIDTH = 300;
const ROW_HEIGHT = 150;

function labelOf(node: GraphNode): string {
  for (const key of ["statement", "question", "title", "summary"]) {
    const value = node.payload[key];
    if (typeof value === "string" && value) {
      return value.length > 90 ? `${value.slice(0, 88)}…` : value;
    }
  }
  return NODE_TYPE_LABELS[node.node_type] ?? node.node_type;
}

function layout(graph: EvidenceGraph): MapFlowNode[] {
  const perColumn = new Map<number, number>();
  return graph.nodes.map((node) => {
    const column = COLUMNS[node.node_type] ?? 2;
    const row = perColumn.get(column) ?? 0;
    perColumn.set(column, row + 1);
    const tone = toneForStatus(node.status) as NodeTone;
    const data: EvidenceNodeData = {
      label: labelOf(node),
      typeLabel: NODE_TYPE_LABELS[node.node_type] ?? node.node_type,
      tone,
      hasNote: false,
    };
    return {
      id: node.id,
      position: { x: column * COLUMN_WIDTH, y: row * ROW_HEIGHT },
      data,
      type: "evidence",
      className: `map-node map-node--${tone}`,
      draggable: true,
    } satisfies Node<EvidenceNodeData> as MapFlowNode;
  });
}

type EdgeKind = "normal" | "opposing" | "cite";

function toEdges(graph: EvidenceGraph): Edge[] {
  const drawn = new Set(
    graph.edges.map((edge) => `${edge.source}->${edge.target}:${edge.edge_type}`),
  );
  const makeEdge = (
    id: string,
    source: string,
    target: string,
    label: string,
    kind: EdgeKind,
  ): Edge => ({
    id,
    source,
    target,
    label,
    className: `map-edge map-edge--${kind}`,
    style:
      kind === "opposing"
        ? { strokeDasharray: "6 4" }
        : kind === "cite"
          ? { strokeDasharray: "2 4" }
          : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
    data: { kind },
  });
  const edges: Edge[] = graph.edges.map((edge) =>
    makeEdge(
      edge.id,
      edge.source,
      edge.target,
      EDGE_TYPE_LABELS[edge.edge_type] ?? edge.edge_type,
      OPPOSING.has(edge.edge_type) ? "opposing" : "normal",
    ),
  );
  // Claim / Blindspot → Source via an intermediate finding, so a conclusion
  // and its paper are one hop on the canvas.
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  for (const hop of graph.edges) {
    const mid = byId.get(hop.target);
    if (!mid || mid.node_type !== "StudyFinding") continue;
    const origin = byId.get(hop.source);
    if (
      !origin ||
      (origin.node_type !== "Claim" && origin.node_type !== "Blindspot")
    ) {
      continue;
    }
    for (const leaf of graph.edges) {
      if (leaf.source !== hop.target || leaf.edge_type !== "DERIVED_FROM") {
        continue;
      }
      const key = `${hop.source}->${leaf.target}:CITES`;
      if (drawn.has(key)) continue;
      drawn.add(key);
      edges.push(
        makeEdge(
          `cite-${hop.source}-${leaf.target}`,
          hop.source,
          leaf.target,
          t("对应文献"),
          "cite",
        ),
      );
    }
  }
  return edges;
}

export function MapView({
  graph,
  taskId,
}: {
  graph: EvidenceGraph;
  taskId: string;
}) {
  const laidOut = useMemo(() => layout(graph), [graph]);
  const flowEdges = useMemo(() => toEdges(graph), [graph]);
  const [nodes, setNodes, onNodesChange] = useNodesState<MapFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [dimRefuted, setDimRefuted] = useState(true);
  const [overlay, setOverlay] = useState<MapOverlay>(() =>
    loadOverlay(taskId),
  );

  // Switching sessions loads that session's own overlay.
  useEffect(() => {
    setOverlay(loadOverlay(taskId));
    setSelected(null);
  }, [taskId]);

  // Persist every overlay change; saveOverlay itself is best-effort.
  useEffect(() => {
    saveOverlay(taskId, overlay);
  }, [taskId, overlay]);

  // Merge a fresh server graph into the flow: formal nodes keep the
  // researcher's dragged position (and selection state), new ones take the
  // column layout, and the researcher's sticky notes are preserved.
  useEffect(() => {
    setNodes((previous) => {
      const positions = new Map(
        previous.map((node) => [
          node.id,
          { x: node.position.x, y: node.position.y },
        ]),
      );
      const evidence = laidOut.map((node) => {
        const kept = positions.get(node.id);
        const noted = Boolean(overlay.notes[node.id]);
        return kept
          ? {
              ...node,
              position: kept,
              data: { ...(node.data as EvidenceNodeData), hasNote: noted },
            }
          : {
              ...node,
              data: { ...(node.data as EvidenceNodeData), hasNote: noted },
            };
      });
      const stickies = previous.filter((node) => node.type === "sticky");
      return [...evidence, ...stickies];
    });
    // overlay.notes is read intentionally: note dots must refresh too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [laidOut, overlay.notes, setNodes]);

  useEffect(() => {
    setEdges(flowEdges);
  }, [flowEdges, setEdges]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      if (node.type === "sticky") return;
      const found = graph.nodes.find((item) => item.id === node.id);
      setSelected(found ?? null);
    },
    [graph.nodes],
  );

  const onNodeDragStop = useCallback(
    (_: unknown, node: Node) => {
      setOverlay((current) => ({
        ...current,
        positions: {
          ...current.positions,
          [node.id]: { x: node.position.x, y: node.position.y },
        },
      }));
    },
    [],
  );

  const resetLayout = useCallback(() => {
    setOverlay((current) => ({ ...current, positions: {} }));
    setNodes((previous) => {
      const byId = new Map(laidOut.map((node) => [node.id, node]));
      return previous.map((node) =>
        node.type === "sticky" ? node : (byId.get(node.id) ?? node),
      );
    });
  }, [laidOut, setNodes]);

  const changeSticky = useCallback((id: string, text: string) => {
    setOverlay((current) => ({
      ...current,
      stickies: current.stickies.map((note) =>
        note.id === id ? { ...note, text } : note,
      ),
    }));
    setNodes((previous) =>
      previous.map((node) =>
        node.id === id && node.type === "sticky"
          ? { ...node, data: { ...node.data, text } as StickyNodeData }
          : node,
      ),
    );
  }, [setNodes]);

  const deleteSticky = useCallback((id: string) => {
    setOverlay((current) => ({
      ...current,
      stickies: current.stickies.filter((note) => note.id !== id),
      positions: Object.fromEntries(
        Object.entries(current.positions).filter(([key]) => key !== id),
      ),
    }));
    setNodes((previous) => previous.filter((node) => node.id !== id));
  }, [setNodes]);

  const addSticky = useCallback(() => {
    const id = newStickyId();
    const count = overlay.stickies.length;
    const position = { x: 72 + (count % 4) * 32, y: 72 + (count % 4) * 28 };
    setOverlay((current) => ({
      ...current,
      stickies: [
        ...current.stickies,
        { id, x: position.x, y: position.y, text: "", color: "amber" },
      ],
      positions: { ...current.positions, [id]: position },
    }));
    setNodes((previous) => [
      ...previous,
      {
        id,
        type: "sticky",
        position,
        draggable: true,
        data: {
          text: "",
          color: "amber",
          onChange: changeSticky,
          onDelete: deleteSticky,
        } satisfies StickyNodeData,
      } satisfies Node<StickyNodeData> as MapFlowNode,
    ]);
  }, [changeSticky, deleteSticky, overlay.stickies.length, setNodes]);

  // Sticky callbacks are created once; wire them into existing sticky nodes
  // so the closures never go stale.
  useEffect(() => {
    setNodes((previous) =>
      previous.map((node) =>
        node.type === "sticky"
          ? {
              ...node,
              data: {
                ...node.data,
                onChange: changeSticky,
                onDelete: deleteSticky,
              } as StickyNodeData,
            }
          : node,
      ),
    );
  }, [changeSticky, deleteSticky, setNodes]);

  const setNodeNote = useCallback((nodeId: string, note: string) => {
    setOverlay((current) => {
      const notes = { ...current.notes };
      if (note.trim()) notes[nodeId] = note;
      else delete notes[nodeId];
      return { ...current, notes };
    });
  }, []);

  const buildExportModel = useCallback((): {
    exportNodes: ExportNode[];
    exportEdges: ExportEdge[];
  } => {
    const exportNodes: ExportNode[] = nodes.map((node) => {
      if (node.type === "sticky") {
        const sticky = overlay.stickies.find((note) => note.id === node.id);
        return {
          id: node.id,
          x: node.position.x,
          y: node.position.y,
          label:
            (node.data as StickyNodeData).text ||
            t("（空便签）"),
          typeLabel: t("研究者便签"),
          tone: "unknown" as NodeTone,
          kind: "sticky",
          stickyColor: sticky?.color ?? "amber",
        };
      }
      const data = node.data as EvidenceNodeData;
      return {
        id: node.id,
        x: node.position.x,
        y: node.position.y,
        label: data.label,
        typeLabel: data.typeLabel,
        tone: data.tone,
        kind: "evidence",
      };
    });
    const exportEdges: ExportEdge[] = edges
      .map((edge) => ({
        source: edge.source,
        target: edge.target,
        label: typeof edge.label === "string" ? edge.label : "",
        kind: (edge.data?.kind as EdgeKind | undefined) ?? "normal",
      }))
      .filter(
        (edge) =>
          exportNodes.some((node) => node.id === edge.source) &&
          exportNodes.some((node) => node.id === edge.target),
      );
    return { exportNodes, exportEdges };
  }, [edges, nodes, overlay.stickies]);

  const exportTitle = useMemo(() => {
    const question = graph.nodes.find(
      (node) => node.node_type === "ResearchQuestion",
    );
    const text = question
      ? labelOf(question)
      : t("Poliscope 争议证据地图");
    return `Poliscope · ${text}`.slice(0, 120);
  }, [graph.nodes, t]);

  if (graph.nodes.length === 0) {
    return (
      <div className="map map--empty">
        <Empty>
          {t(
            "证据图为空。任务尚未产出任何被采纳的证据节点——这不等于「没有争议」，而是「还没有可展示的证据」。",
          )}
        </Empty>
      </div>
    );
  }

  const { exportNodes, exportEdges } = buildExportModel();

  return (
    <div className={`map${dimRefuted ? " map--dim-refuted" : ""}`}>
      <div className="map__canvas-wrap">
        <div className="map__toolbar" role="toolbar" aria-label={t("证据图工具")}>
          <button
            type="button"
            className="map__tool"
            onClick={addSticky}
            title={t("添加一张仅你可见的研究便签，可在画布上拖动")}
          >
            {t("＋ 便签")}
          </button>
          <button
            type="button"
            className="map__tool"
            onClick={resetLayout}
            title={t("清除你拖动过的位置，恢复按证据类型分列的自动布局")}
          >
            {t("重置布局")}
          </button>
          <span className="map__tool-divider" aria-hidden="true" />
          <button
            type="button"
            className="map__tool"
            onClick={() => exportPngFile(exportNodes, exportEdges, exportTitle)}
            title={t("导出当前画布为 PNG 图片（2 倍清晰度）")}
          >
            {t("导出 PNG")}
          </button>
          <button
            type="button"
            className="map__tool"
            onClick={() => exportSvgFile(exportNodes, exportEdges, exportTitle)}
            title={t("导出可缩放矢量图 SVG，适合论文与打印")}
          >
            {t("导出 SVG")}
          </button>
          <button
            type="button"
            className="map__tool"
            onClick={() => exportJsonFile(graph, overlay, taskId)}
            title={t("导出证据图与你的私人备注/布局为 JSON")}
          >
            {t("导出 JSON")}
          </button>
        </div>
        <div className="map__canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onNodeDragStop={onNodeDragStop}
            onPaneClick={() => setSelected(null)}
            fitView
            fitViewOptions={{ padding: 0.2, duration: 320 }}
            minZoom={0.2}
            maxZoom={2}
            defaultEdgeOptions={{ type: "default" }}
            proOptions={{ hideAttribution: true }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={26}
              size={1.4}
              color="rgba(0,0,0,0.12)"
            />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              className="map__minimap"
              maskColor="rgba(0, 102, 204, 0.06)"
            />
          </ReactFlow>
        </div>
      </div>

      <aside className="map__side">
        <div className="map__legend">
          <h3>{t("图例")}</h3>
          <ul>
            <li>
              <Badge tone="admitted">{t("已采纳")}</Badge> {t("全文可得，证据等级 A")}
            </li>
            <li>
              <Badge tone="provisional">{t("仅元数据")}</Badge> {t("等级 B，未读原文")}
            </li>
            <li>
              <Badge tone="refuted">{t("已反驳/隔离")}</Badge> {t("保留可审计，不删除")}
            </li>
            <li>
              <span className="map__legend-edge map__legend-edge--cite" />
              {t("点线蓝边 = 结论/争议对应的文献")}
            </li>
          </ul>
          <label className="map__toggle">
            <input
              type="checkbox"
              checked={dimRefuted}
              onChange={(event) => setDimRefuted(event.target.checked)}
            />
            {t("淡化已反驳节点")}
            <span className="map__toggle-note">
              {t("仅改变不透明度。被反驳的节点永远留在图上。")}
            </span>
          </label>
        </div>

        <div className="map__inspector">
          <h3>{t("节点详情")}</h3>
          {selected === null ? (
            <Empty>{t("点击任一节点查看其类型、状态与可读详情；文献节点可跳转原文。")}</Empty>
          ) : (
            <>
              <MapNodeDetail
                node={selected}
                graph={graph}
                onSelect={setSelected}
              />
              <div className="map__note">
                <span className="map__note-label">
                  {t("研究者私人备注")}
                </span>
                <textarea
                  className="map__note-input"
                  value={overlay.notes[selected.id] ?? ""}
                  placeholder={t(
                    "只保存在你的浏览器里，不会写入证据图，也不会被其他科学家引用。",
                  )}
                  onChange={(event) =>
                    setNodeNote(selected.id, event.target.value)
                  }
                  rows={4}
                />
              </div>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
