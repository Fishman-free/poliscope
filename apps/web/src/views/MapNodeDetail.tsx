/** 证据图节点的可读详情面板。
 *
 * 取代了原来的「原始 JSON 全量展示」：按节点类型渲染人类可读字段（Source
 * 的 DOI 链接、Claim 的主张与证伪条件、StudyFinding 的原文引用等），原始
 * payload 保留在折叠的 <details> 里——CLAUDE.md 2 要求原始记录与 AI 渲染
 * 分离，两者都在，但默认看到的是可读的渲染而不是一串 JSON。
 *
 * Source 节点提供 https://doi.org/{doi} 外链（payload 只有 doi 没有 url）；
 * StudyFinding → Source 的关联通过图里的 DERIVED_FROM 边解析，点击可跳到
 * 对应 Source 节点。
 */

import type { EvidenceGraph, GraphNode } from "../api/types";
import {
  CLAIM_TYPE_LABELS,
  NODE_TYPE_LABELS,
  SEAT_LABELS,
  type Seat,
} from "../api/types";
import { Badge, STATUS_LABELS, toneForStatus } from "../components/primitives";
import { t } from "../i18n";

import "./MapNodeDetail.css";

function str(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function strList(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item : JSON.stringify(item)))
    .filter((item) => item.length > 0);
}

/** 通过 DERIVED_FROM 边找到 StudyFinding 背后的 Source 节点。 */
function sourceOfFinding(
  node: GraphNode,
  graph: EvidenceGraph,
): GraphNode | null {
  const edge = graph.edges.find(
    (item) =>
      item.source === node.id && item.edge_type === "DERIVED_FROM",
  );
  if (!edge) return null;
  return graph.nodes.find((item) => item.id === edge.target) ?? null;
}

/** 按 id 在图中找节点（用于 source_refs 之类的引用解析为可点击 chip）。 */
function referencedNodes(
  payload: Record<string, unknown>,
  key: string,
  graph: EvidenceGraph,
): GraphNode[] {
  const ids = strList(payload, key);
  const byId = new Map(graph.nodes.map((item) => [item.id, item]));
  return ids.map((id) => byId.get(id)).filter((item): item is GraphNode => Boolean(item));
}

function NodeChip({
  node,
  label,
  onSelect,
}: {
  node: GraphNode;
  label: string;
  onSelect: (node: GraphNode) => void;
}) {
  return (
    <button
      type="button"
      className="map-detail__chip"
      onClick={() => onSelect(node)}
      title={node.id}
    >
      {label}
    </button>
  );
}

function RawPayload({ payload }: { payload: Record<string, unknown> }) {
  return (
    <details className="map-detail__raw">
      <summary>{t("查看原始载荷（AI 渲染与原始记录分离）")}</summary>
      <pre className="map-detail__raw-pre">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </details>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <p className="map-detail__field">
      <span className="map-detail__label">{label}</span>
      <span className="map-detail__value">{value}</span>
    </p>
  );
}

function StudyFindingDetail({
  node,
  graph,
  onSelect,
}: {
  node: GraphNode;
  graph: EvidenceGraph;
  onSelect: (node: GraphNode) => void;
}) {
  const payload = node.payload;
  const doi = str(payload, "doi");
  const source = sourceOfFinding(node, graph);
  return (
    <>
      <Field label={t("发现")} value={str(payload, "finding_statement")} />
      {str(payload, "exact_quote") ? (
        <blockquote className="map-detail__quote">
          {str(payload, "exact_quote")}
        </blockquote>
      ) : null}
      {doi ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("文献")}</span>
          <a
            className="map-detail__link"
            href={`https://doi.org/${doi}`}
            target="_blank"
            rel="noreferrer"
          >
            {doi} ↗
          </a>
        </p>
      ) : null}
      {source ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("来源")}</span>
          <NodeChip
            node={source}
            label={str(source.payload, "title") || t("查看来源节点")}
            onSelect={onSelect}
          />
        </p>
      ) : null}
      <RawPayload payload={payload} />
    </>
  );
}

function SourceDetail({ node }: { node: GraphNode }) {
  const payload = node.payload;
  const doi = str(payload, "doi");
  const authors = strList(payload, "authors");
  return (
    <>
      <Field label={t("标题")} value={str(payload, "title")} />
      {doi ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("DOI")}</span>
          <a
            className="map-detail__link"
            href={`https://doi.org/${doi}`}
            target="_blank"
            rel="noreferrer"
          >
            {doi} ↗
          </a>
        </p>
      ) : null}
      {authors.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("作者")}</span>
          <span className="map-detail__value">{authors.join(", ")}</span>
        </p>
      ) : null}
      {payload.is_retracted === true ? (
        <p className="map-detail__field">
          <Badge tone="refuted">{t("已撤回")}</Badge>
        </p>
      ) : null}
      <Field label={t("数据集标识")} value={str(payload, "dataset_id")} />
      <RawPayload payload={payload} />
    </>
  );
}

function ClaimDetail({
  node,
  graph,
  onSelect,
}: {
  node: GraphNode;
  graph: EvidenceGraph;
  onSelect: (node: GraphNode) => void;
}) {
  const payload = node.payload;
  const claimType = str(payload, "claim_type");
  const targets = referencedNodes(
    { edges: payload.edges } as unknown as Record<string, unknown>,
    "edges",
    graph,
  );
  return (
    <>
      <Field label={t("主张")} value={str(payload, "statement")} />
      <p className="map-detail__field">
        <span className="map-detail__label">{t("类型")}</span>
        <span className="map-detail__value">
          {CLAIM_TYPE_LABELS[claimType] ?? claimType ?? t("未记录")}
        </span>
      </p>
      <Field
        label={t("证伪条件")}
        value={str(payload, "falsification_condition")}
      />
      <Field label={t("研究设计")} value={str(payload, "study_design")} />
      {targets.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("反驳的主张")}</span>
          {targets.map((target) => (
            <NodeChip
              key={target.id}
              node={target}
              label={str(target.payload, "statement") || t("查看主张节点")}
              onSelect={onSelect}
            />
          ))}
        </p>
      ) : null}
      <RawPayload payload={payload} />
    </>
  );
}

