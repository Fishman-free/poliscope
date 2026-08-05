/** The shared vocabulary of the three views.
 *
 * Kept in one file because the pieces are small and, more importantly, because
 * the mapping from evidential state to colour has to be defined exactly once.
 * If two views disagreed about what "provisional" looks like, colour would stop
 * being information.
 */

import type { ReactNode } from "react";

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
