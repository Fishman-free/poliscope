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
 * 3. **Sparse, multi-row automatic layout.** Each type lane wraps into several
 *    short sub-columns instead of one long dense vertical stack, so a task
 *    with many claims/sources stays readable.
 * 4. **Live graph updates merge into the researcher's arrangement.** A new
 *    SSE frame adds/moves formal nodes without wiping the positions the
 *    researcher dragged; only a manual "重置布局" does that.
 * 5. **The researcher can edit a personal overlay.** Private node notes,
 *    draggable sticky notes (light paper fills, selectable text colour and
 *    font), arrows drawn between any blocks and the saved layout live in
 *    localStorage (see map/overlayStore.ts); they never enter the formal
 *    Evidence Graph and are labelled as the researcher's own material.
 * 6. **Export is offline and dependency-free** (map/exportGraph.ts): PNG /
 *    SVG snapshot of the current canvas INCLUDING every sticky note and
 *    connector the researcher added, plus JSON carrying graph + overlay.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Background,
  BackgroundVariant,
  type Connection,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
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
  STICKY_COLORS,
  type StickyColor,
  loadOverlay,
  newConnectorId,
  newStickyId,
  saveOverlay,
  type StickyNote,
} from "./map/overlayStore";
import {
  type EvidenceNodeData,
  type MapFlowNode,
  type StickyNodeData,
  type StickyStylePatch,
  nodeTypes,
} from "./map/MapNodes";
import { MapNodeDetail } from "./MapNodeDetail";

/** Relations that mean "this weakens that". Dashed and red so the meaning
 * survives both greyscale printing and colour-blindness. */
const OPPOSING = new Set(["REFUTES", "CONTRADICTS", "CONFOUNDS"]);

/** Major lane per node type: question -> claims/disputes -> findings/context
 * -> sources. The map reads left to right, the direction provenance is
 * checked in. */
const LANES: Record<string, number> = {
  ResearchQuestion: 0,
  Claim: 1,
  Blindspot: 1,
  DebateCapsule: 1,
  StudyFinding: 2,
  Construct: 2,
  Context: 2,
  Source: 3,
  DiscriminatingStudy: 3,
};

/** Sparse grid geometry. A lane wraps into a new sub-column after this many
 * rows instead of stacking everything in one tall, dense column. */
const NODE_W = 220;
const SUBCOLUMN_PITCH = NODE_W + 44;
const LANE_GAP = 92;
const ROW_HEIGHT = 196;
const ROWS_PER_SUBCOLUMN = 4;

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
  // Count nodes per lane to know how many wrapped sub-columns each lane needs.
  const laneLengths: [number, number, number, number] = [0, 0, 0, 0];
  for (const node of graph.nodes) {
    const lane = LANES[node.node_type] ?? 2;
    laneLengths[lane] = (laneLengths[lane] ?? 0) + 1;
  }
  const laneColumns = laneLengths.map((count) =>
    Math.max(1, Math.ceil(count / ROWS_PER_SUBCOLUMN)),
  ) as [number, number, number, number];
  const laneX: [number, number, number, number] = [0, 0, 0, 0];
  let cursor = 0;
  for (let lane = 0; lane < 4; lane += 1) {
    laneX[lane] = cursor;
    cursor += (laneColumns[lane] ?? 1) * SUBCOLUMN_PITCH + LANE_GAP;
  }
  // Running row index per lane, output kept in the server's graph order.
  const laneSeen: [number, number, number, number] = [0, 0, 0, 0];
  return graph.nodes.map((node) => {
    const lane = LANES[node.node_type] ?? 2;
    const index = laneSeen[lane] ?? 0;
    laneSeen[lane] = index + 1;
    const subColumn = Math.floor(index / ROWS_PER_SUBCOLUMN);
    const row = index % ROWS_PER_SUBCOLUMN;
    const tone = toneForStatus(node.status) as NodeTone;
    const data: EvidenceNodeData = {
      label: labelOf(node),
      typeLabel: NODE_TYPE_LABELS[node.node_type] ?? node.node_type,
      tone,
      hasNote: false,
    };
    return {
      id: node.id,
      position: {
        x: (laneX[lane] ?? 0) + subColumn * SUBCOLUMN_PITCH,
        y: row * ROW_HEIGHT,
      },
      data,
      type: "evidence",
      className: `map-node map-node--${tone}`,
      draggable: true,
    } satisfies Node<EvidenceNodeData> as MapFlowNode;
  });
}

