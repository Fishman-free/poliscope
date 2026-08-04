# AGENTS.md

本文件约束所有在 Poliscope 仓库中工作的 AI 编码代理。开始任何任务前必须先阅读本文件和相关设计规格。

## 1. 项目身份

- **项目名称：** Poliscope
- **核心方法：** EpistemoBrain
- **项目类型：** 面向计算社会科学争议问题的深度研究智能体
- **首版领域：** 数字行为、社交媒体与心理健康
- **正式设计规格：** `docs/superpowers/specs/2026-07-31-poliscope-design.md`

Poliscope 组织 7 名 AI 科学家全程参与研究，并以 MemoBrain 管理长程过程记忆。系统输出可审计的争议证据地图，重点发现混杂、反向因果、测量偏差、复现风险、证据依赖和适用边界。

不要把本项目实现成「7 个聊天机器人 + 1 个总结机器人」。

## 2. 强制产品原则

1. **以盲点发现为核心价值。** 不以报告长度、引用数量或 Agent 数量作为成功标准。
2. **证据优先于流畅文本。** 关键判断必须绑定可核验来源和原文位置。
3. **相关不等于因果。** 任何因果表述必须经过因果审查并明确识别局限。
4. **论文数量不等于独立证据数量。** 必须追踪数据集、样本、版本和研究团队依赖。
5. **异议不得被静默删除。** 被反驳、隔离或压缩的观点仍须可追溯。
6. **明确区分原文、作者解释和 AI 推导。** 数据模型、API 和界面都不得混用。
7. **系统必须承认未知。** 全文不可得、解析不确定、引用冲突和预算不足必须显式呈现。
8. **研究者控制方向，但不能绕过证据审核。** 未审计内容不能强制升级为正式证据。

## 3. 七名科学家

以下 7 个席位在每个正式研究任务中全程参与：

1. `theory_builder`：理论建构者；
2. `causal_scientist`：因果推断专家；
3. `measurement_scientist`：测量与构念专家；
4. `replication_scientist`：统计与复现专家；
5. `boundary_scientist`：边界与情境专家；
6. `adversarial_falsifier`：对抗性证伪者；
7. `evidence_auditor`：证据与溯源审计员。

EpistemoBrain 是无投票权的组织脑，不是第 8 名科学家，也不能代表某个学术立场。

7 名科学家共享运行框架和工具缓存，但必须拥有：

- 独立角色规格；
- 独立私人状态；
- 独立 Private MemoBrain；
- 不同 Evidence Projection；
- 不同质询规则和证据排序权重。

不得复制 7 套业务代码或部署 7 个服务。

## 4. 议会协议

初始化阶段完成研究范围和原子主张确认。正式协议包含：

1. 独立预承诺；
2. 专业取证；
3. 证据交换；
4. 交叉质询；
5. 盲点悬赏；
6. 联合建模；
7. 最终独立复判。

科学家只允许提交结构化科研动作：

- `PROPOSE`；
- `SUPPORT`；
- `CHALLENGE`；
- `QUALIFY`；
- `FORK`；
- `REQUEST`；
- `REVISE`；
- `DISSENT`。

收到质询后，只允许：`DEFEND`、`REVISE`、`NARROW`、`WITHDRAW` 或 `DISSENT`。

禁止使用多数投票直接决定科研真理。最终结果必须表达为条件化共识，并保留少数意见、真正分歧和解决分歧所需证据。

### 4.1 JOINT_MODELING 前的人类方向性引导检查点

盲点悬赏（第 5 轮）结束、联合建模（第 6 轮）开始之前，存在一个**可选的**人类方向性引导检查点：任务状态进入 `AWAITING_COUNCIL_INPUT`，研究者可以查看 7 名科学家在盲点悬赏结束时的立场（预承诺、置信度、已提出质询），并提交一段方向性备注，或选择留空、直接继续。

这个检查点**不构成对本条「禁止多数投票裁决科研真理」的违反**：

