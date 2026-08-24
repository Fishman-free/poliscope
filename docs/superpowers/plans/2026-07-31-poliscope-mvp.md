# Poliscope MVP 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 8 周内构建可审计、可恢复、可 Replay 的 Poliscope MVP，用 7 人科学议会、双图证据治理和异议保真机制发现普通研究智能体容易遗漏的高价值科研盲点。

**架构：** 采用模块化单体与异步 Worker。PostgreSQL 保存任务、调用审计、Scientific Event Ledger、双图和恢复点；Redis 只承载 arq 队列与通知；S3 兼容对象存储保存受控 PDF；所有模型和外部数据源分别通过 Model Gateway 与 Tool Gateway；Evidence Gate 在任何首次正式图投影之前完成，Graph Projector 使用独立数据库角色顺序写 Evidence Graph。

**技术栈：** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL、Redis、arq、PyMuPDF、MinIO、pytest、pytest-asyncio、pytest-repeat、Testcontainers、Ruff、mypy、React、TypeScript、Vite、React Flow、TanStack Query、Zustand、Vitest、Testing Library、MSW、Playwright、npm。

**权威规格：** `docs/superpowers/specs/2026-07-31-poliscope-design.md`

---

## 0. 已锁定的实施决策

1. 本计划共 33 个可独立提交的任务，按 8 周执行；每个任务都执行“测试 → 确认目标性失败 → 最小实现 → 精确验证与回归 → 单一提交”。
2. 不引入向量扩展。MVP 的检索合并、语义去重和角色化 Evidence Projection 使用规范化标识、结构化字段、确定性规则与 Recorded Gateway 响应；没有已定义且经测试的向量检索需求。
3. PostgreSQL 集成测试使用 Testcontainers，不能用 SQLite 证明 Alembic、外键、锁、数据库角色或权限。
4. 数据库使用三个连接身份：迁移管理员 `poliscope_migrator`、普通应用 `poliscope_app`、唯一图投影者 `poliscope_projector`。API、Worker 和测试业务夹具默认使用普通应用身份；只有迁移容器和 Projector 分别持有另外两类凭据。
5. 自动化测试不访问公网。Live 适配器有 Recorded 对照测试；Golden、Replay、评测和演示只使用 `RecordedModelGateway` 与 `RecordedToolGateway`。
6. `ContractModel` 只定义在 `packages/kernel/contracts.py`。所有公开 Contract 使用 `tuple`、`frozenset` 与只读 `FrozenDict`，不能以 Pydantic 的 `frozen=True` 代替容器不可变性。
7. Evidence Graph 精确支持 9 种节点与 12 种边；`DiscriminatingStudy` 属于 `packages/evidence`，报告与前端只消费其只读投影。
8. Level A–D 准入矩阵由 Evidence Gate 执行：A 可在完整审核通过后形成正式 Finding；B 只允许正式 Source 与候选 Finding，不产生正式 Finding→Claim 证据边；C 只进入原始研究发现队列；D 只保留工具线索，不能进入 Evidence Graph。
9. 前端开始前必须冻结 Workspace HTTP/SSE Contract 并提供 MSW mock；真实跨端 E2E 只能在后端路由与 SSE 可运行后加入。
10. 前端使用 Vite 与 npm，提交 `apps/web/package-lock.json`；全新环境使用 `npm ci`，Playwright 浏览器使用 `npx playwright install --with-deps chromium`。
11. `pytest-repeat` 是测试依赖，连续 Replay 命令使用其 `--count=2` 参数；验收前用 `python -m pytest --help` 验证插件已加载。
12. Compose 由一次性 `migrate` 服务执行 `alembic upgrade head`；API 和 Worker 等待迁移成功且 PostgreSQL、Redis、MinIO 健康，应用启动代码不隐式建表。
13. 所有 Alembic 迁移只放在 `migrations/versions/`，由创建对应 SQLAlchemy 模型的任务同时负责；领域服务不得在运行时执行 DDL。
14. 上传 PDF 使用私有 bucket、任务级对象前缀、服务端加密标记、短期签名 URL、内容哈希与最小化日志；任何日志、SSE、报告和导出均不得包含 PDF 二进制、签名 URL、用户文件系统路径或未授权原文全文。
15. 产品固定显示：AI 辅助科研工具、不是临床诊断或医疗建议、模型置信度不替代统计不确定性或专家判断、证据覆盖与系统局限。
16. 前端体验采用 Apple Design 的目的、能动性、责任、熟悉、灵活、简洁与工艺原则，但产品定位始终是桌面科研仪器和证据审查工具：不做消费级聊天产品，不使用卡通 Agent、霓虹驾驶舱或装饰动画。交互在 `pointerdown` / `active` 即时反馈，可随时打断，并以触发源、进入路径和退出路径保持空间一致；默认使用 critically damped、`bounce = 0`、`response ≈ 0.35 s` 的运动，只有连续动量拖拽允许轻微 bounce，禁止无因弹跳。界面分别尊重 `prefers-reduced-motion`、`prefers-reduced-transparency` 与 `prefers-contrast`；动画只表达操作反馈和空间关系，不得创建、修改、批准、删除或重排任何证据状态，也不得把视觉突出程度解释为证据强度。
17. 最终产品同时提供 Web 科研工作台、稳定 HTTP/SSE API、Poliscope CLI、Claude Code Skill 和 Codex Skill。Web 仍是完整交互主界面；CLI 与两个 Skill 是调用适配层，必须共享任务 23 冻结的应用 Contract，不复制 7 人议会或证据治理逻辑，不直接调用模型/工具、不直接写数据库或 Evidence Graph，也不能绕过原子主张确认、Evidence Gate、安全声明和审计。

## 1. 目标文件结构与职责

```text
.
├─ pyproject.toml                         # Python 依赖与 pytest/Ruff/mypy 配置
├─ alembic.ini                            # Alembic 入口
├─ docker-compose.yml                     # migrate、PostgreSQL、Redis、MinIO、API、Worker、Web
├─ .env.example                           # 三类数据库凭据和对象存储配置名
├─ apps/
│  ├─ bootstrap.py                        # 唯一依赖组合根
│  ├─ api/
│  │  ├─ main.py                          # FastAPI 应用与健康检查
│  │  ├─ schemas.py                       # HTTP/SSE DTO；不包含私有推理
│  │  └─ routers/
│  │     ├─ tasks.py                      # Research Contract、原子主张、运行控制
│  │     ├─ sources.py                    # DOI/BibTeX/PDF 输入
│  │     ├─ workspace.py                  # Workspace、图、审计只读接口
│  │     ├─ reports.py                    # Markdown/JSON 报告
│  │     └─ stream.py                     # 可恢复 SSE
│  ├─ worker/
│  │  ├─ main.py                          # arq Worker 配置
│  │  └─ jobs.py                          # 长任务入口
│  ├─ cli/
│  │  ├─ main.py                          # research start/confirm/status/watch/export
│  │  └─ client.py                        # 仅调用稳定 HTTP/SSE Contract
│  └─ web/
│     ├─ package.json / package-lock.json # npm 可复现依赖
│     ├─ vite.config.ts                   # Vite/Vitest
│     ├─ playwright.config.ts             # Chromium E2E
│     ├─ src/api/                         # DTO、HTTP、SSE、MSW
│     ├─ src/features/                    # Research、Brief、Council、Map、Audit、Blindspot、Lineage、Evolution
│     └─ e2e/                             # 真实跨端 Playwright
├─ packages/
│  ├─ kernel/                             # ContractModel、配置、数据库、时钟、错误
│  ├─ research/                           # Research Contract 与任务生命周期
│  ├─ models/                             # Model Gateway、Recorded 实现、模型调用审计
│  ├─ tools/                              # Tool Gateway、Recorded 实现、数据源适配器、工具调用审计
│  ├─ papers/                             # Source、Study、PaperEvidencePacket、PDF 与对象存储
│  ├─ evidence/                           # 9/12 图契约、Ledger、Gate、Projector、Lineage、Fold、判别研究
│  ├─ memory/                             # MemoBrain Adapter、Process Graph、Recall、快照
│  ├─ council/                            # 7 席、动作、7 轮处理器、质询与复判
│  ├─ epistemo/                           # 状态机、预算、编排、Blindspot、停止条件、恢复
│  ├─ reports/                            # Brief 与审计报告的只读生成器
│  └─ evaluation/                         # 封闭语料、变体、标注、指标和统计
├─ migrations/versions/                   # 仅 Alembic 管理的 Schema 与权限迁移
├─ .claude/skills/poliscope/               # Claude Code 实际发现路径（原计划的裸 skills/ 目录
│  ├─ SKILL.md                             # 不会被 Claude Code 自动发现，已按官方约定改为此路径）
│  └─ scripts/new_contract.py
├─ .codex/skills/poliscope/                # Codex 对应发现路径，内容与上面保持一致，避免逻辑分叉
│  ├─ SKILL.md
│  └─ scripts/new_contract.py
├─ tests/
│  ├─ fixtures/recordings/                # 模型/工具录制响应
│  ├─ unit/                               # 纯规则与 Contract
│  ├─ integration/                        # PostgreSQL、权限、对象存储、API、Worker
│  ├─ golden/                             # 固定抽取、因果与 Fold
│  ├─ e2e/                                # 后端全流程、Replay、Compose
│  └─ evals/                              # ForesightBlindspot
└─ demo/
   ├─ full/                               # 3 个完整精标案例
   ├─ light/                              # 5 个轻量案例
   └─ core_case/                          # 1 个演示案例、Replay 与固定快照
```

### 1.1 依赖方向

```text
kernel ← research / models / tools / memory
kernel + tools ← papers
kernel + research + papers ← evidence
kernel + models + tools + papers + evidence + memory ← council
research + council + memory + evidence + models + tools ← epistemo
research + council + papers + evidence ← reports
完整应用服务 ← apps/bootstrap.py ← API / Worker
冻结的 HTTP/SSE DTO ← Web；Web 不导入 Python 领域代码
完整系统只读接口 ← evaluation
```

禁止：`council`、`papers`、`memory` 直接写 Evidence Graph；角色代码直接调用厂商 SDK/HTTP；报告重新审核证据；React 计算科研结论；任何 API 绕过 Evidence Gate；Process Fold 修改 Evidence Graph；普通应用数据库角色写 `graph_nodes` 或 `graph_edges`。

## 2. 共享测试辅助与公共 Contract

本节锁定后续任务使用的名称和签名。任务 1–7 按依赖顺序实现它们；后续任务不得另造同义类型。

### 2.1 真正不可变的基础类型

```python
# packages/kernel/contracts.py
from collections.abc import Iterator, Mapping
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, model_validator

K = TypeVar("K")
V = TypeVar("V")

def freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_value(item) for item in value)
    return value

class FrozenDict(Mapping[K, V], Generic[K, V]):
    def __init__(self, values: Mapping[K, V] | None = None) -> None:
        self._values = {key: freeze_value(value) for key, value in (values or {}).items()}
    def __getitem__(self, key: K) -> V:
        return self._values[key]
    def __iter__(self) -> Iterator[K]:
        return iter(self._values)
    def __len__(self) -> int:
        return len(self._values)
    def __hash__(self) -> int:
        return hash(tuple(sorted(self._values.items(), key=lambda item: repr(item[0]))))

class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    @model_validator(mode="before")
    @classmethod
    def freeze_containers(cls, value: Any) -> Any:
        return freeze_value(value)
```

所有公共序列字段声明为 `tuple[T, ...]`，集合声明为 `frozenset[T]`，映射声明为 `FrozenDict[K, V]`。`freeze_value()` 递归冻结任意深度的 list/set/dict；测试必须证明模型属性、嵌套序列和嵌套映射均不能原位修改。

### 2.2 Research Contract

```python
# packages/research/contracts.py
class ResearchScope(ContractModel):
    populations: tuple[str, ...]
    regions: tuple[str, ...]
    languages: tuple[str, ...]
    date_from: date | None
    date_until: date
    evidence_priorities: tuple[EvidenceDemandType, ...]
    allow_preprints: bool

class ResearchBudget(ContractModel):
    wall_clock_minutes: int = Field(gt=0)
    model_cost_usd: Decimal = Field(ge=0)
    tool_call_limit: int = Field(gt=0)
    source_limit: int = Field(gt=0)

class UserEvidenceInput(ContractModel):
    dois: tuple[str, ...] = ()
    bibtex_entries: tuple[str, ...] = ()
    pdf_object_ids: tuple[UUID, ...] = ()

class ResearchContract(ContractModel):
    question: str = Field(min_length=1)
    scope: ResearchScope
    budget: ResearchBudget
    user_evidence: UserEvidenceInput
```

`EvidenceDemandType` 固定为 `CORRELATION`、`CAUSAL_OR_REVERSE_CAUSAL`、`MEASUREMENT`、`REPLICATION`、`BOUNDARY`、`MECHANISM`、`NULL_OR_COUNTEREXAMPLE`。

### 2.3 Gateway 与调用审计

```python
# packages/models/contracts.py / packages/tools/contracts.py
class ModelRequest(ContractModel):
    task_id: UUID
    actor: str
    purpose: str
    model_class: ModelClass
    messages: tuple[ModelMessage, ...]
    output_schema: str
    evidence_refs: tuple[UUID, ...]

class ModelResult(ContractModel):
    call_id: UUID
    payload: FrozenDict[str, object]
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    retries: int
    schema_status: SchemaStatus

class ModelGateway(Protocol):
    async def invoke(self, request: ModelRequest) -> ModelResult: ...

class ToolRequest(ContractModel):
    task_id: UUID
    actor: str
    tool_name: str
    operation: str
    arguments: FrozenDict[str, object]

class ToolResult(ContractModel):
    call_id: UUID
    payload: FrozenDict[str, object]
    latency_ms: int
    retries: int
    error_code: str | None

class ToolGateway(Protocol):
    async def execute(self, request: ToolRequest) -> ToolResult: ...
```

`model_calls` 与 `tool_calls` 保存输入哈希、输出哈希、延迟、费用、重试、错误、输入证据引用及 Schema 状态；不保存模型私有思维链、PDF 二进制或签名 URL。

### 2.4 PaperEvidencePacket

```python
# packages/papers/contracts.py
class StudyDesign(StrEnum):
    CROSS_SECTIONAL = "cross_sectional"
    LONGITUDINAL = "longitudinal"
    EXPERIMENTAL = "experimental"
    QUASI_EXPERIMENTAL = "quasi_experimental"
    QUALITATIVE = "qualitative"
    META_ANALYSIS = "meta_analysis"
    OTHER = "other"

class SampleDescription(ContractModel):
    size: int | None
    population: str
    recruitment: str | None
    regions: tuple[str, ...]
    age_range: str | None

class VariableSpec(ContractModel):
    name: str
    construct: str
    role: Literal["exposure", "outcome", "mediator", "moderator", "confounder", "covariate"]
    operationalization: str

class AnalysisSpec(ContractModel):
    method: str
    estimand: str | None
    adjustments: tuple[str, ...]
    sensitivity_analyses: tuple[str, ...]

class EffectEstimate(ContractModel):
    direction: Literal["positive", "negative", "null", "mixed", "not_reported"]
    measure: str | None
    value: Decimal | None
    uncertainty_type: str | None
    uncertainty_lower: Decimal | None
    uncertainty_upper: Decimal | None
    p_value: Decimal | None

class ResearchArtifactStatus(ContractModel):
    data: AvailabilityStatus
    code: AvailabilityStatus
    preregistration: AvailabilityStatus
    urls: tuple[str, ...]

class CitationAnchor(ContractModel):
    source_version_id: UUID
    source_version_hash: str
    section: str | None
    page: int | None
    locator: str
    exact_quote: str
    extraction_agent: str
    verification_status: VerificationStatus

class StudyFindingCandidate(ContractModel):
    id: UUID
    statement: str
    origin: AssertionOrigin
    effect: EffectEstimate
    anchors: tuple[CitationAnchor, ...]
    author_interpretation: str | None
    ai_derivation_creator: str | None

class StudyPacket(ContractModel):
    id: UUID
    research_question: str
    sample: SampleDescription
    design: StudyDesign
    variables: tuple[VariableSpec, ...]
    analysis: AnalysisSpec
    findings: tuple[StudyFindingCandidate, ...]
    author_conclusions: tuple[str, ...]
    author_limitations: tuple[str, ...]
    artifacts: ResearchArtifactStatus

class PaperEvidencePacket(ContractModel):
    source: NormalizedSource
    source_version: SourceVersion
    studies: tuple[StudyPacket, ...]
    pages: tuple[ParsedPage, ...]
    evidence_level: EvidenceLevel
```

### 2.5 Evidence Graph、Gate 与 Claim revision

```python
class EvidenceNodeType(StrEnum):
    RESEARCH_QUESTION = "ResearchQuestion"
    CLAIM = "Claim"
    SOURCE = "Source"
    STUDY_FINDING = "StudyFinding"
    CONSTRUCT = "Construct"
    CONTEXT = "Context"
    BLINDSPOT = "Blindspot"
    DEBATE_CAPSULE = "DebateCapsule"
    DISCRIMINATING_STUDY = "DiscriminatingStudy"

class EvidenceEdgeType(StrEnum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    QUALIFIES = "QUALIFIES"
    CONTRADICTS = "CONTRADICTS"
    CONFOUNDS = "CONFOUNDS"
    MEDIATES = "MEDIATES"
    MODERATES = "MODERATES"
    OPERATIONALIZES = "OPERATIONALIZES"
    DERIVED_FROM = "DERIVED_FROM"
    APPLIES_IN = "APPLIES_IN"
    EXPOSES = "EXPOSES"
    TESTS = "TESTS"

class ClaimRevision(ContractModel):
    claim_id: UUID
    revision: int = Field(ge=1)
    statement: str
    claim_type: ClaimType
    scope: ResearchScope
    confidence: Decimal = Field(ge=0, le=1)
    falsification_condition: str
    supersedes_revision: int | None
    status: ClaimStatus
```

