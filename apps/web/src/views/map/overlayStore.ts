/** Researcher map overlay: private notes, sticky notes and saved layout.
 *
 * AGENTS.md 5.3: only the Graph Projector writes the formal Evidence Graph.
 * Everything the researcher creates HERE is a *personal workspace overlay* --
 * it never leaves the browser, never enters the Scientific Event Ledger, and
 * is visually marked as the researcher's own material rather than evidence.
 * Persistence is per task in localStorage so a re-opened session restores the
 * researcher's arrangement; storage failures degrade to an in-memory overlay
 * instead of breaking the map.
 */

export interface StickyNote {
  id: string;
  x: number;
  y: number;
  text: string;
  /** Pale paper tones; never an evidential colour (those mean trust state). */
  color: "amber" | "blue" | "green";
}

export interface MapOverlay {
  /** node id -> researcher's private note shown under the node detail. */
  notes: Record<string, string>;
  /** Free-floating researcher sticky notes on the canvas. */
  stickies: StickyNote[];
  /** node id -> manually arranged position (user drag). */
  positions: Record<string, { x: number; y: number }>;
}

export function emptyOverlay(): MapOverlay {
  return { notes: {}, stickies: [], positions: {} };
}

const KEY_PREFIX = "poliscope_map_overlay_v1:";

export function loadOverlay(taskId: string): MapOverlay {
  if (!taskId) return emptyOverlay();
  try {
    const raw = window.localStorage.getItem(KEY_PREFIX + taskId);
    if (!raw) return emptyOverlay();
    const parsed = JSON.parse(raw) as Partial<MapOverlay>;
    return {
      notes:
        parsed.notes && typeof parsed.notes === "object" ? parsed.notes : {},
      stickies: Array.isArray(parsed.stickies) ? parsed.stickies : [],
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

let stickySeq = 0;

/** A readable-enough local id; stickies never reach the server, so a counter
 * plus time is enough and keeps exported JSON tidy. */
export function newStickyId(): string {
  stickySeq += 1;
  return `note-${Date.now().toString(36)}-${stickySeq}`;
}
