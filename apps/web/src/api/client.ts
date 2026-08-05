/** The only place that talks to the API.
 *
 * Errors are values, not thrown strings: a failed fetch has to be renderable,
 * because "the workspace could not be loaded" is information the researcher
 * needs and a blank panel is not. CLAUDE.md 7 asks the system to admit what it
 * does not know, and that applies to the client too.
 */

import type {
  ConfirmClaimsResult,
  CreateTaskResult,
  KnowledgeBaseDetail,
  KnowledgeBaseSummary,
  KnowledgeDocumentDetail,
  KnowledgeDocumentSummary,
  LedgerEvent,
  UploadedPaper,
  WorkspaceSnapshot,
} from "./types";

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

/** Shared with `getJson`'s error handling on purpose -- a failed POST is as
 * renderable a fact as a failed GET, not a thrown string (see file header). */
async function postJson<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
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

/** A researcher's own OpenAI-compatible endpoint for one task. `modelName`
 * is optional -- the deployment's configured model (or `deepseek-chat`) is
 * used when omitted. The api key is stored with the task and never echoed
 * back by any endpoint. */
export interface TaskModelConfig {
  baseUrl: string;
  apiKey: string;
  modelName?: string;
}

/** Everything a researcher can tune before a task starts, beyond the
 * question itself. Defaults mirror the values the Claude Code / Codex skill's
 * `scripts/new_contract.py` has already shipped with -- one set of sane
 * defaults, not two that could drift apart. */
export interface NewTaskOptions {
  populations?: string[];
  regions?: string[];
  languages?: string[];
  dateFrom?: string | null;
  dateUntil?: string;
  evidencePriorities?: string[];
  allowPreprints?: boolean;
  wallClockMinutes?: number;
  modelCostUsd?: string;
  toolCallLimit?: number;
  sourceLimit?: number;
  dois?: string[];
  bibtexEntries?: string[];
  modelConfig?: TaskModelConfig | null;
  /** Knowledge base whose documents the council should treat as Level A
   * user-provided sources for this task. Null (default) links none. */
  knowledgeBaseId?: string | null;
}

export const DEFAULT_NEW_TASK_OPTIONS: Required<
  Omit<NewTaskOptions, "dateFrom" | "dateUntil">
> = {
  populations: ["general population"],
  regions: ["global"],
  languages: ["en"],
  evidencePriorities: ["CORRELATION"],
  allowPreprints: false,
  wallClockMinutes: 60,
  modelCostUsd: "10.00",
  toolCallLimit: 50,
  sourceLimit: 20,
  dois: [],
  bibtexEntries: [],
  // Default: use the deployment's configured model gateway, not a per-task
  // endpoint. A researcher opting into their own endpoint overrides this.
  modelConfig: null,
  knowledgeBaseId: null,
};

/** Create a task from a plain question. The task does not start research --
 * it waits at `AWAITING_CLAIM_CONFIRMATION` until `confirmClaims` is called,
 * which is the control point CLAUDE.md 2 requires (see
 * apps/api/routers/tasks.py's module docstring). */
export function createTask(
  question: string,
  options: NewTaskOptions = {},
): Promise<CreateTaskResult> {
  const merged = { ...DEFAULT_NEW_TASK_OPTIONS, ...options };
  const model = merged.modelConfig;
  return postJson<CreateTaskResult>("/api/tasks", {
    question,
    scope: {
      populations: merged.populations,
      regions: merged.regions,
      languages: merged.languages,
      date_from: merged.dateFrom ?? null,
      date_until: merged.dateUntil ?? new Date().toISOString().slice(0, 10),
      evidence_priorities: merged.evidencePriorities,
      allow_preprints: merged.allowPreprints,
    },
    budget: {
      wall_clock_minutes: merged.wallClockMinutes,
      model_cost_usd: merged.modelCostUsd,
      tool_call_limit: merged.toolCallLimit,
      source_limit: merged.sourceLimit,
    },
    user_evidence: {
      dois: merged.dois,
      bibtex_entries: merged.bibtexEntries,
      pdf_object_ids: [],
    },
    knowledge_base_id: merged.knowledgeBaseId ?? null,
    task_model_config: model
      ? {
          base_url: model.baseUrl,
          api_key: model.apiKey,
          model_name: model.modelName?.trim() || null,
        }
      : null,
  });
}

