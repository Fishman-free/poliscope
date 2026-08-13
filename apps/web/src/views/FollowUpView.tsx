/** 补充提问（round-9）：任务完成后，研究者可就研究结果继续追问模型。
 *
 * 与「实时进展 / Research Brief / Controversy Map」并列的选项卡。任务
 * COMPLETED / COMPLETED_WITH_GAPS 之后可用：研究者输入一个问题，后端用
 * 任务实际运行的模型 + 该任务的研究简报（已确认主张、已采纳发现、盲点、
 * 异议、局限）作为上下文回答，因此回答锚定在这次研究上，而不是一个对
 * 研究一无所知的陌生模型的新鲜意见（CLAUDE.md 2：证据优先于流畅文本）。
 * 论文审查任务还会把上传论文的理解与全文注入上下文（round-10）。
 *
 * 对话框形态：上方滚动问答列表，下方输入框 + 发送。回答流式（round-10）：
 * delta 逐字到达并就地追加，无需等待完整答案。
 */

import { type FormEvent, useEffect, useRef, useState } from "react";

import { fetchPaperMarkdown, fetchSkills, followUpStream } from "../api/client";
import type { SkillSummary } from "../api/types";
import { t } from "../i18n";

import "./FollowUpView.css";

interface Exchange {
  question: string;
  answer: string;
  ok: boolean;
  pending: boolean;
}

const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_GAPS"]);

