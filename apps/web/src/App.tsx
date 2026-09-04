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
 *
 * Layout: the main column carries the work (new-task home or the task
 * workspace); a fixed right sidebar carries the researcher's permanent model
 * settings and the session history -- the replacement for the old "paste a
 * task id" box, so every past session is one click away instead of a UUID.
 */

import { flushSync } from "react-dom";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import { cancelTask, clearToken, fetchMe, fetchPaperMarkdown, fetchReportMarkdown, fetchTasks, getToken, logout, reResearch, rerunFresh, resumeTask } from "./api/client";
import type { ResearchBrief, TaskSummary } from "./api/types";
import { SEAT_LABELS, type Seat } from "./api/types";
import { useWorkspace } from "./api/useWorkspace";
import { ReResearchDialog } from "./components/ReResearchDialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Spinner, TaskStatusBadge } from "./components/primitives";
import { LOCALE_LABELS, LOCALES, setLocale, t, useLocale } from "./i18n";
import { AccountMenu } from "./views/AccountMenu";
import { AuditView } from "./views/AuditView";
import { AuthView } from "./views/AuthView";
import { BlindspotRadarView } from "./views/BlindspotRadarView";
import { BriefView } from "./views/BriefView";
import { CheckpointGate } from "./views/CheckpointGate";
import { CouncilView } from "./views/CouncilView";
import { EvolutionView } from "./views/EvolutionView";
import { AnnotationView } from "./views/AnnotationView";
import { FollowUpView } from "./views/FollowUpView";
import { LineageView } from "./views/LineageView";
import { ResearchToolsView } from "./views/ResearchToolsView";
import { SharedView } from "./views/SharedView";
import { KnowledgeBaseView } from "./views/KnowledgeBaseView";
import { LiveView } from "./views/LiveView";
import { MapView } from "./views/MapView";
import { ModelSettingsPanel } from "./views/ModelSettingsPanel";
import { NewTaskView } from "./views/NewTaskView";
import { PaperView } from "./views/PaperView";
import { SessionHistory } from "./views/SessionHistory";
import { SkillsPanel } from "./views/SkillsPanel";

import "./App.css";

type Tab =
  | "live"
  | "brief"
  | "map"
  | "council"
  | "radar"
  | "evolution"
  | "audit"
  | "paper"
  | "knowledge"
  | "followup"
  | "lineage"
  | "tools"
  | "annotations";

/** No-task home has two screens behind a small segmented control. */
type HomeView = "newtask" | "knowledge";

/** Tab labels are rendered through t() at render time (module-level t() could
 * not react to a language switch). Labels that are already English pass
 * through unchanged; hints are translated. */
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "live", label: "Live Progress", hint: "思考链路与检索" },
  { id: "brief", label: "Research Brief", hint: "结论与局限并排" },
  { id: "map", label: "Controversy Map", hint: "证据图与争议结构" },
  { id: "council", label: "Council", hint: "7 人议会状态" },
  { id: "radar", label: "Blindspot Radar", hint: "影响 x 可调查性" },
  { id: "evolution", label: "Evolution View", hint: "主张分叉与异议时间线" },
  { id: "audit", label: "Audit Trail", hint: "事件账本与拒绝记录" },
  { id: "paper", label: "Final Paper", hint: "整合结论与参考文献" },
  { id: "knowledge", label: "Knowledge Base", hint: "长期记忆与检索" },
  { id: "followup", label: "Follow-up", hint: "完成后追问模型" },
  { id: "lineage", label: "Evidence Lineage", hint: "来源独立性与证据簇" },
  { id: "tools", label: "Research Tools", hint: "成本、裁决、分享与时间旅行" },
  { id: "annotations", label: "Annotations", hint: "人工标注与评分者一致性" },
];

/** While the task sits at this checkpoint, poll for the status change that
 * follows a guidance submission. The SSE stream already covers everything
 * once the worker resumes and starts emitting new phase events again, but the
 * status flip itself (AWAITING_COUNCIL_INPUT -> QUEUED) is a plain column
 * update, not a ledger event -- nothing would otherwise tell this tab to stop
 * showing the gate the instant the researcher's own submission succeeds.
 * QUEUED gets the same polling: a queued task emits no ledger or process
 * events until the worker picks it up, so without polling the "排队中" view
 * would sit frozen until a manual refresh. */
const CHECKPOINT_POLL_MS = 3000;

/** Phase -> the tab that shows that phase's product. While a run is live,
 * every phase drives the Live view (thinking path, real time); a finished
 * task lands on the brief. Values match TaskPhase StrEnum values in
 * PHASE_STARTED payloads. */
