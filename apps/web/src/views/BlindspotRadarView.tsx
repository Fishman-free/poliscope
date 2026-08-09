/** Blindspot Radar: impact x investigability, point size = uncertainty.
 *
 * Hand-rolled SVG rather than a charting dependency -- the product has none
 * beyond @xyflow/react, and one scatterplot with three encoded dimensions
 * does not justify adding one (KISS/YAGNI).
 *
 * Not every Blindspot carries all five scored dimensions: the source-
 * diversity check (packages/council/rounds/registry.py's run_acquisition)
 * only ever emits a statement and source_refs, no score at all. Those are
 * listed separately below the chart, never plotted at the origin -- a
 * missing measurement is not the same as a measured zero, and CLAUDE.md 7
 * requires the system to admit what is unknown rather than silently
 * mis-locate it on the chart.
 *
 * Inspector readability (round-6): a researcher clicking a dot gets the
 * blindspot *explained* -- what it is, how big its impact would be if it
 * were real, whether it can be investigated, and what happens if it is
 * ignored -- never a dump of raw fields. Raw payload survives under a
 * collapsible <details> so the original record stays auditable (CLAUDE.md 2
 * separates the original record from any rendering of it), and UUIDs inside
 * the statement are resolved to the claims they name.
 */

import { useMemo, useState } from "react";

import type {
  ConfirmedClaim,
  EvidenceGraph,
  WorkspaceBlindspot,
} from "../api/types";
import { BLINDSPOT_KIND_LABELS } from "../api/types";
import {
  Badge,
  Empty,
  Panel,
  STATUS_LABELS,
  toneForStatus,
} from "../components/primitives";
import { t } from "../i18n";
import {
  buildClaimLabels,
  humanizeText,
  replaceClaimUuids,
} from "./claimLabels";

import "./BlindspotRadarView.css";

const SIZE = 420;
const MARGIN = 40;
const PLOT = SIZE - MARGIN * 2;

interface Plottable {
  node: WorkspaceBlindspot;
  impact: number;
  uncertainty: number;
  investigability: number;
}

