/** 推理图谱：最终结论的结构化视图。
 *
 * 旧版总结把议会产出按「盲点 / 边界 / 分歧 / 检验」平行罗列，几十条互不
 * 相连的碎片读不出一条论证（用户反馈：知识和盲点特别碎片化、连不起来）。
 * 这里按一条固定的推理脊柱逐步展开 —— 证据基础 → 测量 → 稳健性 → 因果 →
 * 适用边界 → 机制与替代解释 —— 每一步先给结论，再在同一行并排给出「依据」
 * 与「边界与未知」（CLAUDE.md 11：结论与局限并排）。盲点不再单独成列，而是
 * 挂在它所威胁的那一步下面。
 *
 * 阅读顺序即论证顺序：
 *   一句话回答 + 主张判定 → 推理图谱总览（可点击跳转）→ 逐步推理 →
 *   关键分歧 → 下一步研究 → 附录（专家终审原文、未归入条目、文献）。
 *
 * 溯源 chips：每一步引用的条目以 [B3] 这样的编号出现，点开即见议会原文，
 * 所以整合之后仍可审计、异议不会因为被「总结」而消失（CLAUDE.md 4、5.3）。
 */

import { useMemo, useState } from "react";

import type {
  FinalPaper,
  MapItem,
  MapItemKind,
  MapVerdict,
  PaperReference,
  ReasoningMap,
  ReasoningStep,
} from "../api/types";
import { SEAT_LABELS, type Seat } from "../api/types";
import { Badge, Empty, type Tone } from "../components/primitives";
import { Markdown } from "../components/Markdown";
import { t } from "../i18n";

import "./ReasoningMapView.css";

const VERDICT_LABELS: Record<MapVerdict, string> = {
  supported: "成立",
  partially_supported: "部分成立",
  not_supported: "不成立",
  undetermined: "尚无定论",
};

const VERDICT_TONES: Record<MapVerdict, Tone> = {
  supported: "admitted",
  partially_supported: "provisional",
  not_supported: "refuted",
  undetermined: "unknown",
};

const KIND_LABELS: Record<MapItemKind, string> = {
  judgment: "专家终审",
  finding: "研究发现",
  blindspot: "盲点",
  boundary: "适用边界",
  conflict: "未决分歧",
  test: "可证伪检验",
  dissent: "异议",
  study: "判别研究",
};

/** 推理脊柱的短名：总览图谱只放得下两到四个字。 */
const DIMENSION_SHORT: Record<string, string> = {
  evidence: "证据基础",
  measurement: "测量",
  robustness: "稳健性",
  causality: "因果",
  boundary: "适用边界",
  mechanism: "机制",
};

const STEP_NUMERALS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"];

function seatName(seat: string | null): string {
  if (!seat) return "";
  return t(SEAT_LABELS[seat as Seat] ?? seat);
}

function stepAnchor(index: number): string {
  return `rmap-step-${index + 1}`;
}

/** 一句话问题里的冒号前半部分（「测量：测到的是…」→「测到的是…」）。 */
function firstLine(text: string): string {
  return text.split("\n")[0] ?? text;
}

/* ------------------------------------------------------------ 溯源 chips */

