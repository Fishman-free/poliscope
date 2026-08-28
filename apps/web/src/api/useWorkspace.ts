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
  // The latest refresh's controller. A newer refresh() aborts it, so a stale
  // fetch never lands its snapshot over a fresher one.
  const controllerRef = useRef<AbortController | null>(null);
  const timer = useRef<number | undefined>(undefined);
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

  // Initial load, and a fresh load whenever the task changes.
  useEffect(() => {
    void refresh();
    return () => controllerRef.current?.abort();
  }, [refresh]);

  useEffect(() => {
    if (!taskId) {
      setEvents([]);
      return;
    }
    // Terminal: keep the collected events on screen (audit trail), but do not
    // subscribe -- the cleanup below already closed the stream when the
    // status flipped. A re-research flips the status back out of the set and
    // this effect re-subscribes: a fresh EventSource replays the ledger from
    // sequence 0, which refills the just-cleared list.
    if (terminal) return;
    setEvents([]);
    const close = subscribe(
      taskId,
      (event) => {
        setEvents((current) => [...current, event]);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void refresh(), REFRESH_DEBOUNCE_MS);
      },
      setStream,
    );
    return () => {
      window.clearTimeout(timer.current);
      close();
      setStream("closed");
    };
  }, [taskId, refresh, terminal]);

  // Process trace: reconnect replays the newest rows (bounded server-side)
  // and deduplicates by server seq. Incoming frames are buffered and flushed
  // at most every PROCESS_FLUSH_MS so a replay burst produces a few renders
  // instead of one per frame, and the kept array is capped at
  // PROCESS_EVENT_CAP -- the combination is what keeps the tab responsive
  // after a long background period (the old per-frame O(N) path froze it).
  useEffect(() => {
    if (!taskId) {
      setProcessEvents([]);
      return;
    }
    if (terminal) return;
    setProcessEvents([]);
    const seen = new Set<number>();
    const buffer: ProcessEvent[] = [];
    let flushTimer = 0;
    const flush = () => {
      flushTimer = 0;
      if (buffer.length === 0) return;
      const batch = buffer.splice(0, buffer.length);
      setProcessEvents((current) => {
        const next = [...current, ...batch];
        return next.length > PROCESS_EVENT_CAP
          ? next.slice(next.length - PROCESS_EVENT_CAP)
          : next;
      });
    };
    const close = subscribeProcess(taskId, (event) => {
      if (seen.has(event.seq)) return;
      seen.add(event.seq);
      buffer.push(event);
      if (flushTimer === 0) {
        flushTimer = window.setTimeout(flush, PROCESS_FLUSH_MS);
      }
    });
    return () => {
      close();
      if (flushTimer !== 0) window.clearTimeout(flushTimer);
    };
  }, [taskId, terminal]);

  return { snapshot, load, stream, error, events, processEvents, refresh };
}