const PHASE_TAB: Record<string, Tab> = {
  PRECOMMITMENT: "live",
  ACQUISITION: "live",
  EVIDENCE_EXCHANGE: "live",
  CROSS_EXAMINATION: "live",
  BLINDSPOT_BOUNTY: "live",
  JOINT_MODELING: "live",
  FINAL_REJUDGMENT: "live",
  REPORTING: "live",
};

/** Terminal ledger events -> the paper is what a finished task has to offer. */
const TERMINAL_TABS: Record<string, Tab> = {
  TASK_COMPLETED: "paper",
  TASK_COMPLETED_WITH_GAPS: "paper",
  TASK_FAILED: "paper",
};

/** Phase ids -> Chinese labels for the header's gap detail line. Mirrors the
 * PHASES list in LiveView; kept as a small local table so the shell does not
 * import a view module. */
const PHASE_LABELS: Record<string, string> = {
  PRECOMMITMENT: "独立预承诺",
  ACQUISITION: "专业取证",
  EVIDENCE_EXCHANGE: "证据交换",
  CROSS_EXAMINATION: "交叉质询",
  BLINDSPOT_BOUNTY: "盲点悬赏",
  JOINT_MODELING: "联合建模",
  FINAL_REJUDGMENT: "最终复判",
  REPORTING: "报告生成",
};

/** Every concrete gap behind the header's gap count, as full sentences.
 * The "{count} 处未完成" badge names no subject, so a researcher could never
 * tell a missing seat from a failed round -- this line says exactly which
 * seats and which phases, in the same vocabulary as the council views. */
function gapDetails(brief: ResearchBrief): string[] {
  const details: string[] = [];
  for (const seat of new Set(brief.absent_seats)) {
    details.push(`${SEAT_LABELS[seat as Seat] ?? seat}缺席`);
  }
  for (const phase of new Set(brief.skipped_phases)) {
    details.push(`${PHASE_LABELS[phase] ?? phase}未执行`);
  }
  for (const phase of new Set(brief.failed_phases)) {
    details.push(`${PHASE_LABELS[phase] ?? phase}失败`);
  }
  return details;
}

/** Queue visibility for a QUEUED task (round-6 "stuck at 已入队" report):
 * how many tasks are ahead of it, and which task the single worker is
 * currently running. Computed from the session-history list -- the server
 * already orders by created_at, which is the queue order. */
interface QueueInfo {
  ahead: number;
  running: { question: string; minutes: number } | null;
}

/** Statuses a task can be stopped from (round-10 「停止研究」). A terminal
 * task is already over; a draft is still being shaped by the researcher.
 * These are the ones where "stop it now" has a real meaning. */
const STOPPABLE_STATUSES = new Set([
  "QUEUED",
  "RUNNING",
  "DEGRADED_RUNNING",
  "PAUSED",
  "AWAITING_COUNCIL_INPUT",
  "REPORTING",
]);

/** Statuses that can move again without a new task: FAILED / CANCELLED /
 * COMPLETED / COMPLETED_WITH_GAPS via 「重新研究」, PAUSED via 「继续研究」.
 * Mirror ResearchService._RERUNNABLE_STATUSES plus PAUSED. */
const RESUMEABLE_STATUSES = new Set([
  "FAILED",
  "CANCELLED",
  "PAUSED",
  "COMPLETED",
  "COMPLETED_WITH_GAPS",
]);

/** A task in one of these lifecycle states may still change status server-side
 * without a ledger event (watchdog/EventConflict flip the column but not a
 * ledger terminal), so the insurance poll watches them. Terminal and draft
 * states are excluded -- their status is stable and a poll would be noise. */
const INSURANCE_POLL_STATUSES = new Set([
  "QUEUED",
  "RUNNING",
  "DEGRADED_RUNNING",
  "AWAITING_COUNCIL_INPUT",
  "REPORTING",
]);

/** Insurance poll schedule: 15s normally, backing off to 30s then 60s on
 * consecutive failures (never harder than 60s), resetting on success. */
/** Visible-tab cadence. Hidden-tab cadence is slower so a background
 * workspace still refreshes (the worker never depends on the tab, but the
 * researcher coming back must not stare at a frozen RUNNING state). */
const INSURANCE_BACKOFF = [15_000, 30_000, 60_000] as const;
const INSURANCE_HIDDEN_MS = 45_000;

