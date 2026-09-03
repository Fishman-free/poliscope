/** A2 public read-only shared snapshot.
 *
 * Mounted at /shared/{token} WITHOUT authentication. The server has already
 * redacted the snapshot (no model settings, no usage, no share metadata, no
 * private process trace); this view only renders the public-safe surfaces:
 * question, Research Brief (conclusions beside limitations) and evidence
 * lineage. There is deliberately no follow-up, no model panel, no actions.
 */

import { useEffect, useState } from "react";

import { ApiError, fetchSharedSnapshot } from "../api/client";
import type { WorkspaceSnapshot } from "../api/types";
import { Spinner, TaskStatusBadge } from "../components/primitives";
import { t } from "../i18n";
import { LineageView } from "./LineageView";

import "./SharedView.css";

export function SharedView({ token }: { token: string }) {
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSharedSnapshot(token)
      .then((next) => {
        if (!cancelled) setSnapshot(next);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(
            cause instanceof ApiError && cause.status === 404
              ? t("分享链接不存在或已被撤销。")
              : String(cause instanceof Error ? cause.message : cause),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (error) {
    return (
      <div className="shared">
        <div className="shared__card" role="alert">
          <h1>Poliscope</h1>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="shared">
        <Spinner label={t("正在载入分享的研究快照…")} />
      </div>
    );
  }

  const { brief } = snapshot;
  return (
    <div className="shared">
      <header className="shared__chrome">
        <span className="shared__wordmark">Poliscope</span>
        <span className="shared__tag">{t("只读分享 · 七人议会争议证据地图")}</span>
      </header>

      <div className="shared__card">
        <div className="shared__head">
          <h1>{snapshot.task.question}</h1>
          <TaskStatusBadge status={snapshot.task.status} />
        </div>
        <p className="shared__notice">
          {t(
            "这是研究者通过只读链接分享的 AI 辅助研究快照，结论与局限并排呈现；它不构成临床诊断或医疗建议。",
          )}
        </p>

        <section>
          <h2>{t("已确认主张")}</h2>
          {brief.confirmed_claims.length === 0 ? (
            <p className="shared__empty">{t("无")}</p>
          ) : (
            <ol className="shared__list">
              {brief.confirmed_claims.map((claim) => (
                <li key={claim.claim_id}>{claim.statement}</li>
              ))}
            </ol>
          )}
        </section>

        <section>
          <h2>{t("盲点")}</h2>
          {brief.blindspots.length === 0 ? (
            <p className="shared__empty">{t("无")}</p>
          ) : (
            <ul className="shared__list">
              {brief.blindspots.map((node) => (
                <li key={node.id}>{String(node.payload.statement ?? "")}</li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h2>{t("局限")}</h2>
          {brief.limitations.length === 0 ? (
            <p className="shared__empty">{t("无")}</p>
          ) : (
            <ul className="shared__list">
              {brief.limitations.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h2>{t("少数异议")}</h2>
          {brief.dissents.length === 0 ? (
            <p className="shared__empty">{t("无")}</p>
          ) : (
            <ul className="shared__list">
              {brief.dissents.map((node) => (
                <li key={node.id}>{String(node.payload.statement ?? node.payload.reason ?? "")}</li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="shared__card">
        <LineageView lineage={snapshot.lineage} />
      </div>
    </div>
  );
}
