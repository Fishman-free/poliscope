/** The workspace shell.
 *
 * The product centre is the evidence map, not a chat box (CLAUDE.md 11), so the
 * shell is a task header plus three named tabs. Tab names say what is inside
 * them -- "审计轨迹", not "详情" -- because a specific label is what makes the
 * next screen predictable.
 *
 * Every screen answers: which task am I on, what state is it in, is it still
 * running, and how much of it is missing. That last one is the header's
 * gap counter, which is deliberately impossible to navigate past.
 */

import { useEffect, useState } from "react";

import { fetchReportMarkdown } from "./api/client";
import { useWorkspace } from "./api/useWorkspace";
import { Badge, Spinner, type Tone } from "./components/primitives";
import { AuditView } from "./views/AuditView";
import { BlindspotRadarView } from "./views/BlindspotRadarView";
import { BriefView } from "./views/BriefView";
import { CheckpointGate } from "./views/CheckpointGate";
import { CouncilView } from "./views/CouncilView";
import { EvolutionView } from "./views/EvolutionView";
import { KnowledgeBaseView } from "./views/KnowledgeBaseView";
import { MapView } from "./views/MapView";
import { NewTaskView } from "./views/NewTaskView";

import "./App.css";

type Tab =
  | "brief"
  | "map"
  | "council"
  | "radar"
  | "evolution"
  | "audit"
  | "knowledge";

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "brief", label: "Research Brief", hint: "结论与局限并排" },
  { id: "map", label: "Controversy Map", hint: "证据图与争议结构" },
  { id: "council", label: "Council", hint: "7 人议会状态" },
  { id: "radar", label: "Blindspot Radar", hint: "影响 x 可调查性" },
  { id: "evolution", label: "Evolution View", hint: "主张分叉与异议时间线" },
  { id: "audit", label: "Audit Trail", hint: "事件账本与拒绝记录" },
  { id: "knowledge", label: "知识库", hint: "长期记忆与检索" },
];

const STATUS_TONE: Record<string, Tone> = {
  COMPLETED: "admitted",
  COMPLETED_WITH_GAPS: "provisional",
  FAILED: "refuted",
  DEGRADED_RUNNING: "provisional",
  QUEUED: "unknown",
  AWAITING_CLAIM_CONFIRMATION: "unknown",
  AWAITING_COUNCIL_INPUT: "unknown",
  DRAFT: "unknown",
  PAUSED: "unknown",
  REPORTING: "unknown",
};

/** While the task sits at this checkpoint, poll for the status change that
 * follows a guidance submission. The SSE stream already covers everything
 * once the worker resumes and starts emitting new phase events again, but the
 * status flip itself (AWAITING_COUNCIL_INPUT -> QUEUED) is a plain column
 * update, not a ledger event -- nothing would otherwise tell this tab to stop
 * showing the gate the instant the researcher's own submission succeeds. */
const CHECKPOINT_POLL_MS = 3000;

/** Phase -> the tab that shows that phase's product. The council panel is
 * where the protocol runs; blindspot bounty is the one phase whose product
 * has a tab of its own (Blindspot Radar). Values match TaskPhase StrEnum
 * values in PHASE_STARTED payloads. */
const PHASE_TAB: Record<string, Tab> = {
  PRECOMMITMENT: "council",
  ACQUISITION: "council",
  EVIDENCE_EXCHANGE: "council",
  CROSS_EXAMINATION: "council",
  BLINDSPOT_BOUNTY: "radar",
  JOINT_MODELING: "council",
  FINAL_REJUDGMENT: "council",
};

/** Terminal ledger events -> the brief is what a finished task has to offer. */
const TERMINAL_TABS: Record<string, Tab> = {
  TASK_COMPLETED: "brief",
  TASK_COMPLETED_WITH_GAPS: "brief",
  TASK_FAILED: "brief",
};

/** Read the task id from ?task=, so a researcher can share a link to exactly
 * the workspace they are looking at. */
function taskIdFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get("task");
}

