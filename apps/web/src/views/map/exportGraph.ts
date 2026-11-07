/** Zero-dependency evidence-map export: JSON, SVG and raster PNG.
 *
 * The formal graph comes from the server; the exported file additionally
 * carries the researcher's overlay (private notes, stickies, connectors,
 * layout), and the SVG/PNG render a faithful, print-readable snapshot of the
 * current canvas -- dashed red for opposing relations, dotted blue for
 * citation hops, solid purple for the researcher's own connectors, so every
 * edge semantics survives greyscale just like on screen. Sticky notes are
 * painted with the same light fill, text colour and font the researcher
 * chose, so an exported image contains the notes exactly as authored.
 *
 * Fidelity (2026-11 report: the PNG "did not look like the map" and text ran
 * out of its boxes). The old renderer guessed: a fixed 220px box, rounded
 * corners and a left colour bar the canvas no longer has, and a wrap width
 * counted in "CJK units" that let ~26 CJK glyphs (≈300px) into a ≈190px
 * line. Now:
 *
 * - geometry and styling mirror MapView.css (square ink-framed slips, the
 *   tone mark on the type chip, dashed = metadata only, dotted = blindspot)
 *   and every box starts from the size React Flow measured on screen;
 * - text is wrapped by *measured* width (canvas ``measureText``) in the exact
 *   font stack the SVG declares. Web fonts are left out of that stack on
 *   purpose: an SVG rasterised through ``<img>`` cannot load them, so
 *   measuring with them would wrap for a font the image never uses;
 * - a box grows to fit its text, and each box clips its own text, so even a
 *   viewer with different fonts cannot spill text into a neighbour;
 * - edges use React Flow's own ``getBezierPath`` between the same right/left
 *   handles, so curves and label positions match the canvas.
 */

import { Position, getBezierPath } from "@xyflow/react";

import type { EvidenceGraph } from "../../api/types";
import type {
  MapOverlay,
  StickyColor,
  StickyFont,
  StickyTextColor,
} from "./overlayStore";

export type NodeTone = "admitted" | "provisional" | "refuted" | "unknown";

export interface ExportNode {
  id: string;
  x: number;
  y: number;
  /** Size React Flow measured on screen; the export box never shrinks below it. */
  width?: number;
  height?: number;
  label: string;
  typeLabel: string;
  tone: NodeTone;
  kind: "evidence" | "sticky";
  blindspot?: boolean;
  /** Mirrors the canvas's "淡化已反驳节点" toggle. */
  dimmed?: boolean;
  hasNote?: boolean;
  stickyColor?: StickyColor;
  textColor?: StickyTextColor;
  font?: StickyFont;
}

export interface ExportEdge {
  source: string;
  target: string;
  label: string;
  kind: "normal" | "opposing" | "cite" | "user";
}

/* Geometry mirrored from MapView.css. */
const EVIDENCE_W = 230; // .react-flow__node.map-node
const STICKY_W = 208; // .react-flow__node.map-sticky
const BORDER = 1;
const PAD_X = 12; // --space-3
const PAD_Y = 8; // --space-2
const LABEL_SIZE = 13;
const LABEL_LH = LABEL_SIZE * 1.5;
const CHIP_SIZE = 10.5;
const CHIP_LINE = LABEL_LH; // inline-block chip sits on the 13px line strut
const CHIP_GAP = 4;
const STICKY_BAR_H = 26;
const STICKY_PAD_X = 8;
const STICKY_PAD_B = 8;
const STICKY_SIZE = 14; // --text-small
const STICKY_LH = STICKY_SIZE * 1.55;
const STICKY_MIN_TEXT_H = STICKY_SIZE * 4.65;
const EDGE_LABEL_SIZE = 10;
const TITLE_SIZE = 14;
const TITLE_LH = 20;
const CANVAS_PAD = 48;
/* A hair of slack so sub-pixel differences between the measuring canvas and
 * the SVG rasteriser can never push the last glyph past the padding. */
const WRAP_SLACK = 2;

const SANS =
  "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', system-ui, sans-serif";
const MONO =
  "ui-monospace, 'Cascadia Mono', Consolas, 'PingFang SC', 'Microsoft YaHei', monospace";

const FONT_STACKS: Record<StickyFont, string> = {
  sans: "system-ui, 'PingFang SC', 'Microsoft YaHei', sans-serif",
  serif: "Georgia, 'Songti SC', 'SimSun', serif",
  mono: "ui-monospace, 'Cascadia Mono', Consolas, monospace",
};

