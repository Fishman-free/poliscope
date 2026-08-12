/** 7 人议会状态：每位科学家的思考链路时间线.
 *
 * CLAUDE.md 11 lists the council panel as its own required surface, distinct
 * from the Audit Trail: this shows each seat's structured actions -- the
 * precommitment, the challenges it raised, its final judgment -- not a feed
 * of every event in order. An absent or partially-unavailable seat stays a
 * visible card, never an omitted one.
 *
 * Each card is a vertical timeline from precommitment through the council's
 * rounds to the final rejudgment: the phase separators come from
 * ``PHASE_STARTED`` events, and every seat action -- evidence requests and
 * publications, challenges, bounty assignments, absences -- is an entry under
 * the phase its sequence falls in. The model's raw chain of thought
 * (``MODEL_REASONING_CAPTURED``, process-only) is deliberately **not**
 * rendered here or anywhere: it is the vendor's private thinking, never
 * evidence, never a basis for SUPPORTS/REFUTES (CLAUDE.md 5.1/11). Only the
 * structured scientific actions, tool calls, retrieved sources, challenges,
 * absences and audit summaries are shown.
 *
 * Data sources: the timeline runs off ``events`` (the SSE stream replays from
 * sequence 0 on connect, so it is complete and ordered by
 * ``workspace_version``); when a seat has no events at all -- e.g. the stream
 * has not connected yet -- the card falls back to ``seats``' structured
 * summary so no network state renders an empty card.
 */

import { useMemo } from "react";