修订 Claim 时追加新 revision，原 revision 不更新、不删除；`NARROW` 必须产生严格收窄的 scope；`WITHDRAW` 追加撤回 revision。正式 `StudyFinding` 节点必须有且仅有一条出边 `DERIVED_FROM` 指向 `Source`，Projector 在同一事务内写节点与该边。

A–D 准入矩阵：

| 等级 | 条件 | 可写 Evidence Graph | 可支撑 Claim | 降级触发 |
|---|---|---|---|---|
| A | 全文、精确原文、位置、来源身份与三层审核通过 | Source、StudyFinding 及合法关系 | 可，仍受 claim_type、scope 和因果规则约束 | Anchor 冲突、版本不匹配或方法审核阻断时降至 B/Quarantine |
| B | 摘要与可靠元数据，全文或精确 Anchor 不可得 | 仅 Source；Finding 保持候选状态 | 不可形成正式 Finding→Claim 边，不可单独支撑高置信因果 | 发现仅来自综述时降至 C |
| C | 综述中的二手描述 | 不可；进入 `primary_source_requests` | 不可 | 无法定位原始研究时保持 C 并显式未满足 |
| D | 网页、新闻或普通搜索线索 | 不可；只留 `tool_calls` 与候选线索 | 不可 | 不自动升级；找到 DOI/原文后创建新的 B/A 候选 |

### 2.6 7 轮协议与状态

每轮统一实现：

```python
class RoundHandler(Protocol):
    phase: TaskPhase
    input_schema: type[ContractModel]
    output_schema: type[ContractModel]
    async def enter(self, context: RoundContext) -> RoundEntry: ...
    async def run(self, entry: RoundEntry) -> RoundResult: ...
    async def complete(self, result: RoundResult) -> CompletionDecision: ...
    async def on_timeout(self, entry: RoundEntry) -> TimeoutDecision: ...
```

| 轮次 | Handler | 输入/输出 | 进入条件 | 完成条件 | 超时结果 |
|---|---|---|---|---|---|
| 独立预承诺 | `PrecommitmentHandler` | `PrecommitmentInput/PrecommitmentOutput` | 原子主张已确认，7 私有记忆已初始化 | 7 席各有提交或显式失败记录，提交统一 Seal | 缺席记 `SEAT_TIMED_OUT`，任务进入 `DEGRADED_RUNNING` 并保留缺口 |
| 专业取证 | `EvidenceAcquisitionHandler` | `AcquisitionInput/AcquisitionOutput` | 预承诺已 Seal | 每席 Evidence Request 已结算，候选池去重完成 | 停止该席低优先请求，记录未填 Evidence Slot |
| 证据交换 | `EvidenceExchangeHandler` | `ExchangeInput/ExchangeOutput` | 候选池有已解析包或明确空结果 | 只发布结构化证据投影，禁止私有 episode | 隔离未完成交换，其他席继续 |
| 交叉质询 | `CrossExaminationHandler` | `ChallengeInput/ChallengeOutput` | 交换快照已冻结 | 每个致命质询有 DEFEND/REVISE/NARROW/WITHDRAW/DISSENT 或未解决状态 | 未回应致命攻击阻断对应 Claim 准入 |
| 盲点悬赏 | `BlindspotBountyHandler` | `BountyInput/BountyOutput` | 质询结果已持久化 | 五维分数与调查 Assignment 已生成 | 使用已验证输入评分，缺失项标注未调查 |
| 联合建模 | `JointModelingHandler` | `JointModelInput/JointModelOutput` | Blindspot 和质询快照存在 | 条件化共识、正反、铰链变量、边界、冲突、证伪条件齐全 | 不生成虚假共识，输出未完成 DebateCapsule 候选 |
| 最终独立复判 | `FinalRejudgmentHandler` | `FinalRejudgmentInput/FinalRejudgmentOutput` | 联合模型只读快照已冻结 | 7 席分别给出最终判断、证据驱动更新、置信度与 Dissent/缺席记录 | 缺席保留初始判断并标 `NO_FINAL_REJUDGMENT`，任务完成状态为 `COMPLETED_WITH_GAPS` |

运行状态精确区分：`DRAFT`、`AWAITING_CLAIM_CONFIRMATION`、`QUEUED`、7 个轮次状态、`REPORTING`、`COMPLETED`、`DEGRADED_RUNNING`、`COMPLETED_WITH_GAPS`、`PAUSED`、`FAILED`。`DEGRADED_RUNNING` 是可继续的阶段状态，`COMPLETED_WITH_GAPS` 只能是终态。

### 2.7 Process Graph 与分支操作

`ProcessNodeType` 包含上游兼容的 `TASK/SUBTASK/EVIDENCE/SUMMARY` 和 Poliscope 扩展 `DEBATE/DECISION/ASSIGNMENT`。`PerspectiveRecall` 返回 `RoleContext(process_recall, evidence_projection)`；7 席使用同一正式图但不同 `ProjectionPolicy`，不能读到其他席 Private MemoBrain。

`Fork` 创建平行 `branch_id` 与竞争解释；`Merge` 只在提供条件变量、两分支引用和合并后的条件化主张时追加 `MERGE_DECISION`；`Resurrect` 只在新 Evidence Ref 满足原复活条件时追加事件。三者均保留原节点和事件。

### 2.8 Blindspot 五维公式

规格中的五维是影响、不确定性、可调查性、新颖度和成本，公式固定为：

```python
def score_blindspot(item: Blindspot) -> Decimal:
    return (
        Decimal("0.30") * item.impact
        + Decimal("0.25") * item.uncertainty
        + Decimal("0.20") * item.investigability
        + Decimal("0.15") * item.novelty
        + Decimal("0.10") * (Decimal("1") - item.normalized_cost)
    ).quantize(Decimal("0.0001"))
```

5 个输入都在 `[0, 1]`；不得加入支持人数、角色身份或未在规格中的第六维。

### 2.9 固定测试夹具签名

`tests/conftest.py` 在首次引入时提供：`postgres_admin_url`、`app_session`、`projector_session`、`migrated_db`、`fixed_clock`、`recorded_model_gateway`、`recorded_tool_gateway`、`valid_research_contract`、`valid_packet`、`valid_event_candidate`、`minio_client`、`api_client`、`seeded_task`、`seeded_events`、`gateway_spies`、`started_stack`。`tests/factories.py` 在任务 1 创建并固定辅助函数：`make_source()`、`make_study()`、`make_finding()`、`make_claim_revision()`、`make_event()`、`make_blindspot()`、`make_precommitment()`、`make_request()`、`make_graph_node()`、`make_discriminating_study()`、`make_versioned_same_dataset_case()`、`make_same_team_distinct_dataset_case()`、`make_six_support_one_dissent()`、`make_exchange_entry()`、`make_final_entry()`、`empty_store()`、`workspace_event()`；`tests/helpers.py` 在任务 1 创建并固定：`table_names()`、`table_columns()`、`latest_tool_call()`、`graph_row_count()`、`outgoing_edges()`、`collect_sse()`、`load_acceptance_matrix()`、`load_case_inventory()`、`load_causal_cases()`。各函数接收后续测试代码展示的具名参数并返回对应第 2 节 Contract；后续任务不得以内联未定义名称替代这些公共辅助。

---

# 第 1 周：工程基线、Gateway 与首次受审投影

### 任务 1：建立 Python 工程、Kernel Contract 与 Research Contract

**文件：**
- 创建：`pyproject.toml`
- 创建：`packages/kernel/contracts.py`
- 创建：`packages/kernel/errors.py`
- 创建：`packages/research/contracts.py`
- 创建：`tests/unit/test_kernel_contracts.py`
- 创建：`tests/unit/test_research_contract.py`
- 创建：`tests/conftest.py`
- 创建：`tests/factories.py`
- 创建：`tests/helpers.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_nested_contract_containers_are_immutable(valid_research_contract) -> None:
    with pytest.raises(TypeError):
        valid_research_contract.scope.languages[0] = "fr"  # type: ignore[index]
    frozen = FrozenDict({"outer": {"inner": ["a"]}})
    with pytest.raises(TypeError):
        frozen["outer"]["inner"][0] = "b"  # type: ignore[index]


def test_research_contract_requires_question_scope_budget_and_inputs() -> None:
    with pytest.raises(ValidationError):
        ResearchContract.model_validate({"question": "数字行为是否影响心理健康？"})
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_kernel_contracts.py tests/unit/test_research_contract.py -q`

预期：FAIL，首个错误为 `ModuleNotFoundError: No module named 'packages.kernel.contracts'`。

- [ ] **步骤 3：实现最小代码**

实现第 2.1、2.2 节的 `FrozenDict`、`ContractModel`、`ResearchScope`、`ResearchBudget`、`UserEvidenceInput`、`ResearchContract` 与 7 个 `EvidenceDemandType`；Pydantic `field_validator(mode="before")` 将输入 list/set/dict 转成 tuple/frozenset/FrozenDict。`pyproject.toml` 固定 Python 3.12，并声明本计划技术栈中的 Python 包与 `pytest-repeat`。

- [ ] **步骤 4：验证通过与回归**

运行：

```bash
python -m pytest tests/unit/test_kernel_contracts.py tests/unit/test_research_contract.py -q
python -m ruff check packages/kernel packages/research tests/unit tests/conftest.py
python -m mypy packages/kernel packages/research
```

预期：全部 PASS；嵌套 tuple 与 FrozenDict 的原位修改均抛 `TypeError`。

- [ ] **步骤 5：提交**

```bash
git add pyproject.toml packages/kernel packages/research/contracts.py tests/conftest.py tests/factories.py tests/helpers.py tests/unit/test_kernel_contracts.py tests/unit/test_research_contract.py
git commit -m "feat(kernel): establish immutable research contracts"
```

### 任务 2：建立 Alembic、三类数据库身份与基础表

**文件：**
- 创建：`alembic.ini`
- 创建：`packages/kernel/config.py`
- 创建：`packages/kernel/database.py`
- 创建：`packages/research/models.py`
- 创建：`packages/models/models.py`
- 创建：`packages/tools/models.py`
- 创建：`migrations/env.py`
- 创建：`migrations/versions/0001_research_and_calls.py`
- 创建：`tests/integration/test_initial_migration.py`
- 创建：`tests/integration/test_database_roles.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_upgrade_creates_research_and_call_tables(migrated_db) -> None:
    assert {"research_tasks", "research_scopes", "atomic_claims", "model_calls", "tool_calls"} <= await table_names(migrated_db)

async def test_app_role_cannot_run_ddl(app_session) -> None:
    with pytest.raises(DBAPIError):
        await app_session.execute(text("CREATE TABLE forbidden_table(id integer)"))
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_initial_migration.py tests/integration/test_database_roles.py -q`

预期：FAIL，提示 `alembic.ini`/迁移缺失或目标表不存在。

- [ ] **步骤 3：实现最小代码**

`0001_research_and_calls.py` 创建三类角色、5 张表及可查询的 `task_id/status/created_by/created_at` 字段；`model_calls` 额外包含 token、费用、延迟、重试、错误、证据引用和 Schema 状态，`tool_calls` 包含工具名、操作、延迟、重试与错误。迁移管理员拥有 DDL；普通应用只获业务表 DML；projector 此时不获图表权限。`migrations/env.py` 只从 migrator URL 运行迁移。

- [ ] **步骤 4：验证通过与回滚**

运行：

```bash
alembic upgrade head
python -m pytest tests/integration/test_initial_migration.py tests/integration/test_database_roles.py -q
alembic downgrade base
alembic upgrade head
```

预期：全部成功；降级后升级得到同一表集合，普通应用执行 DDL 被拒绝。

- [ ] **步骤 5：提交**

```bash
git add alembic.ini packages/kernel packages/research/models.py packages/models/models.py packages/tools/models.py migrations tests/integration/test_initial_migration.py tests/integration/test_database_roles.py
git commit -m "feat(db): add governed database roles and base schema"
```

### 任务 3：实现 Model/Tool Gateway、Recorded 实现与调用审计

**文件：**
- 创建：`packages/models/contracts.py`
- 创建：`packages/models/gateway.py`
- 创建：`packages/models/recorded.py`
- 创建：`packages/models/audit.py`
- 创建：`packages/tools/contracts.py`
- 创建：`packages/tools/gateway.py`
- 创建：`packages/tools/recorded.py`
- 创建：`packages/tools/audit.py`
- 创建：`tests/fixtures/recordings/gateways.jsonl`
- 创建：`tests/unit/test_recorded_gateways.py`
- 创建：`tests/integration/test_call_audit.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_recorded_model_gateway_is_hash_deterministic(recorded_model_gateway, model_request) -> None:
    first = await recorded_model_gateway.invoke(model_request)
    second = await recorded_model_gateway.invoke(model_request)
    assert first.payload == second.payload
    assert first.call_id != second.call_id

async def test_each_gateway_attempt_is_audited(recorded_tool_gateway, tool_request, app_session) -> None:
    await recorded_tool_gateway.execute(tool_request)
    row = await latest_tool_call(app_session)
    assert row.input_hash and row.output_hash
    assert row.latency_ms >= 0 and row.retries == 0
    assert "signed_url" not in row.request_summary
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_recorded_gateways.py tests/integration/test_call_audit.py -q`

预期：FAIL，提示 `RecordedModelGateway` 与 `RecordedToolGateway` 尚不存在。

- [ ] **步骤 3：实现最小代码**

实现第 2.3 节 Protocol。Recorded 实现按规范化请求 SHA-256 查 JSONL；缺录制项抛 `RecordingNotFound`，不回退网络。装饰器 `AuditedModelGateway`/`AuditedToolGateway` 对成功、重试耗尽和 Schema 失败都写审计；只保存摘要和哈希。结构化输出修复最多 1 次，仍失败返回 `SchemaStatus.QUARANTINED`。

- [ ] **步骤 4：验证通过与边界检查**

运行：

```bash
python -m pytest tests/unit/test_recorded_gateways.py tests/integration/test_call_audit.py -q
python -m ruff check packages/models packages/tools tests
python -m mypy packages/models packages/tools
```

预期：全部 PASS；未录制请求明确失败；每次调用均有审计行且无私有推理和签名 URL。

- [ ] **步骤 5：提交**

```bash
git add packages/models packages/tools tests/fixtures/recordings/gateways.jsonl tests/unit/test_recorded_gateways.py tests/integration/test_call_audit.py
git commit -m "feat(gateways): audit recorded model and tool calls"
```

### 任务 4：实现四个学术数据源适配器

**文件：**
- 创建：`packages/tools/adapters/openalex.py`
- 创建：`packages/tools/adapters/crossref.py`
- 创建：`packages/tools/adapters/unpaywall.py`
- 创建：`packages/tools/adapters/semantic_scholar.py`
- 创建：`packages/tools/adapters/normalization.py`
- 创建：`tests/fixtures/recordings/openalex.json`
- 创建：`tests/fixtures/recordings/crossref.json`
- 创建：`tests/fixtures/recordings/unpaywall.json`
- 创建：`tests/fixtures/recordings/semantic_scholar.json`
- 创建：`tests/unit/test_source_adapters.py`
- 创建：`tests/integration/test_discovery_gateway.py`

- [ ] **步骤 1：编写失败测试**

```python
@pytest.mark.parametrize("adapter_name", ["openalex", "crossref", "unpaywall", "semantic_scholar"])
async def test_adapter_returns_same_normalized_source(adapter_name, recorded_tool_gateway) -> None:
    source = await adapter(adapter_name, recorded_tool_gateway).lookup_doi("10.1234/example")
    assert source.doi == "10.1234/example"
    assert source.title == "Digital behavior and wellbeing"
    assert source.provider_ids[adapter_name]

async def test_adapters_only_call_tool_gateway(spy_http_client, adapters) -> None:
    await run_all(adapters)
    assert spy_http_client.direct_request_count == 0
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_source_adapters.py tests/integration/test_discovery_gateway.py -q`

预期：FAIL，首个缺失模块为 `packages.tools.adapters.openalex`。

- [ ] **步骤 3：实现最小代码**

四个适配器只构造 `ToolRequest` 并解析 `ToolResult`。OpenAlex/Crossref/Semantic Scholar 提供 DOI/标题/作者/年份/类型/撤稿线索；Unpaywall 提供 OA 状态、版本和受控全文 URL 候选。`normalize_doi()` 去协议、转小写、去空白；同 DOI 合并为一个 `NormalizedSource`，保留所有 provider ID 与字段冲突，不能用后返回值静默覆盖。

- [ ] **步骤 4：验证通过与录制契约**

运行：

```bash
python -m pytest tests/unit/test_source_adapters.py tests/integration/test_discovery_gateway.py -q
python -m pytest tests/integration/test_call_audit.py -q
```

预期：全部 PASS；4 个来源生成同一规范 DOI，冲突字段进入 `metadata_conflicts`，工具调用审计完整。

- [ ] **步骤 5：提交**

```bash
git add packages/tools/adapters tests/fixtures/recordings tests/unit/test_source_adapters.py tests/integration/test_discovery_gateway.py
git commit -m "feat(tools): add scholarly discovery adapters"
```

### 任务 5：实现受控 PDF 存储与完整 PaperEvidencePacket

**文件：**
- 创建：`packages/papers/contracts.py`
- 创建：`packages/papers/models.py`
- 创建：`packages/papers/object_store.py`
- 创建：`packages/papers/parser.py`
- 创建：`packages/papers/packet.py`
- 创建：`migrations/versions/0002_papers_and_objects.py`
- 创建：`tests/fixtures/papers/anchor_case.pdf`
- 创建：`tests/unit/test_paper_packet.py`
- 创建：`tests/integration/test_pdf_privacy.py`
- 创建：`tests/integration/test_pdf_to_packet.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_packet_contains_complete_study_method_and_reporting(valid_packet) -> None:
    study = valid_packet.studies[0]
    assert study.sample.population and study.design
    assert {v.role for v in study.variables} >= {"exposure", "outcome"}
    assert study.analysis.method
    assert study.findings[0].effect.direction
    assert study.author_conclusions and study.author_limitations
    assert study.artifacts.data and study.artifacts.code and study.artifacts.preregistration

async def test_pdf_logs_and_exports_do_not_leak_private_material(pdf_service, caplog) -> None:
    stored = await pdf_service.upload(task_id=uuid4(), filename="private.pdf", content=b"secret-pdf")
    assert stored.object_key.startswith(f"tasks/{stored.task_id}/")
    assert stored.encryption == "AES256"
    assert b"secret-pdf" not in caplog.text.encode()
    assert "signed_url" not in stored.model_dump_json()
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_paper_packet.py tests/integration/test_pdf_privacy.py tests/integration/test_pdf_to_packet.py -q`

