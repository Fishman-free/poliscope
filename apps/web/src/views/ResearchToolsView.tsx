/** Research Tools tab: the operational surfaces added on top of the council.
 *
 * - D12 UsagePanel: model/tool cost against the declared budget. No model-
 *   confidence curves (CLAUDE.md 16) -- this is spend and error accounting.
 * - B6 Adjudication: the researcher records process decisions on unresolved
 *   merge candidates and quarantined nodes. Decisions never write the Evidence
 *   Graph (AGENTS.md principle 8); they are appended as RESEARCHER_ADJUDICATION
 *   process events and shown as resolved.
 * - A2 Share: mint/revoke an unauthenticated read-only link.
 * - A3 Time travel: replay a finished task behind a corpus cutoff, and diff
 *   two tasks' claim sets.
 * - A4 Save to knowledge: distil a finished task into one KB text document.
 * - C10 Model hot-swap: repoint a not-yet-running task's model endpoint.
 */

import { useEffect, useMemo, useState } from "react";

import {
  adjudicate,
  compareTasks,
  fetchKnowledgeBases,
  mintShareToken,
  replayAtCutoff,
  revokeShareToken,
  saveTaskToKnowledge,
  setTaskModelOverride,
} from "../api/client";
import type {
  AdjudicationViewData,
  KnowledgeBaseSummary,
  TaskCompare,
  UsageViewData,
  WorkspaceSnapshot,
} from "../api/types";
import { Badge, Empty, Metric, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./ResearchTools.css";

const SWAPPABLE = new Set([
  "QUEUED",
  "PAUSED",
  "AWAITING_COUNCIL_INPUT",
  "AWAITING_CLAIM_CONFIRMATION",
]);

const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_GAPS"]);

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel title={title}>
      <div className="rt-stack">{children}</div>
    </Panel>
  );
}

function useBusy() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setMessage(ok);
    } catch (cause) {
      setMessage(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  }
  return { busy, message, run };
}

/** D12 cost / usage. Deterministic accounting from model_calls/tool_calls. */
function UsageSection({ usage }: { usage: UsageViewData | null | undefined }) {
  const cost = Number(usage?.model.cost_usd ?? 0);
  const budgetCost = usage?.budget?.model_cost_usd
    ? Number(usage.budget.model_cost_usd)
    : null;
  const ratio = budgetCost && budgetCost > 0 ? cost / budgetCost : null;
  if (!usage) {
    return (
      <Section title={t("成本与用量")}>
        <Empty>{t("尚无模型或工具调用记录。")}</Empty>
      </Section>
    );
  }
  return (
    <Section title={t("成本与用量")}>
      <div className="rt-metrics">
        <Metric label={t("模型调用")} value={usage.model.calls} />
        <Metric
          label={t("输入 / 输出 Token")}
          value={`${usage.model.input_tokens} / ${usage.model.output_tokens}`}
        />
        <Metric
          label={t("模型费用（USD）")}
          value={cost.toFixed(4)}
          caveat={
            budgetCost != null ? t("预算 {0}", budgetCost.toFixed(2)) : undefined
          }
          tone={ratio != null && ratio > 1 ? "refuted" : "admitted"}
        />
        <Metric
          label={t("工具调用 / 失败")}
          value={`${usage.tools.calls} / ${usage.tools.error_count}`}
        />
        <Metric
          label={t("模型调用失败")}
          value={usage.model.error_count}
          tone={usage.model.error_count > 0 ? "refuted" : "admitted"}
        />
      </div>
      {ratio != null ? (
        <div className="rt-bar" aria-label={t("费用预算占用")}>
          <div
            className={ratio > 1 ? "rt-bar__fill rt-bar__fill--over" : "rt-bar__fill"}
            style={{ width: `${Math.min(100, Math.round(ratio * 100))}%` }}
          />
        </div>
      ) : null}
    </Section>
  );
}