- 它是单一研究者的方向性输入，不是投票计数，也不产生任何裁决科研真理的聚合分数；
- 它只允许影响联合建模阶段"接下来重点讨论哪些悬而未决的冲突或边界条件"这类调度性内容；
- 它不改变任何 Evidence Gate 判定逻辑，不作为任何 `Claim` 的 `SUPPORTS`/`REFUTES` 证据来源，不进入 `DebateCapsule` 或 `DissentCertificate` 的构造字段；
- 联合建模的 prompt 会把它作为一段独立、明确标注来源的文本注入（例如"[研究者方向性备注，非科学判断]: ..."），供模型参考，不得被模型当成第 8 名科学家的科研判断。

提交（或明确留空）后，任务状态回到 `QUEUED`，由 Worker 重新认领并从联合建模阶段续跑。这是一个**唯一、固定**的检查点，不是通用的"议会任意时点快照/暂停/恢复"——后者仍然超出本版范围（见 README「已知缺口」）。

## 5. 双图与事件账本

### 5.1 Process Graph

- 记录科学家的任务、工具调用、失败路线、质询和决策；
- 由 MemoBrain 管理；
- 允许普通 Flush、Fold 和 Recall；
- 过程节点不得自动成为正式科研证据。

### 5.2 Evidence Graph

首版正式节点：

- `ResearchQuestion`；
- `Claim`；
- `Source`；
- `StudyFinding`；
- `Construct`；
- `Context`；
- `Blindspot`；
- `DebateCapsule`；
- `DiscriminatingStudy`；
- `DissentCertificate`。

首版正式边：

- `SUPPORTS`；
- `REFUTES`；
- `QUALIFIES`；
- `CONTRADICTS`；
- `CONFOUNDS`；
- `MEDIATES`；
- `MODERATES`；
- `OPERATIONALIZES`；
- `DERIVED_FROM`；
- `APPLIES_IN`；
- `EXPOSES`；
- `TESTS`。

### 5.3 强制约束

- 7 名科学家不得直接写 Evidence Graph。
- 所有正式修改必须先写入 Scientific Event Ledger。
- Graph Projector 是正式证据图的唯一写入者。
- Event 必须幂等、可重放、可审计。
- `StudyFinding` 必须来源于 `Source`，并绑定原文、页码或等价位置。
- `Claim` 必须包含 `claim_type` 和 `scope`。
- 被反驳、隔离或折叠节点不得物理删除。
- 普通 Fold 只能压缩 Process Graph。
- Evidence Graph 只允许可回溯的 Dialectical Fold。

## 6. MemoBrain 集成规则

MemoBrain 是外部方法基座，代码仓库为：

https://github.com/qhjqhj00/MemoBrain

集成前必须核验其许可证。通过 `MemoBrainAdapter` 集成，避免无必要地修改上游源代码。

首版适配接口应保持小而清楚：

```python
init_private_memory(agent_id, task)
memorize_episode(agent_id, episode)
recall_private(agent_id, token_budget)
save_snapshot(agent_id)
load_snapshot(agent_id)
```

MemoBrain 管理过程记忆，不得成为正式 Evidence Graph 的事实来源。Fold 后必须验证以下内容仍被保留：

- 当前任务；
- 已确认 Finding；
- 活跃 Blindspot；
- 未解决质询；
- 少数异议；
- 下一步证据需求。

## 7. 证据与引用规则

### 7.1 证据分层

- **Level A：** 全文与精确原文可得；
- **Level B：** 只有摘要和可靠元数据；
- **Level C：** 综述中的二手描述；
- **Level D：** 网页或新闻线索。

Level B 不得单独支撑高置信因果结论；Level C 和 Level D 不得替代原始研究。

### 7.2 精确溯源

关键判断至少保存：

- Source；
- Study；
- Section；
- Page 或等价定位；
- Exact Quote；
- Extraction Agent；
- Verification Status。

