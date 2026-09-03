/** C9 Human Annotation.
 *
 * The missing collection pipeline for packages/evaluation/agreement.py: freeze
 * the blindspots/claims under review into a batch, let multiple raters label
 * each item, and read back inter-rater agreement (Cohen's kappa for two
 * raters, Krippendorff's alpha for three or more).
 *
 * These are human judgments ABOUT the system's output. They never enter the
 * Evidence Graph and the Graph Projector never reads them (AGENTS.md 5).
 */

import { useEffect, useMemo, useState } from "react";

import {
  addAnnotationLabel,
  createAnnotationBatch,
  fetchAnnotationBatch,
  listAnnotationBatches,
} from "../api/client";
import type {
  AnnotationBatchDetail,
  AnnotationBatchSummary,
  WorkspaceSnapshot,
} from "../api/types";
import { Badge, Empty, Metric, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./ResearchTools.css";

const LABELS = [
  { value: "relevant", label: "相关 / 有效" },
  { value: "not_relevant", label: "不相关 / 无效" },
  { value: "unsure", label: "不确定" },
] as const;

interface Candidate {
  refKind: "blindspot" | "claim";
  refNodeId: string;
  statement: string;
}

export function AnnotationView({
  taskId,
  snapshot,
}: {
  taskId: string;
  snapshot: WorkspaceSnapshot;
}) {
  const [batches, setBatches] = useState<AnnotationBatchSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AnnotationBatchDetail | null>(null);
  const [rater, setRater] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const candidates = useMemo<Candidate[]>(() => {
    const blindspots = snapshot.blindspots
      .filter((node) => typeof node.statement === "string")
      .map((node) => ({
        refKind: "blindspot" as const,
        refNodeId: node.id,
        statement: String(node.statement),
      }));
    const claims = snapshot.brief.confirmed_claims.map((claim) => ({
      refKind: "claim" as const,
      refNodeId: claim.claim_id,
      statement: claim.statement,
    }));
    return [...blindspots, ...claims];
  }, [snapshot]);

  async function reloadBatches() {
    try {
      setBatches(await listAnnotationBatches(taskId));
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function openBatch(batchId: string) {
    setActiveId(batchId);
    setDetail(await fetchAnnotationBatch(batchId));
  }

  useEffect(() => {
    void reloadBatches();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  function toggle(key: string) {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function createBatch() {
    setError(null);
    const items = candidates
      .filter((candidate) => picked.has(`${candidate.refKind}:${candidate.refNodeId}`))
      .map((candidate) => ({
        ref_kind: candidate.refKind,
        ref_node_id: candidate.refNodeId,
        statement: candidate.statement,
      }));
    if (items.length === 0) {
      setError(t("请至少勾选一个待标注项。"));
      return;
    }
    try {
      const created = await createAnnotationBatch(taskId, items);
      setPicked(new Set());
      await reloadBatches();
      await openBatch(created.batch_id);
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function label(itemId: string, labelValue: string) {
    if (!activeId || !rater.trim()) {
      setError(t("请先填写评分者姓名。"));
      return;
    }
    setError(null);
    try {
      await addAnnotationLabel(activeId, itemId, rater.trim(), labelValue);
      setDetail(await fetchAnnotationBatch(activeId));
    } catch (cause) {
      setError(String(cause));
    }
  }

  const agreement = detail?.agreement;

  return (
    <div className="rt-columns">
      <Panel
        title={t("新建人工标注批次")}
        subtitle={t("把盲点与已确认主张冻结为待标注项，供多名评分者独立判断。")}
      >
        {candidates.length === 0 ? (
          <Empty>{t("暂无可标注项：需要已确认主张或已提名盲点。")}</Empty>
        ) : (
          <>
            <ul className="rt-candidates">
              {candidates.map((candidate) => {
                const key = `${candidate.refKind}:${candidate.refNodeId}`;
                return (
                  <li key={key}>
                    <label className="rt-candidate">
                      <input
                        type="checkbox"
                        checked={picked.has(key)}
                        onChange={() => toggle(key)}
                      />
                      <Badge tone={candidate.refKind === "blindspot" ? "provisional" : "admitted"}>
                        {candidate.refKind === "blindspot" ? t("盲点") : t("主张")}
                      </Badge>
                      <span>{candidate.statement}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
            <div className="rt-actions">
              <button type="button" onClick={() => void createBatch()}>
                {t("创建批次（已选 {0} 项）", picked.size)}
              </button>
            </div>
          </>
        )}
      </Panel>

      <Panel title={t("标注批次")}>
        {batches.length === 0 ? (
          <Empty>{t("还没有标注批次。")}</Empty>
        ) : (
          <ul className="rt-list">
            {batches.map((batch) => (
              <li key={batch.batch_id} className="rt-list__row">
                <button
                  type="button"
                  className="rt-linkbutton"
                  onClick={() => void openBatch(batch.batch_id)}
                >
                  {batch.title || t("批次 {0}", batch.batch_id.slice(0, 8))}
                </button>
                <span className="rt-list__aside">
                  {t("{0} 项 · {1} 名评分者", batch.item_count, batch.rater_count)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {detail ? (
        <Panel
          title={t("批次明细：{0}", detail.title || detail.batch_id.slice(0, 8))}
          subtitle={t("同一评分者对同一项重复标注会覆盖旧判断。")}
        >
          <div className="rt-actions">
            <input
              type="text"
              placeholder={t("评分者姓名")}
              value={rater}
              onChange={(event) => setRater(event.target.value)}
              aria-label={t("评分者姓名")}
            />
          </div>
          <div className="rt-metrics">
            <Metric label={t("评分者人数")} value={agreement?.raters ?? 0} />
            <Metric
              label={
                agreement?.method === "cohen_kappa"
                  ? t("Cohen's κ")
                  : agreement?.method === "krippendorff_alpha"
                    ? t("Krippendorff's α")
                    : t("一致性")
              }
              value={
                agreement?.value == null
                  ? t("需 ≥2 名评分者")
                  : agreement.value.toFixed(3)
              }
              caveat={agreement?.note ?? undefined}
            />
          </div>
          <div className="rt-stack">
            {detail.items.map((item) => (
              <article key={item.id} className="rt-card">
                <p className="rt-card__text">{item.statement}</p>
                <div className="rt-actions">
                  {LABELS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => void label(item.id, option.value)}
                    >
                      {t(option.label)}
                    </button>
                  ))}
                </div>
                {item.labels.length > 0 ? (
                  <ul className="rt-decisions">
                    {item.labels.map((lab, index) => (
                      <li key={index} className="mono">
                        {lab.rater_name}: {lab.label}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </article>
            ))}
          </div>
        </Panel>
      ) : null}

      {error ? <p className="rt-message rt-message--error">{error}</p> : null}
    </div>
  );
}