预期：FAIL，提示 `PaperEvidencePacket`/`PrivateObjectStore` 缺失。

- [ ] **步骤 3：实现最小代码**

实现第 2.4 节全部 Schema，迁移创建 `objects/sources/source_versions/studies/findings/citation_anchors`。对象存储固定私有 bucket、`tasks/{task_id}/{sha256}.pdf`、AES256 标记、5 分钟签名下载、内容类型与大小限制；数据库只存 object key 和哈希。PyMuPDF 提取页文本；找不到精确 Quote 时不得伪造页码并将包降为 B。明确分离 `SOURCE_TEXT`、`AUTHOR_INTERPRETATION`、`AI_INFERENCE`。

- [ ] **步骤 4：验证通过、迁移与隐私回归**

运行：

```bash
alembic upgrade head
python -m pytest tests/unit/test_paper_packet.py tests/integration/test_pdf_privacy.py tests/integration/test_pdf_to_packet.py -q
python -m ruff check packages/papers tests
python -m mypy packages/papers
```

预期：全部 PASS；Packet 覆盖 Study、样本、设计、变量、分析、方向、效应量、不确定性、作者结论/局限、数据/代码/预注册；日志和 DTO 无私密 PDF 内容。

- [ ] **步骤 5：提交**

```bash
git add packages/papers migrations/versions/0002_papers_and_objects.py tests/fixtures/papers tests/unit/test_paper_packet.py tests/integration/test_pdf_privacy.py tests/integration/test_pdf_to_packet.py
git commit -m "feat(papers): build private complete evidence packets"
```

### 任务 6：定义 10 节点、12 边与最小 Evidence Gate

**文件：**
- 创建：`packages/evidence/contracts.py`
- 创建：`packages/evidence/gate.py`
- 创建：`packages/evidence/causal_policy.py`
- 创建：`tests/unit/test_evidence_graph_contracts.py`
- 创建：`tests/unit/test_minimal_evidence_gate.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_graph_contract_has_exactly_ten_nodes_and_twelve_edges() -> None:
    assert len(EvidenceNodeType) == 10
    assert len(EvidenceEdgeType) == 12
    assert EvidenceNodeType.DISCRIMINATING_STUDY.value == "DiscriminatingStudy"
    assert EvidenceNodeType.DISSENT_CERTIFICATE.value == "DissentCertificate"

@pytest.mark.parametrize("level,expected", [("A", "ADMIT"), ("B", "SOURCE_ONLY"), ("C", "DISCOVERY_ONLY"), ("D", "TOOL_LEAD_ONLY")])
def test_minimal_gate_applies_level_matrix(level, expected, minimal_gate) -> None:
    assert minimal_gate.evaluate(candidate(level=level)).disposition.value == expected
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_evidence_graph_contracts.py tests/unit/test_minimal_evidence_gate.py -q`

预期：FAIL，提示 `EvidenceNodeType`/`MinimalEvidenceGate` 缺失。

- [ ] **步骤 3：实现最小代码**

实现第 2.5 节精确枚举、`ClaimRevision`、`ScientificEventCandidate`、`AdmissionDecision`。最小 Gate 在 Event 可标记 `ADMITTED` 前检查 Schema、Level 矩阵、Source/Study/Anchor、Claim type/scope/falsification condition、相关性不得升级因果，以及拟投影 Finding 的 `DERIVED_FROM Source` 关系。Level B/C/D 的行为严格按矩阵返回。

- [ ] **步骤 4：验证通过与枚举锁定**

运行：

```bash
python -m pytest tests/unit/test_evidence_graph_contracts.py tests/unit/test_minimal_evidence_gate.py -q
python -m mypy packages/evidence
```

预期：全部 PASS；新增第 10 个节点或第 13 条边会使测试失败。

- [ ] **步骤 5：提交**

```bash
git add packages/evidence/contracts.py packages/evidence/gate.py packages/evidence/causal_policy.py tests/unit/test_evidence_graph_contracts.py tests/unit/test_minimal_evidence_gate.py
git commit -m "feat(evidence): define graph ontology and admission gate"
```

### 任务 7：实现受 Gate 约束的 Ledger、Projector 与双角色写权限

**文件：**
- 创建：`packages/evidence/models.py`
- 创建：`packages/evidence/repository.py`
- 创建：`packages/evidence/ledger.py`
- 创建：`packages/evidence/projector.py`
- 创建：`migrations/versions/0003_evidence_ledger_graph.py`
- 创建：`tests/integration/test_gate_before_projection.py`
- 创建：`tests/integration/test_event_idempotency.py`
- 创建：`tests/integration/test_projector_permissions.py`
- 创建：`tests/integration/test_finding_source_edge.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_projector_refuses_unaudited_event(projector, pending_event) -> None:
    with pytest.raises(EventNotAdmitted):
        await projector.project(pending_event.id)
    assert await graph_row_count() == 0

async def test_finding_and_derived_from_source_are_atomic(projector, admitted_finding_event) -> None:
    await projector.project(admitted_finding_event.id)
    assert await outgoing_edges(admitted_finding_event.finding_id, EvidenceEdgeType.DERIVED_FROM) == (admitted_finding_event.source_id,)

async def test_app_role_cannot_write_graph(app_session) -> None:
    with pytest.raises(DBAPIError):
        await app_session.execute(insert(GraphNodeModel).values(make_graph_node()))
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_gate_before_projection.py tests/integration/test_event_idempotency.py tests/integration/test_projector_permissions.py tests/integration/test_finding_source_edge.py -q`

预期：FAIL，提示 Ledger/Projector/图表缺失。

- [ ] **步骤 3：实现最小代码**

迁移创建 `scientific_events/event_audits/event_status_transitions/projection_checkpoints/graph_nodes/graph_edges`，约束 `(task_id,idempotency_key)`、`(task_id,sequence)` 唯一，禁止级联删除。普通应用只能追加 Event/Audit，不能写图；projector 只获图 DML 与 checkpoint 权限。`EventLedger.append()` 对同 key 同 payload 返回原 Event，不同 payload 抛冲突。`GraphProjector.project_next()` 只读取 `ADMITTED`，用 checkpoint 行锁顺序投影；Finding 节点与唯一 `DERIVED_FROM Source` 边同事务写入，失败全部回滚。

- [ ] **步骤 4：验证通过与首次垂直闭环**

运行：

```bash
alembic upgrade head
python -m pytest tests/integration/test_gate_before_projection.py tests/integration/test_event_idempotency.py tests/integration/test_projector_permissions.py tests/integration/test_finding_source_edge.py -q
python -m pytest tests/integration/test_pdf_to_packet.py -q
```

预期：全部 PASS；未审核 Event 的正式图行数为 0；重复投影不新增行；每个 Finding 恰有一条 `DERIVED_FROM Source`。

- [ ] **步骤 5：提交**

```bash
git add packages/evidence migrations/versions/0003_evidence_ledger_graph.py tests/integration/test_gate_before_projection.py tests/integration/test_event_idempotency.py tests/integration/test_projector_permissions.py tests/integration/test_finding_source_edge.py
git commit -m "feat(evidence): gate every ledger projection"
```

**第 1 周退出门槛：** 固定 PDF 先形成完整 Packet，再通过最小 Evidence Gate，随后写 Ledger 并由独立 Projector 投影；任何未审核 Finding 都不能进入正式图。

---

# 第 2 周：Process Graph、MemoBrain 与分支恢复

### 任务 8：核验 MemoBrain 许可证并冻结 Adapter

**文件：**
- 创建：`docs/licenses/memobrain.md`
- 创建：`THIRD_PARTY_NOTICES.md`
- 创建：`packages/memory/contracts.py`
- 创建：`packages/memory/adapter.py`
- 创建：`packages/memory/in_memory_adapter.py`
- 创建：`tests/unit/test_memory_adapter.py`
- 创建：`tests/unit/test_license_record.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_private_memory_is_isolated(memory_adapter, memory_task) -> None:
    await memory_adapter.init_private_memory(THEORY_ID, memory_task)
    await memory_adapter.init_private_memory(CAUSAL_ID, memory_task)
    await memory_adapter.memorize_episode(THEORY_ID, Episode(kind="evidence", summary="private-a"))
    assert "private-a" in (await memory_adapter.recall_private(THEORY_ID, 100)).text
    assert "private-a" not in (await memory_adapter.recall_private(CAUSAL_ID, 100)).text


def test_license_record_has_required_fields() -> None:
    record = parse_license_record(Path("docs/licenses/memobrain.md"))
    assert record.name and record.upstream_url and record.checked_commit and record.redistribution_decision
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_memory_adapter.py tests/unit/test_license_record.py -q`

预期：FAIL，Adapter 和许可证记录不存在。

- [ ] **步骤 3：实现最小代码**

人工核验上游官方仓库许可证，记录许可证名、版权、核验 commit、URL、允许方式和归属要求。若无明确授权，`redistribution_decision=adapter_only_no_upstream_copy`，只实现独立内存兼容 Adapter。Protocol 精确包含 `init_private_memory`、`memorize_episode`、`recall_private`、`save_snapshot`、`load_snapshot` 五方法；领域代码不导入上游内部类。

- [ ] **步骤 4：验证通过与归属边界**

运行：

```bash
python -m pytest tests/unit/test_memory_adapter.py tests/unit/test_license_record.py -q
python -m mypy packages/memory
```

预期：全部 PASS；7 个 agent ID 可分别初始化且 Recall 不串席。

- [ ] **步骤 5：提交**

```bash
git add docs/licenses/memobrain.md THIRD_PARTY_NOTICES.md packages/memory tests/unit/test_memory_adapter.py tests/unit/test_license_record.py
git commit -m "feat(memory): freeze licensed memory adapter boundary"
```

### 任务 9：实现扩展 Process Graph 与角色化 Perspective Recall

**文件：**
- 创建：`packages/memory/process_graph.py`
- 创建：`packages/memory/projection.py`
- 创建：`packages/memory/recall.py`
- 创建：`tests/unit/test_process_graph.py`
- 创建：`tests/unit/test_perspective_recall.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_process_graph_supports_poliscope_nodes() -> None:
    assert {ProcessNodeType.DEBATE, ProcessNodeType.DECISION, ProcessNodeType.ASSIGNMENT} <= set(ProcessNodeType)


def test_role_projections_differ_without_private_memory_leak(process_graph, evidence_snapshot) -> None:
    causal = perspective_recall(CAUSAL_POLICY, process_graph, evidence_snapshot)
    measurement = perspective_recall(MEASUREMENT_POLICY, process_graph, evidence_snapshot)
    assert causal.evidence_projection != measurement.evidence_projection
    assert "theory-private" not in causal.process_recall.text
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_process_graph.py tests/unit/test_perspective_recall.py -q`

预期：FAIL，缺少 `ProcessNodeType` 与 `perspective_recall`。

- [ ] **步骤 3：实现最小代码**

实现第 2.7 节节点枚举、不可变 `ProcessNode/ProcessEdge/ProcessGraphSnapshot`。`ProjectionPolicy` 为 7 席分别定义证据排序权重与质询偏好；`EvidenceProjection` 只含已审核正式证据摘要和引用。`RoleContext = ProcessRecall_i + EvidenceProjection_i`；禁止 Process 节点自动转换为 Evidence Event。

- [ ] **步骤 4：验证通过与边界回归**

运行：

```bash
python -m pytest tests/unit/test_process_graph.py tests/unit/test_perspective_recall.py -q
python -m pytest tests/unit/test_minimal_evidence_gate.py -q
```

预期：全部 PASS；DEBATE/DECISION/ASSIGNMENT 可序列化，7 种投影可区分，私有内容不跨席。

- [ ] **步骤 5：提交**

```bash
git add packages/memory/process_graph.py packages/memory/projection.py packages/memory/recall.py tests/unit/test_process_graph.py tests/unit/test_perspective_recall.py
git commit -m "feat(memory): add role-aware process graph recall"
```

### 任务 10：实现 Process Fold 与科研骨架保留门

**文件：**
- 创建：`packages/memory/fold.py`
- 创建：`tests/golden/fixtures/process_fold.json`
- 创建：`tests/golden/test_process_fold.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_fold_preserves_six_backbone_elements(process_fold_case) -> None:
    folded = fold_process(process_fold_case, token_budget=800)
    assert folded.retention == BackboneRetention(
        current_task_preserved=True,
        confirmed_findings_preserved=True,
        active_blindspots_preserved=True,
        unresolved_challenges_preserved=True,
        minority_dissents_preserved=True,
        next_evidence_needs_preserved=True,
    )


def test_process_fold_rejects_evidence_graph_nodes(evidence_node) -> None:
    with pytest.raises(GraphBoundaryViolation):
        fold_process((evidence_node,), token_budget=800)
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/golden/test_process_fold.py -q`

预期：FAIL，缺少 `fold_process`。

- [ ] **步骤 3：实现最小代码**

实现 `BackboneRetention` 六字段与 `passed` 属性。Fold 只接受 Process Graph snapshot；压缩后逐字段检查，任何字段丢失则返回原轨迹并记录 `FOLD_REJECTED`，不能修改 Evidence Graph 或 Dissent 内容。

- [ ] **步骤 4：验证通过与 Golden 稳定性**

运行：`python -m pytest tests/golden/test_process_fold.py tests/unit/test_process_graph.py -q`

预期：全部 PASS；固定输入的规范化 Fold JSON 与 fixture 相同。

- [ ] **步骤 5：提交**

```bash
git add packages/memory/fold.py tests/golden/fixtures/process_fold.json tests/golden/test_process_fold.py
git commit -m "feat(memory): preserve scientific backbone during fold"
```

### 任务 11：实现 Fork、Merge、Quarantine、Resurrect 与一致性快照

**文件：**
- 创建：`packages/memory/branches.py`
- 创建：`packages/memory/snapshots.py`
- 创建：`packages/evidence/lifecycle.py`
- 创建：`packages/epistemo/recovery.py`
- 创建：`migrations/versions/0004_memory_branches_snapshots.py`
- 创建：`tests/unit/test_branch_operations.py`
- 创建：`tests/integration/test_quarantine_resurrect.py`
- 创建：`tests/integration/test_snapshot_resume.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_merge_requires_condition_variable_and_two_branches(branch_service) -> None:
    with pytest.raises(ValidationError):
        branch_service.merge(MergeRequest(branch_ids=(uuid4(),), condition_variable=""))

async def test_resurrect_requires_new_evidence_satisfying_original_condition(lifecycle, quarantined_claim) -> None:
    with pytest.raises(ResurrectionConditionNotMet):
        await lifecycle.resurrect(quarantined_claim.id, evidence_refs=())
    assert await lifecycle.node_exists(quarantined_claim.id)

async def test_snapshot_resume_keeps_checkpoint_and_challenges(running_task) -> None:
    snap = await running_task.save_snapshot()
    restored = await running_task.restore(snap.id)
    assert restored.projector_checkpoint == snap.projector_checkpoint
    assert restored.unresolved_challenges == snap.unresolved_challenges
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_branch_operations.py tests/integration/test_quarantine_resurrect.py tests/integration/test_snapshot_resume.py -q`

预期：FAIL，缺少 Branch/Lifecycle/Snapshot 服务。

- [ ] **步骤 3：实现最小代码**

实现第 2.7 节 Fork/Merge/Resurrect；Quarantine 必填原因、攻击者、缺失证据、复活条件；状态追加 `PROPOSED→SUPPORTED→CONTESTED→QUARANTINED→RESURRECTED/REJECTED`，不物理删除。迁移创建 `process_nodes/process_edges/process_branches/memory_snapshots`。快照保存 7 个私有快照引用、Process Graph 版本、任务阶段、预算、未解决质询与 projector checkpoint；恢复先校验 Schema 版本和 SHA-256，禁止 checkpoint 倒退。

- [ ] **步骤 4：验证通过与历史保留**

运行：

```bash
alembic upgrade head
python -m pytest tests/unit/test_branch_operations.py tests/integration/test_quarantine_resurrect.py tests/integration/test_snapshot_resume.py -q
```

预期：全部 PASS；Fork/Merge/Resurrect 均追加历史；损坏快照不替换当前状态；恢复后无重复投影。

- [ ] **步骤 5：提交**

```bash
git add packages/memory packages/evidence/lifecycle.py packages/epistemo/recovery.py migrations/versions/0004_memory_branches_snapshots.py tests/unit/test_branch_operations.py tests/integration/test_quarantine_resurrect.py tests/integration/test_snapshot_resume.py
git commit -m "feat(memory): add branch lifecycle and safe recovery"
```

**第 2 周退出门槛：** Process Graph 含 3 种扩展节点；7 席 Recall 角色化且隔离；Fold 保留 6 类科研骨架；Fork/Merge/Resurrect 与快照恢复不删除历史或重复投影。

---

# 第 3 周：完整 7 轮议会与持久状态机

### 任务 12：实现 7 席、结构化科研动作与回应白名单

**文件：**
- 创建：`packages/council/contracts.py`
- 创建：`packages/council/roles.py`
- 创建：`packages/council/actions.py`
- 创建：`tests/unit/test_council_contracts.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_registry_contains_exactly_seven_scientists() -> None:
    assert set(ROLE_SPECS) == set(Seat)
    assert len(ROLE_SPECS) == 7
    assert "epistemo_brain" not in {seat.value for seat in Seat}

@pytest.mark.parametrize("response", ["DEFEND", "REVISE", "NARROW", "WITHDRAW", "DISSENT"])
def test_challenge_response_whitelist(response) -> None:
    assert ChallengeResponseType(response)
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_council_contracts.py -q`

