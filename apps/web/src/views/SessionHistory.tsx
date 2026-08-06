/** Header 上的会话历史：图标按钮弹出过去的所有会话，点击即回到那次会话。
 *
 * 取代侧栏的常驻面板——列表只在研究者想看时出现，不占用工作区。列表只到
 * 「认出并打开」的粒度：问题、状态、时间；完整内容在跳转后由工作台载入。
 * 弹出层按组件菜单标准做：点击外部 / Escape 关闭，点击条目打开并收起；
 * 列表首次展开时才拉取，之后保持（同一会话内数据不变，YAGNI 不做轮询）。
 */

import { useEffect, useRef, useState } from "react";

import { fetchTasks } from "../api/client";
import type { TaskSummary } from "../api/types";
import { Badge, Empty, Spinner, TASK_STATUS_TONE } from "../components/primitives";
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

export function SessionHistory({
  currentTaskId,
  onOpen,
}: {
  currentTaskId: string | null;
  onOpen: (taskId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<TaskSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  return (
    <div className="session" ref={rootRef}>
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
            <ol className="session__list">
              {tasks.map((task) => (
                <li key={task.task_id}>
                  <button
                    type="button"
                    className={
                      "session__row" +
                      (task.task_id === currentTaskId ? " session__row--current" : "")
                    }
                    onClick={() => openAndClose(task.task_id)}
                    title={t("打开这个会话")}
                  >
                    <span className="session__question">{task.question}</span>
                    <span className="session__meta">
                      <Badge tone={TASK_STATUS_TONE[task.status] ?? "unknown"}>
                        {task.status}
                      </Badge>
                      <span className="session__date">{formatDate(task.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </div>
  );
}
