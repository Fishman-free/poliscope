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
  /** Process trace (live view): token/reasoning deltas, tool calls, seat
   * turns, ordered by server seq. Not replay-guaranteed; deduplicated here. */
  processEvents: ProcessEvent[];
  refresh: () => void;
}

export function useWorkspace(taskId: string | null): WorkspaceState {
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [load, setLoad] = useState<LoadState>("idle");
  const [stream, setStream] = useState<StreamState>("closed");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [processEvents, setProcessEvents] = useState<ProcessEvent[]>([]);
  const [nonce, setNonce] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (!taskId) {
      setSnapshot(null);
      setLoad("idle");
      return;
    }
    const controller = new AbortController();
    setLoad((current) => (current === "ready" ? current : "loading"));
    fetchWorkspace(taskId, controller.signal)
      .then((next) => {
        setSnapshot(next);
        setError(null);
        setLoad("ready");
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof ApiError ? cause.message : String(cause),
        );
        setLoad("error");
      });
    return () => controller.abort();
  }, [taskId, nonce]);

  useEffect(() => {
    if (!taskId) return;
    setEvents([]);
    const close = subscribe(
      taskId,
      (event) => {
        setEvents((current) => [...current, event]);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(refresh, REFRESH_DEBOUNCE_MS);
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