预期：FAIL，缺少 `Seat` 与 `ROLE_SPECS`。每个测试文件开头显式导入 `pytest`、被测枚举/服务与 `tests.factories` 中使用的工厂；后续测试代码块均沿用这一规则，不依赖隐式全局名称。

- [ ] **步骤 3：实现最小代码**

定义 7 席固定枚举和参数化 `RoleSpec`，共用一个 Runtime。`ScientificAction` 仅允许 `PROPOSE/SUPPORT/CHALLENGE/QUALIFY/FORK/REQUEST/REVISE/DISSENT`，必填 actor、target、statement、evidence_refs、confidence、falsification_condition、novelty；事实性 SUPPORT 无 Evidence Ref 验证失败。收到 Challenge 后仅允许 5 种回应，无新增信息的同意不产生 Event。

- [ ] **步骤 4：验证通过与类型检查**

运行：

```bash
python -m pytest tests/unit/test_council_contracts.py -q
python -m mypy packages/council
```

预期：全部 PASS；EpistemoBrain 不在席位枚举中。

- [ ] **步骤 5：提交**

```bash
git add packages/council tests/unit/test_council_contracts.py
git commit -m "feat(council): define seven-seat scientific actions"
```

### 任务 13：实现 RoundHandler 框架与独立预承诺

**文件：**
- 创建：`packages/council/rounds/base.py`
- 创建：`packages/council/rounds/precommitment.py`
- 创建：`packages/council/runtime.py`
- 创建：`tests/unit/test_round_handler_contract.py`
- 创建：`tests/integration/test_seven_precommitments.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_precommitments_hidden_until_sealed(council_runtime) -> None:
    await council_runtime.submit_precommitment(Seat.THEORY_BUILDER, make_precommitment())
    with pytest.raises(PrecommitmentNotSealed):
        await council_runtime.read_all_precommitments()

async def test_precommitment_timeout_records_gap_not_abort(handler, round_context) -> None:
    decision = await handler.on_timeout(await handler.enter(round_context))
    assert decision.task_status == TaskStatus.DEGRADED_RUNNING
    assert decision.missing_output == "SEAT_TIMED_OUT"
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_round_handler_contract.py tests/integration/test_seven_precommitments.py -q`

预期：FAIL，缺少 `RoundHandler`/`PrecommitmentHandler`。

- [ ] **步骤 3：实现最小代码**

实现第 2.6 节 `RoundHandler` Protocol、`RoundContext/RoundEntry/RoundResult/CompletionDecision/TimeoutDecision`。预承诺输入是已确认原子主张和私有 Recall；输出含初始判断、置信度、Blindspot、更新条件。7 席各有提交或失败记录后一次 Seal；Seal 前互不可见。

- [ ] **步骤 4：验证通过与隔离回归**

运行：

```bash
python -m pytest tests/unit/test_round_handler_contract.py tests/integration/test_seven_precommitments.py -q
python -m pytest tests/unit/test_memory_adapter.py -q
```

预期：全部 PASS；7 席提交完整且 Seal 前不可互读。

- [ ] **步骤 5：提交**

```bash
git add packages/council/rounds packages/council/runtime.py tests/unit/test_round_handler_contract.py tests/integration/test_seven_precommitments.py
git commit -m "feat(council): seal independent precommitments"
```

### 任务 14：实现专业取证与证据交换两轮

**文件：**
- 创建：`packages/council/rounds/acquisition.py`
- 创建：`packages/council/rounds/exchange.py`
- 创建：`packages/papers/candidate_pool.py`
- 创建：`packages/papers/query_planner.py`
- 创建：`tests/integration/test_acquisition_round.py`
- 创建：`tests/integration/test_exchange_round.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_duplicate_doi_is_one_candidate_with_two_requests(candidate_pool) -> None:
    await candidate_pool.add(make_request(Seat.CAUSAL_SCIENTIST, "10.1234/EXAMPLE"))
    await candidate_pool.add(make_request(Seat.EVIDENCE_AUDITOR, "https://doi.org/10.1234/example"))
    candidate = await candidate_pool.by_doi("10.1234/example")
    assert await candidate_pool.count() == 1
    assert candidate.requesting_seats == frozenset({Seat.CAUSAL_SCIENTIST, Seat.EVIDENCE_AUDITOR})

async def test_exchange_contains_structured_projection_not_private_episode(exchange_handler) -> None:
    result = await exchange_handler.run(make_exchange_entry(private_text="secret"))
    assert "secret" not in result.model_dump_json()
    assert result.evidence_items[0].source_id
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_acquisition_round.py tests/integration/test_exchange_round.py -q`

预期：FAIL，缺少两个 Round Handler 与候选池。

- [ ] **步骤 3：实现最小代码**

`AcquisitionInput/Output` 和 `ExchangeInput/Output` 遵循第 2.6 节条件。Query Planner 合并 7 席请求，通过四个工具适配器检索，DOI/版本规范化后只下载解析一次；重复发现只提高优先级。交换只发布 `EvidenceProjectionItem(source_id,study_id,finding_candidate_id,anchor_summary,level)`，不发布完整私有历史。两轮 timeout 均写未填 Evidence Slot 并允许其他席继续。

- [ ] **步骤 4：验证通过与 Gateway 审计**

运行：

```bash
python -m pytest tests/integration/test_acquisition_round.py tests/integration/test_exchange_round.py -q
python -m pytest tests/integration/test_discovery_gateway.py tests/integration/test_call_audit.py -q
```

预期：全部 PASS；同论文只解析一次，交换 DTO 不含私有文本。

- [ ] **步骤 5：提交**

```bash
git add packages/council/rounds/acquisition.py packages/council/rounds/exchange.py packages/papers/candidate_pool.py packages/papers/query_planner.py tests/integration/test_acquisition_round.py tests/integration/test_exchange_round.py
git commit -m "feat(council): acquire and exchange shared evidence"
```

### 任务 15：实现交叉质询与 Claim revision

**文件：**
- 创建：`packages/council/rounds/cross_examination.py`
- 创建：`packages/council/claim_revision.py`
- 创建：`tests/unit/test_claim_revision.py`
- 创建：`tests/integration/test_cross_examination_round.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_narrow_appends_revision_and_keeps_original(claim_repository, broad_claim) -> None:
    narrowed = revise_claim(broad_claim, ResponseType.NARROW, narrower_scope())
    assert narrowed.revision == broad_claim.revision + 1
    assert narrowed.supersedes_revision == broad_claim.revision
    assert claim_repository.get(broad_claim.claim_id, broad_claim.revision) == broad_claim

async def test_unanswered_fatal_challenge_blocks_claim(handler, challenge_entry) -> None:
    result = await handler.on_timeout(challenge_entry)
    assert result.blocked_claim_ids == (challenge_entry.claim_id,)
    assert result.unresolved_challenge_ids
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_claim_revision.py tests/integration/test_cross_examination_round.py -q`

预期：FAIL，缺少 revision 与交叉质询处理器。

- [ ] **步骤 3：实现最小代码**

实现追加式 `ClaimRevision`：`REVISE` 改 statement/type/confidence，`NARROW` 验证 scope 严格收窄，`WITHDRAW` 追加撤回状态，`DEFEND` 必须补 Evidence Ref，`DISSENT` 生成候选证书。`CrossExaminationHandler` 对因果、测量、统计、边界、来源和隐藏假设分派定向质询；未回应致命攻击进入 Gate blocker，不能被多数意见覆盖。

- [ ] **步骤 4：验证通过与历史回归**

运行：

```bash
python -m pytest tests/unit/test_claim_revision.py tests/integration/test_cross_examination_round.py -q
python -m pytest tests/unit/test_minimal_evidence_gate.py -q
```

预期：全部 PASS；原 Claim revision 可读；未回应致命攻击阻断准入。

- [ ] **步骤 5：提交**

```bash
git add packages/council/rounds/cross_examination.py packages/council/claim_revision.py tests/unit/test_claim_revision.py tests/integration/test_cross_examination_round.py
git commit -m "feat(council): preserve claims through cross examination"
```

### 任务 16：实现盲点悬赏、联合建模与最终独立复判

**文件：**
- 创建：`packages/council/rounds/blindspot_bounty.py`
- 创建：`packages/council/rounds/joint_modeling.py`
- 创建：`packages/council/rounds/final_rejudgment.py`
- 创建：`tests/unit/test_final_round_contracts.py`
- 创建：`tests/integration/test_rounds_five_to_seven.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_joint_model_requires_dialectical_fields(joint_handler, incomplete_entry) -> None:
    result = await joint_handler.run(incomplete_entry)
    assert result.completion.ready is False
    assert set(result.completion.missing_fields) == {"strongest_opposition_refs", "falsification_conditions"}

async def test_final_rejudgment_is_independent_for_all_seats(final_handler, joint_snapshot) -> None:
    result = await final_handler.run(make_final_entry(joint_snapshot))
    assert len(result.judgments) == 7
    assert len({j.seat for j in result.judgments}) == 7
    assert all(j.evidence_driven_update for j in result.judgments)
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_final_round_contracts.py tests/integration/test_rounds_five_to_seven.py -q`

预期：FAIL，3 个 Handler 尚不存在。

- [ ] **步骤 3：实现最小代码**

三轮严格使用第 2.6 节 Schema、进入/完成/超时规则。Blindspot Bounty 仅调用第 2.8 节五维评分并生成 `ASSIGNMENT`。Joint Modeling 输出共同认识、正反立场、铰链变量、边界、未解决冲突和判别条件；不做多数投票。Final Rejudgment 将同一冻结联合快照分别交给 7 席，输出最终判断、置信度变化、引用和 Dissent；席位间输出不可互见直至 Seal。

- [ ] **步骤 4：验证通过与 7 轮 Schema 完整性**

运行：

```bash
python -m pytest tests/unit/test_final_round_contracts.py tests/integration/test_rounds_five_to_seven.py -q
python -m pytest tests/unit/test_round_handler_contract.py tests/integration/test_acquisition_round.py tests/integration/test_exchange_round.py tests/integration/test_cross_examination_round.py -q
```

预期：全部 PASS；7 个 Handler 都有固定 input/output、进入、完成和 timeout 测试；最终复判含 7 个独立结果或明确缺席。

- [ ] **步骤 5：提交**

```bash
git add packages/council/rounds tests/unit/test_final_round_contracts.py tests/integration/test_rounds_five_to_seven.py
git commit -m "feat(council): complete seven-round rejudgment protocol"
```

### 任务 17：实现持久化状态机、预算与阶段降级

**文件：**
- 创建：`packages/epistemo/contracts.py`
- 创建：`packages/epistemo/state_machine.py`
- 创建：`packages/epistemo/budget.py`
- 创建：`packages/epistemo/orchestrator.py`
- 创建：`packages/council/models.py`
- 创建：`migrations/versions/0005_council_runtime.py`
- 创建：`tests/unit/test_task_state_machine.py`
- 创建：`tests/integration/test_single_seat_degradation.py`
- 创建：`tests/integration/test_round_timeouts.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_degraded_running_is_not_terminal(state_machine) -> None:
    assert state_machine.is_terminal(TaskStatus.DEGRADED_RUNNING) is False
    assert state_machine.is_terminal(TaskStatus.COMPLETED_WITH_GAPS) is True

async def test_one_seat_failure_continues_current_round(orchestrator) -> None:
    result = await orchestrator.run_round(TaskPhase.EVIDENCE_EXCHANGE, failing_seat=Seat.MEASUREMENT_SCIENTIST)
    assert len(result.completed_seats) == 6
    assert result.status == TaskStatus.DEGRADED_RUNNING
    assert result.next_phase == TaskPhase.CROSS_EXAMINATION
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_task_state_machine.py tests/integration/test_single_seat_degradation.py tests/integration/test_round_timeouts.py -q`

预期：FAIL，缺少持久状态机/迁移。

- [ ] **步骤 3：实现最小代码**

迁移创建 `council_rounds/scientist_runs/round_outputs`。状态机使用第 2.6 节状态，每个阶段注册对应 Handler 的进入、完成、timeout 和唯一正常下一阶段；非法跳转抛 `InvalidTransition`。预算分别扣 wall clock、模型费用、工具次数和来源数；耗尽时 `stop_reason=BUDGET_EXHAUSTED`、保留 `unfilled_evidence_slots`，不得写成 `EVIDENCE_SATURATION`。单席失败写 `scientist_runs` 并进入 `DEGRADED_RUNNING`；只有报告完成时转换 `COMPLETED_WITH_GAPS`。

- [ ] **步骤 4：验证通过、迁移与恢复**

运行：

```bash
alembic upgrade head
python -m pytest tests/unit/test_task_state_machine.py tests/integration/test_single_seat_degradation.py tests/integration/test_round_timeouts.py tests/integration/test_snapshot_resume.py -q
python -m mypy packages/epistemo packages/council
```

预期：全部 PASS；7 轮顺序持久化；暂停恢复后阶段一致；单席失败不终止其他席。

- [ ] **步骤 5：提交**

```bash
git add packages/epistemo packages/council/models.py migrations/versions/0005_council_runtime.py tests/unit/test_task_state_machine.py tests/integration/test_single_seat_degradation.py tests/integration/test_round_timeouts.py
git commit -m "feat(epistemo): persist degraded seven-round execution"
```

**第 3 周退出门槛：** 7 个 Round Handler 均具 Schema、进入、完成和 timeout；7 席完成独立预承诺与最终复判；阶段降级不混用终态，预算缺口可审计。

---

# 第 4 周：完整 Evidence Engine、谱系与异议保真

### 任务 18：实现 Research Service 与完整 Evidence Demand Matrix

**文件：**
- 创建：`packages/research/service.py`
- 创建：`packages/research/atomization.py`
- 创建：`packages/research/demand_matrix.py`
- 创建：`tests/integration/test_research_service.py`
- 创建：`tests/unit/test_evidence_demand_matrix.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_task_cannot_queue_before_atomic_claim_confirmation(research_service, valid_research_contract) -> None:
    task = await research_service.create(valid_research_contract)
    with pytest.raises(UnconfirmedClaims):
        await research_service.queue(task.id)


def test_demand_matrix_contains_exactly_seven_slots(valid_matrix) -> None:
    assert {slot.demand_type for slot in valid_matrix.slots} == set(EvidenceDemandType)
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_research_service.py tests/unit/test_evidence_demand_matrix.py -q`

预期：FAIL，缺少 Research Service 和 Demand Matrix。

- [ ] **步骤 3：实现最小代码**

`create()` 持久化问题、完整 scope、四维预算以及 DOI/BibTeX/PDF object IDs；`suggest_atomic_claims()` 返回带 claim_type/scope/证伪条件的候选；用户确认后才能 queue。Demand Matrix 精确包含 7 类 Evidence Slot，每槽记录优先级、请求席位、满足来源、状态与缺口理由。

- [ ] **步骤 4：验证通过与 Contract 回归**

运行：

```bash
python -m pytest tests/integration/test_research_service.py tests/unit/test_evidence_demand_matrix.py -q
python -m pytest tests/unit/test_research_contract.py -q
```

预期：全部 PASS；DOI/BibTeX/PDF 三类输入可同时存在且任务未确认前不可运行。

- [ ] **步骤 5：提交**

```bash
git add packages/research/service.py packages/research/atomization.py packages/research/demand_matrix.py tests/integration/test_research_service.py tests/unit/test_evidence_demand_matrix.py
git commit -m "feat(research): enforce research contract and evidence demands"
```

### 任务 19：扩展 Evidence Gate 为完整三层审核与 A–D 矩阵

**文件：**
- 创建：`packages/evidence/source_verifier.py`
- 创建：`packages/evidence/citation_verifier.py`
- 创建：`packages/evidence/method_auditor.py`
- 创建：`packages/evidence/consistency.py`
- 修改：`packages/evidence/gate.py`
- 创建：`tests/integration/test_full_evidence_gate.py`
- 创建：`tests/golden/fixtures/causal_cases.json`
- 创建：`tests/golden/test_causal_entailment.py`
- 创建：`tests/unit/test_evidence_level_matrix.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_gate_records_required_stage_order(full_gate, valid_event_candidate) -> None:
    decision = await full_gate.audit(valid_event_candidate)
    assert tuple(item.stage for item in decision.audit_findings) == (
        AuditStage.SCHEMA, AuditStage.DEDUPLICATION, AuditStage.SOURCE,
        AuditStage.CITATION_ENTAILMENT, AuditStage.METHOD_QUALITY, AuditStage.GRAPH_CONSISTENCY,
    )

@pytest.mark.parametrize("case", load_causal_cases())
def test_forbidden_entailment_upgrades(case, causal_policy) -> None:
    assert causal_policy.evaluate(case.finding, case.claim) == case.expected
```

Golden 固定包含：相关→因果、不显著→无效应、讨论推测→实证机制、子群→总体、Level B→高置信因果、引言假设→结果六类违规。

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_full_evidence_gate.py tests/golden/test_causal_entailment.py tests/unit/test_evidence_level_matrix.py -q`

预期：FAIL，完整审核器与阶段记录缺失。

- [ ] **步骤 3：实现最小代码**

Gate 固定顺序 `Schema→Dedup→Source→Citation Entailment→Method Quality→Graph Consistency`；每阶段写 `event_audits`。来源真实性核 DOI/标题/作者/版本/撤稿/PDF 身份；引用审核核 exact quote 的蕴含与限定词；方法审核分别输出直接性、设计、测量、精度、复现、外部效度，不合并成单总分。严格执行第 2.5 节 A–D 矩阵；阻断失败保留 Event 并标 Quarantined，不重写原动作。

- [ ] **步骤 4：验证通过与 Projector 回归**

运行：

```bash
python -m pytest tests/integration/test_full_evidence_gate.py tests/golden/test_causal_entailment.py tests/unit/test_evidence_level_matrix.py -q
python -m pytest tests/integration/test_gate_before_projection.py tests/integration/test_finding_source_edge.py -q
```

预期：全部 PASS；A 才可形成正式 Finding；B/C/D 不产生正式 Finding→Claim 边；Projector 只消费 ADMITTED。

- [ ] **步骤 5：提交**

```bash
git add packages/evidence tests/integration/test_full_evidence_gate.py tests/golden tests/unit/test_evidence_level_matrix.py
git commit -m "feat(evidence): complete evidence admission audits"
```

### 任务 20：实现 Dialectical Fold、Dissent Certificate 与条件化共识

**文件：**
- 创建：`packages/evidence/dialectical_fold.py`
- 创建：`packages/council/dissent.py`
- 创建：`packages/council/consensus.py`
- 创建：`tests/unit/test_dialectical_fold.py`
- 创建：`tests/integration/test_dissent_preservation.py`
- 创建：`tests/integration/test_conditional_consensus.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_debate_capsule_requires_all_dialectical_fields() -> None:
    with pytest.raises(ValidationError):
        DebateCapsule(common_ground=("association exists",))