function computeQueue(
  tasks: TaskSummary[],
  currentId: string | null,
): QueueInfo {
  const ordered = [...tasks].sort((a, b) =>
    String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")),
  );
  const currentIndex = currentId
    ? ordered.findIndex((task) => task.task_id === currentId)
    : -1;
  const ahead =
    currentIndex > 0
      ? ordered
          .slice(0, currentIndex)
          .filter((task) => task.status === "QUEUED").length
      : 0;
  const running = tasks.find((task) => task.status === "RUNNING") ?? null;
  return {
    ahead,
    running: running
      ? {
          question: running.question,
          minutes: running.updated_at
            ? Math.max(
                1,
                Math.round(
                  (Date.now() - new Date(running.updated_at).getTime()) / 60000,
                ),
              )
            : 0,
        }
      : null,
  };
}

/** Read the task id from ?task=, so a researcher can share a link to exactly
 * the workspace they are looking at. */
function taskIdFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get("task");
}

/** Apple-style screen switch: View Transitions API captures before/after
 * snapshots and plays the CSS transition between them. `flushSync` forces the
 * state commit inside the snapshot window -- React 18's async batching would
 * otherwise capture the old DOM as the "new" snapshot. Browsers without the
 * API fall back to the plain state change (whose entrance animation lives in
 * App.css). */
function withTransition(apply: () => void) {
  if (typeof document.startViewTransition === "function") {
    document.startViewTransition(() => flushSync(apply));
  } else {
    apply();
  }
}

/** "checking" while the remembered token is verified against /api/auth/me;
 * "anon" renders the login screen; "authed" renders the workspace. */
type AuthState = "checking" | "anon" | "authed";

/** Language switcher (round-4 UI i18n). Changing the locale re-renders the
 * whole app through useSyncExternalStore, so every t() call refreshes. */
function LanguageSwitcher() {
  const current = useLocale();
  return (
    <label className="app__lang">
      <span className="app__lang-label">{t("语言")}</span>
      <select
        value={current}
        onChange={(event) => setLocale(event.target.value as (typeof LOCALES)[number])}
        aria-label={t("界面语言")}
      >
        {LOCALES.map((localeOption) => (
          <option key={localeOption} value={localeOption}>
            {LOCALE_LABELS[localeOption]}
          </option>
        ))}
      </select>
    </label>
  );
}