function toNumber(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function split(blindspots: WorkspaceBlindspot[]): {
  plottable: Plottable[];
  unscored: WorkspaceBlindspot[];
} {
  const plottable: Plottable[] = [];
  const unscored: WorkspaceBlindspot[] = [];
  for (const node of blindspots) {
    const impact = toNumber(node.impact);
    const uncertainty = toNumber(node.uncertainty);
    const investigability = toNumber(node.investigability);
    if (impact === null || uncertainty === null || investigability === null) {
      unscored.push(node);
    } else {
      plottable.push({ node, impact, uncertainty, investigability });
    }
  }
  return { plottable, unscored };
}

/** Marker shape per kind. "bounty" is a circle, "source_diversity" a
 * diamond; an unrecognised kind is a square so a future kind is visibly
 * distinct rather than silently rendered as a scored bounty. */
function Marker({
  kind,
  cx,
  cy,
  r,
}: {
  kind: string | undefined;
  cx: number;
  cy: number;
  r: number;
}) {
  if (kind === "source_diversity") {
    return (
      <polygon
        points={`${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`}
      />
    );
  }
  if (kind === "bounty") {
    return <circle cx={cx} cy={cy} r={r} />;
  }
  return (
    <rect x={cx - r * 0.85} y={cy - r * 0.85} width={r * 1.7} height={r * 1.7} />
  );
}

/** 0-1 数值的通俗刻度。刻度的切分点与措辞是固定的，不随数据改变——
 * 「显著/中等/轻微」是对评分区间的直接翻译，不是对盲点内容的推测。 */
function impactTier(value: number): string {
  if (value >= 0.66) return t("显著");
  if (value >= 0.33) return t("中等");
  return t("轻微");
}

function investigabilityTier(value: number): string {
  if (value >= 0.66) return t("高——现有手段即可开展检验");
  if (value >= 0.33) return t("中——需要补充证据或设计");
  return t("低——目前难以直接检验");
}

function uncertaintyTier(value: number): string {
  if (value >= 0.66) return t("高——围绕它的认识分歧很大");
  if (value >= 0.33) return t("中——存在部分分歧");
  return t("低——较为确定");
}

/** 按影响评分给出一句「忽略它的后果」解读。模板只翻译评分刻度，不
 * 编造盲点内容；评分缺失时（未评分盲点）不渲染本段。 */
function consequenceForImpact(value: number | null): string | null {
  if (value === null) return null;
  if (value >= 0.66) {
    return t(
      "若忽略此盲点，最终结论可能被实质性扭曲——它指向的未知足以改变排序或因果判断的可信度。",
    );
  }
  if (value >= 0.33) {
    return t("若忽略此盲点，结论的适用范围或稳健性可能被高估。");
  }
  return t("此盲点对结论的潜在影响有限，但仍是记录在案的未知。");
}

/** 一条可读的盲点明细行：标签 + 值 + 刻度条。 */
function ScoreRow({
  label,
  value,
  tier,
}: {
  label: string;
  value: number | null;
  tier: string;
}) {
  return (
    <div className="radar__score-row">
      <span className="radar__score-label">{label}</span>
      {value === null ? (
        <span className="radar__score-value">{t("未评分")}</span>
      ) : (
        <>
          <span className="radar__score-value">{percent(value)}</span>
          <span className="radar__bar" aria-hidden="true">
            <span
              className="radar__bar-fill"
              style={{ width: `${Math.round(value * 100)}%` }}
            />
          </span>
        </>
      )}
      <span className="radar__score-tier">{tier}</span>
    </div>
  );
}

/** 点击点后的可读详情：盲点是什么、为什么影响大、能否调查、忽略的
 * 后果，逐条说人话；原始载荷收进可折叠的 details（CLAUDE.md 2）。 */
function BlindspotInspector({
  selected,
  labels,
}: {
  selected: WorkspaceBlindspot;
  labels: Map<string, string>;
}) {
  const impact = toNumber(selected.impact);
  const uncertainty = toNumber(selected.uncertainty);
  const investigability = toNumber(selected.investigability);
  const statement = humanizeText(
    replaceClaimUuids(selected.statement ?? t("（未记录陈述）"), labels),
  );
  const consequence = consequenceForImpact(impact);
  const sourceNote =
    selected.kind === "source_diversity"
      ? t("来源单一检查：证据可能依赖同一个来源或同一批数据，需要独立来源复核。")
      : selected.kind === "bounty"
        ? t("议会提名：在盲点悬赏轮由科学家提出并评选的盲点。")
        : null;

  return (
    <div className="radar__inspector">
      <h3>{t("这个盲点是什么")}</h3>
      <p className="radar__inspector-head">
        <Badge tone={toneForStatus(selected.status)}>
          {t(STATUS_LABELS[selected.status] ?? selected.status)}
        </Badge>
        {selected.kind ? (
          <span className="radar__kind-label">
            {BLINDSPOT_KIND_LABELS[selected.kind] ?? selected.kind}
          </span>
        ) : null}
        {selected.score ? (
          <span className="radar__score-badge">
            {t("综合 {0}", percent(toNumber(selected.score) ?? 0))}
          </span>
        ) : null}
      </p>
      <p className="radar__statement">{statement}</p>
      {sourceNote ? <p className="radar__source-note">{sourceNote}</p> : null}

      <div className="radar__scores">
        <ScoreRow
          label={t("影响程度")}
          value={impact}
          tier={impact === null ? "" : impactTier(impact)}
        />
        <ScoreRow
          label={t("可调查性")}
          value={investigability}
          tier={investigability === null ? "" : investigabilityTier(investigability)}
        />
        <ScoreRow
          label={t("不确定性")}
          value={uncertainty}
          tier={uncertainty === null ? "" : uncertaintyTier(uncertainty)}
        />
      </div>

      {consequence ? (
        <p className="radar__consequence">
          <span className="radar__consequence-label">{t("轻信的后果：")}</span>
          {consequence}
        </p>
      ) : null}
      <p className="radar__unscored-note">
        {t(
          "以上解读由评分区间直接翻译；如某项未评分（来源单一检查不打分），不代表它为零。",
        )}
      </p>

      <details className="radar__raw">
        <summary>{t("原始记录（可审计）")}</summary>
        <pre className="radar__payload">{JSON.stringify(selected, null, 2)}</pre>
      </details>
    </div>
  );
}

export function BlindspotRadarView({
  blindspots,
  claims,
  graph,
}: {
  blindspots: WorkspaceBlindspot[];
  claims: ConfirmedClaim[];
  graph: EvidenceGraph;
}) {
  const [selected, setSelected] = useState<WorkspaceBlindspot | null>(null);
  const { plottable, unscored } = useMemo(() => split(blindspots), [blindspots]);
  const labels = useMemo(
    () => buildClaimLabels(claims, graph),
    [claims, graph],
  );

  if (blindspots.length === 0) {
    return (
      <Panel title={t("盲点雷达")} subtitle={t("横轴影响、纵轴可调查性，点的大小表示不确定性。")}>
        <Empty>
          {t(
            "本任务尚未产出任何盲点——这不等于「没有盲点」，而是「盲点悬赏与来源多样性检查均未触发」。",
          )}
        </Empty>
      </Panel>
    );
  }

  return (
    <Panel
      title={t("盲点雷达")}
      subtitle={t("横轴影响、纵轴可调查性，点的大小表示不确定性。● = 议会提名，◆ = 来源单一检查。")}
    >
      <div className="radar">
        <svg
          className="radar__plot"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="img"
          aria-label={t("盲点影响与可调查性散点图")}
        >
          <line
            x1={MARGIN}
            y1={SIZE - MARGIN}
            x2={SIZE - MARGIN}
            y2={SIZE - MARGIN}
            className="radar__axis"
          />
          <line
            x1={MARGIN}
            y1={MARGIN}
            x2={MARGIN}
            y2={SIZE - MARGIN}
            className="radar__axis"
          />
          <text x={SIZE / 2} y={SIZE - 10} className="radar__axis-label" textAnchor="middle">
            {t("影响 →")}
          </text>
          <text
            x={16}
            y={SIZE / 2}
            className="radar__axis-label"
            textAnchor="middle"
            transform={`rotate(-90 16 ${SIZE / 2})`}
          >
            {t("可调查性 →")}
          </text>

          {plottable.map(({ node, impact, uncertainty, investigability }) => {
            const cx = MARGIN + clamp01(impact) * PLOT;
            const cy = SIZE - MARGIN - clamp01(investigability) * PLOT;
            const r = 5 + clamp01(uncertainty) * 14;
            const tone = toneForStatus(node.status);
            return (
              <g
                key={node.id}
                className={`radar__point radar__point--${tone}${
                  selected?.id === node.id ? " radar__point--selected" : ""
                }`}
                onClick={() => setSelected(node)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setSelected(node);
                }}
              >
                <Marker kind={node.kind} cx={cx} cy={cy} r={r} />
              </g>
            );
          })}
        </svg>

        <aside className="radar__side">
          {selected === null ? (
            <Empty>{t("点击任一点查看该盲点的可读解释与原始记录。")}</Empty>
          ) : (
            <BlindspotInspector selected={selected} labels={labels} />
          )}

          {unscored.length > 0 ? (
            <div className="radar__unscored">
              <h3>{t("未评分盲点（{0}）", unscored.length)}</h3>
              <p className="radar__unscored-note">
                {t(
                  "这些盲点缺少影响 / 不确定性 / 可调查性中的至少一项——例如来源多样性检查只标注问题，不打分。展示于此而非画在原点，避免把「未测量」误显示为「已测量且为零」。",
                )}
              </p>
              <ul className="radar__unscored-list">
                {unscored.map((node) => (
                  <li
                    key={node.id}
                    className="radar__unscored-row"
                    onClick={() => setSelected(node)}
                  >
                    <Badge tone={toneForStatus(node.status)}>
                      {node.kind
                        ? (BLINDSPOT_KIND_LABELS[node.kind] ?? node.kind)
                        : t("未知类型")}
                    </Badge>
                    <span>
                      {typeof node.statement === "string"
                        ? humanizeText(
                            replaceClaimUuids(node.statement, labels),
                          )
                        : t("（未记录陈述）")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </aside>
      </div>
    </Panel>
  );
}
