import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, fetchWorkspace, subscribe, subscribeProcess } from "./client";
import type { LedgerEvent, ProcessEvent, WorkspaceSnapshot } from "./types";

export type LoadState = "idle" | "loading" | "ready" | "error";
export type StreamState = "open" | "reconnecting" | "closed";

/** How long to sit on a burst of events before re-reading the snapshot.
 *
 * A round emits a dozen events in a few milliseconds. Refetching per event
 * would hammer the API and, worse, render a half-projected graph. Coalescing
 * means the map only ever shows a state the server actually committed. */
const REFRESH_DEBOUNCE_MS = 250;

/** Insurance poll while a task is still live and the tab is visible.
 *
 * SSE is the fast path, but a backgrounded browser throttles timers and an
 * idle proxy can silently kill an EventSource; this slow poll bounds how
 * stale a returned-to tab can be even when no frame ever arrives. It pauses
 * while the tab is hidden (background-tab responsiveness fix). */
const INSURANCE_POLL_MS = 15_000;

/** Task statuses after which no further event can arrive. Both subscriptions
 * close when the snapshot reaches one of these and stay closed; they re-open
 * on their own if a re-research moves the status back out of the set. */
const TERMINAL_STATUSES = new Set([
  "COMPLETED",
  "COMPLETED_WITH_GAPS",
  "FAILED",
  "CANCELLED",
]);

/** Process events kept in memory. The live view only reads the recent slice
 * (each seat's thinking slice resets on the next seat_deliberation, the tool
 * cards scroll), so an unbounded array only costs memory and re-render time:
 * the per-event O(N) aggregation over a whole run's token stream is what
 * froze the tab when a reconnect replayed the trace (background-tab
 * unresponsive fix). */
const PROCESS_EVENT_CAP = 5000;

/** High-volume, droppable process kinds (token deltas and heartbeats). The
 * sparse structural kinds (seat_deliberation/model_done/seat_absent/
 * tool_call/tool_result) are what open/close seat and tool cards: when the
 * in-memory window trims to the newest PROCESS_EVENT_CAP rows, those anchors
 * must be retained regardless of age, otherwise still-running seats' token
 * deltas evict already-finished seats' deliberation anchors and their cards
 * collapse to (or stay on) the empty fallback -- the production bug where
 * only one scientist remained after a background-tab thaw. */
const HEAVY_PROCESS_KINDS = new Set([
  "model_reasoning",
  "model_token",
  "seat_working",
]);

/** Keep the newest ``PROCESS_EVENT_CAP`` rows wholesale, plus every structural
 * anchor older than that window. Input must be seq-sorted; output stays
 * sorted. Structural volume is bounded (a handful per seat per phase). */
function capProcessEvents(events: ProcessEvent[]): ProcessEvent[] {
  if (events.length <= PROCESS_EVENT_CAP) return events;
  const tail = events.slice(events.length - PROCESS_EVENT_CAP);
  const older = events
    .slice(0, events.length - PROCESS_EVENT_CAP)
    .filter((event) => !HEAVY_PROCESS_KINDS.has(event.kind));
  return [...older, ...tail];
}

/** Flush cadence for process events: incoming frames collect in a buffer and
 * land in state at most every 250 ms, so a thawing tab's burst of thousands
 * of replayed frames produces a handful of renders instead of one render per
 * frame. */
const PROCESS_FLUSH_MS = 250;