export function App() {
  const [taskId, setTaskId] = useState<string | null>(taskIdFromLocation);
  const [tab, setTab] = useState<Tab>("brief");
  // Auto-follow: while it is on, each new phase event moves the active tab to
  // the phase's own view ("模型走到哪一步，界面切到哪一步"). The moment the
  // researcher clicks a tab themselves, follow stops -- a live run must not
  // yank the screen away from what they chose to look at. Opening a different
  // task re-arms it.
  const [follow, setFollow] = useState(true);
  const [draft, setDraft] = useState(taskId ?? "");
  const { snapshot, load, stream, error, events, refresh } = useWorkspace(taskId);

  useEffect(() => {
    const onPop = () => setTaskId(taskIdFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    if (snapshot?.task.status !== "AWAITING_COUNCIL_INPUT") return;
    const id = window.setInterval(refresh, CHECKPOINT_POLL_MS);
    return () => window.clearInterval(id);
  }, [snapshot?.task.status, refresh]);

  // Follow the newest ledger event while auto-follow is armed. This reads the
  // wire's event list, never the snapshot -- the projector, not the browser,
  // decides what an event means (see useWorkspace's docstring).
  useEffect(() => {
    if (!follow) return;
    const latest = events[events.length - 1];
    if (!latest) return;
    const phase = latest.kind === "PHASE_STARTED"
      && typeof latest.payload.phase === "string"
      ? latest.payload.phase
      : "";
    const next = PHASE_TAB[phase] ?? TERMINAL_TABS[latest.kind];
    if (next) setTab(next);
  }, [events, follow]);

  function open(next: string) {
    const trimmed = next.trim();
    if (!trimmed) return;
    const url = new URL(window.location.href);
    url.searchParams.set("task", trimmed);
    window.history.pushState({}, "", url);
    setTaskId(trimmed);
    setTab("brief");
    setFollow(true);
  }

  /** A manual tab choice always wins over auto-follow. */
  function selectTab(next: Tab) {
    setFollow(false);
    setTab(next);
  }

  async function exportMarkdown() {
    if (!taskId) return;
    const markdown = await fetchReportMarkdown(taskId);
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `poliscope-brief-${taskId}.md`;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  const brief = snapshot?.brief;
  const gapCount = brief
    ? new Set(brief.absent_seats).size +
      new Set(brief.skipped_phases).size +
      new Set(brief.failed_phases).size
    : 0;

  return (
    <div className="app">
      <header className="app__chrome">
        <div className="app__identity">
          <a className="app__wordmark" href="/" title="返回公开落地页">
            Poliscope
          </a>
          <span className="app__method">EpistemoBrain · 争议证据地图</span>
        </div>

        <form
          className="app__open"
          onSubmit={(event) => {
            event.preventDefault();
            open(draft);
          }}
        >
          <label className="app__open-label" htmlFor="task-id">
            任务 ID
          </label>
          <input
            id="task-id"
            className="app__open-input mono"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="粘贴 task_id"
            spellCheck={false}
          />
          <button type="submit" className="button">
            打开
          </button>
        </form>
      </header>

      {taskId === null ? (
        <main className="app__main app__main--split">
          {/* 无任务时并排展示：左表单建任务，右知识库管理（长期记忆）。
              有任务后知识库仍可通过同名 tab 访问。 */}
          <NewTaskView onCreated={open} />
          <KnowledgeBaseView />
        </main>
      ) : (
        <>
          <div className="app__task">
            <div className="app__task-head">
              <h1>{snapshot?.task.question ?? "载入中…"}</h1>
              <div className="app__task-meta">
                {snapshot ? (
                  <Badge tone={STATUS_TONE[snapshot.task.status] ?? "unknown"}>
                    {snapshot.task.status}
                  </Badge>
                ) : null}
                {/* The gap count is on the header of every tab. A researcher
                    must not be able to read a conclusion without seeing how
                    much of the protocol did not run. */}
                {gapCount > 0 ? (
                  <Badge
                    tone="refuted"
                    title="缺席席位、未执行轮次与失败轮次的合计"
                  >
                    {gapCount} 处未完成
                  </Badge>
                ) : null}
                <span className="app__task-id mono">{taskId}</span>
              </div>
            </div>

            <nav className="app__tabs" aria-label="视图">
              {TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`app__tab${tab === item.id ? " app__tab--on" : ""}`}
                  aria-current={tab === item.id ? "page" : undefined}
                  onClick={() => selectTab(item.id)}
                >
                  <span>{item.label}</span>
                  <span className="app__tab-hint">{item.hint}</span>
                </button>
              ))}
            </nav>
          </div>

          <main className="app__main">
            {load === "loading" && !snapshot ? (
              <Spinner label="正在载入工作台快照…" />
            ) : null}

            {load === "error" ? (
              <div className="app__error" role="alert">
                <strong>无法载入工作台</strong>
                <p>{error}</p>
                <p className="app__error-note">
                  这不是「没有证据」，而是「读不到证据」。请确认 API 正在运行且
                  task_id 正确。
                </p>
              </div>
            ) : null}

            {snapshot && brief ? (
              <>
                {taskId && snapshot.task.status === "AWAITING_COUNCIL_INPUT" ? (
                  <CheckpointGate
                    taskId={taskId}
                    seats={snapshot.seats}
                    onSubmitted={refresh}
                  />
                ) : null}
                {/* key={tab} forces a fresh mount per tab switch, so the
                    entrance animation in App.css replays every time instead
                    of only on first load -- see .app__panel there. */}
                <div className="app__panel" key={tab}>
                  {tab === "brief" ? (
                    <BriefView
                      brief={brief}
                      safety={snapshot.safety_notice}
                      onExport={exportMarkdown}
                    />
                  ) : null}
                  {tab === "map" ? <MapView graph={snapshot.graph} /> : null}
                  {tab === "council" ? (
                    <CouncilView seats={snapshot.seats} events={events} />
                  ) : null}
                  {tab === "radar" ? (
                    <BlindspotRadarView blindspots={snapshot.blindspots} />
                  ) : null}
                  {tab === "evolution" ? (
                    <EvolutionView entries={snapshot.evolution} />
                  ) : null}
                  {tab === "audit" ? (
                    <AuditView events={events} streamOpen={stream === "open"} />
                  ) : null}
                  {tab === "knowledge" ? <KnowledgeBaseView /> : null}
                </div>
              </>
            ) : null}
          </main>
        </>
      )}
    </div>
  );
}
