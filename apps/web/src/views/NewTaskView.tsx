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
  // 任务模式（round-7）：深度研究（原始流程）或论文审查（上传论文交议会
  // 审查）。模式只影响入口形态与后端 task_type；议会流程完全复用。
  const [mode, setMode] = useState<"deep_research" | "paper_review">(
    "deep_research",
  );
  // 上传：论文审查模式下，上传入口在 question 阶段第一步就出现。上传端点
  // 要求任务已存在（ObjectModel.task_id NOT NULL），所以选文件时先本地暂存
  // （pendingFiles），点「开始审查」先建任务、拿 taskId、再逐个上传。
  // 多格式自 round-7：PDF/DOCX/PPTX/XLSX/HTML/TXT/MD/CSV，格式与大小由
  // 服务端校验（magic bytes + 试提取 + 20 MB），前端 accept 只做第一道过滤。
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<{ name: string; size: number }[]>([]);

  const UPLOAD_ACCEPT =
    ".pdf,.docx,.pptx,.xlsx,.html,.htm,.txt,.md,.csv,application/pdf";

  /** question 阶段：选文件后本地暂存（不上传），用户可继续加选或移除。
   *
   * 传入普通 ``File[]`` 而非 ``FileList``：``FileList`` 是 input 的 live
   * 集合，onChange 里先 ``stageFiles`` 后 ``event.target.value = ""`` 会清空
   * 这个 live 集合，而 ``setPendingFiles`` 的 updater 是异步执行的——等它
   * 真正读 ``files`` 时集合已经空了，导致选了文件毫无反应（round-9 bug）。
   * 调用方必须在 onChange 内同步 ``Array.from`` 拷贝。 */
  function stageFiles(files: File[]) {
    if (files.length === 0) return;
    setUploadError(null);
    setPendingFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name + f.size));
      const added = files.filter((file) => !seen.has(file.name + file.size));
      return [...prev, ...added];
    });
  }

  function removeStaged(index: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }

  /** claims 阶段：任务已存在，逐个真正上传到该任务。 */
  async function handleFiles(files: FileList | null) {
    if (!taskId || !files || uploading) return;
    await uploadStaged(Array.from(files), taskId);
  }

  /** 上传一批文件到指定任务（``targetTaskId`` 必须已存在）。
   *
   * ``targetTaskId`` 作为显式参数传入，而不是读闭包里的 ``taskId`` 状态：
   * ``submitQuestion`` 在 ``setTaskId(created.task_id)`` 之后立即上传，
   * 那一刻 React 还没重渲染，闭包里的 ``taskId`` 仍是旧值（首次为 null，
   * 返回修改后重试则是上一个任务的 id）——读它会把论文传到一个不存在或
   * 已废弃的任务上，导致 ``confirmClaims`` 在新任务上读不到论文。 */
  async function uploadStaged(files: File[], targetTaskId: string) {
    if (!targetTaskId || uploading) return;
    setUploading(true);
    setUploadError(null);
    const fresh: { name: string; size: number }[] = [];
    for (const file of files) {
      // 格式校验交给服务端（magic bytes + 试提取，422 带原因），前端不再
      // 按扩展名自行判断——伪装扩展名的文件必须由字节级校验拒绝。
      try {
        const result = await uploadPaper(targetTaskId, file);
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
    // 深度研究必须有研究问题；论文审查的问题（审查要求）可选，留空时用
    // 占位问题（后端契约要求 question 非空，占位如实描述任务性质）。
    const effectiveQuestion =
      mode === "paper_review" && !question.trim()
        ? "审查上传论文的论证严谨性与证据充分性，并给出改进建议"
        : question.trim();
    if (!effectiveQuestion || submitting) return;
    // 论文审查必须至少有一篇论文：question 阶段暂存的文件，或 claims
    // 阶段已上传的文件。都没有则不让创建任务（避免进到 claims 才发现
    // 无论文可审——上传入口前置的意义就是先选文件再继续）。
    if (mode === "paper_review" && pendingFiles.length === 0 && uploads.length === 0) {
      setError(
        t("论文审查必须先上传至少一篇论文，才能开始。请先选择待审查的文件。"),
      );
      return;
    }
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
        taskType: mode,
        // 模型设置不再随任务提交：右侧栏的永久设置由服务器在创建时自动套用。
      };
      const created = await createTask(effectiveQuestion, options);
      setTaskId(created.task_id);
      setClaims(created.suggested_claims);
      setSelected(new Set(created.suggested_claims.map((claim) => claim.id)));
      setPhase("claims");
      // 任务已存在，现在把 question 阶段暂存的文件真正上传到该任务。
      // 先清空暂存，避免 uploadStaged 内读取旧 taskId 前 pendingFiles 还引用。
      // 上传用刚创建的 taskId（显式传参），而非闭包里的旧 taskId 状态。
      const staged = pendingFiles;
      setPendingFiles([]);
      if (staged.length > 0) {
        await uploadStaged(staged, created.task_id);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  /** 返回第一步：清空本轮已建任务的上传状态。
   *
   * 若不清空，用户「返回修改问题」后重新提交会残留上一轮任务的论文清单
   * （``uploads`` 仍指向旧任务的已上传文件），而 ``confirmClaims`` 用的是
   * 新建任务的 id——旧任务的论文对新任务无效，后端会报「必须至少上传一篇
   * 论文」，前端却显示「已加入证据」，二者矛盾。返回即视为放弃本轮，干净
   * 重置，让下一轮从选文件重新开始。 */
  function backToQuestion() {
    setPhase("question");
    setTaskId(null);
    setUploads([]);
    setUploadError(null);
    setClaims([]);
    setSelected(new Set());
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

        {/* round-9 修复：论文审查的上传入口只在第一步（question 阶段）出现，
            第二步（claims 阶段）不再重复展示文件选择框——第一步已选的文件已
            在建任务时全部上传。此处只展示已上传的论文清单；若上传意外失败
            （uploads 为空）则提示返回第一步重新选择，而不是让用户在第二步
            再传一次（会与「先选文件再建任务」的入口设计矛盾）。 */}
        {mode === "paper_review" ? (
          <section className="newtask__upload">
            <h4>{t("已上传的待审查论文")}</h4>
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
            ) : (
              <p className="newtask__upload-required">
                {t(
                  "论文上传未成功，请点下方「返回修改问题」回到第一步重新选择待审查论文。",
                )}
              </p>
            )}
            {uploadError ? (
              <p className="newtask__error" role="alert">
                {uploadError}
              </p>
            ) : null}
          </section>
        ) : (
          <section className="newtask__upload">
            <h4>{t("补充 PDF 文献（可选，单个不超过 20 MB）")}</h4>
            <p className="newtask__upload-hint">
              {t(
                "上传的 PDF 会作为用户提供的证据，交给议会做全文核验后按 Level A 进入证据图。",
              )}
            </p>
            <input
              type="file"
              accept={UPLOAD_ACCEPT}
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
        )}

        {error ? (
          <p className="newtask__error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="newtask__actions">
          <button
            type="button"
            className="button"
            onClick={backToQuestion}
            disabled={submitting}
          >
            {t("返回修改问题")}
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={confirmAndStart}
            disabled={
              submitting ||
              selected.size === 0 ||
              (mode === "paper_review" && uploads.length === 0)
            }
          >
            {submitting
              ? t("提交中…")
              : mode === "paper_review"
                ? t("确认并开始审查")
                : t("确认并开始研究")}
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
        mode === "paper_review"
          ? "上传论文，七人议会会先读清论文的研究问题、主要观点与佐证，再逐项审查论证严谨性与证据充分性，产出一份可审计的论文审查报告。"
          : "七人议会会围绕这个问题独立取证、交叉质询，产出一张可审计的证据地图，而不是一段读起来通顺的摘要。",
      )}
    >
      {/* 模式切换（round-7）：Apple 风格 segmented pill，唯一交互色
          #0066cc 承载选中态。切换只改变入口形态与后端 task_type。 */}
      <div className="newtask__mode" role="tablist" aria-label={t("任务类型")}>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "deep_research"}
          className={
            mode === "deep_research"
              ? "newtask__mode-pill newtask__mode-pill--active"
              : "newtask__mode-pill"
          }
          onClick={() => setMode("deep_research")}
          disabled={submitting}
        >
          {t("深度研究")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "paper_review"}
          className={
            mode === "paper_review"
              ? "newtask__mode-pill newtask__mode-pill--active"
              : "newtask__mode-pill"
          }
          onClick={() => setMode("paper_review")}
          disabled={submitting}
        >
          {t("论文审查")}
        </button>
      </div>

      {/* 论文审查：上传入口第一步就出现（round-9）。先选文件（本地暂存），
          再填审查要求；点「开始审查」时建任务并上传。格式与大小由服务端
          校验（magic bytes + 试提取 + 20 MB）。 */}
      {mode === "paper_review" ? (
        <section className="newtask__upload newtask__upload--pre">
          <h4>{t("上传待审查论文（必填，单个不超过 20 MB）")}</h4>
          <p className="newtask__upload-hint">
            {t(
              "支持 PDF / DOCX / PPTX / XLSX / HTML / TXT / MD / CSV。论文全文会交给议会核验并按 Level A 进入证据图，审查报告将指出其中不严谨、证据不充分之处并给出改进建议。",
            )}
          </p>
          <input
            type="file"
            accept={UPLOAD_ACCEPT}
            multiple
            disabled={submitting || uploading}
            onChange={(event) => {
              // 同步拷贝：FileList 是 live 集合，value="" 前先 Array.from
              // 存下文件，否则异步 updater 读到的是已被清空的空集合。
              const picked = event.target.files
                ? Array.from(event.target.files)
                : [];
              stageFiles(picked);
              event.target.value = "";
            }}
          />
          {pendingFiles.length > 0 ? (
            <ul className="newtask__upload-list">
              {pendingFiles.map((file, index) => (
                <li key={`${file.name}-${index}`}>
                  <span className="newtask__upload-name">
                    {file.name}（{(file.size / 1024).toFixed(0)} KB）
                  </span>
                  <button
                    type="button"
                    className="newtask__upload-remove"
                    onClick={() => removeStaged(index)}
                    disabled={submitting}
                    aria-label={t("移除 {0}", file.name)}
                  >
                    {t("移除")}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="newtask__upload-required">
              {t("论文审查必须先上传至少一篇论文，才能开始审查。")}
            </p>
          )}
          {uploadError ? (
            <p className="newtask__error" role="alert">
              {uploadError}
            </p>
          ) : null}
        </section>
      ) : null}

      <form className="newtask__form" onSubmit={submitQuestion}>
        <label className="newtask__label" htmlFor="new-task-question">
          {mode === "paper_review" ? t("审查要求（可选）") : t("研究问题")}
        </label>
        <textarea
          id="new-task-question"
          className="newtask__textarea"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={
            mode === "paper_review"
              ? t("例如：重点审查论文的因果推断、测量方式与样本代表性（可留空，系统会审查全文）")
              : t("例如：社交媒体使用时长是否会降低青少年的心理健康水平？")
          }
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

        {mode === "deep_research" ? (
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
        ) : null}

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
          disabled={
            submitting ||
            (mode === "deep_research" && !question.trim())
          }
        >
          {submitting
            ? t("创建中…")
            : mode === "paper_review"
              ? t("开始审查")
              : t("开始研究")}
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
