/** Custom React Flow nodes for the Controversy Map.
 *
 * EvidenceNode: a formal evidence-graph node rendered as an instrument card --
 * status carried by a left bar AND a type chip colour (never colour alone).
 * StickyNode: the researcher's own free-floating note, visually distinct
 * (paper fill, dashed ring) so it can never be mistaken for formal evidence
 * (AGENTS.md 6: distinguish source text, author interpretation and AI
 * derivation -- the researcher's own overlay is a fourth, clearly labelled
 * layer). Text editing uses React Flow's ``nodrag`` class so typing never
 * pans the canvas.
 */

import { memo } from "react";
import { type NodeProps, type Node } from "@xyflow/react";

import { t } from "../../i18n";
import type { NodeTone } from "./exportGraph";

export interface EvidenceNodeData {
  label: string;
  typeLabel: string;
  tone: NodeTone;
  hasNote: boolean;
  [key: string]: unknown;
}

export interface StickyNodeData {
  text: string;
  color: "amber" | "blue" | "green";
  onChange: (id: string, text: string) => void;
  onDelete: (id: string) => void;
  [key: string]: unknown;
}

export type MapFlowNode = Node<EvidenceNodeData | StickyNodeData>;

function EvidenceNode({
  data,
  selected,
}: NodeProps<Node<EvidenceNodeData>>) {
  return (
    <div
      className={
        "evidence-node evidence-node--" +
        data.tone +
        (selected ? " evidence-node--selected" : "") +
        (data.hasNote ? " evidence-node--noted" : "")
      }
    >
      <span className="evidence-node__chip">{data.typeLabel}</span>
      <span className="evidence-node__label">{data.label}</span>
      {data.hasNote ? (
        <span
          className="evidence-node__note-dot"
          title={t("该节点有你的私人备注")}
          aria-label={t("该节点有你的私人备注")}
        >
          ✎
        </span>
      ) : null}
    </div>
  );
}

function StickyNode({
  id,
  data,
  selected,
}: NodeProps<Node<StickyNodeData>>) {
  return (
    <div
      className={
        "sticky-node sticky-node--" +
        data.color +
        (selected ? " sticky-node--selected" : "")
      }
    >
      <div className="sticky-node__bar">
        <span>{t("研究者便签")}</span>
        <button
          type="button"
          className="sticky-node__delete nodrag"
          onClick={() => data.onDelete(id)}
          title={t("删除这张便签（仅影响你的私人视图）")}
          aria-label={t("删除便签")}
        >
          ×
        </button>
      </div>
      <textarea
        className="sticky-node__text nodrag"
        value={data.text}
        placeholder={t("双击式输入：记录你的判断、疑点或待查线索…")}
        onChange={(event) => data.onChange(id, event.target.value)}
        rows={4}
      />
    </div>
  );
}

export const nodeTypes = {
  evidence: memo(EvidenceNode) as never,
  sticky: memo(StickyNode) as never,
};