async def test_six_supporters_cannot_override_failed_gate(consensus_service, failed_claim) -> None:
    result = await consensus_service.decide(failed_claim, make_six_support_one_dissent())
    assert result.status != ConsensusStatus.ADMITTED
    assert result.unresolved_blockers
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_dialectical_fold.py tests/integration/test_dissent_preservation.py tests/integration/test_conditional_consensus.py -q`

预期：FAIL，缺少 Fold/Dissent/Consensus 服务。

- [ ] **步骤 3：实现最小代码**

`DebateCapsule` 必填共同认识、最强支持、最强反对、铰链变量、适用边界、未解决冲突、证伪条件、Source Ref、Dissent Certificate ID。`DissentCertificate` 必填作者、目标、异议、理由、Evidence Ref、撤回条件。Dialectical Fold 通过 Event Ledger 追加 Capsule，不删除原 Claim/Finding/Event/Dissent。条件化共识要求可追溯证据、引用核验、scope、证伪条件、无未回应致命攻击，不读取票数决定准入。

- [ ] **步骤 4：验证通过与历史保真**

运行：

```bash
python -m pytest tests/unit/test_dialectical_fold.py tests/integration/test_dissent_preservation.py tests/integration/test_conditional_consensus.py -q
python -m pytest tests/integration/test_cross_examination_round.py -q
```

预期：全部 PASS；Fold 后正反证据、冲突和 Dissent 仍可逐项追溯。

- [ ] **步骤 5：提交**

```bash
git add packages/evidence/dialectical_fold.py packages/council/dissent.py packages/council/consensus.py tests/unit/test_dialectical_fold.py tests/integration/test_dissent_preservation.py tests/integration/test_conditional_consensus.py
git commit -m "feat(evidence): preserve dissent in conditional consensus"
```

### 任务 21：实现 Evidence Lineage 与独立证据簇

**文件：**
- 创建：`packages/evidence/lineage.py`
- 创建：`packages/evidence/independence.py`
- 创建：`migrations/versions/0006_evidence_lineage.py`
- 创建：`tests/unit/test_lineage_relations.py`
- 创建：`tests/integration/test_independent_clusters.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_versions_and_same_dataset_count_as_one_cluster(lineage_service) -> None:
    result = lineage_service.cluster(make_versioned_same_dataset_case())
    assert result.paper_count == 3
    assert result.independent_cluster_count == 1


def test_same_team_alone_does_not_merge_evidence(lineage_service) -> None:
    result = lineage_service.cluster(make_same_team_distinct_dataset_case())
    assert result.paper_count == 2
    assert result.independent_cluster_count == 2
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_lineage_relations.py tests/integration/test_independent_clusters.py -q`

预期：FAIL，Lineage 服务与迁移缺失。

- [ ] **步骤 3：实现最小代码**

迁移创建 `lineage_edges/evidence_clusters`。精确支持 `SAME_DATASET/OVERLAPPING_SAMPLE/SAME_RESEARCH_TEAM/PREPRINT_VERSION_OF/EXTENSION_OF/REANALYSIS_OF/CITES_WITHOUT_NEW_DATA/META_ANALYSIS_INCLUDES`。只有已核验且表示数据依赖的边参与合并；`SAME_RESEARCH_TEAM` 单独不合并。不确定边标 `PENDING_REVIEW`。簇 ID 由排序 Source UUID 的 SHA-256 稳定生成。

- [ ] **步骤 4：验证通过与不重复计票**

运行：

```bash
alembic upgrade head
python -m pytest tests/unit/test_lineage_relations.py tests/integration/test_independent_clusters.py -q
```

预期：全部 PASS；输入顺序不影响簇 ID；同时返回论文数和独立证据簇数。

- [ ] **步骤 5：提交**

```bash
git add packages/evidence/lineage.py packages/evidence/independence.py migrations/versions/0006_evidence_lineage.py tests/unit/test_lineage_relations.py tests/integration/test_independent_clusters.py
git commit -m "feat(evidence): count independent evidence lineages"
```

### 任务 22：实现 Blindspot 五维评分、停止条件与 DiscriminatingStudy

**文件：**
- 创建：`packages/epistemo/blindspots.py`
- 创建：`packages/epistemo/stopping.py`
- 创建：`packages/evidence/discriminating_study.py`
- 创建：`packages/evidence/blindspot_models.py`
- 创建：`migrations/versions/0007_blindspots_and_discriminating_studies.py`
- 创建：`tests/unit/test_blindspot_score.py`
- 创建：`tests/unit/test_evidence_saturation.py`
- 创建：`tests/unit/test_discriminating_study.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_blindspot_score_uses_exact_five_dimension_formula() -> None:
    item = make_blindspot(impact="1", uncertainty="0.8", investigability="0.6", novelty="0.4", normalized_cost="0.2")
    assert score_blindspot(item) == Decimal("0.7600")


def test_budget_exhaustion_is_not_saturation() -> None:
    assert decide_stop(no_new_information_rounds=0, budget_remaining=0).reason == StopReason.BUDGET_EXHAUSTED
    assert decide_stop(no_new_information_rounds=1, budget_remaining=10).reason == StopReason.EVIDENCE_SATURATION


def test_discriminating_study_is_evidence_artifact() -> None:
    study = make_discriminating_study()
    assert study.node_type == EvidenceNodeType.DISCRIMINATING_STUDY
    assert study.artifact_type == "research_recommendation"
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_blindspot_score.py tests/unit/test_evidence_saturation.py tests/unit/test_discriminating_study.py -q`

预期：FAIL，评分、停止与判别研究服务缺失。

- [ ] **步骤 3：实现最小代码**

评分精确使用第 2.8 节 5 维公式。Saturation 仅在一个完整检索轮次没有新增 Claim、反例、边界、研究设计、独立数据源或显著信念更新时成立。`DiscriminatingStudy` 位于 evidence，字段为目标 Blindspot IDs、研究目标、推荐设计、关键数据、至少两个竞争预测、可解决 Blindspot、预期信息增益，固定 `artifact_type=research_recommendation`；通过 `TESTS`/`EXPOSES` 进入正式图，不能伪装为已有实证 Finding。迁移 `0007` 创建 `blindspots` 与 `discriminating_studies`，两表均以 `created_by_event_id` 外键追溯 Ledger，禁止级联删除。

- [ ] **步骤 4：验证通过与边界回归**

运行：

```bash
alembic upgrade head
python -m pytest tests/unit/test_blindspot_score.py tests/unit/test_evidence_saturation.py tests/unit/test_discriminating_study.py -q
python -m pytest tests/unit/test_evidence_graph_contracts.py -q
```

预期：全部 PASS；第六维输入被 Contract 拒绝；预算耗尽与饱和有不同 stop reason。

- [ ] **步骤 5：提交**

```bash
git add packages/epistemo/blindspots.py packages/epistemo/stopping.py packages/evidence/discriminating_study.py packages/evidence/blindspot_models.py migrations/versions/0007_blindspots_and_discriminating_studies.py tests/unit/test_blindspot_score.py tests/unit/test_evidence_saturation.py tests/unit/test_discriminating_study.py
git commit -m "feat(evidence): rank blindspots and propose discriminating studies"
```

**第 4 周退出门槛：** 完整 A–D Gate、三层审核、Claim revision、Dialectical Fold、Dissent、Lineage、五维 Blindspot 与 Evidence-owned DiscriminatingStudy 全部可测试且不可绕过。

---

# 第 5 周：先冻结后端 Workspace/SSE，再构建科研工作台

### 任务 23：实现 Workspace/API/SSE 契约与 Worker 入口

**文件：**
- 创建：`apps/bootstrap.py`
- 创建：`apps/api/main.py`
- 创建：`apps/api/schemas.py`
- 创建：`apps/api/routers/tasks.py`
- 创建：`apps/api/routers/sources.py`
- 创建：`apps/api/routers/workspace.py`
- 创建：`apps/api/routers/stream.py`
- 创建：`apps/worker/main.py`
- 创建：`apps/worker/jobs.py`
- 创建：`tests/integration/test_task_api.py`
- 创建：`tests/integration/test_workspace_api.py`
- 创建：`tests/integration/test_sse_contract.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_workspace_dto_is_whitelisted(api_client, seeded_task) -> None:
    response = await api_client.get(f"/api/tasks/{seeded_task.id}/workspace")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"task", "brief", "seats", "graph", "blindspots", "discriminating_studies", "dissents", "evolution", "paper_count", "independent_cluster_count", "workspace_version", "safety_notice"}
    assert "private_reasoning" not in response.text

async def test_sse_resumes_after_last_event_id(api_client, seeded_events) -> None:
    events = await collect_sse(api_client, "/api/tasks/core/stream", headers={"Last-Event-ID": "evt-2"}, limit=2)
    assert [event.id for event in events] == ["evt-3", "evt-4"]
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_task_api.py tests/integration/test_workspace_api.py tests/integration/test_sse_contract.py -q`

预期：FAIL，FastAPI 应用和路由缺失。

- [ ] **步骤 3：实现最小代码**

`apps/bootstrap.py` 是唯一组合根，注入 Repository/Gateway/Service/Orchestrator；路由不创建厂商客户端。写 API 精确包括创建 Research Contract、确认原子主张、上传 PDF、导入 DOI/BibTeX、run/pause/resume；读 API 包括 task/workspace/graph/events/node audit/source。无直接写图或强制批准端点。`WorkspaceSnapshot` 使用步骤 1 白名单；`SafetyNotice` 固定科研辅助/非诊断/非医疗建议/统计不确定性声明。SSE 格式为 `id/event/data`，事件含 `event_id/task_id/kind/workspace_version/payload`，支持 `Last-Event-ID`、心跳和断线续传。Worker 只调用应用服务。

- [ ] **步骤 4：验证通过与越权检查**

运行：

```bash
python -m pytest tests/integration/test_task_api.py tests/integration/test_workspace_api.py tests/integration/test_sse_contract.py -q
python -m pytest tests/integration/test_projector_permissions.py -q
python -m mypy apps packages
```

预期：全部 PASS；OpenAPI 不含直接建图/跳过 Gate 路由；Workspace/SSE 不泄漏私有记忆。

- [ ] **步骤 5：提交**

```bash
git add apps tests/integration/test_task_api.py tests/integration/test_workspace_api.py tests/integration/test_sse_contract.py
git commit -m "feat(api): freeze auditable workspace and SSE contracts"
```

### 任务 24：建立 Vite、Vitest、MSW、Playwright 与 Apple Design 基础

**文件：**
- 创建：`apps/web/package.json`
- 创建：`apps/web/package-lock.json`
- 创建：`apps/web/tsconfig.json`
- 创建：`apps/web/vite.config.ts`
- 创建：`apps/web/playwright.config.ts`
- 创建：`apps/web/index.html`
- 创建：`apps/web/src/main.tsx`
- 创建：`apps/web/src/api/types.ts`
- 创建：`apps/web/src/api/client.ts`
- 创建：`apps/web/src/api/sse.ts`
- 创建：`apps/web/src/styles/tokens.css`
- 创建：`apps/web/src/styles/materials.css`
- 创建：`apps/web/src/styles/motion.ts`
- 创建：`apps/web/src/test/msw.ts`
- 创建：`apps/web/src/test/workspace-fixture.ts`
- 创建：`apps/web/src/test/visual-contracts.test.ts`
- 创建：`apps/web/src/api/client.test.ts`

- [ ] **步骤 1：编写失败测试**

```tsx
// apps/web/src/test/visual-contracts.test.ts
import materials from '../styles/materials.css?raw'
import tokens from '../styles/tokens.css?raw'
import { motionTokens } from '../styles/motion'

it('锁定桌面科研工作台的实际视觉变量与系统主题', () => {
  for (const token of [
    '--font-sans', '--text-12', '--text-14', '--text-16', '--text-20', '--text-28',
    '--space-1', '--space-2', '--space-3', '--space-4', '--radius-1', '--radius-2',
    '--border-subtle', '--surface-canvas', '--surface-evidence', '--text-primary',
    '--status-support', '--status-refute', '--shadow-raised', '--z-sidebar', '--z-drawer',
  ]) expect(tokens).toContain(token)
  expect(tokens).toContain('color-scheme: light dark')
  expect(tokens).toContain('@media (prefers-color-scheme: dark)')
})

it('锁定材料降级、对比度和克制动效', () => {
  expect(materials).toContain('backdrop-filter: blur(var(--material-blur))')
  expect(materials).toContain('[data-surface="evidence"]')
  expect(materials).toContain('@media (prefers-reduced-transparency: reduce)')
  expect(materials).toContain('@media (prefers-contrast: more)')
  expect(motionTokens.defaultSpring).toMatchObject({ type: 'spring', bounce: 0, response: 0.35 })
  expect(motionTokens.reduced).toMatchObject({ duration: 0.12, properties: ['opacity'] })
})

// apps/web/src/api/client.test.ts
it('解析冻结的 WorkspaceSnapshot 且拒绝私有字段', async () => {
  server.use(http.get('/api/tasks/core/workspace', () => HttpResponse.json(workspaceFixture)))
  const snapshot = await fetchWorkspace('core')
  expect(snapshot.seats).toHaveLength(7)
  expect(JSON.stringify(snapshot)).not.toContain('private_reasoning')
})

it('按 event_id 去重 SSE', () => {
  const store = applyEvents(emptyStore(), [workspaceEvent('evt-1'), workspaceEvent('evt-1')])
  expect(store.eventIds).toEqual(['evt-1'])
})
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：

```bash
npm --prefix apps/web install --package-lock-only
npm --prefix apps/web ci
npm --prefix apps/web run test -- --run src/api/client.test.ts src/test/visual-contracts.test.ts
```

预期：FAIL，测试入口、API client、CSS Contract 或 motion tokens 尚不存在；失败不是缺少 npm 锁文件。

- [ ] **步骤 3：实现最小代码**

`package.json` 固定 React、Vite、Vitest、Testing Library、MSW、Playwright、React Flow 与 Motion 依赖以及 `dev/build/typecheck/test/e2e` 脚本。`types.ts` 与任务 23 JSON 字段逐字一致，`safety_notice` 必填，并以运行时解析拒绝未知顶层字段。MSW 返回固定 Workspace 与 SSE；Replay 模式不创建 EventSource；Live 模式按 event ID 去重并保存最后 ID。`workspace-fixture.ts` 导出 `workspaceFixture`、`findingFixture`、`fillCompleteContract()` 与测试 server；`sse.ts` 导出 `emptyStore()`、`workspaceEvent()`、`applyEvents()`。

`tokens.css` 必须给出以下可执行基线，而不是只写视觉形容词；默认 Light，系统为 Dark 时切换变量，桌面信息密度以 14 px 正文、紧凑控制和 8 pt 间距网格为基准，200% 字号时布局随 `rem` 扩展：

```css
:root {
  color-scheme: light dark;
  --font-sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, "SFMono-Regular", Consolas, monospace;
  --text-12: 0.75rem; --leading-12: 1rem; --tracking-12: 0.01em;
  --text-14: 0.875rem; --leading-14: 1.25rem; --tracking-14: 0;
  --text-16: 1rem; --leading-16: 1.5rem; --tracking-16: -0.005em;
  --text-20: 1.25rem; --leading-20: 1.625rem; --tracking-20: -0.012em;
  --text-28: 1.75rem; --leading-28: 2.125rem; --tracking-28: -0.02em;
  --space-1: 0.5rem; --space-2: 1rem; --space-3: 1.5rem; --space-4: 2rem; --space-6: 3rem;
  --radius-1: 0.25rem; --radius-2: 0.5rem; --radius-3: 0.75rem;
  --border-width: 1px; --border-subtle: #d7dce2; --border-strong: #6b7480;
  --surface-canvas: #f4f6f8; --surface-panel: #ffffff; --surface-evidence: #ffffff;
  --text-primary: #17202a; --text-secondary: #52606d; --text-inverse: #ffffff;
  --status-support: #176b45; --status-refute: #9c2f35; --status-qualify: #865b00;
  --status-unknown: #59636e; --status-focus: #165dba; --status-quarantine: #7a4d9d;
  --material-sidebar: rgb(248 250 252 / 88%); --material-toolbar: rgb(255 255 255 / 82%);
  --material-inspector: rgb(250 251 252 / 90%); --material-blur: 18px;
  --shadow-raised: 0 8px 24px rgb(17 24 39 / 12%); --shadow-drawer: 0 18px 48px rgb(17 24 39 / 18%);
  --z-base: 0; --z-sidebar: 10; --z-toolbar: 20; --z-drawer: 40; --z-dialog: 60;
}
@media (prefers-color-scheme: dark) {
  :root {
    --border-subtle: #3c4652; --border-strong: #9aa5b1;
    --surface-canvas: #11161c; --surface-panel: #192028; --surface-evidence: #202832;
    --text-primary: #f3f6f8; --text-secondary: #b8c1ca;
    --status-support: #62c995; --status-refute: #ff8c92; --status-qualify: #e7bd62;
    --status-unknown: #aeb7c1; --status-focus: #7fb5ff; --status-quarantine: #c9a0e8;
    --material-sidebar: rgb(24 31 39 / 90%); --material-toolbar: rgb(25 32 40 / 86%);
    --material-inspector: rgb(29 37 46 / 92%);
  }
}
```

