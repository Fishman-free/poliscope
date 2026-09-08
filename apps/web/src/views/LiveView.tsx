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
 *
 * round-18 修复（用户反馈：科学家卡片顺序错乱、完成后正文空白、输出框
 * 折叠、检索卡「已等待 0s」与永久 pending、长时间运行后页面卡死）：
 *  - 七张席位卡严格按 SEATS 固定顺序 1→7 渲染，阶段开始后七个槽位恒在；
 *  - 思考流按「席位 × 阶段」分段累积（分阶段留痕，切换阶段不再清空），
 *    每段文本与分片数量都有硬上界，消除每帧 O(全量 token) 的重复拼接；
 *  - 流式片段缺失（重放尾部被裁剪、非流式兜底调用）时，用账本里的
 *    预承诺/质询/最终复判正文兜底 —— 已完成的卡片永不空白、永不折叠；
 *  - 检索卡按 query 把 tool_call/tool_result FIFO 配对（并行检索不再错配
 *    到最后一张卡），startedAt 跨重算保持稳定（秒数真实增长）。
 */

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

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

const PHASE_LABELS: Record<string, string> = Object.fromEntries(
  PHASES.map((phase) => [phase.id, phase.label]),
);

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

/* ------------------------------------------------------------------ */
/* 席位思考流：按「席位 × 阶段」分段、有界地聚合过程流 token 片段。      */
/* ------------------------------------------------------------------ */

/** 单个阶段内一个席位的一段思考。 */
interface SeatPhaseSlice {
  phase: string;
  text: string;
  running: boolean;
  elapsed: number;
  absent: boolean;
  absentReason: string;
}

/** 一个席位在本次会话中累积的全部阶段切片（最新在最后）。 */
interface SeatStream {
  slices: SeatPhaseSlice[];
}

/** 每段最多保留多少个原始增量分片；超出后把最老的一半折叠进 head，
 *  保证任何一轮聚合的字符串工作量都有硬上界（长阶段不再拖死主线程）。 */
const SLICE_PART_CAP = 800;
/** head（已折叠的老文本）保留的最新字符数。 */
const PHASE_HEAD_CAP = 6_000;
/** 单段最终渲染文本的字符上限。 */
const SEAT_PHASE_TEXT_CAP = 12_000;
/** 每席位最多保留的阶段切片数（八阶段，留 1 段余量）。 */
const MAX_SLICES_PER_SEAT = 9;

/** 尚无过程流席位共享的稳定空数组，避免 memo 卡因新 [] 引用每帧重渲染。 */
const EMPTY_SLICES: SeatPhaseSlice[] = [];

interface MutableSlice {
  phase: string;
  head: string;
  parts: string[];
  running: boolean;
  elapsed: number;
  absent: boolean;
  absentReason: string;
}

function capTail(text: string, cap: number): string {
  return text.length > cap ? text.slice(text.length - cap) : text;
}

function finalizeSlice(slice: MutableSlice): SeatPhaseSlice {
  const joined = slice.head + slice.parts.join("");
  return {
    phase: slice.phase,
    text: capTail(joined, SEAT_PHASE_TEXT_CAP),
    running: slice.running,
    elapsed: slice.elapsed,
    absent: slice.absent,
    absentReason: slice.absentReason,
  };
}

/** 把全量（已去重、有上界的）过程事件聚合成每席位的阶段切片。
 *  复杂度 O(事件数)，且每席位的字符串拼接量被 head/parts 上界封死。 */