/** Light paper fills, the same as the canvas's sticky notes. */
const STICKY_FILLS: Record<StickyColor, string> = {
  yellow: "#fff7d6",
  pink: "#ffe9f0",
  blue: "#e8f1ff",
  green: "#e6f6e9",
  purple: "#f1ebff",
  orange: "#ffeddf",
};

const STICKY_TEXT_COLORS: Record<StickyTextColor, string> = {
  ink: "#1d1d1f",
  red: "#b3261e",
  blue: "#1d4ed8",
  green: "#166534",
};

interface Palette {
  paper: string;
  paperRaised: string;
  ink1: string;
  ink3: string;
  lineStrong: string;
  accent: string;
  tones: Record<NodeTone, string>;
}

/** Design tokens as currently resolved (tokens.css), with literal fallbacks
 * so the export still renders where computed styles are unavailable. */
function palette(): Palette {
  const style =
    typeof document !== "undefined"
      ? getComputedStyle(document.documentElement)
      : null;
  const token = (name: string, fallback: string) =>
    style?.getPropertyValue(name).trim() || fallback;
  return {
    paper: token("--paper", "#f3efe6"),
    paperRaised: token("--paper-raised", "#f9f6f0"),
    ink1: token("--ink-1", "#191714"),
    ink3: token("--ink-3", "#6a6356"),
    lineStrong: token("--line-strong", "rgba(25, 23, 20, 0.32)"),
    accent: token("--accent", "#21416a"),
    tones: {
      admitted: token("--admitted", "#2c6a3c"),
      provisional: token("--provisional", "#8f600f"),
      refuted: token("--refuted", "#9d2b23"),
      unknown: token("--unknown", "#6a6356"),
    },
  };
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const n = (value: number) => value.toFixed(1);

/* ---- Text measurement and wrapping ------------------------------------ */

const WIDE_CHAR = /[⺀-鿿豈-﫿＀-￯]/;
let measureContext: CanvasRenderingContext2D | null | undefined;

function textWidth(text: string, size: number, family: string, weight = 400): number {
  if (measureContext === undefined) {
    measureContext =
      typeof document !== "undefined"
        ? document.createElement("canvas").getContext("2d")
        : null;
  }
  if (measureContext) {
    measureContext.font = `${weight} ${size}px ${family}`;
    return measureContext.measureText(text).width;
  }
  // No canvas: a deliberately generous estimate (wide glyphs = 1em).
  let width = 0;
  for (const char of text) width += WIDE_CHAR.test(char) ? size : size * 0.62;
  return width;
}

/** Wide glyphs break anywhere; Latin words break at spaces unless a single
 * word is wider than the line (the canvas's ``overflow-wrap: anywhere``). */
function tokenize(paragraph: string): string[] {
  return (
    paragraph.match(
      /[⺀-鿿豈-﫿＀-￯]|\s+|[^\s⺀-鿿豈-﫿＀-￯]+/g,
    ) ?? []
  );
}

function wrapText(
  text: string,
  maxWidth: number,
  size: number,
  family: string,
  weight = 400,
): string[] {
  const width = (value: string) => textWidth(value, size, family, weight);
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    let line = "";
    const place = (piece: string) => {
      const blank = /^\s+$/.test(piece);
      if (!line && blank) return;
      if (line && width(line + piece) > maxWidth) {
        lines.push(line.trimEnd());
        line = blank ? "" : piece;
      } else {
        line += piece;
      }
    };
    for (const token of tokenize(paragraph)) {
      if (!/^\s+$/.test(token) && width(token) > maxWidth) {
        for (const char of token) place(char);
      } else {
        place(token);
      }
    }
    lines.push(line.trimEnd());
  }
  return lines;
}

/* ---- Layout ------------------------------------------------------------- */

interface Box {
  node: ExportNode;
  x: number;
  y: number;
  w: number;
  h: number;
  lines: string[];
}

