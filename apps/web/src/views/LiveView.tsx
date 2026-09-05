/** 实时进展：运行中议会的思考链路可视化（CLAUDE.md 11）。
 *
 * 网页版要让研究者实时知道模型在干什么：数据来自两条流 —— 账本阶段事件
 * （PHASE_STARTED/PHASE_COMPLETED 驱动阶段时间线）与 process_stream 过程流
 * （结构化席位动作、工具调用、检索结果、模型思考片段，独立连接）。
 *
 * 渲染纪律（CLAUDE.md 11）：展示结构化过程轨迹 —— 阶段推进、席位运行
 * 状态、检索与文献链接、议会动作，以及各席位流式输出的思考过程
 * （round-12 恢复：model_reasoning / model_token 片段实时聚合）。任何片段
 * 都是过程数据而非正式证据，重连后从服务端重放并由 seq 去重，不作为
 * 审计依据；正式结论以 Research Brief 与最终论文为准。
 */

import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ConfirmedClaim,
  EvidenceGraph,
  LedgerEvent,
  ProcessEvent,
  Seat,
  SeatSummary,
} from "../api/types";
import { SEATS, SEAT_LABELS } from "../api/types";
import { Empty } from "../components/primitives";
import { t } from "../i18n";
import {
  buildClaimLabels,
  humanizeText,
  replaceClaimUuids,
} from "./claimLabels";
import { CheckpointGate } from "./CheckpointGate";

import "./LiveView.css";

/** 八个阶段，与 packages/epistemo/contracts.py 的 PHASE_SEQUENCE 一致。 */
const PHASES: { id: string; label: string }[] = [
  { id: "PRECOMMITMENT", label: "独立预承诺" },
  { id: "ACQUISITION", label: "专业取证" },
  { id: "EVIDENCE_EXCHANGE", label: "证据交换" },
  { id: "CROSS_EXAMINATION", label: "交叉质询" },
  { id: "BLINDSPOT_BOUNTY", label: "盲点悬赏" },
  { id: "JOINT_MODELING", label: "联合建模" },
  { id: "FINAL_REJUDGMENT", label: "最终复判" },
  { id: "REPORTING", label: "报告生成" },
];

/** 议会结构化动作的中文呈现 —— 预承诺、质询、复判是议会「过程」的
 * 骨架，与 token 流互补：token 是思考的原料，动作是思考的产物。 */
const ACTION_META: Record<
  string,
  { label: string; tone: "admitted" | "provisional" | "refuted" | "unknown" }
> = {
  PRECOMMITMENT_SEALED: { label: "预承诺", tone: "admitted" },
  CHALLENGE_RAISED: { label: "质询", tone: "refuted" },
  FINAL_JUDGMENT: { label: "复判", tone: "admitted" },
  CONFIDENCE_UPDATED: { label: "置信度调整", tone: "provisional" },
  EVIDENCE_REQUESTED: { label: "证据请求", tone: "unknown" },
  SEAT_UNAVAILABLE: { label: "席位缺席", tone: "unknown" },
};

/** 把账本事件折叠成一句可读的议会动作摘要。不是投票记录 —— 议会
 * 禁止多数投票裁决科研真理（CLAUDE.md 4），这里展示的是每位科学家
 * 的预承诺、质询与复判，以及它们引用的证据。
 *
 * ``labels``（claim_id → 「主张：…」）把进展里出现的 UUID 换成研究者
 * 看得懂的主张文本（round-8 用户反馈：裸 UUID 无法阅读）。识别不出的
 * UUID 保留原样 —— 绝不臆造（claimLabels.ts）。 */
