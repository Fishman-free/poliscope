/** A1 Evidence Lineage.
 *
 * Paper count is not independent-evidence count (CLAUDE.md 7.4 / principle 4).
 * This view makes the gap explicit: every admitted Source, the dependency
 * links that tie papers together (shared dataset, overlapping sample,
 * preprint-vs-published, same research team is shown but never merged), and
 * the independent clusters the papers fold into.
 *
 * The structure is computed server-side by build_lineage_view; this component
 * only renders it. A Source with no merging link is its own cluster, which is
 * exactly the independence claim the researcher needs to be able to audit.
 */

import { useMemo } from "react";

import type { LineageViewData } from "../api/types";
import { Badge, Empty, Metric, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./ResearchTools.css";

const DEP_LABELS: Record<string, string> = {
  SHARED_DATASET: "共享数据集",
  OVERLAPPING_SAMPLE: "重叠样本",
  PREPRINT_PUBLISHED: "预印本/正式版",
  EXTENSION_REANALYSIS: "扩展/再分析",
  REVIEW_INCLUDES_STUDY: "综述包含原始研究",
  SAME_RESEARCH_TEAM: "同一研究团队（不合并）",
};

export function LineageView({ lineage }: { lineage: LineageViewData | null | undefined }) {
  const mergedLinks = useMemo(
    () => lineage?.links.filter((link) => link.merges) ?? [],
    [lineage],
  );

  if (!lineage) {
    return (
      <Panel title={t("证据谱系")} subtitle={t("论文数量不等于独立证据数量。")}>
        <Empty>{t("尚无已采纳来源，或任务尚未进入取证阶段。")}</Empty>
      </Panel>
    );
  }

  return (
    <Panel
      title={t("证据谱系")}
      subtitle={t("按数据集、样本、版本与团队依赖追踪来源独立性。")}
    >
      <div className="rt-metrics">
        <Metric
          label={t("论文数量")}
          value={lineage.paper_count}
          caveat={t("已采纳来源总数")}
        />
        <Metric
          label={t("独立证据簇")}
          value={lineage.independent_cluster_count}
          caveat={t("扣除依赖后的独立证据数")}
          tone={
            lineage.independent_cluster_count < lineage.paper_count
              ? "provisional"
              : "admitted"
          }
        />
        <Metric
          label={t("合并依赖")}
          value={mergedLinks.length}
          caveat={t("使论文不能重复计票的依赖")}
        />
      </div>

      {lineage.sources.length === 0 ? (
        <Empty>{t("尚无已采纳来源。")}</Empty>
      ) : (
        <div className="rt-stack">
          {lineage.clusters.map((cluster) => {
            const sources = lineage.sources.filter((source) =>
              cluster.source_ids.includes(source.source_id),
            );
            return (
              <article key={cluster.cluster_index} className="rt-card">
                <header className="rt-card__head">
                  <Badge tone={sources.length > 1 ? "provisional" : "admitted"}>
                    {t("独立簇 {0}", cluster.cluster_index + 1)}
                  </Badge>
                  <span className="rt-card__meta">
                    {cluster.reason ? DEP_LABELS[cluster.reason] ?? cluster.reason : t("无合并依赖")}
                    {" · "}
                    {t("{0} 篇", sources.length)}
                  </span>
                </header>
                <ul className="rt-list">
                  {sources.map((source) => (
                    <li key={source.source_id} className="rt-list__row">
                      <span className="rt-list__main">{source.title}</span>
                      <span className="rt-list__aside">
                        {source.year ?? "—"}
                        {source.evidence_level ? ` · Level ${source.evidence_level}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </article>
            );
          })}
        </div>
      )}

      {lineage.links.length > 0 ? (
        <section className="rt-section">
          <h3>{t("全部依赖关系（含不合并的同团队标记）")}</h3>
          <ul className="rt-list">
            {lineage.links.map((link) => (
              <li key={`${link.dep_type}-${link.group_key}`} className="rt-list__row">
                <Badge tone={link.merges ? "provisional" : "unknown"}>
                  {DEP_LABELS[link.dep_type] ?? link.dep_type}
                </Badge>
                <span className="rt-list__main mono">{link.group_key}</span>
                <span className="rt-list__aside">
                  {link.merges ? t("合并计为一簇") : t("仅标记，不合并")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Panel>
  );
}
