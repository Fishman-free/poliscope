/** Evolution View: a claim-referencing timeline.
 *
 * Four event types ever name a claim in the current ledger -- a Claim itself
 * (which covers a fork's anchor and its narrower offspring), a challenge, a
 * dissent certificate, and (since plan phase 5) a qualitative
 * `CONFIDENCE_UPDATED` marker -- so that is what this feed shows, ordered by
 * the ledger's own sequence rather than arrival time, matching the Audit
 * Trail's convention for the same reason: a reconnect must not reorder
 * history.
 *
 * `CONFIDENCE_UPDATED` gives every confirmed claim a point at each of
 * EVIDENCE_EXCHANGE, CROSS_EXAMINATION, JOINT_MODELING, and FINAL_REJUDGMENT
 * (`packages.council.rounds.registry._confidence_marker`), so a claim with
 * enough of those markers now reads as a continuous trajectory across
 * phases here. Its `confidence_delta_note` is deliberately a plain-language
 * sentence, never a number -- CLAUDE.md 16 forbids treating a model's
 * confidence as a substitute for real statistical uncertainty, and no model
 * in this MVP computes an actual confidence delta for a claim. So this stays
 * a list of qualitative trajectory points, not a plotted quantitative curve.
 */

import { useMemo } from "react";

import type { EvolutionEntry } from "../api/types";
import { NODE_TYPE_LABELS } from "../api/types";
import { Badge, Empty, Panel, toneForStatus } from "../components/primitives";

import "./EvolutionView.css";

const EVENT_LABELS: Record<string, string> = {
  Claim: "主张（含分叉）",
  CHALLENGE_RAISED: "提出质询",
  DissentCertificate: "异议证书",
  CONFIDENCE_UPDATED: "置信度轨迹点",
};

function labelEvent(eventType: string): string {
  return EVENT_LABELS[eventType] ?? NODE_TYPE_LABELS[eventType] ?? eventType;
}

/** Best-effort one-line text out of whatever this event type actually
 * carries -- mirrors AuditView's `summarise`, narrowed to the fields the
 * four event types here are known to set. */
function summarise(entry: EvolutionEntry): string {
  const { payload } = entry;
  for (const key of [
    "confidence_delta_note",
    "statement",
    "final_judgment",
    "reason",
  ]) {
    const value = payload[key];
    if (typeof value === "string" && value) return value;
  }
  return "（该事件未记录可展示的文本字段）";
}

export function EvolutionView({ entries }: { entries: EvolutionEntry[] }) {
  const ordered = useMemo(
    () => [...entries].sort((a, b) => a.sequence - b.sequence),
    [entries],
  );

  return (
    <Panel
      title="演化视图"
      subtitle="按账本序号排列的主张分叉、质询、异议与置信度轨迹点时间线。CONFIDENCE_UPDATED 在证据交换、交叉质询、联合建模与最终复判四个阶段边界为每个确认主张给出定性变化说明，而非可绘制的数值曲线。"
    >
      {ordered.length === 0 ? (
        <Empty>
          本任务未记录主张分叉、质询或异议——这不是抓取失败，而是本轮协议确实未触发这些路径。
        </Empty>
      ) : (
        <ol className="evolution__list">
          {ordered.map((entry) => (
            <li key={entry.sequence} className="evolution__row">
              <span className="evolution__seq mono">#{entry.sequence}</span>
              <Badge tone={toneForStatus(entry.status)}>
                {labelEvent(entry.event_type)}
              </Badge>
              <span className="evolution__claim mono">
                {entry.claim_id ? `${entry.claim_id.slice(0, 8)}…` : "—"}
              </span>
              <span className="evolution__summary">{summarise(entry)}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
