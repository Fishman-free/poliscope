# Poliscope

> 面向计算社会科学争议问题的可审计深度研究智能体

Poliscope 组织 7 名具有互补认识能力的 AI 科学家，全程参与问题拆解、独立判断、论文取证、交叉质询、盲点调查和最终复判，最终生成一张**可审计的争议证据地图**。

它不追求更长的文献综述。它重点回答：**当前结论最可能错在哪里？哪些盲点、反例、边界条件或证据依赖会改变科研判断？**

---

## 目录

- [核心理念](#核心理念)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [CLI 命令参考](#cli-命令参考)
- [HTTP API 参考](#http-api-参考)
- [数据模型](#数据模型)
- [七名科学家](#七名科学家)
- [议会协议](#议会协议)
- [证据治理](#证据治理)
- [评测体系](#评测体系)
- [开发指南](#开发指南)
- [测试](#测试)
- [目录结构](#目录结构)
- [局限与伦理](#局限与伦理)
- [许可证与归属](#许可证与归属)

---

## 核心理念

Poliscope 的核心方法名为 **EpistemoBrain**——科学议会的集体执行记忆与组织控制层。与普通 Deep Research 和固定角色多智能体系统相比，它的差异体现在：

1. **独立预承诺**：7 名科学家先独立预承诺，再交换证据，减少锚定和角色同质化。
2. **结构化动作**：科学家只能提交 `PROPOSE`、`SUPPORT`、`CHALLENGE` 等结构化科研动作，不能用自由聊天直接修改正式结论。
3. **双图隔离**：MemoBrain 管理推理过程；正式证据由独立的 Evidence Graph 管理。
4. **精确溯源**：每项关键判断可回溯到具体论文、研究、页码和原文。
5. **证据谱系**：识别同一数据集、重叠样本和论文版本，避免重复计票。
6. **盲点悬赏**：将 Blindspot 作为一等科研对象，转化为可执行的判别研究。
7. **异议保真**：异议不会因上下文压缩或多数意见而消失。

### 首版非目标

Poliscope **不实现**：

- 全学科通用科研平台
- 自动控制实验室设备
- 任意论文代码的一键复现
- 全自动元分析
- 自训练或大规模微调模型
- 多人实时协作、订阅付费或插件市场

---

## 系统架构

### 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI |
| 数据校验 | Pydantic v2 |
| ORM | SQLAlchemy 2.0 (async) |
| 数据库 | PostgreSQL + pgvector |
| 缓存/队列 | Redis + arq |
| 迁移 | Alembic |
| Lint | Ruff |
| 测试 | pytest + pytest-asyncio |

### 模块划分

```
packages/
├── kernel/        # 共享基础设施：ContractModel、FrozenDict、数据库、配置
├── council/       # 七名科学家、议会协议、轮次 Handler、共识与异议
├── epistemo/      # EpistemoBrain：状态机、预算、编排、停止条件、恢复
├── evidence/      # 双图治理：Event Ledger、Evidence Gate、Graph Projector、Lineage
├── memory/        # MemoBrain 适配器、Process Graph、Fold/Recall/Snapshot
├── models/        # 统一模型网关（Model Gateway）
├── tools/         # 统一工具网关（Tool Gateway）
├── papers/        # 论文发现、规范化、PDF 解析、Finding 抽取
├── research/      # 研究服务：需求矩阵、原子主张、任务生命周期
├── reports/       # Research Brief、安全门、Markdown/JSON 导出
└── evaluation/    # ForesightBlindspot 评测：语料、标注、消融

apps/
├── api/           # FastAPI 应用、REST 路由、SSE 流式端点
├── worker/        # 异步任务 Worker（arq）
├── cli/           # 命令行入口（poliscope 命令）
└── bootstrap.py   # 启动引导

demo/core_case/    # 演示用例：预期图与预期报告
docs/              # 设计规格、Schema、测试矩阵、演示脚本
skills/poliscope/  # Claude Code / Codex Skill 定义
tests/             # 单元、集成、Golden、E2E、评测测试
```

### 双图架构

```
┌──────────────────────────────────────────────────────┐
│                  Scientific Event Ledger              │
│         (幂等、可重放、可审计的事件日志)                │
└───────────────────────┬──────────────────────────────┘
                        │ 唯一写入者
                        ▼
┌──────────────────────────────────────────────────────┐
│                   Graph Projector                     │
│         (顺序处理事件，版本锁，生成正式图)              │
└─────────┬────────────────────────────┬───────────────┘
          │                            │
          ▼                            ▼
┌──────────────────┐        ┌──────────────────────┐
│   Process Graph  │        │    Evidence Graph    │
│  (过程记忆)      │        │   (正式科研证据)      │
│  MemoBrain 管理  │        │   9 种节点 12 种边   │
│  可 Flush/Fold   │        │   只允许可回溯折叠    │
└──────────────────┘        └──────────────────────┘
```

---

## 快速开始

### 环境要求

- Python 3.12
- PostgreSQL 16+（带 pgvector）
- Redis 5.2+
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip

### 安装

```bash
# 克隆仓库
git clone <repo-url> && cd poliscope

# 安装依赖
uv sync

# 激活虚拟环境
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 配置

设置环境变量（可写入 `.env` 文件）：

```bash
# 数据库
export POLISCOPE_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/poliscope"
export POLISCOPE_MIGRATOR_DATABASE_URL="postgresql+asyncpg://migrator:pass@localhost/poliscope"

# Redis
export POLISCOPE_REDIS_URL="redis://localhost:6379/0"

# 模型网关（示例）
export POLISCOPE_MODEL_PROVIDER="anthropic"
export POLISCOPE_MODEL_NAME="claude-sonnet-4-20250514"
export ANTHROPIC_API_KEY="sk-ant-..."

# 可选：S3 对象存储（用于 PDF 上传）
export POLISCOPE_S3_BUCKET="poliscope-objects"
export AWS_REGION="us-east-1"
```

### 初始化数据库

```bash
# 运行 Alembic 迁移（创建角色、表、索引）
alembic upgrade head
```

### 启动服务

```bash
# 终端 1：启动 FastAPI 服务
uvicorn apps.api.main:app --reload --port 8000

# 终端 2：启动异步 Worker
python -m apps.worker.main
```

### 验证安装

```bash
# 健康检查
curl http://localhost:8000/health

# 创建研究任务
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "question": "社交媒体使用是否导致青少年抑郁？",
    "scope": {
      "populations": ["adolescents", "young adults"],
      "regions": ["global"],
      "languages": ["en"],
      "date_from": "2015-01-01",
      "date_until": "2025-12-31",
      "evidence_priorities": ["CAUSAL_OR_REVERSE_CAUSAL", "MEASUREMENT", "REPLICATION"],
      "allow_preprints": false
    },
    "budget": {
      "wall_clock_minutes": 60,
      "model_cost_usd": 10.0,
      "tool_call_limit": 200,
      "source_limit": 50
    },
    "user_evidence": {"dois": [], "bibtex_entries": [], "pdf_object_ids": []}
  }'
```

---

## 使用指南

### 研究任务生命周期

```
创建任务 → 确认主张 → 排队执行 → 议会多轮取证 → 生成报告
   │            │           │              │            │
   ▼            ▼           ▼              ▼            ▼
 DRAFT     AWAITING_     QUEUED      DEGRADED_      COMPLETED
           CLAIM_                      RUNNING       or COMPLETED_
           CONFIRMATION                               WITH_GAPS
```

### 典型工作流

```
┌─────────┐     ┌──────────────┐     ┌─────────────┐
│ 1. 创建  │────▶│ 2. 确认主张   │────▶│ 3. 排队执行  │
│ 任务     │     │ (人工审核)    │     │             │
└─────────┘     └──────────────┘     └──────┬──────┘
                                            │
                                            ▼
┌─────────┐     ┌──────────────┐     ┌─────────────┐
│ 6. 导出  │◀────│ 5. 生成报告   │◀────│ 4. 议会运行  │
│ 报告     │     │             │     │ (7 轮协议)   │
└─────────┘     └──────────────┘     └─────────────┘
```

### 步骤详解

#### 1. 创建研究任务

提交研究问题、范围、预算和可选的用户证据（DOI / BibTeX / PDF）。

```bash
poliscope start --contract contract.json
```

#### 2. 审核并确认原子主张

系统将模糊问题拆解为可证伪的原子主张。研究者必须显式确认后任务才能运行。

```bash
poliscope confirm-claims --task-id <task-id> --claim-ids <id1> <id2>
```

#### 3. 监控运行状态

```bash
# 查看任务状态
poliscope status --task-id <task-id>

# 实时流式监控（SSE）
poliscope watch --task-id <task-id> --last-event-id <evt>
```

#### 4. 导出报告

```bash
# Markdown 格式
poliscope export --task-id <task-id> --format markdown > report.md

# JSON 格式
poliscope export --task-id <task-id> --format report.json > report.json
```

---

## CLI 命令参考

```
poliscope <command> [options]
```

| 命令 | 说明 | 必要参数 |
|---|---|---|
| `start` | 创建并初始化研究任务 | `--contract`（JSON 文件路径） |
| `confirm-claims` | 确认原子主张 | `--task-id`, `--claim-ids` |
| `status` | 查看任务当前状态 | `--task-id` |
| `watch` | 实时监控任务（SSE 流） | `--task-id`, `--last-event-id`（可选） |
| `export` | 导出研究报告 | `--task-id`, `--format`（markdown/json） |

---

## HTTP API 参考

### 任务管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/tasks` | 创建研究任务 |
| `GET` | `/tasks/{task_id}` | 获取任务状态 |
| `POST` | `/tasks/{task_id}/confirm-claims` | 确认原子主张 |
| `POST` | `/tasks/{task_id}/queue` | 将任务加入执行队列 |

### 工作空间与流式

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/workspace/{task_id}` | 获取工作空间快照（白名单 DTO） |
| `GET` | `/stream/{task_id}` | SSE 实时流（支持 `Last-Event-ID` 恢复） |

### SSE 事件格式

```
id: evt-12
event: workspace_update
data: {"status": "running", ...}
```

支持通过 `Last-Event-ID` 请求头恢复中断的流。

---

## 数据模型

### 核心契约（ContractModel）

所有领域模型继承自 `ContractModel`，具备以下特性：

- **不可变**：`frozen=True`
- **禁止额外字段**：`extra="forbid"`
- **自动冻结容器**：`list` → `tuple`，`dict` → `FrozenDict`，`set` → `frozenset`
- **类型安全枚举**：仅允许纯数据 `StrEnum` / `IntEnum`
- **禁止可变默认值**：不允许 `default_factory`

### 证据图（Evidence Graph）

#### 9 种节点类型

| 节点 | 说明 |
|---|---|
| `ResearchQuestion` | 研究问题 |
| `Claim` | 主张（含 claim_type 和 scope） |
| `Source` | 来源（论文、数据集） |
| `StudyFinding` | 研究发现（绑定 Source + 引用锚点） |
| `Construct` | 构念/概念 |
| `Context` | 情境/边界条件 |
| `Blindspot` | 盲点 |
| `DebateCapsule` | 辩论胶囊（辩证折叠） |
| `DiscriminatingStudy` | 判别研究建议 |

#### 12 种边类型

| 边 | 说明 |
|---|---|
| `SUPPORTS` | 支持 |
| `REFUTES` | 反驳 |
| `QUALIFIES` | 限定 |
| `CONTRADICTS` | 矛盾 |
| `CONFOUNDS` | 混杂 |
| `MEDIATES` | 中介 |
| `MODERATES` | 调节 |
| `OPERATIONALIZES` | 操作化 |
| `DERIVED_FROM` | 派生自 |
| `APPLIES_IN` | 适用于 |
| `EXPOSES` | 暴露 |
| `TESTS` | 检验 |

### 证据层级矩阵（A-D）

| 层级 | 含义 | 准入处置 |
|---|---|---|
| **A** | 全文与精确原文可得 | `ADMIT` |
| **B** | 只有摘要和可靠元数据 | `SOURCE_ONLY` |
| **C** | 综述中的二手描述 | `DISCOVERY_ONLY` |
| **D** | 网页或新闻线索 | `TOOL_LEAD_ONLY` |

> **强制约束**：Level B 不得单独支撑高置信因果结论；Level C 和 Level D 不得替代原始研究。

---

## 七名科学家

每个研究任务中，7 名科学家全程参与。EpistemoBrain 是无投票权组织脑，不是第 8 名科学家。

| 席位 | 标识 | 核心问题 | 主要职责 |
|---|---|---|---|
| 理论建构者 | `theory_builder` | 为什么发生？ | 构建理论机制、竞争解释和可证伪预测 |
| 因果推断专家 | `causal_scientist` | 是否因果？ | 审查混杂、反向因果、选择偏差和识别策略 |
| 测量与构念专家 | `measurement_scientist` | 测量了什么？ | 审查操作化、量表效度、自报告偏差和构念漂移 |
| 统计与复现专家 | `replication_scientist` | 结果稳健吗？ | 审查统计功效、多重比较、p-hacking 和复现风险 |
| 边界与情境专家 | `boundary_scientist` | 适用边界在哪？ | 审查外部效度、文化差异、时期效应和人群局限 |
| 对抗性证伪者 | `adversarial_falsifier` | 如何推翻？ | 主动寻找反例、替代解释和失败条件 |
| 证据与溯源审计员 | `evidence_auditor` | 证据可靠吗？ | 审查来源真实性、引用蕴含和方法质量 |

### 科学家权限

每名科学家拥有：

- **独立角色规格**——不同的 Evidence Projection
- **独立私人状态**——独立的 Private MemoBrain
- **不同质询规则和证据排序权重**

科学家只允许提交以下结构化科研动作：

| 动作 | 说明 |
|---|---|
| `PROPOSE` | 提出主张或假设 |
| `SUPPORT` | 支持某主张 |
| `CHALLENGE` | 质询某主张 |
| `QUALIFY` | 限定某主张的适用范围 |
| `FORK` | 创建分支讨论 |
| `REQUEST` | 请求更多证据 |
| `REVISE` | 修订先前主张 |
| `DISSENT` | 正式提出异议 |

收到质询后，只允许以下回应：

| 回应 | 说明 |
|---|---|
| `DEFEND` | 为自己的主张辩护 |
| `REVISE` | 根据质询修订 |
| `NARROW` | 收窄主张范围 |
| `WITHDRAW` | 撤回主张 |
| `DISSENT` | 坚持异议 |

---

## 议会协议

正式研究任务遵循 **7 轮协议**：

```
┌─────────────────────────────────────────────────────────────┐
│  1. Precommitment    │  独立预承诺（减少锚定效应）           │
├─────────────────────────────────────────────────────────────┤
│  2. Acquisition      │  专业取证（各席位独立检索）           │
├─────────────────────────────────────────────────────────────┤
│  3. Exchange         │  证据交换（跨席位共享）               │
├─────────────────────────────────────────────────────────────┤
│  4. CrossExamination │  交叉质询（结构化 CHALLENGE/RESPOND） │
├─────────────────────────────────────────────────────────────┤
│  5. BlindspotBounty  │  盲点悬赏（五维评分 + 排序）          │
├─────────────────────────────────────────────────────────────┤
│  6. JointModeling    │  联合建模（含最强反例 + 证伪条件）    │
├─────────────────────────────────────────────────────────────┤
│  7. FinalRejudgment  │  最终复判（7 份独立判断）            │
└─────────────────────────────────────────────────────────────┘
```

### 盲点五维评分公式

```
score = 0.30 × impact
      + 0.25 × uncertainty
      + 0.20 × investigability
      + 0.15 × novelty
      + 0.10 × (1 − normalized_cost)
```

每个维度取值 0–1，结果量化到 0.0001 精度。

### 条件化共识

共识判定**不使用多数投票**决定科研真理：

- 存在未解决的致命质询 → `BLOCKED`
- 无证据引用 → `BLOCKED`
- 全部条件满足 → `ADMITTED`
- 有支持但存在限定条件 → `CONDITIONAL`

最终结果表达为**条件化共识**，保留少数意见、真正分歧和解决分歧所需证据。

---

## 证据治理

### 三层审计

正式 Finding 必须经过：

1. **来源真实性**——DOI/标题/作者/撤稿状态/PDF 匹配
2. **引用蕴含**——原文是否真正蕴含所声称的结论
3. **方法质量**——直接性、设计、测量、精确性、可复现性、外部效度

### 因果升级防护

系统**禁止**相关性自动升级为因果性。`CausalUpgradePolicy` 强制执行：

- 论文引言中的假设 ≠ 实证结果
- 讨论段中的推测 ≠ 已检验机制
- 不显著结果 ≠ "不存在效应"
- 子群结果 ≠ 总体结论

### 证据独立性聚类

通过并查集（union-find）算法将来源分组为独立证据簇：

- 合并条件：共享同一数据集、重叠样本、预印本与正式版等
- **同一研究团队**（`SAME_RESEARCH_TEAM`）**不单独触发合并**
- 簇 ID 由排序后 UUID 的 SHA-256 生成，保证稳定性

### 辩证折叠（Dialectical Fold）

折叠辩论时保留：

- 原始主张
- 所有反对意见
- 被反驳但仍可追溯的节点
- 异议证书（DissentCertificate）

**被反驳、隔离或折叠的节点不得物理删除。**

---

## 评测体系

### ForesightBlindspot 时间切片基准

核心评测方法：使用截止日期前的封闭语料，测试系统发现高价值科研盲点的能力。

### 消融对比

| 变体 | 说明 |
|---|---|
| Single-Agent Deep Research | 单智能体基线 |
| Fixed Multi-Agent Debate | 固定角色多智能体 |
| Council + Linear Context | 议会 + 线性上下文 |
| Council + MemoBrain, no Evidence Engine | 议会 + 记忆，无证据引擎 |
| **完整 Poliscope** | 全功能 |

### 核心指标

- **Blindspot Recall**——盲点召回率
- **Precision**——精确率
- **引用蕴含**——Citation Entailment
- **因果过度推断**——Causal Overclaim
- **异议保留**——Dissent Preservation
- **虚假共识**——False Consensus
- **长程漂移**——Long-Range Drift
- **每个有效盲点成本**——Cost per Valid Blindspot

### MVP 验收矩阵

| # | 规格项 | 测试路径 |
|---|---|---|
| 1 | 7 席参与 | `tests/unit/test_council_contracts.py` |
| 2 | 原子主张必需 | `tests/integration/test_research_service.py` |
| 3 | 证据门阻止相关→因果 | `tests/unit/test_minimal_evidence_gate.py` |
| 4 | 完整 6 阶段审核 | `tests/integration/test_full_evidence_gate.py` |
| 5 | 辩证折叠保留异议 | `tests/unit/test_dialectical_fold.py` |
| 6 | 独立证据簇 | `tests/integration/test_independent_clusters.py` |
| 7 | 盲点五维评分 | `tests/unit/test_blindspot_score.py` |
| 8 | DiscriminatingStudy | `tests/unit/test_discriminating_study.py` |
| 9 | 预算耗尽 ≠ 饱和 | `tests/unit/test_evidence_saturation.py` |
| 10 | 单席降级 | `tests/integration/test_single_seat_degradation.py` |
| 11 | 因果 Golden 案例 | `tests/golden/test_causal_entailment.py` |
| 12 | 报告安全 | `tests/unit/test_report_safety.py` |
| 13 | CLI 契约 | `tests/unit/test_cli_contract.py` |
| 14 | Workspace DTO 白名单 | `tests/integration/test_workspace_api.py` |
| 15 | SSE 恢复 | `tests/integration/test_sse_contract.py` |
| 16 | 发布门 | `tests/e2e/test_release_gate.py` |

---

## 开发指南

### 开发工作流

本项目遵循 **TDD（测试驱动开发）** 纪律：

```
1. 先写失败的测试（Red）
2. 确认测试失败符合预期
3. 实现最小代码使测试通过（Green）
4. 重构并确认测试仍通过
5. 提交
```

### Lint 与格式化

```bash
# 检查
ruff check .

# 自动修复
ruff check --fix .

# 类型检查
mypy packages/
```

Ruff 配置：行宽 88，启用规则 `E, F, I, UP, B, SIM`。

### 数据库迁移

```bash
# 创建新迁移
alembic revision --autogenerate -m "描述"

# 升级到最新
alembic upgrade head

# 回滚一步
alembic downgrade -1
```

### 数据库角色

系统使用职责分离的数据库角色：

| 角色 | 权限 |
|---|---|
| `poliscope_app` | 运行时读写（SELECT/INSERT/UPDATE） |
| `poliscope_projector` | Graph Projector 专用，受限写入 |
| `poliscope_migrator` | 迁移专用（DDL 权限） |

---

## 测试

### 运行测试

```bash
# 全部测试（含数据库集成测试，需要 Docker）
pytest

# 仅单元测试（无需外部服务）
pytest tests/unit/ -v

# 仅集成测试（需要 PostgreSQL）
pytest tests/integration/ -v

# 仅 E2E 测试
pytest tests/e2e/ -v

# 评测测试
pytest tests/evals/ -v

# Golden 测试
pytest tests/golden/ -v
```

### 测试层级

```
tests/
├── unit/          # 单元测试（纯内存，无外部依赖）
├── integration/   # 集成测试（需要 PostgreSQL）
├── e2e/           # 端到端测试（发布门）
├── evals/         # 评测测试（消融对比）
├── golden/        # Golden 测试（因果蕴含、过程折叠）
├── fixtures/      # 共享测试数据
├── factories.py   # 工厂函数
├── helpers.py     # 测试辅助函数
└── conftest.py    # pytest fixtures 和配置
```

### 数据库集成测试

集成测试使用 [testcontainers-python](https://github.com/testcontainers/testcontainers-python) 启动隔离的 PostgreSQL 容器。需要 Docker 运行环境。

---

## 目录结构

```
poliscope-mvp/
├── CLAUDE.md                  # AI 编码代理约束文件
├── README.md                  # 本文件
├── pyproject.toml             # 项目配置与依赖
├── alembic.ini                # Alembic 配置
├── uv.lock                    # 依赖锁定文件
│
├── packages/                  # 业务模块
│   ├── kernel/                # 共享基础设施
│   ├── council/               # 议会：角色、协议、轮次
│   ├── epistemo/              # EpistemoBrain：编排、状态机
│   ├── evidence/              # 双图治理
│   ├── memory/                # MemoBrain 适配
│   ├── models/                # 模型网关
│   ├── tools/                 # 工具网关
│   ├── papers/                # 论文处理
│   ├── research/              # 研究服务
│   ├── reports/               # 报告生成
│   └── evaluation/            # 评测
│
├── apps/                      # 应用入口
│   ├── api/                   # FastAPI REST + SSE
│   ├── worker/                # 异步 Worker
│   ├── cli/                   # 命令行工具
│   └── bootstrap.py           # 启动引导
│
├── migrations/                # Alembic 数据库迁移
│   └── versions/              # 迁移脚本
│
├── docs/                      # 文档
│   ├── schema.md              # Schema 参考
│   ├── testing.md             # 测试矩阵
│   ├── demo.md                # 演示脚本
│   └── superpowers/specs/     # 正式设计规格
│
├── tests/                     # 测试套件
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── evals/
│   └── golden/
│
├── demo/                      # 演示用例
│   └── core_case/
│       ├── expected_graph.json
│       └── expected_report.json
│
└── skills/                    # Agent Skill 定义
    └── poliscope/
        ├── SKILL.md
        ├── claude-code/
        └── codex/
```

---

## 局限与伦理

### 科研辅助定位

⚠️ **Poliscope 是科研辅助工具，不是临床诊断或医疗建议。**

### 已知局限

- **全文不可得**：部分论文仅摘要可获取，系统会明确标注
- **解析不确定**：PDF 表格和复杂排版无法保证 100% 可靠解析
- **引用冲突**：不同研究结论冲突时系统会呈现而非隐藏
- **预算不足**：预算耗尽时报告未完成证据槽位，**绝不伪造完整结果**

### 安全门（Safety Gate）

- 涉及心理健康的报告**自动附加安全声明**
- 导出时**清除签名 URL 和本地路径**，防止材料泄露
- 工作空间 DTO 使用**白名单机制**，不暴露私有推理链
- 前端只展示结构化动作、证据引用、质询回应和置信度变化

### 研究者控制

- 研究者**控制研究方向**，但**不能绕过证据审核**
- 未审计内容**不能强制升级为正式证据**
- 原子主张必须**显式确认**后任务才能执行

---

## 许可证与归属

### 方法基座

Poliscope 以 [MemoBrain](https://github.com/qhjqhj00/MemoBrain) 作为执行记忆方法基座：

- 论文：*MemoBrain: Executive Memory as an Agentic Brain for Reasoning*
- ACL Anthology：https://aclanthology.org/2026.findings-acl.127/

集成前已核验上游许可证，并通过 `MemoBrainAdapter` 适配，避免修改上游源代码。

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

---

## 路线图

| 阶段 | 状态 | 说明 |
|---|---|---|
| MVP 后端 | ✅ 完成 | 双图治理、7 轮协议、证据门、CLI、API |
| 数据库迁移 | ✅ 完成 | 角色分离、表结构、索引 |
| Golden 测试 | ✅ 完成 | 因果蕴含、过程折叠 |
| E2E 发布门 | ✅ 完成 | 7 席 / 9 节点 / 12 边验证 |
| 前端（Vite/React） | 🔧 契约就绪 | Workspace/SSE 契约已实现 |
| 完整评测案例 | 🔧 框架就绪 | 语料与消融框架 |
| 生产部署 | 📋 待办 | Docker Compose、监控、告警 |

---

> **一句话总结**：Poliscope 不是更快的文献综述机器，而是一面让你看见证据裂缝的科研透镜。
