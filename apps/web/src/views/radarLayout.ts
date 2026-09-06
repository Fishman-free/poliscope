/** Deterministic de-overlap layout for the Blindspot Radar scatter plot.
 *
 * The radar encodes impact on the x axis and investigability on the y axis;
 * when the council scores several blindspots almost identically, their
 * markers stack on top of one another and the plot reads as one blob. This
 * module spreads those markers out with the smallest possible displacement:
 *
 * - a weak spring always pulls a marker back to its TRUE (scored) position;
 * - pair repulsion only kicks in when two markers actually overlap;
 * - markers are clamped inside the plot rectangle.
 *
 * The relaxation is fully deterministic (coincident pairs separate along a
 * direction derived from their ids, never Math.random), so a streaming SSE
 * refresh never shuffles the dots around. The view renders a faint ghost dot
 * plus a leader line for every marker that was nudged, keeping the chart
 * honest: the score is still at the ghost, the visible marker is only moved
 * so the reader can click it.
 */

export interface RadarDatum {
  id: string;
  /** True plot coordinates derived from the scores. */
  x: number;
  y: number;
  /** Marker radius in SVG units (encodes uncertainty). */
  r: number;
}

export interface LaidRadarDatum extends RadarDatum {
  /** Display coordinates after de-overlap. */
  dx: number;
  dy: number;
  moved: boolean;
}

export interface RadarBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** Small, stable string hash (FNV-1a style) -> [0, 1). */
function hash01(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  // >>> 0 makes it an unsigned 32-bit int before dividing into [0, 1).
  return (hash >>> 0) / 4294967296;
}

/** Minimum visual gap between two marker edges, in SVG units. */
const EDGE_GAP = 5;
/** Spring back toward the true position: pos += (true - pos) * SPRING. */
const SPRING = 0.08;
/** Position-based relaxation iterations. The point counts here are small
 * (a task rarely has more than a few dozen blindspots), so a fixed high
 * iteration count is cheap and converges to a stable arrangement. */
const ITERATIONS = 320;
/** A nudge smaller than this many SVG units is treated as "not moved". */
const MOVE_EPSILON = 1.5;

interface MutablePoint {
  x: number;
  y: number;
}

export function deoverlapRadarPoints(
  data: RadarDatum[],
  bounds: RadarBounds,
): LaidRadarDatum[] {
  const positions: MutablePoint[] = data.map((point) => ({
    x: point.x,
    y: point.y,
  }));
  const maxR = data.reduce((acc, point) => Math.max(acc, point.r), 0);
  const minX = bounds.minX + maxR;
  const maxX = bounds.maxX - maxR;
  const minY = bounds.minY + maxR;
  const maxY = bounds.maxY - maxR;

  for (let iteration = 0; iteration < ITERATIONS; iteration += 1) {
    // 1. Pair repulsion: separate overlapping markers along their connecting
    //    vector. Exactly coincident markers use a deterministic id-derived
    //    direction so they do not stay glued together forever.
    for (let i = 0; i < positions.length; i += 1) {
      for (let j = i + 1; j < positions.length; j += 1) {
        const a = positions[i] as MutablePoint;
        const b = positions[j] as MutablePoint;
        const datumA = data[i] as RadarDatum;
        const datumB = data[j] as RadarDatum;
        const wanted = datumA.r + datumB.r + EDGE_GAP;
        let vx = b.x - a.x;
        let vy = b.y - a.y;
        let distance = Math.hypot(vx, vy);
        if (distance >= wanted) continue;
        if (distance < 1e-6) {
          // Deterministic break of the tie: angle from the pair's ids.
          const angle =
            hash01(`${datumA.id}|${datumB.r}|${iteration}`) * Math.PI * 2;
          vx = Math.cos(angle);
          vy = Math.sin(angle);
          distance = 1e-6;
        }
        const push = (wanted - distance) / 2;
        const ux = vx / distance;
        const uy = vy / distance;
        a.x -= ux * push;
        a.y -= uy * push;
        b.x += ux * push;
        b.y += uy * push;
      }
    }
    // 2. Spring back to the scored position, then clamp inside the plot. The
    //    spring is what keeps displacement minimal and preserves the visual
    //    ordering the scores imply.
    for (let i = 0; i < positions.length; i += 1) {
      const position = positions[i] as MutablePoint;
      const datum = data[i] as RadarDatum;
      position.x += (datum.x - position.x) * SPRING;
      position.y += (datum.y - position.y) * SPRING;
      position.x = Math.min(maxX, Math.max(minX, position.x));
      position.y = Math.min(maxY, Math.max(minY, position.y));
    }
  }

  return data.map((point, index) => {
    const position = positions[index] as MutablePoint;
    const movedDistance = Math.hypot(
      position.x - point.x,
      position.y - point.y,
    );
    return {
      ...point,
      dx: position.x,
      dy: position.y,
      moved: movedDistance > MOVE_EPSILON,
    };
  });
}