function seatStreams(processEvents: ProcessEvent[]): Record<string, SeatStream> {
  const current: Record<string, MutableSlice[]> = {};
  const recoverable = new Set([
    "model_reasoning",
    "model_token",
    "model_done",
    "seat_absent",
    "seat_working",
  ]);

  const lastSlice = (seat: string): MutableSlice | null => {
    const list = current[seat];
    if (!list || list.length === 0) return null;
    return list[list.length - 1] ?? null;
  };

  const openSlice = (seat: string, phase: string): MutableSlice => {
    const list = (current[seat] ??= []);
    const previous = list[list.length - 1];
    if (previous) previous.running = false;
    const slice: MutableSlice = {
      phase,
      head: "",
      parts: [],
      running: true,
      elapsed: 0,
      absent: false,
      absentReason: "",
    };
    list.push(slice);
    if (list.length > MAX_SLICES_PER_SEAT) list.shift();
    return slice;
  };

  for (const event of processEvents) {
    const payload = event.payload as Record<string, unknown>;
    const seat = typeof payload.seat === "string" ? payload.seat : null;
    if (event.kind === "seat_deliberation" && seat) {
      openSlice(
        seat,
        typeof payload.phase === "string" ? payload.phase : "",
      );
      continue;
    }
    if (!seat) continue;
    let slice = lastSlice(seat);
    if (!slice) {
      if (!recoverable.has(event.kind)) continue;
      slice = openSlice(
        seat,
        typeof payload.phase === "string" ? payload.phase : "",
      );
      slice.running =
        event.kind !== "model_done" && event.kind !== "seat_absent";
      if (event.kind === "seat_absent") {
        slice.absent = true;
        slice.absentReason = String(payload.reason ?? "");
      }
    }
    const text = typeof payload.text === "string" ? payload.text : "";
    if (event.kind === "model_reasoning" || event.kind === "model_token") {
      if (text) {
        slice.parts.push(text);
        if (slice.parts.length > SLICE_PART_CAP) {
          const folded = slice.parts.splice(0, SLICE_PART_CAP / 2);
          slice.head = capTail(slice.head + folded.join(""), PHASE_HEAD_CAP);
        }
      }
    } else if (event.kind === "model_done") {
      slice.running = false;
    } else if (event.kind === "seat_working") {
      slice.elapsed =
        typeof payload.elapsed === "number" ? payload.elapsed : 0;
    } else if (event.kind === "seat_absent") {
      slice.running = false;
      slice.absent = true;
      slice.absentReason = String(payload.reason ?? "");
    }
  }

  const result: Record<string, SeatStream> = {};
  for (const [seat, list] of Object.entries(current)) {
    result[seat] = { slices: list.map(finalizeSlice) };
  }
  return result;
}

/* ------------------------------------------------------------------ */
/* 席位结构化产物：从账本事件提取每位科学家的预承诺/质询/最终复判正文。  */
/* 这些是科学家的「正式输出」，不依赖可能被重放窗口裁剪的 token 流：     */
/* 只要账本在，已完成的卡片就永远有内容（修复「完成后空白/折叠」）。     */
/* ------------------------------------------------------------------ */

interface SeatChallengeRecord {
  statement: string;
  fatal: boolean;
  target: string | null;
}
interface SeatRecord {
  precommitment: {
    text: string;
    confidence: number | null;
    updateCondition: string;
  } | null;
  challenges: SeatChallengeRecord[];
  final: { text: string; confidence: number | null; dissent: boolean } | null;
}

function seatRecords(
  events: LedgerEvent[],
  labels: Map<string, string>,
): Record<string, SeatRecord> {
  const result: Record<string, SeatRecord> = {};
  const ensure = (seat: string): SeatRecord =>
    (result[seat] ??= {
      precommitment: null,
      challenges: [],
      final: null,
    });
  for (const event of events) {
    const payload = event.payload as Record<string, unknown>;
    if (typeof payload.seat !== "string") continue;
    const record = ensure(payload.seat);
    if (event.kind === "PRECOMMITMENT_SEALED") {
      record.precommitment = {
        text: replaceClaimUuids(
          humanizeText(String(payload.initial_judgment ?? "")),
          labels,
        ),
        confidence:
          typeof payload.confidence === "number" ? payload.confidence : null,
        updateCondition: replaceClaimUuids(
          humanizeText(String(payload.update_condition ?? "")),
          labels,
        ),
      };
    } else if (event.kind === "CHALLENGE_RAISED") {
      record.challenges.push({
        statement: replaceClaimUuids(
          humanizeText(String(payload.statement ?? "")),
          labels,
        ),
        fatal: payload.is_fatal === true,
        target:
          typeof payload.claim_id === "string"
            ? replaceClaimUuids(payload.claim_id, labels)
            : null,
      });
    } else if (event.kind === "FINAL_JUDGMENT") {
      record.final = {
        text: replaceClaimUuids(
          humanizeText(String(payload.final_judgment ?? "")),
          labels,
        ),
        confidence:
          typeof payload.confidence === "number" ? payload.confidence : null,
        dissent: payload.has_dissent === true,
      };
    }
  }
  return result;
}

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

