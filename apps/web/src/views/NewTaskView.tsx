/** The default first screen: turn a plain question into a running task.
 *
 * Two steps, not one form: `POST /api/tasks` only drafts the contract and
 * proposes claims, it never queues research on its own (CLAUDE.md 2/5.3) --
 * so this view has to show the researcher what got proposed and let them
 * confirm before anything is queued, not paper over that step for the sake
 * of a one-click flow.
 */

import { type FormEvent, useEffect, useState } from "react";

import {
  confirmClaims,
  createTask,
  DEFAULT_NEW_TASK_OPTIONS,
  fetchKnowledgeBases,
  type NewTaskOptions,
  uploadPaper,
} from "../api/client";
import type { KnowledgeBaseSummary, SuggestedClaim } from "../api/types";
import { Empty, Panel } from "../components/primitives";

import "./NewTaskView.css";

const EVIDENCE_PRIORITY_LABELS: Record<string, string> = {
  CORRELATION: "相关关系",
  CAUSAL_OR_REVERSE_CAUSAL: "因果 / 反向因果",
  MEASUREMENT: "测量与构念",
  REPLICATION: "复现与统计",
  BOUNDARY: "适用边界",
  MECHANISM: "作用机制",
  NULL_OR_COUNTEREXAMPLE: "零效应 / 反例",
};

