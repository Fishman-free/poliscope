/** 极小、零依赖、默认安全的内联 Markdown 渲染器。
 *
 * 最终论文/审查报告由模型生成，需要 **加粗**、短列表、小标题与换行来分段，
 * 但不值得为此引入完整 markdown 库（包体 + XSS 面）。这里只识别白名单语法：
 * ##/### 小标题、- / * 无序列表、1. 有序列表、**加粗**、空行分段、段内换行。
 * 任何原始 HTML 都以文本形式显示、不注入 DOM —— 这是 React 文本节点自带的
 * 保障（textContent 注入），无需也不应手动做 HTML 实体转义：那会把 `"` 变成
 * 字面 `&quot;` 显示给读者（round-19 用户反馈）。
 */
import type { ReactNode } from "react";

/** 行内：把 **加粗** 切成 <strong>，其余语法一律不识别。
 *
 * 不做 HTML 实体转义：React 以 textContent 注入文本节点，浏览器不会解析
 * 其中的标签或实体，天然免疫 XSS；若在此预先把 `"`/`&`/`<`/`>` 转成
 * `&quot;`/`&amp;`/`&lt;`/`&gt;`，这些实体会被原样显示（round-19 反馈的
 * `&quot;` 无关字符即由此而来）。 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\*\*([^*]+)\*\*/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    nodes.push(<strong key={`${keyPrefix}-b${index}`}>{match[1]}</strong>);
    last = match.index + match[0].length;
    index += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const UL = /^\s*[-*]\s+(.*)$/;
const OL = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{2,4})\s+(.*)$/;

type Block =
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; lines: string[] };

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  // 空行分段；没有空行时，连续的同类列表行归并为一个列表。
  let current: Block | null = null;
  const pushCurrent = () => {
    if (current) blocks.push(current);
    current = null;
  };
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trimEnd();
    if (!line.trim()) {
      pushCurrent();
      continue;
    }
    const ul = UL.exec(line);
    const ol = OL.exec(line);
    const heading = HEADING.exec(line);
    if (heading) {
      pushCurrent();
      current = {
        kind: "heading",
        level: heading[1]?.length ?? 3,
        text: heading[2] ?? "",
      };
      pushCurrent();
      continue;
    }
    if (ul) {
      if (!current || current.kind !== "ul") {
        pushCurrent();
        current = { kind: "ul", items: [] };
      }
      current.items.push(ul[1] ?? "");
      continue;
    }
    if (ol) {
      if (!current || current.kind !== "ol") {
        pushCurrent();
        current = { kind: "ol", items: [] };
      }
      current.items.push(ol[1] ?? "");
      continue;
    }
    if (!current || current.kind !== "paragraph") {
      pushCurrent();
      current = { kind: "paragraph", lines: [] };
    }
    current.lines.push(line);
  }
  pushCurrent();
  return blocks;
}

/** 把一段（可能含轻量 Markdown 的）文本渲染为 React 节点片段。 */
export function Markdown({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  if (!text) return null;
  const blocks = toBlocks(text);
  return (
    <span className={className}>
      {blocks.map((block, bi) => {
        if (block.kind === "heading") {
          const Tag = block.level === 2 ? "h4" : "h5";
          return (
            <Tag key={bi} className="md-heading">
              {renderInline(block.text, `h${bi}`)}
            </Tag>
          );
        }
        if (block.kind === "ul" || block.kind === "ol") {
          const ListTag = block.kind === "ul" ? "ul" : "ol";
          return (
            <ListTag key={bi} className="md-list">
              {block.items.map((item, ii) => (
                <li key={ii}>{renderInline(item, `l${bi}-${ii}`)}</li>
              ))}
            </ListTag>
          );
        }
        return (
          <p key={bi} className="md-paragraph">
            {block.lines.map((line, li) => (
              <span key={li}>
                {li > 0 ? <br /> : null}
                {renderInline(line, `p${bi}-${li}`)}
              </span>
            ))}
          </p>
        );
      })}
    </span>
  );
}