/** 检索卡最多渲染多少张（最新在前由调用方保证顺序，这里裁最早的）。
 *  过程检索只增不减，几百张 DOM 卡本身就足以拖死页面；更早的检索仍在
 *  审计轨迹里，这里折叠并给出数量说明。 */
const TOOL_GROUPS_RENDER_CAP = 60;

/** 议会动作最多渲染多少条。终态任务一次性回放整本账本（REST 上限 4000
 *  行），无上限渲染就是一次提交上万个 DOM 节点 —— 与检索卡同样的问题，
 *  同样的处理：裁最早的，折叠数量显式说明，完整留痕仍在审计轨迹。 */
const ACTION_RENDER_CAP = 300;

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
const ToolPending = memo(function ToolPending({
  startedAt,
}: {
  startedAt: number;
}) {
  const elapsed = Math.max(
    0,
    Math.floor((performance.now() - startedAt) / 1000),
  );
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
});

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

/** 一张席位卡。抽成 memo 组件：七张卡的 props 在每 250ms flush 后才变化
 *  一次，未变化的卡跳过重渲染（长阶段渲染开销的主要来源之一）。 */
interface SeatCardProps {
  seat: Seat;
  slices: SeatPhaseSlice[];
  record: SeatRecord | null;
  summary: SeatSummary | null;
  status: string | undefined;
  currentPhase: string | null;
  /** 最新一段切片的滚动容器注册回调（只对最新段自动滚底）。 */
  registerStream: (seat: Seat, node: HTMLDivElement | null) => void;
}

