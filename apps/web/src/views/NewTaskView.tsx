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
  createKnowledgeBase,
  createTask,
  DEFAULT_NEW_TASK_OPTIONS,
  fetchKnowledgeBases,
  fetchSkills,
  type NewTaskOptions,
  uploadPaper,
} from "../api/client";
import type { KnowledgeBaseSummary, SkillSummary, SuggestedClaim } from "../api/types";
import { Empty, Panel } from "../components/primitives";
import { t } from "../i18n";

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

/** 草稿键：按账号命名空间存本机（多账号共用浏览器时草稿不串）。
 * 输入即自动保存（防抖 300ms），刷新、切视图、关页面都不丢；
 * 任务确认开始后清除（那时草稿已完成使命）。 */
const DRAFT_KEY_PREFIX = "poliscope:newtask-draft:";

interface NewTaskDraft {
  question: string;
  populations: string;
  regions: string;
  languages: string;
  priorities: string[];
  allowPreprints: boolean;
  wallClockMinutes: number;
  toolCallLimit: number;
  sourceLimit: number;
  knowledgeBaseId: string;
  selectedSkills: string[];
}

function loadDraft(key: string): Partial<NewTaskDraft> | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<NewTaskDraft>;
    return typeof parsed === "object" && parsed !== null ? parsed : null;
  } catch {
    return null; // 损坏的草稿不值得让表单崩掉：忽略，从默认值开始。
  }
}

