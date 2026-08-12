/** The only place that talks to the API.
 *
 * Errors are values, not thrown strings: a failed fetch has to be renderable,
 * because "the workspace could not be loaded" is information the researcher
 * needs and a blank panel is not. CLAUDE.md 7 asks the system to admit what it
 * does not know, and that applies to the client too.
 */

import type {
  AuthSession,
  ConfirmClaimsResult,
  CreateTaskResult,
  FollowUpResult,
  KnowledgeBaseDetail,
  KnowledgeBaseSummary,
  KnowledgeDocumentDetail,
  KnowledgeDocumentSummary,
  LedgerEvent,
  MeInfo,
  ProcessEvent,
  ModelSettings,
  ModelSettingsUpdate,
  ModelTestResult,
  RegistrationRequest,
  SkillSummary,
  TaskSummary,
  UploadedPaper,
  VerificationResponse,
  WorkspaceSnapshot,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

/** Remember-me: the bearer token lives in localStorage so a logged-in machine
 * stays logged in for the token's 30-day lifetime (本机免登录). It is sent
 * on every request; a 401 means "re-login", never a silent retry. */
const TOKEN_KEY = "poliscope_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { authorization: `Bearer ${token}` } : {};
}

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
      headers: { accept: "application/json", ...authHeaders() },
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
  return sendJson<T>("POST", path, body);
}

/** PUT sibling of `postJson`, for idempotent saves (settings, skill toggles). */
async function putJson<T>(path: string, body: unknown): Promise<T> {
  return sendJson<T>("PUT", path, body);
}

async function sendJson<T>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        ...authHeaders(),
      },
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
 * is optional -- the deployment's configured model (or `deepseek-v4-flash`) is
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
  /** Explicit per-task model endpoint. Leave null (default) to inherit the
   * permanent model settings saved via the right-side settings panel -- the
   * server applies them when this is absent. */
  modelConfig?: TaskModelConfig | null;
  /** Knowledge base whose documents the council should treat as Level A
   * user-provided sources for this task. Null (default) links none. */
  knowledgeBaseId?: string | null;
  /** Skill ids to enable for this task; their downloaded SKILL.md texts are
   * injected into the council's prompts as non-evidence instructions. */
  skillIds?: string[];
  /** Task mode (round-7): "deep_research" (default) or "paper_review" --
   * the council critiques an uploaded paper instead of investigating a
   * controversy question. */
  taskType?: string;
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
  // Default: use the permanent settings (or the deployment's gateway when
  // none are saved). A researcher opting into a per-task endpoint overrides.
  modelConfig: null,
  knowledgeBaseId: null,
  skillIds: [],
  taskType: "deep_research",
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
      // The web form no longer collects DOIs/BibTeX (the knowledge base
      // covers the researcher's own evidence); these stay empty so the
      // backend contract shape is unchanged. The CLI still fills them.
      dois: [],
      bibtex_entries: [],
      pdf_object_ids: [],
    },
    knowledge_base_id: merged.knowledgeBaseId ?? null,
    skill_ids: merged.skillIds ?? [],
    task_type: merged.taskType ?? "deep_research",
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
      headers: authHeaders(),
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
  const response = await fetch(`${BASE}/api/reports/${taskId}?format=markdown`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return response.text();
}

export async function fetchPaperMarkdown(taskId: string): Promise<string> {
  const response = await fetch(
    `${BASE}/api/reports/${taskId}/paper?format=markdown`,
    { headers: authHeaders() },
  );
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
      headers: { "content-type": "application/json", ...authHeaders() },
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
      headers: authHeaders(),
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
    headers: authHeaders(),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
}

