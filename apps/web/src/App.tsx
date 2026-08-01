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
import { BriefView } from "./views/BriefView";
import { MapView } from "./views/MapView";

import "./App.css";

type Tab = "brief" | "map" | "audit";

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "brief", label: "Research Brief", hint: "结论与局限并排" },
  { id: "map", label: "Controversy Map", hint: "证据图与争议结构" },
  { id: "audit", label: "Audit Trail", hint: "事件账本与拒绝记录" },
];

const STATUS_TONE: Record<string, Tone> = {
  COMPLETED: "admitted",
  COMPLETED_WITH_GAPS: "provisional",
  FAILED: "refuted",
  DEGRADED_RUNNING: "provisional",
  QUEUED: "unknown",
  AWAITING_CLAIM_CONFIRMATION: "unknown",
  DRAFT: "unknown",
  PAUSED: "unknown",
  REPORTING: "unknown",
};

/** Read the task id from ?task=, so a researcher can share a link to exactly
 * the workspace they are looking at. */
function taskIdFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get("task");
}

export function App() {
  const [taskId, setTaskId] = useState<string | null>(taskIdFromLocation);
  const [tab, setTab] = useState<Tab>("brief");
  const [draft, setDraft] = useState(taskId ?? "");
  const { snapshot, load, stream, error, events } = useWorkspace(taskId);

  useEffect(() => {
    const onPop = () => setTaskId(taskIdFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function open(next: string) {
    const trimmed = next.trim();
    if (!trimmed) return;
    const url = new URL(window.location.href);
    url.searchParams.set("task", trimmed);
    window.history.pushState({}, "", url);
    setTaskId(trimmed);
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
          <span className="app__wordmark">Poliscope</span>
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
        <main className="app__main app__placeholder">
          <h1>打开一个研究任务</h1>
          <p>
            用 <code>poliscope start</code> 创建任务并确认原子主张，
            然后把返回的 task_id 粘贴到上方。
          </p>
          <p className="app__placeholder-note">
            本系统为科研辅助工具，不提供医学诊断或医疗建议。
          </p>
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
                  onClick={() => setTab(item.id)}
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
                {tab === "brief" ? (
                  <BriefView
                    brief={brief}
                    safety={snapshot.safety_notice}
                    onExport={exportMarkdown}
                  />
                ) : null}
                {tab === "map" ? <MapView graph={snapshot.graph} /> : null}
                {tab === "audit" ? (
                  <AuditView events={events} streamOpen={stream === "open"} />
                ) : null}
              </>
            ) : null}
          </main>
        </>
      )}
    </div>
  );
}