`materials.css` 只允许侧栏、工具栏与检查器各自使用单层半透明材料；证据正文与 Exact Quote 固定为高对比实色表面，禁止材料嵌套。减少透明度时去掉 blur 并改为实色；增强对比度时显示清晰边框。结论与局限的表面规则保持同级并排，不以玻璃层区分可信度：

```css
[data-material="sidebar"], [data-material="toolbar"], [data-material="inspector"] {
  backdrop-filter: blur(var(--material-blur)) saturate(130%);
  border: var(--border-width) solid var(--border-subtle);
}
[data-material="sidebar"] { background: var(--material-sidebar); }
[data-material="toolbar"] { background: var(--material-toolbar); }
[data-material="inspector"] { background: var(--material-inspector); box-shadow: var(--shadow-drawer); }
[data-material] [data-material] { backdrop-filter: none; background: var(--surface-panel); }
[data-surface="evidence"], [data-surface="exact-quote"] {
  background: var(--surface-evidence); color: var(--text-primary); opacity: 1;
}
@media (prefers-reduced-transparency: reduce) {
  [data-material] { backdrop-filter: none; background: var(--surface-panel); }
}
@media (prefers-contrast: more) {
  [data-material], [data-surface="evidence"], [data-surface="exact-quote"] {
    background: var(--surface-evidence); border: 2px solid var(--border-strong);
  }
}
```

`motion.ts` 只导出统一配置和纯计算工具：默认 critically damped，`bounce = 0`、`response = 0.35`；只有具备释放速度的连续拖拽可使用 `momentumSpring` 的轻微 bounce。减少动态效果时以 120 ms opacity cross-fade 替代位移和 spring。任何 motion 回调不得分派 Evidence Event 或修改 Workspace 数据：

```ts
export const motionTokens = {
  defaultSpring: { type: 'spring' as const, bounce: 0, response: 0.35 },
  momentumSpring: { type: 'spring' as const, bounce: 0.12, response: 0.35 },
  reduced: { duration: 0.12, properties: ['opacity'] as const },
}
export const animatedProperties = ['transform', 'opacity'] as const
export const projectMomentum = (velocity: number, rate = 0.998) =>
  (velocity / 1000) * rate / (1 - rate)
```

- [ ] **步骤 4：验证通过与可复现安装**

运行：

```bash
npm --prefix apps/web ci
npm --prefix apps/web run typecheck
npm --prefix apps/web run test -- --run src/api/client.test.ts src/test/visual-contracts.test.ts
npx --prefix apps/web playwright install --with-deps chromium
npm --prefix apps/web run build
```

预期：全部 PASS；视觉 Contract 包含字体、字号/行高/字距、8 pt spacing、圆角、边框、背景、文字、状态色、材料层、阴影与 z-index；Light 与系统 Dark 均有实值；`dist/` 构建成功；全新 `npm ci` 不修改 lockfile。

- [ ] **步骤 5：提交**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/tsconfig.json apps/web/vite.config.ts apps/web/playwright.config.ts apps/web/index.html apps/web/src/main.tsx apps/web/src/api apps/web/src/styles apps/web/src/test
git commit -m "build(web): establish reproducible instrument design stack"
```

### 任务 25：实现 Research Contract、Research Brief 与 7 席工作台

**文件：**
- 创建：`apps/web/src/features/research/ResearchContractForm.tsx`
- 创建：`apps/web/src/features/brief/ResearchBriefView.tsx`
- 创建：`apps/web/src/features/council/CouncilRail.tsx`
- 创建：`apps/web/src/features/workbench/ResearchWorkbenchPage.tsx`
- 创建：`apps/web/src/features/workbench/ResearchWorkbenchPage.test.tsx`
- 创建：`apps/web/src/features/workbench/ResearchWorkbenchPage.responsive.test.tsx`
- 创建：`apps/web/src/features/research/ResearchContractForm.test.tsx`

- [ ] **步骤 1：编写失败测试**

```tsx
it('Research Contract 提交问题、范围、预算、DOI、BibTeX 和 PDF', async () => {
  render(<ResearchContractForm onSubmit={submit} />)
  await fillCompleteContract(user)
  const create = screen.getByRole('button', { name: '创建研究任务' })
  fireEvent.pointerDown(create)
  expect(create).toHaveAttribute('data-pressed', 'true')
  fireEvent.pointerUp(create)
  await user.click(create)
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({
    question: expect.any(String), scope: expect.any(Object), budget: expect.any(Object),
    user_evidence: expect.objectContaining({
      dois: expect.any(Array), bibtex_entries: expect.any(Array), pdf_object_ids: expect.any(Array),
    }),
  }))
})

it('在 1440 × 900 提供当前位置、当前任务、退出路径、并排结论与局限以及恰好 7 个结构化状态行', () => {
  render(<ResearchWorkbenchPage snapshot={workspaceFixture} viewport={{ width: 1440, height: 900 }} />)
  expect(screen.getByRole('navigation', { name: '研究路径' })).toHaveTextContent('Research Brief')
  expect(screen.getByTestId('current-task')).toHaveTextContent(workspaceFixture.task.question)
  expect(screen.getByRole('link', { name: '返回研究任务' })).toBeVisible()
  expect(screen.getByRole('main')).toHaveAttribute('data-layout', 'three-column')
  expect(screen.getByRole('region', { name: '当前判断' })).toBeVisible()
  expect(screen.getByRole('region', { name: '局限与未知项' })).toBeVisible()
  expect(screen.getAllByRole('listitem', { name: /科学家状态/ })).toHaveLength(7)
  expect(screen.queryByRole('log', { name: /聊天/ })).toBeNull()
})

it.each([
  [1024, 768, 'drawer'],
  [1440, 900, 'three-column'],
])('在 %i × %i 使用 %s 布局且保留字号放大语义', (width, height, layout) => {
  render(<ResearchWorkbenchPage snapshot={workspaceFixture} viewport={{ width, height }} />)
  expect(screen.getByRole('main')).toHaveAttribute('data-layout', layout)
  expect(screen.getByRole('main')).not.toHaveStyle({ height: expect.stringMatching(/^\d+px$/) })
  expect(screen.getByRole('button', { name: '打开证据检查器' })).toHaveAttribute('aria-expanded', 'false')
})
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：

```bash
npm --prefix apps/web run test -- --run src/features/research src/features/workbench
```

预期：FAIL，组件、即时 pressed 状态、wayfinding 或响应式工作台 Contract 不存在。

- [ ] **步骤 3：实现最小代码**

表单字段与后端 Contract 一一对应，PDF 先上传取得 object ID，原子主张确认独立一步。按钮在 `pointerdown` 设置 pressed 反馈，在 `pointerup`、`pointercancel` 或指针离开提交范围时恢复；提交仍发生在有效 release/click，不用动画延迟业务动作。每屏明确显示当前位置、当前任务、可去视图与“返回研究任务”退出路径。

Research Brief 始终将当前条件化结论与局限/未知并排呈现，并同时显示论文数和独立证据簇数。CouncilRail 使用 `ul/li` 的 7 个结构化状态行，字段固定为席位、当前任务、最近结构化动作、审查量、未解决质询和置信变化；不使用头像聊天卡，不显示私有思维链；EpistemoBrain 只作为流程状态，不是第 8 席。

工作台在 1440 × 900 使用左侧研究导航、中部证据工作区、右侧检查器三栏；在 1024 × 768 保留左侧紧凑导航和中部工作区，右侧变为从右侧进入且可打断的 drawer。布局使用 `minmax(0, 1fr)`、内容滚动区和 `rem` 间距，不使用阻止 200% 字号重排的固定内容高度；焦点顺序按导航→主内容→检查器，关闭 drawer 后焦点返回触发按钮。视觉层级服务于目的、能动性、责任、熟悉、灵活、简洁与工艺，保持科研仪器的信息密度和严肃性。

```tsx
<main data-layout={viewport.width >= 1280 ? 'three-column' : 'drawer'}>
  <nav aria-label="研究路径">…</nav>
  <section aria-label="证据工作区">…</section>
  <aside data-material="inspector" aria-label="证据检查器">…</aside>
</main>
<ul aria-label="7 人议会状态">
  {seats.map((seat) => <li key={seat.id} aria-label={`${seat.role} 科学家状态`}>…</li>)}
</ul>
```

- [ ] **步骤 4：验证通过与直接反馈、wayfinding、响应式可访问性**

运行：

```bash
npm --prefix apps/web run test -- --run src/features/research src/features/brief src/features/council src/features/workbench
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

预期：全部 PASS；按钮在 pointer-down/active 立即反馈且不延迟提交；1440 宽为三栏，1024 宽右栏为可中断 drawer；当前任务、当前位置、可去视图和退出路径可读；200% 字号所需重排不受固定高度阻挡；键盘可完成 Contract；安全声明、结论和局限保持可见；7 席为结构化状态行而不是头像聊天卡。

- [ ] **步骤 5：提交**

```bash
git add apps/web/src/features/research apps/web/src/features/brief apps/web/src/features/council apps/web/src/features/workbench
git commit -m "feat(web): add responsive research council workbench"
```

### 任务 26：实现 Controversy Map、Evidence Inspector 与 Audit Trail

**文件：**
- 创建：`apps/web/src/features/map/graph-semantics.ts`
- 创建：`apps/web/src/features/map/ControversyMapView.tsx`
- 创建：`apps/web/src/features/map/AccessibleGraphOutline.tsx`
- 创建：`apps/web/src/features/audit/EvidenceInspector.tsx`
- 创建：`apps/web/src/features/audit/AuditTrailDrawer.tsx`
- 创建：`apps/web/src/features/map/ControversyMapView.test.tsx`
- 创建：`apps/web/src/features/audit/EvidenceInspector.test.tsx`
- 创建：`apps/web/src/features/audit/InspectorDrawer.motion.test.tsx`

- [ ] **步骤 1：编写失败测试**

```tsx
it('节点 pointer-down 即时选择且 10 节点/12 边语义不只依赖颜色', () => {
  render(<ControversyMapView graph={workspaceFixture.graph} />)
  fireEvent.pointerDown(screen.getByRole('button', { name: /Claim c-1/ }))
  expect(screen.getByRole('button', { name: /Claim c-1/ })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('complementary', { name: '证据检查器' })).toHaveAttribute('data-node-id', 'c-1')
  expect(Object.keys(NODE_SEMANTICS)).toHaveLength(9)
  expect(Object.keys(EDGE_SEMANTICS)).toHaveLength(12)
  expect(screen.getByTestId('edge-REFUTES')).toHaveAttribute('data-line-style', 'dashed')
  expect(screen.getByText('REFUTES')).toBeVisible()
  expect(screen.getByTestId('node-Claim')).toHaveAttribute('data-shape')
})

it('AccessibleGraphOutline 与画布共享选择且键盘可到达 Exact Quote', async () => {
  render(<ControversyMapView graph={workspaceFixture.graph} />)
  const outlineNode = screen.getByRole('treeitem', { name: /Claim c-1/ })
  outlineNode.focus()
  await user.keyboard('{Enter}')
  expect(screen.getByRole('button', { name: /Claim c-1/ })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByText('Exact Quote')).toBeVisible()
})

it('Finding Inspector 使用实色高对比 Quote 且不渲染注入私有字段', () => {
  render(<EvidenceInspector node={{ ...findingFixture, private_reasoning: 'secret' } as never} />)
  for (const label of ['Source', 'Study', 'Section', 'Page', 'Exact Quote', 'Extraction Agent', 'Verification Status']) {
    expect(screen.getByText(label)).toBeVisible()
  }
  expect(screen.getByTestId('exact-quote')).toHaveAttribute('data-surface', 'exact-quote')
  expect(screen.queryByText('secret')).toBeNull()
})

it('drawer 中途反向时从当前 presentation value 继续且不锁输入', async () => {
  const triggerRect = new DOMRect(980, 80, 32, 32)
  const onOpenChange = vi.fn()
  const { rerender } = render(<AuditTrailDrawer open triggerRect={triggerRect} onOpenChange={onOpenChange} />)
  const drawer = screen.getByRole('complementary', { name: '审计轨迹' })
  drawer.style.transform = 'translateX(42px)'
  const midTransform = getComputedStyle(drawer).transform
  rerender(<AuditTrailDrawer open={false} triggerRect={triggerRect} onOpenChange={onOpenChange} />)
  rerender(<AuditTrailDrawer open triggerRect={triggerRect} onOpenChange={onOpenChange} />)
  expect(drawer).toHaveAttribute('data-animation-from', midTransform)
  expect(drawer).toHaveAttribute('data-enter-edge', 'right')
  expect(drawer).toHaveAttribute('data-exit-edge', 'right')
  await user.click(screen.getByRole('button', { name: '关闭审计轨迹' }))
  expect(onOpenChange).toHaveBeenCalledWith(false)
})
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：

```bash
npm --prefix apps/web run test -- --run src/features/map src/features/audit
```

预期：FAIL，Map、Inspector、AccessibleGraphOutline 或可打断 drawer motion Contract 不存在。

- [ ] **步骤 3：实现最小代码**

React Flow 节点同时使用形状、图标/文字标签和状态；边同时使用方向、线型、箭头和文字标签，任何图形语义都不只靠颜色。节点在 pointer-down 更新本地选择并立即打开 Inspector，选择动作不得生成 Scientific Event；AccessibleGraphOutline 用 tree/treeitem、方向键、Enter 与画布共享同一 selection store，并提供与画布等价的节点、边、邻接关系和 Inspector 路径。

Evidence Inspector 显示 Source、Study、Section、Page、Exact Quote、Extraction Agent、Verification Status、方法字段和 A–D 等级；证据正文与 Quote 使用 `data-surface="evidence"` / `data-surface="exact-quote"` 的高对比实色表面。Audit Trail 显示 Event、每阶段 Gate 结果、Claim revision、Quarantine、Rejected、Reverted 与 Replayed，不提供批准按钮。

Inspector/审计 drawer 以触发元素为可理解来源，从右侧进入并原路退出；新目标在运动中到达时读取当前 presentation transform 和 velocity 后重定向，不先完成旧动画，不在 transition 期间禁用 pointer 或键盘输入。drawer 默认使用 `motionTokens.defaultSpring`，减少动态效果时只做短 cross-fade。手势调整宽度使用 Pointer Events、`setPointerCapture(pointerId)`、抓取偏移和 1:1 跟踪，只有 `transform` / `opacity` 参与逐帧动画；释放时才允许将真实拖拽速度交给轻微 bounce 的 momentum spring。

地图实时更新只合并新节点、边和状态，不调用自动 `fitView`，不改变用户 pan/zoom、当前选择或焦点；只有用户明确执行“适配全图”才调用 `fitView`：

```ts
const onPointerDown = (event: React.PointerEvent) => {
  event.currentTarget.setPointerCapture(event.pointerId)
  drag.start({ pointerId: event.pointerId, x: event.clientX, grabOffset: readGrabOffset(event) })
}
const onWorkspaceEvent = (event: WorkspaceEvent) => {
  graphStore.merge(event)
  // 不调用 fitView；保留 viewport、selection 与 focus。
}
```

- [ ] **步骤 4：验证通过与语义、动效、键盘等价性锁定**

运行：

```bash
npm --prefix apps/web run test -- --run src/features/map src/features/audit
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

预期：全部 PASS；9/12 语义键与后端逐字一致；节点选择在 pointer-down 立即响应；drawer 从右侧进入并原路退出，中途反向无跳变、无输入锁；拖拽 1:1 且使用 pointer capture；地图收到实时事件后不自动 fitView、不抢焦点；键盘可通过 AccessibleGraphOutline 从 Claim 到达同一 Exact Quote；Quote 为实色高对比；形状、线型、方向和文字提供非颜色语义。

- [ ] **步骤 5：提交**

```bash
git add apps/web/src/features/map apps/web/src/features/audit
git commit -m "feat(web): add interruptible controversy map audit flow"
```

### 任务 27：实现 Blindspot、Lineage、Evolution 与真实跨端视觉交互验收

**文件：**
- 创建：`apps/web/src/features/blindspots/BlindspotRadar.tsx`
- 创建：`apps/web/src/features/blindspots/DiscriminatingStudyCard.tsx`
- 创建：`apps/web/src/features/lineage/EvidenceLineageView.tsx`
- 创建：`apps/web/src/features/evolution/EvolutionView.tsx`
- 创建：`apps/web/src/features/blindspots/BlindspotRadar.test.tsx`
- 创建：`apps/web/src/features/lineage/EvidenceLineageView.test.tsx`
- 创建：`apps/web/src/features/evolution/EvolutionView.test.tsx`
- 创建：`apps/web/e2e/helpers/media-preferences.ts`
- 创建：`apps/web/e2e/helpers/interaction-assertions.ts`
- 创建：`apps/web/e2e/core-workspace.spec.ts`
- 创建：`apps/web/e2e/live-replay.spec.ts`
- 创建：`apps/web/e2e/visual-interaction.spec.ts`

- [ ] **步骤 1：编写失败测试**

```tsx
it('Radar 与列表共享五维分数、非颜色语义和研究建议标签', () => {
  render(<BlindspotRadar blindspots={workspaceFixture.blindspots} studies={workspaceFixture.discriminating_studies} />)
  for (const label of ['影响', '不确定性', '可调查性', '新颖度', '成本']) expect(screen.getByText(label)).toBeVisible()
  expect(screen.getByTestId('radar-point-confounding')).toHaveAttribute('data-shape', 'diamond')
  expect(screen.getByRole('table', { name: 'Blindspot 等价数据' })).toBeVisible()
  expect(screen.getByText('研究建议，不是已有实证结果')).toBeVisible()
})

