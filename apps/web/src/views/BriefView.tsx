/** Research Brief.
 *
 * The layout is the argument. CLAUDE.md 11 requires conclusions and limitations
 * side by side, so at any width wide enough for two columns they are two
 * columns of equal weight -- not a findings section with a caveats footnote.
 * Below that width they stack, and limitations come *second within the same
 * section*, never after the blindspots and the appendix, because a reader who
 * stops halfway must not stop having read only the findings.
 */

import type {
  BriefNode,
  EvidenceGraph,
  ResearchBrief,
  SafetyNotice,
} from "../api/types";
import { Badge, Empty, Metric, Panel, STATUS_LABELS, toneForStatus } from "../components/primitives";
import { t } from "../i18n";

import "./BriefView.css";

function statementOf(node: BriefNode): string {
  for (const key of ["statement", "question", "summary", "exact_quote"]) {
    const value = node.payload[key];
    if (typeof value === "string" && value) return value;
  }
  return `${node.node_type} ${node.id.slice(0, 8)}`;
}

function NodeList({ nodes, empty }: { nodes: BriefNode[]; empty: string }) {
  if (nodes.length === 0) return <Empty>{empty}</Empty>;
  return (
    <ul className="brief__list">
      {nodes.map((node) => (
        <li key={node.id}>
          <span>{statementOf(node)}</span>
          {node.status !== "active" ? (
            <Badge tone={toneForStatus(node.status)}>
              {t(STATUS_LABELS[node.status] ?? node.status)}
            </Badge>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function BriefView({
  brief,
  safety,
  graph,
  onExport,
}: {
  brief: ResearchBrief;
  safety: SafetyNotice;
  /** 争议结构来自证据图：DebateCapsule 节点与 REFUTES/CONTRADICTS 边。
   * 每次任务运行结束必投影证据图（CLAUDE.md 5），所以这个 section 永远
   * 反映本任务真实的争议轮廓，而不是一个固定模板。 */
  graph?: EvidenceGraph;
  onExport: () => void;
}) {
  const overstated = brief.independent_cluster_count < brief.paper_count;
  const debateNodes = (graph?.nodes ?? []).filter(
    (node) => node.node_type === "DebateCapsule",
  ).length;
  const conflictEdges = (graph?.edges ?? []).filter((edge) =>
    ["REFUTES", "CONTRADICTS"].includes(edge.edge_type),
  ).length;

  return (
    <div className="brief">
      {/* CLAUDE.md 16: the clinical disclaimer sits above the conclusions for a
          mental-health question, not in a footer. */}
      {brief.is_mental_health ? (
        <aside className="brief__safety" role="note">
          <strong>{safety.classification}</strong>
          <p>{safety.medical_disclaimer}</p>
          <p>{safety.limitations}</p>
        </aside>
      ) : null}

      <Panel
        title={t("结论与局限")}
        subtitle={t("两栏等重呈现。局限不是脚注。")}
        actions={
          <button type="button" className="button" onClick={onExport}>
            {t("导出 Markdown")}
          </button>
        }
      >
        <div className="brief__columns">
          <div className="brief__column">
            <h3>{t("已确认原子主张")}</h3>
            {brief.confirmed_claims.length === 0 ? (
              <Empty>{t("研究者尚未确认任何原子主张。")}</Empty>
            ) : (
              <ul className="brief__claims">
                {brief.confirmed_claims.map((claim) => (
                  <li key={claim.claim_id}>
                    <p className="brief__claim-statement">{claim.statement}</p>
                    <dl className="brief__claim-meta">
                      <dt>{t("类型")}</dt>
                      <dd>{claim.claim_type}</dd>
                      <dt>{t("证伪条件")}</dt>
                      <dd>{claim.falsification_condition}</dd>
                    </dl>
                  </li>
                ))}
              </ul>
            )}

            <h3>{t("已采纳发现")}</h3>
            <NodeList
              nodes={brief.findings}
              empty={t("无已采纳的研究发现：本次运行没有任何结论建立在已取回的原文之上。")}
            />
          </div>

          <div className="brief__column brief__column--limits">
            <h3>{t("局限与未知")}</h3>
            {brief.limitations.length === 0 ? (
              <Empty>{t("未记录局限。")}</Empty>
            ) : (
              <ul className="brief__limits">
                {brief.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Panel>

      <Panel title={t("证据覆盖")} subtitle={t("论文数量不等于独立证据数量。")}>
        <div className="brief__metrics">
          <Metric label={t("论文数量")} value={brief.paper_count} />
          <Metric
            label={t("独立证据簇")}
            value={brief.independent_cluster_count}
            tone={overstated ? "provisional" : undefined}
            caveat={
              overstated
                ? t("论文数高于独立证据簇数，直接引用论文数会夸大佐证强度。")
                : undefined
            }
          />
          <Metric
            label={t("缺席席位")}
            value={new Set(brief.absent_seats).size}
            tone={brief.absent_seats.length ? "refuted" : undefined}
            caveat={
              brief.absent_seats.length
                ? t("这些席位的视角未进入结论。")
                : undefined
            }
          />
          <Metric
            label={t("被证据门拒绝")}
            value={brief.unadmitted_events.length}
            caveat={t("保留在账本中可审计，未参与任何结论。")}
          />
          <Metric
            label={t("被反驳/收窄/撤回")}
            value={brief.refuted_or_withdrawn.length}
            caveat={t("按 CLAUDE.md 5.3 保留，未物理删除。")}
          />
        </div>
      </Panel>

      <div className="brief__grid">
        <Panel title={t("盲点")} subtitle={t("本产品的核心价值所在。")}>
          <NodeList nodes={brief.blindspots} empty={t("本轮未提名盲点。")} />
        </Panel>
        <Panel title={t("少数意见与异议")} subtitle={t("被反驳的立场依然可追溯。")}>
          <NodeList nodes={brief.dissents} empty={t("无记录在案的异议。")} />
        </Panel>
        <Panel title={t("争议结构")} subtitle={t("议会对峙的证据轮廓（来自证据图）。")}>
          {debateNodes === 0 && conflictEdges === 0 ? (
            <Empty>
              {t(
                "本任务未产生争议节点（无 DebateCapsule、无反驳/冲突边）——这不是「没有争议」，而是本轮议会未将其登记为正式结构。",
              )}
            </Empty>
          ) : (
            <ul className="brief__dispute">
              <li>
                <strong>{debateNodes}</strong> {t("个争议胶囊（DebateCapsule）")}
              </li>
              <li>
                <strong>{conflictEdges}</strong> {t("条反驳/冲突边（REFUTES / CONTRADICTS）")}
              </li>
            </ul>
          )}
        </Panel>
        <Panel title={t("可区分性研究建议")}>
          <NodeList
            nodes={brief.discriminating_studies}
            empty={t("未提出可区分性研究。")}
          />
        </Panel>
      </div>

      <p className="brief__ai-notice">
        {t(
          "本报告由 AI 辅助研究系统生成。所有结论均须结合原始文献独立复核；模型置信度不替代统计不确定性或专家判断。",
        )}
      </p>
    </div>
  );
}