export async function deleteKnowledgeBase(kbId: string): Promise<void> {
  const response = await fetch(`${BASE}/api/knowledge-bases/${kbId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
}

/** Session history: every task, newest first, for the right-side panel.
 * Replaces the old "paste a task id" box -- the researcher's whole history
 * is one click away. */
export function fetchTasks(): Promise<TaskSummary[]> {
  return getJson<TaskSummary[]>("/api/tasks");
}

/** 「重新研究」: move a FAILED/CANCELLED task back to QUEUED so the worker
 * runs it again (round-8).
 *
 * Round-12 「重新研究模式」: ``mode`` decides where the re-run starts.
 * ``full`` clears the council checkpoint so the whole protocol re-runs from
 * PRECOMMITMENT; ``first_gap`` (default) restarts from the first unfinished
 * phase when the checkpoint records one, and falls back to a full restart
 * when there is no recorded gap. Same semantics for deep-research and
 * paper-review tasks.
 */
export function reResearch(
  taskId: string,
  mode: "full" | "first_gap" = "first_gap",
): Promise<{ task_id: string; status: string }> {
  return postJson<{ task_id: string; status: string }>(
    `/api/tasks/${taskId}/re-research`,
    { mode },
  );
}

/** 「从头研究」（round-13）: the only true restart. Re-running the same task
 * cannot start over -- its ledger idempotency keys derive from phase and
 * seat, so a fresh pass's events would be swallowed by the previous run's
 * rows. The server therefore creates a brand-new task (fresh ledger and
 * evidence graph) carrying over the question, scope, confirmed claims,
 * budget and model config; this returns the new task id to open. */
export function rerunFresh(
  taskId: string,
): Promise<{ task_id: string; status: string; source_task_id: string }> {
  return postJson<{ task_id: string; status: string; source_task_id: string }>(
    `/api/tasks/${taskId}/rerun-fresh`,
    {},
  );
}

/** 「继续研究」: move a PAUSED task back to QUEUED so a worker can claim it
 * again. The requeue path is proven idempotent server-side (already-run
 * phases replay as no-ops via stable idempotency keys), so resuming is safe. */
export function resumeTask(taskId: string): Promise<{ task_id: string; status: string }> {
  return postJson<{ task_id: string; status: string }>(
    `/api/tasks/${taskId}/resume`,
    {},
  );
}

/** 「补充提问」: ask a finished task a follow-up question; the answer is
 * grounded in the task's Research Brief and confirmed claims (round-9). */
export function followUp(taskId: string, question: string): Promise<FollowUpResult> {
  return postJson<FollowUpResult>(`/api/tasks/${taskId}/followup`, { question });
}

/** 「停止研究」(round-10): stop a running or queued task. A QUEUED/PAUSED
 * task flips straight to CANCELLED; a RUNNING task's stop is recorded server-
 * side and the worker halts it at the next phase boundary. Returns the status
 * the task will reach (CANCELLED for a stopable task). */
export function cancelTask(taskId: string): Promise<{ task_id: string; status: string }> {
  return postJson<{ task_id: string; status: string }>(
    `/api/tasks/${taskId}/cancel`,
    {},
  );
}

/** 「补充提问·流式」: stream a follow-up answer (round-10).
 *
 * Uses fetch with a reader (not EventSource) because the endpoint needs the
 * Authorization header, which EventSource cannot attach. Each SSE frame is
 * ``data: {"text": "<delta>"}``; the stream ends with ``data: [DONE]`` or an
 * ``event: error`` frame carrying ``{"detail": ...}``.
 *
 * @param onDelta called with each text delta as it arrives.
 * @param signal lets the caller abort the stream (e.g. a stop button).
 */
export async function followUpStream(
  taskId: string,
  question: string,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/tasks/${taskId}/followup/stream`, {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question }),
      signal,
    });
  } catch (cause) {
    throw new ApiError(0, `无法连接 API：${String(cause)}`);
  }
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames end with a blank line; split on it and process whole frames.
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        let event = "message";
        let raw = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) raw = line.slice(5).trim();
        }
        if (event === "error") {
          let detail = "模型流式回答失败";
          try {
            const payload = JSON.parse(raw) as { detail?: string };
            if (payload.detail) detail = payload.detail;
          } catch {
            // fall through with the default message
          }
          throw new ApiError(0, detail);
        }
        if (raw === "[DONE]") return;
        try {
          const payload = JSON.parse(raw) as { text?: string; detail?: string };
          if (payload.text) onDelta(payload.text);
        } catch {
          // A malformed frame is not worth failing the whole answer over.
        }
        sep = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Permanently delete a task and all its records (server confirms the task
 * is owned by the caller). The UI must confirm with the researcher before
 * calling -- this cannot be undone. */
export async function deleteTask(taskId: string): Promise<{ deleted: string }> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/tasks/${taskId}`, {
      method: "DELETE",
      headers: { accept: "application/json", ...authHeaders() },
    });
  } catch (cause) {
    throw new ApiError(0, `无法连接 API：${String(cause)}`);
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as { deleted: string };
}

/** The permanent model endpoint. `has_api_key` is all the server ever tells
 * the browser about the key -- the key itself never leaves the server
 * (CLAUDE.md 16). */
export function fetchModelSettings(): Promise<ModelSettings> {
  return getJson<ModelSettings>("/api/settings/model");
}

export function saveModelSettings(
  update: ModelSettingsUpdate,
): Promise<ModelSettings> {
  return putJson<ModelSettings>("/api/settings/model", update);
}

/** Save the deployment's free-trial endpoint (qwen3.8-max) as this account's
 * model settings. The server enforces the two-use quota; activating only
 * saves the endpoint, and each confirmed research task consumes one slot. */
export function activateFreeTrial(): Promise<ModelSettings> {
  return postJson<ModelSettings>("/api/settings/model/free-trial", {});
}

/** Probe the researcher's current form values against the live endpoint.
 * Nothing is saved; the server answers with the reason when the connection
 * fails, and the API key never appears in the response. */
export function testModelConnection(
  update: ModelSettingsUpdate,
): Promise<ModelTestResult> {
  return postJson<ModelTestResult>("/api/settings/model/test", update);
}

/** Paste a researcher's own text into a knowledge base as a document. The
 * content becomes searchable and the council treats it as a Level A
 * user-provided source exactly like an uploaded file. */
export async function addTextDocument(
  kbId: string,
  title: string,
  content: string,
): Promise<KnowledgeDocumentSummary> {
  return postJson<KnowledgeDocumentSummary>(
    `/api/knowledge-bases/${kbId}/documents/text`,
    { title, content },
  );
}

/** Account sessions: register (two-phase email verification) and login,
 * storing the bearer token for remember-me. The token is handed out exactly
 * once -- only confirmRegistration and login write it. */
export async function requestRegistration(
  body: RegistrationRequest,
): Promise<VerificationResponse> {
  return postJson<VerificationResponse>("/api/auth/register", body);
}

export async function confirmRegistration(
  body: RegistrationRequest & { code: string },
): Promise<AuthSession> {
  const session = await postJson<AuthSession>("/api/auth/register/confirm", body);
  setToken(session.token);
  return session;
}

export async function login(
  username: string,
  password: string,
): Promise<AuthSession> {
  const session = await postJson<AuthSession>("/api/auth/login", {
    username,
    password,
  });
  setToken(session.token);
  return session;
}

export async function logout(): Promise<void> {
  const token = getToken();
  try {
    if (token) await postJson<{ ok: boolean }>("/api/auth/logout", {});
  } finally {
    clearToken();
  }
}

/** Verify the remembered token at startup; throws 401 when it expired. */
export function fetchMe(): Promise<MeInfo> {
  return getJson<MeInfo>("/api/auth/me");
}

/** Account self-management (round-8): avatar, username, password, delete. */

/** Upload a new avatar image. Returns the stored image's metadata. */
export async function uploadAvatar(file: File): Promise<{ content_type: string; size_bytes: number }> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${BASE}/api/account/avatar`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as { content_type: string; size_bytes: number };
}