function layoutBox(node: ExportNode): Box {
  if (node.kind === "sticky") {
    const w = node.width ?? STICKY_W;
    const family = FONT_STACKS[node.font ?? "sans"];
    const lines = wrapText(
      node.label,
      w - 2 * BORDER - 2 * STICKY_PAD_X - WRAP_SLACK,
      STICKY_SIZE,
      family,
    );
    const textH = Math.max(STICKY_MIN_TEXT_H, lines.length * STICKY_LH);
    const h = Math.max(
      node.height ?? 0,
      2 * BORDER + STICKY_BAR_H + textH + STICKY_PAD_B,
    );
    return { node, x: node.x, y: node.y, w, h, lines };
  }
  const w = node.width ?? EVIDENCE_W;
  const lines = wrapText(
    node.label,
    w - 2 * BORDER - 2 * PAD_X - WRAP_SLACK,
    LABEL_SIZE,
    SANS,
  );
  const h = Math.max(
    node.height ?? 0,
    2 * BORDER + 2 * PAD_Y + CHIP_LINE + CHIP_GAP + lines.length * LABEL_LH,
  );
  return { node, x: node.x, y: node.y, w, h, lines };
}

/* ---- Rendering ---------------------------------------------------------- */

function edgeStyle(kind: ExportEdge["kind"], colors: Palette) {
  switch (kind) {
    case "opposing":
      return { stroke: colors.tones.refuted, width: 1.8, dash: "6 4" };
    case "cite":
      return { stroke: colors.accent, width: 1.6, dash: "2 4" };
    case "user":
      return { stroke: "#7c5cbf", width: 2.2, dash: "" };
    default:
      return { stroke: colors.lineStrong, width: 1.8, dash: "" };
  }
}

function renderEvidence(box: Box, clipId: string, colors: Palette): string {
  const { node, x, y, w, h } = box;
  const tone = node.blindspot ? colors.accent : colors.tones[node.tone];
  const frame = node.blindspot ? colors.accent : colors.lineStrong;
  const dash = node.blindspot
    ? ' stroke-dasharray="1 2"'
    : node.tone === "provisional"
      ? ' stroke-dasharray="4 3"'
      : "";
  const fill = node.blindspot ? colors.paper : colors.paperRaised;
  const left = x + BORDER + PAD_X;
  const chipY = y + BORDER + PAD_Y + CHIP_LINE / 2;
  const labelTop = y + BORDER + PAD_Y + CHIP_LINE + CHIP_GAP;
  const parts = [
    `<rect x="${n(x + 0.5)}" y="${n(y + 0.5)}" width="${n(w - 1)}" height="${n(h - 1)}" fill="${fill}" stroke="${frame}" stroke-width="1"${dash}/>`,
    `<g clip-path="url(#${clipId})">`,
    `<text x="${n(left)}" y="${n(chipY)}" dominant-baseline="central" font-size="${CHIP_SIZE}" font-weight="500" letter-spacing="0.6" fill="${tone}" font-family="${MONO}">■ ${escapeXml(node.typeLabel)}</text>`,
    ...box.lines.map(
      (line, index) =>
        `<text x="${n(left)}" y="${n(labelTop + index * LABEL_LH + LABEL_LH / 2)}" dominant-baseline="central" font-size="${LABEL_SIZE}" fill="${colors.ink1}" font-family="${SANS}">${escapeXml(line)}</text>`,
    ),
    "</g>",
  ];
  if (node.hasNote) {
    parts.push(
      `<rect x="${n(x + w - 11)}" y="${n(y - 7)}" width="18" height="18" fill="${colors.accent}"/>`,
      `<text x="${n(x + w - 2)}" y="${n(y + 2)}" dominant-baseline="central" text-anchor="middle" font-size="10" fill="#ffffff" font-family="${SANS}">✎</text>`,
    );
  }
  return parts.join("\n  ");
}

function renderSticky(box: Box, clipId: string, colors: Palette): string {
  const { node, x, y, w, h } = box;
  const family = FONT_STACKS[node.font ?? "sans"];
  const textFill = STICKY_TEXT_COLORS[node.textColor ?? "ink"];
  const textTop = y + BORDER + STICKY_BAR_H;
  return [
    `<rect x="${n(x + 0.5)}" y="${n(y + 0.5)}" width="${n(w - 1)}" height="${n(h - 1)}" rx="2" fill="${STICKY_FILLS[node.stickyColor ?? "yellow"]}" stroke="rgba(0,0,0,0.22)" stroke-width="1" stroke-dasharray="4 3"/>`,
    `<g clip-path="url(#${clipId})">`,
    `<text x="${n(x + BORDER + STICKY_PAD_X)}" y="${n(y + BORDER + STICKY_BAR_H / 2)}" dominant-baseline="central" font-size="10" font-weight="600" letter-spacing="0.3" fill="${colors.ink3}" font-family="${SANS}">${escapeXml(node.typeLabel)}</text>`,
    ...box.lines.map(
      (line, index) =>
        `<text x="${n(x + BORDER + STICKY_PAD_X)}" y="${n(textTop + index * STICKY_LH + STICKY_LH / 2)}" dominant-baseline="central" font-size="${STICKY_SIZE}" fill="${textFill}" font-family="${family}">${escapeXml(line)}</text>`,
    ),
    "</g>",
  ].join("\n  ");
}

