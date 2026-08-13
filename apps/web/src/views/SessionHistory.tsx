/** Header 上的会话历史：图标按钮弹出过去的所有会话，点击即回到那次会话。
 *
 * 取代侧栏的常驻面板——列表只在研究者想看时出现，不占用工作区。列表只到
 * 「认出并打开」的粒度：问题、状态、时间；完整内容在跳转后由工作台载入。
 * 弹出层按组件菜单标准做：点击外部 / Escape 关闭，点击条目打开并收起；
 * 列表首次展开时才拉取，之后保持（同一会话内数据不变，YAGNI 不做轮询）。
 *
 * Round-6 additions: each row carries a model-config badge (whose endpoint
 * this session actually runs on), and rows can be deleted -- a queued or
 * stale session is the researcher's own data, and discarding it is the
 * intended way to unblock a queue. Deletion is destructive, so it uses a
 * two-step inline confirm per row, and the whole list has a "clear all"
 * two-step confirm as well.
 */

import { useEffect, useRef, useState } from "react";

import { deleteTask, fetchTasks, reResearch, rerunFresh, resumeTask } from "../api/client";
import { ReResearchDialog } from "../components/ReResearchDialog";
import type { TaskSummary } from "../api/types";
import { Badge, Empty, Spinner, TaskStatusBadge } from "../components/primitives";
import { t } from "../i18n";

import "./SessionHistory.css";

function formatDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 历史时钟图标（lucide 风格线性图标，与 shadcn/ui 的图标语言一致）。 */
function HistoryIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 12a9 9 0 1 0 3.4-7" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l3 3" />
    </svg>
  );
}

/** 每行右侧：模型配置来源徽章——「你保存的配置」或「系统默认」，悬停
 * 给出端点与模型名。这直接回答「我的模型设置到底有没有生效」（round-6）。 */
function ModelBadge({ task }: { task: TaskSummary }) {
  const config = task.effective_model_config;
  if (!config) return null;
  if (config.source === "saved") {
    const detail = [config.base_url, config.model_name].filter(Boolean).join(" · ");
    return (
      <Badge tone="admitted" title={t("本会话使用你保存的模型配置：{0}", detail)}>
        {t("你保存的配置")}
      </Badge>
    );
  }
  return (
    <Badge tone="unknown" title={t("本会话使用部署方配置的系统默认模型")}>
      {t("系统默认")}
    </Badge>
  );
}

