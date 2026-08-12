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
    if (!taskId) return;
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
  }, [taskId, refresh]);

  // Process trace: reconnect replays from the start (deliberately not
  // Last-Event-ID resume), so deduplicate by server seq.
  useEffect(() => {
    if (!taskId) {
      setProcessEvents([]);
      return;
    }
    const seen = new Set<number>();
    const close = subscribeProcess(taskId, (event) => {
      if (seen.has(event.seq)) return;
      seen.add(event.seq);
      setProcessEvents((current) => [...current, event]);
    });
    return () => close();
  }, [taskId]);

  return { snapshot, load, stream, error, events, processEvents, refresh };
}
