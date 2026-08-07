/** 最终论文：综合 agent 整合议会产出后的完整论文视图。
 *
 * 这是用户要求的「最终结论」：与 Research Brief（模板组装的结构化摘要）
 * 不同，它是由一个独立的综合 agent 在议会结束后把七位科学家的立场、
 * 条件化共识、已采纳发现整合成的完整论文（摘要、正文、参考文献、
 * 局限、调查过程）。
 *
 * 诚实性纪律（CLAUDE.md 10/11）：
 * - 论文不存在时不渲染任何模板假论文——只有空态 + 原因 + 指向 Brief。
 * - 局限与结论同区呈现，不隐藏。
 * - 参考文献的 DOI 可点击跳转；无 DOI 的来源只显示标题。
 */

import type { FinalPaper, PaperReference } from "../api/types";
import { Badge, Empty, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./PaperView.css";

function ReferenceList({ references }: { references: PaperReference[] }) {
  if (references.length === 0) {
    return <Empty>{t("论文未引用任何来源。")}</Empty>;
  }
  return (
    <ol className="paper__references">
      {references.map((reference, index) => (
        <li key={`${reference.id}-${index}`}>
          <span className="paper__ref-index">[{index + 1}]</span>
          <span className="paper__ref-title">{reference.title}</span>
          {reference.doi ? (
            <a
              className="paper__ref-link"
              href={`https://doi.org/${reference.doi}`}
              target="_blank"
              rel="noreferrer"
              title={reference.doi}
            >
              {t("打开文献 ↗")}
            </a>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export function PaperView({
  paper,
  onExport,
  onViewBrief,
}: {
  paper: FinalPaper | null;
  onExport: () => void;
  onViewBrief: () => void;
}) {
  if (paper === null) {
    return (
      <Panel
        title={t("最终论文")}
        subtitle={t("综合七位科学家立场与议会共识的整合论文。")}
      >
        <Empty>
          {t(
            "综合论文尚未生成。这通常意味着议会仍在进行、模型网关未配置，或综合生成失败——原因可在审计轨迹中查看。当前结论以 Research Brief 为准。",
          )}
        </Empty>
        <div className="paper__empty-actions">
          <button type="button" className="button" onClick={onViewBrief}>
            {t("查看 Research Brief")}
          </button>
        </div>
      </Panel>
    );
  }

  return (
    <div className="paper">
      <Panel
        title={paper.title}
        subtitle={t("最终论文 · 综合 agent 整合议会产出")}
        actions={
          <button type="button" className="button" onClick={onExport}>
            {t("下载论文 Markdown")}
          </button>
        }
      >
        <section className="paper__abstract">
          <h3>{t("摘要")}</h3>
          <p>{paper.abstract}</p>
        </section>

        {paper.investigation_process.length > 0 ? (
          <section className="paper__process">
            <h3>{t("调查过程")}</h3>
            <ul>
              {paper.investigation_process.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </Panel>

      <Panel title={t("正文")} subtitle={t("综合 agent 对议会产出的整合叙述。")}>
        {paper.sections.length === 0 ? (
          <Empty>{t("论文正文为空。")}</Empty>
        ) : (
          <div className="paper__sections">
            {paper.sections.map((section, index) => (
              <section key={index} className="paper__section">
                <h3>{section.heading}</h3>
                {section.paragraphs.map((paragraph, pIndex) => (
                  <p key={pIndex}>{paragraph}</p>
                ))}
              </section>
            ))}
          </div>
        )}
      </Panel>

      <div className="paper__grid">
        <Panel title={t("结论与局限")} subtitle={t("局限与结论并排呈现。")}>
          {paper.limitations.length === 0 ? (
            <Empty>{t("未记录局限。")}</Empty>
          ) : (
            <ul className="paper__limits">
              {paper.limitations.map((item, index) => (
                <li key={index}>
                  <Badge tone="unknown">{t("局限")}</Badge> {item}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title={t("参考文献")} subtitle={t("论文引用的来源（DOI 可点击）。")}>
          <ReferenceList references={paper.references} />
        </Panel>
      </div>

      <p className="paper__ai-notice">
        {t(
          "本论文由 AI 辅助研究系统综合生成，供研究者核验与引用原始文献时使用；论文本身不构成新的科学证据，所有结论须结合审计轨迹中的原始来源独立复核。",
        )}
      </p>
    </div>
  );
}