/** Confirm which suggested claims the council should investigate, then queue
 * the task for a worker to claim. Two separate backend steps behind one call
 * here because the web form always confirms and queues in the same action --
 * unlike the CLI, which exposes them as two commands for scriptability. */
export function confirmClaims(
  taskId: string,
  claimIds: string[],
): Promise<ConfirmClaimsResult> {
  return postJson<ConfirmClaimsResult>(`/api/tasks/${taskId}/confirm-claims`, {
    claim_ids: claimIds,
  });
}

export function fetchWorkspace(
  taskId: string,
  signal?: AbortSignal,
): Promise<WorkspaceSnapshot> {
  return getJson<WorkspaceSnapshot>(`/api/workspace/${taskId}`, signal);
}

/** Upload a PDF to a task that already exists (the API requires a task id
 * before an object can reference it -- see apps/api/routers/papers.py's
 * module docstring for the recorded deviation that made this flow).
 *
 * The server validates magic bytes and size; the client sends the file as
 * multipart without setting a content-type (the browser adds the boundary).
 * The uploaded object id is returned for display only -- the task's stored
 * user_evidence is patched server-side. */
export async function uploadPaper(
  taskId: string,
  file: File,
): Promise<UploadedPaper> {
  const form = new FormData();
  form.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/tasks/${taskId}/papers/upload`, {
      method: "POST",
      body: form,
    });
  } catch (cause) {
    throw new ApiError(0, `无法上传文件：${String(cause)}`);
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as UploadedPaper;
}

export async function fetchReportMarkdown(taskId: string): Promise<string> {
  const response = await fetch(`${BASE}/api/reports/${taskId}?format=markdown`);
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return response.text();
}

/** Submit (or decline) the researcher's advisory steer at the
 * AWAITING_COUNCIL_INPUT checkpoint.
 *
 * ``guidanceText: ""`` is a first-class, deliberate "no intervention" answer,
 * not a missing one -- CLAUDE.md 4/8 requires that declining to steer read as
 * an honest choice, never a defaulted-away field. See
 * ``CouncilGuidanceRequest`` in apps/api/schemas.py and the CLI's identically
 * shaped ``council_guidance`` in apps/cli/client.py. */
export async function submitCouncilGuidance(
  taskId: string,
  guidanceText: string,
): Promise<{ task_id: string; status: string }> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/tasks/${taskId}/council-guidance`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ guidance_text: guidanceText }),
    });
  } catch (cause) {
    throw new ApiError(0, `无法连接 API：${String(cause)}`);
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as { task_id: string; status: string };
}

/** Knowledge-base management. The collection is the researcher's long-term
 * memory: documents uploaded once are parsed to text, kept across tasks, and
 * linkable to a task at creation (NewTaskOptions.knowledgeBaseId). */

export function fetchKnowledgeBases(): Promise<KnowledgeBaseSummary[]> {
  return getJson<KnowledgeBaseSummary[]>("/api/knowledge-bases");
}

export function createKnowledgeBase(
  name: string,
  description?: string,
): Promise<KnowledgeBaseSummary> {
  return postJson<KnowledgeBaseSummary>("/api/knowledge-bases", {
    name,
    description: description || null,
  });
}

export function fetchKnowledgeBase(
  kbId: string,
): Promise<KnowledgeBaseDetail> {
  return getJson<KnowledgeBaseDetail>(`/api/knowledge-bases/${kbId}`);
}

export async function uploadKnowledgeDocument(
  kbId: string,
  file: File,
): Promise<KnowledgeDocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/knowledge-bases/${kbId}/documents/upload`, {
      method: "POST",
      body: form,
    });
  } catch (cause) {
    throw new ApiError(0, `无法上传文档：${String(cause)}`);
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as KnowledgeDocumentSummary;
}

export function fetchKnowledgeDocumentText(
  kbId: string,
  docId: string,
): Promise<KnowledgeDocumentDetail> {
  return getJson<KnowledgeDocumentDetail>(
    `/api/knowledge-bases/${kbId}/documents/${docId}`,
  );
}

export async function deleteKnowledgeDocument(
  kbId: string,
  docId: string,
): Promise<void> {
  const response = await fetch(`${BASE}/api/knowledge-bases/${kbId}/documents/${docId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
}

export async function deleteKnowledgeBase(kbId: string): Promise<void> {
  const response = await fetch(`${BASE}/api/knowledge-bases/${kbId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
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