import type { LedgerEvent, Seat, SeatSummary } from "../api/types";
import { SEAT_LABELS } from "../api/types";
import { Badge, Empty, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./CouncilView.css";

/* Values match TaskPhase's StrEnum values (``phase.value``) written into the
 * PHASE_STARTED payloads. */
const PHASE_LABELS: Record<string, string> = {
  PRECOMMITMENT: "预承诺",
  ACQUISITION: "证据获取",
  EVIDENCE_EXCHANGE: "证据交换",
  CROSS_EXAMINATION: "交叉质询",
  BLINDSPOT_BOUNTY: "盲点悬赏",
  JOINT_MODELING: "联合建模",
  FINAL_REJUDGMENT: "最终复判",
};

/** Events attributed to a single seat, in ledger order. MODEL_REASONING_CAPTURED
 * is intentionally absent: its payload is the vendor's private chain of
 * thought, which this panel does not render (CLAUDE.md 11). */
const SEAT_EVENT_KINDS = new Set([
  "PRECOMMITMENT_SEALED",
  "EVIDENCE_REQUESTED",
  "EVIDENCE_PUBLISHED",
  "CHALLENGE_RAISED",
  "FINAL_JUDGMENT",
  "SEAT_UNAVAILABLE",
]);

interface PhaseSpan {
  phase: string;
  start: number;
}

function phaseSpans(events: LedgerEvent[]): PhaseSpan[] {
  const spans: PhaseSpan[] = [];
  for (const event of events) {
    if (event.kind !== "PHASE_STARTED") continue;
    const phase = (event.payload as Record<string, unknown>).phase;
    if (typeof phase === "string") spans.push({ phase, start: event.workspace_version });
  }
  return spans;
}

/** The phase a ledger sequence falls in, or null before the first phase. */
function phaseFor(spans: PhaseSpan[], sequence: number): string | null {
  let current: string | null = null;
  for (const span of spans) {
    if (span.start > sequence) break;
    current = span.phase;
  }
  return current;
}

interface SeatTimelineEntry {
  sequence: number;
  kind: string;
  phase: string | null;
  payload: Record<string, unknown>;
}

/** Every seat's timeline entries, keyed by seat value. BOUNTY_ASSIGNED is
 * expanded per assignment (its payload carries an array of target seats),
 * so bounty work shows up on each assigned scientist's own line. */
function timelineBySeat(events: LedgerEvent[]): Map<string, SeatTimelineEntry[]> {
  const spans = phaseSpans(events);
  const map = new Map<string, SeatTimelineEntry[]>();
  const push = (seat: string, entry: SeatTimelineEntry) => {
    const list = map.get(seat) ?? [];
    list.push(entry);
    map.set(seat, list);
  };
  for (const event of events) {
    const payload = event.payload as Record<string, unknown>;
    if (event.kind === "BOUNTY_ASSIGNED") {
      const assignments = payload.assignments;
      if (Array.isArray(assignments)) {
        for (const item of assignments) {
          if (typeof item !== "object" || item === null) continue;
          const target = (item as Record<string, unknown>).target_seat;
          if (typeof target === "string") {
            push(target, {
              sequence: event.workspace_version,
              kind: event.kind,
              phase: phaseFor(spans, event.workspace_version),
              payload: item as Record<string, unknown>,
            });
          }
        }
      }
      continue;
    }
    if (!SEAT_EVENT_KINDS.has(event.kind)) continue;
    const seat = payload.seat;
    if (typeof seat !== "string") continue;
    push(seat, {
      sequence: event.workspace_version,
      kind: event.kind,
      phase: phaseFor(spans, event.workspace_version),
      payload,
    });
  }
  for (const entries of map.values()) {
    entries.sort((a, b) => a.sequence - b.sequence);
  }
  return map;
}

/** The phases that actually ran, in order, for the timeline rails. */
function ranPhases(events: LedgerEvent[]): string[] {
  return phaseSpans(events).map((span) => span.phase);
}

function seatName(seat: string): string {
  return SEAT_LABELS[seat as Seat] ?? seat;
}

/** One timeline entry, rendered by event kind. Reasoning entries stay
 * collapsed `<details>` blocks; everything else is a one-line structured
 * action (CLAUDE.md 11: structured actions are the surface, reasoning the
 * background). */
function TimelineEntry({ entry }: { entry: SeatTimelineEntry }) {
  const { kind, payload } = entry;

  if (kind === "PRECOMMITMENT_SEALED") {
    const confidence = payload.confidence;
    const condition = typeof payload.update_condition === "string" ? payload.update_condition : "";
    return (
      <li className="council__tl-item">
        <span className="council__tl-label">{t("预承诺已密封")}</span>
        <span className="council__tl-meta mono">
          {t("置信度")} {confidence !== null && confidence !== undefined ? String(confidence) : t("未记录")}
        </span>
        {condition ? <p className="council__text">{condition}</p> : null}
      </li>
    );
  }

  if (kind === "EVIDENCE_REQUESTED") {
    const count = payload.request_count;
    return (
      <li className="council__tl-item">
        <span className="council__tl-label">{t("请求证据")}</span>
        <span className="council__tl-meta">{typeof count === "number" ? t("{0} 条", count) : ""}</span>
      </li>
    );
  }

  if (kind === "EVIDENCE_PUBLISHED") {
    const items = payload.items;
    return (
      <li className="council__tl-item">
        <span className="council__tl-label">{t("发布证据条目")}</span>
        <span className="council__tl-meta">
          {Array.isArray(items) ? t("{0} 条", items.length) : ""}
        </span>
      </li>
    );
  }

  if (kind === "CHALLENGE_RAISED") {
    const statement = typeof payload.statement === "string" ? payload.statement : "";
    const fatal = payload.is_fatal === true;
    return (
      <li className="council__tl-item">
        <Badge tone={fatal ? "refuted" : "provisional"}>{fatal ? t("致命质询") : t("非致命质询")}</Badge>
        <span className="council__tl-text">{statement || t("（未记录质询内容）")}</span>
      </li>
    );
  }

  if (kind === "BOUNTY_ASSIGNED") {
    const rank = payload.priority_rank;
    return (
      <li className="council__tl-item">
        <span className="council__tl-label">{t("认领盲点悬赏")}</span>
        <span className="council__tl-meta">
          {typeof rank === "number" ? t("优先级 #{0}", rank) : ""}
        </span>
      </li>
    );
  }

  if (kind === "FINAL_JUDGMENT") {
    const judgment = typeof payload.final_judgment === "string" ? payload.final_judgment : "";
    const confidence = payload.confidence;
    return (
      <li className="council__tl-item council__tl-item--final">
        <span className="council__tl-label">{t("最终复判")}</span>
        {payload.has_dissent === true ? <Badge tone="refuted">{t("异议")}</Badge> : null}
        <span className="council__tl-meta mono">
          {t("置信度")} {confidence !== null && confidence !== undefined ? String(confidence) : t("未记录")}
        </span>
        {judgment ? <p className="council__text">{judgment}</p> : null}
      </li>
    );
  }

  if (kind === "SEAT_UNAVAILABLE") {
    const reason = typeof payload.reason === "string" ? payload.reason : "";
    return (
      <li className="council__tl-item">
        <Badge tone="unknown">{t("缺席")}</Badge>
        <span className="council__tl-text">{reason || t("该轮次未产出判断")}</span>
      </li>
    );
  }

  return (
    <li className="council__tl-item">
      <span className="council__tl-label">{kind}</span>
    </li>
  );
}

/** Fallback card for a seat with no streamed events yet: the structured
 * summary from the workspace snapshot. Never blank on any network state. */
function SummaryCard({ entry }: { entry: SeatSummary }) {
  return (
    <>
      <section className="council__block">
        <h4>{t("预承诺")}</h4>
        {entry.precommitment ? (
          <>
            <p className="council__confidence mono">
              {t("置信度")}{" "}
              {entry.precommitment.confidence !== null
                ? entry.precommitment.confidence
                : t("未记录")}
            </p>
            <p className="council__text">
              {entry.precommitment.update_condition ?? t("未记录更新条件")}
            </p>
          </>
        ) : (
          <Empty>{t("本轮未记录预承诺。")}</Empty>
        )}
      </section>

      <section className="council__block">
        <h4>{t("提出的质询（{0}）", entry.challenges_raised.length)}</h4>
        {entry.challenges_raised.length === 0 ? (
          <Empty>{t("本轮未提出质询。")}</Empty>
        ) : (
          <ul className="council__challenges">
            {entry.challenges_raised.map((challenge, index) => (
              <li key={index}>
                <Badge tone={challenge.is_fatal ? "refuted" : "provisional"}>
                  {challenge.is_fatal ? t("致命") : t("非致命")}
                </Badge>
                <span>{challenge.statement ?? t("（未记录质询内容）")}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="council__block">
        <h4>{t("最终复判")}</h4>
        {entry.final_judgment ? (
          <>
            <p className="council__text">
              {entry.final_judgment.final_judgment ?? t("（未记录判定文本）")}
            </p>
            <p className="council__confidence mono">
              {t("置信度")}{" "}
              {entry.final_judgment.confidence !== null
                ? entry.final_judgment.confidence
                : t("未记录")}
            </p>
          </>
        ) : (
          <Empty>{t("本轮未产出最终复判。")}</Empty>
        )}
      </section>
    </>
  );
}

/** 最终立场汇总：七位科学家的最终复判并排呈现。
 *
 * 不是投票。CLAUDE.md 4 禁止多数投票裁决科研真理，所以这里只有每个席位
 * 自己的预承诺置信度、最终复判与置信度——没有任何聚合分数、没有「多数」、
 * 没有「胜出」。读者看的是七个人的独立立场，而不是一次表决。 */
function StanceSummary({ seats }: { seats: SeatSummary[] }) {
  return (
    <section className="council__stance">
      <h4>{t("最终立场汇总")}</h4>
      <p className="council__stance-note">
        {t(
          "本面板仅并列呈现七位科学家的独立立场；议会不进行多数投票裁决，也不产生任何聚合裁决分数。共识以条件化共识呈现（见下）。",
        )}
      </p>
      <ul className="council__stance-grid">
        {seats.map((entry) => {
          const pre = entry.precommitment?.confidence;
          const final = entry.final_judgment?.confidence;
          return (
            <li key={entry.seat} className="council__stance-card">
              <header className="council__stance-head">
                <span className="council__seat-name">{seatName(entry.seat)}</span>
                {entry.final_judgment?.has_dissent ? (
                  <Badge tone="refuted">{t("异议")}</Badge>
                ) : entry.unavailable_phases.length > 0 ? (
                  <Badge tone="unknown">{t("部分缺席")}</Badge>
                ) : null}
              </header>
              <dl className="council__stance-meta">
                <dt>{t("预承诺置信度")}</dt>
                <dd className="mono">
                  {pre !== null && pre !== undefined ? String(pre) : t("未记录")}
                </dd>
                <dt>{t("最终复判置信度")}</dt>
                <dd className="mono">
                  {final !== null && final !== undefined ? String(final) : t("未记录")}
                </dd>
              </dl>
              {entry.final_judgment?.final_judgment ? (
                <p
                  className="council__stance-text"
                  title={entry.final_judgment.final_judgment}
                >
                  {entry.final_judgment.final_judgment}
                </p>
              ) : (
                <p className="council__stance-text council__stance-text--missing">
                  {t("（本轮未产出最终复判）")}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 条件化共识：联合建模阶段的产出，作为与「投票」截然不同的共识形态
 * 呈现（CLAUDE.md 4）。数据来自 workspace 快照的 consensus 字段。 */
function ConsensusPanel({ consensus }: { consensus: Record<string, unknown> | null }) {
  const text =
    typeof consensus?.conditional_consensus === "string"
      ? consensus.conditional_consensus
      : null;
  const boundaries = Array.isArray(consensus?.boundary_conditions)
    ? (consensus.boundary_conditions as unknown[])
    : [];
  const conflicts = Array.isArray(consensus?.unresolved_conflicts)
    ? (consensus.unresolved_conflicts as unknown[])
    : [];
  const falsifiable = Array.isArray(consensus?.falsification_conditions)
    ? (consensus.falsification_conditions as unknown[])
    : [];

  if (!text) {
    return (
      <section className="council__consensus">
        <h4>{t("条件化共识")}</h4>
        <Empty>{t("联合建模未形成条件化共识。")}</Empty>
      </section>
    );
  }
  return (
    <section className="council__consensus">
      <h4>{t("条件化共识")}</h4>
      <p className="council__consensus-text">{text}</p>
      {boundaries.length > 0 ? (
        <>
          <h5>{t("边界条件")}</h5>
          <ul className="council__consensus-list">
            {boundaries.map((item, index) => (
              <li key={index}>{String(item)}</li>
            ))}
          </ul>
        </>
      ) : null}
      {conflicts.length > 0 ? (
        <>
          <h5>{t("未解决冲突")}</h5>
          <ul className="council__consensus-list">
            {conflicts.map((item, index) => (
              <li key={index}>
                {String(item)}
                <span className="council__consensus-hint">
                  {t("（合并候选，由研究者裁决）")}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {falsifiable.length > 0 ? (
        <>
          <h5>{t("可证伪条件")}</h5>
          <ul className="council__consensus-list">
            {falsifiable.map((item, index) => (
              <li key={index}>{String(item)}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

export function CouncilView({
  seats,
  events,
  consensus,
}: {
  seats: SeatSummary[];
  events: LedgerEvent[];
  consensus?: Record<string, unknown> | null;
}) {
  const timeline = useMemo(() => timelineBySeat(events), [events]);
  const phases = useMemo(() => ranPhases(events), [events]);

  return (
    <Panel
      title={t("七人议会")}
      subtitle={t("每位科学家的思考链路：预承诺 → 各轮次行动 → 最终复判。仅展示结构化科研动作、检索与证据、质询与缺席原因——不展示模型私有推理链（CLAUDE.md 11）。")}
    >
      <StanceSummary seats={seats} />
      <ConsensusPanel consensus={consensus ?? null} />
      {seats.length === 0 ? (
        <Empty>{t("尚未收到任何席位事件。任务可能仍在队列中。")}</Empty>
      ) : (
        <ul className="council__grid">
          {seats.map((entry) => {
            const entries = timeline.get(entry.seat) ?? [];
            return (
              <li key={entry.seat} className="council__seat">
                <header className="council__seat-head">
                  <span className="council__seat-name">{seatName(entry.seat)}</span>
                  {entry.final_judgment?.has_dissent ? (
                    <Badge tone="refuted">{t("异议")}</Badge>
                  ) : entry.unavailable_phases.length > 0 ? (
                    <Badge tone="unknown">{t("部分缺席")}</Badge>
                  ) : (
                    <Badge tone="admitted">{t("完整参与")}</Badge>
                  )}
                </header>

                {entries.length === 0 ? (
                  <SummaryCard entry={entry} />
                ) : (
                  <ol className="council__timeline">
                    {phases.map((phase) => {
                      const phaseEntries = entries.filter(
                        (item) => item.phase === phase,
                      );
                      if (phaseEntries.length === 0) return null;
                      return (
                        <li key={phase} className="council__tl-phase-row">
                          <span className="council__tl-phase">
                            {t(PHASE_LABELS[phase] ?? phase)}
                          </span>
                          <ol className="council__tl-items">
                            {phaseEntries.map((item, index) => (
                              <TimelineEntry key={index} entry={item} />
                            ))}
                          </ol>
                        </li>
                      );
                    })}
                    {/* 没有 PHASE_STARTED 分隔的阶段外事件（极端情况） */}
                    {entries.some((item) => item.phase === null) ? (
                      <li className="council__tl-phase-row">
                        <span className="council__tl-phase">{t("（未定位轮次）")}</span>
                        <ol className="council__tl-items">
                          {entries
                            .filter((item) => item.phase === null)
                            .map((item, index) => (
                              <TimelineEntry key={index} entry={item} />
                            ))}
                        </ol>
                      </li>
                    ) : null}
                  </ol>
                )}

                {entry.unavailable_phases.length > 0 ? (
                  <footer className="council__unavailable">
                    {t("缺席轮次：{0}", entry.unavailable_phases.join("、"))}
                  </footer>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
