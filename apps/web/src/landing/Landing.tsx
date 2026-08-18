/** 公开落地页，部署在站点根路径 `/`，不需要登录；研究证据工作台在
 * `/workspace`（未登录时由 AuthView 引导注册/登录）。
 *
 * 视觉语言仿照研究者选定的 Viktor Oddy 模板：白色底、PP Mondwest serif
 * 强调词、mono 标签行、跑马灯证据卡、滚动入场动画（IntersectionObserver
 * 触发一次）、深蓝黑分层阴影按钮。段落结构一一对应模板，但填充内容全部
 * 换成 Poliscope 的机制事实 —— 这里没有编造的统计数字（CLAUDE.md 16），
 * 「示例研究问题」明确标注为示例，机制名词以设计规格为准。
 */

import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Quote,
  Star,
} from "lucide-react";

import { LOCALE_LABELS, LOCALES, setLocale, t, useLocale } from "../i18n";

import "./Landing.css";

/** 模板的 useInViewAnimation：IntersectionObserver（threshold 0.1），
 * 元素进入视口触发一次入场动画；opacity-0 起步，命中后播放 fadeInUp。 */
function Reveal({
  delay = 0,
  children,
  className = "",
}: {
  delay?: number;
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry?.isIntersecting) {
          setInView(true);
          obs.disconnect();
        }
      },
      { threshold: 0.1 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className={`landing__reveal${inView ? " landing__reveal--on" : ""} ${className}`}
      style={delay ? { animationDelay: `${delay}s` } : undefined}
    >
      {children}
    </div>
  );
}

/** 七名科学家的席位卡（跑马灯与引文区复用）。 */
const SEATS: { id: string; name: string; note: string }[] = [
  { id: "theory_builder", name: "理论建构者", note: "机制与风险预测" },
  { id: "causal_scientist", name: "因果推断专家", note: "混杂与反向因果" },
  { id: "measurement_scientist", name: "测量与构念专家", note: "测量偏差" },
  { id: "replication_scientist", name: "统计与复现专家", note: "复现风险" },
  { id: "boundary_scientist", name: "边界与情境专家", note: "适用边界" },
  { id: "adversarial_falsifier", name: "对抗性证伪者", note: "专门找反例" },
  { id: "evidence_auditor", name: "证据与溯源审计员", note: "三层审计" },
];

/** 议会的八个阶段（设计规格 PHASE_SEQUENCE 的公开呈现）。 */
const PHASES = [
  "独立预承诺",
  "专业取证",
  "证据交换",
  "交叉质询",
  "盲点悬赏",
  "联合建模",
  "最终复判",
  "报告生成",
];

/* 1. HERO：三个错落的全小写大字（brainstorming / researching / mapping，
   对应七人独立构思、质询、绘制证据图三个环节），外加一行 mono 标签、
   一句描述与登录/注册入口。宣传语克制 —— 机制靠大字暗示，不靠段落。 */
function Hero() {
  return (
    <header className="landing__hero">
      <nav className="landing__nav">
        <a className="landing__brand" href="/workspace">
          <span className="landing__brand-mark" aria-hidden="true">
            P
          </span>
          <span className="landing__brand-name">Poliscope</span>
        </a>
        <LandingLanguageSwitcher />
        <div className="landing__nav-links">
          <a className="landing__nav-link" href="#views">
            {t("核心视图")}
          </a>
          <a
            className="landing__nav-link"
            href="https://github.com/Fishman-free/poliscope"
            rel="noreferrer"
          >
            {t("开源仓库")}
          </a>
          <a
            className="landing__nav-link"
            href="https://github.com/Fishman-free/poliscope/blob/main/docs/DEVELOPMENT.md"
            rel="noreferrer"
          >
            {t("文档")}
          </a>
        </div>
        <a className="landing__btn landing__btn--primary" href="/workspace">
          {t("进入工作台")}
        </a>
      </nav>

      {/* 三个大字走正常文档流（不是绝对定位），Z 字错落靠水平偏移：
          行与行永不重叠，也就永远不会压到下面的描述与按钮。 */}
      <div className="landing__words">
        <Reveal delay={0.1} className="landing__word landing__word--1">
          <h1>brainstorming</h1>
        </Reveal>
        <Reveal delay={0.2} className="landing__word landing__word--2">
          <h1>researching</h1>
        </Reveal>
        <Reveal delay={0.3} className="landing__word landing__word--3">
          <h1>mapping</h1>
        </Reveal>
      </div>

      <div className="landing__hero-foot">
        <Reveal delay={0.4}>
          <p className="landing__tagline">
            EPISTEMOBRAIN · {t("七人议会争议证据地图")}
          </p>
        </Reveal>
        <Reveal delay={0.5}>
          <p className="landing__hero-desc">
            {t("七名 AI 科学家独立取证、交叉质询、专门找反例，产出一张可审计的争议证据地图。")}
          </p>
        </Reveal>
        <Reveal delay={0.6}>
          <div className="landing__hero-actions">
            <a
              className="landing__btn landing__btn--primary"
              href="/workspace?mode=register"
            >
              {t("注册 · 开始研究")}
            </a>
            <a className="landing__btn landing__btn--secondary" href="/workspace">
              {t("登录")}
            </a>
          </div>
        </Reveal>
      </div>
    </header>
  );
}

