# Poliscope

> 面向计算社会科学争议问题的可审计深度研究智能体

Poliscope 组织 7 名具有互补认识能力的 AI 科学家，全程参与问题拆解、独立判断、论文取证、交叉质询、盲点调查与最终复判，产出一张**可审计的争议证据地图**。

它不追求更长的文献综述。它回答的是：**当前结论最可能错在哪里？哪些盲点、反例、边界条件或证据依赖会改变科研判断？**

核心方法名为 **EpistemoBrain**。设计规格见 [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)。

---

## 目录

- [设计思路：从 MemoBrain 到 EpistemoBrain](#设计思路从-memobrain-到-epistemobrain)
- [先读这一节：当前真实状态](#先读这一节当前真实状态)
- [设计上的五个硬约束](#设计上的五个硬约束)
- [快速开始](#快速开始)
- [完整工作流](#完整工作流)
- [系统架构](#系统架构)
- [七名科学家与议会协议](#七名科学家与议会协议)
- [证据治理](#证据治理)
- [目录结构](#目录结构)
- [开发与测试](#开发与测试)
- [已知缺口](#已知缺口)
- [安全与伦理](#安全与伦理)
- [许可证与归属](#许可证与归属)

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

`Fork`（对无法调和的冲突分叉出平行研究路径）、`Merge`（为看似矛盾的两个结果找到能同时解释它们的边界变量）、`Resurrect`（新证据满足复活条件时主动唤醒被隔离的假设），以及盲证据评审、独立双抽取、来源多样性约束、对抗式检索这四种专门对抗「共享证据错误」的机制，均已按设计规格（见 [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md) 第 5、7 节）接入主流程，但每一项的完成度并不相同——有的是端到端真实闭环，有的裁剪为「只记录候选、由人决定」，有的只做到意图生成而非真实检索。具体到每一项的实际状态，见[已知缺口](#已知缺口)第 5、6 项下的详细说明，不要只凭这里的名字判断某个机制「能用到什么程度」。

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
| 六个 CLI 子命令 | 逐条对真实 API 手工验证 |
| 三个前端视图（Research Brief / Controversy Map / Audit Trail） | 真实数据 + 浏览器截图核对，无控制台错误 |
| 全文获取 → 解析 → StudyFinding 抽取 → 引用锚点核验 | 单元测试（程序化生成 PDF fixture，无需网络）+ 集成测试断言 `DERIVED_FROM` 边真正出现在证据图上 |
| 联合建模 → Dialectical Fold → `DebateCapsule`；最终复判 → 异议 → `DissentCertificate` | 单元测试覆盖两条产出路径与「无边界/无冲突则不折叠」「无异议目标则记未填槽位」两条弃权路径；集成测试断言完整任务运行后证据图上真的出现对应节点 |

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

## 快速开始

### 依赖

Python 3.12、PostgreSQL 16+、Node.js 20+（仅前端需要）、Docker（仅集成测试需要）。

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

### 4. 起前端（可选）

```bash
cd apps/web && npm install && npm run dev
```

打开 `http://localhost:5173/?task=<task_id>`。开发服务器把 `/api` 代理到 8000 端口，浏览器看到同源，SSE 不需要 CORS 配置。

### 5. 看一个跑完的任务

```bash
python scripts/seed_demo_task.py     # 用真实 Worker 跑一个演示任务
```

这个脚本用的是真实编排器、真实证据门、真实投影器，只有模型和工具供应商是脚本化的替身。它打印出的 `task_id` 可以直接贴进前端。

---

## 完整工作流

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

# 4. 看结果
poliscope status --task-id <id>
poliscope export --task-id <id> --format markdown --output brief.md
```

**为什么第 1 步不直接开始研究？** CLAUDE.md 第 2 条要求研究者控制方向。如果建任务即开跑，议会就自己挑了研究问题。

注意 `--json` 是全局选项，要放在子命令**前面**。

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

节点：`ResearchQuestion`、`Claim`、`Source`、`StudyFinding`、`Construct`、`Context`、`Blindspot`、`DebateCapsule`、`DiscriminatingStudy`、`DissentCertificate`。DissentCertificate 由本阶段（阶段 2）新增接线，见下文[已知缺口](#已知缺口)第 5 项的更新说明。

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
  api/         FastAPI：tasks / workspace / reports / stream
  worker/      任务认领、议会执行、图投影
  cli/         六个子命令，纯 HTTP 客户端，不直连 packages
  web/         React + TypeScript + React Flow 三视图工作台

poliscope/     CLI 入口点
migrations/    Alembic：建表 + 建角色 + 授权
scripts/       开发辅助（演示任务播种）
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

按「离核心闭环的距离」排序。

**阻塞真实使用：**

1. **模型网关无真实厂商凭证。** `OpenAICompatibleModelGateway`（`packages/models/openai_compatible.py`）已实现并接入 `apps/worker`，对接任意 DeepSeek / LongCat / 国内中转站等 OpenAI-Chat-Completions 兼容端点；仅经 `httpx.MockTransport` 单元测试验证，尚未填入真实 `POLISCOPE_MODEL_API_KEY` 跑通一次真实调用。
2. **工具网关无真实厂商凭证。** `HttpToolGateway`（`packages/tools/http_gateway.py`）已实现并接入 `apps/worker`，直接调用 OpenAlex / Crossref / Unpaywall / Semantic Scholar 的真实公开 API；OpenAlex / Crossref / Semantic Scholar 无需凭证，仅 Unpaywall 需要 `POLISCOPE_TOOLS_CONTACT_EMAIL`。同样仅经 `httpx.MockTransport` 单元测试验证，尚未打过真实网络。

**影响证据质量：**

4. **`packages/evidence/lifecycle.py`、`consistency.py`、`repository.py` 无生产调用者。**
5. ~~盲证据评审、独立双抽取、来源多样性约束、对抗式检索四种防止「共享证据错误」的机制尚未实现。~~ **已解决，但四个子机制的完成度不对称，见下方说明。**
6. ~~`Fork`、`Merge`、`Resurrect` 三个新增记忆操作尚未实现。~~ **已解决，但三条路径的完成度不对称，见下方说明。**

> 原第 4、5 项（Dialectical Fold 未接线；DebateCapsule 与 DissentCertificate 未产出）已解决：`run_joint_modeling` 和 `run_final_rejudgment`（`packages/council/rounds/registry.py`）现在分别产出 `DebateCapsule` 和 `DissentCertificate` 事件，前端「少数意见与异议」面板不再恒为空。同时修复了一个此前未记录的 bug：`apps/api/routers/workspace.py` 与 `packages/reports/service.py` 的 `dissents` 字段此前都错误地查询 `DebateCapsule` 节点类型，现已改为查询 `DissentCertificate`。
>
> 原第 5 项（四种防「共享证据错误」机制）**已全部接入主流程，但完成度不对称，按设计规格 §7.9 分别说明：**
> - **来源多样性约束——完整闭环。** `packages/evidence/source_diversity.py` 检查每个 Claim 的全部支持证据是否共享同一 `dataset_id`/`authors`，触发时自动产出一条 `evidence_level="A"` 的 `Blindspot` 事件（标注「来源单一」），挂在议会轮次末尾，端到端验证过。
> - **盲证据评审——完整闭环。** `packages/council/deliberation.py` 构造 ACQUISITION/EVIDENCE_EXCHANGE 阶段 prompt 时结构性去掉 author/journal/citation_count 字段，是一个恒定生效的denylist守卫，不依赖某次运行是否「刚好」触发。
> - **独立双抽取——完整闭环。** `FindingExtractor.extract(dual_extraction=True)`（`packages/papers/finding_extraction.py`）对 Level A 候选跑两次独立抽取，比较 `exact_quote`/`effect_direction`；任一不一致都记为「需人工审计」的 gap，不自动裁决哪一次对。四条路径（一致通过、引用不一致、效应方向不一致、第二次抽取本身失败）均有单测覆盖。
> - **对抗式检索——只做到意图生成，不做到真实检索。** `packages/evidence/adversarial_retrieval.py` 为每个 confirmed claim 生成 6 类反向检索意图字符串（反驳、零结果、替代理论、测量批评、复现失败、边界反转），由 `run_acquisition` 无条件追加、归属给 `adversarial_falsifier` 席位。**但目前系统没有任何真正的全文搜索适配器**——`CandidatePool.add` 只能解析 DOI 形态的字符串，这六类查询本身是自由文本，所以在生产环境里**目前恒定无法解析到真实来源**。这不是遗漏，是设计规格 §7.9 本身声明的范围：意图生成是这版机制的全部。为了不让这个恒定的、系统级的适配器缺口每次都被误计成某个具体任务的证据缺口（那样会让 `COMPLETED_WITH_GAPS` 对所有有已确认主张的任务永久成立，这个状态就失去意义了），这些查询的解析结果**不计入** `unfilled_slots`，而是通过一条独立的 `ADVERSARIAL_RETRIEVAL_ATTEMPTED` 事件记录「尝试数/已解析数/未解析数」，保持在审计轨迹上可见——CLAUDE.md 第 7 条「承认未知」用可见性实现，而不是用缺口计数实现。真正让这一机制发挥作用，需要接入一个真正的全文检索适配器（例如 Semantic Scholar 的语义检索、Google Scholar 或类似服务），目前不在本版范围内。
>
> 原第 6 项（`Fork`、`Merge`、`Resurrect`）**已全部接入主流程，但完成度不对称：**
> - **`Resurrect`——完整闭环。** `packages/evidence/lifecycle.py::check_resurrection_conditions()` 接入 `run_evidence_exchange`（`packages/council/rounds/registry.py`）：席位在证据交换阶段自报「哪个被隔离节点、什么新证据」，一旦满足该节点记录的复活条件即产出 `RESURRECTION_GRANTED` 事件——这是一次状态变更，不是新的正式图节点类型，因此只留痕在事件账本上，不写入 `graph_nodes`。畸形或指向未知节点的复活请求会被记为 `unfilled_slots`，不会静默丢弃（CLAUDE.md 第 7 条）。`tests/integration/test_resurrect_pipeline.py` 端到端验证了真实被网关隔离的 Claim（因果主张建立在横截面证据上）经由完整 `run_task` 复活的全链路。
> - **`Fork`——图层完整闭环，但独立于 `packages/memory/branches.py::BranchService`。** `run_cross_examination` 里，一个席位自报为致命（`is_fatal: True`）且附带 `fork` 子结构的质询，会额外产出两个 `Claim` 事件：一个指向原主张的锚点（仅用于让 `CONTRADICTS` 边有落点），一个平行的新主张，用既有的 `CONTRADICTS` 边关联（不新增边类型，YAGNI）。新主张的 id 用 `uuid5` 从任务、原主张、席位、序号确定性派生，保证重放/续跑不会重复分叉。这条路径完全绕开了 `BranchService`——那是一套纯内存、无持久化路径的 `fork()`/`merge()` 记录类，目前仍然没有生产调用者，按既定原则（死代码接线而非删除）予以保留；图层的分叉是靠直接产出 `Claim`/`CONTRADICTS` 事件实现的，不经过它。
> - **`Merge`——裁剪为「记录合并候选」，不做自动合并。** `run_joint_modeling` 产出的 `CONSENSUS_DRAFTED` 事件新增 `merge_candidates` 字段，把该轮的全部 `unresolved_conflicts` 原样列为候选；没有任何代码执行真正的合并——按 CLAUDE.md 第 8 条「研究者控制方向」，合并与否是人在前端做的判断，不是系统自动裁决。
>
> 原第 3 项（证据谱系边没有落库）**部分解决，不对称**：`sources` 表新增了 `authors`（JSONB）与 `dataset_id`（可空字符串）两列（迁移 `0006_source_lineage_fields`），`packages/evidence/lineage_detection.py` 新增 `detect_lineage()` 统一取代此前 `reports/service.py` 与 `workspace.py` 里重复的内联 DOI 拼接逻辑。`authors` 是**端到端真实接线**：OpenAlex/Crossref 适配器本就返回作者列表，此前只是在 `SourceAcquisition._persist()` 里被丢弃，现在会落库并驱动 `SAME_RESEARCH_TEAM` 边（只标注，不合并独立证据簇计数，见 CLAUDE.md 7.4）。`dataset_id` 是**仅完成建模、未接通真实数据**：目前没有任何适配器能从 DOI 查询解析出数据集标识符，这一列和 `SAME_DATASET` 检测逻辑只经过合成数据的单元测试验证，生产环境中每一行的 `dataset_id` 实际上恒为 `NULL`，等一个真正能提取数据集标识（例如论文 Data Availability 声明）的抽取路径接入之后才会真正发挥作用。

**尚未开始：**

9. ~~ForesightBlindspot 评测基准。语料与验收矩阵的骨架在 `packages/evaluation/`，五个对照组和消融实验都还没跑。~~ **已解决，但完成度不对称，见下方说明。**
10. **Evolution View 与 Blindspot Radar。** 工作台快照里 `evolution` 和 `seats` 恒为空数组。
11. **快照 / 暂停 / 恢复的端到端路径。** `CouncilMemory.snapshot/restore` 与 `restore_task_state` 都有实现和测试，但没有暴露成 API 或 CLI 操作。

> 原第 9 项（ForesightBlindspot 评测基准）**已接入，但完成度不对称：**
> - **五个对照组——完整闭环。** `packages/evaluation/harness.py::run_baseline()` 支持全部 `BaselineVariant`（Single-Agent Deep Research、Fixed Multi-Agent Debate、Council + Linear Context、Council + MemoBrain 无 Evidence Engine、完整 Poliscope），每一级只比上一级多打开一个能力（席位数、prompt 专业化、`SharedLinearMemoryAdapter` 强制共享记忆、`FullEvidenceGate` 有无），且用无数据库依赖的 `EvalLedger` 跑通，`tests/unit/test_evaluation_harness.py` 逐条断言了这些差异确实存在，而不只是「跑起来了」。
> - **打分函数——完整闭环，代码可计算部分。** `packages/evaluation/scoring.py` 提供 `score_blindspots`（Recall/Precision）、`score_citation_entailment`、`score_evidence_independence`、`score_dissent_preservation`、`cost_per_valid_blindspot`，全部复用生产环境本身的判定规则（`verify_citation_entailment`、`detect_lineage`/`cluster_evidence`、`CausalUpgradePolicy`），不重新发明一套平行标准。
> - **`score_causal_overclaim`——在当前生产接线下恒为 `None`，这是一个真实存在、尚待解决的缺口，不是评测框架本身的缺陷。** 追踪到根源：全仓库唯一产出 `Claim` 事件的路径是 `packages/council/rounds/registry.py::_fork_events`（Fork 机制），它硬编码 `claim_type="correlational"`，从未设置 `study_design` 字段——也就是说没有任何一条生产路径会产出因果类型的 `Claim` 事件，无论跑哪个基线。`tests/unit/test_evaluation_demo_case.py` 的端到端演示案例对此显式断言 `score_causal_overclaim(...) is None` 并注释了原因，而不是绕开真实编排去手造一个生产环境不可能产出的因果 Claim 事件来让分数看起来能用。修复需要给 Fork 之外再补一条能识别并标记因果主张 `study_design` 的路径，目前不在本版范围内。
> - **人工标注 Kappa/Alpha——只搭了统计骨架，没有标注数据。** `packages/evaluation/agreement.py` 里 `cohen_kappa`/`krippendorff_alpha_nominal` 两个统计函数本身完整、已用合成数据单测验证；但 `load_human_annotations()` 按 CLAUDE.md 第 7 条故意抛出 `HumanAnnotationsNotCollected`，因为目前没有任何标注 UI、招募或培训流程——这是独立于本模块的产品工作，不是一个缺失的公式。
> - **端到端演示案例——1 个，覆盖完整 Poliscope 基线。** `tests/unit/test_evaluation_demo_case.py` 用脚本化的 `ModelGateway`/`SourceAcquirer`/`FindingExtractor`（复用 `scripts/seed_demo_task.py` 与既有单测已验证过的三方 fake 模式）跑通一次完整的 8 阶段议会，验证 Blindspot Recall/Precision、Citation Entailment、Evidence Independence、Dissent Preservation 四项打分都能从一次真实（脚本化）议会运行中算出非平凡的值，而不是只喂给打分函数手造的 `LedgerEntry`。
> - **尚未做到的部分：** 真实厂商模型网关 + 真实论文语料的时间切片评测语料尚未策划（受限于[第 1、2 项](#已知缺口)本身尚缺真实凭证）；五个基线之间的正式对比报告/消融实验表尚未作为一次真实实验跑出来记录——框架支持（`BaselineVariant` 枚举本身就是消融维度），但目前只有单测层面验证了「每一级确实不同」，没有产出一份实际的对照数字。

---

## 安全与伦理

- 系统输出**不是**临床诊断或医疗建议。涉及心理健康的问题会自动附加科研辅助定位声明——只在相关领域附加，全篇都贴会训练读者跳过它。
- 不抓取或存储未授权的个人数据，不绕过付费墙或访问控制。
- 导出会脱敏签名 URL 与本地路径，避免上传材料通过报告泄露。
- 报告始终声明 AI 辅助、证据覆盖与系统局限。
- **不以模型置信度替代统计不确定性或专家判断。** 这句话出现在每一份报告的局限一节，不是免责套话——它是这个系统全部设计取舍的出发点。

---

## 许可证与归属

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

其中 Dialectical Fold、Dissent Certificate 与 Fork/Merge/Resurrect 已接入主流程（完成度不对称，见上文）；Evidence Lineage Graph 部分接入（`authors` 端到端真实接线，`dataset_id` 仅完成建模）；ForesightBlindspot 五个基线与打分函数已接入并有端到端演示案例验证，但因果过度推断打分恒为 `None`、真实语料评测与消融实验尚未跑（完成度不对称，见上文）。

---

> **一句话总结**：Poliscope 不是更快的文献综述机器，而是一面让你看见证据裂缝的科研透镜——包括它自己的裂缝。
