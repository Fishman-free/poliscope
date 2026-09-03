/** Zero-dependency evidence-map export: JSON, SVG and raster PNG.
 *
 * The formal graph comes from the server; the exported file additionally
 * carries the researcher's overlay (private notes, stickies, layout), and the
 * SVG/PNG render a faithful, print-readable snapshot of the current canvas --
 * dashed red for opposing relations, dotted blue for citation hops, so the
 * edge semantics survive greyscale just like on screen.
 */

import type { EvidenceGraph } from "../../api/types";
import type { MapOverlay, StickyNote } from "./overlayStore";

export type NodeTone = "admitted" | "provisional" | "refuted" | "unknown";

export interface ExportNode {
  id: string;
  x: number;
  y: number;
  label: string;
  typeLabel: string;
  tone: NodeTone;
  kind: "evidence" | "sticky";
  stickyColor?: StickyNote["color"];
}

export interface ExportEdge {
  source: string;
  target: string;
  label: string;
  kind: "normal" | "opposing" | "cite";
}

const NODE_W = 220;
const PAD_X = 14;
const PAD_Y = 10;
const LINE_H = 17;
const HEADER_H = 22;
const CANVAS_PAD = 48;

const TONE_COLORS: Record<NodeTone, string> = {
  admitted: "#1a7f37",
  provisional: "#9a6700",
  refuted: "#cf222e",
  unknown: "#6e7781",
};

const STICKY_COLORS: Record<StickyNote["color"], { fill: string; bar: string }> =
  {
    amber: { fill: "#fff8e1", bar: "#d4a72c" },
    blue: { fill: "#eef4ff", bar: "#0066cc" },
    green: { fill: "#edf7ef", bar: "#1a7f37" },
  };

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Greedy wrap by visual width: CJK chars count ~1 unit, Latin ~0.55. */
function wrapLines(text: string, maxUnits = 26): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    let line = "";
    let width = 0;
    for (const char of paragraph) {
      const unit = /[⺀-鿿＀-￯]/.test(char) ? 1 : 0.55;
      if (width + unit > maxUnits && line) {
        lines.push(line);
        line = char;
        width = unit;
      } else {
        line += char;
        width += unit;
      }
    }
    lines.push(line || " ");
  }
  return lines.slice(0, 6);
}

function nodeHeight(label: string): number {
  return HEADER_H + wrapLines(label).length * LINE_H + PAD_Y * 2;
}