function actionSummary(
  event: LedgerEvent,
  labels: Map<string, string>,
): { meta: string; tone: string; body: string } | null {
  const payload = event.payload as Record<string, unknown>;
  const meta = ACTION_META[event.kind];
  if (!meta) return null;
  const seat =
    typeof payload.seat === "string"
      ? (SEAT_LABELS[payload.seat as Seat] ?? payload.seat)
      : null;
  // Translation keys use {n} placeholders so the dictionaries can cover the
  // template; the values are substituted after lookup (see i18n/index.ts).
  switch (event.kind) {
    case "PRECOMMITMENT_SEALED": {
      const confidence = String(payload.confidence ?? t("未记录"));
      const updateCondition = humanizeText(
        String(payload.update_condition ?? ""),
      );
      const initialJudgment = replaceClaimUuids(
        humanizeText(String(payload.initial_judgment ?? "")),
        labels,
      );
      // 主要观点优先展示 —— 这是「科学家在质询开始前说了什么」，是完整
      // 进展链路的起点（round-8 用户反馈：不能只有质询）。
      if (seat && initialJudgment) {
        return {
          meta: meta.label,
          tone: meta.tone,
          body: t("{0} 预承诺：{1}（置信度 {2}）", seat, initialJudgment, confidence),
        };
      }
      return {
        meta: meta.label,
        tone: meta.tone,
        body: seat
          ? updateCondition
            ? t("{0} 预承诺置信度 {1}，更新条件：{2}", seat, confidence, updateCondition)
            : t("{0} 预承诺置信度 {1}", seat, confidence)
          : t("预承诺置信度 {0}", confidence),
      };
    }
    case "CHALLENGE_RAISED": {
      const statement = replaceClaimUuids(
        humanizeText(String(payload.statement ?? "")),
        labels,
      );
      const fatal = payload.is_fatal === true ? t("（致命）") : "";
      const claimId =
        typeof payload.claim_id === "string" ? payload.claim_id : null;
      // 质询必须说明「针对什么」——claim_id 若指名一个已知主张，换成
      // 「主张：…」，让质询与被质询者之间有来有回（round-8 用户反馈）。
      const target = claimId
        ? `，针对 ${replaceClaimUuids(claimId, labels)}`
        : "";
      return {
        meta: meta.label,
        tone: meta.tone,
        body: seat
          ? t("{0} 质询：{1}{2}{3}", seat, statement, fatal, target)
          : t("质询：{0}{1}{2}", statement, fatal, target),
      };
    }
    case "FINAL_JUDGMENT": {
      const judgment = String(payload.final_judgment ?? "");
      const confidence = String(payload.confidence ?? t("未记录"));
      const dissent = payload.has_dissent === true ? t(" · 附异议") : "";
      return {
        meta: meta.label,
        tone: meta.tone,
        body: seat
          ? t("{0} 复判「{1}」置信度 {2}{3}", seat, judgment, confidence, dissent)
          : t("复判「{0}」置信度 {1}{2}", judgment, confidence, dissent),
      };
    }
    case "CONFIDENCE_UPDATED": {
      // 置信度调整的说明（如「作为对 {claim} 的分支主张被提出」）里的
      // UUID 换成主张文本；内部标识串（如 ACQUISITION:no_tool_provider）
      // 换成中文。识别不出的 UUID 保留原样。
      const note = replaceClaimUuids(
        humanizeText(String(payload.confidence_delta_note ?? "")),
        labels,
      );
      return { meta: meta.label, tone: meta.tone, body: note };
    }
    case "EVIDENCE_REQUESTED":
      return {
        meta: meta.label,
        tone: meta.tone,
        body: seat ? t("{0} 请求补充证据", seat) : t("请求补充证据"),
      };
    case "SEAT_UNAVAILABLE": {
      // 缺席原因来自账本事件本身（worker 记录的真实失败原因，如连接
      // 错误、401、schema 修复失败）——「席位缺席」必须说明缺席为什么，
      // 否则研究者无法区分「没有配置模型」与「模型调用失败」。
      const reason = humanizeText(String(payload.reason ?? ""));
      const clipped = reason.length > 90 ? `${reason.slice(0, 90)}…` : reason;
      const phase = String(payload.phase ?? "");
      return {
        meta: meta.label,
        tone: meta.tone,
        body: seat
          ? t("{0} {1}{2}{3}", seat, phase ? `${phase} ${t("缺席")}` : t("缺席"), clipped ? `：${clipped}` : "", "")
          : t("席位缺席{0}", clipped ? `：${clipped}` : ""),
      };
    }
    default:
      return null;
  }
}

/** 从账本事件推导每个阶段的完成度：有 PHASE_COMPLETED 即完成；最后一个
 * PHASE_STARTED 且未完成的是当前阶段；REPORTING 的 COMPLETED 无后续。 */
function phaseProgress(events: LedgerEvent[]): {
  current: string | null;
  done: Set<string>;
} {
  const started: string[] = [];
  const done = new Set<string>();
  for (const event of events) {
    const phase = (event.payload as Record<string, unknown>).phase;
    if (typeof phase !== "string") continue;
    if (event.kind === "PHASE_STARTED") started.push(phase);
    else if (event.kind === "PHASE_COMPLETED") done.add(phase);
  }
  let current: string | null = null;
  for (let index = started.length - 1; index >= 0; index -= 1) {
    const phase = started[index];
    if (phase === undefined) continue;
    if (!done.has(phase)) {
      current = phase;
      break;
    }
  }
  return { current, done };
}