function DebateCapsuleDetail({
  node,
  graph,
  onSelect,
}: {
  node: GraphNode;
  graph: EvidenceGraph;
  onSelect: (node: GraphNode) => void;
}) {
  const payload = node.payload;
  const commonGround = strList(payload, "common_ground");
  const hinge = strList(payload, "hinge_variables");
  const boundary = strList(payload, "boundary_conditions");
  const conflicts = strList(payload, "unresolved_conflicts");
  const falsifiable = strList(payload, "falsification_conditions");
  const refs = referencedNodes(payload, "source_refs", graph);
  return (
    <>
      {commonGround.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("共同基础")}</span>
          <span className="map-detail__value">{commonGround.join("；")}</span>
        </p>
      ) : null}
      {hinge.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("枢纽变量")}</span>
          <span className="map-detail__value">{hinge.join("；")}</span>
        </p>
      ) : null}
      {boundary.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("边界条件")}</span>
          <span className="map-detail__value">{boundary.join("；")}</span>
        </p>
      ) : null}
      {conflicts.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("未解决冲突")}</span>
          <span className="map-detail__value">{conflicts.join("；")}</span>
        </p>
      ) : null}
      {falsifiable.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("可证伪条件")}</span>
          <span className="map-detail__value">{falsifiable.join("；")}</span>
        </p>
      ) : null}
      {refs.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("来源")}</span>
          {refs.map((ref) => (
            <NodeChip
              key={ref.id}
              node={ref}
              label={str(ref.payload, "title") || t("查看来源节点")}
              onSelect={onSelect}
            />
          ))}
        </p>
      ) : null}
      <RawPayload payload={payload} />
    </>
  );
}

function DissentDetail({
  node,
  graph,
  onSelect,
}: {
  node: GraphNode;
  graph: EvidenceGraph;
  onSelect: (node: GraphNode) => void;
}) {
  const payload = node.payload;
  const author = str(payload, "author");
  const targets = referencedNodes(payload, "target_id", graph);
  return (
    <>
      <Field
        label={t("异议人")}
        value={SEAT_LABELS[author as Seat] ?? author}
      />
      <Field label={t("异议内容")} value={str(payload, "statement")} />
      <Field label={t("理由")} value={str(payload, "reason")} />
      <Field
        label={t("撤回条件")}
        value={str(payload, "withdrawal_condition")}
      />
      {targets.length > 0 ? (
        <p className="map-detail__field">
          <span className="map-detail__label">{t("针对的主张")}</span>
          {targets.map((target) => (
            <NodeChip
              key={target.id}
              node={target}
              label={str(target.payload, "statement") || t("查看主张节点")}
              onSelect={onSelect}
            />
          ))}
        </p>
      ) : null}
      <RawPayload payload={payload} />
    </>
  );
}

function BlindspotDetail({ node }: { node: GraphNode }) {
  const payload = node.payload;
  return (
    <>
      <Field label={t("盲点")} value={str(payload, "statement")} />
      <Field label={t("类型")} value={str(payload, "kind")} />
      <Field label={t("影响")} value={str(payload, "impact")} />
      <Field label={t("不确定性")} value={str(payload, "uncertainty")} />
      <RawPayload payload={payload} />
    </>
  );
}

function DefaultDetail({ node }: { node: GraphNode }) {
  const payload = node.payload;
  return (
    <>
      <Field label={t("内容")} value={str(payload, "statement") || str(payload, "question") || str(payload, "summary")} />
      <RawPayload payload={payload} />
    </>
  );
}

export function MapNodeDetail({
  node,
  graph,
  onSelect,
}: {
  node: GraphNode;
  graph: EvidenceGraph;
  onSelect: (node: GraphNode) => void;
}) {
  return (
    <div className="map-detail">
      <p className="map-detail__type">
        {NODE_TYPE_LABELS[node.node_type] ?? node.node_type}
        <Badge tone={toneForStatus(node.status)}>
          {STATUS_LABELS[node.status] ?? node.status}
        </Badge>
      </p>

      {node.node_type === "Source" ? (
        <SourceDetail node={node} />
      ) : node.node_type === "StudyFinding" ? (
        <StudyFindingDetail node={node} graph={graph} onSelect={onSelect} />
      ) : node.node_type === "Claim" ? (
        <ClaimDetail node={node} graph={graph} onSelect={onSelect} />
      ) : node.node_type === "DebateCapsule" ? (
        <DebateCapsuleDetail node={node} graph={graph} onSelect={onSelect} />
      ) : node.node_type === "DissentCertificate" ? (
        <DissentDetail node={node} graph={graph} onSelect={onSelect} />
      ) : node.node_type === "Blindspot" ? (
        <BlindspotDetail node={node} />
      ) : node.node_type === "ResearchQuestion" ||
        node.node_type === "DiscriminatingStudy" ||
        node.node_type === "Construct" ||
        node.node_type === "Context" ? (
        <DefaultDetail node={node} />
      ) : (
        <RawPayload payload={node.payload} />
      )}
    </div>
  );
}
