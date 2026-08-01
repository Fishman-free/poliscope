/** Research Brief.
 *
 * The layout is the argument. CLAUDE.md 11 requires conclusions and limitations
 * side by side, so at any width wide enough for two columns they are two
 * columns of equal weight -- not a findings section with a caveats footnote.
 * Below that width they stack, and limitations come *second within the same
 * section*, never after the blindspots and the appendix, because a reader who
 * stops halfway must not stop having read only the findings.
 */

import type { BriefNode, ResearchBrief, SafetyNotice } from "../api/types";
import { Badge, Empty, Metric, Panel, STATUS_LABELS, toneForStatus } from "../components/primitives";

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
              {STATUS_LABELS[node.status] ?? node.status}
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
  onExport,
}: {
  brief: ResearchBrief;
  safety: SafetyNotice;
  onExport: () => void;
}) {
  const overstated = brief.independent_cluster_count < brief.paper_count;

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
        title="结论与局限"
        subtitle="两栏等重呈现。局限不是脚注。"
        actions={
          <button type="button" className="button" onClick={onExport}>
            导出 Markdown
          </button>
        }
      >
        <div className="brief__columns">
          <div className="brief__column">
            <h3>已确认原子主张</h3>
            {brief.confirmed_claims.length === 0 ? (
              <Empty>研究者尚未确认任何原子主张。</Empty>
            ) : (
              <ul className="brief__claims">
                {brief.confirmed_claims.map((claim) => (
                  <li key={claim.claim_id}>
                    <p className="brief__claim-statement">{claim.statement}</p>
                    <dl className="brief__claim-meta">
                      <dt>类型</dt>
                      <dd>{claim.claim_type}</dd>
                      <dt>证伪条件</dt>
                      <dd>{claim.falsification_condition}</dd>
                    </dl>
                  </li>
                ))}
              </ul>
            )}

            <h3>已采纳发现</h3>
            <NodeList
              nodes={brief.findings}
              empty="无已采纳的研究发现：本次运行没有任何结论建立在已取回的原文之上。"
            />
          </div>

          <div className="brief__column brief__column--limits">
            <h3>局限与未知</h3>
            {brief.limitations.length === 0 ? (
              <Empty>未记录局限。</Empty>
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

      <Panel title="证据覆盖" subtitle="论文数量不等于独立证据数量。">
        <div className="brief__metrics">
          <Metric label="论文数量" value={brief.paper_count} />
          <Metric
            label="独立证据簇"
            value={brief.independent_cluster_count}
            tone={overstated ? "provisional" : undefined}
            caveat={
              overstated
                ? "论文数高于独立证据簇数，直接引用论文数会夸大佐证强度。"
                : undefined
            }
          />
          <Metric
            label="缺席席位"
            value={new Set(brief.absent_seats).size}
            tone={brief.absent_seats.length ? "refuted" : undefined}
            caveat={
              brief.absent_seats.length
                ? "这些席位的视角未进入结论。"
                : undefined
            }
          />
          <Metric
            label="被证据门拒绝"
            value={brief.unadmitted_events.length}
            caveat="保留在账本中可审计，未参与任何结论。"
          />
          <Metric
            label="被反驳/收窄/撤回"
            value={brief.refuted_or_withdrawn.length}
            caveat="按 CLAUDE.md 5.3 保留，未物理删除。"
          />
        </div>
      </Panel>

      <div className="brief__grid">
        <Panel title="盲点" subtitle="本产品的核心价值所在。">
          <NodeList nodes={brief.blindspots} empty="本轮未提名盲点。" />
        </Panel>
        <Panel title="少数意见与异议" subtitle="被反驳的立场依然可追溯。">
          <NodeList nodes={brief.dissents} empty="无记录在案的异议。" />
        </Panel>
        <Panel title="可区分性研究建议">
          <NodeList
            nodes={brief.discriminating_studies}
            empty="未提出可区分性研究。"
          />
        </Panel>
      </div>

      <p className="brief__ai-notice">
        本报告由 AI 辅助研究系统生成。所有结论均须结合原始文献独立复核；
        模型置信度不替代统计不确定性或专家判断。
      </p>
    </div>
  );
}