export function buildSvg(
  nodes: ExportNode[],
  edges: ExportEdge[],
  title: string,
): string {
  const heights = new Map(nodes.map((node) => [node.id, nodeHeight(node.label)]));
  const bounds = nodes.reduce(
    (acc, node) => {
      const h = heights.get(node.id) ?? 80;
      acc.minX = Math.min(acc.minX, node.x);
      acc.minY = Math.min(acc.minY, node.y);
      acc.maxX = Math.max(acc.maxX, node.x + NODE_W);
      acc.maxY = Math.max(acc.maxY, node.y + h);
      return acc;
    },
    { minX: 0, minY: 0, maxX: 0, maxY: 0 },
  );
  const width = Math.max(
    800,
    bounds.maxX - bounds.minX + CANVAS_PAD * 2,
  );
  const height =
    Math.max(600, bounds.maxY - bounds.minY + CANVAS_PAD * 2) + 40;
  const ox = CANVAS_PAD - bounds.minX;
  const oy = CANVAS_PAD - bounds.minY;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const edgeSvg: string[] = [];
  for (const edge of edges) {
    const s = byId.get(edge.source);
    const t = byId.get(edge.target);
    if (!s || !t) continue;
    const sh = heights.get(s.id) ?? 80;
    const x1 = s.x + ox + NODE_W;
    const y1 = s.y + oy + sh / 2;
    const x2 = t.x + ox;
    const y2 = t.y + oy + (heights.get(t.id) ?? 80) / 2;
    const dx = Math.max(48, Math.abs(x2 - x1) / 2);
    const path = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 + dx).toFixed(1)} ${y1.toFixed(1)}, ${(x2 - dx).toFixed(1)} ${y2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
    const color =
      edge.kind === "opposing"
        ? TONE_COLORS.refuted
        : edge.kind === "cite"
          ? "#0066cc"
          : "#86868b";
    const dash =
      edge.kind === "opposing"
        ? ' stroke-dasharray="6 4"'
        : edge.kind === "cite"
          ? ' stroke-dasharray="2 4"'
          : "";
    const midX = (x1 + x2) / 2;
    const midY = (y1 + y2) / 2 - 4;
    edgeSvg.push(
      `<path d="${path}" fill="none" stroke="${color}" stroke-width="1.6"${dash} marker-end="url(#arrow-${edge.kind})"/>`,
    );
    if (edge.label) {
      edgeSvg.push(
        `<rect x="${(midX - edge.label.length * 3.2 - 4).toFixed(1)}" y="${(midY - 11).toFixed(1)}" width="${(edge.label.length * 6.4 + 8).toFixed(1)}" height="15" rx="7" fill="#ffffff" fill-opacity="0.92"/>`,
      );
      edgeSvg.push(
        `<text x="${midX.toFixed(1)}" y="${midY.toFixed(1)}" text-anchor="middle" font-size="9.5" fill="#6e6e73" font-family="system-ui, sans-serif">${escapeXml(edge.label)}</text>`,
      );
    }
  }

  const nodeSvg: string[] = [];
  for (const node of nodes) {
    const h = heights.get(node.id) ?? 80;
    const x = node.x + ox;
    const y = node.y + oy;
    if (node.kind === "sticky") {
      const paper = STICKY_COLORS[node.stickyColor ?? "amber"];
      nodeSvg.push(
        `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${NODE_W}" height="${h.toFixed(1)}" rx="12" fill="${paper.fill}" stroke="${paper.bar}" stroke-opacity="0.45"/>`,
      );
    } else {
      const tone = TONE_COLORS[node.tone];
      nodeSvg.push(
        `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${NODE_W}" height="${h.toFixed(1)}" rx="12" fill="#ffffff" stroke="rgba(0,0,0,0.14)"/>`,
      );
      nodeSvg.push(
        `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="4" height="${h.toFixed(1)}" rx="2" fill="${tone}"/>`,
      );
      nodeSvg.push(
        `<text x="${(x + PAD_X + 4).toFixed(1)}" y="${(y + 16).toFixed(1)}" font-size="9.5" font-weight="600" fill="${tone}" font-family="system-ui, sans-serif">${escapeXml(node.typeLabel)}</text>`,
      );
    }
    const lines = wrapLines(node.label);
    lines.forEach((line, index) => {
      nodeSvg.push(
        `<text x="${(x + PAD_X + (node.kind === "sticky" ? 0 : 4)).toFixed(1)}" y="${(y + HEADER_H + (index + 1) * LINE_H - 4).toFixed(1)}" font-size="11.5" fill="#1d1d1f" font-family="system-ui, sans-serif">${escapeXml(line)}</text>`,
      );
    });
  }

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <defs>
    <marker id="arrow-normal" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#86868b"/></marker>
    <marker id="arrow-opposing" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${TONE_COLORS.refuted}"/></marker>
    <marker id="arrow-cite" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#0066cc"/></marker>
  </defs>
  <rect width="100%" height="100%" fill="#fbfbfd"/>
  <text x="${CANVAS_PAD}" y="28" font-size="14" font-weight="600" fill="#1d1d1f" font-family="system-ui, sans-serif">${escapeXml(title)}</text>
  ${edgeSvg.join("\n  ")}
  ${nodeSvg.join("\n  ")}
</svg>`;
}

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function stamp(): string {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
}

export function exportSvgFile(
  nodes: ExportNode[],
  edges: ExportEdge[],
  title: string,
): void {
  const svg = buildSvg(nodes, edges, title);
  download(
    new Blob([svg], { type: "image/svg+xml;charset=utf-8" }),
    `poliscope-evidence-map-${stamp()}.svg`,
  );
}

export function exportJsonFile(
  graph: EvidenceGraph,
  overlay: MapOverlay,
  taskId: string,
): void {
  const payload = {
    exported_at: new Date().toISOString(),
    task_id: taskId,
    schema: "poliscope.evidence_map_export.v1",
    evidence_graph: graph,
    researcher_overlay: overlay,
    note: "researcher_overlay 是研究者的私人备注与布局，不是正式证据；正式证据以 evidence_graph 为准。",
  };
  download(
    new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
    `poliscope-evidence-map-${stamp()}.json`,
  );
}

/** Rasterise the self-contained SVG at 2x for crisp PNG export. */
export function exportPngFile(
  nodes: ExportNode[],
  edges: ExportEdge[],
  title: string,
): void {
  const svg = buildSvg(nodes, edges, title);
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const image = new Image();
  image.onload = () => {
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = image.width * scale;
    canvas.height = image.height * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      URL.revokeObjectURL(url);
      return;
    }
    ctx.scale(scale, scale);
    ctx.fillStyle = "#fbfbfd";
    ctx.fillRect(0, 0, image.width, image.height);
    ctx.drawImage(image, 0, 0, image.width, image.height);
    URL.revokeObjectURL(url);
    canvas.toBlob((png) => {
      if (png) download(png, `poliscope-evidence-map-${stamp()}.png`);
    }, "image/png");
  };
  image.onerror = () => URL.revokeObjectURL(url);
  image.src = url;
}