### 7.3 三层审计

正式 Finding 必须经过：

1. 来源真实性；
2. 引用蕴含；
3. 方法质量。

不得将以下内容误写为实证结果：

- 论文引言中的假设；
- 讨论段中的推测；
- 作者未检验的机制；
- 不显著结果对应的「不存在效应」；
- 相关关系对应的因果关系；
- 子群结果对应的总体结论。

### 7.4 证据独立性

必须尽量识别：

- 同一数据集；
- 重叠样本；
- 预印本与正式版；
- 扩展研究与再分析；
- 综述包含的原始研究。

界面与报告应同时显示论文数量和独立证据簇数量。

## 8. 工程架构约束

首版采用模块化单体与异步 Worker。

推荐：

- Python 3.12；
- FastAPI；
- Pydantic；
- SQLAlchemy；
- Alembic；
- PostgreSQL；
- pgvector；
- Redis；
- React；
- TypeScript；
- React Flow；
- Server-Sent Events；
- pytest。

数据库是事实来源。即使使用 LangGraph，也不得让任务状态只存在于内存或工作流框架内部。

所有模型调用必须通过 Model Gateway；所有外部工具必须通过 Tool Gateway。不得在角色实现中散落厂商 SDK 调用、HTTP 请求、重试和费用统计。

## 9. 模块边界

建议模块：

- `packages/kernel`：冻结契约、数据库会话、配置与权限常量；被所有模块依赖，不依赖任何业务模块；
- `packages/council`：角色规格、协议、动作 Schema、轮次注册表与席位—网关桥；
- `packages/epistemo`：状态机、预算、调度和停止条件；
- `packages/memory`：MemoBrain Adapter、席位私有记忆与 Process Graph；
- `packages/evidence`：Event Ledger、Evidence Gate、Graph Projector 和 Lineage；
- `packages/papers`：发现、规范化、全文解析和 Finding 抽取；
- `packages/research`：研究契约、主张原子化、任务仓储与应用服务；
- `packages/models`：统一模型网关；
- `packages/tools`：统一工具网关；
- `packages/reports`：Research Brief 和审计报告；
- `packages/evaluation`：基线、消融和 ForesightBlindspot。

应用层：

- `apps/api`：HTTP 接口，只持有应用身份；
- `apps/worker`：任务认领与议会执行，分两个事务分别以应用身份和投影器身份运行；
- `apps/cli`：纯 HTTP 客户端，不得 import `packages`——第二条进入研究服务的路径会绕过 Evidence Gate；
- `apps/web`：证据工作台前端。

每个模块必须能回答：

- 它负责什么；
- 它不负责什么；
- 输入和输出 Schema 是什么；
- 依赖什么；
- 如何独立测试。

避免跨模块直接操作内部表或共享可变对象。跨模块通信优先使用明确的 Pydantic Schema 和应用服务接口。

## 10. 状态与可靠性

- 任务阶段必须使用显式状态机；
- 每个状态定义进入条件、完成条件、超时策略和下一状态；
- 单个科学家失败不得中止整个任务；
- 结构化输出修复失败后必须隔离，不能写正式图；
- Graph Projector 顺序处理事件并使用版本或乐观锁；
- 模型与工具调用必须记录延迟、费用、重试和错误；
- 预算耗尽时报告未完成证据槽位，不得伪造完整结果；
- 关键长任务必须支持快照、暂停、恢复和 Replay。

## 11. 前端与用户体验约束

产品中心是证据地图，不是聊天框。

必须提供：

- Research Brief；
- Controversy Map；
- Audit Trail；
- 7 人议会状态；
- Blindspot Radar；
- DiscriminatingStudy；
- Evidence Lineage；
- Evolution View。

不要展示模型私有思维链。只展示：

- 结构化动作；
- 使用的证据；
- 质询与回应；
- 结论和置信度变化；
- 可审计决策摘要。