export function FollowUpView({
  taskId,
  status,
}: {
  taskId: string;
  /** 任务状态；只有终态任务可以补充提问。 */
  status: string;
}) {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
  const [searchLiterature, setSearchLiterature] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const terminal = TERMINAL.has(status);

  useEffect(() => {
    let cancelled = false;
    void fetchSkills()
      .then((list) => {
        if (!cancelled) setSkills(list);
      })
      .catch(() => {
        // Skills are optional process context; a failed list must not block asking.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 新回答到达时把对话滚到底——研究者在看「现在」的答案。
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [exchanges.length, sending]);

  useEffect(() => {
    if (terminal) inputRef.current?.focus();
  }, [terminal]);

  function downloadBlob(content: Blob, filename: string) {
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(content);
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  async function downloadMarkdown(fetch: () => Promise<string>, basename: string) {
    const markdown = await fetch();
    downloadBlob(
      new Blob([markdown], { type: "text/markdown;charset=utf-8" }),
      `${basename}-${taskId.slice(0, 8)}.md`,
    );
  }

  function threadMarkdown(): string {
    const lines = [`# ${t("补充提问")}`, ""];
    for (const exchange of exchanges) {
      lines.push(`## ${t("你")}`, "", exchange.question, "");
      lines.push(`## ${t("模型")}`, "", exchange.ok ? exchange.answer : t("回答不可用"), "");
    }
    return lines.join("\n");
  }

  function exportThread(kind: "md" | "docx") {
    const markdown = threadMarkdown();
    if (kind === "md") {
      downloadBlob(
        new Blob([markdown], { type: "text/markdown;charset=utf-8" }),
        `poliscope-followup-${taskId.slice(0, 8)}.md`,
      );
      return;
    }
    // Minimal WordprocessingML document — no extra dependency.
    const escaped = markdown
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    const paragraphs = escaped.split("\n").map(
      (line) =>
        `<w:p><w:r><w:t xml:space="preserve">${line || " "}</w:t></w:r></w:p>`,
    );
    const documentXml =
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
      `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">` +
      `<w:body>${paragraphs.join("")}</w:body></w:document>`;
    downloadBlob(
      new Blob([documentXml], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
      `poliscope-followup-${taskId.slice(0, 8)}.docx`,
    );
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || sending || !terminal) return;
    setSending(true);
    setError(null);
    // 先渲染一条 pending 的追问，让研究者看到问题已发出；流式 delta 会
    // 就地追加到这条 answer。
    setExchanges((prev) => [
      ...prev,
      { question: text, answer: "", ok: true, pending: true },
    ]);
    setQuestion("");
    try {
      await followUpStream(
        taskId,
        text,
        (delta) => {
          setExchanges((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.pending) {
              next[next.length - 1] = {
                ...last,
                answer: last.answer + delta,
              };
            }
            return next;
          });
        },
        undefined,
        {
          skillIds: [...selectedSkills],
          searchLiterature,
        },
      );
      setExchanges((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.pending) {
          next[next.length - 1] = { ...last, pending: false };
        }
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      // 失败时把 pending 那条标记为失败，不让它一直转圈。
      setExchanges((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.pending) {
          next[next.length - 1] = {
            question: last.question,
            answer: "",
            ok: false,
            pending: false,
          };
        }
        return next;
      });
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="followup">
      <div className="followup__head">
        <h3>{t("补充提问")}</h3>
        <p className="followup__hint">
          {terminal
            ? t("研究已完成。你可以就研究结论、证据或局限继续追问，回答基于本次研究的议会产出。")
            : t("任务尚未完成，完成后才能补充提问。")}
        </p>
      </div>

      <div className="followup__thread" ref={listRef} aria-live="polite">
        {exchanges.length === 0 ? (
          <div className="followup__empty">
            <p>{t("还没有提问。输入一个问题，模型会基于本次研究的产出回答。")}</p>
          </div>
        ) : (
          exchanges.map((exchange, index) => (
            <div key={index} className="followup__exchange">
              <div className="followup__q">
                <span className="followup__role">{t("你")}</span>
                <p>{exchange.question}</p>
              </div>
              <div className="followup__a">
                <span className="followup__role">{t("模型")}</span>
                {exchange.pending ? (
                  <p className="followup__pending">{t("思考中…")}</p>
                ) : exchange.ok ? (
                  <p>{exchange.answer}</p>
                ) : (
                  <p className="followup__failed">
                    {t("回答不可用")}
                    {exchange.answer ? `：${exchange.answer}` : ""}
                  </p>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {error ? (
        <p className="followup__error" role="alert">
          {error}
        </p>
      ) : null}

      <form className="followup__form" onSubmit={ask}>
        {skills.length > 0 ? (
          <div className="followup__skills" role="group" aria-label={t("本轮启用的技能")}>
            {skills.map((skill) => {
              const on = selectedSkills.has(skill.id);
              return (
                <button
                  key={skill.id}
                  type="button"
                  className={
                    "followup__chip" + (on ? " followup__chip--on" : "")
                  }
                  aria-pressed={on}
                  disabled={!terminal || sending}
                  onClick={() => {
                    setSelectedSkills((prev) => {
                      const next = new Set(prev);
                      if (next.has(skill.id)) next.delete(skill.id);
                      else next.add(skill.id);
                      return next;
                    });
                  }}
                >
                  {skill.name}
                </button>
              );
            })}
          </div>
        ) : null}
        <label className="followup__search">
          <input
            type="checkbox"
            checked={searchLiterature}
            disabled={!terminal || sending}
            onChange={(event) => setSearchLiterature(event.target.checked)}
          />
          {t("同时检索外部文献（非正式证据）")}
        </label>
        <textarea
          ref={inputRef}
          className="followup__input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={t("追问研究结论、证据或局限…")}
          rows={3}
          disabled={!terminal || sending}
          onKeyDown={(event) => {
            // Enter 发送，Shift+Enter 换行。
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void ask(event);
            }
          }}
        />
        <div className="followup__actions">
          <button
            type="submit"
            className="button button--primary followup__send"
            disabled={!terminal || sending || !question.trim()}
          >
            {sending ? t("回答中…") : t("提问")}
          </button>
          <button
            type="button"
            className="button followup__export"
            disabled={exchanges.length === 0}
            onClick={() => exportThread("md")}
          >
            {t("导出 Markdown")}
          </button>
          <button
            type="button"
            className="button followup__export"
            disabled={exchanges.length === 0}
            onClick={() => exportThread("docx")}
          >
            {t("导出 Word")}
          </button>
          <button
            type="button"
            className="button followup__export"
            disabled={!taskId}
            onClick={() => {
              void downloadMarkdown(
                () => fetchPaperMarkdown(taskId),
                "poliscope-paper",
              );
            }}
          >
            {t("下载论文 Markdown")}
          </button>
        </div>
      </form>
    </div>
  );
}