/** B6 researcher adjudication. */
function AdjudicationSection({
  taskId,
  adjudication,
  onChanged,
}: {
  taskId: string;
  adjudication: AdjudicationViewData | null | undefined;
  onChanged: () => void;
}) {
  const { busy, message, run } = useBusy();
  const items = [
    ...(adjudication?.merge_candidates ?? []),
    ...(adjudication?.quarantined ?? []),
  ];
  async function decide(key: string, decision: string) {
    await run(
      () => adjudicate(taskId, key, decision),
      t("已记录裁决（仅过程事件，不写入证据图）"),
    );
    onChanged();
  }
  return (
    <Section title={t("合并 / 隔离裁决")}>
      <p className="rt-note">
        {t(
          "研究者只记录过程裁决：它影响后续讨论焦点，不会绕过证据门直接改写证据图。",
        )}
      </p>
      {items.length === 0 ? (
        <Empty>{t("当前没有待裁决的合并候选或隔离节点。")}</Empty>
      ) : (
        items.map((item) => (
          <article key={item.key} className="rt-card">
            <header className="rt-card__head">
              <Badge tone={item.kind === "quarantined" ? "refuted" : "provisional"}>
                {item.kind === "quarantined" ? t("隔离节点") : t("合并候选")}
              </Badge>
              {item.resolved ? <Badge tone="admitted">{t("已裁决")}</Badge> : null}
            </header>
            <p className="rt-card__text">{item.detail}</p>
            {item.decisions.length > 0 ? (
              <ul className="rt-decisions">
                {item.decisions.map((decision, index) => (
                  <li key={index} className="mono">
                    {decision.decided_by ?? "?"}: {decision.decision}
                    {decision.note ? `（${decision.note}）` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
            {!item.resolved ? (
              <div className="rt-actions">
                {item.kind === "merge_candidate" ? (
                  <>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void decide(item.key, "保持分离")}
                    >
                      {t("保持分离")}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void decide(item.key, "标记相关，继续观察")}
                    >
                      {t("标记相关")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void decide(item.key, "维持隔离")}
                    >
                      {t("维持隔离")}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void decide(item.key, "提请证据审计复核")}
                    >
                      {t("提请复核")}
                    </button>
                  </>
                )}
              </div>
            ) : null}
          </article>
        ))
      )}
      {message ? <p className="rt-message">{message}</p> : null}
    </Section>
  );
}

/** A2 read-only share. */
function ShareSection({ taskId }: { taskId: string }) {
  const [token, setToken] = useState<string | null>(null);
  const { busy, message, run } = useBusy();
  const url = token ? `${window.location.origin}/shared/${token}` : null;
  return (
    <Section title={t("只读分享链接")}>
      <p className="rt-note">
        {t("链接无需登录即可查看脱敏后的研究快照；重新生成会使旧链接失效。")}
      </p>
      <div className="rt-actions">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void run(async () => setToken((await mintShareToken(taskId)).share_token), t("已生成分享链接"))
          }
        >
          {t("生成 / 轮换链接")}
        </button>
        <button
          type="button"
          disabled={busy || !token}
          onClick={() =>
            void run(async () => {
              await revokeShareToken(taskId);
              setToken(null);
            }, t("已撤销分享链接"))
          }
        >
          {t("撤销链接")}
        </button>
        {url ? (
          <button
            type="button"
            onClick={() => void navigator.clipboard.writeText(url)}
          >
            {t("复制链接")}
          </button>
        ) : null}
      </div>
      {url ? <code className="rt-link mono">{url}</code> : null}
      {message ? <p className="rt-message">{message}</p> : null}
    </Section>
  );
}

/** A3 time-travel replay + compare. */
function TimeTravelSection({
  taskId,
  status,
  onOpenTask,
}: {
  taskId: string;
  status: string;
  onOpenTask: (id: string) => void;
}) {
  const [year, setYear] = useState("");
  const [otherId, setOtherId] = useState("");
  const [diff, setDiff] = useState<TaskCompare | null>(null);
  const { busy, message, run } = useBusy();
  const canReplay = TERMINAL.has(status);
  return (
    <Section title={t("时间旅行（语料截止复跑）")}>
      <p className="rt-note">
        {t(
          "按年份封闭语料后克隆复跑，原任务保留以便对比；这是 ForesightBlindspot 时间切片的产品化入口。",
        )}
      </p>
      <div className="rt-actions">
        <input
          type="number"
          min="1900"
          max="2100"
          placeholder={t("截止年份，如 2018")}
          value={year}
          onChange={(event) => setYear(event.target.value)}
          aria-label={t("语料截止年份")}
        />
        <button
          type="button"
          disabled={busy || !canReplay || !/^\d{4}$/.test(year)}
          onClick={() =>
            void run(async () => {
              const result = await replayAtCutoff(taskId, `${year}-12-31`);
              onOpenTask(result.task_id);
            }, t("已创建截止复跑任务"))
          }
        >
          {canReplay ? t("按此年份复跑") : t("任务完成后才能复跑")}
        </button>
      </div>

      <h3 className="rt-subhead">{t("与另一任务对比主张集合")}</h3>
      <div className="rt-actions">
        <input
          type="text"
          placeholder={t("另一个任务的 task_id")}
          value={otherId}
          onChange={(event) => setOtherId(event.target.value.trim())}
          aria-label={t("对比任务 ID")}
        />
        <button
          type="button"
          disabled={busy || otherId.length < 8}
          onClick={() => void run(async () => setDiff(await compareTasks(taskId, otherId)), t("对比完成"))}
        >
          {t("对比")}
        </button>
      </div>
      {diff ? (
        <div className="rt-diff">
          <div>
            <h4>{t("仅本任务有（{0}）", diff.only_in_a.length)}</h4>
            <ul>{diff.only_in_a.map((text) => <li key={text}>{text}</li>)}</ul>
          </div>
          <div>
            <h4>{t("双方共有（{0}）", diff.shared.length)}</h4>
            <ul>{diff.shared.map((text) => <li key={text}>{text}</li>)}</ul>
          </div>
          <div>
            <h4>{t("仅对比任务有（{0}）", diff.only_in_b.length)}</h4>
            <ul>{diff.only_in_b.map((text) => <li key={text}>{text}</li>)}</ul>
          </div>
        </div>
      ) : null}
      {message ? <p className="rt-message">{message}</p> : null}
    </Section>
  );
}

/** C10 model hot-swap. */
function ModelOverrideSection({
  taskId,
  status,
  onChanged,
}: {
  taskId: string;
  status: string;
  onChanged: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const { busy, message, run } = useBusy();
  const swappable = SWAPPABLE.has(status);
  async function apply() {
    await run(
      () =>
        setTaskModelOverride(taskId, {
          baseUrl: baseUrl.trim(),
          apiKey,
          modelName: modelName.trim() || undefined,
        }),
      t("已为本任务切换模型端点"),
    );
    onChanged();
  }
  return (
    <Section title={t("本任务模型热切换")}>
      <p className="rt-note">
        {swappable
          ? t("仅在任务尚未实际运行时可改；运行中途不允许切换。")
          : t("任务正在运行或已结束，当前状态不可切换模型（避免在 LLM 调用中途改端点）。")}
      </p>
      <fieldset className="rt-fieldset" disabled={!swappable}>
        <input
          type="text"
          placeholder="Base URL，如 https://api.openai.com/v1"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          aria-label={t("模型 Base URL")}
        />
        <input
          type="password"
          placeholder={t("API Key（仅保存，不回显）")}
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          aria-label={t("API Key")}
        />
        <input
          type="text"
          placeholder={t("模型名（可选）")}
          value={modelName}
          onChange={(event) => setModelName(event.target.value)}
          aria-label={t("模型名")}
        />
        <div className="rt-actions">
          <button
            type="button"
            disabled={busy || !baseUrl.trim() || !apiKey}
            onClick={() => void apply()}
          >
            {t("应用为本任务端点")}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await setTaskModelOverride(taskId, null, true);
                setBaseUrl("");
                setApiKey("");
                setModelName("");
              }, t("已清除本任务覆盖，回到继承配置"))
            }
          >
            {t("清除覆盖")}
          </button>
        </div>
      </fieldset>
      {message ? <p className="rt-message">{message}</p> : null}
    </Section>
  );
}