/** 每个席位最近的「一段」思考流：从最近的 seat_deliberation 起聚合
 * reasoning/token 片段，至最近的 model_done 截断。倒序遍历取最后一段。
 * ``seat_working`` 是模型调用期间服务器发的心跳（elapsed = 已等待秒数），
 * 供「思考中… 已等待 Ns」显示：一次调用卡住时，前端不再只有死寂的
 * 「思考中…」，而是能看见它已经等了多久。
 *
 * 思考片段（model_reasoning / model_token）是过程数据，不是正式证据：它
 * 随模型调用实时流动，重连后从服务端重放并按 seq 去重。展示它们是为了
 * 让研究者实时看见七位科学家正在想什么（round-12 恢复），但任何片段都
 * 不是正式结论 —— 正式结论以 Research Brief 与最终论文为准。 */
/** 一个席位的思考流状态。``absent`` 由 ``seat_absent`` 事件置位（round-15：
 * 模型调用失败或被研究者停止时后端补发，关闭该段思考 —— 否则前端会
 * 永远停在「思考中…」，因为没有任何事件能把 running 置 false）。 */
interface SeatSlice {
  phase: string;
  text: string;
  running: boolean;
  elapsed: number;
  absent: boolean;
  absentReason: string;
}

function seatStreams(processEvents: ProcessEvent[]): Record<string, SeatSlice> {
  const result: Record<string, SeatSlice> = {};
  const current: Record<
    string,
    {
      phase: string;
      parts: string[];
      running: boolean;
      elapsed: number;
      absent: boolean;
      absentReason: string;
    }
  > = {};
  // 仅凭席位事件即可懒开 slice 的种类：当 seat_deliberation 锚点被重放
  // 窗口挤出（旧构建/超长 token 流）时，第一个到达的席位事件也能把卡片
  // 救回来，席位不会因为锚点丢失而永久消失。
  const recoverable = new Set([
    "model_reasoning",
    "model_token",
    "model_done",
    "seat_absent",
    "seat_working",
  ]);
  for (const event of processEvents) {
    const payload = event.payload as Record<string, unknown>;
    const seat = typeof payload.seat === "string" ? payload.seat : null;
    if (event.kind === "seat_deliberation" && seat) {
      // 新一段思考开始：缺席标记随段重置 —— 上一个阶段缺席不代表这个
      // 阶段也缺席（round-15）。
      current[seat] = {
        phase: typeof payload.phase === "string" ? payload.phase : "",
        parts: [],
        running: true,
        elapsed: 0,
        absent: false,
        absentReason: "",
      };
      continue;
    }
    if (!seat) continue;
    if (!current[seat]) {
      if (!recoverable.has(event.kind)) continue;
      current[seat] = {
        phase: typeof payload.phase === "string" ? payload.phase : "",
        parts: [],
        running: event.kind !== "model_done" && event.kind !== "seat_absent",
        elapsed: 0,
        absent: event.kind === "seat_absent",
        absentReason:
          event.kind === "seat_absent" ? String(payload.reason ?? "") : "",
      };
    }
    const slice = current[seat];
    const text = typeof payload.text === "string" ? payload.text : "";
    if (event.kind === "model_reasoning" || event.kind === "model_token") {
      if (text) slice.parts.push(text);
    } else if (event.kind === "model_done") {
      slice.running = false;
    } else if (event.kind === "seat_working") {
      const elapsed = typeof payload.elapsed === "number" ? payload.elapsed : 0;
      slice.elapsed = elapsed;
    } else if (event.kind === "seat_absent") {
      // 思考结束但没有产出：模型调用失败（缺席）或被研究者停止。关闭
      // 本段思考 —— 没有这个事件，前端会永远显示「思考中… 已等待
      // Ns」（round-15 生产故障）。
      slice.running = false;
      slice.absent = true;
      slice.absentReason = String(payload.reason ?? "");
    }
  }
  for (const [seat, entry] of Object.entries(current)) {
    const joined = entry.parts.join("");
    result[seat] = {
      phase: entry.phase,
      // Bound the rendered thinking text: a long phase streamed tens of
      // thousands of token deltas; an unbounded string turned into one giant
      // DOM node was itself enough to jank/freeze the pane. Keep the newest
      // slice, which is what the live card shows while streaming.
      text:
        joined.length > SEAT_TEXT_RENDER_CAP
          ? joined.slice(joined.length - SEAT_TEXT_RENDER_CAP)
          : joined,
      running: entry.running,
      elapsed: entry.elapsed,
      absent: entry.absent,
      absentReason: entry.absentReason,
    };
  }
  return result;
}

/** Max characters of one seat's live thinking rendered in its card. */
const SEAT_TEXT_RENDER_CAP = 24_000;

/** 一个席位连续无进展多久后提示「可能卡住」（秒）。阈值与后端模型调用
 * 总 deadline（240s）+ 重试余量对齐：超过 300s 而仍在 running，说明
 * 调用链已越过所有正常超时，即将被 watchdog 中断。 */
