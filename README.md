# Poliscope

> 面向计算社会科学争议问题的可审计深度研究智能体

Poliscope 组织 7 名具有互补认识能力的 AI 科学家，全程参与问题拆解、独立判断、论文取证、交叉质询、盲点调查与最终复判，产出一张**可审计的争议证据地图**。

它不追求更长的文献综述。它回答的是：**当前结论最可能错在哪里？哪些盲点、反例、边界条件或证据依赖会改变科研判断？**

核心方法名为 **EpistemoBrain**。设计规格见 [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)。

---

## 目录

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

**尚未接入真实供应商的：**

- **模型网关没有任何厂商实现。** 系统不会因此假装科学家发言了——它把每个席位记为「缺席」，任务以 `COMPLETED_WITH_GAPS` 结束，并在报告的「局限与未知」中逐条列出未填的证据槽位。接一个 `ModelGateway` 实现即可，其余代码不用改。
- **工具网关同理。** 没有工具供应商时，取证轮只记录请求，不会凭空生成 Source 节点。
- **全文解析与 StudyFinding 抽取未接线。** 因此目前只能产出 Level B（仅元数据）的 Source，产不出 Level A 的 StudyFinding，证据图上也就没有 `DERIVED_FROM` 边。

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
- **Evidence Graph**：9 种节点、12 种边。只有 Graph Projector 能写。

节点：`ResearchQuestion`、`Claim`、`Source`、`StudyFinding`、`Construct`、`Context`、`Blindspot`、`DebateCapsule`、`DiscriminatingStudy`。

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

1. **模型网关无厂商实现。** 现状是每个席位都记为缺席。需要一个实现 `ModelGateway` 协议的类；`GatewayDeliberator` 和其余代码不用改。
2. **工具网关无真实数据源实现。** 四个适配器（OpenAlex / Crossref / Unpaywall / Semantic Scholar）都已写好并走网关，但网关本身只有录制回放实现。
3. **全文解析与 StudyFinding 抽取未接线。** `packages/papers/` 的 parser 与 packet 存在但没有调用者，所以拿不到 Level A 证据，也就没有引用锚点。

**影响证据质量：**

4. **证据谱系边没有落库。** 独立证据簇目前只能按 canonical DOI 合并，是独立性的**上界**。数据集复用、样本重叠、团队重叠需要一张 lineage 表。
5. **Dialectical Fold 未接线。** `packages/evidence/dialectical_fold.py` 有实现和测试，但编排器不调用它。
6. **DebateCapsule 与 DissentCertificate 未产出。** 因此前端「少数意见与异议」面板目前恒为空。
7. **`packages/evidence/lifecycle.py`、`consistency.py`、`repository.py` 无生产调用者。**

**尚未开始：**

8. **ForesightBlindspot 评测基准。** 语料与验收矩阵的骨架在 `packages/evaluation/`，五个对照组和消融实验都还没跑。
9. **Evolution View 与 Blindspot Radar。** 工作台快照里 `evolution` 和 `seats` 恒为空数组。
10. **快照 / 暂停 / 恢复的端到端路径。** `CouncilMemory.snapshot/restore` 与 `restore_task_state` 都有实现和测试，但没有暴露成 API 或 CLI 操作。

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

其中 Dialectical Fold、Fork/Merge/Resurrect、Dissent Certificate、Evidence Lineage Graph 与 ForesightBlindspot 目前尚未接入主流程，见[已知缺口](#已知缺口)。

---

> **一句话总结**：Poliscope 不是更快的文献综述机器，而是一面让你看见证据裂缝的科研透镜——包括它自己的裂缝。
