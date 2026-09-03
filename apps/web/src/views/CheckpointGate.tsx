/** JOINT_MODELING 前的人类引导检查点.
 *
 * Shown while the task is halted at AWAITING_COUNCIL_INPUT, right after
 * BLINDSPOT_BOUNTY. The seven scientists' positions here are the same
 * structured fields CouncilView already renders (precommitment, challenges
 * raised) -- reused via `snapshot.seats` rather than a second fetch, since
 * both are built from the identical `_seats()` backend helper and a second
 * fetch could only ever drift from this one.
 *
 * This is advisory context, not a ballot -- CLAUDE.md 4/8 forbid letting a
 * human "vote" decide scientific truth. Submitting an empty guidance_text is
 * exactly as valid an action as submitting a directional note; the button
 * label says so plainly rather than implying that leaving the box blank is
 * an incomplete action.
 */

import { useEffect, useState } from "react";

import { submitCouncilGuidance } from "../api/client";
import type { Seat, SeatSummary } from "../api/types";
import { SEAT_LABELS } from "../api/types";
import { Badge, Empty, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./CheckpointGate.css";

/** Countdown to the worker's automatic resume (default 5-minute grace). The
 * clock starts when the researcher first sees the gate; the server is the
 * authority and the SSE refresh swaps this panel away the moment it resumes. */
function AutoResumeHint() {
  const [left, setLeft] = useState(300);
  useEffect(() => {
    const id = window.setInterval(
      () => setLeft((value) => Math.max(0, value - 1)),
      1000,
    );
    return () => window.clearInterval(id);
  }, []);
  const stamp = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(
    left % 60,
  ).padStart(2, "0")}`;
  return (
    <p className="checkpoint__auto">
      {left > 0
        ? t("不操作也没关系：{0} 后议会将自动进入联合建模。", stamp)
        : t("正在自动继续，请稍候…")}
    </p>
  );
}

export function CheckpointGate({
  taskId,
  seats,
  onSubmitted,
}: {
  taskId: string;
  seats: SeatSummary[];
  onSubmitted: () => void;
}) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await submitCouncilGuidance(taskId, text);
      onSubmitted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setSubmitting(false);
    }
  }

  return (
    <Panel
      title={t("议会检查点：等待研究者方向性引导")}
      subtitle={t("七位科学家在盲点悬赏阶段结束时的立场仅供参考——这不是投票，您的意见不会改变任何证据判定。")}
    >
      {seats.length === 0 ? (
        <Empty>{t("尚未收到任何席位事件。")}</Empty>
      ) : (
        <ul className="checkpoint__grid">
          {seats.map((entry) => (
            <li key={entry.seat} className="checkpoint__seat">
              <header className="checkpoint__seat-head">
                <span className="checkpoint__seat-name">
                  {SEAT_LABELS[entry.seat as Seat] ?? entry.seat}
                </span>
                {entry.unavailable_phases.length > 0 ? (
                  <Badge tone="unknown">{t("部分缺席")}</Badge>
                ) : (
                  <Badge tone="admitted">{t("完整参与")}</Badge>
                )}
              </header>
              {entry.precommitment ? (
                <>
                  <p className="checkpoint__confidence mono">
                    {t("置信度")}{" "}
                    {entry.precommitment.confidence !== null
                      ? entry.precommitment.confidence
                      : t("未记录")}
                  </p>
                  <p className="checkpoint__text">
                    {entry.precommitment.update_condition ?? t("未记录更新条件")}
                  </p>
                </>
              ) : (
                <Empty>{t("本轮未记录预承诺。")}</Empty>
              )}
              <p className="checkpoint__challenge-count">
                {t("提出质询 {0} 条", entry.challenges_raised.length)}
              </p>
            </li>
          ))}
        </ul>
      )}

      <div className="checkpoint__guidance">
        <label htmlFor="council-guidance" className="checkpoint__label">
          {t("方向性备注（可留空，直接继续；不会作为证据或科学判断使用）")}
        </label>
        <textarea
          id="council-guidance"
          className="checkpoint__textarea"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={t("例如：优先讨论跨文化适用边界")}
          rows={4}
          disabled={submitting}
        />
        {error ? (
          <p className="checkpoint__error" role="alert">
            {error}
          </p>
        ) : null}
        <AutoResumeHint />
        <button
          type="button"
          className="button"
          onClick={submit}
          disabled={submitting}
        >
          {submitting
            ? t("提交中…")
            : text.trim()
              ? t("提交引导并继续")
              : t("不干预，直接继续")}
        </button>
      </div>
    </Panel>
  );
}