type FormalEdgeKind = "normal" | "opposing" | "cite";
type AnyEdgeKind = FormalEdgeKind | "user";

function makeEdge(
  id: string,
  source: string,
  target: string,
  label: string,
  kind: AnyEdgeKind,
): Edge {
  const isUser = kind === "user";
  return {
    id,
    source,
    target,
    label,
    className: `map-edge map-edge--${kind}`,
    // Formal edges cannot be deleted; only the researcher's own connectors can.
    deletable: isUser,
    selectable: isUser,
    focusable: isUser,
    style:
      kind === "opposing"
        ? { strokeDasharray: "6 4" }
        : kind === "cite"
          ? { strokeDasharray: "2 4" }
          : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
    data: { kind },
  };
}

function toEdges(graph: EvidenceGraph): Edge[] {
  const drawn = new Set(
    graph.edges.map((edge) => `${edge.source}->${edge.target}:${edge.edge_type}`),
  );
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

function MapViewInner({
  graph,
  taskId,
}: {
  graph: EvidenceGraph;
  taskId: string;
}) {
  const laidOut = useMemo(() => layout(graph), [graph]);
  const flowEdges = useMemo(() => toEdges(graph), [graph]);
  const [nodes, setNodes, onNodesChange] = useNodesState<MapFlowNode>([]);
  const [edges, setEdges, onEdgesChangeRaw] = useEdgesState<Edge>([]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [dimRefuted, setDimRefuted] = useState(true);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [overlay, setOverlay] = useState<MapOverlay>(() =>
    loadOverlay(taskId),
  );
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const reactFlow = useReactFlow();

  // Switching sessions loads that session's own overlay.
  useEffect(() => {
    setOverlay(loadOverlay(taskId));
    setSelected(null);
    setSelectedEdgeId(null);
  }, [taskId]);

  // Persist every overlay change; saveOverlay itself is best-effort.
  useEffect(() => {
    saveOverlay(taskId, overlay);
  }, [taskId, overlay]);

  const buildStickyNode = useCallback(
    (note: StickyNote): MapFlowNode =>
      ({
        id: note.id,
        type: "sticky",
        position: { x: note.x, y: note.y },
        draggable: true,
        data: {
          text: note.text,
          color: note.color,
          textColor: note.textColor,
          font: note.font,
          onChange: (id: string, text: string) => changeSticky(id, { text }),
          onStyle: (id: string, patch: StickyStylePatch) =>
            changeSticky(id, patch),
          onDelete: deleteSticky,
        } satisfies StickyNodeData,
      }) satisfies Node<StickyNodeData> as MapFlowNode,
    // callbacks are stable via useCallback below; eslint sees them later.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Merge a fresh server graph into the flow. The overlay is the single
  // source of truth for anything the researcher owns: dragged positions,
  // sticky notes (including ones restored from a previous session) and note
  // dots.
  useEffect(() => {
    setNodes(() => {
      const evidence = laidOut.map((node) => {
        const saved = overlay.positions[node.id];
        const noted = Boolean(overlay.notes[node.id]);
        return {
          ...node,
          position: saved ?? node.position,
          data: { ...(node.data as EvidenceNodeData), hasNote: noted },
        };
      });
      const stickies = overlay.stickies.map(buildStickyNode);
      return [...evidence, ...stickies];
    });
  }, [laidOut, overlay.notes, overlay.positions, overlay.stickies, buildStickyNode, setNodes]);

  // Formal edges + the researcher's own connectors. Connectors are rebuilt
  // from the overlay so a formal graph refresh can never wipe them.
  const connectorEdges = useMemo(
    () =>
      overlay.connectors.map((connector) =>
        makeEdge(
          connector.id,
          connector.source,
          connector.target,
          t("我的连线"),
          "user",
        ),
      ),
    [overlay.connectors],
  );

  useEffect(() => {
    setEdges([...flowEdges, ...connectorEdges]);
  }, [flowEdges, connectorEdges, setEdges]);

  // React Flow asks to remove an edge when the user presses Delete/Backspace
  // on a selected connector: drop it from the overlay as well, otherwise the
  // rebuild above would immediately resurrect it.
  const onEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChangeRaw>[0]) => {
      const removed = changes
        .filter((change) => change.type === "remove")
        .map((change) => change.id);
      if (removed.length > 0) {
        const removedSet = new Set(removed);
        setOverlay((current) => ({
          ...current,
          connectors: current.connectors.filter(
            (connector) => !removedSet.has(connector.id),
          ),
        }));
        setSelectedEdgeId((current) =>
          current && removedSet.has(current) ? null : current,
        );
      }
      onEdgesChangeRaw(changes);
    },
    [onEdgesChangeRaw],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      if (connection.source === connection.target) return;
      setOverlay((current) => {
        const duplicate = current.connectors.some(
          (connector) =>
            connector.source === connection.source &&
            connector.target === connection.target,
        );
        if (duplicate) return current;
        return {
          ...current,
          connectors: [
            ...current.connectors,
            {
              id: newConnectorId(),
              source: connection.source,
              target: connection.target,
            },
          ],
        };
      });
    },
    [],
  );

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedEdgeId) return;
    const target = selectedEdgeId;
    setOverlay((current) => ({
      ...current,
      connectors: current.connectors.filter(
        (connector) => connector.id !== target,
      ),
    }));
    setSelectedEdgeId(null);
  }, [selectedEdgeId]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      setSelectedEdgeId(null);
      if (node.type === "sticky") return;
      const found = graph.nodes.find((item) => item.id === node.id);
      setSelected(found ?? null);
    },
    [graph.nodes],
  );

  const onNodeDragStop = useCallback(
    (_: unknown, node: Node) => {
      // Sticky notes read their position back from overlay.stickies (the
      // merge effect rebuilds them from that list), so a dragged sticky must
      // update its own x/y; saving only positions would make it snap back.
      setOverlay((current) => {
        if (node.type === "sticky") {
          return {
            ...current,
            stickies: current.stickies.map((note) =>
              note.id === node.id
                ? { ...note, x: node.position.x, y: node.position.y }
                : note,
            ),
            positions: {
              ...current.positions,
              [node.id]: { x: node.position.x, y: node.position.y },
            },
          };
        }
        return {
          ...current,
          positions: {
            ...current.positions,
            [node.id]: { x: node.position.x, y: node.position.y },
          },
        };
      });
    },
    [],
  );

  const resetLayout = useCallback(() => {
    setOverlay((current) => {
      // Keep sticky positions (they are the researcher's own blocks); only
      // formal evidence nodes return to the automatic layout.
      const stickyIds = new Set(current.stickies.map((note) => note.id));
      const positions = Object.fromEntries(
        Object.entries(current.positions).filter(([id]) => stickyIds.has(id)),
      );
      return { ...current, positions };
    });
  }, []);

  const changeSticky = useCallback(
    (id: string, patch: Partial<Omit<StickyNote, "id">>) => {
      setOverlay((current) => ({
        ...current,
        stickies: current.stickies.map((note) =>
          note.id === id ? { ...note, ...patch } : note,
        ),
        positions:
          typeof patch.x === "number" && typeof patch.y === "number"
            ? { ...current.positions, [id]: { x: patch.x, y: patch.y } }
            : current.positions,
      }));
    },
    [],
  );

  const deleteSticky = useCallback((id: string) => {
    setOverlay((current) => ({
      ...current,
      stickies: current.stickies.filter((note) => note.id !== id),
      connectors: current.connectors.filter(
        (connector) => connector.source !== id && connector.target !== id,
      ),
      positions: Object.fromEntries(
        Object.entries(current.positions).filter(([key]) => key !== id),
      ),
    }));
  }, []);

  // Create a note at the CENTER of the visible viewport (not a fixed world
  // coordinate hidden behind the first evidence card, which is why added
  // notes used to look like they "did not appear"), cascaded slightly.
  const addSticky = useCallback(
    (color: StickyColor) => {
      const id = newStickyId();
      const wrapper = wrapperRef.current;
      let position = { x: 96, y: 96 };
      if (wrapper) {
        const rect = wrapper.getBoundingClientRect();
        position = reactFlow.screenToFlowPosition({
          x: rect.left + rect.width / 2 + (overlay.stickies.length % 4) * 28,
          y: rect.top + rect.height / 2 + (overlay.stickies.length % 4) * 24,
        });
      }
      const note: StickyNote = {
        id,
        x: position.x,
        y: position.y,
        text: "",
        color,
        textColor: "ink",
        font: "sans",
      };
      setOverlay((current) => ({
        ...current,
        stickies: [...current.stickies, note],
        positions: { ...current.positions, [id]: position },
      }));
      setPaletteOpen(false);
    },
    [overlay.stickies.length, reactFlow],
  );

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
        const data = node.data as StickyNodeData;
        return {
          id: node.id,
          x: node.position.x,
          y: node.position.y,
          label: data.text || t("（空便签）"),
          typeLabel: t("研究者便签"),
          tone: "unknown" as NodeTone,
          kind: "sticky",
          stickyColor: data.color,
          textColor: data.textColor,
          font: data.font,
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
        kind: (edge.data?.kind as AnyEdgeKind | undefined) ?? "normal",
      }))
      .filter(
        (edge) =>
          exportNodes.some((node) => node.id === edge.source) &&
          exportNodes.some((node) => node.id === edge.target),
      );
    return { exportNodes, exportEdges };
  }, [edges, nodes]);

  const exportTitle = useMemo(() => {
    const question = graph.nodes.find(
      (node) => node.node_type === "ResearchQuestion",
    );
    const text = question
      ? labelOf(question)
      : t("Poliscope 争议证据地图");
    return `Poliscope · ${text}`.slice(0, 120);
  }, [graph.nodes]);

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
          <div className="map__sticky-add">
            <button
              type="button"
              className="map__tool"
              onClick={() => setPaletteOpen((open) => !open)}
              title={t("选择浅色便签颜色，在画布中央添加一张可拖动的便签")}
            >
              {t("＋ 便签")}
            </button>
            {paletteOpen ? (
              <div className="map__palette" role="menu" aria-label={t("选择便签颜色")}>
                {STICKY_COLORS.map((color) => (
                  <button
                    key={color}
                    type="button"
                    role="menuitem"
                    className={`map__palette-swatch map__palette-swatch--${color}`}
                    title={color}
                    onClick={() => addSticky(color)}
                  />
                ))}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className="map__tool"
            onClick={resetLayout}
            title={t("清除你拖动过的证据节点位置，恢复稀疏自动布局（便签保留）")}
          >
            {t("重置布局")}
          </button>
          {selectedEdgeId ? (
            <button
              type="button"
              className="map__tool map__tool--danger"
              onClick={deleteSelectedEdge}
              title={t("删除选中的连线")}
            >
              {t("删除连线")}
            </button>
          ) : null}
          <span className="map__tool-divider" aria-hidden="true" />
          <button
            type="button"
            className="map__tool"
            onClick={() => exportPngFile(exportNodes, exportEdges, exportTitle)}
            title={t("导出当前画布为 PNG 图片（2 倍清晰度，包含便签与连线）")}
          >
            {t("导出 PNG")}
          </button>
          <button
            type="button"
            className="map__tool"
            onClick={() => exportSvgFile(exportNodes, exportEdges, exportTitle)}
            title={t("导出可缩放矢量图 SVG，适合论文与打印（包含便签与连线）")}
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
        <p className="map__hint">
          {t(
            "把节点两侧的小圆点拖到另一个方块上即可画箭头连接；点击连线后可删除。便签可拖动、改底色/文字颜色/字体，导出图片时会一并保留。",
          )}
        </p>
        <div className="map__canvas" ref={wrapperRef}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onNodeDragStop={onNodeDragStop}
            onEdgeClick={(_, edge) => setSelectedEdgeId(edge.id)}
            onPaneClick={() => {
              setSelected(null);
              setSelectedEdgeId(null);
              setPaletteOpen(false);
            }}
            fitView
            fitViewOptions={{ padding: 0.2, duration: 320 }}
            minZoom={0.2}
            maxZoom={2}
            deleteKeyCode={["Backspace", "Delete"]}
            connectionLineStyle={{ stroke: "#7c5cbf", strokeWidth: 2 }}
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
            <li>
              <span className="map__legend-edge map__legend-edge--user" />
              {t("紫色实线 = 你自己画的连接箭头")}
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

/** Provider wrapper: useReactFlow (viewport-centered sticky creation) needs a
 * ReactFlowProvider above the component that calls it. */
export function MapView(props: { graph: EvidenceGraph; taskId: string }) {
  return (
    <ReactFlowProvider>
      <MapViewInner {...props} />
    </ReactFlowProvider>
  );
}
