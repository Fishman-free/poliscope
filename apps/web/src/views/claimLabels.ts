/** 主张 ID → 可读标签的共享解析（Evolution View 与盲点雷达共用）。
 *
 * 界面里任何地方出现 UUID（账本事件、盲点陈述、质询正文）都必须换成
 * 主张说了什么 —— 裸 UUID 对研究者毫无意义（round-5/6 用户反馈）。标签
 * 解析顺序：已确认主张（brief.confirmed_claims）→ 图中 Claim 节点（分叉
 * 与被质询主张只存在于图里）→ 兜底「主张（未命名）」——绝不臆造。
 */

import type { ConfirmedClaim, EvidenceGraph } from "../api/types";
import { t } from "../i18n";

/** 账本/过程流里可能出现的内部标识串，原样展示等于给研究者看乱码；
 * 未知字符串保持原样 —— 标签表永远不许猜。 */
export const INTERNAL_LABELS: Record<string, string> = {
  "ACQUISITION:no_tool_provider": "未配置工具网关，无法获取证据",
  "JOINT_MODELING:no_capsule_fold": "联合建模未形成可折叠的争论胶囊",
  "JOINT_MODELING:missing_fields": "联合建模输出缺少必需字段",
  "FINAL_REJUDGMENT:no_dissent_target": "最终复判未指向异议目标",
  resurrection_condition_not_met: "复活条件未满足",
  "no model provider is connected to the Model Gateway": "模型网关未连接",
  "acquisition timed out": "证据获取超时",
  "source budget exhausted": "来源预算已耗尽",
  "source is retracted": "来源已撤回",
};

/** 句子中出现的内部标识一律换成中文标签；未知片段原样保留。 */
export function humanizeText(text: string): string {
  let result = text;
  for (const [raw, label] of Object.entries(INTERNAL_LABELS)) {
    result = result.split(raw).join(label);
  }
  return result;
}

/** 构建 claim_id → 「主张：{statement}」映射，先已确认主张，后图内 Claim
 * 节点，命中即止（同一主张优先采用已确认版本的措辞）。 */
export function buildClaimLabels(
  claims: ConfirmedClaim[],
  graph: EvidenceGraph,
): Map<string, string> {
  const labels = new Map<string, string>();
  for (const claim of claims) {
    labels.set(claim.claim_id, `主张：${claim.statement}`);
  }
  for (const node of graph.nodes) {
    if (node.node_type !== "Claim") continue;
    if (labels.has(node.id)) continue;
    const statement = node.payload.statement;
    if (typeof statement === "string" && statement.trim()) {
      labels.set(node.id, `主张：${statement}`);
    }
  }
  return labels;
}

export const CLAIM_LABEL_MAX_CHARS = 60;

export function claimLabel(claimId: string, labels: Map<string, string>): string {
  const label = labels.get(claimId);
  if (label) {
    return label.length > CLAIM_LABEL_MAX_CHARS
      ? `${label.slice(0, CLAIM_LABEL_MAX_CHARS - 2)}…`
      : label;
  }
  return t("主张（未命名）");
}

/** 把正文中出现的每一个 UUID 替换为对应主张的可读标签（若它指名一个
 * 已知主张），其余 UUID 保留原样 —— 识别不出就不能假装认识。 */
export function replaceClaimUuids(
  text: string,
  labels: Map<string, string>,
): string {
  const uuid =
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i;
  let result = text;
  let match = uuid.exec(result);
  while (match !== null) {
    const label = labels.get(match[0]);
    if (label) {
      result = `${result.slice(0, match.index)}「${label}」${result.slice(
        match.index + match[0].length,
      )}`;
    }
    match = uuid.exec(result);
  }
  return result;
}