it('Lineage 同时显示论文与独立簇并保留视口', () => {
  const { rerender } = render(<EvidenceLineageView lineage={workspaceFixture.lineage} />)
  panLineageTo({ x: 120, y: 48, zoom: 1.4 })
  rerender(<EvidenceLineageView lineage={appendLineageEvent(workspaceFixture.lineage)} />)
  expect(readLineageViewport()).toEqual({ x: 120, y: 48, zoom: 1.4 })
  expect(screen.getByText(/论文数/)).toBeVisible()
  expect(screen.getByText(/独立证据簇数/)).toBeVisible()
})

it('Evolution 历史视图只读且 Replay 不连接 SSE', () => {
  render(<EvolutionView events={workspaceFixture.events} mode="replay" eventSourceFactory={eventSourceFactory} />)
  expect(screen.getByRole('region', { name: '历史视图' })).toHaveAttribute('aria-readonly', 'true')
  expect(screen.queryByRole('button', { name: /批准|修改证据|写入图/ })).toBeNull()
  expect(eventSourceFactory).not.toHaveBeenCalled()
})

// apps/web/e2e/visual-interaction.spec.ts
for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }]) {
  test(`工作台 ${viewport.width}×${viewport.height} 的视觉与语义 Contract`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.goto('/tasks/core')
    await expect(page.getByRole('main')).toHaveAttribute('data-layout', viewport.width === 1440 ? 'three-column' : 'drawer')
    await expect(page.getByRole('region', { name: '当前判断' })).toBeVisible()
    await expect(page.getByRole('region', { name: '局限与未知项' })).toBeVisible()
    await expect(page).toHaveScreenshot(`workbench-${viewport.width}x${viewport.height}.png`, { maxDiffPixelRatio: 0.01 })
  })
}

test('运动中重新点击和反向拖拽不跳变也不锁输入', async ({ page }) => {
  await page.goto('/tasks/core/map')
  await page.getByRole('button', { name: /Claim c-1/ }).dispatchEvent('pointerdown')
  await page.waitForTimeout(80)
  const before = await page.getByRole('complementary', { name: '证据检查器' }).evaluate(readPresentationTransform)
  await page.getByRole('button', { name: '关闭证据检查器' }).click()
  await page.getByRole('button', { name: /Claim c-1/ }).dispatchEvent('pointerdown')
  const after = await page.getByRole('complementary', { name: '证据检查器' }).evaluate(readPresentationTransform)
  expect(transformJump(before, after)).toBeLessThan(2)
  await dragAndReverse(page.getByRole('separator', { name: '调整检查器宽度' }))
  await expect(page.getByRole('button', { name: '关闭证据检查器' })).toBeEnabled()
})

test('200% 字号重排不遮挡且键盘路径可退出', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto('/tasks/core')
  await page.addStyleTag({ content: ':root { font-size: 200% !important; }' })
  await expect(page.getByRole('region', { name: '当前判断' })).toBeVisible()
  await expect(page.getByRole('region', { name: '局限与未知项' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.keyboard.press('Tab')
  await expect(page.locator(':focus-visible')).toBeVisible()
  await page.getByRole('button', { name: '打开证据检查器' }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: '关闭证据检查器' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: '打开证据检查器' })).toBeFocused()
})

test('reduced motion、transparency 与 more contrast 使用可访问降级', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' })
  await installMediaPreferences(page, { reducedTransparency: true, moreContrast: true })
  await page.goto('/tasks/core/map')
  await page.getByRole('button', { name: /Claim c-1/ }).dispatchEvent('pointerdown')
  const drawer = page.getByRole('complementary', { name: '证据检查器' })
  await expect(drawer).toHaveAttribute('data-motion-mode', 'cross-fade')
  await expect(drawer).toHaveCSS('backdrop-filter', 'none')
  expect(parseFloat(await drawer.evaluate((element) => getComputedStyle(element).borderTopWidth))).toBeGreaterThanOrEqual(2)
  await expect(page.getByTestId('exact-quote')).toHaveAttribute('data-surface', 'exact-quote')
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`${colorScheme} 主题保留语义、焦点与高对比 Quote`, async ({ page }) => {
    await page.emulateMedia({ colorScheme })
    await page.goto('/tasks/core/map')
    await page.getByRole('button', { name: /Claim c-1/ }).focus()
    await expect(page.locator(':focus-visible')).toBeVisible()
    await expect(page.getByTestId('edge-REFUTES')).toHaveAttribute('data-line-style', 'dashed')
    await expect(page.getByTestId('exact-quote')).toHaveCSS('opacity', '1')
    await expect(page).toHaveScreenshot(`map-${colorScheme}.png`, { maxDiffPixelRatio: 0.01 })
  })
}
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：

```bash
npm --prefix apps/web run test -- --run src/features/blindspots src/features/lineage src/features/evolution
npm --prefix apps/web run e2e -- e2e/core-workspace.spec.ts e2e/live-replay.spec.ts e2e/visual-interaction.spec.ts
```

预期：FAIL，Radar、Lineage、Evolution、媒体偏好辅助或真实跨端视觉/可打断交互 Contract 不存在；后端 Contract 已存在，因此不以未定义 API 为失败原因。

- [ ] **步骤 3：实现最小代码**

Radar 以形状表示 Blindspot 类型、点大小表示不确定性、文字/数值表示五维评分，并提供键盘可操作的等价表格；DiscriminatingStudyCard 显示目标、设计、数据、竞争预测、Blindspot、信息增益和“研究建议，不是已有实证结果”。Radar 的连续阈值/时间窗拖拽使用 Pointer Events、pointer capture、1:1 跟踪和可打断 spring；只在连续拖拽越过有效边界时使用渐进 rubber-band，按钮、离散选择和抽屉不得使用 rubber-band。

Lineage 同时显示论文数、独立证据簇数和关系依据；拖拽/pan 可中断并从当前 presentation value 延续。Evolution 回放 Claim revision、Quarantine、Resurrect、Dissent、Dialectical Fold 和证据降级；时间线拖拽可中断，越界时才使用 rubber-band；历史视图明确 `aria-readonly="true"`，不呈现任何写入或批准控制，不修改当前 Evidence Graph。Replay 模式不构造 EventSource，也不连接 SSE；Live 新事件只增量合并，不抢焦点，不重置 Map、Radar、Lineage 或 Evolution 的视口/时间位置。

Playwright 使用真实 API 路径验证 Brief→Claim→Quote→Audit→Lineage→Blindspot→DiscriminatingStudy→Evolution。`media-preferences.ts` 在应用脚本运行前稳定模拟 `prefers-reduced-transparency: reduce` 与 `prefers-contrast: more`；Playwright 原生 `reducedMotion` 与 `colorScheme` 覆盖 reduced motion 和深浅主题。验收矩阵必须覆盖：

- 1440 × 900 三栏与 1024 × 768 右侧 drawer；
- 根字号 200% 时无文本/控件遮挡、无水平页面溢出，结论与局限仍可到达；
- 完整键盘路径，焦点可见，打开/关闭 drawer 后焦点归还；
- reduced motion 使用短 cross-fade，无位移、spring 或 rubber-band；
- reduced transparency 使用实色且无 backdrop blur；more contrast 显示明确边框；
- Light 与 Dark 主题中的正文、Exact Quote、状态标签和焦点环可辨识；
- 截图阈值只辅助发现视觉回归，正确性还必须通过角色、可见文本、形状/线型、ARIA 状态、边框、computed style、焦点和网络请求断言，不能把颜色作为唯一判据；
- 中途重新点击 drawer、反向拖拽 inspector/radar/timeline，逐帧采样 transform 验证跳变小于 2 px，交互期间按钮与键盘输入不被锁定；
- 实时事件到达后 activeElement、选中节点与 viewport 均不变；Replay 网络记录中没有 stream/SSE 请求。

```ts
// apps/web/e2e/helpers/media-preferences.ts
const mediaQueryResult = (media: string, matches: boolean): MediaQueryList => ({
  media, matches, onchange: null,
  addListener: () => undefined, removeListener: () => undefined,
  addEventListener: () => undefined, removeEventListener: () => undefined,
  dispatchEvent: () => false,
})

export async function installMediaPreferences(
  page: Page,
  preferences: { reducedTransparency?: boolean; moreContrast?: boolean },
) {
  await page.addInitScript((value) => {
    const native = window.matchMedia.bind(window)
    const result = (media: string, matches: boolean): MediaQueryList => ({
      media, matches, onchange: null,
      addListener: () => undefined, removeListener: () => undefined,
      addEventListener: () => undefined, removeEventListener: () => undefined,
      dispatchEvent: () => false,
    })
    window.matchMedia = (query) => {
      if (query === '(prefers-reduced-transparency: reduce)') return result(query, !!value.reducedTransparency)
      if (query === '(prefers-contrast: more)') return result(query, !!value.moreContrast)
      return native(query)
    }
  }, preferences)
}

// apps/web/e2e/helpers/interaction-assertions.ts
export const readPresentationTransform = (element: Element) => getComputedStyle(element).transform
export const transformJump = (before: string, after: string) =>
  Math.abs(new DOMMatrix(before).m41 - new DOMMatrix(after).m41)

export async function dragAndReverse(handle: Locator) {
  const box = await handle.boundingBox()
  if (!box) throw new Error('缺少可拖拽边界')
  await handle.page().mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await handle.page().mouse.down()
  await handle.page().mouse.move(box.x - 80, box.y, { steps: 4 })
  await handle.page().mouse.move(box.x - 20, box.y, { steps: 4 })
  await handle.page().mouse.up()
}
```

- [ ] **步骤 4：验证通过与完整视觉/交互验收**

运行：

```bash
npm --prefix apps/web run test -- --run
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run e2e -- e2e/core-workspace.spec.ts e2e/live-replay.spec.ts e2e/visual-interaction.spec.ts
```

预期：全部 PASS；真实核心路径可用；1440 × 900、1024 × 768、200% 字号、键盘路径、reduced motion、reduced transparency、more contrast、Light/Dark 全部通过语义与视觉验收；运动中重新点击/拖拽没有跳变或输入锁；仅连续拖拽边界出现 rubber-band；历史视图只读；新事件不抢焦点、不重置视口；Replay 网络记录无 stream/SSE 请求。

- [ ] **步骤 5：提交**

```bash
git add apps/web/src/features/blindspots apps/web/src/features/lineage apps/web/src/features/evolution apps/web/e2e
git commit -m "feat(web): validate fluid evidence exploration workflows"
```

**第 5 周退出门槛：** 后端 Workspace/API/SSE 契约先完成；前端可用 MSW 单测和真实 API 跨端 E2E；所有核心视图均显示科研安全声明且不暴露私有推理。

---

# 第 6 周：报告、Replay、Compose 与故障恢复

### 任务 28：实现 Research Brief、审计报告与心理健康安全门

**文件：**
- 创建：`packages/reports/contracts.py`
- 创建：`packages/reports/research_brief.py`
- 创建：`packages/reports/markdown.py`
- 创建：`packages/reports/json_export.py`
- 创建：`packages/reports/safety.py`
- 创建：`apps/api/routers/reports.py`
- 创建：`tests/unit/test_report_safety.py`
- 创建：`tests/integration/test_report_api.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_mental_health_report_contains_required_safety_statement(report) -> None:
    text = render_markdown(report)
    assert "AI 辅助科研工具" in text
    assert "不是临床诊断或医疗建议" in text
    assert "模型置信度不替代统计不确定性或专家判断" in text
    assert "证据覆盖" in text and "系统局限" in text

async def test_report_export_does_not_leak_pdf_or_signed_url(api_client, seeded_private_pdf) -> None:
    response = await api_client.get("/api/tasks/core/reports/markdown")
    assert "X-Amz-Signature" not in response.text
    assert seeded_private_pdf.local_path not in response.text
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_report_safety.py tests/integration/test_report_api.py -q`

预期：FAIL，报告生成器与路由缺失。

- [ ] **步骤 3：实现最小代码**

只读投影生成 Markdown/JSON，固定包含范围、检索策略、执行摘要、原子主张、支持/反驳/限定证据、因果/测量/统计/边界审核、Blindspot、Dissent、DiscriminatingStudy、审计覆盖、未知项、预算缺口、系统局限、参考文献和 Event 附录。`SafetyPolicy` 对心理健康任务强制插入步骤 1 三句声明。Quote 只导出已授权最小片段，不导出 PDF 全文、对象 key、签名 URL 或用户路径。

- [ ] **步骤 4：验证通过与 Workspace 一致性**

运行：

```bash
python -m pytest tests/unit/test_report_safety.py tests/integration/test_report_api.py -q
python -m pytest tests/integration/test_workspace_api.py -q
```

预期：全部 PASS；报告论文数/独立簇数/结论/局限与 Workspace 同版本一致。

- [ ] **步骤 5：提交**

```bash
git add packages/reports apps/api/routers/reports.py tests/unit/test_report_safety.py tests/integration/test_report_api.py
git commit -m "feat(reports): export safe auditable research briefs"
```

### 任务 29：实现确定性 Replay、Compose 迁移门与健康检查

**文件：**
- 创建：`packages/evidence/replay.py`
- 创建：`apps/worker/replay_job.py`
- 创建：`docker-compose.yml`
- 创建：`.env.example`
- 创建：`apps/api/Dockerfile`
- 创建：`apps/worker/Dockerfile`
- 创建：`apps/web/Dockerfile`
- 创建：`tests/integration/test_replay_failures.py`
- 创建：`tests/e2e/test_replay.py`
- 创建：`tests/e2e/test_compose_stack.py`

- [ ] **步骤 1：编写失败测试**

```python
async def test_replay_rebuilds_same_graph_without_gateways(replay_service, core_case, gateway_spies) -> None:
    expected = await core_case.normalized_graph_hash()
    await core_case.clear_rebuildable_projection()
    result = await replay_service.rebuild(core_case.task_id)
    assert result.normalized_graph_hash == expected
    assert gateway_spies.model.external_call_count == 0
    assert gateway_spies.tool.external_call_count == 0

async def test_compose_waits_for_migration_and_health(started_stack) -> None:
    assert started_stack.service_exit_code("migrate") == 0
    assert await started_stack.health("api") == "healthy"
    assert await started_stack.health("worker") == "healthy"
    assert await started_stack.health("postgres") == "healthy"
    assert await started_stack.health("redis") == "healthy"
    assert await started_stack.health("minio") == "healthy"
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/integration/test_replay_failures.py tests/e2e/test_replay.py tests/e2e/test_compose_stack.py -q`

预期：FAIL，Replay/Compose 尚不存在。

- [ ] **步骤 3：实现最小代码**

Replay 只读 Ledger、Source version 与固定快照，从空的可重建投影顺序恢复 Graph、Quarantine、Resurrect、Dissent、Lineage、Fold；不调用 Model/Tool Gateway，不删除 Ledger；序列缺口/哈希错误闭锁失败。故障注入覆盖 Event 已写但 checkpoint 未写、两个 Projector 竞争、损坏快照、SSE 重复。Compose 定义 PostgreSQL/Redis/MinIO 健康检查；一次性 migrate 使用 migrator 凭据；API/Worker 依赖 migrate `service_completed_successfully` 和基础服务 healthy；Projector Worker 单独获得 projector DSN；应用 DSN 无图写权限。

- [ ] **步骤 4：验证通过、Replay 与一键启动**

运行：

```bash
python -m pytest --help
python -m pytest tests/integration/test_replay_failures.py tests/e2e/test_replay.py -q --count=2
docker compose up -d --build --wait
python -m pytest tests/e2e/test_compose_stack.py -q
docker compose down -v
```

预期：`pytest --help` 包含 `--count`；两次 Replay 图摘要一致；migrate 退出码 0；API/Worker/PostgreSQL/Redis/MinIO 均 healthy；普通 app DSN 仍不能写图。

- [ ] **步骤 5：提交**

```bash
git add packages/evidence/replay.py apps/worker/replay_job.py docker-compose.yml .env.example apps/api/Dockerfile apps/worker/Dockerfile apps/web/Dockerfile tests/integration/test_replay_failures.py tests/e2e/test_replay.py tests/e2e/test_compose_stack.py
git commit -m "build: gate healthy stack on migrations and replay"
```

**第 6 周退出门槛：** 报告安全且可审计；Replay 不调用外部服务并可重复；Compose 显式迁移、健康依赖和双数据库写权限全部通过。

---

# 第 7 周：ForesightBlindspot 案例、变体与完整指标

### 任务 30：冻结 3 个完整、5 个轻量与 1 个演示案例并建立双人标注

**文件：**
- 创建：`packages/evaluation/contracts.py`
- 创建：`packages/evaluation/corpus.py`
- 创建：`packages/evaluation/annotation.py`
- 创建：`demo/full/case-01/manifest.json`
- 创建：`demo/full/case-02/manifest.json`
- 创建：`demo/full/case-03/manifest.json`
- 创建：`demo/light/case-01/manifest.json`
- 创建：`demo/light/case-02/manifest.json`
- 创建：`demo/light/case-03/manifest.json`
- 创建：`demo/light/case-04/manifest.json`
- 创建：`demo/light/case-05/manifest.json`
- 创建：`demo/core_case/manifest.json`
- 创建：`tests/evals/test_case_inventory.py`
- 创建：`tests/evals/test_temporal_leakage.py`
- 创建：`tests/evals/test_annotation_workflow.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_case_inventory_is_exact() -> None:
    inventory = load_case_inventory(Path("demo"))
    assert inventory.full_count == 3
    assert inventory.light_count == 5
    assert inventory.demo_count == 1


def test_each_gold_item_has_two_blind_annotations_and_adjudication(case_inventory) -> None:
    for item in case_inventory.gold_items:
        assert len(item.independent_annotations) == 2
        assert item.independent_annotations[0].annotator_id != item.independent_annotations[1].annotator_id
        assert item.adjudication.adjudicator_id not in {a.annotator_id for a in item.independent_annotations}
        assert item.system_identity_visible_to_annotators is False
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/evals/test_case_inventory.py tests/evals/test_temporal_leakage.py tests/evals/test_annotation_workflow.py -q`

