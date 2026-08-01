/** The only place that talks to the API.
 *
 * Errors are values, not thrown strings: a failed fetch has to be renderable,
 * because "the workspace could not be loaded" is information the researcher
 * needs and a blank panel is not. CLAUDE.md 7 asks the system to admit what it
 * does not know, and that applies to the client too.
 */

import type { LedgerEvent, WorkspaceSnapshot } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      signal,
      headers: { accept: "application/json" },
    });
  } catch (cause) {
    throw new ApiError(0, `无法连接 API：${String(cause)}`);
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as T;
}

export function fetchWorkspace(
  taskId: string,
  signal?: AbortSignal,
): Promise<WorkspaceSnapshot> {
  return getJson<WorkspaceSnapshot>(`/api/workspace/${taskId}`, signal);
}

export async function fetchReportMarkdown(taskId: string): Promise<string> {
  const response = await fetch(`${BASE}/api/reports/${taskId}?format=markdown`);
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return response.text();
}

/** Subscribe to the task's event stream.
 *
 * The browser's EventSource resends `Last-Event-ID` on reconnect by itself, and
 * the server replays from the ledger sequence, so a dropped connection loses
 * nothing. That is the whole reason the stream is backed by the ledger rather
 * than by an in-process queue -- see apps/api/routers/stream.py.
 */
export function subscribe(
  taskId: string,
  onEvent: (event: LedgerEvent) => void,
  onStateChange?: (state: "open" | "reconnecting") => void,
): () => void {
  const source = new EventSource(`${BASE}/api/stream/${taskId}`);
  source.onopen = () => onStateChange?.("open");
  source.onerror = () => onStateChange?.("reconnecting");
  // One handler for every frame. The server sends no `event:` line precisely so
  // that this cannot become a list of kinds the client happens to know about --
  // an audit trail that omits unfamiliar events without saying so is worse than
  // no audit trail.
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as LedgerEvent);
    } catch {
      // An unparseable frame is skipped rather than crashing the view; the next
      // snapshot refresh is authoritative anyway.
    }
  };
  return () => source.close();
}
