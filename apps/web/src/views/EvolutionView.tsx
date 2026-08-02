/** Evolution View: a claim-referencing timeline.
 *
 * Only three event types ever name a claim in the current ledger -- a Claim
 * itself (which covers a fork's anchor and its narrower offspring), a
 * challenge, and a dissent certificate -- so that is what this feed shows,
 * ordered by the ledger's own sequence rather than arrival time, matching
 * the Audit Trail's convention for the same reason: a reconnect must not
 * reorder history.
 *
 * No round currently emits a dedicated "confidence changed" event for a
 * Claim, so this cannot draw a continuous confidence curve -- only the
 * discrete events that actually exist. Documented in the README's known-gaps
 * section rather than papered over with an invented interpolation.
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
};

function labelEvent(eventType: string): string {
  return EVENT_LABELS[eventType] ?? NODE_TYPE_LABELS[eventType] ?? eventType;
}

/** Best-effort one-line text out of whatever this event type actually
 * carries -- mirrors AuditView's `summarise`, narrowed to the fields the
 * three event types here are known to set. */
function summarise(entry: EvolutionEntry): string {
  const { payload } = entry;
  for (const key of ["statement", "final_judgment", "reason"]) {
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
      subtitle="按账本序号排列的主张分叉、质询与异议时间线。当前没有轮次会为主张发出连续的置信度变化事件，因此这里只展示离散事件，无法绘出连续曲线。"
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
