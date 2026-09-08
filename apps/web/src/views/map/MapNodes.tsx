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
 *
 * Both node kinds expose small connection handles on their left/right edges
 * so the researcher can draw arrows between any two blocks the same way a
 * mind-map tool does. The arrows are personal overlay connectors, never
 * formal evidence edges.
 */

import { memo, useEffect, useRef, useState } from "react";
import { Handle, type NodeProps, type Node, Position } from "@xyflow/react";

import { t } from "../../i18n";
import {
  STICKY_COLORS,
  STICKY_FONTS,
  STICKY_TEXT_COLORS,
  type StickyColor,
  type StickyFont,
  type StickyTextColor,
} from "./overlayStore";
import type { NodeTone } from "./exportGraph";

export interface EvidenceNodeData {
  label: string;
  typeLabel: string;
  tone: NodeTone;
  hasNote: boolean;
  [key: string]: unknown;
}

export interface StickyStylePatch {
  color?: StickyColor;
  textColor?: StickyTextColor;
  font?: StickyFont;
}

export interface StickyNodeData {
  text: string;
  color: StickyColor;
  textColor: StickyTextColor;
  font: StickyFont;
  onChange: (id: string, text: string) => void;
  onStyle: (id: string, patch: StickyStylePatch) => void;
  onDelete: (id: string) => void;
  [key: string]: unknown;
}

export type MapFlowNode = Node<EvidenceNodeData | StickyNodeData>;

/** Connection handles shared by every block. A target on the left, a source
 * on the right; hovering a block reveals them so the canvas stays clean. */
function ConnectHandles() {
  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        className="map-connect-handle"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="map-connect-handle"
      />
    </>
  );
}

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
      <ConnectHandles />
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

const TEXT_COLOR_LABEL: Record<StickyTextColor, string> = {
  ink: "深灰字",
  red: "红字",
  blue: "蓝字",
  green: "绿字",
};

const FONT_LABEL: Record<StickyFont, string> = {
  sans: "黑体",
  serif: "宋体",
  mono: "等宽",
};

function StickyNode({
  id,
  data,
  selected,
}: NodeProps<Node<StickyNodeData>>) {
  // A textarea always swallows the pointer, so a note whose body is a live
  // textarea can only be dragged by some other strip -- which is exactly why
  // this note was reported as immovable. Instead the body is a plain div (part
  // of the drag surface) until the researcher double-clicks to edit, the same
  // gesture every whiteboard tool uses. Editing ends on blur or Escape.
  const [editing, setEditing] = useState(false);
  const textRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!editing) return;
    const area = textRef.current;
    if (!area) return;
    area.focus();
    // Caret to the end, so double-click continues the note instead of
    // overwriting it.
    area.setSelectionRange(area.value.length, area.value.length);
  }, [editing]);

  return (
    <div
      className={
        "sticky-node sticky-node--" +
        data.color +
        " sticky-node--font-" +
        data.font +
        (selected ? " sticky-node--selected" : "") +
        (editing ? " sticky-node--editing" : "")
      }
    >
      <ConnectHandles />
      <div className="sticky-node__bar nodrag">
        {/* Keeps the note labelled as the researcher's own overlay layer, never
            formal evidence (AGENTS.md 6). */}
        <span className="sticky-node__title">{t("便签")}</span>
        <label className="sticky-node__control" aria-label={t("便签底色")}>
          <span className="sticky-node__swatch" aria-hidden="true">
            {STICKY_COLORS.map((color) => (
              <button
                key={color}
                type="button"
                className={
                  "sticky-node__color sticky-node__color--" +
                  color +
                  (data.color === color ? " sticky-node__color--on" : "")
                }
                title={color}
                onClick={(event) => {
                  event.stopPropagation();
                  data.onStyle(id, { color });
                }}
              />
            ))}
          </span>
        </label>
        <select
          className="sticky-node__select nodrag nowheel"
          value={data.textColor}
          aria-label={t("文字颜色")}
          onChange={(event) =>
            data.onStyle(id, {
              textColor: event.target.value as StickyTextColor,
            })
          }
          onClick={(event) => event.stopPropagation()}
        >
          {STICKY_TEXT_COLORS.map((color) => (
            <option key={color} value={color}>
              {t(TEXT_COLOR_LABEL[color])}
            </option>
          ))}
        </select>
        <select
          className="sticky-node__select nodrag nowheel"
          value={data.font}
          aria-label={t("字体")}
          onChange={(event) =>
            data.onStyle(id, { font: event.target.value as StickyFont })
          }
          onClick={(event) => event.stopPropagation()}
        >
          {STICKY_FONTS.map((font) => (
            <option key={font} value={font}>
              {t(FONT_LABEL[font])}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="sticky-node__delete"
          onClick={(event) => {
            event.stopPropagation();
            data.onDelete(id);
          }}
          title={t("删除这张便签（仅影响你的私人视图）")}
          aria-label={t("删除便签")}
        >
          ×
        </button>
      </div>
      {editing ? (
        <textarea
          ref={textRef}
          className={
            "sticky-node__text nodrag sticky-node__text--" + data.textColor
          }
          value={data.text}
          placeholder={t("双击式输入：记录你的判断、疑点或待查线索…")}
          onChange={(event) => data.onChange(id, event.target.value)}
          onBlur={() => setEditing(false)}
          onKeyDown={(event) => {
            // Escape leaves editing without bubbling to the canvas (which
            // would otherwise clear the selection).
            if (event.key === "Escape") {
              event.stopPropagation();
              setEditing(false);
            }
          }}
          rows={4}
        />
      ) : (
        <div
          className={
            "sticky-node__text sticky-node__text--view sticky-node__text--" +
            data.textColor +
            (data.text ? "" : " sticky-node__text--empty")
          }
          onDoubleClick={() => setEditing(true)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              setEditing(true);
            }
          }}
          role="button"
          tabIndex={0}
          aria-label={t("双击编辑便签")}
          title={t("双击编辑便签")}
        >
          {data.text || t("双击式输入：记录你的判断、疑点或待查线索…")}
        </div>
      )}
    </div>
  );
}

export const nodeTypes = {
  evidence: memo(EvidenceNode) as never,
  sticky: memo(StickyNode) as never,
};