export function buildSvg(
  nodes: ExportNode[],
  edges: ExportEdge[],
  title: string,
): string {
  const colors = palette();
  const boxes = nodes.map(layoutBox);
  const byId = new Map(boxes.map((box) => [box.node.id, box]));

  const edgeSvg: string[] = [];
  const labelSpots: { x: number; y: number; w: number; h: number }[] = [];
  for (const edge of edges) {
    const s = byId.get(edge.source);
    const t = byId.get(edge.target);
    if (!s || !t) continue;
    // The same handles as the canvas: source on the right, target on the left.
    const [path, labelX, labelY] = getBezierPath({
      sourceX: s.x + s.w,
      sourceY: s.y + s.h / 2,
      sourcePosition: Position.Right,
      targetX: t.x,
      targetY: t.y + t.h / 2,
      targetPosition: Position.Left,
    });
    const style = edgeStyle(edge.kind, colors);
    // Every path attribute must appear exactly once: a duplicated attribute
    // makes the SVG invalid XML, so rasterising it via <img> fails silently.
    edgeSvg.push(
      `<path d="${path}" fill="none" stroke="${style.stroke}" stroke-width="${style.width}"${style.dash ? ` stroke-dasharray="${style.dash}"` : ""} marker-end="url(#arrow-${edge.kind})"/>`,
    );
    if (edge.label) {
      const labelW = textWidth(edge.label, EDGE_LABEL_SIZE, SANS) + 8;
      const labelH = EDGE_LABEL_SIZE + 4;
      labelSpots.push({ x: labelX - labelW / 2, y: labelY - labelH / 2, w: labelW, h: labelH });
      edgeSvg.push(
        `<rect x="${n(labelX - labelW / 2)}" y="${n(labelY - labelH / 2)}" width="${n(labelW)}" height="${n(labelH)}" fill="${colors.paper}"/>`,
        `<text x="${n(labelX)}" y="${n(labelY)}" dominant-baseline="central" text-anchor="middle" font-size="${EDGE_LABEL_SIZE}" fill="${colors.ink3}" font-family="${SANS}">${escapeXml(edge.label)}</text>`,
      );
    }
  }

  // Bounds seeded from the content, not from 0, so a graph dragged far from
  // the origin does not export with a band of dead whitespace.
  const rects = [...boxes, ...labelSpots];
  const minX = rects.length ? Math.min(...rects.map((r) => r.x)) : 0;
  const minY = rects.length ? Math.min(...rects.map((r) => r.y)) : 0;
  const maxX = rects.length ? Math.max(...rects.map((r) => r.x + r.w)) : 0;
  const maxY = rects.length ? Math.max(...rects.map((r) => r.y + r.h)) : 0;
  const width = Math.ceil(Math.max(800, maxX - minX + CANVAS_PAD * 2));
  const titleLines = wrapText(
    title,
    width - CANVAS_PAD * 2,
    TITLE_SIZE,
    SANS,
    600,
  );
  const header = 20 + titleLines.length * TITLE_LH;
  const height = Math.ceil(
    Math.max(600, header + maxY - minY + CANVAS_PAD * 2),
  );
  const ox = CANVAS_PAD - minX;
  const oy = header + CANVAS_PAD - minY;

  const clipDefs: string[] = [];
  const nodeSvg: string[] = [];
  boxes.forEach((box, index) => {
    const clipId = `clip-${index}`;
    clipDefs.push(
      `<clipPath id="${clipId}"><rect x="${n(box.x + BORDER)}" y="${n(box.y + BORDER)}" width="${n(box.w - 2 * BORDER)}" height="${n(box.h - 2 * BORDER)}"/></clipPath>`,
    );
    const body =
      box.node.kind === "sticky"
        ? renderSticky(box, clipId, colors)
        : renderEvidence(box, clipId, colors);
    nodeSvg.push(
      box.node.dimmed ? `<g opacity="0.42">\n  ${body}\n  </g>` : body,
    );
  });

  const marker = (kind: ExportEdge["kind"]) => {
    const color = edgeStyle(kind, colors).stroke;
    return `<marker id="arrow-${kind}" viewBox="-10 -10 20 20" markerWidth="15" markerHeight="15" refX="0" refY="0" orient="auto-start-reverse"><polyline points="-5,-4 0,0 -5,4 -5,-4" fill="${color}" stroke="${color}" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"/></marker>`;
  };

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <defs>
    ${(["normal", "opposing", "cite", "user"] as const).map(marker).join("\n    ")}
    <pattern id="board-dots" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="13" cy="13" r="0.7" fill="rgba(0,0,0,0.12)"/></pattern>
  </defs>
  <rect width="100%" height="100%" fill="${colors.paperRaised}"/>
  <rect width="100%" height="100%" fill="url(#board-dots)"/>
  ${titleLines
    .map(
      (line, index) =>
        `<text x="${CANVAS_PAD}" y="${n(16 + index * TITLE_LH + TITLE_LH / 2)}" dominant-baseline="central" font-size="${TITLE_SIZE}" font-weight="600" fill="${colors.ink1}" font-family="${SANS}">${escapeXml(line)}</text>`,
    )
    .join("\n  ")}
  <g transform="translate(${n(ox)} ${n(oy)})">
  <defs>
    ${clipDefs.join("\n    ")}
  </defs>
  ${edgeSvg.join("\n  ")}
  ${nodeSvg.join("\n  ")}
  </g>
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
    schema: "poliscope.evidence_map_export.v2",
    evidence_graph: graph,
    researcher_overlay: overlay,
    note: "researcher_overlay 是研究者的私人备注、便签、连线与布局，不是正式证据；正式证据以 evidence_graph 为准。",
  };
  download(
    new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
    `poliscope-evidence-map-${stamp()}.json`,
  );
}