function RefChips({
  refs,
  items,
  label,
}: {
  refs: string[];
  items: Map<string, MapItem>;
  label?: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const known = refs.filter((ref) => items.has(ref));
  if (known.length === 0) return null;
  const current = open ? items.get(open) : undefined;
  return (
    <div className="rmap__refs">
      <span className="rmap__refs-label">{label ?? t("溯源")}</span>
      {known.map((ref) => {
        const item = items.get(ref)!;
        return (
          <button
            key={ref}
            type="button"
            className={
              "rmap__chip rmap__chip--" +
              item.kind +
              (open === ref ? " rmap__chip--on" : "")
            }
            aria-expanded={open === ref}
            title={`${t(KIND_LABELS[item.kind] ?? item.kind)}：${item.headline}`}
            onClick={() => setOpen(open === ref ? null : ref)}
          >
            {ref}
          </button>
        );
      })}
      {current ? <ItemCard item={current} /> : null}
    </div>
  );
}

function ItemCard({ item }: { item: MapItem }) {
  return (
    <div className="rmap__item-card" role="region" aria-label={item.id}>
      <p className="rmap__item-meta">
        <span className="mono">{item.id}</span>
        <span>{t(KIND_LABELS[item.kind] ?? item.kind)}</span>
        {item.seat ? <span>{seatName(item.seat)}</span> : null}
        {item.kind === "blindspot" && item.score ? (
          <span>{t("悬赏得分 {0}", item.score)}</span>
        ) : null}
        {item.has_dissent ? <Badge tone="provisional">{t("保留异议")}</Badge> : null}
      </p>
      <Markdown className="rmap__item-text paper__md" text={item.text} />
    </div>
  );
}

/** 同一环节、未在正文点名的其余条目：默认折叠，展开即列，一条不少。 */
function RelatedItems({
  related,
  items,
}: {
  related: string[];
  items: Map<string, MapItem>;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const known = related.filter((ref) => items.has(ref));
  if (known.length === 0) return null;
  return (
    <details className="rmap__related">
      <summary>{t("同一环节的其余条目（{0}）", known.length)}</summary>
      <ul>
        {known.map((ref) => {
          const item = items.get(ref)!;
          return (
            <li key={ref}>
              <button
                type="button"
                className="rmap__related-row"
                aria-expanded={open === ref}
                onClick={() => setOpen(open === ref ? null : ref)}
              >
                <span className={`rmap__chip rmap__chip--${item.kind}`}>{ref}</span>
                <span className="rmap__related-kind">
                  {t(KIND_LABELS[item.kind] ?? item.kind)}
                </span>
                <span className="rmap__related-headline">{item.headline}</span>
              </button>
              {open === ref ? <ItemCard item={item} /> : null}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

/* --------------------------------------------------------------- 各分区 */

function BulletGroup({
  label,
  values,
  tone,
}: {
  label: string;
  values: string[];
  tone: "support" | "limit" | "unknown";
}) {
  if (values.length === 0) return null;
  return (
    <div className={`rmap__group rmap__group--${tone}`}>
      <h5>{t(label)}</h5>
      <ul>
        {values.map((value, index) => (
          <li key={index}>{value}</li>
        ))}
      </ul>
    </div>
  );
}

function StepCard({
  step,
  index,
  items,
}: {
  step: ReasoningStep;
  index: number;
  items: Map<string, MapItem>;
}) {
  const hasSupport = step.support.length > 0;
  const hasDoubts = step.limits.length > 0 || step.unknowns.length > 0;
  return (
    <section className="rmap__step" id={stepAnchor(index)}>
      <header className="rmap__step-head">
        <span className="rmap__step-number" aria-hidden="true">
          {STEP_NUMERALS[index] ?? index + 1}
        </span>
        <h4>{step.question}</h4>
      </header>
      <div className="rmap__step-answer">
        {step.answer.split("\n").map((line, lineIndex) => (
          <p key={lineIndex}>{line}</p>
        ))}
      </div>
      {hasSupport ? (
        <div className="rmap__step-columns">
          <div className="rmap__step-column">
            <BulletGroup label="依据" values={step.support} tone="support" />
          </div>
          <div className="rmap__step-column rmap__step-column--doubts">
            <BulletGroup label="边界与反例" values={step.limits} tone="limit" />
            <BulletGroup label="尚未知道" values={step.unknowns} tone="unknown" />
            {!hasDoubts ? (
              <p className="rmap__none">{t("本环节未记录边界或未知。")}</p>
            ) : null}
          </div>
        </div>
      ) : hasDoubts ? (
        // 没有已采纳发现时，空着的「依据」栏只会占掉半屏：改为一行说明，
        // 边界与未知并排两栏铺开。
        <>
          <p className="rmap__none">{t("本环节没有已采纳的研究发现作为依据。")}</p>
          <div className="rmap__step-columns">
            <div className="rmap__step-column">
              <BulletGroup label="边界与反例" values={step.limits} tone="limit" />
            </div>
            <div className="rmap__step-column">
              <BulletGroup label="尚未知道" values={step.unknowns} tone="unknown" />
            </div>
          </div>
        </>
      ) : null}
      {step.resolve ? (
        <p className="rmap__resolve">
          <span>{t("如何验证")}</span>
          {step.resolve}
        </p>
      ) : null}
      <RefChips refs={step.refs} items={items} />
      <RelatedItems related={step.related} items={items} />
    </section>
  );
}

/** 推理图谱总览：一条脊柱，每一步一行，带依据/边界/未知计数，点击跳到该步。 */
function MapOverview({ steps, question }: { steps: ReasoningStep[]; question: string }) {
  function jump(index: number) {
    const target = document.getElementById(stepAnchor(index));
    if (!target) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }
  return (
    <nav className="rmap__overview" aria-label={t("推理图谱")}>
      <div className="rmap__overview-root">
        <span className="rmap__overview-root-label">{t("研究问题")}</span>
        <span className="rmap__overview-root-text">{question}</span>
      </div>
      <ol className="rmap__spine">
        {steps.map((step, index) => (
          <li key={`${step.dimension}-${index}`}>
            <button type="button" className="rmap__spine-node" onClick={() => jump(index)}>
              <span className="rmap__spine-number">{STEP_NUMERALS[index] ?? index + 1}</span>
              <span className="rmap__spine-name">
                {t(DIMENSION_SHORT[step.dimension] ?? step.dimension)}
              </span>
              <span className="rmap__spine-answer">{firstLine(step.answer)}</span>
              <span className="rmap__spine-counts">
                {step.support.length > 0 ? (
                  <span className="rmap__count rmap__count--support">
                    {t("依据 {0}", step.support.length)}
                  </span>
                ) : null}
                {step.limits.length > 0 ? (
                  <span className="rmap__count rmap__count--limit">
                    {t("边界 {0}", step.limits.length)}
                  </span>
                ) : null}
                {step.unknowns.length > 0 ? (
                  <span className="rmap__count rmap__count--unknown">
                    {t("未知 {0}", step.unknowns.length)}
                  </span>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

function ReferenceList({ references }: { references: PaperReference[] }) {
  if (references.length === 0) {
    return <Empty>{t("本次没有可绑定 DOI 的已采纳研究发现。")}</Empty>;
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

/* ------------------------------------------------------------------ 主视图 */

export function ReasoningMapView({
  paper,
  map,
  question,
  onExport,
}: {
  paper: FinalPaper;
  map: ReasoningMap;
  question: string;
  onExport: () => void;
}) {
  const items = useMemo(() => {
    const index = new Map<string, MapItem>();
    for (const item of map.items) index.set(item.id, item);
    return index;
  }, [map.items]);
  const judgments = map.items.filter((item) => item.kind === "judgment");
  const deterministic = map.mode === "deterministic";

  return (
    <div className="rmap">
      <section className="rmap__answer" aria-label={t("一句话回答")}>
        <div className="rmap__answer-head">
          <div>
            <p className="rmap__eyebrow">{t("研究结论")}</p>
            <h2 className="rmap__question">{question}</h2>
          </div>
          <button type="button" className="button" onClick={onExport}>
            {t("下载 Markdown")}
          </button>
        </div>
        {deterministic ? (
          <p className="rmap__mode-note" role="status">
            {map.derived_on_read
              ? t(
                  "这是较早完成的任务：系统按新的推理链结构，把当时的专家终审与全部讨论条目重新归档（未重跑、未调用模型）。每一步的判断以专家原话为准。",
                )
              : t(
                  "本次结论未经模型整合{0}：系统把专家终审要点与全部讨论条目按推理链机械归档，每一步的判断以专家原话为准。",
                  paper.fallback_reason ? `（${paper.fallback_reason}）` : "",
                )}
          </p>
        ) : null}
        {map.bottom_line ? (
          <Markdown className="rmap__bottom-line paper__md" text={map.bottom_line} />
        ) : (
          <p className="rmap__bottom-line rmap__bottom-line--none">
            {t("专家之间没有形成可以一句话概括的共同结论 —— 请沿下方推理链逐步阅读各环节的判断。")}
          </p>
        )}
        {map.verdicts.length > 0 ? (
          <ul className="rmap__verdicts">
            {map.verdicts.map((verdict, index) => (
              <li key={index} className="rmap__verdict">
                <Badge tone={VERDICT_TONES[verdict.verdict]}>
                  {t(VERDICT_LABELS[verdict.verdict])}
                </Badge>
                <span className="rmap__verdict-claim">{verdict.claim}</span>
                {verdict.reason ? (
                  <span className="rmap__verdict-reason">{verdict.reason}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {map.steps.length > 0 ? (
        <>
          <MapOverview steps={map.steps} question={question} />
          <div className="rmap__chain">
            <h3 className="rmap__section-title">{t("逐步推理")}</h3>
            <p className="rmap__section-note">
              {t("每一步先给结论，右侧并排列出它的边界与尚未知道的事 —— 盲点挂在它所威胁的那一步下面。")}
            </p>
            {map.steps.map((step, index) => (
              <StepCard key={`${step.dimension}-${index}`} step={step} index={index} items={items} />
            ))}
          </div>
        </>
      ) : (
        <Empty>{t("本次研究没有足够材料形成推理链。")}</Empty>
      )}

      {map.cruxes.length > 0 ? (
        <section className="rmap__block">
          <h3 className="rmap__section-title">{t("关键分歧")}</h3>
          <p className="rmap__section-note">
            {t("证据尚未裁决的分歧：各方观点并列保留，不按人数定对错。")}
          </p>
          {map.cruxes.map((crux, index) => (
            <div key={index} className="rmap__crux">
              <h4>{crux.question}</h4>
              <ul className="rmap__positions">
                {crux.positions.map((position, pIndex) => (
                  <li key={pIndex}>
                    <span className="rmap__holders">{position.holders || t("一方")}</span>
                    <span>{position.view}</span>
                  </li>
                ))}
              </ul>
              {crux.decider ? (
                <p className="rmap__resolve">
                  <span>{t("什么证据能裁决")}</span>
                  {crux.decider}
                </p>
              ) : null}
              <RefChips refs={crux.refs} items={items} />
            </div>
          ))}
        </section>
      ) : null}

      {map.next_steps.length > 0 ? (
        <section className="rmap__block">
          <h3 className="rmap__section-title">{t("下一步研究")}</h3>
          <ol className="rmap__next">
            {map.next_steps.map((action, index) => (
              <li key={index}>
                <span className="rmap__next-action">{action.action}</span>
                {action.why ? <span className="rmap__next-why">{action.why}</span> : null}
                <RefChips refs={action.refs} items={items} />
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <section className="rmap__block rmap__limits">
        <h3 className="rmap__section-title">{t("本次研究的局限")}</h3>
        {paper.limitations.length === 0 ? (
          <Empty>{t("未记录局限。")}</Empty>
        ) : (
          <ul>
            {paper.limitations.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        )}
      </section>

      <details className="rmap__appendix">
        <summary>
          {t("附录：专家终审原文、未归入条目、调查过程与参考文献")}
        </summary>
        {judgments.length > 0 ? (
          <div className="rmap__appendix-block">
            <h4>{t("专家终审原文")}</h4>
            {judgments.map((item) => (
              <details key={item.id} className="rmap__judgment">
                <summary>
                  <span className="mono">{item.id}</span>
                  <span className="rmap__judgment-seat">{seatName(item.seat)}</span>
                  {item.has_dissent ? (
                    <Badge tone="provisional">{t("保留异议")}</Badge>
                  ) : null}
                  <span className="rmap__judgment-headline">{item.headline}</span>
                </summary>
                <Markdown className="paper__md" text={item.text} />
              </details>
            ))}
          </div>
        ) : null}
        {map.unassigned.length > 0 ? (
          <div className="rmap__appendix-block">
            <h4>{t("未归入推理链的条目")}</h4>
            <RelatedItems related={map.unassigned} items={items} />
          </div>
        ) : null}
        {paper.investigation_process.length > 0 ? (
          <div className="rmap__appendix-block">
            <h4>{t("调查过程")}</h4>
            <ul>
              {paper.investigation_process.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="rmap__appendix-block">
          <h4>{t("参考文献")}</h4>
          <ReferenceList references={paper.references} />
        </div>
      </details>

      <p className="paper__ai-notice">
        {t(
          "本结论由 AI 辅助研究系统整合生成，供研究者核验原始文献时使用；它本身不构成新的科学证据，所有判断须结合审计轨迹中的原始来源独立复核。",
        )}
      </p>
    </div>
  );
}
