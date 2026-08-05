/** 右侧栏会话历史。
 *
 * 取代原先 header 里的「任务 ID」输入框：一个账号（单用户部署 = 全部任务）
 * 的所有会话都列在这里，点击即跳转。列表只有摘要（问题、状态、时间），
 * 完整内容在跳转后由工作台载入——面板只需要足够「认出并打开」一个会话。
 */

import { useEffect, useState } from "react";

import { fetchTasks } from "../api/client";
import type { TaskSummary } from "../api/types";
import { Badge, Empty, Panel, Spinner, TASK_STATUS_TONE } from "../components/primitives";

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

export function SessionHistory({
  currentTaskId,
  onOpen,
}: {
  currentTaskId: string | null;
  onOpen: (taskId: string) => void;
}) {
  const [tasks, setTasks] = useState<TaskSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
  }, []);

  return (
    <Panel title="会话历史" subtitle="全部研究会话，点击跳转">
      {error ? (
        <p className="session__error" role="alert">
          {error}
        </p>
      ) : null}
      {tasks === null ? (
        <Spinner label="正在加载会话…" />
      ) : tasks.length === 0 ? (
        <Empty>还没有会话。创建第一个研究任务后，它会出现在这里。</Empty>
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
                onClick={() => onOpen(task.task_id)}
                title="打开这个会话"
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
    </Panel>
  );
}