预期：FAIL，案例 manifest 与评测 Contract 不存在。

- [ ] **步骤 3：实现最小代码**

每个 manifest 固定案例 ID、类型、截止日期、封闭语料文件 SHA-256、允许来源、截止日前先兆、截止日后确认、金标准 Blindspot、模型参数可能含未来知识声明。Corpus 拒绝截止日后来源、现代排名和开放搜索。每个金标准项保存 Annotator A/B 独立盲标、冲突字段、第三人仲裁、最终标签；禁止在标注输入中显示系统变体身份。

- [ ] **步骤 4：验证通过与案例泄漏防护**

运行：

```bash
python -m pytest tests/evals/test_case_inventory.py tests/evals/test_temporal_leakage.py tests/evals/test_annotation_workflow.py -q
python -m ruff check packages/evaluation tests/evals
```

预期：全部 PASS；案例计数严格为 3+5+1；任一语料哈希变化或未来来源会失败。

- [ ] **步骤 5：提交**

```bash
git add packages/evaluation/contracts.py packages/evaluation/corpus.py packages/evaluation/annotation.py demo tests/evals/test_case_inventory.py tests/evals/test_temporal_leakage.py tests/evals/test_annotation_workflow.py
git commit -m "test(eval): freeze blinded foresight case set"
```

### 任务 31：实现全部 5 种系统变体、6 个消融、规格指标与统计

**文件：**
- 创建：`packages/evaluation/variants.py`
- 创建：`packages/evaluation/metrics.py`
- 创建：`packages/evaluation/statistics.py`
- 创建：`packages/evaluation/runner.py`
- 创建：`tests/evals/test_variants.py`
- 创建：`tests/evals/test_metrics_coverage.py`
- 创建：`tests/evals/test_statistics.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_all_comparison_variants_and_ablations_exist() -> None:
    assert set(SystemVariant) == {
        SystemVariant.SINGLE_AGENT_DEEP_RESEARCH,
        SystemVariant.FIXED_MULTI_AGENT_DEBATE,
        SystemVariant.COUNCIL_LINEAR_CONTEXT,
        SystemVariant.COUNCIL_MEMOBRAIN_NO_EVIDENCE_ENGINE,
        SystemVariant.FULL_POLISCOPE,
    }
    assert len(AblationVariant) == 6


def test_every_spec_metric_is_measured_or_explained(metric_registry) -> None:
    expected = set(SPEC_METRIC_NAMES)
    assert set(metric_registry) == expected
    assert all(record.status == MetricStatus.MEASURED or (record.status == MetricStatus.NOT_MEASURED and record.reason) for record in metric_registry.values())
```

`SPEC_METRIC_NAMES` 精确包含 25 项：Blindspot Recall、Blindspot Precision、高影响盲点召回、具体性、可行动性、前瞻距离、Citation Existence Accuracy、Citation Entailment Accuracy、Evidence Independence Accuracy、Causal Overclaim Rate、全文证据覆盖率、Context Compression Ratio、Scientific Backbone Retention、Contradiction Retention、Long-Horizon Drift、Recovery Quality、Role Diversity、Debate Utility、Belief Update Quality、Dissent Preservation Rate、False Consensus Rate、Cost per Valid Blindspot、Token per Admitted Finding、Redundant Work Ratio、每轮边际增益。

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/evals/test_variants.py tests/evals/test_metrics_coverage.py tests/evals/test_statistics.py -q`

预期：FAIL，变体、指标注册表和统计实现缺失。

- [ ] **步骤 3：实现最小代码**

5 种比较系统由同一 Runner 的 `VariantConfig` 实现。6 个消融精确为无独立预承诺、无对抗性证伪者、无证据审计员、普通 Fold、无证据谱系、无 MemoBrain；每次只改变目标开关并记录配置哈希。25 项指标全部返回 `MetricRecord(name,status,value,unit,reason,numerator,denominator)`；无法可靠计算时必须 `NOT_MEASURED` 且 reason 非空，不能填 0。统计实现 Cohen’s Kappa 或 Krippendorff’s Alpha、逐案例配对差、Bootstrap 95% CI、Wilcoxon 符号秩检验和效应量。

- [ ] **步骤 4：验证通过与完整评测输出**

运行：

```bash
python -m pytest tests/evals/test_variants.py tests/evals/test_metrics_coverage.py tests/evals/test_statistics.py -q
python -m pytest tests/evals -q
```

预期：全部 PASS；5 个比较系统、6 个消融均有唯一配置哈希；每个案例输出逐案例结果；25 个指标无缺项且只有 measured/not_measured 两种显式状态。

- [ ] **步骤 5：提交**

```bash
git add packages/evaluation/variants.py packages/evaluation/metrics.py packages/evaluation/statistics.py packages/evaluation/runner.py tests/evals/test_variants.py tests/evals/test_metrics_coverage.py tests/evals/test_statistics.py
git commit -m "feat(eval): run complete baselines ablations and metrics"
```

**第 7 周退出门槛：** 3 个完整、5 个轻量、1 个演示案例均通过哈希和时间切片检查；双人独立盲标加第三人仲裁可审计；5 种比较系统、6 个消融和全部 25 项指标具结果或明确 not_measured 原因。

---

# 第 8 周：固定演示、全量验收与发布门

### 任务 32：实现 Poliscope CLI、Claude Code Skill 与 Codex Skill

**文件：**
- 修改：`pyproject.toml`
- 创建：`apps/cli/__init__.py`
- 创建：`apps/cli/client.py`
- 创建：`apps/cli/main.py`
- 创建：`.claude/skills/poliscope/SKILL.md`、`.claude/skills/poliscope/scripts/new_contract.py`
- 创建：`.codex/skills/poliscope/SKILL.md`、`.codex/skills/poliscope/scripts/new_contract.py`
  （改用 Claude Code / Codex 各自的官方发现路径，取代最初计划的裸 `skills/poliscope/`——后者
  不会被任何一个工具自动发现，见实施时确认的官方约定）
- 创建：`tests/unit/test_cli_contract.py`
- 创建：`tests/integration/test_cli_api_parity.py`
- 创建：`tests/e2e/test_agent_skills.py`
- 修改：`README.md`

- [ ] **步骤 1：核验调用端规范并编写失败测试**

实现前核验 Claude Code 与 Codex 当前官方 Skill 发现目录、`SKILL.md` frontmatter、安装方式和允许的脚本/资源结构，将核验日期与官方链接记录在两个安装说明中。Skill 正文使用双方共同支持的最小 Agent Skills 子集；平台差异只放安装适配说明，不复制研究工作流。

```python
def test_cli_exposes_stable_research_commands(cli_runner) -> None:
    result = cli_runner.invoke(["research", "--help"])
    assert result.exit_code == 0
    assert {"start", "confirm-claims", "status", "watch", "export"} <= extract_commands(result.output)


async def test_cli_and_http_create_equivalent_contracts(cli_client, api_client, contract_payload) -> None:
    cli_task = await cli_client.start(contract_payload)
    api_task = await api_client.start(contract_payload)
    assert cli_task.normalized_contract == api_task.normalized_contract
    assert cli_task.status == api_task.status == "AWAITING_CLAIM_CONFIRMATION"


def test_skills_are_thin_adapters(skill_specs) -> None:
    for spec in skill_specs:
        assert spec.invokes_poliscope_cli_or_api
        assert not spec.direct_model_or_tool_calls
        assert not spec.direct_database_or_graph_writes
        assert spec.requires_claim_confirmation
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/unit/test_cli_contract.py tests/integration/test_cli_api_parity.py tests/e2e/test_agent_skills.py -q`

预期：FAIL，CLI 入口与 Skill 包尚不存在。

- [ ] **步骤 3：实现最小代码**

`poliscope` CLI 只作为任务 23 HTTP/SSE Contract 的客户端，支持从 JSON 文件或标准输入读取 Research Contract；`start` 返回 `task_id` 与待确认原子主张，`confirm-claims` 显式确认后才能运行，`status` 提供机器可读 JSON，`watch` 支持 `Last-Event-ID` 恢复，`export` 只下载已授权的 Markdown/JSON/图与审计产物。退出码稳定区分参数错误、确认待定、服务不可用、任务失败和导出不完整；不在终端输出 PDF、签名 URL、私有思维链或未授权全文。

共享 `skills/poliscope/SKILL.md` 描述何时调用 Poliscope、如何收集 Research Contract、确认原子主张、启动/恢复长任务和保存产物。Claude Code 与 Codex 安装说明把同一共享 Skill 暴露到各自官方发现目录；两个适配不得嵌入科研 Prompt、复制议会逻辑或把调用端自身回答当作 Poliscope 已审计结论。默认不扫描仓库和上传文件；只有用户明确指定路径后才读取或提交材料。

- [ ] **步骤 4：验证通过与跨入口等价性**

运行：

```bash
python -m pytest tests/unit/test_cli_contract.py tests/integration/test_cli_api_parity.py tests/e2e/test_agent_skills.py -q
python -m poliscope --help
python -m ruff check apps/cli tests/unit/test_cli_contract.py tests/integration/test_cli_api_parity.py tests/e2e/test_agent_skills.py
python -m mypy apps/cli
```

预期：全部 PASS；同一 Recorded 核心案例经 Web/API、CLI、Claude Code Skill 与 Codex Skill 获得相同 `task_id` 生命周期和等价导出 hash；中断 `watch` 后可恢复；两种 Skill 均保留确认门、Evidence Gate、安全声明和审计，不直接产生科研结论。

- [ ] **步骤 5：提交**

```bash
git add pyproject.toml apps/cli skills/poliscope tests/unit/test_cli_contract.py tests/integration/test_cli_api_parity.py tests/e2e/test_agent_skills.py README.md
git commit -m "feat(skill): expose governed Poliscope agent entrypoints"
```

### 任务 33：冻结核心案例并执行全量验收

**文件：**
- 创建：`demo/core_case/recorded_model_calls.jsonl`
- 创建：`demo/core_case/recorded_tool_calls.jsonl`
- 创建：`demo/core_case/replay_events.jsonl`
- 创建：`demo/core_case/expected_graph.json`
- 创建：`demo/core_case/expected_report.json`
- 创建：`tests/e2e/test_core_case_acceptance.py`
- 创建：`tests/e2e/test_release_gate.py`
- 创建：`docs/schema.md`
- 创建：`docs/testing.md`
- 创建：`docs/demo.md`
- 修改：`README.md`

- [ ] **步骤 1：编写失败测试**

```python
async def test_core_case_meets_complete_mvp_acceptance(core_case_result) -> None:
    assert len(core_case_result.precommitments) == 7
    assert len(core_case_result.final_rejudgments) == 7
    assert core_case_result.private_memory_count == 7
    assert core_case_result.process_graph_separate_from_evidence_graph
    assert core_case_result.direct_graph_writes == 0
    assert all(f.source_id and f.study_id and f.anchors for f in core_case_result.admitted_findings)
    assert core_case_result.effective_challenge_count >= 1
    assert core_case_result.audit_caused_revision_or_downgrade
    assert core_case_result.high_value_blindspots
    assert core_case_result.fold_preserved_opposition_and_dissent
    assert core_case_result.dissent_certificates
    assert core_case_result.discriminating_studies
    assert core_case_result.paper_count > core_case_result.independent_cluster_count
    assert core_case_result.workspace_has_brief_map_audit
    assert core_case_result.replay_hash == core_case_result.live_hash


def test_release_matrix_maps_every_spec_acceptance_item() -> None:
    matrix = load_acceptance_matrix(Path("docs/testing.md"))
    assert set(matrix.spec_items) == set(range(1, 17))
    assert all(item.test_path and item.expected_assertion for item in matrix.items)
```

- [ ] **步骤 2：运行并确认目标性失败**

运行：`python -m pytest tests/e2e/test_core_case_acceptance.py tests/e2e/test_release_gate.py -q`

预期：FAIL，核心录制资产、预期图/报告与验收矩阵缺失。

- [ ] **步骤 3：实现最小代码**

录制核心案例必须包含：7 席不同预承诺；引用审核将因果 Claim 降为相关/条件化支持；自报告测量 Blindspot；多论文共享数据集导致论文数大于独立簇数；日志研究改变边界；一个 Fold 后仍保留的 Dissent；一个 DiscriminatingStudy；预算、调用费用和未知 Evidence Slot。录制只含结构化输入哈希、输出、token、费用、延迟、重试和 Schema 状态，不含私有思维链。`docs/schema.md` 记录公共 Contract、9/12 图和 A–D 矩阵；`docs/testing.md` 将规格 16 项验收逐项映射到精确测试；`docs/demo.md` 固定 5 分钟 Live/Replay/快照演示步骤与安全声明。

- [ ] **步骤 4：验证通过与人工演示门**

运行：

```bash
alembic upgrade head
python -m pytest --help
python -m pytest tests/unit tests/integration tests/golden tests/e2e tests/evals -q
python -m pytest tests/e2e/test_replay.py -q --count=2
python -m pytest tests/unit/test_cli_contract.py tests/integration/test_cli_api_parity.py tests/e2e/test_agent_skills.py -q
python -m poliscope --help
python -m ruff check .
python -m mypy packages apps
npm --prefix apps/web ci
npm --prefix apps/web run typecheck
npm --prefix apps/web run test -- --run
npm --prefix apps/web run build
docker compose up -d --build --wait
npm --prefix apps/web run e2e
docker compose down -v
```

预期：全部 PASS；`pytest --help` 显示 `--count`；两次 Replay hash 相同；Compose migration 成功且服务健康；Playwright 真实核心路径通过。随后按 `docs/demo.md` 人工确认 6 个画面：7 席初始分歧、审计降级、论文数/独立簇数差异、Blindspot→DiscriminatingStudy、Fold 后 Dissent、无外部调用 Replay；任一画面缺失则发布门失败。

- [ ] **步骤 5：提交**

```bash
git add demo/core_case tests/e2e/test_core_case_acceptance.py tests/e2e/test_release_gate.py docs/schema.md docs/testing.md docs/demo.md README.md
git commit -m "release: freeze auditable Poliscope MVP demo"
```

**第 8 周退出门槛：** 规格 16 项 MVP 验收全部有自动化测试和界面/报告证据；单元、集成、Golden、E2E、评测、静态检查、类型检查、迁移、Compose 健康与重复 Replay 全部通过后，才能宣称 MVP 完成。

---

## 3. 关键路径与任务依赖

```text
1 Contract → 2 DB → 3 Gateway → 4 数据源 → 5 Packet
→ 6 最小 Gate → 7 首次受审投影
→ 8 Adapter → 9 Process Graph → 10 Fold → 11 Branch/恢复
→ 12 席位动作 → 13–16 完整 7 轮 → 17 持久状态机
→ 18 Research Service → 19 完整 Gate → 20 Fold/Dissent
→ 21 Lineage → 22 Blindspot/DiscriminatingStudy
→ 23 Workspace/API/SSE → 24 前端工程 → 25–27 工作台/E2E
→ 28 报告安全 → 29 Replay/Compose
→ 30 案例/标注 → 31 变体/指标 → 32 CLI/Skills → 33 发布门
```

强制依赖：任务 6 在任务 7 之前；任务 23 在任务 24–27 和任务 32 之前；任务 27 的真实跨端 E2E 在任务 23 后；任务 19 的完整 Gate 不改变“任何投影先过 Gate”的任务 6–7 边界；任务 30 的封闭案例在任务 31 运行评测前冻结；任务 32 的 CLI/Skills 必须复用任务 23 的 HTTP/SSE Contract，并在任务 33 发布门前通过跨入口等价性验收。

## 4. 每任务通用提交纪律

- 每个任务开始时只把该任务测试写红；如果失败来自拼写、导入路径或夹具错误，先修正测试，直到失败明确指向目标行为缺失。
- 步骤 3 只实现让当前精确测试通过的代码；新增 Prompt、模型解析器或 Recorded 响应时同时运行对应 Golden Test。
- 步骤 4 必须执行任务列出的精确命令和受影响回归；任何命令失败均不得执行步骤 5。
- 每次提交仅包含该任务“文件”列表；共享 Contract 如需改变，先增加回归测试并在当前任务文件列表中明确列出。
- 所有命令从仓库根目录执行；Python 命令使用 `python -m`，前端命令使用 `npm --prefix apps/web`。

## 5. 完成定义

只有同时满足以下条件，才允许宣称 Poliscope MVP 完成：

- 7 名科学家均有独立预承诺、Private MemoBrain 和最终独立复判或明确缺席记录；演示案例要求 7 席全部完成。
- Process Graph 与 Evidence Graph 分离，Process Graph 包含 DEBATE/DECISION/ASSIGNMENT 和角色化投影。
- 所有正式图修改来自先通过 Evidence Gate 的 Ledger Event，且普通应用身份无法写图。
- Evidence Graph 只有 9 种节点、12 种边；每个 StudyFinding 恰有一条 `DERIVED_FROM Source`。
- PaperEvidencePacket 完整记录 Study、样本、设计、变量、分析、方向、效应量、不确定性、作者结论/局限及数据/代码/预注册。
- A–D 准入矩阵、因果防升级、Claim 追加 revision、Quarantine/Fork/Merge/Resurrect、Dialectical Fold、Dissent 与 Lineage 均通过测试。
- Workspace、SSE、报告和前端不泄漏私有推理、PDF、签名 URL或本地路径；心理健康安全声明始终存在。
- 3 个完整案例、5 个轻量案例、1 个演示案例、双人盲标与第三人仲裁均可审计。
- 5 种比较系统、6 个消融和规格 25 项指标全部输出 measured 或带原因的 not_measured。
- Alembic 升降级、双数据库写身份、Compose migration/healthcheck、npm ci、Vitest、Playwright、pytest-repeat、Ruff、mypy 与两次确定性 Replay 全部通过。
- Web/API、Poliscope CLI、Claude Code Skill 与 Codex Skill 对同一核心案例保持 Contract、生命周期、确认门、审计语义和导出 hash 等价；Skill 不包含独立科研逻辑。
