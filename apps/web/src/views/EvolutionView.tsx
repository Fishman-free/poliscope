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

import "./EvolutionView.css";

const EVENT_LABELS: Record<string, string> = {
  Claim: "主张（含分叉）",
  CHALLENGE_RAISED: "提出质询",
  DissentCertificate: "异议证书",
  CONFIDENCE_UPDATED: "置信度轨迹点",
};

/** Internal gap/absence strings the ledger may carry; shown to the reader in
 * Chinese because a raw English identifier like
 * ``JOINT_MODELING:no_capsule_fold`` means nothing. Unknown strings stay
 * as-is -- a label map must never guess. */
const INTERNAL_LABELS: Record<string, string> = {
  "ACQUISITION:no_tool_provider": "未配置工具网关，无法获取证据",
  "JOINT_MODELING:no_capsule_fold": "联合建模未形成可折叠的争论胶囊",
  "JOINT_MODELING:missing_fields": "联合建模输出缺少必需字段",
  "FINAL_REJUDGMENT:no_dissent_target": "最终复判未指向异议目标",
  resurrection_condition_not_met: "复活条件未满足",
  "no model provider is connected to the Model Gateway": "模型网关未连接",
  "acquisition timed out": "证据获取超时",
  "source budget exhausted": "来源预算已耗尽",
  "source is retracted": "来源已撤回",
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

/** Replace every internal identifier found in a sentence with its Chinese
 * label, so a payload that carries one still reads in the interface
 * language. Unknown fragments are left untouched. */
function humanizeText(text: string): string {
  let result = text;
  for (const [raw, label] of Object.entries(INTERNAL_LABELS)) {
    result = result.split(raw).join(label);
  }
  return result;
}

/** Build claim_id -> readable label, preferring confirmed claims, then graph
 * Claim nodes (forks and challenged claims live there, not in the brief). */
function buildClaimLabels(
  claims: ConfirmedClaim[],
  graph: EvidenceGraph,
): Map<string, string> {
  const labels = new Map<string, string>();
  for (const claim of claims) {
    labels.set(claim.claim_id, `主张：${claim.statement}`);
  }
  for (const node of graph.nodes) {
    if (node.node_type !== "Claim") continue;
    if (labels.has(node.id)) continue;
    const statement = node.payload.statement;
    if (typeof statement === "string" && statement.trim()) {
      labels.set(node.id, `主张：${statement}`);
    }
  }
  return labels;
}

function claimLabel(claimId: string, labels: Map<string, string>): string {
  const label = labels.get(claimId);
  if (label) return label.length > 60 ? `${label.slice(0, 58)}…` : label;
  return t("主张（未命名）");
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
