/** Researcher map overlay: private notes, sticky notes, connectors and layout.
 *
 * AGENTS.md 5.3: only the Graph Projector writes the formal Evidence Graph.
 * Everything the researcher creates HERE is a *personal workspace overlay* --
 * it never leaves the browser, never enters the Scientific Event Ledger, and
 * is visually marked as the researcher's own material rather than evidence.
 * Persistence is per task in localStorage so a re-opened session restores the
 * researcher's arrangement; storage failures degrade to an in-memory overlay
 * instead of breaking the map.
 */

/** Pale paper fills for sticky notes. Every fill is intentionally light so
 * dark note text stays the most readable thing on the note. */
export const STICKY_COLORS = [
  "yellow",
  "pink",
  "blue",
  "green",
  "purple",
  "orange",
] as const;
export type StickyColor = (typeof STICKY_COLORS)[number];

/** Dark, high-contrast text colours offered on top of the pale fills. */
export const STICKY_TEXT_COLORS = ["ink", "red", "blue", "green"] as const;
export type StickyTextColor = (typeof STICKY_TEXT_COLORS)[number];

/** Font families the researcher can switch a note to. */
export const STICKY_FONTS = ["sans", "serif", "mono"] as const;
export type StickyFont = (typeof STICKY_FONTS)[number];

export interface StickyNote {
  id: string;
  x: number;
  y: number;
  text: string;
  color: StickyColor;
  textColor: StickyTextColor;
  font: StickyFont;
}

/** A connector the researcher drew between any two blocks (formal nodes or
 * sticky notes). It is part of the personal overlay, never a formal edge. */
export interface UserConnector {
  id: string;
  source: string;
  target: string;
}

export interface MapOverlay {
  /** node id -> researcher's private note shown under the node detail. */
  notes: Record<string, string>;
  /** Free-floating researcher sticky notes on the canvas. */
  stickies: StickyNote[];
  /** Researcher-drawn arrows between blocks. */
  connectors: UserConnector[];
  /** node id -> manually arranged position (user drag). */
  positions: Record<string, { x: number; y: number }>;
}

export function emptyOverlay(): MapOverlay {
  return { notes: {}, stickies: [], connectors: [], positions: {} };
}

const KEY_PREFIX = "poliscope_map_overlay_v1:";

/** Normalise one sticky note loaded from storage, filling fields older
 * overlays never saved (and mapping the old 3-colour palette onto the new
 * one). Returns null for entries that are not objects. */
function normalizeSticky(raw: unknown): StickyNote | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.id !== "string") return null;
  const legacyColor =
    typeof value.color === "string" ? value.color : "yellow";
  const color: StickyColor = (STICKY_COLORS as readonly string[]).includes(
    legacyColor,
  )
    ? (legacyColor as StickyColor)
    : legacyColor === "amber"
      ? "yellow"
      : "yellow";
  const rawTextColor =
    typeof value.textColor === "string" ? value.textColor : "ink";
  const textColor: StickyTextColor = (
    STICKY_TEXT_COLORS as readonly string[]
  ).includes(rawTextColor)
    ? (rawTextColor as StickyTextColor)
    : "ink";
  const rawFont = typeof value.font === "string" ? value.font : "sans";
  const font: StickyFont = (STICKY_FONTS as readonly string[]).includes(rawFont)
    ? (rawFont as StickyFont)
    : "sans";
  return {
    id: value.id,
    x: typeof value.x === "number" ? value.x : 72,
    y: typeof value.y === "number" ? value.y : 72,
    text: typeof value.text === "string" ? value.text : "",
    color,
    textColor,
    font,
  };
}

function normalizeConnector(raw: unknown): UserConnector | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (
    typeof value.id !== "string" ||
    typeof value.source !== "string" ||
    typeof value.target !== "string"
  ) {
    return null;
  }
  return { id: value.id, source: value.source, target: value.target };
}

export function loadOverlay(taskId: string): MapOverlay {
  if (!taskId) return emptyOverlay();
  try {
    const raw = window.localStorage.getItem(KEY_PREFIX + taskId);
    if (!raw) return emptyOverlay();
    const parsed = JSON.parse(raw) as Partial<MapOverlay>;
    return {
      notes:
        parsed.notes && typeof parsed.notes === "object" ? parsed.notes : {},
      stickies: Array.isArray(parsed.stickies)
        ? parsed.stickies
            .map(normalizeSticky)
            .filter((note): note is StickyNote => note !== null)
        : [],
      connectors: Array.isArray(parsed.connectors)
        ? parsed.connectors
            .map(normalizeConnector)
            .filter((item): item is UserConnector => item !== null)
        : [],
      positions:
        parsed.positions && typeof parsed.positions === "object"
          ? parsed.positions
          : {},
    };
  } catch {
    return emptyOverlay();
  }
}

export function saveOverlay(taskId: string, overlay: MapOverlay): void {
  if (!taskId) return;
  try {
    window.localStorage.setItem(
      KEY_PREFIX + taskId,
      JSON.stringify(overlay),
    );
  } catch {
    // Quota / private mode: the overlay still works for this session.
  }
}

let overlaySeq = 0;

/** A readable-enough local id; overlay objects never reach the server, so a
 * counter plus time is enough and keeps exported JSON tidy. */
function localId(prefix: string): string {
  overlaySeq += 1;
  return `${prefix}-${Date.now().toString(36)}-${overlaySeq}`;
}

export function newStickyId(): string {
  return localId("note");
}

export function newConnectorId(): string {
  return localId("conn");
}
