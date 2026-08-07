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
 *
 * Readability rule (round-5): the claim column shows what the claim *says*,
 * never its raw UUID -- "8d0a6ff7…" means nothing to a researcher, while
 * "主张：社交媒体使用与抑郁呈正相关" means everything. The label resolves
 * from the confirmed claims, then from the graph's Claim nodes (which cover
 * forks and challenged claims), and only falls back to a short id when the
 * claim is genuinely unnamed in both.
 */

import { useMemo } from "react";

import type {
  ConfirmedClaim,
  EvidenceGraph,
  EvolutionEntry,
} from "../api/types";
import { NODE_TYPE_LABELS } from "../api/types";
import { Badge, Empty, Panel, toneForStatus } from "../components/primitives";
import { t } from "../i18n";
import {
  INTERNAL_LABELS,
  buildClaimLabels,
  claimLabel,
  humanizeText,
} from "./claimLabels";

import "./EvolutionView.css";

const EVENT_LABELS: Record<string, string> = {
  Claim: "主张（含分叉）",
  CHALLENGE_RAISED: "提出质询",
  DissentCertificate: "异议证书",
  CONFIDENCE_UPDATED: "置信度轨迹点",
};

/** Label the event type: known event kinds, then known internal strings
 * (which can appear inside a `reason`/`statement` payload), then raw. */
function humanize(value: string): string {
  return (
    EVENT_LABELS[value] ??
    NODE_TYPE_LABELS[value] ??
    INTERNAL_LABELS[value] ??
    value
  );
}

function labelEvent(eventType: string): string {
  return humanize(eventType);
}

/** Best-effort one-line text out of whatever this event type actually
 * carries -- mirrors AuditView's `summarise`, narrowed to the fields the
 * four event types here are known to set. Internal identifiers in the text
 * are translated, and raw UUIDs are replaced by the claim label when the
 * value names one. */
function summarise(entry: EvolutionEntry, labels: Map<string, string>): string {
  const { payload } = entry;
  let text = "";
  for (const key of [
    "confidence_delta_note",
    "statement",
    "final_judgment",
    "reason",
  ]) {
    const value = payload[key];
    if (typeof value === "string" && value) {
      text = value;
      break;
    }
  }
  if (!text) return t("（该事件未记录可展示的文本字段）");
  text = humanizeText(text);
  // Replace any UUID that names a known claim with its readable label.
  const uuid =
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i;
  for (const [id, label] of labels) {
    if (text.includes(id)) text = text.split(id).join(label);
  }
  // Drop any remaining raw UUIDs from the summary; they are noise to the
  // reader (a UUID never labels a scientist or a phase).
  return text.replace(uuid, "（标识符）").trim();
}

export function EvolutionView({
  entries,
  claims,
  graph,
}: {
  entries: EvolutionEntry[];
  claims: ConfirmedClaim[];
  graph: EvidenceGraph;
}) {
  const ordered = useMemo(
    () => [...entries].sort((a, b) => a.sequence - b.sequence),
    [entries],
  );
  const labels = useMemo(
    () => buildClaimLabels(claims, graph),
    [claims, graph],
  );

  return (
    <Panel
      title={t("演化视图")}
      subtitle={t("按账本序号排列的主张分叉、质询、异议与置信度轨迹点时间线。CONFIDENCE_UPDATED 在证据交换、交叉质询、联合建模与最终复判四个阶段边界为每个确认主张给出定性变化说明，而非可绘制的数值曲线。")}
    >
      {ordered.length === 0 ? (
        <Empty>
          {t(
            "本任务未记录主张分叉、质询或异议——这不是抓取失败，而是本轮协议确实未触发这些路径。",
          )}
        </Empty>
      ) : (
        <ol className="evolution__list">
          {ordered.map((entry) => (
            <li key={entry.sequence} className="evolution__row">
              <span className="evolution__seq mono">#{entry.sequence}</span>
              <Badge tone={toneForStatus(entry.status)}>
                {labelEvent(entry.event_type)}
              </Badge>
              <span className="evolution__claim">
                {entry.claim_id
                  ? claimLabel(entry.claim_id, labels)
                  : "—"}
              </span>
              <span className="evolution__summary">
                {summarise(entry, labels)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
