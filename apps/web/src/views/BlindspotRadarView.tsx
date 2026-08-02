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
 */

import { useMemo, useState } from "react";

import type { WorkspaceBlindspot } from "../api/types";
import { BLINDSPOT_KIND_LABELS } from "../api/types";
import {
  Badge,
  Empty,
  Panel,
  STATUS_LABELS,
  toneForStatus,
} from "../components/primitives";

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

export function BlindspotRadarView({
  blindspots,
}: {
  blindspots: WorkspaceBlindspot[];
}) {
  const [selected, setSelected] = useState<WorkspaceBlindspot | null>(null);
  const { plottable, unscored } = useMemo(() => split(blindspots), [blindspots]);

  if (blindspots.length === 0) {
    return (
      <Panel title="盲点雷达" subtitle="横轴影响、纵轴可调查性，点的大小表示不确定性。">
        <Empty>
          本任务尚未产出任何盲点——这不等于「没有盲点」，而是「盲点悬赏与来源多样性检查均未触发」。
        </Empty>
      </Panel>
    );
  }

  return (
    <Panel
      title="盲点雷达"
      subtitle="横轴影响、纵轴可调查性，点的大小表示不确定性。● = 议会提名，◆ = 来源单一检查。"
    >
      <div className="radar">
        <svg
          className="radar__plot"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="img"
          aria-label="盲点影响与可调查性散点图"
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
            影响 →
          </text>
          <text
            x={16}
            y={SIZE / 2}
            className="radar__axis-label"
            textAnchor="middle"
            transform={`rotate(-90 16 ${SIZE / 2})`}
          >
            可调查性 →
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
          <div className="radar__inspector">
            <h3>详情</h3>
            {selected === null ? (
              <Empty>点击任一点查看该盲点的完整载荷。</Empty>
            ) : (
              <>
                <p className="radar__inspector-head">
                  <Badge tone={toneForStatus(selected.status)}>
                    {STATUS_LABELS[selected.status] ?? selected.status}
                  </Badge>
                  {selected.kind ? (
                    <span className="radar__kind-label">
                      {BLINDSPOT_KIND_LABELS[selected.kind] ?? selected.kind}
                    </span>
                  ) : null}
                </p>
                <p className="radar__statement">
                  {selected.statement ?? "（未记录陈述）"}
                </p>
                {/* Raw payload, unformatted on purpose: CLAUDE.md 2 separates
                    the original record from any AI rendering of it. */}
                <pre className="radar__payload">
                  {JSON.stringify(selected, null, 2)}
                </pre>
              </>
            )}
          </div>

          {unscored.length > 0 ? (
            <div className="radar__unscored">
              <h3>未评分盲点（{unscored.length}）</h3>
              <p className="radar__unscored-note">
                这些盲点缺少影响 / 不确定性 / 可调查性中的至少一项——例如来源多样性检查只标注问题，不打分。
                展示于此而非画在原点，避免把「未测量」误显示为「已测量且为零」。
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
                        : "未知类型"}
                    </Badge>
                    <span>
                      {typeof node.statement === "string"
                        ? node.statement
                        : "（未记录陈述）"}
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