/* 2. MARQUEE：7 张席位卡复制成 14 张，无限平移（30s linear），
   卡片像证据图节点 —— 语义色边框 + 席位名 + mono 职责。 */
function Marquee() {
  return (
    <section className="landing__marquee" aria-label={t("七名科学家")}>
      <div className="landing__marquee-track">
        {[...SEATS, ...SEATS].map((seat, index) => (
          <div key={`${seat.id}-${index}`} className="landing__seat-card">
            <span className="landing__seat-dot" aria-hidden="true" />
            <span className="landing__seat-name">{t(seat.name)}</span>
            <span className="landing__seat-note mono">{t(seat.note)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

/* 3. QUOTE：大引文 + 机制词文字 logo 行 + 议会八阶段流程（替换模板的
   parallax 图 —— 自部署站点不加载第三方媒体，CLAUDE.md 16）。 */
function QuoteSection() {
  return (
    <section className="landing__quote" id="protocol">
      <Reveal delay={0.1}>
        <Quote size={24} strokeWidth={1.5} color="#051A24" />
      </Reveal>
      <Reveal delay={0.2}>
        <h2 className="landing__big-quote">
          {t("不是七个聊天机器人，")}
          <br />
          {t("是一个")}
          <span className="landing__serif">{t("自我质询")}</span>
          {t("的科研共同体。")}
        </h2>
      </Reveal>
      <Reveal delay={0.3}>
        <p className="landing__quote-author">
          EpistemoBrain · {t("无投票权的组织脑，不代表任何学术立场")}
        </p>
      </Reveal>
      <Reveal delay={0.4}>
        <div className="landing__logo-row">
          <span>{t("独立预承诺")}</span>
          <span>{t("异议保真")}</span>
          <span>{t("精确溯源")}</span>
        </div>
      </Reveal>
      <Reveal delay={0.5}>
        <div className="landing__phases" aria-label={t("议会协议八阶段")}>
          {PHASES.map((phase, index) => (
            <span key={phase} className="landing__phase">
              <span className="mono">{index + 1}</span>
              {phase}
            </span>
          ))}
        </div>
      </Reveal>
    </section>
  );
}

/* 4. CAPABILITIES（模板的 pricing 位置）：深色卡「证据治理」+ 白卡
   「过程透明」，两个约束即价格 —— 约束是真实的，价格是编的。 */
function Capabilities() {
  return (
    <section className="landing__caps" aria-label={t("能力")}>
      <Reveal delay={0.1}>
        <div className="landing__cap landing__cap--dark">
          <h3 className="landing__cap-title">{t("证据治理")}</h3>
          <p className="landing__cap-desc">
            {t("双图隔离：过程轨迹永远成不了正式证据")}
            <br />
            {t("事件账本：幂等、可重放、可审计")}
          </p>
          <ul className="landing__cap-list">
            <li>
              <Check size={14} strokeWidth={2} /> {t("三层审计（来源真实性 · 引用蕴含 · 方法质量）")}
            </li>
            <li>
              <Check size={14} strokeWidth={2} /> {t("Evidence Graph 唯一写入者")}
            </li>
            <li>
              <Check size={14} strokeWidth={2} /> {t("被反驳、隔离的节点永不物理删除")}
            </li>
          </ul>
          <a className="landing__btn landing__btn--primary" href="/workspace">
            {t("进入工作台")}
          </a>
        </div>
      </Reveal>
      <Reveal delay={0.2}>
        <div className="landing__cap landing__cap--light">
          <h3 className="landing__cap-title">{t("过程透明")}</h3>
          <p className="landing__cap-desc">
            {t("思考链路实时可见")}
            <br />
            {t("模型推理、检索、文献链接全程流式呈现")}
          </p>
          <ul className="landing__cap-list">
            <li>
              <Check size={14} strokeWidth={2} /> {t("阶段推进自动跟随，不用干等结果")}
            </li>
            <li>
              <Check size={14} strokeWidth={2} /> {t("异议保真：DissentCertificate 可追溯")}
            </li>
            <li>
              <Check size={14} strokeWidth={2} /> {t("缺口计数常驻：未完成槽位显式报告")}
            </li>
          </ul>
          <a
            className="landing__btn landing__btn--secondary"
            href="https://github.com/Fishman-free/poliscope/blob/main/docs/DEVELOPMENT.md"
            rel="noreferrer"
          >
            {t("阅读开发者文档")}
          </a>
        </div>
      </Reveal>
    </section>
  );
}

/* 5. QUESTIONS（模板的 testimonial 位置）：研究者可能会问的问题 ——
   明确标注为示例，不冒充真实用户数据。 */
const QUESTIONS: { q: string; status: string; tone: string }[] = [
  {
    q: "社交媒体使用时长与青少年抑郁：是因果，还是选择偏差？",
    status: "已生成争议证据地图",
    tone: "admitted",
  },
  {
    q: "数字行为数据的测量偏差，如何影响效应估计的方向？",
    status: "盲点雷达已定位未调查盲区",
    tone: "provisional",
  },
  {
    q: "屏幕时间与心理健康的相关证据，是否存在发表偏差？",
    status: "交叉质询完成 · 待联合建模",
    tone: "unknown",
  },
];

function Questions() {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const id = window.setInterval(
      () => setIndex((i) => (i + 1) % QUESTIONS.length),
      3000,
    );
    return () => window.clearInterval(id);
  }, [paused]);

  const go = (delta: number) =>
    setIndex((i) => (i + delta + QUESTIONS.length) % QUESTIONS.length);

  return (
    <section className="landing__questions" aria-label={t("示例研究问题")}>
      <div className="landing__questions-head">
        <Reveal>
          <h2 className="landing__section-title">
            {t("研究者会问")}
            <span className="landing__serif">{t("什么")}</span>
          </h2>
        </Reveal>
        <div className="landing__questions-rating">
          {Array.from({ length: 5 }).map((_, i) => (
            <Star key={i} size={20} strokeWidth={1.5} fill="#051A24" color="#051A24" />
          ))}
          <span className="mono">{t("7 人议会 · 0 票多数决")}</span>
        </div>
      </div>
      <div
        className="landing__carousel"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        <button
          type="button"
          className="landing__carousel-btn"
          aria-label={t("上一个问题")}
          onClick={() => go(-1)}
        >
          <ChevronLeft size={20} strokeWidth={1.5} />
        </button>
        <div className="landing__carousel-viewport">
          {QUESTIONS.map((item, i) => (
            <div
              key={t(item.q)}
              className={
                "landing__question-card" +
                (i === index ? " landing__question-card--on" : "")
              }
              aria-hidden={i !== index}
            >
              <span
                className={`landing__status landing__status--${item.tone}`}
              >
                {t(item.status)}
              </span>
              <p className="landing__question-text">{t(item.q)}</p>
              <p className="landing__question-note mono">
                {t("（示例研究问题，用于演示视图）")}
              </p>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="landing__carousel-btn"
          aria-label={t("下一个问题")}
          onClick={() => go(1)}
        >
          <ChevronRight size={20} strokeWidth={1.5} />
        </button>
      </div>
    </section>
  );
}

/* 6. VIEWS（模板的 projects 位置）：三个核心视图。 */
const VIEWS = [
  {
    name: "Controversy Map",
    desc: "一张可审计的争议证据地图：节点、边与证据谱系，结论与局限并排，相关性不自动升级为因果性。",
    badge: "证据图",
  },
  {
    name: "Blindspot Radar",
    desc: "影响 × 可调查性：还没有人查过的盲区先于结论暴露 —— 这是本产品最看重的产出。",
    badge: "盲点雷达",
  },
  {
    name: "Audit Trail",
    desc: "事件账本：谁在什么时候、基于什么证据、改了什么判断；异议与拒绝记录都在。",
    badge: "审计轨迹",
  },
];

function Views() {
  return (
    <section className="landing__views" id="views" aria-label={t("核心视图")}>
      {VIEWS.map((view, i) => (
        <Reveal key={t(view.name)} delay={0.05 * i}>
          <article className="landing__view">
            <div className="landing__view-text">
              <span className="mono landing__view-badge">{t(view.badge)}</span>
              <h3 className="landing__view-name landing__serif">{t(view.name)}</h3>
              <p className="landing__view-desc">{t(view.desc)}</p>
            </div>
            <div className="landing__view-visual" aria-hidden="true">
              <div className="landing__view-grid" />
              <span className="landing__view-node landing__view-node--a" />
              <span className="landing__view-node landing__view-node--b" />
              <span className="landing__view-node landing__view-node--c" />
              <span className="landing__view-line landing__view-line--1" />
              <span className="landing__view-line landing__view-line--2" />
            </div>
          </article>
        </Reveal>
      ))}
    </section>
  );
}

/* 7. CTA（模板的 partner 位置）：大标题 + 主按钮；不做鼠标轨迹特效
   （CLAUDE.md 11：没有无意义动画）。 */
function Cta() {
  return (
    <section className="landing__cta" aria-label={t("开始研究")}>
      <Reveal>
        <h2 className="landing__cta-title">
          {t("开始你的")}
          <span className="landing__serif">{t("研究")}</span>
        </h2>
      </Reveal>
      <Reveal delay={0.15}>
        <a className="landing__btn landing__btn--primary landing__btn--lg" href="/workspace">
          {t("进入工作台")}
        </a>
      </Reveal>
      <Reveal delay={0.25}>
        <p className="landing__cta-note">
          {t("科研辅助工具，输出不是临床诊断或医疗建议")}
        </p>
      </Reveal>
    </section>
  );
}

/* 8-9. FOOTER + COPYRIGHT。 */
function Footer() {
  return (
    <>
      <footer className="landing__footer">
        <a className="landing__btn landing__btn--primary" href="/workspace">
          {t("开始研究")}
        </a>
        <ArrowUpRight size={22} strokeWidth={1.5} className="landing__footer-arrow" />
        <div className="landing__footer-cols">
          <div>
            <a href="#views">{t("核心视图")}</a>
            <a href="#protocol">{t("议会协议")}</a>
            <a href="/workspace">{t("工作台")}</a>
          </div>
          <div>
            <a href="https://github.com/Fishman-free/poliscope" target="_blank" rel="noreferrer">
              GitHub
            </a>
            <a
              href="https://github.com/Fishman-free/poliscope/blob/main/docs/DEVELOPMENT.md"
              target="_blank"
              rel="noreferrer"
            >
              License · MIT
            </a>
          </div>
        </div>
      </footer>
      <div className="landing__copyright">
        <span>Poliscope · EpistemoBrain</span>
        <span>{t("科研辅助工具 · 非临床诊断")}</span>
        <a
          className="landing__icp"
          href="https://beian.miit.gov.cn/"
          target="_blank"
          rel="noreferrer"
        >
          苏ICP备2026058422号
        </a>
      </div>
    </>
  );
}

/* 10. BOTTOM NAV：浮动 pill，P 字母 + 主按钮。 */
function BottomNav() {
  return (
    <nav className="landing__bottom-nav" aria-label={t("快捷入口")}>
      <span className="landing__bottom-mark landing__serif" aria-hidden="true">
        P
      </span>
      <a className="landing__btn landing__btn--primary" href="/workspace">
        {t("进入工作台")}
      </a>
    </nav>
  );
}

/** 落地页语言切换（round-4）：Landing 不经 App.tsx 渲染，所以在这里
 * 提供同款下拉；切换通过 useSyncExternalStore 触发全页 t() 重渲染。 */
function LandingLanguageSwitcher() {
  const current = useLocale();
  return (
    <label className="app__lang landing__lang" aria-label={t("界面语言")}>
      <select
        value={current}
        onChange={(event) => setLocale(event.target.value as (typeof LOCALES)[number])}
      >
        {LOCALES.map((localeOption) => (
          <option key={localeOption} value={localeOption}>
            {LOCALE_LABELS[localeOption]}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Landing() {
  // Subscribes the whole landing page to locale changes: every child's t()
  // call re-renders when the user switches language (the switcher alone
  // cannot re-render the tree it lives in).
  useLocale();
  return (
    <div className="landing">
      <Hero />
      <Marquee />
      <QuoteSection />
      <Capabilities />
      <Questions />
      <Views />
      <Cta />
      <Footer />
      <BottomNav />
    </div>
  );
}
