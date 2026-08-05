/** 实时进展：运行中议会的思考链路可视化（CLAUDE.md 11）。
 *
 * 与 skill 调用的「跑完再看」不同，网页版要让研究者实时知道模型在干什么：
 * 数据来自两条流 —— 账本阶段事件（PHASE_STARTED/PHASE_COMPLETED 驱动阶段
 * 时间线）与 process_stream 过程流（token/推理片段/工具调用，独立连接）。
 *
 * 渲染纪律（CLAUDE.md 11）：只展示结构化过程轨迹 —— 阶段推进、席位思考
 * 片段、检索与文献链接 —— 不展示模型私有思维链以外的任何推测；这里出现
 * 的任何内容都不是正式结论，正式结论以 Research Brief 为准。token 流是
 * 易逝过程数据：重连后从服务端重放并由 seq 去重，不作为审计依据。
 */

import { useEffect, useMemo, useRef } from "react";

import type { LedgerEvent, ProcessEvent } from "../api/types";
import { Empty } from "../components/primitives";

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

const SEAT_LABELS: Record<string, string> = {
  theory_builder: "理论建构者",
  causal_scientist: "因果推断专家",
  measurement_scientist: "测量与构念专家",
  replication_scientist: "统计与复现专家",
  boundary_scientist: "边界与情境专家",
  adversarial_falsifier: "对抗性证伪者",
  evidence_auditor: "证据与溯源审计员",
};

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
 * reasoning/token 片段，至最近的 model_done 截断。倒序遍历取最后一段。 */
function seatStreams(
  processEvents: ProcessEvent[],
): Record<string, { phase: string; text: string; running: boolean }> {
  const result: Record<
    string,
    { phase: string; text: string; running: boolean }
  > = {};
  const current: Record<string, { phase: string; parts: string[]; running: boolean }> =
    {};
  for (const event of processEvents) {
    const payload = event.payload as Record<string, unknown>;
    const seat = typeof payload.seat === "string" ? payload.seat : null;
    if (event.kind === "seat_deliberation" && seat) {
      current[seat] = {
        phase: typeof payload.phase === "string" ? payload.phase : "",
        parts: [],
        running: true,
      };
    } else if (seat && current[seat]) {
      const text = typeof payload.text === "string" ? payload.text : "";
      if (event.kind === "model_reasoning" || event.kind === "model_token") {
        if (text) current[seat].parts.push(text);
      } else if (event.kind === "model_done") {
        current[seat].running = false;
      }
    }
  }
  for (const [seat, entry] of Object.entries(current)) {
    result[seat] = { phase: entry.phase, text: entry.parts.join(""), running: entry.running };
  }
  return result;
}

export function LiveView({
  events,
  processEvents,
}: {
  events: LedgerEvent[];
  processEvents: ProcessEvent[];
}) {
  const { current, done } = useMemo(() => phaseProgress(events), [events]);
  const streams = useMemo(() => seatStreams(processEvents), [processEvents]);

  // 工具调用日志（检索/DOI 解析），命中带可点击链接。
  const toolLog = useMemo(
    () =>
      processEvents.filter(
        (event) => event.kind === "tool_call" || event.kind === "tool_result",
      ),
    [processEvents],
  );

  const scrollRef = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    // 新内容到达时把每个有流的席位面板滚到底 —— 研究者在看「现在」，不是
    // 往回翻。手动上滚会被下一次 flush 拉回，这是实时视图的取舍。
    for (const ref of Object.values(scrollRef.current)) {
      if (ref) ref.scrollTop = ref.scrollHeight;
    }
  }, [processEvents.length]);

  const anyTrace = processEvents.length > 0;

  return (
    <div className="live">
      <section className="live__phases" aria-label="八阶段进度">
        {PHASES.map((phase) => {
          const isDone = done.has(phase.id);
          const isCurrent = current === phase.id;
          return (
            <span
              key={phase.id}
              className={
                "live__phase" +
                (isDone ? " live__phase--done" : "") +
                (isCurrent ? " live__phase--current" : "")
              }
              title={phase.label}
            >
              {isDone ? "✓ " : ""}
              {phase.label}
            </span>
          );
        })}
      </section>

      {!anyTrace ? (
        <Empty>任务已入队。Worker 开始运行后，这里会实时显示阶段推进、七位科学家的思考与检索过程。</Empty>
      ) : (
        <div className="live__grid">
          <section className="live__seats" aria-label="席位思考流">
            {Object.entries(streams).length === 0 ? (
              <Empty>还没有席位开始思考。</Empty>
            ) : (
              Object.entries(streams).map(([seat, entry]) => (
                <div key={seat} className="live__seat">
                  <div className="live__seat-head">
                    <span className="live__seat-name">
                      {SEAT_LABELS[seat] ?? seat}
                    </span>
                    {entry.phase ? (
                      <span className="live__seat-phase mono">{entry.phase}</span>
                    ) : null}
                    {entry.running ? (
                      <span className="live__seat-running">思考中…</span>
                    ) : null}
                  </div>
                  <div
                    className="live__seat-text mono"
                    ref={(node) => {
                      scrollRef.current[seat] = node;
                    }}
                  >
                    {entry.text || "（尚无输出）"}
                  </div>
                </div>
              ))
            )}
          </section>

          <section className="live__tools" aria-label="检索与文献">
            <h3>检索与文献</h3>
            {toolLog.length === 0 ? (
              <Empty>还没有检索活动。</Empty>
            ) : (
              <ol className="live__tool-log">
                {toolLog.map((event, index) => {
                  const payload = event.payload as Record<string, unknown>;
                  if (event.kind === "tool_call") {
                    return (
                      <li key={index} className="live__tool-call">
                        <span className="live__tool-kind mono">
                          {payload.kind === "doi_lookup" ? "DOI 解析" : "检索"}
                        </span>
                        <span className="live__tool-query">{String(payload.query ?? "")}</span>
                        {Array.isArray(payload.seats) ? (
                          <span className="live__tool-seats mono">
                            {payload.seats.join(", ")}
                          </span>
                        ) : null}
                      </li>
                    );
                  }
                  const url = typeof payload.url === "string" ? payload.url : null;
                  if (payload.miss === true) {
                    return (
                      <li key={index} className="live__tool-result live__tool-result--miss">
                        <span className="live__tool-kind mono">未命中</span>
                        <span className="live__tool-query">{String(payload.query ?? "")}</span>
                      </li>
                    );
                  }
                  return (
                    <li key={index} className="live__tool-result">
                      <span className="live__tool-kind mono">命中</span>
                      <span className="live__tool-title">
                        {String(payload.title ?? payload.doi ?? "")}
                      </span>
                      {url ? (
                        <a
                          className="live__tool-link"
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          打开来源 ↗
                        </a>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