/* Browsers refuse canvases past ~16k px a side or ~268M px in area; a large
 * map is exported at a lower scale rather than as a blank image. */
const MAX_CANVAS_SIDE = 16_000;
const MAX_CANVAS_AREA = 200_000_000;

/** Rasterise the self-contained SVG at up to 2x for crisp PNG export.
 *
 * SVG-as-image is parsed as strict XML: any malformed attribute fires a silent
 * ``onerror`` and the old code did nothing (the export "looked broken"). This
 * rasteriser therefore (1) uses naturalWidth/Height, (2) falls back from a blob
 * URL to a data URL once, and (3) surfaces a console error instead of a no-op.
 */
export function exportPngFile(
  nodes: ExportNode[],
  edges: ExportEdge[],
  title: string,
): void {
  const svg = buildSvg(nodes, edges, title);
  const rasterise = (url: string, revokeUrl: () => void, retried: boolean) => {
    const image = new Image();
    image.onload = () => {
      const width = image.naturalWidth || image.width;
      const height = image.naturalHeight || image.height;
      if (!width || !height) {
        revokeUrl();
        console.error("PNG export: SVG reported zero intrinsic size");
        return;
      }
      const scale = Math.min(
        2,
        MAX_CANVAS_SIDE / width,
        MAX_CANVAS_SIDE / height,
        Math.sqrt(MAX_CANVAS_AREA / (width * height)),
      );
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(width * scale);
      canvas.height = Math.floor(height * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        revokeUrl();
        console.error("PNG export: 2D canvas context unavailable");
        return;
      }
      ctx.scale(scale, scale);
      ctx.drawImage(image, 0, 0, width, height);
      revokeUrl();
      canvas.toBlob((png) => {
        if (png) {
          download(png, `poliscope-evidence-map-${stamp()}.png`);
        } else {
          console.error("PNG export: canvas.toBlob returned null");
        }
      }, "image/png");
    };
    image.onerror = () => {
      revokeUrl();
      // One data-URL retry: a few hardened browser setups refuse blob: SVG
      // images while accepting the equivalent data: URL.
      if (!retried) {
        const dataUrl =
          "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
        rasterise(dataUrl, () => undefined, true);
        return;
      }
      console.error("PNG export: SVG failed to decode as an image");
    };
    image.src = url;
  };
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const blobUrl = URL.createObjectURL(blob);
  rasterise(blobUrl, () => URL.revokeObjectURL(blobUrl), false);
}
