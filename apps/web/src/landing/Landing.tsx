/** 公开落地页，部署在站点根路径 `/`，不设访问口令；研究证据工作台在
 * `/workspace`（由 Caddy 在 /workspace* 上保留共享口令，见 deploy/caddy）。
 *
 * 视觉语言是黑白色调的科研海报：浮动胶囊导航、三个错落的全小写大词
 * （researching / questioning / mapping 对应七人独立取证、交叉质询、
 * 绘制证据图三个环节）、一张不依赖任何第三方媒体的背景占位符。
 *
 * 这里没有编造的统计数字（模板里的 +65k/+1.5b/+300k 是虚构广告数据，
 * CLAUDE.md 16 禁止系统呈现未知内容），三个角位放的是机制事实：
 * 独立预承诺、异议保真、精确溯源——它们才是这个产品真正拿得出手的"数字"。
 */

import "./Landing.css";

function Logo() {
  /* 三条证据线从不同方向汇聚到中心圆点：七名科学家独立取证后，
   * 汇入同一张可审计的证据图。 */
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="16" cy="16" r="2.5" fill="#ffffff" />
      <path
        d="M3 6 L14 13.5"
        stroke="#ffffff"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M29 6 L18 13.5"
        stroke="#ffffff"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M16 1.5 L16 13"
        stroke="#ffffff"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** 角位机制标签——模板里是统计数字块的位置，这里放真实的产品机制。 */
function Fact({
  name,
  note,
  side,
}: {
  name: string;
  note: string;
  side: "tr" | "bl" | "br";
}) {
  const dividerClass =
    side === "bl" ? "hero__fact-divider--back" : "hero__fact-divider--fwd";
  return (
    <div className={`hero__fact hero__fact--${side}`}>
      <div className="hero__fact-row">
        <span className={`hero__fact-divider ${dividerClass}`} aria-hidden="true" />
        <span className="hero__fact-name">{name}</span>
      </div>
      <span className="hero__fact-note">{note}</span>
    </div>
  );
}

export function Landing() {
  return (
    <section className="hero">
      {/* 背景视频占位符：纯 CSS 的"暗房 + 极细网格刻度"，不加载外部
          CDN 视频（自部署站点不应依赖第三方媒体），也不做循环动画
          （CLAUDE.md 11：没有无意义动画）。 */}
      <div className="hero__backdrop" aria-hidden="true" />

      <nav className="hero__nav">
        <a className="hero__brand" href="/workspace">
          <Logo />
          <span className="hero__brand-name">Poliscope</span>
        </a>

        <div className="hero__links">
          <a className="hero__link" href="/workspace">
            工作台
          </a>
          <a
            className="hero__link"
            href="https://github.com/Fishman-free/poliscope"
            rel="noreferrer"
          >
            开源仓库
          </a>
          <a
            className="hero__link"
            href="https://github.com/Fishman-free/poliscope/blob/main/docs/DEVELOPMENT.md"
            rel="noreferrer"
          >
            文档
          </a>
        </div>

        <a className="hero__cta" href="/workspace">
          进入工作台
        </a>
      </nav>

      <h1 className="hero-title hero__word hero__word--1">researching</h1>
      <h1 className="hero-title hero__word hero__word--2">questioning</h1>
      <h1 className="hero-title hero__word hero__word--3">mapping</h1>

      <p className="hero__desc">
        七名 AI 科学家独立取证、交叉质询、专门找反例，产出一张可审计的争议证据地图。
      </p>

      <Fact
        side="tr"
        name="独立预承诺"
        note="七人先各自下判断，再交换证据——质询是真的质询"
      />
      <Fact
        side="bl"
        name="异议保真"
        note="被反驳的观点不删除，仍可追溯，不允许假共识"
      />
      <Fact
        side="br"
        name="精确溯源"
        note="关键判断绑定原文位置，三层审计后才能进证据图"
      />

      <div className="hero__fade" aria-hidden="true" />
    </section>
  );
}