const SeatCard = memo(function SeatCard({
  seat,
  slices,
  record,
  summary,
  status,
  currentPhase,
  registerStream,
}: SeatCardProps) {
  const last = slices[slices.length - 1] ?? null;
  const pill = last
    ? last.running
      ? {
          label:
            last.elapsed >= SEAT_STUCK_CRITICAL_SECONDS
              ? t("模型长时间无响应（已等待 {0}s），系统将自动中断", last.elapsed)
              : t("思考中… 已等待 {0}s", last.elapsed),
          tone:
            last.elapsed >= SEAT_STUCK_CRITICAL_SECONDS
              ? "critical"
              : last.elapsed >= SEAT_STUCK_WARN_SECONDS
                ? "slow"
                : "running",
        }
      : last.absent
        ? { label: t("缺席"), tone: "absent" as const }
        : { label: t("已完成"), tone: "idle" as const }
    : durablePill(summary, status, currentPhase);

  const pillClass =
    pill.tone === "absent"
      ? "live__seat-running live__seat-absent"
      : pill.tone === "critical"
        ? "live__seat-running live__seat-running--critical"
        : pill.tone === "slow"
          ? "live__seat-running live__seat-running--slow"
          : pill.tone === "running"
            ? "live__seat-running"
            : "live__seat-idle";

  // 结构化产物块（预承诺/质询/最终复判）——正式输出，全程留痕。
  const recordBlocks: ReactNode[] = [];
  if (record?.precommitment?.text) {
    const confidence =
      record.precommitment.confidence != null
        ? `（${t("置信度 {0}", record.precommitment.confidence)}）`
        : "";
    recordBlocks.push(
      <div key="pre" className="live__seat-record live__seat-record--pre">
        <span className="live__seat-record-tag">{t("预承诺")}</span>
        <p className="live__seat-record-text">
          {record.precommitment.text}
          {confidence}
        </p>
      </div>,
    );
  }
  if (record && record.challenges.length > 0) {
    recordBlocks.push(
      <div key="chal" className="live__seat-record live__seat-record--chal">
        <span className="live__seat-record-tag">
          {t("质询（{0} 项）", record.challenges.length)}
        </span>
        {record.challenges.map((challenge, index) => (
          <p key={index} className="live__seat-record-text">
            {challenge.fatal ? t("【致命】") : "· "}
            {challenge.statement}
            {challenge.target ? `（${t("针对")} ${challenge.target}）` : ""}
          </p>
        ))}
      </div>,
    );
  }
  if (record?.final?.text) {
    recordBlocks.push(
      <div key="final" className="live__seat-record live__seat-record--final">
        <span className="live__seat-record-tag">
          {t("最终复判")}
          {record.final.dissent ? t("（附异议）") : ""}
          {record.final.confidence != null
            ? `（${t("置信度 {0}", record.final.confidence)}）`
            : ""}
        </span>
        <p className="live__seat-record-text">{record.final.text}</p>
      </div>,
    );
  }

  // 持久摘要的补充说明（结构化产物缺失时的兜底信息）。
  const durableNotes: string[] = [];
  if (summary?.precommitment?.confidence != null && !record?.precommitment?.text)
    durableNotes.push(t("预承诺置信度 {0}", summary.precommitment.confidence));
  if (
    summary &&
    summary.challenges_raised.length > 0 &&
    (record?.challenges.length ?? 0) === 0
  )
    durableNotes.push(t("已提出 {0} 项质询", summary.challenges_raised.length));
  if (summary?.final_judgment && !record?.final?.text)
    durableNotes.push(t("已提交最终复判"));
  if (summary && summary.unavailable_phases.length > 0) {
    const phaseLabels = PHASES.filter((phase) =>
      summary.unavailable_phases.includes(phase.id),
    ).map((phase) => t(phase.label));
    durableNotes.push(t("缺席阶段：{0}", phaseLabels.join("、")));
  }

  // 卡片正文绝不允许完全空白（用户反馈：完成后框直接折叠/空白）。
  const hasStreamText = slices.some((slice) => slice.text.length > 0);
  const hasBody =
    hasStreamText || recordBlocks.length > 0 || durableNotes.length > 0;

  return (
    <div className="live__seat">
      <div className="live__seat-head">
        <span className="live__seat-name">
          {SEAT_LABELS[seat] ?? seat}
        </span>
        {(last?.phase || currentPhase) ? (
          <span className="live__seat-phase mono">
            {PHASE_LABELS[last?.phase ?? ""] ??
              PHASE_LABELS[currentPhase ?? ""] ??
              last?.phase ??
              currentPhase}
          </span>
        ) : null}
        <span className={pillClass} title={last?.absentReason || undefined}>
          {pill.label}
        </span>
      </div>

      {/* 分阶段思考切片：按阶段顺序全部保留（留痕），最新在最后。 */}
      {slices.map((slice, index) => {
        const isLast = index === slices.length - 1;
        if (slice.text) {
          return (
            <div
              key={`slice-${index}`}
              className="live__seat-stream"
              ref={isLast ? (node) => registerStream(seat, node) : undefined}
            >
              {slices.length > 1 && slice.phase ? (
                <span className="live__seat-slice-phase mono">
                  {PHASE_LABELS[slice.phase] ?? slice.phase}
                </span>
              ) : null}
              <p className="live__seat-stream-text">{slice.text}</p>
            </div>
          );
        }
        if (isLast && slice.running) {
          return (
            <p
              key="empty-running"
              className="live__seat-stream live__seat-stream--empty"
            >
              {t("（尚无输出）")}
            </p>
          );
        }
        return null;
      })}

      {recordBlocks}

      {durableNotes.length > 0 ? (
        <ul className="live__seat-durable">
          {durableNotes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {!hasBody ? (
        <p className="live__seat-stream live__seat-stream--empty">
          {last?.running
            ? t("（尚无输出）")
            : t("本阶段无流式输出，正式结论见研究简报与最终论文")}
        </p>
      ) : null}
    </div>
  );
});

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
  const phaseStarted = current !== null || done.size > 0;
  const durableById = useMemo(() => {
    const map = new Map<string, SeatSummary>();
    for (const summary of seats ?? []) map.set(summary.seat, summary);
    return map;
  }, [seats]);
  const claimLabels = useMemo(
    () => buildClaimLabels(claims ?? [], graph ?? { nodes: [], edges: [] }),
    [claims, graph],
  );
  const records = useMemo(
    () => seatRecords(events, claimLabels),
    [events, claimLabels],
  );
  const actions = useMemo(
    () =>
      events
        .map((event) => actionSummary(event, claimLabels))
        .filter(
          (item): item is { meta: string; tone: string; body: string } =>
            item !== null,
        ),
    [events, claimLabels],
  );

  // 七张席位卡严格固定顺序：阶段一旦开始（或任务已离开队列），七个槽位
  // 恒在，谁先出内容都不引起重排；之前未开始时仅展示已有持久摘要的卡。
  const visibleSeatIds = useMemo((): readonly Seat[] => {
    const showAll =
      phaseStarted || (!!status && status !== "QUEUED");
    if (showAll) return SEATS;
    return SEATS.filter((seat) => durableById.has(seat));
  }, [phaseStarted, status, durableById]);

  // 每秒重渲染一次，让「思考中… 已等待 Ns」与检索卡片的秒数走动。只在
  // 还有席位 running 或有检索 pending 时启动定时器，空闲时不空转。
  const [, setTick] = useState(0);
  const anyRunning = useMemo(
    () =>
      Object.values(streams).some((stream) =>
        stream.slices.some((slice) => slice.running),
      ),
    [streams],
  );

  // 最新一段思考切片自动滚底；手动上滚会被下一次 flush 拉回，这是实时
  // 视图的取舍（研究者在看「现在」）。
  const streamRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const registerStream = useCallback(
    (seat: Seat, node: HTMLDivElement | null) => {
      streamRefs.current[seat] = node;
    },
    [],
  );
  useEffect(() => {
    for (const ref of Object.values(streamRefs.current)) {
      if (ref) ref.scrollTop = ref.scrollHeight;
    }
  }, [processEvents.length]);

  /* ---------------- 检索与文献：按 query FIFO 配对 ---------------- */
  interface ToolResult {
    url: string | null;
    title: string;
    miss: boolean;
    reason?: string;
    citationCount?: number;
  }
  interface ToolGroup {
    kind: string;
    query: string;
    seats: string[];
    startedAt: number;
    results: ToolResult[];
  }
  // startedAt 必须跨 useMemo 重算保持稳定：processEvents 每 250ms 换一次
  // 身份，若在重建时取 performance.now()，「已等待 Ns」会被永远重置为 0
  // （生产 bug：检索卡永远显示「已等待 0s」）。按 callKey 记住首次见到
  // 该检索的时刻。
  const startedAtRef = useRef<Map<string, number>>(new Map());
  const toolGroups = useMemo(() => {
    const groups: ToolGroup[] = [];
    // 后端并行检索的事件顺序是「全部 tool_call → 并发完成后 tool_result」，
    // result 自带 query：按 query 建 FIFO 队列配对，单 current 变量会把
    // 所有结果错配到最后一张卡（生产 bug：三张卡永久等待结果）。
    const pendingByQuery = new Map<string, ToolGroup[]>();
    const callOrdinal = new Map<string, number>();
    const liveKeys = new Set<string>();
    for (const event of processEvents) {
      const payload = event.payload as Record<string, unknown>;
      if (event.kind === "tool_call") {
        const query = String(payload.query ?? "");
        const rawKind = String(payload.kind ?? "search");
        const ordinal = (callOrdinal.get(query) ?? 0) + 1;
        callOrdinal.set(query, ordinal);
        const callKey = `${rawKind}|${query}|${ordinal}`;
        liveKeys.add(callKey);
        let startedAt = startedAtRef.current.get(callKey);
        if (startedAt === undefined) {
          startedAt = performance.now();
          startedAtRef.current.set(callKey, startedAt);
        }
        const group: ToolGroup = {
          kind: rawKind === "doi_lookup" ? t("DOI 解析") : t("检索"),
          query,
          seats: Array.isArray(payload.seats)
            ? payload.seats.map(String)
            : [],
          startedAt,
          results: [],
        };
        groups.push(group);
        const queue = pendingByQuery.get(query) ?? [];
        queue.push(group);
        pendingByQuery.set(query, queue);
      } else if (event.kind === "tool_result") {
        const query = String(payload.query ?? "");
        let group = pendingByQuery.get(query)?.shift() ?? null;
        if (!group) {
          // 重放窗口裁掉了对应 tool_call（结构锚点超上限的极端情况）：
          // 为结果补建一张已完成卡，结果永不丢失。
          group = {
            kind: t("检索"),
            query,
            seats: [],
            startedAt: performance.now(),
            results: [],
          };
          groups.push(group);
        }
        const citationCount =
          typeof payload.citation_count === "number" &&
          payload.citation_count > 0
            ? payload.citation_count
            : undefined;
        group.results.push({
          url: typeof payload.url === "string" ? payload.url : null,
          title: String(payload.title ?? payload.doi ?? ""),
          miss: payload.miss === true,
          reason:
            typeof payload.reason === "string" ? payload.reason : undefined,
          citationCount,
        });
      }
    }
    // 收敛 startedAt 缓存：只保留本轮组用到的 key，防止长跑后无限增长。
    for (const key of startedAtRef.current.keys()) {
      if (!liveKeys.has(key)) startedAtRef.current.delete(key);
    }
    return groups;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processEvents]);

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
  const hiddenToolCount = Math.max(0, toolGroups.length - TOOL_GROUPS_RENDER_CAP);
  const visibleToolGroups = toolGroups.slice(-TOOL_GROUPS_RENDER_CAP);
  const hiddenActionCount = Math.max(0, actions.length - ACTION_RENDER_CAP);
  const visibleActions = actions.slice(-ACTION_RENDER_CAP);

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
              说明它的边界 —— 可以调整讨论重点，不能影响判定。 */}
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
                {visibleSeatIds.map((seat) => (
                  <SeatCard
                    key={seat}
                    seat={seat}
                    slices={streams[seat]?.slices ?? EMPTY_SLICES}
                    record={records[seat] ?? null}
                    summary={durableById.get(seat) ?? null}
                    status={status}
                    currentPhase={current}
                    registerStream={registerStream}
                  />
                ))}
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
                  <>
                    {hiddenToolCount > 0 ? (
                      <p className="live__tool-folded">
                        {t(
                          "更早的 {0} 条检索已折叠（过程留痕可在审计轨迹查看）",
                          hiddenToolCount,
                        )}
                      </p>
                    ) : null}
                    <div className="live__tool-grid">
                      {visibleToolGroups.map((group, index) => (
                        <div key={`${group.query}-${index}`} className="live__tool-card">
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
                            <ToolPending startedAt={group.startedAt} />
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
                  </>
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
                  <>
                    {hiddenActionCount > 0 ? (
                      <p className="live__tool-folded">
                        {t(
                          "更早的 {0} 条动作已折叠（过程留痕可在审计轨迹查看）",
                          hiddenActionCount,
                        )}
                      </p>
                    ) : null}
                    <ol className="live__action-log">
                      {visibleActions.map((item, index) => (
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
                  </>
                )}
              </section>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