/** 过程流里没有该席位的实时片段时，用 snapshot 里的持久席位摘要兜底，
 *  让「断点续研后七位科学家整体消失」不再发生：实时输出缺席 ≠ 席位不存在。
 *  返回状态徽标文案与样式基调（复用 live__seat-idle/live__seat-absent）。 */
function durablePill(
  summary: SeatSummary | null,
  status: string | undefined,
  currentPhase: string | null,
): { label: string; tone: "idle" | "done" | "absent" } {
  const terminal =
    status === "COMPLETED" ||
    status === "COMPLETED_WITH_GAPS" ||
    status === "FAILED" ||
    status === "CANCELLED";
  if (status === "QUEUED") return { label: t("排队中"), tone: "idle" };
  if (status === "AWAITING_COUNCIL_INPUT")
    return { label: t("等待方向性引导"), tone: "idle" };
  if (
    currentPhase &&
    summary?.unavailable_phases.some((phase) => phase === currentPhase)
  )
    return { label: t("本阶段缺席"), tone: "absent" };
  if (terminal) {
    if (summary?.final_judgment) return { label: t("已完成复判"), tone: "done" };
    if (summary?.precommitment) return { label: t("已提交预承诺"), tone: "done" };
    if (summary && summary.unavailable_phases.length > 0)
      return { label: t("全程缺席"), tone: "absent" };
    return { label: t("无席位输出"), tone: "absent" };
  }
  return { label: t("等待本阶段输出…"), tone: "idle" };
}

const SEAT_STUCK_WARN_SECONDS = 60;
const SEAT_STUCK_CRITICAL_SECONDS = 300;

/** 检索等待的时间阈值。WARN 对齐后端每查询上限 45s（acquisition.py 的
 * ACQUISITION_PER_QUERY_SECONDS）；CRITICAL 对齐整轮上限 600s。超过即
 * 警示「将超时」，让等待有界可见，而不是看起来永久卡住。 */
const TOOL_STUCK_WARN_SECONDS = 45;
const TOOL_STUCK_CRITICAL_SECONDS = 300;

/** 未命中原因里的 URL（OpenAlex 的 404 地址、Mozilla 文档页等），提取
 * 出来作为可点击链接 —— 原始原因整段塞进卡片会撑破方框，且那一串
 * repr 对读者没有意义。 */