export interface WorkspaceState {
  snapshot: WorkspaceSnapshot | null;
  load: LoadState;
  stream: StreamState;
  error: string | null;
  /** Events seen on the wire this session, newest last. The audit trail reads
   * this; it is never used to mutate the snapshot, because the projector -- not
   * the browser -- decides what an event means. */
  events: LedgerEvent[];
  /** Process trace (live view): tool calls, seat turns, status heartbeats,
   * ordered by server seq. Not replay-guaranteed; deduplicated here. */
  processEvents: ProcessEvent[];
  /** Re-read the snapshot from the server.
   *
   * Resolves `true` on success and `false` on failure (never throws, so call
   * sites that do not await -- e.g. `onSubmitted={refresh}` -- never produce an
   * unhandled rejection); the insurance poll awaits it to back off on
   * consecutive failures. Only the most recent call owns the network fetch: an
   * older in-flight one is aborted and settles without touching the snapshot,
   * so two refreshes can never run concurrently and overwrite each other. */
  refresh: () => Promise<boolean>;
}

export function useWorkspace(taskId: string | null): WorkspaceState {
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [load, setLoad] = useState<LoadState>("idle");
  const [stream, setStream] = useState<StreamState>("closed");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [processEvents, setProcessEvents] = useState<ProcessEvent[]>([]);
  // Bumped when a stream reports it has permanently closed, or when the tab
  // becomes visible again / network returns: it forces both EventSources to
  // be rebuilt instead of leaving a returned-to tab on a dead connection.
  const [streamNonce, setStreamNonce] = useState(0);
  // The latest refresh's controller. A newer refresh() aborts it, so a stale
  // fetch never lands its snapshot over a fresher one.
  const controllerRef = useRef<AbortController | null>(null);
  const timer = useRef<number | undefined>(undefined);
  // Throttle stream rebuilds: an unreachable server must not turn CLOSED ->
  // rebuild -> CLOSED into a tight request loop.
  const lastRebuildRef = useRef(0);
  const rebuildStream = useCallback(() => {
    const now = Date.now();
    if (now - lastRebuildRef.current < 3_000) return;
    lastRebuildRef.current = now;
    setStreamNonce((current) => current + 1);
  }, []);
  // Terminal snapshot: both streams close (and stay closed) once the task
  // cannot emit anything more; a re-research flips this back to false.
  const terminal =
    snapshot !== null && TERMINAL_STATUSES.has(snapshot.task.status);

  const refresh = useCallback((): Promise<boolean> => {
    if (!taskId) {
      setSnapshot(null);
      setLoad("idle");
      return Promise.resolve(true);
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoad((current) => (current === "ready" ? current : "loading"));
    return fetchWorkspace(taskId, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return true;
        setSnapshot(next);
        setError(null);
        setLoad("ready");
        return true;
      })
      .catch((cause: unknown) => {
        // An aborted fetch was superseded by a newer refresh; that one owns
        // the outcome, so settle silently instead of double-reporting.
        if (controller.signal.aborted) return true;
        setError(cause instanceof ApiError ? cause.message : String(cause));
        setLoad("error");
        return false;
      });
  }, [taskId]);

  // Switching sessions must feel instant: drop the previous task's snapshot
  // immediately (otherwise the old workspace stays on screen while the new
  // one loads, which reads as a frozen/long-waiting history open) and show
  // the loading branch until the first snapshot of the new task arrives.
  useEffect(() => {
    setSnapshot(null);
    setEvents([]);
    setProcessEvents([]);
    setError(null);
    setLoad(taskId ? "loading" : "idle");
  }, [taskId]);

  // Initial load, and a fresh load whenever the task changes.
  useEffect(() => {
    void refresh();
    return () => controllerRef.current?.abort();
  }, [refresh]);

  // Returning from a background tab (or the network coming back) must heal
  // itself immediately: pull the committed snapshot and rebuild any stream
  // that the browser/proxy silently killed while hidden. Without this, a tab
  // left in the background came back to a stale map and "unresponsive"
  // errors until the next event happened to arrive.
  useEffect(() => {
    if (!taskId) return;
    const recover = () => {
      void refresh();
      rebuildStream();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") recover();
    };
    window.addEventListener("online", recover);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("online", recover);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [taskId, refresh, rebuildStream]);

  // Slow insurance poll while live and visible. Hidden tabs are skipped --
  // polling a throttled background tab is exactly what used to pile up
  // requests and stall the return -- and the next visibility recovery heals
  // them instead.
  useEffect(() => {
    if (!taskId || terminal) return;
    const interval = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, INSURANCE_POLL_MS);
    return () => window.clearInterval(interval);
  }, [taskId, terminal, refresh]);

  useEffect(() => {
    if (!taskId) {
      setEvents([]);
      return;
    }
    // Terminal: keep the collected events on screen (audit trail), but do not
    // subscribe -- the cleanup below already closed the stream when the
    // status flipped. A re-research flips the status back out of the set and
    // this effect re-subscribes. Same-task resubscription (self-heal rebuild,
    // checkpoint re-research) KEEPS the events it already has: a fresh
    // EventSource replays the ledger from sequence 0, and overlap is merged
    // by workspace_version instead of first blanking the phase timeline and
    // audit trail (only a real task switch clears, in the effect above).
    if (terminal) return;
    const close = subscribe(
      taskId,
      (event) => {
        setEvents((current) =>
          current.some((entry) => entry.workspace_version === event.workspace_version)
            ? current
            : [...current, event].sort(
                (a, b) => a.workspace_version - b.workspace_version,
              ),
        );
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void refresh(), REFRESH_DEBOUNCE_MS);
      },
      (state) => {
        setStream(state);
        // A permanently closed stream rebuilds itself via streamNonce rather
        // than leaving the workspace on a dead EventSource forever.
        if (state === "closed") rebuildStream();
      },
    );
    return () => {
      window.clearTimeout(timer.current);
      close();
      setStream("closed");
    };
  }, [taskId, refresh, terminal, streamNonce, rebuildStream]);

  // Process trace: reconnect replays the newest rows (bounded server-side)
  // and deduplicates by server seq. Incoming frames are buffered and flushed
  // at most every PROCESS_FLUSH_MS so a replay burst produces a few renders
  // instead of one per frame, and the kept array is capped at
  // PROCESS_EVENT_CAP -- the combination is what keeps the tab responsive
  // after a long background period (the old per-frame O(N) path froze it).
  useEffect(() => {
    if (!taskId) return;
    if (terminal) return;
    // Same-task resubscription (a self-heal streamNonce rebuild, or a
    // checkpoint re-research flipping terminal back to QUEUED) must NOT wipe
    // the trace it already holds: the replay tail is bounded and can briefly
    // contain no seat_deliberation, and a wiped trace collapsed the whole
    // seven-seat live view to "no seats running" until the next model call
    // -- the production "断点续研后科学家输出全部消失" freeze. Clearing on a
    // real task switch belongs to the taskId effect above; here replayed
    // frames are merged by server seq and kept sorted.
    const seen = new Set<number>();
    const buffer: ProcessEvent[] = [];
    let flushTimer = 0;
    const flush = () => {
      flushTimer = 0;
      if (buffer.length === 0) return;
      const batch = buffer.splice(0, buffer.length);
      setProcessEvents((current) => {
        const known = new Set(current.map((entry) => entry.seq));
        const fresh = batch.filter((entry) => !known.has(entry.seq));
        if (fresh.length === 0) return current;
        const next = [...current, ...fresh].sort((a, b) => a.seq - b.seq);
        return capProcessEvents(next);
      });
    };
    const close = subscribeProcess(
      taskId,
      (event) => {
        if (seen.has(event.seq)) return;
        seen.add(event.seq);
        buffer.push(event);
        if (flushTimer === 0) {
          flushTimer = window.setTimeout(flush, PROCESS_FLUSH_MS);
        }
      },
      (state) => {
        if (state === "closed") rebuildStream();
      },
    );
    return () => {
      close();
      if (flushTimer !== 0) window.clearTimeout(flushTimer);
    };
  }, [taskId, terminal, streamNonce, rebuildStream]);

  return { snapshot, load, stream, error, events, processEvents, refresh };
}