export function App() {
  // Subscribes the shell to locale changes; the switcher and every t() call
  // in the tree re-render through the same store.
  useLocale();
  const [auth, setAuth] = useState<AuthState>(
    getToken() ? "checking" : "anon",
  );
  const [username, setUsername] = useState("");
  // taskId 初始化为 null 而不是直接从 URL 读：auth 尚未确定为 authed 时
  // 不携带任务状态（未登录打开 ?task=xxx 会触发无 token 的 401 workspace
  // 拉取）。登录/免登录验证成功后再从 URL 补读 taskId——见 onAuthed 与
  // fetchMe 成功分支。
  const [taskId, setTaskId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("brief");
  const [homeView, setHomeView] = useState<HomeView>("newtask");
  // Auto-follow: while it is on, each new phase event moves the active tab to
  // the phase's own view ("模型走到哪一步，界面切到哪一步"). The moment the
  // researcher clicks a tab themselves, follow stops -- a live run must not
  // yank the screen away from what they chose to look at. Opening a different
  // task re-arms it.
  const [follow, setFollow] = useState(true);
  const { snapshot, load, stream, error, events, processEvents, refresh } =
    useWorkspace(taskId);

  // 排队任务的队列可见性：QUEUED 时轮询任务列表，算出前面还有几个、
  // Worker 正在跑哪个任务（round-6「已入队后迟迟无响应」的根因是队列
  // 里别的任务把单 worker 占满，前端却什么都不说）。拉取失败保持现状
  // ——队列信息是增强，不能打断主视图。
  const [queue, setQueue] = useState<QueueInfo | null>(null);
  useEffect(() => {
    if (snapshot?.task.status !== "QUEUED") {
      setQueue(null);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const list = await fetchTasks();
        if (!cancelled) setQueue(computeQueue(list, taskId));
      } catch {
        // 队列信息失败不报错：下一轮轮询会重试。
      }
    };
    void poll();
    const id = window.setInterval(poll, CHECKPOINT_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [snapshot?.task.status, taskId]);

  // Sliding tab indicator: measure the active tab so the bar glides from
  // wherever it was to the new position (App.css transitions the transform).
  // When the rail overflows (narrow screens), the active tab is scrolled into
  // view so switching tabs never leaves the current one off-screen.
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });
  useEffect(() => {
    const active = tabRefs.current[tab];
    if (active) {
      setIndicator({ left: active.offsetLeft, width: active.offsetWidth });
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      active.scrollIntoView({
        behavior: reduce ? "auto" : "smooth",
        block: "nearest",
        inline: "center",
      });
    }
  }, [tab, taskId]);

  // 本机免登录：有 token 就先验证；过期或无效则清掉回到登录页。
  useEffect(() => {
    if (auth !== "checking") return;
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (!cancelled) {
          setUsername(me.username);
          // 免登录进入时补读 URL 上的 ?task=（可能是收藏夹/分享链接直接
          // 指向某个任务工作台），登录已有效所以可以安全加载。
          setTaskId(taskIdFromLocation());
          setAuth("authed");
        }
      })
      .catch(() => {
        if (!cancelled) {
          clearToken();
          setAuth("anon");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [auth]);

  async function signOut() {
    await logout();
    setAuth("anon");
    setTaskId(null);
    window.history.replaceState({}, "", "/workspace");
  }

  useEffect(() => {
    const onPop = () => setTaskId(taskIdFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const status = snapshot?.task.status;
    if (status !== "AWAITING_COUNCIL_INPUT" && status !== "QUEUED") return;
    const id = window.setInterval(() => void refresh(), CHECKPOINT_POLL_MS);
    return () => window.clearInterval(id);
  }, [snapshot?.task.status, refresh]);

  // 状态保险刷新（round 修复）：SSE 仍是主路径，但 watchdog/EventConflict
  // 只改数据库状态、不发账本终态事件时，前端会一直停在 RUNNING 上，连
  // 「重新研究」入口都看不到。此轮询只覆盖可能无声变动的非终态，且：
  //   - 页面隐藏即停（visibilitychange 恢复可见时立即 refresh 一次）；
  //   - 同时间只允许一个刷新请求（useWorkspace 的 refresh 已 abort 上一次）；
  //   - 连续失败退避 15→30→60s，成功后回到 15s；
  //   - 切换 taskId 清理旧 timer（依赖 taskId）。
  useEffect(() => {
    if (!taskId || !isStatusIn(INSURANCE_POLL_STATUSES, snapshot?.task.status)) return;
    let cancelled = false;
    let timer = 0;
    let failStreak = 0;
    const schedule = () => {
      if (cancelled) return;
      const delay =
        document.visibilityState === "hidden"
          ? INSURANCE_HIDDEN_MS
          : INSURANCE_BACKOFF[Math.min(failStreak, INSURANCE_BACKOFF.length - 1)];
      timer = window.setTimeout(run, delay);
    };
    const run = async () => {
      if (cancelled) return;
      // Hidden tabs still refresh, just slower: the worker never depends on
      // this connection, but a researcher who tabbed away must not come back
      // to a frozen RUNNING state (browsers throttle timers; we still fire).
      const ok = await refresh();
      failStreak = ok ? 0 : failStreak + 1;
      schedule();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void run();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [taskId, snapshot?.task.status, refresh]);

  // 打开任务后按状态定初始视图：排队中 / 运行中 / 停在检查点的任务落在
  // 「实时进展」（思考链路是这类任务唯一有内容可看的地方，尤其是从外部
  // 链接直达时 —— 没有新事件驱动 follow，不显式切换就会停在初始 tab）；
  // 终态任务没有新事件、落在「最终论文」（有完整可读内容的是整合论文，
  // Research Brief 仍可手动查看）。
  useEffect(() => {
    if (!follow || !snapshot) return;
    const terminal =
      snapshot.task.status === "COMPLETED" ||
      snapshot.task.status === "COMPLETED_WITH_GAPS" ||
      snapshot.task.status === "FAILED";
    if (terminal) setTab("paper");
    // CANCELLED (round-10): the researcher just stopped the run, so they are
    // watching it go quiet -- a paper tab over a half-run council would be
    // empty. Leave the current tab where it is.
    else if (snapshot.task.status === "CANCELLED") {
      return;
    }
    // The shell's initial tab is "brief"; a still-running task must move the
    // researcher to the live view, not leave them reading an empty brief.
    else if (tab === "brief") setTab("live");
  }, [follow, snapshot, tab]);

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

  function open(taskIdToOpen: string) {
    withTransition(() => {
      const url = new URL(window.location.href);
      url.searchParams.set("task", taskIdToOpen);
      window.history.pushState({}, "", url);
      setTaskId(taskIdToOpen);
      // 打开任务直接落在「实时进展」：研究者要看到思考链路，不是干等结果。
      // 任务若已终态，下方 snapshot 终态 effect 会把它带回 brief。
      setTab("live");
      setFollow(true);
    });
  }

  function switchHome(next: HomeView) {
    withTransition(() => setHomeView(next));
  }

  /** 会话历史里删掉了当前正在看的任务：离开它，回到新建任务页。 */
  function handleTaskDeleted(deletedId: string) {
    if (deletedId === taskId) {
      setTaskId(null);
      window.history.replaceState({}, "", "/workspace");
    }
  }

  /** 「新建研究」：回到首页，重新打开一个空白的新任务表单。 */
  function handleNewResearch() {
    withTransition(() => {
      setTaskId(null);
      window.history.replaceState({}, "", "/workspace");
      setHomeView("newtask");
    });
  }

  // busyTask 绑定具体正在变动的任务：让「重新研究/继续研究/停止」的 loading
  // 只落在那一行的按钮上，而不是禁用整片界面。值为 null 表示没有任务在变动。
  const [busyTask, setBusyTask] = useState<string | null>(null);

  function isStatusIn(
    set: Set<string>,
    value: string | null | undefined,
  ): boolean {
    return value != null && set.has(value);
  }

  /** 通用“让任务恢复前进”的入口：FAILED/CANCELLED 走「重新研究」，
   * PAUSED 走「继续研究」。成功后若该任务正是当前工作台，立即刷新
   * snapshot（否则保险轮询会兜底）。返回是否成功，供调用方决定是否刷新
   * 会话历史。 */
  const runMutation = useCallback(
    async (target: string, mutate: (id: string) => Promise<unknown>): Promise<boolean> => {
      if (busyTask) return false;
      setBusyTask(target);
      try {
        await mutate(target);
        if (target === taskId) {
          await refresh();
        }
        return true;
      } catch (cause) {
        // 失败保留现状，不把界面推进到一个没发生的状态。
        console.error(cause);
        return false;
      } finally {
        setBusyTask(null);
      }
    },
    [busyTask, taskId, refresh],
  );

  /** 「重新研究」：把 FAILED/CANCELLED 任务交回队列。round-12 起先弹出
   * 模式选择（从头研究 / 从断点处研究），再按所选模式发起请求——两种
   * 模式对深度研究与论文审查任务同样生效。 */
  const [rerunTarget, setRerunTarget] = useState<string | null>(null);

  const handleReResearch = useCallback(
    (target: string) => setRerunTarget(target),
    [],
  );

  const handleRerunChoose = useCallback(
    (mode: "full" | "first_gap") => {
      const target = rerunTarget;
      setRerunTarget(null);
      if (!target) return;
      void runMutation(target, async (id) => {
        if (mode === "full") {
          // round-13 「从头研究」：同一任务无法真正重来——账本幂等键按
          // 阶段/席位派生，旧一轮的事件会与新一轮冲突。服务器创建全新
          // 任务（全新账本与证据图、继承问题/范围/确认主张/预算/模型
          // 配置），完成后直接打开新任务。
          const fresh = await rerunFresh(id);
          open(fresh.task_id);
        } else {
          // 从断点处研究：有断点时原任务续跑；无断点时服务端同样会创建
          // 全新任务（响应里的 task_id 变为新 id）——同样跳转过去。
          const result = await reResearch(id, mode);
          if (result.task_id !== id) open(result.task_id);
        }
      });
    },
    [rerunTarget, runMutation, open],
  );

  const handleRerunCancel = useCallback(() => setRerunTarget(null), []);

  /** 「继续研究」：把 PAUSED 任务交回队列（后端 /resume 只接受 PAUSED）。 */
  const handleResume = useCallback(
    (target: string) => runMutation(target, resumeTask),
    [runMutation],
  );

  /** 「停止研究」（round-10）：停止当前正在运行或排队的任务。 */
  async function handleStopResearch() {
    if (!taskId || busyTask) return;
    setBusyTask(taskId);
    try {
      await cancelTask(taskId);
      // 状态由 stream / snapshot 驱动刷新；这里立即拉一次让界面快速反映。
      await refresh();
    } catch (cause) {
      // 停止失败保留现状，不把界面推进到一个没发生的状态。
      console.error(cause);
    } finally {
      setBusyTask(null);
    }
  }

  /** 会话历史里的操作（重新研究/继续研究）成功后回调：若该任务正是当前
   * 工作台，立即 refresh —— 与任务头按钮走同一路径。 */
  const handleTaskMutated = useCallback(
    (mutatedId: string) => {
      if (mutatedId === taskId) void refresh();
    },
    [taskId, refresh],
  );

  /** A manual tab choice always wins over auto-follow. */
  function selectTab(next: Tab) {
    withTransition(() => {
      setFollow(false);
      setTab(next);
    });
  }

  /** Blob-download any server-rendered markdown document under a given name.
   * The filename carries a short task id so two downloads don't collide, and
   * the full id stays in the URL bar for a researcher who needs it. */
  async function downloadMarkdown(
    fetch: () => Promise<string>,
    basename: string,
  ) {
    if (!taskId) return;
    const markdown = await fetch();
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `${basename}-${taskId.slice(0, 8)}.md`;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  function exportBriefMarkdown() {
    if (!taskId) return;
    void downloadMarkdown(
      () => fetchReportMarkdown(taskId),
      "poliscope-brief",
    );
  }

  function exportPaperMarkdown() {
    if (!taskId) return;
    void downloadMarkdown(() => fetchPaperMarkdown(taskId), "poliscope-paper");
  }

  const brief = snapshot?.brief;
  const gapCount = brief
    ? new Set(brief.absent_seats).size +
      new Set(brief.skipped_phases).size +
      new Set(brief.failed_phases).size
    : 0;

  // A2 public read-only share: /shared/{token} renders WITHOUT the auth gate
  // or the workspace chrome. The server has already redacted the snapshot, so
  // no token, sidebar, follow-up or model panel is offered.
  const sharedMatch = window.location.pathname.match(/^\/shared\/([^/]+)\/?$/);
  if (sharedMatch) {
    return <SharedView token={decodeURIComponent(sharedMatch[1] ?? "")} />;
  }

  if (auth !== "authed") {
    return (
      <div className="app">
        <header className="app__chrome">
          <div className="app__identity">
            <a className="app__wordmark" href="/" title={t("返回公开落地页")}>
              Poliscope
            </a>
            <span className="app__method">{t("EpistemoBrain · 争议证据地图")}</span>
          </div>
          <LanguageSwitcher />
        </header>
        {auth === "checking" ? (
          <main className="app__main">
            <Spinner label={t("正在验证登录状态…")} />
          </main>
        ) : (
          <AuthView
            onAuthed={(name) => {
              setUsername(name);
              // 登录/注册成功后：补读 URL 上的 task=（可能是分享链接指向
              // 某个任务），并清除 mode 参数——落地页的注册便捷入口在
              // 登录后没有意义，残留会让后续访问一直顶到注册页。
              const url = new URL(window.location.href);
              const pendingTask = url.searchParams.get("task");
              url.searchParams.delete("mode");
              window.history.replaceState({}, "", url);
              setTaskId(pendingTask ?? null);
              setAuth("authed");
            }}
            initialMode={
              new URLSearchParams(window.location.search).get("mode") === "register" &&
              !new URLSearchParams(window.location.search).get("task")
                ? "register"
                : "login"
            }
          />
        )}
      </div>
    );
  }

  return (
    <div className="app">
      {rerunTarget ? (
        <ReResearchDialog
          onChoose={handleRerunChoose}
          onCancel={handleRerunCancel}
        />
      ) : null}
      <header className="app__chrome">
        <div className="app__identity">
          <a className="app__wordmark" href="/" title={t("返回公开落地页")}>
            Poliscope
          </a>
          <span className="app__method">{t("EpistemoBrain · 争议证据地图")}</span>
        </div>
        <div className="app__account">
          <LanguageSwitcher />
          <AccountMenu username={username} onSignedOut={signOut} />
          <SessionHistory
            currentTaskId={taskId}
            onOpen={open}
            onDeleted={handleTaskDeleted}
            onTaskMutated={handleTaskMutated}
          />
        </div>
      </header>

      <div className="app__layout">
        <main className="app__main">
          {taskId === null ? (
            <>
              <div className="app__home-switch" role="tablist" aria-label={t("主页视图")}>
                {(["newtask", "knowledge"] as const).map((view) => (
                  <button
                    key={view}
                    type="button"
                    role="tab"
                    aria-selected={homeView === view}
                    className={
                      "app__home-tab" + (homeView === view ? " app__home-tab--on" : "")
                    }
                    onClick={() => switchHome(view)}
                  >
                    {view === "newtask" ? t("新建任务") : t("知识库")}
                  </button>
                ))}
              </div>
              {/* 两个主页视图同时挂载、以 CSS 显隐切换，而不是 key={homeView}
                  重挂载：切到知识库再切回时，新建任务已填的内容（问题、高级
                  选项、勾选）与滚动位置都原样保留。切换动画由 View Transitions
                  提供（withTransition 的快照交叉淡化）；无该 API 的浏览器直接
                  切换。隐藏面板不参与视图过渡捕获（view-transition-name: none）。 */}
              <div className="app__home">
                {/* 两个面板各自持有固定的 view-transition-name（home-newtask /
                    home-knowledge，见 App.css）：切换时浏览器把它们当作两个
                    独立元素各自淡出/淡入，快照不会在元素间转移 —— 那正是
                    「先跳到知识库、又闪回新建任务、再跳回知识库」的来回跳的
                    根因（同一个 name 从一个元素转移到另一个元素时，浏览器
                    的快照配对会错乱）。 */}
                <div
                  className={
                    "app__panel app__panel--home-newtask" +
                    (homeView === "newtask" ? "" : " app__panel--hidden")
                  }
                  aria-hidden={homeView !== "newtask"}
                >
                  <NewTaskView
                    onCreated={open}
                    onManageKnowledge={() => switchHome("knowledge")}
                    active={homeView === "newtask"}
                    draftNamespace={username}
                  />
                </div>
                <div
                  className={
                    "app__panel app__panel--home-knowledge" +
                    (homeView === "newtask" ? " app__panel--hidden" : "")
                  }
                  aria-hidden={homeView === "newtask"}
                >
                  <KnowledgeBaseView active={homeView === "knowledge"} />
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="app__task">
                <div className="app__task-head">
                  {/* 标题与状态徽标必须紧邻（round 任务头修复）：h1 与主状态
                      badge 在同一行、flex-wrap 允许自然换行、gap 8px —— 徽标
                      不再孤零零漂到右侧。次级 gap 徽标（N 处未完成）仍属
                      title copy，视觉权重低于主状态徽标，用 operational 中性色
                      而非 evidence refuted 红。 */}
                  <div className="app__title-copy">
                    <h1>{snapshot?.task.question ?? t("载入中…")}</h1>
                    {snapshot ? (
                      <TaskStatusBadge status={snapshot.task.status} />
                    ) : null}
                    {/* The gap count is on the header of every tab. A researcher
                        must not be able to read a conclusion without seeing how
                        much of the protocol did not run. The badge counts; the
                        detail line names every concrete gap (which seats were
                        absent, which phases did not run or failed), so the
                        number is never a mystery. */}
                    {gapCount > 0 && brief ? (
                      <span
                        className="app__task-gaps"
                        title={`${t("缺席席位、未执行轮次与失败轮次的合计")}：${gapDetails(brief).join("；")}`}
                      >
                        <span className="app__gaps-badge">
                          {t("{0} 处未完成", gapCount)}
                        </span>
                        <span className="app__task-gaps-detail">
                          {gapDetails(brief).join("、")}
                        </span>
                      </span>
                    ) : null}
                  </div>
                  <div className="app__task-actions">
                    {/* 恢复/停止入口（round 修复）：FAILED/CANCELLED 提供
                        「重新研究」、PAUSED 提供「继续研究」—— 失败后不用再
                        新建任务就能让 worker 从 checkpoint 续跑；可运行态
                        提供「停止研究」。状态矩阵见 ResearchService，前端
                        不扩展。 */}
                    {snapshot &&
                    isStatusIn(RESUMEABLE_STATUSES, snapshot.task.status) ? (
                      snapshot.task.status === "PAUSED" ? (
                        <button
                          type="button"
                          className="app__resume app__action-primary"
                          onClick={() => void handleResume(taskId!)}
                          disabled={busyTask === taskId}
                          title={t("继续研究：将已暂停的任务交回队列，Worker 从 checkpoint 续跑")}
                        >
                          {busyTask === taskId ? t("请稍候…") : t("继续研究")}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="app__rerun app__action-primary"
                          onClick={() => void handleReResearch(taskId!)}
                          disabled={busyTask === taskId}
                          title={t("重新研究：从头克隆同题任务，或从第一个未完成阶段续跑")}
                        >
                          {busyTask === taskId ? t("请稍候…") : t("重新研究")}
                        </button>
                      )
                    ) : null}
                    {snapshot &&
                    STOPPABLE_STATUSES.has(snapshot.task.status) ? (
                      <button
                        type="button"
                        className="app__stop"
                        onClick={() => void handleStopResearch()}
                        disabled={busyTask === taskId}
                        title={t("停止研究：当前任务的议会运行将停止")}
                      >
                        {busyTask === taskId ? t("停止中…") : t("停止研究")}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="app__new"
                      onClick={handleNewResearch}
                      title={t("新建研究：回到首页开始一个全新的任务")}
                    >
                      {t("新建研究")}
                    </button>
                  </div>
                </div>

                <nav className="app__tabs" aria-label={t("视图")}>
                  {TABS.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      ref={(node) => {
                        tabRefs.current[item.id] = node;
                      }}
                      className={`app__tab${tab === item.id ? " app__tab--on" : ""}`}
                      aria-current={tab === item.id ? "page" : undefined}
                      onClick={() => selectTab(item.id)}
                    >
                      <span>{t(item.label)}</span>
                      <span className="app__tab-hint">{t(item.hint)}</span>
                    </button>
                  ))}
                  <span
                    className="app__tab-indicator"
                    style={
                      {
                        "--indicator-left": `${indicator.left}px`,
                        "--indicator-width": `${indicator.width}px`,
                      } as CSSProperties
                    }
                  />
                </nav>
              </div>

              {load === "loading" && !snapshot ? (
                <Spinner label={t("正在载入工作台快照…")} />
              ) : null}

              {load === "error" ? (
                <div className="app__error" role="alert">
                  <strong>{t("无法载入工作台")}</strong>
                  <p>{error}</p>
                  <p className="app__error-note">
                    {t(
                      "这不是「没有证据」，而是「读不到证据」。请确认 API 正在运行且 task_id 正确。",
                    )}
                  </p>
                </div>
              ) : null}

              {snapshot && brief ? (
                <>
                  {/* 检查点在「实时进展」tab 内由 LiveView 就地渲染（研究者
                      盯着进展页时不想离开去找输入框）；其他 tab 仍显示在
                      任务头下方，两处不重复。 */}
                  {taskId &&
                  snapshot.task.status === "AWAITING_COUNCIL_INPUT" &&
                  tab !== "live" ? (
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
                    {/* 单个视图渲染崩溃只落在自己的边界内，不再白屏整个
                        工作台；key 随 tab 重置，切换标签即恢复。 */}
                    <ErrorBoundary key={`view-boundary-${tab}`}>
                    {tab === "live" ? (
                      <LiveView
                        events={events}
                        processEvents={processEvents}
                        status={snapshot.task.status}
                        taskId={taskId}
                        seats={snapshot.seats}
                        queue={queue}
                        claims={brief?.confirmed_claims ?? []}
                        graph={snapshot.graph}
                        onGuidanceSubmitted={refresh}
                      />
                    ) : null}
                    {tab === "brief" ? (
                      <BriefView
                        brief={brief}
                        safety={snapshot.safety_notice}
                        graph={snapshot.graph}
                        onExport={exportBriefMarkdown}
                      />
                    ) : null}
                    {tab === "map" ? (
                      <MapView graph={snapshot.graph} taskId={taskId ?? ""} />
                    ) : null}
                    {tab === "council" ? (
                      <CouncilView
                        seats={snapshot.seats}
                        events={events}
                        consensus={snapshot.consensus}
                      />
                    ) : null}
                    {tab === "radar" ? (
                      <BlindspotRadarView
                        blindspots={snapshot.blindspots}
                        claims={brief.confirmed_claims}
                        graph={snapshot.graph}
                      />
                    ) : null}
                    {tab === "evolution" ? (
                      <EvolutionView
                        entries={snapshot.evolution}
                        claims={brief.confirmed_claims}
                        graph={snapshot.graph}
                      />
                    ) : null}
                    {tab === "audit" ? (
                      <AuditView events={events} streamOpen={stream === "open"} />
                    ) : null}
                    {tab === "paper" ? (
                      <PaperView
                        paper={snapshot.paper}
                        onExport={exportPaperMarkdown}
                        onViewBrief={() => selectTab("brief")}
                      />
                    ) : null}
                    {tab === "knowledge" ? <KnowledgeBaseView /> : null}
                    {tab === "followup" && taskId ? (
                      <FollowUpView taskId={taskId} status={snapshot.task.status} />
                    ) : null}
                    {tab === "lineage" ? (
                      <LineageView lineage={snapshot.lineage} />
                    ) : null}
                    {tab === "tools" && taskId ? (
                      <ResearchToolsView
                        taskId={taskId}
                        snapshot={snapshot}
                        onChanged={refresh}
                        onOpenTask={open}
                      />
                    ) : null}
                    {tab === "annotations" && taskId ? (
                      <AnnotationView taskId={taskId} snapshot={snapshot} />
                    ) : null}
                    </ErrorBoundary>
                  </div>
                </>
              ) : null}
            </>
          )}
        </main>

        <aside className="app__sidebar">
          <ModelSettingsPanel />
          <SkillsPanel />
        </aside>
      </div>
    </div>
  );
}
