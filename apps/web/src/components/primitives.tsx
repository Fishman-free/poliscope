/** The shared vocabulary of the three views.
 *
 * Kept in one file because the pieces are small and, more importantly, because
 * the mapping from evidential state to colour has to be defined exactly once.
 * If two views disagreed about what "provisional" looks like, colour would stop
 * being information.
 */

import type { ReactNode } from "react";

import { t } from "../i18n";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  FileText,
  Hourglass,
  MessageSquare,
  Pause,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import "./primitives.css";

export type Tone = "admitted" | "provisional" | "refuted" | "unknown";

/** Node status -> tone. Anything unrecognised is `unknown`, never `admitted`:
 * an unfamiliar state must not be shown as verified. */
export function toneForStatus(status: string): Tone {
  switch (status) {
    case "active":
      return "admitted";
    case "provisional":
      return "provisional";
    case "refuted":
    case "withdrawn":
    case "quarantined":
      return "refuted";
    default:
      return "unknown";
  }
}

export const STATUS_LABELS: Record<string, string> = {
  active: "已采纳",
  provisional: "仅元数据",
  refuted: "已反驳",
  narrowed: "已收窄",
  withdrawn: "已撤回",
  quarantined: "已隔离",
};

/** Task lifecycle status -> visible Chinese label (rendered through t()).
 * The enum value stays available in `title`/`aria-label`; the label is what a
 * researcher reads. Statuses outside the map fall back to the raw enum so an
 * unknown lifecycle state is never mislabelled as a known one. */
export const TASK_STATUS_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "研究中",
  DEGRADED_RUNNING: "降级运行",
  AWAITING_COUNCIL_INPUT: "等待方向性引导",
  REPORTING: "生成报告中",
  PAUSED: "已暂停",
  COMPLETED: "已完成",
  COMPLETED_WITH_GAPS: "已完成，有缺口",
  FAILED: "研究中断",
  CANCELLED: "已停止",
};

/** Task lifecycle status -> geometric icon. Colour is never the only signal:
 * each status also carries a distinct symbol so a red-green colour-blind
 * researcher can still tell 研究中 from 已停止. Unknown statuses get the
 * neutral clock marker. */
export const TASK_STATUS_ICONS: Record<string, LucideIcon> = {
  QUEUED: Hourglass,
  RUNNING: Activity,
  DEGRADED_RUNNING: AlertTriangle,
  AWAITING_COUNCIL_INPUT: MessageSquare,
  REPORTING: FileText,
  PAUSED: Pause,
  COMPLETED: CheckCircle2,
  COMPLETED_WITH_GAPS: AlertCircle,
  FAILED: XCircle,
  CANCELLED: CircleSlash,
};

/** The task-lifecycle badge used in the task header and session history.
 * Uses independent operational colours (`.status-badge`), never the evidence
 * tones -- a run's lifecycle state is a different axis from how much a finding
 * is trusted. The raw enum rides in `title` and `aria-label` for audit. */
export function TaskStatusBadge({ status }: { status: string }) {
  const Icon = TASK_STATUS_ICONS[status] ?? Hourglass;
  const label = TASK_STATUS_LABELS[status] ?? status;
  return (
    <span
      className={`status-badge status-badge--${status.toLowerCase()}`}
      title={status}
      aria-label={`${status}（${label}）`}
    >
      <Icon className="status-badge__icon" size={12} strokeWidth={2.2} aria-hidden="true" />
      <span>{t(label)}</span>
    </span>
  );
}

export function Badge({
  tone = "unknown",
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={className ? `panel ${className}` : "panel"}>
      <header className="panel__head">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="panel__subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

/** Shown wherever a list is legitimately empty.
 *
 * The wording distinguishes "nothing was found" from "this was never run" --
 * CLAUDE.md 7 forbids letting an absence read as a negative result. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** A number the researcher must not misread. `caveat` is rendered next to it,
 * never below the fold: CLAUDE.md 7.4's whole point is that a paper count
 * without its cluster count invites the wrong conclusion. */
export function Metric({
  label,
  value,
  caveat,
  tone,
}: {
  label: string;
  value: ReactNode;
  caveat?: string;
  tone?: Tone;
}) {
  return (
    <div className="metric">
      <span className="metric__label">{label}</span>
      <span className={`metric__value${tone ? ` metric__value--${tone}` : ""}`}>
        {value}
      </span>
      {caveat ? <span className="metric__caveat">{caveat}</span> : null}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <p className="loading" role="status">
      {label}
    </p>
  );
}