/** A4 distil into a knowledge base. */
function SaveToKnowledgeSection({
  taskId,
  status,
}: {
  taskId: string;
  status: string;
}) {
  const [bases, setBases] = useState<KnowledgeBaseSummary[]>([]);
  const [kbId, setKbId] = useState("");
  const { busy, message, run } = useBusy();
  useEffect(() => {
    void fetchKnowledgeBases()
      .then((list) => {
        setBases(list);
        if (list[0]) setKbId(list[0].id);
      })
      .catch(() => undefined);
  }, []);
  const canSave = TERMINAL.has(status);
  return (
    <Section title={t("沉淀到知识库")}>
      <p className="rt-note">
        {t(
          "把已确认主张、发现、盲点与异议整理成一篇带谱系头与 AI 辅助声明的文本文档，供后续任务作为 Level A 用户证据引用。",
        )}
      </p>
      <div className="rt-actions">
        <select
          value={kbId}
          onChange={(event) => setKbId(event.target.value)}
          aria-label={t("选择知识库")}
        >
          {bases.length === 0 ? <option value="">{t("（暂无知识库）")}</option> : null}
          {bases.map((base) => (
            <option key={base.id} value={base.id}>
              {base.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy || !canSave || !kbId}
          onClick={() =>
            void run(() => saveTaskToKnowledge(taskId, kbId), t("已沉淀为知识库文档"))
          }
        >
          {canSave ? t("沉淀本次研究") : t("任务完成后才能沉淀")}
        </button>
      </div>
      {message ? <p className="rt-message">{message}</p> : null}
    </Section>
  );
}

export function ResearchToolsView({
  taskId,
  snapshot,
  onChanged,
  onOpenTask,
}: {
  taskId: string;
  snapshot: WorkspaceSnapshot;
  onChanged: () => void;
  onOpenTask: (id: string) => void;
}) {
  const sections = useMemo(
    () => [
      <UsageSection key="usage" usage={snapshot.usage} />,
      <AdjudicationSection
        key="adjudication"
        taskId={taskId}
        adjudication={snapshot.adjudication}
        onChanged={onChanged}
      />,
      <TimeTravelSection
        key="timetravel"
        taskId={taskId}
        status={snapshot.task.status}
        onOpenTask={onOpenTask}
      />,
      <ModelOverrideSection
        key="override"
        taskId={taskId}
        status={snapshot.task.status}
        onChanged={onChanged}
      />,
      <ShareSection key="share" taskId={taskId} />,
      <SaveToKnowledgeSection key="save" taskId={taskId} status={snapshot.task.status} />,
    ],
    [taskId, snapshot, onChanged, onOpenTask],
  );
  return <div className="rt-columns">{sections}</div>;
}
