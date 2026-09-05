/** Audit Trail.
 *
 * The Scientific Event Ledger as the researcher sees it. Three rules:
 *
 * 1. **Refusals are first-class rows, not omissions.** A quarantined event, an
 *    unavailable seat, a skipped round -- each is something the system did not
 *    do, and CLAUDE.md 7 requires those to be visible rather than inferred from
 *    an absence.
 * 2. **No chain of thought.** CLAUDE.md 11 permits structured actions, the
 *    evidence used, challenges and responses, and confidence changes. A model's
 *    private reasoning is not shown even where it exists.
 * 3. **Order is the ledger's, not the browser's.** Rows are keyed and sorted by
 *    the per-task sequence, which is the same total order the projector used.
 *    Sorting by arrival time would let a reconnect reorder history.
 */

import { useMemo, useState } from "react";

import type { LedgerEvent } from "../api/types";
import { NODE_TYPE_LABELS, SEAT_LABELS, type Seat } from "../api/types";
import { Badge, Empty, Panel, type Tone } from "../components/primitives";
import { t } from "../i18n";

import "./AuditView.css";

/** Event kind -> how much it should alarm the reader.
 *
 * Anything unrecognised is neutral, never "admitted": a new event type must not
 * arrive on screen already looking like verified evidence.
 */
const KIND_TONE: Record<string, Tone> = {
  PHASE_STARTED: "unknown",
  PHASE_COMPLETED: "admitted",
  PHASE_FAILED: "refuted",
  PHASE_SKIPPED: "refuted",
  SEAT_UNAVAILABLE: "refuted",
  SOURCE_REFUSED: "refuted",
  PRECOMMITMENT_SEALED: "admitted",
  EVIDENCE_REQUESTED: "unknown",
  EVIDENCE_PUBLISHED: "admitted",
  CHALLENGE_RAISED: "provisional",
  BOUNTY_ASSIGNED: "unknown",
  CONSENSUS_DRAFTED: "admitted",
  FINAL_JUDGMENT: "admitted",
};

const KIND_LABELS: Record<string, string> = {
  PHASE_STARTED: "轮次开始",
  PHASE_COMPLETED: "轮次完成",
  PHASE_FAILED: "轮次失败",
  PHASE_SKIPPED: "轮次未执行",
  SEAT_UNAVAILABLE: "席位缺席",
  SOURCE_REFUSED: "来源被拒",
  PRECOMMITMENT_SEALED: "预承诺封存",
  EVIDENCE_REQUESTED: "证据请求",
  EVIDENCE_PUBLISHED: "证据发布",
  CHALLENGE_RAISED: "提出质询",
  BOUNTY_ASSIGNED: "盲点悬赏分派",
  CONSENSUS_DRAFTED: "条件化共识草案",
  FINAL_JUDGMENT: "最终独立复判",
  FINAL_PAPER_DRAFTED: "最终论文已生成",
  FINAL_PAPER_FAILED: "综合论文生成失败",
};

/** Kinds that record something the system refused or could not do. */
const REFUSALS = new Set([
  "PHASE_FAILED",
  "PHASE_SKIPPED",
  "SEAT_UNAVAILABLE",
  "SOURCE_REFUSED",
]);

/** Label an event kind. Evidence node types fall back to their own vocabulary
 * rather than showing a raw English identifier in a Chinese trail. */
function labelKind(kind: string): string {
  return KIND_LABELS[kind] ?? NODE_TYPE_LABELS[kind] ?? kind;
}

function summarise(event: LedgerEvent): string {
  const { payload } = event;
  const parts: string[] = [];
  const seat = payload.seat;
  if (typeof seat === "string") {
    parts.push(SEAT_LABELS[seat as Seat] ?? seat);
  }
  const phase = payload.phase;
  if (typeof phase === "string") parts.push(phase);
  // `title` and `doi` matter for Source rows, which otherwise rendered blank --
  // a row with a badge and no text tells the reader nothing about what the
  // system did.
  for (const key of ["reason", "statement", "query", "question", "title", "doi"]) {
    const value = payload[key];
    if (typeof value === "string" && value) {
      parts.push(value);
      break;
    }
  }
  const assignments = payload.assignments;
  if (Array.isArray(assignments)) {
    parts.push(t("{0} 项分派", assignments.length));
  }
  const consensus = payload.conditional_consensus;
  if (typeof consensus === "string" && consensus) parts.push(consensus);
  return parts.join(" · ");
}

/** Max audit rows mounted as DOM (newest first). */
const AUDIT_RENDER_CAP = 500;

export function AuditView({
  events,
  streamOpen,
}: {
  events: LedgerEvent[];
  streamOpen: boolean;
}) {
  const [refusalsOnly, setRefusalsOnly] = useState(false);

  const ordered = useMemo(
    () =>
      [...events].sort((a, b) => a.workspace_version - b.workspace_version),
    [events],
  );
  const shown = refusalsOnly
    ? ordered.filter((event) => REFUSALS.has(event.kind))
    : ordered;
  const refusalCount = ordered.filter((event) =>
    REFUSALS.has(event.kind),
  ).length;
  // Render cap: a long, re-run task can carry thousands of ledger rows;
  // mounting them all as DOM nodes froze the audit tab. Show the newest
  // AUDIT_RENDER_CAP (the ledger itself keeps the full history server-side).
  const visibleShown =
    shown.length > AUDIT_RENDER_CAP
      ? shown.slice(shown.length - AUDIT_RENDER_CAP)
      : shown;

  return (
    <Panel
      title={t("审计轨迹")}
      subtitle={t("科研事件账本的实时视图。不展示模型私有思维链。")}
      actions={
        <label className="audit__filter">
          <input
            type="checkbox"
            checked={refusalsOnly}
            onChange={(event) => setRefusalsOnly(event.target.checked)}
          />
          {t("只看拒绝与缺席（{0}）", refusalCount)}
        </label>
      }
    >
      <p className="audit__stream" role="status">
        <span
          className={`audit__dot${streamOpen ? " audit__dot--live" : ""}`}
          aria-hidden="true"
        />
        {streamOpen
          ? t("已连接事件流。断线重连时按账本序号续传，不会丢事件。")
          : t("事件流未连接。下方为本次会话已收到的事件。")}
      </p>

      {shown.length === 0 ? (
        <Empty>
          {refusalsOnly
            ? t("本次会话未收到任何拒绝或缺席事件。")
            : t("尚未收到事件。任务可能仍在队列中。")}
        </Empty>
      ) : (
        <>
        {shown.length > visibleShown.length ? (
          <p className="audit__truncated" role="status">
            {t(
              "事件较多，仅渲染最新 {0} 条（共 {1} 条，完整记录保存在服务端事件账本）。",
              AUDIT_RENDER_CAP,
              shown.length,
            )}
          </p>
        ) : null}
        <ol className="audit__list">
          {visibleShown.map((event) => (
            <li
              key={`${event.workspace_version}-${event.event_id}`}
              className={
                REFUSALS.has(event.kind) ? "audit__row audit__row--refusal" : "audit__row"
              }
            >
              <span
                className="audit__seq mono"
                title={t("账本事件序号，与事件流重放顺序一致")}
              >
                #{event.workspace_version}
              </span>
              <Badge tone={KIND_TONE[event.kind] ?? "unknown"}>
                {labelKind(event.kind)}
              </Badge>
              <span className="audit__summary">{summarise(event)}</span>
            </li>
          ))}
        </ol>
        </>
      )}
    </Panel>
  );
}
