# Poliscope 开发者文档

> 本文档面向想要理解设计思路、自建部署或参与开发的人。想直接使用网页版的用户，请看根目录 [`README.md`](../README.md)。

设计规格：[`docs/superpowers/specs/2026-07-31-poliscope-design.md`](superpowers/specs/2026-07-31-poliscope-design.md)。行为约束：[`CLAUDE.md`](../CLAUDE.md)。

---

## 目录

- [为什么会有 Poliscope](#为什么会有-poliscope)
- [核心能力一览](#核心能力一览)
- [适合谁 · 不适合做什么](#适合谁-不适合做什么)
- [设计思路：从 MemoBrain 到 EpistemoBrain](#设计思路从-memobrain-到-epistemobrain)
- [先读这一节：当前真实状态](#先读这一节当前真实状态)
- [设计上的五个硬约束](#设计上的五个硬约束)
- [本机开发环境](#本机开发环境)
- [自建部署：Docker Compose + Caddy](#自建部署docker-compose--caddy)
- [完整工作流](#完整工作流)
- [系统架构](#系统架构)
- [七名科学家与议会协议](#七名科学家与议会协议)
- [证据治理](#证据治理)
- [目录结构](#目录结构)
- [Agent Skill 集成细节](#agent-skill-集成细节)
- [开发与测试](#开发与测试)
- [已知缺口](#已知缺口)
- [安全与伦理](#安全与伦理)
- [许可证与归属](#许可证与归属)

---

## 为什么会有 Poliscope

这三个场景，做计算社会科学研究的人大概都遇到过：

- 你让一个通用 Agent 帮你梳理「远程办公是否降低团队创新产出」，它十分钟内给出一段读起来很顺的结论——但你没法知道这个结论建立在几篇独立观察之上，也不知道那几篇论文是不是同一批团队用同一份调查数据反复发表的「一份证据的六个马甲」。
- 一篇论文说「社交媒体使用时长与青少年抑郁显著相关」，经过几轮转述之后变成了「社交媒体导致抑郁」——横截面数据从来支撑不了这个因果表述，但从摘要到新闻标题，没有一个环节做减法，只有环节在加码。
- 一个多智能体辩论系统让几个角色吵了一轮，主持人模型最后说「综合来看，大家基本认同……」——那个不同意的角色具体在反对什么、还差什么证据才会改变立场，随着这句总结一起消失了。

Poliscope 把这三件事当成要用架构解决的问题，而不是补一行免责声明就算交代过去：

- **七个角色不是用来把答案讲得更全，是用来从七个方向主动攻击同一个结论。** 因果推断专家的工作就是找混杂变量和反向因果，测量专家专门挑构念操作化的裂缝，对抗性证伪者的唯一职责是攻击这个结论看起来最强的版本——不是「一个模型戴七顶帽子」，是七套独立角色规格、独立私有记忆、独立证据排序权重，详见[核心能力一览](#核心能力一览)。
- **论文篇数从不被当成证据强度的替身。** 六篇论文如果共享同一个数据集，系统只算一份独立证据，界面同时展示论文篇数与独立证据簇数量——这两个数字第一次被分开摆出来，你才看得出一个「共识」到底是七次独立观察，还是一次观察被转述了七遍。
- **相关性想升级成因果性，必须先过一道拦截。** `CausalUpgradePolicy` 在证据门第六阶段专门拦「横截面设计 + 因果主张」这个组合，拦下来的事件会被隔离并留痕，不会悄悄溜进最终结论——见[证据治理](#证据治理)。
- **分歧留在案发现场，不会被一句「大家基本同意」抹掉。** 被反驳、被隔离的观点永远可以点回原文，持异议的科学家会拿到一份具名的 `DissentCertificate`，不会有主持人模型替全体代签一份共识。
- **系统会主动承认「我不知道」，而不是安静地把空白填成看起来完整的样子。** 缺席的席位、没跑完的轮次、被拒绝的来源全部是账本里的一等事件，并出现在报告的局限一节里——见[先读这一节：当前真实状态](#先读这一节当前真实状态)。

如果你要的是一段马上能用、读起来通顺的摘要，Poliscope 不是最快的选择——七人议会加六阶段证据审核，天然比单智能体摘要慢，这是刻意的取舍，不是还没优化的性能。但如果你要的是「这个结论到底建立在多强的证据上、我还应该怀疑什么」，这正是 Poliscope 唯一在做的事。**它卖的不是答案，是一份你可以拿着去反驳自己结论的审计记录。**

---

## 核心能力一览

- **7 名常驻科学家，而非 1 个模型戴 7 顶帽子。** 理论建构、因果推断、测量与构念、统计复现、边界与情境、对抗性证伪、证据与溯源审计——七个独立角色规格、独立私有记忆、独立证据投影权重，每个任务全程参与，不做「动态选组省成本」（取舍过程见[设计思路](#设计思路从-memobrain-到-epistemobrain)）。
- **双图治理：过程记忆与正式证据物理分离。** 科学家的推理步骤走 Process Graph；只有通过[六阶段证据门](#证据治理)审核的内容才能被 Graph Projector 写入 Evidence Graph——这一隔离由数据库权限强制，不是代码自律。
- **证据分层 A–D，绝不用摘要撑因果结论。** 只有全文与精确原文可得才算 Level A；仅有摘要、二手描述或网页线索的证据会被诚实标注为「仅元数据」「仅讨论」「仅线索」，不会被包装成同等强度的支持证据。
- **论文数量 ≠ 独立证据数量。** 证据谱系自动识别共享数据集与重叠作者，界面同时显示论文篇数与独立证据簇数——六篇论文如果共享一个数据集，只算一份证据。
- **异议永久保留，不靠多数票压下去。** 联合建模禁止用投票裁决科研真理；被反驳、隔离或折叠的观点仍可追溯，持异议的科学家会拿到一份具名的 `DissentCertificate`。
- **盲点驱动下一步调查，不是主持人按台本点名。** 证据图发现的缺口会生成「盲点悬赏」，按影响、不确定性、可调查性打分排出优先级，再由七人认领——Blindspot 是产品里的一等公民对象，不是报告末尾一段「局限性」文字。
- **每个关键判断都能点回原文。** Source → Study → Section → 原文定位 → 抽取 Agent → 核验状态，六阶段证据门逐条审核来源真实性、引用蕴含与方法质量。
- **四个入口，一套后端契约。** CLI、HTTP API、Web 证据工作台（Research Brief / Controversy Map / Audit Trail / Council / Blindspot Radar / Evolution View）与 Claude Code / Codex Agent Skill，全部走同一条 CLI/API 契约，没有第二条能绕过证据门的路径。

## 适合谁 · 不适合做什么

**适合：** 需要审查一个存在争议的计算社会科学实证问题（首版聚焦数字行为、社交媒体与心理健康），并且在意「结论建立在多强的证据上、哪些地方还有真正的分歧」，而不只是想要一段读起来通顺的文献综述。

**请不要用于：** 临床诊断或医疗建议（见[安全与伦理](#安全与伦理)）；需要通用全学科覆盖、自动实验设备或自动元分析的场景（首版范围边界见 `CLAUDE.md` 第 14 条）；期待「秒出结论」的场景——七人议会加六阶段证据审核本身就比单智能体摘要慢，这是刻意的权衡，不是尚未优化的性能问题。

---

## 设计思路：从 MemoBrain 到 EpistemoBrain

这一节说的是「为什么这样设计」，不是「跑到哪一步了」——运行状态请看下一节和文末的[已知缺口](#已知缺口)。

### 起点：一个单智能体的记忆压缩器，管不了一个科学共同体

Poliscope 的执行记忆基座是 [MemoBrain](https://github.com/qhjqhj00/MemoBrain)。它的原始设定是给**一个** Agent 用的：这个 Agent 自己检索、自己推理、自己在 token 预算见底之前把已经走过的路压缩掉，留下还有用的部分。`Flush` 隔离走不通的分支，`Fold` 压缩已完成的子任务，`Recall` 在预算内重建工作上下文——三个动作服务同一个目的：让一个大脑在有限记忆里跑得更远。

我们最早的直觉是「在 MemoBrain 外面套七个 Agent」——每个科学家一份 MemoBrain 实例，问题不就解决了？没有。原因不是工程量，是这三个动作压根没有回答一个更根本的问题：**当七个独立认知主体需要共享、交换、审计彼此的证据时，谁的记忆图是准的？**

如果直接把某个科学家的 MemoBrain ReasoningGraph 当成最终的证据地图，会依次出这些问题：工具调用步骤和学术证据混在一张图里，分不清谁是证据谁是过程；一个科学家的中间猜测被用户误当成事实；`Fold` 把还没解决的争议一起压缩掉；一篇论文里的三个不同发现，没法在图上分别挂到三个不同结论上；界面最终没法讲清楚「这个结论现在有多大争议」。这些不是可以靠 Prompt 调好的小毛病，是「过程记忆」和「科研证据」这两个概念本来就不是一回事——前者回答「这个科学家是怎么做到的」，后者回答「我们现在对这个问题知道什么」。

所以第一个决定是**双图分离**：MemoBrain 继续做它最擅长的事，管理 Process Graph；证据的正式状态另外建一张 Evidence Graph，只认原子主张、研究发现、构念、情境和盲点这些学术对象。两张图之间不是各管各的，而是靠一本可审计的 Scientific Event Ledger 联动——过程产生候选证据，证据暴露认知缺口，缺口再触发新一轮调查（详见[证据治理](#证据治理)）。

### 一个更麻烦的问题：七个科学家不会自动带来七倍可靠性

七人议会解决的是「视角单一」——一个 Agent 读论文，容易只看到自己那一路的解释。但视角多样性和证据独立性是两件不同的事，混为一谈会埋下更隐蔽的风险：如果七名科学家读到的是同一份错误摘要、同一篇被撤稿的论文、同一条张冠李戴的引用，系统反而可能把**共享的证据错误**包装成看起来众口一词的「共识」——七个人同时说错，比一个人说错更有迷惑性，因为它看起来像是被反复验证过的。

这也是为什么「论文数量不等于独立证据数量」被写进了[设计上的五个硬约束](#设计上的五个硬约束)，而不只是一句提醒：六篇论文如果共享同一个数据集、同一个研究团队，本质上是一份证据被讲了六遍。证据谱系（谁复用了谁的数据、谁是谁的预印本、谁是谁的扩展研究）决定的是**独立证据簇**的数量，这个数字才是真正该被信任的分母——而不是论文篇数。

### 记忆操作不能直接照搬，得先问「这个动作在科研场景里意味着什么」

MemoBrain 的三个原生动作，在证据层面必须被重新定义，否则每一个都会制造事故：

- **`Flush` → `Quarantine`（隔离，而不是清空）。** 一条被反驳的主张不会消失，只会换状态：`PROPOSED → SUPPORTED → CONTESTED → QUARANTINED → RESURRECTED / REJECTED`。隔离记录必须写清楚是谁反驳的、缺什么证据、满足什么条件可以复活。普通 `Flush` 是为「确认没用了」设计的；一条科研主张在被证伪之前，谁也不能替它下这个判断。
- **`Fold` → `Dialectical Fold`（辩证折叠，而不是压缩字数）。** 一场争议只有在产出了共同认识、最强支持、最强反对、铰链变量、适用边界、未解决冲突和证伪条件之后，才允许被折叠成一个 `DebateCapsule`。这不是把长文本变短文本，是把一场争议变成它自身「最小充分表示」——原始节点仍在，用户仍能从 capsule 点回原文。
- **`Recall` → `Perspective Recall`（角色化召回，而不是同一份摘要发给所有人）。** `Context_i = ProcessRecall_i + EvidenceProjection_i`：每个席位拿到的是自己的私有过程记忆，加上从同一张证据图生成的、专属于自己关注维度的切片——因果专家看到的是混杂变量和反向因果攻击，测量专家看到的是构念冲突和数据来源。让七个人都读同一份摘要，图省事，但也是制造观点同质化最快的办法。

### 一次被我们自己推翻的方案：动态选组 vs 七人全程

早期方案想用一个 `CoalitionScore` 公式，给每个研究任务动态挑选一个「最适合」的科学家子集，理由是省成本、还能体现「智能调度」。这个方案后来被我们自己否决了：任何一次省略某个角色的调度，都在赌这个角色这次用不上——但争议问题的盲点恰恰常常出现在被认为「用不上」的那个维度上。测量专家看似和因果争议无关，但自报告偏差经常就是那场因果争议的真正根源。

所以最终决定是**每个任务七人全程参与**，成本用别的方式控制，而不是靠减人：每轮发言有预算（一个主动作 + 一个质询，没有新信息就 `PASS`）、语义去重后才触发重推理、复杂判断用强模型/格式化用轻量模型分层调用、七人共享同一份检索缓存而不是各查各的。工程上更麻烦，但换来的是「不会因为调度算法的一次误判，漏掉本该被发现的盲点」。

### 认识论路由：让证据图自己提出下一步该查什么

七人议会不是机械地跑满七轮就算完成。证据图发现缺口后会生成「盲点悬赏」，按影响、不确定性、可调查性、新颖度和成本打分排出优先级，再广播给七人认领——**是证据状态在驱动下一步调查方向，而不是主持人按预定台本依次点名发言**。这也是为什么产品里 Blindspot 是一等公民对象，而不是报告末尾的一段「局限性」文字。

### 完成度不对称的部分

`Fork`（对无法调和的冲突分叉出平行研究路径）、`Merge`（为看似矛盾的两个结果找到能同时解释它们的边界变量）、`Resurrect`（新证据满足复活条件时主动唤醒被隔离的假设），以及盲证据评审、独立双抽取、来源多样性约束、对抗式检索这四种专门对抗「共享证据错误」的机制，均已按设计规格（见 [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](superpowers/specs/2026-07-31-poliscope-design.md) 第 5、7 节）接入主流程，但每一项的完成度并不相同——有的是端到端真实闭环，有的裁剪为「只记录候选、由人决定」，对抗式检索目前只对免密钥数据源真正执行检索、付费数据源仍待真实凭证。具体到每一项的实际状态，见[已知缺口](#已知缺口)第 4、5 项下的详细说明，不要只凭这里的名字判断某个机制「能用到什么程度」。

---

## 先读这一节：当前真实状态

这一节存在的理由，和这个产品存在的理由是同一个：**不把未完成的东西说成已完成的**。

**已经端到端跑通并经过验证的：**

| 能力 | 验证方式 |
|---|---|
| 建任务 → 确认原子主张 → 入队 → Worker 执行 → 事件账本 → 证据门 → 投影器 → 证据图 | 真实 PostgreSQL + 真实 Worker，集成测试覆盖 |
| 三个数据库身份的权限隔离（迁移者 / 应用 / 投影器） | 集成测试断言应用身份写图会被数据库拒绝 |
| 事件账本幂等与断线续传（SSE 按 `Last-Event-ID` 续传） | 集成测试 + 浏览器实测 53/53 事件 |
| 证据门六阶段审核、A–D 分级、因果越级隔离 | 集成测试，关键项经变异测试自证 |
| 十三个 CLI 子命令（含 `pause`/`resume`/`health`/`council-preview`/`council-guidance`/`login`/`register`/`logout`） | 逐条对真实 API 手工验证 |
| 六个前端视图（Research Brief / Controversy Map / Audit Trail / Council / Blindspot Radar / Evolution View）+ 议会检查点模态框 + 建任务/确认主张首屏 | 真实数据 + 浏览器截图核对，无控制台错误 |
| 全文获取 → 解析 → StudyFinding 抽取 → 引用锚点核验 | 单元测试（程序化生成 PDF fixture，无需网络）+ 集成测试断言 `DERIVED_FROM` 边真正出现在证据图上 |
| 联合建模 → Dialectical Fold → `DebateCapsule`；最终复判 → 异议 → `DissentCertificate` | 单元测试覆盖两条产出路径与「无边界/无冲突则不折叠」「无异议目标则记未填槽位」两条弃权路径；集成测试断言完整任务运行后证据图上真的出现对应节点 |
| Docker Compose 一键部署（postgres / migrate / api / worker / web / caddy） | `docker compose up --build` 后真实提交一个任务，走完整 CLI → API → Worker → 图投影路径，再经 Caddy → web 容器 nginx 反代验证 |
| Claude Code / Codex Skill（薄封装：生成待确认 Contract → 调用 CLI），`login` 后即可访问已部署实例 | 手工跑通一次 `start`/`confirm-claims`/`watch`/`export` 全链路，见[Agent Skill 集成细节](#agent-skill-集成细节) |

**尚未接入真实厂商凭证的：**

- **模型网关已有真实实现（`OpenAICompatibleModelGateway`），但尚未连真实凭证。** 对接任意 OpenAI-Chat-Completions 兼容端点（DeepSeek / LongCat / 国内中转站），已接入 `apps/worker`；共享的传输重试策略见 `packages/kernel/http_retry.py`。`POLISCOPE_MODEL_API_KEY` 留空时行为不变——每个席位记为「缺席」，任务以 `COMPLETED_WITH_GAPS` 结束，并在报告的「局限与未知」中逐条列出未填的证据槽位。目前只经过 `httpx.MockTransport` 单元测试验证，尚未打过真实厂商的网络。
- **工具网关已有真实实现（`HttpToolGateway`），同样尚未连真实凭证。** 直接对接 OpenAlex、Crossref、Unpaywall、Semantic Scholar 的公开 REST API，已接入 `apps/worker`。OpenAlex / Crossref / Semantic Scholar 无需任何凭证即可工作；仅 Unpaywall 的使用条款要求每次请求带联系邮箱（`POLISCOPE_TOOLS_CONTACT_EMAIL`），未设置时只有该供应商的调用会报错，其余三个不受影响。同样只经过 `httpx.MockTransport` 单元测试验证，尚未打过真实厂商的网络。

完整清单见 [已知缺口](#已知缺口)。

---

## 设计上的五个硬约束

这五条不是风格偏好，是被数据库权限、类型系统和测试同时约束的机制。

**1. 科学家不能写证据图。**
7 个席位只能向科研事件账本追加事件。只有 Graph Projector 能写 `graph_nodes` / `graph_edges`，而这一点由 PostgreSQL 的 `GRANT`/`REVOKE` 保证——不是靠代码自律。应用身份即使有 bug 也碰不到证据图。

**2. 任何东西都不物理删除。**
被反驳、被隔离、被撤回的节点只改 `status`。所有角色都没有 `DELETE` 权限。研究者放弃的原子主张标记为 `DISCARDED` 而非删除。

**3. 相关不自动升级为因果。**
`CausalUpgradePolicy` 在证据门第六阶段拦截「横截面设计 + 因果主张」，事件被隔离并留下审计记录。

**4. 论文数量不等于独立证据数量。**
`cluster_evidence` 按 (依赖类型, 依赖对象) 合并证据簇。共享研究团队**不**合并——同一个实验室的两个数据集仍然是两份证据。界面同时显示两个数字。

**5. 说不出来的就说「不知道」。**
缺席的席位、未执行的轮次、被拒绝的来源、无法解析的请求，全都是账本里的一等事件，并出现在报告的局限一节。空白不代表没问题。

---

## 本机开发环境

### 依赖

Python 3.12、PostgreSQL 16+、Node.js 20+（仅前端需要）、Docker（集成测试和下面的一键部署都需要）。

```bash
uv sync                    # 或 pip install -e ".[dev]"
```

### 1. 起数据库并建表

```bash
docker run -d --name poliscope-db \
  -e POSTGRES_USER=poliscope_migrator \
  -e POSTGRES_PASSWORD=devpass \
  -e POSTGRES_DB=poliscope \
  -p 55432:5432 postgres:16-alpine

export POLISCOPE_MIGRATOR_DATABASE_URL="postgresql+asyncpg://poliscope_migrator:devpass@127.0.0.1:55432/poliscope"
export POLISCOPE_APP_DATABASE_PASSWORD=devapp
export POLISCOPE_PROJECTOR_DATABASE_PASSWORD=devproj

alembic upgrade head
```

迁移会创建 `poliscope_app` 和 `poliscope_projector` 两个角色并授予各自最小权限。这一步不能跳过——**权限即设计**。

### 2. 起 API

```bash
export POLISCOPE_APP_DATABASE_URL="postgresql+asyncpg://poliscope_app:devapp@127.0.0.1:55432/poliscope"
export POLISCOPE_PROJECTOR_DATABASE_URL="postgresql+asyncpg://poliscope_projector:devproj@127.0.0.1:55432/poliscope"

uvicorn apps.api.main:app --port 8000
```

`GET /health` 会真的查一次数据库，而不是只回报进程活着。

### 3. 起 Worker

```bash
python -m apps.worker.main
```

Worker 用 `SELECT ... FOR UPDATE SKIP LOCKED` 从 `research_tasks` 认领任务，多开安全。

### 4. 起前端

```bash
cd apps/web && npm install && npm run dev
```

打开 `http://localhost:5173/`。开发服务器把 `/api` 代理到 8000 端口，浏览器看到同源，SSE 不需要 CORS 配置。`/` 是公开落地页，研究证据工作台在 `http://localhost:5173/workspace`（开发模式无账号；部署时首次访问需注册/登录账号）——首屏是建任务表单，也可以在页头粘贴一个已有的 `task_id` 直接打开。

### 5. 看一个跑完的任务

```bash
python scripts/seed_demo_task.py     # 用真实 Worker 跑一个演示任务
```

这个脚本用的是真实编排器、真实证据门、真实投影器，只有模型和工具供应商是脚本化的替身。它打印出的 `task_id` 可以直接贴进前端。

---

## 自建部署：Docker Compose + Caddy

不想手动起五个进程，可以直接用根目录的 `docker-compose.yml`：

```bash
cp .env.example .env        # 按注释填数据库密码；模型/工具网关留空也能跑
docker compose up --build -d
```

六个服务：`postgres`（数据）、`migrate`（一次性初始化容器，跑 `alembic upgrade head` 并创建
`poliscope_app`/`poliscope_projector` 两个角色，`api`/`worker` 会等它 exit 0 才启动）、`api`、
`worker`、`web`（nginx 静态资源 + `/api` 反向代理到 `api` 容器）、`caddy`（唯一对外暴露端口的
服务，见下方说明）。没有 Redis——工作队列是 Postgres 的 `SELECT ... FOR UPDATE SKIP LOCKED`，MVP
没有任何组件依赖 Redis 做缓存或广播，加一个没人用的服务违反 CLAUDE.md 第 14 条的 YAGNI 约束。

### 为什么 `api`/`web` 不直接暴露端口

`api` 和 `web` 只在 Compose 内部网络上用 `expose:` 声明端口，不映射到宿主机——`caddy` 是**唯一**
绑定 `80`/`443` 的服务。Caddy 只做反代不做认证（不再有 `basic_auth`）：访问控制由 API 层的账号
系统完成（见下节）。`web` 容器自己的 nginx（`apps/web/nginx.conf`）已经把 `/api/*`（含 SSE，
`proxy_buffering off` + `proxy_read_timeout 3600s`）和静态资源拆开转发到 `api:8000` / 自身静态文件，
所以 Caddy 只需要两条 `handle` 规则（见 `deploy/caddy/Caddyfile`），不需要在边缘再拆一次路由。

### 公开落地页与账号系统的边界

访问控制分两层：

- **`/` 落地页是公开的**：它只有静态文案和链接，不读任何任务数据、不花模型调用的钱，所以不需要登录。
- **研究证据工作台与全部 `/api/*` 需要账号**：注册/登录后拿到 30 天 bearer token（浏览器存
  localStorage 实现本机免登录）；所有业务端点按账号隔离，跨账号访问一律 404（不泄露存在性）。

账号系统已取代旧的 Caddy 共享口令（`basic_auth`）。`.env` 里的 `POLISCOPE_SITE_USERNAME` /
`POLISCOPE_SITE_PASSWORD` 已不再使用，可删除；`deploy/caddy/Caddyfile` 与 `entrypoint.sh` 不再
生成或注入任何口令。账号模型（`users` / `auth_tokens`，PBKDF2 密码哈希、token 仅存 sha256）在
`packages/accounts/`，API 端点在 `apps/api/routers/auth.py`，按账号隔离的数据列由迁移 0012–0014
管理。

### 域名与 HTTPS：只改一行

`POLISCOPE_SITE_ADDRESS` 默认 `:80`（纯 HTTP，适合本机或还没申请域名的裸 IP 云主机）。域名申请
下来之后，把这一行改成真实域名（例如 `poliscope.example.com`），Caddy 会自动申请并续期一张真实的
HTTPS 证书——这是 Caddy 的原生行为，栈里其余任何一处配置都不用动。

```bash
docker compose ps                              # 确认 6 个服务都在跑（migrate 会 exit 0，这是正常的）
curl -I http://localhost/                      # 落地页公开：200
curl -u poliscope:<你的密码> http://localhost/workspace   # 工作台要口令：带凭据 200，不带 401
```

`.env.example` 里的模型/工具网关变量留空时，Worker 会诚实降级为 `COMPLETED_WITH_GAPS`（7 个席位全部
标注 `SEAT_UNAVAILABLE`），不会假装完整跑完——这是设计使然，不是 Bug。想接真实模型供应商，填
`POLISCOPE_MODEL_*` 系列变量指向任意 OpenAI-Chat-Completions 兼容端点；**千万不要**填成运行 Claude
Code 会话自己的 `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL`——那是 Claude Code 连 Anthropic 用的凭证，
不属于 Poliscope 的模型网关。

### 任务级模型设置：研究者自带接口

网页端建任务时可以在"模型设置"里填自己的 OpenAI 兼容端点（Base URL + API Key + 可选模型名），
本次任务就运行在研究者自己的接口上，与部署方的 `POLISCOPE_MODEL_*` 配置无关（worker 按任务读取
`research_tasks.model_config`，迁移 0009）。模型名留空时按序回退：任务自带的模型名 →
部署环境 `POLISCOPE_MODEL_NAME` → `deepseek-chat`。API Key 只存任务行，**任何读端点都不回显**（单元
测试 `test_create_with_model_config_stores_it_and_never_echoes_it` 钉死这条）；密钥的明文存储是
自部署工具下的务实选择，请按你部署环境的数据库访问权限来评估风险。

---

## 完整工作流

### 网页端

打开落地页点"进入工作台"（或直接访问 `/workspace`，设了口令时这里才弹登录框），看到问题输入框
（不需要先懂 CLI）。展开"高级选项"可以调整人群/地区/语言/日期范围/证据优先级/预算，留空则使用
已验证过的默认值；"模型设置"可填研究者自己的模型接口（留空用部署方默认模型）。提交后原地展示
API 返回的 `suggested_claims`（默认全选，可取消勾选），点"确认并开始研究"后跳转到既有的六视图
工作台，`?task=<id>` 驱动当前打开的任务，可以直接复制这个链接分享。研究过程中工作台会随轮次自动
切换到对应视图（手动点过标签后停止跟随），Council 视图里可以展开每位科学家思考密集轮次的原始
模型推理（折叠展示，标注为过程数据、非正式证据）。

### CLI

```bash
# 1. 建任务：只创建，不开始研究
poliscope --json start --contract research.json
#    → status: AWAITING_CLAIM_CONFIRMATION，并返回建议的原子主张

# 2. 研究者确认要调查哪些主张，任务同时入队
poliscope confirm-claims --task-id <id> --claim-ids <a> <b>
#    → status: QUEUED

# 3. 跟踪执行
poliscope watch --task-id <id>
#    [1] PHASE_STARTED
#    [2] ResearchQuestion
#    [3] SEAT_UNAVAILABLE
#    ...
#    跑到盲点悬赏结束时会停在 AWAITING_COUNCIL_INPUT——见下面「议会检查点」，
#    这不是任务失败，是唯一一个刻意设计的暂停点。

# 4. （可选）查看七人立场，提交或跳过方向性引导
poliscope council-preview --task-id <id>
poliscope council-guidance --task-id <id> --text "优先讨论跨文化适用边界"
#    → status: QUEUED，worker 重新认领并从联合建模续跑

# 5. 看结果
poliscope status --task-id <id>
poliscope export --task-id <id> --format markdown --output brief.md
```

**为什么第 1 步不直接开始研究？** CLAUDE.md 第 2 条要求研究者控制方向。如果建任务即开跑，议会就自己挑了研究问题。

注意 `--json` 是全局选项，要放在子命令**前面**。

访问一个已部署实例时，先登录一次：

```bash
poliscope login --base-url <URL>          # 交互输入用户名密码；首次可用 register 注册
```

token 保存在 `~/.poliscope/credentials.json`，之后每个子命令自动带上；非交互环境也可以设置
`POLISCOPE_API_TOKEN` 环境变量（优先级更高）。本机直连一个本地 API 不需要登录。

### 议会检查点：盲点悬赏结束后，研究者可以插一句话，但不能投票

八个阶段跑到 `BLINDSPOT_BOUNTY` 结束、`JOINT_MODELING` 开始之前，任务会停在
`AWAITING_COUNCIL_INPUT`——这是唯一、固定的一个检查点，不是「议会随时可以被打断」的通用能力（后者
仍不在本版范围，见[已知缺口](#已知缺口)第 7 项）。停在这里时：

```bash
poliscope council-preview --task-id <id>
# task       <id>
# status     AWAITING_COUNCIL_INPUT
#
# causal_scientist
#   confidence        0.42
#   update_condition  纵向数据出现且效应量不变
#   challenges_raised 2
#
# measurement_scientist
#   ...
```

看完七人在盲点悬赏结束时的预承诺置信度、更新条件和已提出的质询之后，研究者可以提交一段方向性备注，
也可以什么都不填直接继续——两者是**同样有效**的动作：

```bash
poliscope council-guidance --task-id <id> --text "优先讨论跨文化适用边界"
poliscope council-guidance --task-id <id> --text ""      # 不干预，直接继续
```

这段文字只会作为 `"[研究者方向性备注，非科学判断]: ..."` 独立注入联合建模阶段的 prompt，供模型参考
接下来重点讨论哪些悬而未决的冲突——它不进入任何 `Claim` 的证据来源，不改变 Evidence Gate 的判定逻辑，
不构成对 CLAUDE.md 第 4/8 条「禁止多数投票裁决科研真理」的违反（完整说明见 CLAUDE.md 第 4.1 节、设计
规格第 4.5 节）。Web 工作台会自动检测到这个状态并弹出对应的检查点面板，无需手动刷新。

### 暂停与恢复：挡认领，不打断在跑的议会

```bash
poliscope pause --task-id <id>     # 只对还在 QUEUED 里排队的任务生效
poliscope resume --task-id <id>
```

`pause` 把一个还没被 worker 认领的任务从 `QUEUED` 挪到 `PAUSED`，恢复之前它永远不会被认领。它**不能**
打断一个已经在跑的议会——`deliberate()` 把一个任务的八阶段全部跑在一次未提交事务里，跑到一半没有可
供快照的持久状态。这与上面的议会检查点是两件事：检查点是内容层面「唯一固定的暂停点」，`pause`/`resume`
是队列层面「还没轮到你就不会被抢」，完整边界说明见[已知缺口](#已知缺口)第 7 项。

### 上传 PDF：没有 DOI 的来源怎么进证据管线

`user_evidence.pdf_object_ids` 不能在建任务时就带文件——`ObjectModel.task_id` 是非空外键，对象必须
挂在一个已存在的任务上，所以流程是「先建任务 → 再上传 → 上传结果回填任务」，而不是一步到位：

```bash
poliscope start --contract research.json --json    # 先拿到 task_id，pdf_object_ids 留空
curl -F "file=@paper.pdf" http://localhost:8000/api/tasks/<task_id>/papers/upload
#    → {"object_id": "...", ...}，服务端已自动把它写回该任务的 user_evidence
poliscope confirm-claims --task-id <task_id> --claim-ids <a> <b>
```

**当前只有原始 HTTP 接口，`apps/cli` 和 Web 工作台都还没有对应的上传入口**——这是一个已记录的、诚实
的窄范围（CLAUDE.md 第 17 条），不是假装支持。上传的字节只写入私有对象存储，从不出现在日志或导出里
（CLAUDE.md 第 16 条）。

`research.json` 的 schema：

```json
{
  "question": "远程办公是否降低团队创新产出？",
  "scope": {
    "populations": ["knowledge workers"],
    "regions": ["global"],
    "languages": ["en", "zh"],
    "date_from": "2015-01-01",
    "date_until": "2025-12-31",
    "evidence_priorities": ["CAUSAL_OR_REVERSE_CAUSAL", "REPLICATION"],
    "allow_preprints": true
  },
  "budget": {
    "wall_clock_minutes": 60,
    "model_cost_usd": "10.00",
    "tool_call_limit": 100,
    "source_limit": 50
  },
  "user_evidence": {}
}
```

`evidence_priorities` 的取值：`CORRELATION`、`CAUSAL_OR_REVERSE_CAUSAL`、`MEASUREMENT`、`REPLICATION`、`BOUNDARY`、`MECHANISM`、`NULL_OR_COUNTEREXAMPLE`。

### HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 真实查询数据库 |
| `POST` | `/api/tasks` | 建任务，返回建议的原子主张 |
| `GET` | `/api/tasks/{id}` | 任务状态 |
| `POST` | `/api/tasks/{id}/confirm-claims` | 确认主张并入队 |
| `POST` | `/api/tasks/{id}/pause` | 挡认领（仅对仍在 `QUEUED` 的任务有效） |
| `POST` | `/api/tasks/{id}/resume` | 从 `PAUSED` 放回 `QUEUED` |
| `GET` | `/api/tasks/{id}/council-preview` | 只读查看盲点悬赏结束时 7 席位的立场 |
| `POST` | `/api/tasks/{id}/council-guidance` | 提交（或留空放弃）方向性备注，续跑联合建模 |
| `POST` | `/api/tasks/{id}/papers/upload` | 上传 PDF 并回填该任务的 `user_evidence` |
| `GET` | `/api/workspace/{id}` | 整个工作台快照（含 brief、图、计数、版本号） |
| `GET` | `/api/reports/{id}?format=json\|markdown` | Research Brief |
| `GET` | `/api/stream/{id}` | SSE 事件流，支持 `Last-Event-ID` 续传 |

工作台是**一个**端点而不是每个面板一个，这样 Research Brief、Controversy Map 和议会状态不可能显示三个不同时刻的状态。`workspace_version` 是快照对应的账本序号。

SSE 帧只有 `id:` 和 `data:`，没有 `event:` 行。原因是 SSE 的类型化帧只会送达注册了同名类型的监听器，于是任何客户端都必须穷举后端的事件词汇表，并**静默丢弃**没见过的类型——审计轨迹一度因此只显示 53 个事件中的 37 个，而且什么都没提示。事件类型放在 body 的 `kind` 字段里，客户端不可能收不到。

---

## 系统架构

```
研究者
  │
  ├─ CLI (apps/cli) ─────┐
  └─ Web (apps/web) ─────┤
                         ▼
                  Caddy（唯一公开端口；认证在 API 层）
                         ▼
                   API (apps/api)  ← 只持有应用身份
                         │
                    PostgreSQL ← ← ← ← ← ← ← ← ← ┐
                         │                        │
                   Worker (apps/worker)           │
                         │                        │
        ┌────────────────┴────────────────┐       │
        │ 事务一：poliscope_app            │       │
        │  CouncilOrchestrator            │       │
        │   └─ 8 个阶段 × 7 个席位          │       │
        │       ├─ GatewayDeliberator ──→ Model Gateway
        │       ├─ CouncilMemory ──────→ MemoBrain Adapter
        │       └─ SourceAcquisition ──→ Tool Gateway
        │  → 追加到科研事件账本            │       │
        │  ── commit ──                   │       │
        ├─────────────────────────────────┤       │
        │ 事务二：poliscope_projector      │       │
        │  SqlGraphProjector              │       │
        │   └─ FullEvidenceGate（六阶段）  │       │
        │  → 写入 graph_nodes / graph_edges ──────┘
        └─────────────────────────────────┘
```

**为什么拆成两个事务两个身份？** 因为「投影器是证据图唯一写入者」如果只写在文档里，就只是一句愿望。拆开之后，应用身份在数据库层面没有写图的权限，投影器在数据库层面没有写账本的权限——两边都无法互相伪造。

**为什么队列在 PostgreSQL 而不是 arq？** 认领任务和更新状态在同一个事务里，Worker 崩溃时锁自动释放。用 Redis 会多出一个可能与数据库不一致的「谁在跑」的真相来源。Redis 仍然适合做缓存与广播，只是不做工作队列。这条偏离已按 CLAUDE.md 第 17 条记录在 `apps/worker/main.py` 的模块文档中。

---

## 七名科学家与议会协议

| 席位 | 职责 |
|---|---|
| `theory_builder` | 理论建构者：机制与风险预测 |
| `causal_scientist` | 因果推断专家：混杂、反向因果、选择偏差 |
| `measurement_scientist` | 测量与构念专家：构念与操作化的距离 |
| `replication_scientist` | 统计与复现专家：功效、精度、复现史 |
| `boundary_scientist` | 边界与情境专家：适用与不适用的范围 |
| `adversarial_falsifier` | 对抗性证伪者：攻击最强版本 |
| `evidence_auditor` | 证据与溯源审计员：锚点与独立性 |

7 个席位共享运行时和工具缓存，但各自拥有：独立角色规格（`packages/council/roles.py` 与 `deliberation.py` 的席位指令）、独立私有记忆（`{task_id}:{seat}` 作用域）、不同的证据投影权重（`packages/memory/projection.py`）。**这三样都有测试断言它们确实不同**——曾经有五个席位共用一个空权重表，在唯一用于区分它们的维度上其实是同一个 Agent 的五份拷贝。

八个阶段按 `PHASE_SEQUENCE` 顺序执行，一个都不能跳：

```
PRECOMMITMENT → ACQUISITION → EVIDENCE_EXCHANGE → CROSS_EXAMINATION
  → BLINDSPOT_BOUNTY → JOINT_MODELING → FINAL_REJUDGMENT → REPORTING
```

- **独立预承诺先于一切。** 封存之后才能读取，读取之后不能提交。这是「后续一致不是锚定」的唯一保证。
- **一个席位失败不终止任务。** 失败被记为该轮的 `PHASE_FAILED` 事件和一个未填槽位，下一轮照常执行，任务以 `COMPLETED_WITH_GAPS` 收尾。
- **禁止多数投票裁决科研真理。** 联合建模在缺少「最强反对」或「证伪条件」时拒绝出具共识。
- **重放安全。** 每个事件的幂等键由阶段 + 席位 + 位置派生，重跑一次是空操作。任何把 `uuid4()` 写进 payload 的轮次都会在重放时与自己的幂等键冲突——有测试专门盯这件事。

---

## 证据治理

### 双图隔离

- **Process Graph**：科学家的任务、工具调用、失败路线、质询、决策。由 MemoBrain 管理，允许 Fold 与 Recall。**过程节点不会自动成为正式证据。**
- **Evidence Graph**：10 种节点、12 种边。只有 Graph Projector 能写。

节点：`ResearchQuestion`、`Claim`、`Source`、`StudyFinding`、`Construct`、`Context`、`Blindspot`、`DebateCapsule`、`DiscriminatingStudy`、`DissentCertificate`。`DebateCapsule` 由联合建模阶段的 Dialectical Fold 产出，`DissentCertificate` 由最终复判阶段为持异议的席位产出——两者都是 CLAUDE.md 第 4 条「异议不得被静默删除」的直接实现。

边：`SUPPORTS`、`REFUTES`、`QUALIFIES`、`CONTRADICTS`、`CONFOUNDS`、`MEDIATES`、`MODERATES`、`OPERATIONALIZES`、`DERIVED_FROM`、`APPLIES_IN`、`EXPOSES`、`TESTS`。

### 证据分层

| 等级 | 含义 | 处置 |
|---|---|---|
| A | 全文与精确原文可得 | `ADMIT` → 节点 `active` |
| B | 只有摘要和可靠元数据 | `SOURCE_ONLY` → 节点 `provisional` |
| C | 综述中的二手描述 | `DISCOVERY_ONLY` → 留在账本，不入图 |
| D | 网页或新闻线索 | `TOOL_LEAD_ONLY` → 留在账本，不入图 |

目前的取证管线只能拿到元数据，所以只产 Level B。**这不是配置问题，是实事求是**：把元数据标成 Level A，就等于允许一个高置信因果结论建立在摘要之上。

### 六阶段证据门

`SCHEMA → DEDUPLICATION → SOURCE → CITATION_ENTAILMENT → METHOD_QUALITY → GRAPH_CONSISTENCY`

第 3 和第 5 阶段曾经用零参数调用各自的校验器，因此拿的是乐观默认值——一篇撤稿论文可以一路通过。现在它们读取候选事件自己带的元数据，撤稿会被拒。

---

## 目录结构

```
packages/
  kernel/      冻结契约（ContractModel/FrozenDict）、数据库、配置、权限
  council/     7 个席位、结构化动作、8 个轮次、轮次注册表、席位—网关桥
  epistemo/    编排器、状态机、预算、停止条件、恢复
  memory/      MemoBrain 适配器、席位私有记忆、Process Graph、证据投影
  evidence/    事件账本、证据门、图投影器、谱系、独立性、辩证折叠
  papers/      取证服务、候选池、查询规划、解析、Packet
  models/      模型网关（审计装饰器 + 录制回放）
  tools/       工具网关、四个学术数据源适配器
  reports/     Research Brief 组装、Markdown / JSON 渲染、导出脱敏
  research/    研究契约、主张原子化、任务仓储与应用服务
  evaluation/  ForesightBlindspot 基准语料与验收矩阵

apps/
  api/         FastAPI：tasks / workspace / reports / stream（含 Dockerfile）
  worker/      任务认领、议会执行、图投影（含 Dockerfile）
  cli/         十个子命令，纯 HTTP 客户端，不直连 packages
  web/         React + TypeScript + React Flow 建任务表单 + 六视图工作台（含 Dockerfile、nginx.conf）

poliscope/       CLI 入口点
migrations/      Alembic：建表 + 建角色 + 授权
scripts/         开发辅助（演示任务播种）
docker-compose.yml  一键部署：postgres / migrate / api / worker / web / caddy
.env.example        部署所需环境变量，不含真实凭证
deploy/caddy/       反向代理 + HTTPS-when-domain-exists 配置（访问控制由 API 层账号系统负责）

.claude/skills/poliscope/   Claude Code 的 Agent Skill（项目级，随仓库自动发现）
.codex/skills/poliscope/    同一 Skill 的 Codex 副本，内容与上面保持一致
.agents/skills/poliscope/   同一 Skill 的通用 AGENTS.md 副本，内容与上面保持一致
skills/poliscope/           Claude Code 插件分发用副本（/plugin install 后可用；.claude-plugin/plugin.json 为插件元数据）
tests/
  unit/        无外部依赖，约 1 秒跑完
  integration/ 需要 Docker，跨真实数据库与真实角色
  golden/      因果蕴含黄金用例
  e2e/         发布门禁
```

CLI 是纯 HTTP 客户端，不 import `packages`。第二条进入研究服务的代码路径会让 CLI 绕过证据门。

### 前端设计取向

界面像科研仪器与证据审查工具，不像驾驶舱。具体来说：

- **结论与局限是两列等宽**，中间只用一根分隔线，局限位于结论面板**内部**、盲点之前。半途停止阅读的人不能只读到发现。
- **颜色只表示证据状态**：已采纳 / 仅元数据 / 已反驳 / 未知。无法识别的状态映射为「未知」而非「已采纳」——新状态不能一出现就长得像已核实。
- **被反驳的节点淡化，永不消失。** 地图上的开关只改不透明度，不能删除。反驳类边同时用红色和虚线，去掉颜色也能读。
- **缺口计数常驻在每个标签页的页头。** 不可能在看不见「多少协议没跑完」的情况下读到结论。
- 无卡通 Agent、无霓虹、无脉冲指示灯。唯一的动效是按压反馈和地图自身的平移缩放。
- 完整支持 `prefers-reduced-motion`、`prefers-reduced-transparency`、`prefers-contrast`。

---

## Agent Skill 集成细节

四个入口：`.claude/skills/poliscope/`（Claude Code 项目级 Skill，随仓库自动发现）、
`.codex/skills/poliscope/`（Codex 对应副本）、`.agents/skills/poliscope/`（通用
`AGENTS.md` 约定读取的第三份副本）与 `skills/poliscope/`（Claude Code 插件分发结构，
`/plugin install` 后可用，配套 `.claude-plugin/plugin.json`）——四份内容保持一致，避免多个入口
的科研逻辑分叉——设计规格 §8.7 的硬约束。它们本质上都只是薄封装：解析用户意图 → 用
`scripts/new_contract.py` 生成待确认的 Research Contract → 展示给用户确认 → 依次调用
`poliscope start` / `confirm-claims` / `watch` / `status` / `export`，不直接 import
`packages`，不直接调模型或论文数据源，不绕过原子主张确认和证据门。

**访问已部署实例：** 先登录一次——`poliscope login --base-url <URL>`（或 `poliscope register
--base-url <URL>` 注册新账号），token 保存在 `~/.poliscope/credentials.json`
（`apps/cli/main.py::_save_token()`），之后每个子命令自动附带 `Authorization: Bearer` 头
（`apps/cli/main.py::_load_token()` 传给 `apps/cli/client.py::CLIClient` 的 `token` 参数）。非交互
环境（Agent 场景）也可以设置 `POLISCOPE_API_TOKEN` 环境变量，优先级高于凭据文件。本机直连一个
本地 API 不需要登录。

**已知缺口：** `POST /api/tasks/{id}/papers/upload` 这个 HTTP 接口本身已经真实可用（见上表），但
`apps/cli` 和 Skill 侧都还没有对应的封装命令——Skill 只能诚实地告诉用户"任务必须先建好，PDF 要绕开
本 Skill 直接调那个接口"，而不是假装自己能一步完成上传，见上文[上传 PDF](#上传-pdf没有-doi-的来源怎么进证据管线)。

---

## 开发与测试

```bash
pytest                                     # 全部（需要 Docker）
pytest tests/unit tests/golden tests/e2e   # 不需要 Docker，约 1 秒
ruff check .
mypy .                                     # strict，含测试
cd apps/web && npm run build               # tsc --noEmit && vite build
```

没有 Docker 时集成测试会**跳过并说明原因**，不会静默通过——一份在没有数据库的机器上依然全绿的测试报告，等于在撒谎。

**测试纪律：**

- 业务规则、状态机和图约束先写测试。
- 修 Bug 必须加回归测试。
- 不满足于「请求成功」，要断言证据状态和溯源字段。
- 关键修复用变异测试自证：把修复回退，确认测试确实失败，再恢复。
- 不写只调用同文件其它测试的 `test_suite()` 型空壳——它们只会虚增通过数。

`tests/integration/` 下的每个文件都必须真的连数据库（由目录级 conftest 强制打标）。只测纯逻辑的文件属于 `tests/unit/`。

---

## 已知缺口

我们把这份清单当成一个功能：一个愿意说清楚自己边界的科研工具，比一个假装什么都做完的工具更值得信任（CLAUDE.md 第 7 条）。按「离核心闭环的距离」排序，这里只列**当前仍然真实存在**的缺口——已经解决的部分不再作为「曾经的缺口」占篇幅，其修复过程见 `git log`。

**阻塞真实使用：**

1. **模型网关无真实厂商凭证。** `OpenAICompatibleModelGateway`（`packages/models/openai_compatible.py`）已实现并接入 `apps/worker`，对接任意 DeepSeek / LongCat / 国内中转站等 OpenAI-Chat-Completions 兼容端点；仅经 `httpx.MockTransport` 单元测试验证，尚未填入真实 `POLISCOPE_MODEL_API_KEY` 跑通一次真实调用。
2. **工具网关无真实厂商凭证。** `HttpToolGateway`（`packages/tools/http_gateway.py`）已实现并接入 `apps/worker`，直接调用 OpenAlex / Crossref / Unpaywall / Semantic Scholar 的真实公开 API；OpenAlex / Crossref / Semantic Scholar 无需凭证，仅 Unpaywall 需要 `POLISCOPE_TOOLS_CONTACT_EMAIL`。同样仅经 `httpx.MockTransport` 单元测试验证，尚未打过真实网络。

**影响证据质量：**

3. **`packages/evidence/repository.py::GraphRepository` 仍无生产调用者，但 `GRAPH_CONSISTENCY` 门禁本身已经真实生效。** 六阶段证据门的 `GRAPH_CONSISTENCY` 阶段现在真正调用 `consistency.py::check_graph_consistency()`（`gate.py:378-395`），判定所需的两个布尔值来自 `SqlGraphConsistencyQuery`（在 `sql_projector.py:245-247` 挂接）对数据库会话的真实查询——既有矛盾节点类型、既有重复分叉谱系都会被真正检出，不再是恒真的空检查。但 `repository.py::GraphRepository`——一个纯内存图存储类——仍然没有任何生产调用者：生产路径绕开了它，直接走 SQL 会话查询。按既定原则（死代码接线而非删除）予以保留，尚待接入或视为可清理的重复实现。
4. **四种防「共享证据错误」机制完成度不对称（设计规格 §7.9）。** 来源多样性约束（`packages/evidence/source_diversity.py`）、盲证据评审（`packages/council/deliberation.py` 构造 prompt 时结构性去掉 author/journal/citation_count）、独立双抽取（`FindingExtractor.extract(dual_extraction=True)` 比较两次抽取的 `exact_quote`/`effect_direction`）三项仍是**完整闭环**，均有端到端验证。对抗式检索（`packages/evidence/adversarial_retrieval.py`）**现在会真正执行检索**：为每个 confirmed claim 生成的 6 类反向检索意图字符串，会经 `packages/tools` 网关对免密钥数据源（OpenAlex、Semantic Scholar 等）发起真实查询，命中结果回填 `CandidatePool`；查不到的（免密钥数据源覆盖有限，或确实没有反例）诚实记录在 `ADVERSARIAL_RETRIEVAL_ATTEMPTED` 事件的未解析计数里，不伪造命中，保持在审计轨迹上可见。付费数据源仍受限于[第 1、2 项](#已知缺口)尚缺真实凭证。
5. **`Fork`、`Merge`、`Resurrect` 完成度不对称。** `Resurrect`（`packages/evidence/lifecycle.py::check_resurrection_conditions()`，接入 `run_evidence_exchange`）与 `Fork`（`run_cross_examination` 里致命质询产出平行 `Claim` + `CONTRADICTS` 边）均为**完整闭环**，各有集成测试端到端验证。`Merge` **裁剪为「记录合并候选」，不做自动合并**：`run_joint_modeling` 产出的事件把未解决冲突列为 `merge_candidates`，是否合并留给研究者在前端判断（CLAUDE.md 第 8 条）。`packages/memory/branches.py::BranchService`——一套与账本式 Fork/`merge_candidates` 机制不兼容、且 `merge()` 本身有静默丢弃多余 `branch_ids` 之 Bug 的纯内存记录类——已删除（连同其死测试 `tests/unit/test_branch_operations.py`），不再作为「保留但未接线」的死代码占位。

**尚未开始 / 明确超出本版范围：**

6. **ForesightBlindspot 评测完成度不对称。** 五个对照组（`packages/evaluation/harness.py::run_baseline()`）与打分函数（`packages/evaluation/scoring.py`）是**完整闭环**，`tests/unit/test_evaluation_demo_case.py` 用一次脚本化的完整议会验证 Blindspot Recall/Precision、Citation Entailment、Evidence Independence、Dissent Preservation 四项打分都能算出非平凡的值。`score_causal_overclaim` **不再恒为 `None`，但只是自报告，没有独立核验**：Fork 产出 Claim 时的 `claim_type` 现在由 `_self_reported_claim_type()`（`registry.py:1010-1024`）判定，可以产出 `ClaimType.CAUSAL`，让打分函数算出真实值；但这仍然只是自我报告，没有独立分类器核验一个 Fork 是否真的携带因果设计元数据——这是一个已披露的偏离（原因记在 `registry.py:950-966`），不能宣称「因果过度推断检测已彻底解决」。人工标注 Kappa/Alpha（`packages/evaluation/agreement.py`）**只搭了统计骨架，没有标注数据**——`load_human_annotations()` 按 CLAUDE.md 第 7 条故意抛出 `HumanAnnotationsNotCollected`，标注 UI、招募与培训流程是独立于本模块的产品工作，需要真人标注员，不是代码问题。真实厂商模型 + 真实论文语料的时间切片评测语料尚未采集（受限于[第 1、2 项](#已知缺口)本身尚缺真实凭证），五个基线之间的正式对照实验也尚未作为一次真实实验跑出数字。
7. **快照 / 暂停 / 恢复的范围比字面意思窄——是「暂停认领」，不是「打断在跑的议会」。** `deliberate()`（`apps/worker/jobs.py`）把一个任务的完整 8 阶段议会跑在一次未提交事务里，`CouncilMemory` 每次调用都现造一个全新的 `InMemoryMemoryAdapter()`，没有任何跨调用持久化状态可供跑到一半时快照。能诚实交付的是队列层面：`poliscope pause`/`resume` 和对应的 `POST /api/tasks/{id}/pause`/`resume` 端点把任务在 `QUEUED`⇄`PAUSED` 间搬动——一个被暂停的任务在恢复之前永远不会被 worker 认领（`test_a_paused_task_is_never_claimed_until_resumed` 端到端验证），但**不能**打断一个已经在跑的议会。这与本版新增的 JOINT_MODELING 前人类引导检查点（`AWAITING_COUNCIL_INPUT`，见 CLAUDE.md 第 4.1 节、设计规格第 4.5 节）是两回事——那是一个**唯一、固定**的暂停点，只出现在 BLINDSPOT_BOUNTY 与 JOINT_MODELING 之间；真正的「议会任意时点打断再续跑」需要重构 `CouncilOrchestrator` 的阶段循环与事务边界、并接一个持久化的快照列，是编排器层面的改动，仍不在本版范围内。
8. **Evolution View 只有定性的连续阶段轨迹，没有数值化的置信度曲线。** 关键阶段边界（证据交换、交叉质询、联合建模、最终复判结束时）现在都会为受影响的 `Claim` 追加一条 `CONFIDENCE_UPDATED` 过程事件（`_confidence_marker()`，`registry.py:345-376`），`_evolution()`（`workspace.py:238-291`）读取后能画出跨越全部四个阶段的连续轨迹点——这不再是生产编排本身的空白。但每条事件只携带一段文字说明（`confidence_delta_note`），从不产出精确数值置信度：这是刻意设计，不是尚未实现——CLAUDE.md 第 16 条禁止用模型置信度替代统计不确定性，因此这里做的是定性变化轨迹，不是可绘制数值曲线的量化分数。
9. **域名接入 HTTPS 未做真实端到端验证。** `deploy/caddy/` 的域名切换逻辑（把 `POLISCOPE_SITE_ADDRESS` 改成真实域名即自动签发证书）依赖的是 Caddy 自身广泛验证过的行为，配置本身经过语法与拓扑核查（`docker compose config`），但本仓库开发环境没有可用的真实域名，因此没有跑过一次真实的证书签发流程。第一次接入真实域名时按 Caddy 官方文档的期望行为发生，但这一步本身尚未被亲眼验证过。

---

## 安全与伦理

- 系统输出**不是**临床诊断或医疗建议。涉及心理健康的问题会自动附加科研辅助定位声明——只在相关领域附加，全篇都贴会训练读者跳过它。
- 不抓取或存储未授权的个人数据，不绕过付费墙或访问控制。
- 导出会脱敏签名 URL 与本地路径，避免上传材料通过报告泄露。
- 报告始终声明 AI 辅助、证据覆盖与系统局限。
- **不以模型置信度替代统计不确定性或专家判断。** 这句话出现在每一份报告的局限一节，不是免责套话——它是这个系统全部设计取舍的出发点。
- 账号系统（注册/登录/按账号隔离）能把不同研究者及其任务、知识库、设置分开，但本机部署的信任模型是「能进这台机器就是这台机器的主人」；公开部署时应配套 HTTPS、强密码与备份策略。密码哈希（PBKDF2）与 token 只存 sha256 是对泄库的缓解，不是授权边界本身。

---

## 许可证与归属

Poliscope 自身代码以 [MIT 许可证](../LICENSE) 开源；MemoBrain 作为外部方法基座保留其自身许可证，两者不混用（见下文）。

### 方法基座

Poliscope 以 [MemoBrain](https://github.com/qhjqhj00/MemoBrain) 作为执行记忆方法基座：

- 论文：*MemoBrain: Executive Memory as an Agentic Brain for Reasoning*
- ACL Anthology：<https://aclanthology.org/2026.findings-acl.127/>

集成前已核验上游许可证，并通过 `MemoBrainAdapter` 适配，避免修改上游源代码。相关记录见 `docs/licenses/`。

### Poliscope 扩展

以下机制属于本项目设计，不得误写为 MemoBrain 原论文贡献：

- 7 人完整科学议会
- 私人、议会和正式证据的分层记忆
- Process Graph 与 Evidence Graph 双图架构
- Scientific Event Ledger
- Quarantine、Dialectical Fold、Perspective Recall
- Fork、Merge、Resurrect
- 盲点悬赏与认识论路由
- 条件化共识与 Dissent Certificate
- Evidence Lineage Graph
- ForesightBlindspot 时间切片评测

其中 Dialectical Fold 与 Dissent Certificate 已完整接入主流程（`DebateCapsule`、`DissentCertificate` 均端到端产出并写入证据图）；Fork/Merge/Resurrect、Evidence Lineage Graph、ForesightBlindspot 三项完成度不对称，具体到每一项的实际状态见[已知缺口](#已知缺口)。

---

> **一句话总结**：Poliscope 不是更快的文献综述机器，而是一面让你看见证据裂缝的科研透镜——包括它自己的裂缝。
