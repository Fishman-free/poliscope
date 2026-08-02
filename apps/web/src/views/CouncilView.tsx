/** 7 人议会状态.
 *
 * CLAUDE.md 11 lists the council panel as its own required surface, distinct
 * from the Audit Trail: this shows each seat's structured actions -- the
 * precommitment, the challenges it raised, its final judgment -- not a feed
 * of every event in order. An absent or partially-unavailable seat stays a
 * visible card, never an omitted one; a single seat's failure elsewhere in
 * the pipeline must not make it disappear from the panel that reports on it.
 *
 * No seat's private chain of thought is shown, only the same self-reported
 * strings the round itself already emitted as structured fields.
 */

import type { Seat, SeatSummary } from "../api/types";
import { SEAT_LABELS } from "../api/types";
import { Badge, Empty, Panel } from "../components/primitives";

import "./CouncilView.css";

export function CouncilView({ seats }: { seats: SeatSummary[] }) {
  return (
    <Panel
      title="七人议会"
      subtitle="每位科学家的预承诺、提出的质询与最终复判——不展示模型私有思维链。"
    >
      {seats.length === 0 ? (
        <Empty>尚未收到任何席位事件。任务可能仍在队列中。</Empty>
      ) : (
        <ul className="council__grid">
          {seats.map((entry) => (
            <li key={entry.seat} className="council__seat">
              <header className="council__seat-head">
                <span className="council__seat-name">
                  {SEAT_LABELS[entry.seat as Seat] ?? entry.seat}
                </span>
                {entry.final_judgment?.has_dissent ? (
                  <Badge tone="refuted">异议</Badge>
                ) : entry.unavailable_phases.length > 0 ? (
                  <Badge tone="unknown">部分缺席</Badge>
                ) : (
                  <Badge tone="admitted">完整参与</Badge>
                )}
              </header>

              <section className="council__block">
                <h4>预承诺</h4>
                {entry.precommitment ? (
                  <>
                    <p className="council__confidence mono">
                      置信度{" "}
                      {entry.precommitment.confidence !== null
                        ? entry.precommitment.confidence
                        : "未记录"}
                    </p>
                    <p className="council__text">
                      {entry.precommitment.update_condition ?? "未记录更新条件"}
                    </p>
                  </>
                ) : (
                  <Empty>本轮未记录预承诺。</Empty>
                )}
              </section>

              <section className="council__block">
                <h4>提出的质询（{entry.challenges_raised.length}）</h4>
                {entry.challenges_raised.length === 0 ? (
                  <Empty>本轮未提出质询。</Empty>
                ) : (
                  <ul className="council__challenges">
                    {entry.challenges_raised.map((challenge, index) => (
                      <li key={index}>
                        <Badge tone={challenge.is_fatal ? "refuted" : "provisional"}>
                          {challenge.is_fatal ? "致命" : "非致命"}
                        </Badge>
                        <span>{challenge.statement ?? "（未记录质询内容）"}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="council__block">
                <h4>最终复判</h4>
                {entry.final_judgment ? (
                  <>
                    <p className="council__text">
                      {entry.final_judgment.final_judgment ?? "（未记录判定文本）"}
                    </p>
                    <p className="council__confidence mono">
                      置信度{" "}
                      {entry.final_judgment.confidence !== null
                        ? entry.final_judgment.confidence
                        : "未记录"}
                    </p>
                  </>
                ) : (
                  <Empty>本轮未产出最终复判。</Empty>
                )}
              </section>

              {entry.unavailable_phases.length > 0 ? (
                <footer className="council__unavailable">
                  缺席轮次：{entry.unavailable_phases.join("、")}
                </footer>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