export function NewTaskView({
  onCreated,
  onManageKnowledge,
  active = true,
  draftNamespace = "default",
}: {
  onCreated: (taskId: string) => void;
  /** Jump to the knowledge-base management view (pasting text, uploading
   * files). Passed up so the home screen can switch views. */
  onManageKnowledge: () => void;
  /** True while this view is the visible home view. The two home views stay
   * mounted side by side, so lists refresh on re-entry, not on mount. */
  active?: boolean;
  /** 草稿命名空间（用户名）：同一浏览器的不同账号各自保存草稿。 */
  draftNamespace?: string;
}) {
  const draftKey = `${DRAFT_KEY_PREFIX}${draftNamespace}`;
  const [draft] = useState(() => loadDraft(draftKey));
  const [question, setQuestion] = useState(draft?.question ?? "");
  const [populations, setPopulations] = useState(
    draft?.populations ?? DEFAULT_NEW_TASK_OPTIONS.populations.join(", "),
  );
  const [regions, setRegions] = useState(
    draft?.regions ?? DEFAULT_NEW_TASK_OPTIONS.regions.join(", "),
  );
  const [languages, setLanguages] = useState(
    draft?.languages ?? DEFAULT_NEW_TASK_OPTIONS.languages.join(", "),
  );
  const [priorities, setPriorities] = useState<string[]>(
    draft?.priorities ?? DEFAULT_NEW_TASK_OPTIONS.evidencePriorities,
  );
  const [allowPreprints, setAllowPreprints] = useState(
    draft?.allowPreprints ?? DEFAULT_NEW_TASK_OPTIONS.allowPreprints,
  );
  const [wallClockMinutes, setWallClockMinutes] = useState(
    draft?.wallClockMinutes ?? DEFAULT_NEW_TASK_OPTIONS.wallClockMinutes,
  );
  const [toolCallLimit, setToolCallLimit] = useState(
    draft?.toolCallLimit ?? DEFAULT_NEW_TASK_OPTIONS.toolCallLimit,
  );
  const [sourceLimit, setSourceLimit] = useState(
    draft?.sourceLimit ?? DEFAULT_NEW_TASK_OPTIONS.sourceLimit,
  );
  // 知识库（长期记忆）：文档作为 Level A 用户提供源交给议会。模型设置
  // 已移至右侧栏永久设置（保存于服务器，创建任务时自动生效），表单不再收集。
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState(draft?.knowledgeBaseId ?? "");
  // 内联新建知识库：研究者在建任务时顺手建库，不必切到知识库页再回来
  // （省跳转，KnowledgeBaseView 的创建表单保留完整字段，这里是轻量版）。
  const [newKbName, setNewKbName] = useState("");
  const [creatingKb, setCreatingKb] = useState(false);
  const [kbCreateError, setKbCreateError] = useState<string | null>(null);
  // Skills：账号已下载的技能，默认勾选启用的；提交时携带勾选结果，
  // worker 会将其注入议会 prompt（非正式证据）。
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(
    new Set(draft?.selectedSkills ?? []),
  );

  // 自动保存：任何表单字段变化后 300ms 落盘（防抖，避免每次击键写
  // localStorage）。草稿只存本机，绝不离开浏览器（CLAUDE.md 16）。
  useEffect(() => {
    const id = window.setTimeout(() => {
      try {
        localStorage.setItem(
          draftKey,
          JSON.stringify({
            question,
            populations,
            regions,
            languages,
            priorities,
            allowPreprints,
            wallClockMinutes,
            toolCallLimit,
            sourceLimit,
            knowledgeBaseId,
            selectedSkills: Array.from(selectedSkills),
          } satisfies NewTaskDraft),
        );
      } catch {
        // 存储不可用（隐私模式/配额）时静默放弃：自动保存是便利，不是承诺。
      }
    }, 300);
    return () => window.clearTimeout(id);
  }, [
    draftKey,
    question,
    populations,
    regions,
    languages,
    priorities,
    allowPreprints,
    wallClockMinutes,
    toolCallLimit,
    sourceLimit,
    knowledgeBaseId,
    selectedSkills,
  ]);

  useEffect(() => {
    // 可见时刷新：知识库页可能已增删（两个主页视图同时挂载、各自持列表
    // 状态），回到本视图时下拉要跟上最新集合。
    if (!active) return;
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
  }, [active]);

  useEffect(() => {
    let cancelled = false;
    fetchSkills()
      .then((list) => {
        if (cancelled) return;
        setSkills(list);
        // 默认勾选账号里已启用的技能；用户可取消。
        setSelectedSkills(
          new Set(list.filter((skill) => skill.enabled).map((skill) => skill.id)),
        );
      })
      .catch(() => {
        // 拉取失败不阻塞建任务：Skills 是可选项。
        if (!cancelled) setSkills([]);
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
        setUploadError(t("「{0}」不是 PDF 文件，已跳过", file.name));
        continue;
      }
      try {
        const result = await uploadPaper(taskId, file);
        fresh.push({ name: file.name, size: result.size_bytes });
      } catch (cause) {
        setUploadError(
          t(
            `「${file.name}」上传失败：${cause instanceof Error ? cause.message : String(cause)}`,
          ),
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

  /** 内联建库并自动关联到当前任务（创建成功后刷新下拉并选中新库）。 */
  async function createKbInline() {
    if (!newKbName.trim() || creatingKb) return;
    setCreatingKb(true);
    setKbCreateError(null);
    try {
      const created = await createKnowledgeBase(newKbName.trim());
      setKnowledgeBases((prev) => [...prev, created]);
      setKnowledgeBaseId(created.id);
      setNewKbName("");
    } catch (cause) {
      setKbCreateError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCreatingKb(false);
    }
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
        knowledgeBaseId: knowledgeBaseId || null,
        skillIds: Array.from(selectedSkills),
        // 模型设置不再随任务提交：右侧栏的永久设置由服务器在创建时自动套用。
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
      // 任务真正开始：草稿完成使命，从本机清掉，避免下次建任务时串内容。
      try {
        localStorage.removeItem(draftKey);
      } catch {
        // 存储不可用则忽略——清理是便利，不是承诺。
      }
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
        title={t("确认要调查的原子主张")}
        subtitle={t(
          "七人议会只会调查下面确认的主张。取消勾选不会删除——它仍会被记录，随时可在证据图里追溯。",
        )}
      >
        {claims.length === 0 ? (
          <Empty>{t("没能从这个问题里拆出任何主张，请返回修改问题的表述。")}</Empty>
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
                      {claim.claim_type} · {t("证伪条件：")}
                      {claim.falsification_condition}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        <section className="newtask__upload">
          <h4>{t("补充 PDF 文献（可选，单个不超过 20 MB）")}</h4>
          <p className="newtask__upload-hint">
            {t("上传的 PDF 会作为用户提供的证据，交给议会做全文核验后按 Level A 进入证据图。")}
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
                  {t(
                    `${item.name}（${(item.size / 1024).toFixed(0)} KB，已加入证据）`,
                  )}
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
            {t("返回修改问题")}
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={confirmAndStart}
            disabled={submitting || selected.size === 0}
          >
            {submitting ? t("提交中…") : t("确认并开始研究")}
          </button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      key="question"
      className="fade-in-up"
      title={t("打开一个新的研究任务")}
      subtitle={t(
        "七人议会会围绕这个问题独立取证、交叉质询，产出一张可审计的证据地图，而不是一段读起来通顺的摘要。",
      )}
    >
      <form className="newtask__form" onSubmit={submitQuestion}>
        <label className="newtask__label" htmlFor="new-task-question">
          {t("研究问题")}
        </label>
        <textarea
          id="new-task-question"
          className="newtask__textarea"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={t("例如：社交媒体使用时长是否会降低青少年的心理健康水平？")}
          rows={3}
          disabled={submitting}
        />

        <details className="newtask__advanced">
          <summary>{t("高级选项（人群 / 地区 / 语言 / 证据侧重 / 预算，不填则使用默认值）")}</summary>
          <div className="newtask__advanced-grid">
            <label className="newtask__field">
              {t("人群（逗号分隔）")}
              <input
                value={populations}
                onChange={(event) => setPopulations(event.target.value)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              {t("地区（逗号分隔）")}
              <input
                value={regions}
                onChange={(event) => setRegions(event.target.value)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              {t("语言（逗号分隔）")}
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
              {t("允许预印本作为证据来源")}
            </label>
            <label className="newtask__field">
              {t("时间预算（分钟）")}
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
              {t("工具调用上限")}
              <input
                type="number"
                min={1}
                value={toolCallLimit}
                onChange={(event) => setToolCallLimit(Number(event.target.value) || 1)}
                disabled={submitting}
              />
            </label>
            <label className="newtask__field">
              {t("来源篇数上限")}
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
            <legend>{t("证据侧重（可多选，默认相关关系）")}</legend>
            {Object.entries(EVIDENCE_PRIORITY_LABELS).map(([key, label]) => (
              <label key={key} className="newtask__priority">
                <input
                  type="checkbox"
                  checked={priorities.includes(key)}
                  onChange={() => togglePriority(key)}
                  disabled={submitting}
                />
                {t(label)}
              </label>
            ))}
          </fieldset>
        </details>

        <details className="newtask__advanced">
          <summary>
            {t("用户提供的证据（可选）——关联知识库，文档会作为正式证据源交给议会核验")}
          </summary>
          <div className="newtask__advanced-grid">
            <label className="newtask__field newtask__field--wide">
              {t("关联知识库（可选，长期记忆——其中的文档会作为 Level A 用户提供源）")}
              <select
                value={knowledgeBaseId}
                onChange={(event) => setKnowledgeBaseId(event.target.value)}
                disabled={submitting || knowledgeBases.length === 0}
              >
                <option value="">{t("不关联（默认从公开文献检索）")}</option>
                {knowledgeBases.map((kb) => (
                  <option key={kb.id} value={kb.id}>
                    {t("{0}（{1} 篇文档）", kb.name, kb.document_count)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="button"
              onClick={onManageKnowledge}
              disabled={submitting}
            >
              {t("管理知识库 →")}
            </button>
            <details className="newtask__inline-create">
              <summary>{t("没有合适的知识库？在这里新建一个")}</summary>
              <div className="newtask__inline-create-row">
                <input
                  value={newKbName}
                  onChange={(event) => setNewKbName(event.target.value)}
                  placeholder={t("知识库名称")}
                  disabled={submitting || creatingKb}
                />
                <button
                  type="button"
                  className="button"
                  onClick={createKbInline}
                  disabled={submitting || creatingKb || !newKbName.trim()}
                >
                  {creatingKb ? t("创建中…") : t("创建并关联")}
                </button>
              </div>
              {kbCreateError ? (
                <p className="newtask__error" role="alert">
                  {kbCreateError}
                </p>
              ) : null}
            </details>
          </div>

          {skills.length > 0 ? (
            <fieldset className="newtask__priorities">
              <legend>
                {t("调用 Skills（可选，勾选后其指令会注入议会 prompt，作为非正式证据）")}
              </legend>
              {skills.map((skill) => (
                <label key={skill.id} className="newtask__priority">
                  <input
                    type="checkbox"
                    checked={selectedSkills.has(skill.id)}
                    onChange={() =>
                      setSelectedSkills((prev) => {
                        const next = new Set(prev);
                        if (next.has(skill.id)) next.delete(skill.id);
                        else next.add(skill.id);
                        return next;
                      })
                    }
                    disabled={submitting}
                  />
                  {skill.name}
                </label>
              ))}
            </fieldset>
          ) : null}
        </details>

        <p className="newtask__model-note">
          {t(
            "模型设置已移至右侧栏「模型设置」面板：保存一次，之后创建的任务都会自动使用；不设置则使用系统默认模型。",
          )}
        </p>

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
          {submitting ? t("创建中…") : t("开始研究")}
        </button>
      </form>

      <p className="newtask__safety">
        {t(
          "已填写的内容会自动保存在本机浏览器（草稿），切走、刷新都不丢；确认开始研究后清空。本系统为科研辅助工具，不提供医学诊断或医疗建议。",
        )}
      </p>
    </Panel>
  );
}