function splitList(value: string): string[] {
  // Commas or newlines -- the evidence textareas are one-per-line lists, the
  // population/region/language inputs are comma-separated, and both should
  // feed the same splitting rule.
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function NewTaskView({ onCreated }: { onCreated: (taskId: string) => void }) {
  const [question, setQuestion] = useState("");
  const [populations, setPopulations] = useState(
    DEFAULT_NEW_TASK_OPTIONS.populations.join(", "),
  );
  const [regions, setRegions] = useState(DEFAULT_NEW_TASK_OPTIONS.regions.join(", "));
  const [languages, setLanguages] = useState(DEFAULT_NEW_TASK_OPTIONS.languages.join(", "));
  const [priorities, setPriorities] = useState<string[]>(
    DEFAULT_NEW_TASK_OPTIONS.evidencePriorities,
  );
  const [allowPreprints, setAllowPreprints] = useState(
    DEFAULT_NEW_TASK_OPTIONS.allowPreprints,
  );
  const [wallClockMinutes, setWallClockMinutes] = useState(
    DEFAULT_NEW_TASK_OPTIONS.wallClockMinutes,
  );
  const [toolCallLimit, setToolCallLimit] = useState(DEFAULT_NEW_TASK_OPTIONS.toolCallLimit);
  const [sourceLimit, setSourceLimit] = useState(DEFAULT_NEW_TASK_OPTIONS.sourceLimit);
  // 任务级模型设置：留空使用系统默认模型（部署配置的 DeepSeek）；
  // 填写后本次任务使用研究者自己的 OpenAI 兼容接口。
  const [modelBaseUrl, setModelBaseUrl] = useState("");
  const [modelApiKey, setModelApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  // 用户提供的证据（可选）：DOI 与 BibTeX 文本。只随创建任务提交，
  // 工作台不提供任务创建后的编辑入口（YAGNI）。
  const [doisText, setDoisText] = useState("");
  const [bibtexText, setBibtexText] = useState("");
  // 知识库（长期记忆）：文档作为 Level A 用户提供源交给议会。
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    fetchKnowledgeBases()
      .then((bases) => {
        if (!cancelled) setKnowledgeBases(bases);
      })
      .catch(() => {
        // 拉取失败不阻塞建任务：知识库是可选项。
        if (!cancelled) setKnowledgeBases([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [phase, setPhase] = useState<"question" | "claims">("question");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [claims, setClaims] = useState<SuggestedClaim[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // PDF 上传（claims 阶段，任务已创建后才可能挂对象）：本会话已上传列表
  // 只存在组件状态里，不做回读端点（YAGNI——已上传管理的完整视图留给知识库页）。
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<{ name: string; size: number }[]>([]);

  async function handleFiles(files: FileList | null) {
    if (!taskId || !files || uploading) return;
    setUploading(true);
    setUploadError(null);
    const fresh: { name: string; size: number }[] = [];
    for (const file of Array.from(files)) {
      const looksPdf =
        file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
      if (!looksPdf) {
        setUploadError(`「${file.name}」不是 PDF 文件，已跳过`);
        continue;
      }
      try {
        const result = await uploadPaper(taskId, file);
        fresh.push({ name: file.name, size: result.size_bytes });
      } catch (cause) {
        setUploadError(
          `「${file.name}」上传失败：${cause instanceof Error ? cause.message : String(cause)}`,
        );
      }
    }
    if (fresh.length > 0) setUploads((prev) => [...prev, ...fresh]);
    setUploading(false);
  }

  function togglePriority(key: string) {
    setPriorities((prev) =>
      prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key],
    );
  }

  function toggleClaim(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function submitQuestion(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const options: NewTaskOptions = {
        populations: splitList(populations),
        regions: splitList(regions),
        languages: splitList(languages),
        evidencePriorities: priorities.length ? priorities : ["CORRELATION"],
        allowPreprints,
        wallClockMinutes,
        toolCallLimit,
        sourceLimit,
        dois: splitList(doisText),
        bibtexEntries: splitList(bibtexText),
        knowledgeBaseId: knowledgeBaseId || null,
        modelConfig:
          modelBaseUrl.trim() && modelApiKey.trim()
            ? {
                baseUrl: modelBaseUrl.trim(),
                apiKey: modelApiKey.trim(),
                modelName: modelName.trim() || undefined,
              }
            : null,
      };
      const created = await createTask(question.trim(), options);
      setTaskId(created.task_id);
      setClaims(created.suggested_claims);
      setSelected(new Set(created.suggested_claims.map((claim) => claim.id)));
      setPhase("claims");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmAndStart() {
    if (!taskId || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await confirmClaims(taskId, Array.from(selected));
      onCreated(taskId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setSubmitting(false);
    }
  }

  if (phase === "claims") {
    return (
      <Panel
        key="claims"
        className="fade-in-up"
        title="确认要调查的原子主张"
        subtitle="七人议会只会调查下面确认的主张。取消勾选不会删除——它仍会被记录，随时可在证据图里追溯。"
      >
        {claims.length === 0 ? (
          <Empty>没能从这个问题里拆出任何主张，请返回修改问题的表述。</Empty>
        ) : (
          <ul className="newtask__claims">
            {claims.map((claim) => (
              <li key={claim.id} className="newtask__claim">
                <label className="newtask__claim-row">
                  <input
                    type="checkbox"
                    checked={selected.has(claim.id)}
                    onChange={() => toggleClaim(claim.id)}
                  />
                  <span className="newtask__claim-body">
                    <span className="newtask__claim-statement">{claim.statement}</span>
                    <span className="newtask__claim-meta mono">
                      {claim.claim_type} · 证伪条件：{claim.falsification_condition}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        <section className="newtask__upload">
          <h4>补充 PDF 文献（可选，单个不超过 20 MB）</h4>
          <p className="newtask__upload-hint">
            上传的 PDF 会作为用户提供的证据，交给议会做全文核验后按 Level A 进入证据图。
          </p>
          <input
            type="file"
            accept="application/pdf"
            multiple
            disabled={submitting || uploading}
            onChange={(event) => handleFiles(event.target.files)}
          />
          {uploads.length > 0 ? (
            <ul className="newtask__upload-list">
              {uploads.map((item, index) => (
                <li key={index}>
                  {item.name}（{(item.size / 1024).toFixed(0)} KB，已加入证据）
                </li>
              ))}
            </ul>
          ) : null}
          {uploadError ? (
            <p className="newtask__error" role="alert">
              {uploadError}
            </p>
          ) : null}
        </section>

        {error ? (
          <p className="newtask__error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="newtask__actions">
          <button
            type="button"
            className="button"
            onClick={() => setPhase("question")}
            disabled={submitting}
          >
            返回修改问题
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={confirmAndStart}
            disabled={submitting || selected.size === 0}
          >
            {submitting ? "提交中…" : "确认并开始研究"}
          </button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      key="question"
      className="fade-in-up"
      title="打开一个新的研究任务"
      subtitle="七人议会会围绕这个问题独立取证、交叉质询，产出一张可审计的证据地图，而不是一段读起来通顺的摘要。"
    >
      <form className="newtask__form" onSubmit={submitQuestion}>
        <label className="newtask__label" htmlFor="new-task-question">
          研究问题
        </label>
        <textarea
          id="new-task-question"
          className="newtask__textarea"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="例如：社交媒体使用时长是否会降低青少年的心理健康水平？"
          rows={3}
          disabled={submitting}
        />

        <details className="newtask__advanced">
          <summary>高级选项（人群 / 地区 / 语言 / 证据侧重 / 预算，不填则使用默认值）</summary>
          <div className="newtask__advanced-grid">
            <label className="newtask__field">
              人群（逗号分隔）
              <input
                value={populations}
                onChange={(event) => setPopulations(event.target.value)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              地区（逗号分隔）
              <input
                value={regions}
                onChange={(event) => setRegions(event.target.value)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              语言（逗号分隔）
              <input
                value={languages}
                onChange={(event) => setLanguages(event.target.value)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field newtask__field--checkbox">
              <input
                type="checkbox"
                checked={allowPreprints}
                onChange={(event) => setAllowPreprints(event.target.checked)}
                disabled={submitting}
              />
              允许预印本作为证据来源
            </label>
            <label className="newtask__field">
              时间预算（分钟）
              <input
                type="number"
                min={1}
                value={wallClockMinutes}
                onChange={(event) =>
                  setWallClockMinutes(Number(event.target.value) || 1)
                }
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              工具调用上限
              <input
                type="number"
                min={1}
                value={toolCallLimit}
                onChange={(event) => setToolCallLimit(Number(event.target.value) || 1)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              来源篇数上限
              <input
                type="number"
                min={1}
                value={sourceLimit}
                onChange={(event) => setSourceLimit(Number(event.target.value) || 1)}
                disabled={submitting}
              />
            </label>
          </div>

          <fieldset className="newtask__priorities">
            <legend>证据侧重（可多选，默认相关关系）</legend>
            {Object.entries(EVIDENCE_PRIORITY_LABELS).map(([key, label]) => (
              <label key={key} className="newtask__priority">
                <input
                  type="checkbox"
                  checked={priorities.includes(key)}
                  onChange={() => togglePriority(key)}
                  disabled={submitting}
                />
                {label}
              </label>
            ))}
          </fieldset>
        </details>

        <details className="newtask__advanced">
          <summary>
            用户提供的证据（可选）——知识库、DOI 与 BibTeX，会作为正式证据源交给议会核验
          </summary>
          <div className="newtask__advanced-grid">
            <label className="newtask__field newtask__field--wide">
              关联知识库（可选，长期记忆——其中的文档会作为 Level A 用户提供源）
              <select
                value={knowledgeBaseId}
                onChange={(event) => setKnowledgeBaseId(event.target.value)}
                disabled={submitting || knowledgeBases.length === 0}
              >
                <option value="">不关联（默认从公开文献检索）</option>
                {knowledgeBases.map((kb) => (
                  <option key={kb.id} value={kb.id}>
                    {kb.name}（{kb.document_count} 篇文档）
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="newtask__advanced-grid">
            <label className="newtask__field newtask__field--wide">
              DOI（每行一个，或逗号分隔）
              <textarea
                className="newtask__textarea newtask__textarea--compact"
                placeholder={"10.1000/example\n10.1016/j.jadohealth.2023.01.001"}
                value={doisText}
                onChange={(event) => setDoisText(event.target.value)}
                disabled={submitting}
                rows={3}
                spellCheck={false}
              />
            </label>
            <label className="newtask__field newtask__field--wide">
              BibTeX 条目（粘贴即可，系统会从中提取 DOI；无法提取的条目不会被消费）
              <textarea
                className="newtask__textarea newtask__textarea--compact"
                placeholder={"@article{example,\n  doi = {10.1000/example},\n}"}
                value={bibtexText}
                onChange={(event) => setBibtexText(event.target.value)}
                disabled={submitting}
                rows={4}
                spellCheck={false}
              />
            </label>
          </div>
        </details>

        <details className="newtask__advanced">
          <summary>
            模型设置（可选）——留空使用系统默认模型，填写则本次任务使用你自己的模型接口
          </summary>
          <div className="newtask__advanced-grid">
            <label className="newtask__field">
              Base URL（例如 https://api.deepseek.com）
              <input
                type="url"
                placeholder="https://…"
                value={modelBaseUrl}
                onChange={(event) => setModelBaseUrl(event.target.value)}
                disabled={submitting}
                spellCheck={false}
              />
            </label>
            <label className="newtask__field">
              API Key
              <input
                type="password"
                autoComplete="off"
                value={modelApiKey}
                onChange={(event) => setModelApiKey(event.target.value)}
                disabled={submitting}
                spellCheck={false}
              />
            </label>
            <label className="newtask__field">
              模型名（可留空，默认 deepseek-chat）
              <input
                placeholder="deepseek-chat"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                disabled={submitting}
                spellCheck={false}
              />
            </label>
            <p className="newtask__model-note">
              API Key 只随本次任务存储，任何页面都不会回显；不填则使用部署方配置的系统默认模型。
            </p>
          </div>
        </details>

        {error ? (
          <p className="newtask__error" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          className="button button--primary"
          disabled={submitting || !question.trim()}
        >
          {submitting ? "创建中…" : "开始研究"}
        </button>
      </form>

      <p className="newtask__safety">
        本系统为科研辅助工具，不提供医学诊断或医疗建议。
      </p>
    </Panel>
  );
}
