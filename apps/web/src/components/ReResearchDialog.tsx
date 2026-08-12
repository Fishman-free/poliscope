/** 重新研究弹窗（round-12 「重新研究模式」，round-13 修正「从头研究」）：
 * 点击「重新研究」后让研究者选择从哪里重新开始。
 *
 * - 从头研究（round-13）：同一任务无法真正重来——账本幂等键按阶段/席位
 *   派生，旧一轮的事件会吞掉新一轮（模型被重新征询但结果写不进去）。
 *   因此服务器创建**全新任务**（全新账本与证据图、继承问题/范围/确认
 *   主张/预算/模型配置），从独立预承诺真正重新开始；原任务保留为审计
 *   历史。完成后前端直接打开新任务。
 * - 从断点处研究：从第一个未完成（失败/跳过）的阶段重新执行——该阶段
 *   真正重跑（它的事件此前从未写入），已完成阶段原样保留；若任务没有
 *   记录到断点，自动退化为从头研究。
 *
 * 两种模式对深度研究与论文审查任务同样生效。弹窗沿用工作台视觉纪律：
 * surface + hairline、Action Blue 唯一交互色、44px 触控目标、
 * Escape/遮罩可关闭。
 */

import { useEffect, useRef } from "react";

import { t } from "../i18n";

import "./ReResearchDialog.css";

export type ReResearchMode = "full" | "first_gap";

export function ReResearchDialog({
  onChoose,
  onCancel,
}: {
  onChoose: (mode: ReResearchMode) => void;
  onCancel: () => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  // Escape 关闭 + 焦点进入弹窗，关闭后由调用方归还焦点。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    const focusable = rootRef.current?.querySelector<HTMLElement>(
      "button, [tabindex]",
    );
    focusable?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  return (
    <div
      className="rerun-dialog__backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div
        className="rerun-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t("重新研究")}
        ref={rootRef}
      >
        <div className="rerun-dialog__head">
          <h2 className="rerun-dialog__title">{t("重新研究")}</h2>
          <button
            type="button"
            className="rerun-dialog__close"
            onClick={onCancel}
            title={t("关闭")}
            aria-label={t("关闭")}
          >
            ×
          </button>
        </div>
        <p className="rerun-dialog__intro">
          {t("请选择重新研究的方式：")}
        </p>
        <div className="rerun-dialog__options">
          <button
            type="button"
            className="rerun-dialog__option rerun-dialog__option--primary"
            onClick={() => onChoose("first_gap")}
          >
            <span className="rerun-dialog__option-title">
              {t("从断点处研究")}
            </span>
            <span className="rerun-dialog__option-desc">
              {t(
                "从第一个未完成（失败或跳过）的阶段重新执行，已完成阶段原样保留；没有断点时自动从头研究。",
              )}
            </span>
          </button>
          <button
            type="button"
            className="rerun-dialog__option"
            onClick={() => onChoose("full")}
          >
            <span className="rerun-dialog__option-title">{t("从头研究")}</span>
            <span className="rerun-dialog__option-desc">
              {t(
                "创建全新一轮研究，从独立预承诺真正重新开始（全新账本与证据图，原任务保留为历史）。",
              )}
            </span>
          </button>
        </div>
        <div className="rerun-dialog__actions">
          <button
            type="button"
            className="button"
            onClick={onCancel}
            autoFocus
          >
            {t("取消")}
          </button>
        </div>
      </div>
    </div>
  );
}