const REASON_URL_RE = /https?:\/\/[^\s"')]+/g;

/** 未命中原因里可能带上的 DOI（模型常把 "doi:10.xxxx 请核对其中是否…"
 * 粘在一起；lookup 失败时 reason 里残留这段），提取出来生成一个真正
 * 有用的跳转 —— doi.org 解析页 —— 而不是只给一个 404 地址。 */
const REASON_DOI_RE = /10\.\d{4,9}\/[0-9A-Za-z._;()/:+-]+/g;

/** 方框内一行内放得下的原因文本长度。完整原因保留在 title 里。 */
const REASON_MAX_CHARS = 80;

/** 把一次未命中的原始原因压缩成可读的一行：剥离 URL，正文截断到
 * REASON_MAX_CHARS，URL 与 DOI 单独返回供渲染成链接。CLAUDE.md 11 只让
 * 界面展示结构化内容 —— 一屏报错栈不算。 */
function compactReason(reason: string): {
  text: string;
  urls: string[];
  dois: string[];
} {
  const urls = Array.from(new Set(reason.match(REASON_URL_RE) ?? []));
  const dois = Array.from(new Set(reason.match(REASON_DOI_RE) ?? []));
  const stripped = reason.replace(REASON_URL_RE, " ").replace(/\s+/g, " ").trim();
  const text =
    stripped.length > REASON_MAX_CHARS
      ? `${stripped.slice(0, REASON_MAX_CHARS)}…`
      : stripped;
  return { text, urls, dois };
}

/** 未命中原因行：一行压缩文本 + 可点击的 doi.org / 原始 URL 链接。
 * 完整原因在 title 中，悬停可审计 —— 卡片永不被长文本撑破。 */
function MissReason({ reason }: { reason?: string }) {
  if (!reason) return null;
  const { text, urls, dois } = compactReason(reason);
  return (
    <span className="live__tool-miss" title={reason}>
      {text ? <span className="live__tool-miss-text">（{text}）</span> : null}
      {dois.map((doi) => (
        <a
          key={`doi:${doi}`}
          className="live__tool-link"
          href={`https://doi.org/${doi}`}
          target="_blank"
          rel="noreferrer"
        >
          {t("查看该文献 ↗")}
        </a>
      ))}
      {urls.map((url) => (
        <a
          key={url}
          className="live__tool-link"
          href={url}
          target="_blank"
          rel="noreferrer"
        >
          {t("错误详情 ↗")}
        </a>
      ))}
    </span>
  );
}

/** 检索卡片「等待结果…」：显示已等待秒数（由每秒 tick 驱动），并在越过
 * 服务端每查询超时阈值后警示。服务端有 45s/600s 硬上限与 watchdog，这里
 * 只是把「卡住」变成可读的倒计时。 */
function ToolPending({ group }: { group: { startedAt: number } }) {
  const elapsed = Math.max(0, Math.floor((performance.now() - group.startedAt) / 1000));
  const critical = elapsed >= TOOL_STUCK_CRITICAL_SECONDS;
  const warn = elapsed >= TOOL_STUCK_WARN_SECONDS;
  return (
    <p
      className={
        "live__tool-empty" +
        (critical
          ? " live__tool-empty--critical"
          : warn
            ? " live__tool-empty--warn"
            : "")
      }
    >
      {critical
        ? t("检索长时间未返回（已等待 {0}s），系统将自动中断", elapsed)
        : t("等待结果… 已等待 {0}s", elapsed)}
    </p>
  );
}

/** 检索/文献卡片里的科学家徽标组：多个科学家共享同一次动作时折叠为
 * 「第一个 + …」，悬停显示全部 —— 检索与文献的科学家列表是全宽平铺的
 * 长字符串，多个席位挤在一起时字都看不见（用户反馈）。单个科学家直接
 * 显示；全部通过 title 悬停提示与一个 visually-hidden 的可读文本给到。
 */
function SeatCluster({ seats }: { seats: string[] }) {
  const labelled = seats.map((seat) => SEAT_LABELS[seat as Seat] ?? seat);
  if (labelled.length === 1) {
    return <span className="live__tool-seats mono">{labelled[0]}</span>;
  }
  const preview = `${labelled[0]} +${labelled.length - 1}`;
  const full = labelled.join("、");
  return (
    <span
      className="live__tool-seats mono live__tool-seats--cluster"
      data-seats={full}
      title={full}
      aria-label={full}
    >
      {preview}
    </span>
  );
}

/** 排队中的队列信息（App.tsx 轮询计算后传入）：本任务前面还有几个
 * 任务、Worker 当前正在跑哪个、已跑多久。 */
export interface QueueInfo {
  ahead: number;
  running: { question: string; minutes: number } | null;
}

export function LiveView({
  events,
  processEvents,
  status,
  taskId,
  seats,
  queue,
  claims,
  graph,
  onGuidanceSubmitted,
}: {
  events: LedgerEvent[];
  processEvents: ProcessEvent[];
  /** 任务状态：QUEUED 时展示排队说明，而不是让研究者以为没反应。 */
  status?: string;
  /** 方向性检查点（AWAITING_COUNCIL_INPUT）时在实时进展页就地提交。 */
  taskId?: string;
  seats?: SeatSummary[];
  /** 队列可见性：为什么「已入队」却迟迟不开始（round-6）。 */
  queue?: QueueInfo | null;
  /** 已确认主张与证据图，用于把进展里的 UUID 换成可读的「主张：…」。 */
  claims?: ConfirmedClaim[];
  graph?: EvidenceGraph | null;
  onGuidanceSubmitted?: () => void;
}) {
  const { current, done } = useMemo(() => phaseProgress(events), [events]);
  const streams = useMemo(() => seatStreams(processEvents), [processEvents]);
  // 断点续研/重连后过程流可能暂时没有 seat_deliberation：用 snapshot 的持久
  // 席位摘要兜底渲染七张卡片，实时片段一到就自动升级为流式卡片。
  const phaseStarted = current !== null || done.size > 0;
  const durableById = useMemo(() => {
    const map = new Map<string, SeatSummary>();
    for (const summary of seats ?? []) map.set(summary.seat, summary);
    return map;
  }, [seats]);
  // 七张席位卡按固定顺序排列：有实时片段就渲染流式卡，否则渲染持久兜底
  // 卡，实时片段一到自动原位升级 —— 卡片不再重排、不再整列消失。
  const visibleSeatIds = useMemo(
    () =>
      SEATS.filter(
        (seat) =>
          streams[seat] !== undefined ||
          (status !== "QUEUED" &&
            (phaseStarted || durableById.has(seat))),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [streams, seats, status, current, done.size],
  );
  // 每秒重渲染一次，让「思考中… 已等待 Ns」与检索卡片的秒数走动。只在
  // 还有席位 running 或有检索 pending 时启动定时器，空闲时不空转。
  const [, setTick] = useState(0);
  const anyRunning = useMemo(
    () => Object.values(streams).some((entry) => entry.running),
    [streams],
  );

  // 思考流自动滚底（round-12 恢复）：新片段到达时把每个有流的席位面板滚
  // 到底 —— 研究者在看「现在」，不是往回翻。手动上滚会被下一次 flush
  // 拉回，这是实时视图的取舍。
  const streamRefs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    for (const ref of Object.values(streamRefs.current)) {
      if (ref) ref.scrollTop = ref.scrollHeight;
    }
  }, [processEvents.length]);
  const claimLabels = useMemo(
    () => buildClaimLabels(claims ?? [], graph ?? { nodes: [], edges: [] }),
    [claims, graph],
  );
  const actions = useMemo(
    () =>
      events
        .map((event) => actionSummary(event, claimLabels))
        .filter(
          (item): item is { meta: string; tone: string; body: string } => item !== null,
        ),
    [events, claimLabels],
  );

  // 检索与文献：一次检索（tool_call）与其结果（tool_result）配对成一个
  // 卡片组，多列平铺。分组而不是逐条平铺，是因为一次检索的多个命中
  // 属于同一个动作——把结果拆散成独立条目会让「这次检索找到了什么」
  // 无法从布局上直接读出。citation_count 是权威度信号（检索按被引量
  // 排序），缺失时显示为 undefined，卡片不渲染徽标。
  // 卡死感知：pending 组（只有 tool_call 没有 tool_result）显示已等待秒数
  // ——检索有 45s 每查询 / 600s 整轮的服务端硬上限，前端把「等待」变成
  // 可见的倒计时，而不是一片死寂的「等待结果…」；miss 结果带 reason 时
  // 说明这次检索为什么没命中（超时/预算/撤回），而不是让读者自己猜。
  interface ToolGroup {
    kind: string;
    query: string;
    seats: string[];
    startedAt: number;
    results: {
      url: string | null;
      title: string;
      miss: boolean;
      reason?: string;
      citationCount?: number;
    }[];
  }
  const toolGroups = useMemo(() => {
    const groups: ToolGroup[] = [];
    let current: ToolGroup | null = null;
    for (const event of processEvents) {
      const payload = event.payload as Record<string, unknown>;
      if (event.kind === "tool_call") {
        current = {
          kind: payload.kind === "doi_lookup" ? t("DOI 解析") : t("检索"),
          query: String(payload.query ?? ""),
          seats: Array.isArray(payload.seats)
            ? payload.seats.map(String)
            : [],
          // performance.now() is monotonic (unaffected by clock jumps), so a
          // resumed session's elapsed can't go negative or jump.
          startedAt: performance.now(),
          results: [],
        };
        groups.push(current);
      } else if (event.kind === "tool_result" && current) {
        const citationCount =
          typeof payload.citation_count === "number" && payload.citation_count > 0
            ? payload.citation_count
            : undefined;
        current.results.push({
          url: typeof payload.url === "string" ? payload.url : null,
          title: String(payload.title ?? payload.doi ?? ""),
          miss: payload.miss === true,
          reason: typeof payload.reason === "string" ? payload.reason : undefined,
          citationCount,
        });
      }
    }
    return groups;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processEvents, t]);

  // 检索 pending 时也驱动每秒 tick（与席位 running 并列的第二种等待）。
  const anyToolPending = useMemo(
    () => toolGroups.some((group) => group.results.length === 0),
    [toolGroups],
  );
  useEffect(() => {
    if (!anyRunning && !anyToolPending) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [anyRunning, anyToolPending]);

  // 阶段切换时把当前阶段 pill 滚进视野：阶段行可换行，研究者不能被
  // 一个刚刚开始的阶段留在屏幕之外（reduced-motion 时不做平滑滚动）。
  const currentRef = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!current) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    currentRef.current?.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
      block: "nearest",
      inline: "center",
    });
  }, [current]);

  const anyTrace = processEvents.length > 0;

  return (
    <div className="live">
      <section className="live__phases" aria-label={t("八阶段进度")}>
        {PHASES.map((phase) => {
          const isDone = done.has(phase.id);
          const isCurrent = current === phase.id;
          return (
            <span
              key={phase.id}
              ref={isCurrent ? currentRef : undefined}
              className={
                "live__phase" +
                (isDone ? " live__phase--done" : "") +
                (isCurrent ? " live__phase--current" : "")
              }
              title={t(phase.label)}
            >
              {isDone ? "✓ " : ""}
              {t(phase.label)}
            </span>
          );
        })}
      </section>

      {!anyTrace && status === "QUEUED" ? (
        /* 排队 ≠ 没反应：任务还没有任何过程事件，但状态是「在队列里等
           worker」。round-6 报告「已入队后迟迟无响应」的根因是队列里别的
           任务把单 worker 占满——所以这里必须说清楚：前面还有几个、Worker
           正在跑哪个、跑了多久，而不是留一句干巴巴的「已入队」。 */
        <div className="live__queued" role="status">
          <span className="live__queued-badge">{t("排队中")}</span>
          <p className="live__queued-title">{t("任务已入队，等待 Worker 认领")}</p>
          {queue?.running ? (
            <p className="live__queued-note">
              {t(
                "Worker 当前正在运行：「{0}」（已运行约 {1} 分钟）。每个任务最长运行约 60 分钟，它结束后队列自动推进。",
                queue.running.question,
                queue.running.minutes,
              )}
            </p>
          ) : null}
          {queue && queue.ahead > 0 ? (
            <p className="live__queued-note">
              {t(
                "本任务前面还有 {0} 个任务在排队；如队列中有不再需要的任务，可在「会话历史」中删除，队列会立即推进。",
                queue.ahead,
              )}
            </p>
          ) : null}
          <p className="live__queued-note">
            {t(
              "本任务开始运行后，这里会自动显示七位科学家的思考、检索与议会动作，无需手动刷新。盲点悬赏结束后会到达方向性检查点，届时可在此提交备注调整后续讨论重点（不进入任何证据判定）。",
            )}
          </p>
        </div>
      ) : (
        <>
          {/* 研究者干预窗口：唯一、固定的方向性检查点（CLAUDE.md 4.1）。
              说明它的边界 —— 可以调整讨论重点，不能影响任何判定。 */}
          {status === "AWAITING_COUNCIL_INPUT" && taskId && seats ? (
            <CheckpointGate
              taskId={taskId}
              seats={seats}
              onSubmitted={() => {
                onGuidanceSubmitted?.();
              }}
            />
          ) : status && !anyTrace ? (
            <p className="live__checkpoint-note">
              {t(
                "研究者干预窗口：盲点悬赏结束后会到达方向性检查点，届时可在本页提交备注调整后续讨论重点 —— 备注不进入任何证据判定，也不改变异议保留（CLAUDE.md 4.1）。",
              )}
            </p>
          ) : null}

          <div className="live__grid">
            {/* 左列：席位思考流在上，检索与文献在下方 —— 研究者先看科学家
                在做什么，再看他们检索到了什么（用户要求：文献区放在主界面
                下方，一行多列铺开，而不是挤在右侧小栏里）。 */}
            <div className="live__main">
              <section className="live__seats" aria-label={t("席位运行状态")}>
                {visibleSeatIds.map((seat) => {
                  const entry = streams[seat];
                  if (entry) {
                    return (
                      <div key={seat} className="live__seat">
                        <div className="live__seat-head">
                          <span className="live__seat-name">
                            {SEAT_LABELS[seat as Seat] ?? seat}
                          </span>
                          {entry.phase ? (
                            <span className="live__seat-phase mono">{entry.phase}</span>
                          ) : null}
                          {entry.running ? (
                            <span
                              className={
                                entry.elapsed >= SEAT_STUCK_CRITICAL_SECONDS
                                  ? "live__seat-running live__seat-running--critical"
                                  : entry.elapsed >= SEAT_STUCK_WARN_SECONDS
                                    ? "live__seat-running live__seat-running--slow"
                                    : "live__seat-running"
                              }
                            >
                              {entry.elapsed >= SEAT_STUCK_CRITICAL_SECONDS
                                ? t("模型长时间无响应（已等待 {0}s），系统将自动中断", entry.elapsed)
                                : t("思考中… 已等待 {0}s", entry.elapsed)}
                            </span>
                          ) : entry.absent ? (
                            /* round-15：思考结束但没有产出（调用失败或被停止）。
                               原因悬停可见，与账本 SEAT_UNAVAILABLE 语义一致。 */
                            <span
                              className="live__seat-running live__seat-absent"
                              title={entry.absentReason || undefined}
                            >
                              {t("缺席")}
                            </span>
                          ) : (
                            <span className="live__seat-idle">{t("已完成")}</span>
                          )}
                        </div>
                        {/* round-12 恢复：流式思考过程。过程数据，非正式证据——
                           正式结论以 Research Brief 与最终论文为准。 */}
                        {entry.text ? (
                          <div
                            className="live__seat-stream"
                            ref={(node) => {
                              streamRefs.current[seat] = node;
                            }}
                          >
                            <p className="live__seat-stream-text">{entry.text}</p>
                          </div>
                        ) : entry.running ? (
                          <p className="live__seat-stream live__seat-stream--empty">
                            {t("（尚无输出）")}
                          </p>
                        ) : null}
                      </div>
                    );
                  }
                  // 持久兜底卡片：过程流暂时没有该席位片段（断点续跑重连、
                  // 重放尾部不含锚点）时，席位也固定在自己的位置上，不会消失
                  // 或重排；实时片段一到即在原位升级为流式卡。
                  const summary = durableById.get(seat) ?? null;
                  const pill = durablePill(summary, status, current);
                  const notes: string[] = [];
                  if (summary?.precommitment?.confidence != null)
                    notes.push(
                      t("预承诺置信度 {0}", summary.precommitment.confidence),
                    );
                  if (summary && summary.challenges_raised.length > 0)
                    notes.push(t("已提出 {0} 项质询", summary.challenges_raised.length));
                  if (summary?.final_judgment) notes.push(t("已提交最终复判"));
                  if (summary && summary.unavailable_phases.length > 0) {
                    const phaseLabels = PHASES.filter((phase) =>
                      summary.unavailable_phases.includes(phase.id),
                    ).map((phase) => t(phase.label));
                    notes.push(t("缺席阶段：{0}", phaseLabels.join("、")));
                  }
                  return (
                    <div
                      key={`durable-${seat}`}
                      className="live__seat live__seat--durable"
                    >
                      <div className="live__seat-head">
                        <span className="live__seat-name">
                          {SEAT_LABELS[seat as Seat] ?? seat}
                        </span>
                        {current ? (
                          <span className="live__seat-phase mono">{current}</span>
                        ) : null}
                        <span
                          className={
                            pill.tone === "absent"
                              ? "live__seat-running live__seat-absent"
                              : "live__seat-idle"
                          }
                        >
                          {pill.label}
                        </span>
                      </div>
                      {notes.length > 0 ? (
                        <ul className="live__seat-durable">
                          {notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  );
                })}
                {visibleSeatIds.length === 0 ? (
                  phaseStarted ? (
                    <Empty>
                      {t(
                        "议会正在运行，席位实时输出即将出现；已完成阶段的结论可在「研究简报」查看。",
                      )}
                    </Empty>
                  ) : (
                    <Empty>{t("还没有席位开始运行。")}</Empty>
                  )
                ) : null}
              </section>

              <section className="live__tools" aria-label={t("检索与文献")}>
                <h3>{t("检索与文献")}</h3>
                {toolGroups.length === 0 ? (
                  <Empty>{t("还没有检索活动。")}</Empty>
                ) : (
                  <div className="live__tool-grid">
                    {toolGroups.map((group, index) => (
                      <div key={index} className="live__tool-card">
                        <div className="live__tool-card-head">
                          <span className="live__tool-kind mono">{group.kind}</span>
                          <span className="live__tool-query" title={group.query}>
                            {group.query}
                          </span>
                          {group.seats.length > 0 ? (
                            <SeatCluster seats={group.seats} />
                          ) : null}
                        </div>
                        {group.results.length === 0 ? (
                          <ToolPending group={group} />
                        ) : group.results.every((result) => result.miss) ? (
                          <p className="live__tool-empty live__tool-empty--miss">
                            {t("未命中")}
                            <MissReason reason={group.results[0]?.reason} />
                          </p>
                        ) : (
                          <ul className="live__tool-results">
                            {group.results.map((result, resultIndex) => (
                              <li key={resultIndex} className="live__tool-result">
                                {result.miss ? (
                                  <span className="live__tool-title live__tool-title--miss">
                                    {result.title || t("未命中")}
                                  </span>
                                ) : (
                                  <>
                                    <span className="live__tool-title">
                                      {result.title}
                                    </span>
                                    <span className="live__tool-meta">
                                      {result.citationCount !== undefined ? (
                                        <span
                                          className="live__tool-citations"
                                          title={t("被引次数（权威度信号）")}
                                        >
                                          {t("被引 {0}", result.citationCount)}
                                        </span>
                                      ) : null}
                                      {result.url ? (
                                        <a
                                          className="live__tool-link"
                                          href={result.url}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          {t("打开来源 ↗")}
                                        </a>
                                      ) : null}
                                    </span>
                                  </>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {/* 议会动作：不再挤在 340px 右栏 —— 放在检索与文献下方全宽
                  展开，预承诺/质询/复判的完整正文才有编排空间（用户要求：
                  议会动作放到检索文献板块下面，其他板块相应增宽）。 */}
              <section className="live__actions" aria-label={t("议会动作")}>
                <h3>{t("议会动作")}</h3>
                {actions.length === 0 ? (
                  <Empty>{t("还没有结构化动作。")}</Empty>
                ) : (
                  <ol className="live__action-log">
                    {actions.map((item, index) => (
                      <li key={index} className="live__action">
                        <span
                          className={`live__action-kind live__action-kind--${item.tone}`}
                        >
                          {t(item.meta)}
                        </span>
                        <span className="live__action-body">{item.body}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