/** Fetch the avatar image bytes as a blob, or null when none is set. */
export async function fetchAvatarBlob(): Promise<Blob | null> {
  const token = getToken();
  if (!token) return null;
  const response = await fetch(`${BASE}/api/account/avatar`, {
    headers: authHeaders(),
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || response.statusText);
  }
  return response.blob();
}

/** Rename the account. Requires the current password. */
export function changeUsername(newUsername: string, password: string): Promise<{ username: string }> {
  return postJson<{ username: string }>("/api/account/username", {
    new_username: newUsername,
    password,
  });
}

/** Replace the password after verifying the old one. */
export function changePassword(oldPassword: string, newPassword: string): Promise<{ status: string }> {
  return postJson<{ status: string }>("/api/account/password", {
    old_password: oldPassword,
    new_password: newPassword,
  });
}

/** Permanently delete the account. Clears the local token on success. */
export async function deleteAccount(password: string): Promise<void> {
  await sendJson<unknown>("DELETE", "/api/account", { password });
  clearToken();
}

/** Request a password-reset code (202 even for unknown emails). */
export function requestPasswordReset(email: string): Promise<VerificationResponse> {
  return postJson<VerificationResponse>("/api/auth/forgot-password", { email });
}

/** Reset the password with the emailed code. */
export function resetPassword(email: string, code: string, password: string): Promise<{ status: string }> {
  return postJson<{ status: string }>("/api/auth/reset-password", {
    email,
    code,
    password,
  });
}

/** The account's skills: download, list, toggle, forget. */
export function fetchSkills(): Promise<SkillSummary[]> {
  return getJson<SkillSummary[]>("/api/skills");
}

export function addSkill(githubUrl: string): Promise<SkillSummary[]> {
  return postJson<SkillSummary[]>("/api/skills", { github_url: githubUrl });
}

export function setSkillEnabled(
  skillId: string,
  enabled: boolean,
): Promise<SkillSummary> {
  return putJson<SkillSummary>(`/api/skills/${skillId}`, { enabled });
}

export async function deleteSkill(skillId: string): Promise<void> {
  const response = await fetch(`${BASE}/api/skills/${skillId}`, {
    method: "DELETE",
    headers: authHeaders(),
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
 *
 * EventSource cannot attach Authorization headers, so the bearer token rides
 * in the query string -- a documented trade-off (README security section).
 */
export function subscribe(
  taskId: string,
  onEvent: (event: LedgerEvent) => void,
  onStateChange?: (state: "open" | "reconnecting") => void,
): () => void {
  const token = getToken();
  const source = new EventSource(
    `${BASE}/api/stream/${taskId}?token=${encodeURIComponent(token ?? "")}`,
  );
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

/** Process trace stream (live view). Frames carry an explicit `event: process`
 * line, so this uses addEventListener rather than onmessage; deduplication by
 * `seq` belongs to the caller (the server replays from the start on reconnect,
 * see packages/evidence/process_stream.py). */
export function subscribeProcess(
  taskId: string,
  onEvent: (event: ProcessEvent) => void,
  onStateChange?: (state: "open" | "reconnecting") => void,
): () => void {
  const token = getToken();
  const source = new EventSource(
    `${BASE}/api/stream/${taskId}/process?token=${encodeURIComponent(token ?? "")}`,
  );
  source.onopen = () => onStateChange?.("open");
  source.onerror = () => onStateChange?.("reconnecting");
  source.addEventListener("process", (raw) => {
    try {
      onEvent(JSON.parse((raw as MessageEvent).data) as ProcessEvent);
    } catch {
      // Skip a malformed frame; the next snapshot refresh is authoritative.
    }
  });
  return () => source.close();
}