export function SessionHistory({
  currentTaskId,
  onOpen,
  onDeleted,
  onTaskMutated,
}: {
  currentTaskId: string | null;
  onOpen: (taskId: string) => void;
  /** Called after a task was deleted, with its id, so the shell can leave it
   * if it was the one being viewed. */
  onDeleted?: (deletedId: string) => void;
  /** Called after a task was requeued (重新研究 / 继续研究), with its id. If
   * that task is the currently open workspace the shell refreshes it
   * immediately -- the header's 重新研究/继续研究 button must not diverge from
   * what the history panel just did. */
  onTaskMutated?: (taskId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<TaskSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 两段式确认：pendingDelete 是等待二次确认的任务 id，「clear」表示
  // 等待确认清空全部。删除中（deleting）期间禁用所有删除操作。
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [pendingClear, setPendingClear] = useState(false);
  // 「清空全部」的独立 loading（不是行内操作，不需要 busyTaskId 粒度）。
  const [clearing, setClearing] = useState(false);
  // loading 绑定具体任务 id：只有正在重新研究/继续研究的那一行显示
  // 「请稍候…」，其他行的按钮与整条列表都不被禁用。
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 每次展开都重新拉列表：刚创建的任务（或新回到列表的会话）必须立刻
  // 可见，不能缓存一份旧列表。首次（tasks === null）显示 Spinner，之后
  // 静默刷新——旧列表先显示，拉取完成后原地更新，不闪加载态。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchTasks()
      .then((list) => {
        if (!cancelled) setTasks(list);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // 点击弹出层外部或按 Escape 关闭。
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function openAndClose(taskId: string) {
    setOpen(false);
    onOpen(taskId);
  }

  async function removeOne(taskId: string) {
    setBusyTaskId(taskId);
    setError(null);
    try {
      await deleteTask(taskId);
      setTasks((list) => list?.filter((task) => task.task_id !== taskId) ?? null);
      onDeleted?.(taskId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyTaskId(null);
      setPendingDelete(null);
    }
  }

  /** 「重新研究/继续研究」：把 FAILED/CANCELLED（重新研究）或 PAUSED（继续
   * 研究）任务交回队列，worker 从 checkpoint 续跑。成功后刷新列表让状态
   * 变回 QUEUED，并回调父组件——若该任务正是当前工作台，父组件立即刷新
   * snapshot（否则保险轮询会兜底）。loading 只落在这一行（busyTaskId）。
   *  round-12 起「重新研究」先弹出模式选择（从头 / 从断点处研究）。 */
  async function requeueOne(
    taskId: string,
    resume: boolean,
    mode: "full" | "first_gap" = "first_gap",
  ) {
    setBusyTaskId(taskId);
    setError(null);
    try {
      if (resume) {
        await resumeTask(taskId);
      } else if (mode === "full") {
        // round-13 「从头研究」：同一任务无法真正重来（账本幂等键按阶段/
        // 席位派生，旧一轮事件会与新一轮冲突），服务器创建全新任务并返回
        // 新 id——刷新列表后直接打开它。原任务保留为审计历史。
        const fresh = await rerunFresh(taskId);
        const tasks = await fetchTasks();
        setTasks(tasks);
        onTaskMutated?.(taskId);
        onOpen(fresh.task_id);
        return;
      } else {
        const result = await reResearch(taskId, mode);
        if (result.task_id !== taskId) {
          // first_gap 无断点：服务端同样创建了全新任务（返回新 id），
          // 刷新列表并打开它；原任务保留为审计历史。
          const tasks = await fetchTasks();
          setTasks(tasks);
          onTaskMutated?.(taskId);
          onOpen(result.task_id);
          return;
        }
      }
      // 刷新列表让状态从 FAILED/PAUSED 变回 QUEUED。
      const fresh = await fetchTasks();
      setTasks(fresh);
      onTaskMutated?.(taskId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyTaskId(null);
    }
  }

  // 「重新研究」模式选择弹窗的目标任务（round-12）。null 表示未打开。
  const [rerunTarget, setRerunTarget] = useState<string | null>(null);

  /** 清空全部：逐个删除剩余任务。失败即中断并显示原因——绝不静默吞掉
   * 一个没删掉的任务，假装清空成功。 */
  async function clearAll() {
    if (!tasks) return;
    setClearing(true);
    setError(null);
    try {
      for (const task of [...tasks]) {
        await deleteTask(task.task_id);
        onDeleted?.(task.task_id);
      }
      setTasks([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setClearing(false);
      setPendingClear(false);
      setPendingDelete(null);
    }
  }

  return (
    <div className="session" ref={rootRef}>
      {rerunTarget ? (
        <ReResearchDialog
          onChoose={(mode) => {
            const target = rerunTarget;
            setRerunTarget(null);
            if (target) void requeueOne(target, false, mode);
          }}
          onCancel={() => setRerunTarget(null)}
        />
      ) : null}
      <button
        type="button"
        className="session__trigger"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="true"
        aria-expanded={open}
        title={t("会话历史：回到任意一次研究会话")}
      >
        <HistoryIcon />
        {t("会话历史")}
      </button>
      {open ? (
        <div className="session__popover" role="group" aria-label={t("会话历史")}>
          <span className="session__popover-head">{t("全部会话")}</span>
          {error ? (
            <p className="session__error" role="alert">
              {error}
            </p>
          ) : null}
          {tasks === null ? (
            <Spinner label={t("正在加载会话…")} />
          ) : tasks.length === 0 ? (
            <Empty>{t("还没有会话。创建第一个研究任务后，它会出现在这里。")}</Empty>
          ) : (
            <>
              <ol className="session__list">
                {tasks.map((task) => (
                  <li key={task.task_id} className="session__row-wrap">
                    <button
                      type="button"
                      className={
                        "session__row" +
                        (task.task_id === currentTaskId
                          ? " session__row--current"
                          : "")
                      }
                      onClick={() => openAndClose(task.task_id)}
                      title={t("打开这个会话")}
                    >
                      <span className="session__question">{task.question}</span>
                      <span className="session__meta">
                        <ModelBadge task={task} />
                        <TaskStatusBadge status={task.status} />
                        <span className="session__date">
                          {formatDate(task.created_at)}
                        </span>
                      </span>
                    </button>
                    {pendingDelete === task.task_id ? (
                      <span className="session__confirm">
                        {t("删除后不可恢复。确认删除？")}
                        <button
                          type="button"
                          className="session__danger"
                          onClick={() => removeOne(task.task_id)}
                          disabled={busyTaskId !== null || clearing}
                        >
                          {busyTaskId === task.task_id ? t("删除中…") : t("确认删除")}
                        </button>
                        <button
                          type="button"
                          className="session__cancel"
                          onClick={() => setPendingDelete(null)}
                          disabled={busyTaskId !== null || clearing}
                        >
                          {t("取消")}
                        </button>
                      </span>
                    ) : (
                      <>
                        {task.status === "FAILED" ||
                        task.status === "CANCELLED" ||
                        task.status === "COMPLETED" ||
                        task.status === "COMPLETED_WITH_GAPS" ? (
                          <button
                            type="button"
                            className="session__rerun"
                            onClick={() => setRerunTarget(task.task_id)}
                            disabled={busyTaskId !== null}
                            title={t("重新研究：从头克隆同题任务，或从第一个未完成阶段续跑")}
                          >
                            {busyTaskId === task.task_id ? t("请稍候…") : t("重新研究")}
                          </button>
                        ) : null}
                        {task.status === "PAUSED" ? (
                          <button
                            type="button"
                            className="session__rerun"
                            onClick={() => requeueOne(task.task_id, true)}
                            disabled={busyTaskId !== null}
                            title={t("继续研究：将已暂停的任务交回队列")}
                          >
                            {busyTaskId === task.task_id ? t("请稍候…") : t("继续研究")}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="session__delete"
                          onClick={() => {
                            setPendingDelete(task.task_id);
                            setPendingClear(false);
                          }}
                          disabled={busyTaskId !== null || clearing}
                          title={t("删除这个会话（不可恢复）")}
                          aria-label={t("删除会话")}
                        >
                          ✕
                        </button>
                      </>
                    )}
                  </li>
                ))}
              </ol>
              {pendingClear ? (
                <div className="session__clear-confirm">
                  {t("将删除全部 {0} 个会话及其证据记录，不可恢复。确认清空？", tasks.length)}
                  <button
                    type="button"
                    className="session__danger"
                    onClick={clearAll}
                    disabled={clearing}
                  >
                    {clearing ? t("删除中…") : t("确认清空全部")}
                  </button>
                  <button
                    type="button"
                    className="session__cancel"
                    onClick={() => setPendingClear(false)}
                    disabled={clearing}
                  >
                    {t("取消")}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="session__clear-all"
                  onClick={() => {
                    setPendingClear(true);
                    setPendingDelete(null);
                  }}
                  disabled={clearing}
                >
                  {t("清空全部会话")}
                </button>
              )}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
