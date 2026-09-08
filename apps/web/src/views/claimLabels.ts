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

/** 句子中出现的内部标识一律换成中文标签；未知片段原样保留。
 *
 * 每个候选串先用 `includes` 试探再 `split/join`：这个函数会被账本里每条事件
 * 的每个文本字段调用（4000 行 × 约 2 字段 × 10 个候选），而绝大多数字段
 * 一个候选都不含。一次扫描远比一次分配数组的 split 便宜。 */
export function humanizeText(text: string): string {
  let result = text;
  for (const [raw, label] of Object.entries(INTERNAL_LABELS)) {
    if (!result.includes(raw)) continue;
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

/** 正文中的 UUID 匹配式。带 `g`：`String.replace` 会在进入和退出时重置
 * `lastIndex`，因此这里复用同一个常量是安全的；但绝不可改用 `.exec()` 或
 * `.test()` 逐个推进 —— 那会因为残留的 `lastIndex` 而漏匹配。 */
const CLAIM_UUID_RE =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

/** 把正文中出现的每一个 UUID 替换为对应主张的可读标签（若它指名一个
 * 已知主张），其余 UUID 保留原样 —— 识别不出就不能假装认识。
 *
 * 单次 `String.replace` 扫描输入，绝不重扫自己的输出。这一点是硬性的：
 * 旧实现用无 `g` 的正则反复 `exec`，每轮都从下标 0 重新开始，只在标签命中
 * 时才改写字符串。于是标签查不到（模型给出未登记的 claim_id、Claim 节点
 * statement 为空、任务还没有 brief）时字符串不变，下一轮匹配到同一位置 ——
 * 死循环，主线程永久锁死，页面完全不响应。若标签文本自身含 UUID，则改写
 * 后又产生新匹配，字符串无界增长。两种失败模式在这里都被结构性排除：
 * 推进不再依赖查表是否命中。
 */
export function replaceClaimUuids(
  text: string,
  labels: Map<string, string>,
): string {
  return text.replace(CLAIM_UUID_RE, (id) => {
    // 正则大小写不敏感，而 Map 的键是小写，故补一次小写回退。
    const label = labels.get(id) ?? labels.get(id.toLowerCase());
    return label ? `「${label}」` : id;
  });
}