视觉风格应像科研仪器和证据审查工具，避免卡通 Agent、霓虹驾驶舱和无意义动画。结论与局限必须并排显示。

## 12. 测试要求

### 12.1 开发纪律

- 业务规则、状态机和图约束先写测试，再实现；
- 修复 Bug 时必须添加回归测试；
- Prompt、模型或解析器变化必须运行 Golden Tests；
- 不得只测试「请求成功」，还要断言证据状态和溯源字段正确。

### 12.2 必测行为

- Event Ledger 幂等；
- Graph Projector 单一写入；
- Finding 必须绑定 Source 和引用锚点；
- 相关性不得自动升级为因果性；
- Quarantine 不物理删除；
- Dialectical Fold 保留异议和反例；
- Recall 不丢失科研骨架；
- 证据谱系不重复计票；
- 单席失败可降级继续；
- 快照恢复后任务状态一致。

### 12.3 完成标准

任何功能在宣称完成前，至少通过：

- 单元测试；
- 相关集成测试；
- 类型检查和静态检查；
- 对受影响真实流程的端到端验证。

不得在测试失败、验证未运行或只完成部分行为时声称完成。

## 13. 评测要求

核心评测为 ForesightBlindspot 时间切片基准。必须保留以下对比：

1. Single-Agent Deep Research；
2. Fixed Multi-Agent Debate；
3. Council + Linear Context；
4. Council + MemoBrain，但无 Evidence Engine；
5. 完整 Poliscope。

优先消融：

- 无独立预承诺；
- 无对抗性证伪者；
- 无证据审计员；
- 普通 Fold；
- 无证据谱系；
- 无 MemoBrain。

核心指标包括 Blindspot Recall、Precision、引用蕴含、因果过度推断、异议保留、虚假共识、长程漂移和每个有效盲点成本。

时间切片评测必须使用截止日前封闭语料，并明确声明基础模型参数可能包含未来知识的风险。

## 14. 范围与 YAGNI

首版只实现能证明以下主张的功能：

> EpistemoBrain 通过 7 人议会、执行记忆、双图证据治理和异议保真机制，能够比普通研究智能体更可靠地发现高价值科研盲点。

以下需求默认拒绝或后置，除非设计规格明确升级：

- 通用全学科；
- 自动实验设备；
- 任意代码复现；
- 自动元分析；
- 模型训练平台；
- 微服务拆分；
- 多租户与复杂权限；
- 插件市场；
- 移动端；
- 与核心证据闭环无关的装饰功能。

## 15. 文档与命名

- 面向团队的文档默认使用简体中文；
- 中英文、中文与数字之间留空格；
- 代码标识符使用英文；
- 核心术语必须统一，不得随意创造同义名；
- `Claim`、`StudyFinding`、`Blindspot`、`DebateCapsule`、`DissentCertificate` 和 `DiscriminatingStudy` 的含义以设计规格为准；
- 引用 MemoBrain 时明确区分上游机制与 Poliscope 扩展；
- 新增模块或协议时同步更新设计文档、Schema 文档和测试说明。

## 16. 安全与伦理

- 不把系统输出表达为临床诊断或医疗建议；
- 涉及心理健康时明确科研辅助定位；
- 不抓取或存储未授权的个人数据；
- 不绕过付费墙或访问控制；
- 上传 PDF 的存储、日志和导出必须避免泄露用户材料；
- 报告必须说明 AI 辅助、证据覆盖和系统局限；
- 不以模型置信度替代统计不确定性或专家判断。

## 17. 变更决策

若实现过程中需要偏离本文件或正式设计规格：

1. 先说明偏离原因；
2. 分析对证据可靠性、评测和 MVP 的影响；
3. 更新设计规格或记录架构决策；
4. 获得用户确认后再实施重大变更。

不得以「更容易实现」为理由，静默删除证据审计、异议保留、双图隔离或精确溯源等核心机制。
