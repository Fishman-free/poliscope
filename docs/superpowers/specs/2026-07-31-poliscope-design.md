# Poliscope 系统设计规格

- **文档状态：** 已批准，待实现
- **版本：** v1.0
- **日期：** 2026-07-31
- **目标赛道：** 面向真实学术场景的深度研究智能体设计大赛
- **首版领域：** 数字行为、社交媒体与心理健康等计算社会科学争议问题

## 1. 项目概述

### 1.1 产品定义

Poliscope 是一个面向计算社会科学争议问题的深度研究智能体。系统组织 7 名具有互补认识能力的 AI 科学家，全程参与问题拆解、独立判断、论文取证、交叉质询、盲点调查和最终复判，生成可审计的争议证据地图。

Poliscope 不以生成更长、更流畅的文献综述为目标。它重点回答：

> 当前结论最可能错在哪里？哪些盲点、反例、边界条件或证据依赖会改变科研判断？

### 1.2 核心方法

系统核心方法命名为 **EpistemoBrain**。它是科学议会的集体执行记忆与组织控制层，负责：

- 管理 7 名科学家的长期研究过程；
- 保存重要反例、少数意见和未解决冲突；
- 根据证据状态生成调查任务；
- 在固定预算下控制议会轮次；
- 维护过程记忆与正式科研证据之间的边界；
- 形成条件化共识，而不是强制投票共识。

### 1.3 核心价值主张

Poliscope 与普通 Deep Research 和固定角色多智能体系统的差异包括：

1. 7 名科学家先独立预承诺，再交换证据，减少锚定和角色同质化。
2. 科学家只能提交结构化科研动作，不能用自由聊天直接修改正式结论。
3. MemoBrain 管理推理过程；正式证据由独立证据图管理。
4. 每项关键判断可回溯到具体论文、研究、页码和原文。
5. 证据谱系会识别同一数据集、重叠样本和论文版本，避免重复计票。
6. 系统将 Blindspot 作为一等科研对象，并将盲点转化为可执行判别研究。
7. 异议不会因上下文压缩或多数意见而消失。

## 2. 目标用户与使用场景

### 2.1 首版目标用户

- 正在确定研究问题的硕士生、博士生和青年研究者；
- 正在撰写文献综述或研究计划的计算社会科学研究者；
- 需要审查争议证据、因果解释和适用边界的跨学科团队。

### 2.2 核心任务

首版支持以下任务：

- 将模糊研究问题拆成可证伪的原子主张；
- 检索、解析和审查公开论文及用户上传 PDF；
- 区分相关性、因果性、机制性、测量性和外推性结论；
- 识别反向因果、混杂、测量偏差、复现风险和外部效度边界；
- 构建支持、反驳、限定与冲突关系；
- 输出关键盲点和最小判别研究；
- 导出带精确证据锚点的科研审计报告。

### 2.3 首版非目标

首版不实现：

- 全学科通用科研平台；
- 自动控制实验室设备；
- 自动发表论文；
- 任意论文代码的一键复现；
- 全自动元分析；
- 自训练或大规模微调模型；
- 通用知识图谱本体编辑器；
- 多人实时协作、订阅付费或插件市场；
- 对任意 PDF 表格实现 100% 可靠解析。

## 3. 方法来源与创新边界

### 3.1 MemoBrain 原始机制

Poliscope 以 MemoBrain 的公开论文与代码为执行记忆方法基座：

- 论文：*MemoBrain: Executive Memory as an Agentic Brain for Reasoning*；
- ACL Anthology：https://aclanthology.org/2026.findings-acl.127/；
- 代码：https://github.com/qhjqhj00/MemoBrain。

MemoBrain 原始能力包括：

- 依赖感知的 ReasoningGraph；
- `task`、`subtask`、`evidence`、`summary` 等过程节点；
- `memorize()` 记录推理 episode；
- `Flush` 隔离无效或失败路径；
- `Fold` 压缩已完成子轨迹；
- `Recall` 在固定 token 预算内重建高显著性上下文。

实现前必须核验上游仓库许可证，并在代码与文档中保留所需归属说明。

### 3.2 Poliscope 扩展

以下机制属于本项目设计，不得误写为 MemoBrain 原论文贡献：

- 7 人完整科学议会；
- 私人、议会和正式证据的分层记忆；
- Process Graph 与 Evidence Graph 双图架构；
- Scientific Event Ledger；
- Quarantine、Dialectical Fold、Perspective Recall；
- Fork、Merge、Resurrect；
- 盲点悬赏与认识论路由；
- 条件化共识与 Dissent Certificate；
- Evidence Lineage Graph；
- ForesightBlindspot 时间切片评测。

### 3.3 为什么不能直接复用 MemoBrain

MemoBrain 的 `ReasoningGraph`、`Flush`、`Fold`、`Recall` 是为**一个** Agent 设计的：这个 Agent 自己检索、自己推理、自己在 token 预算见底前压缩掉已经走过的路。把某个科学家的 ReasoningGraph 直接当作 Poliscope 最终的证据地图，会依次产生以下问题：

1. 工具调用步骤和学术证据混在同一张图里，无法区分「过程」与「证据」；
2. 一名科学家的中间猜测可能被用户误读为已核实的事实；
3. 普通 `Fold` 会把尚未解决的争议一并压缩，等同于静默删除异议（违反第 2 章「异议不得被静默删除」）；
4. 一篇论文内的多个研究发现无法在图上分别归属到不同结论；
5. 前端无法清楚呈现「这个结论现在有多大争议」。

这五个问题的共同根源是：ReasoningGraph 回答的是「这名科学家是怎么完成这次研究的」，Poliscope 需要回答的是「我们现在对这个研究问题知道什么」——这是两个不同的问题，不能共用一张图来回答。因此正式决策是双图分离（见第 6 章），MemoBrain 仅作为 Process Graph 的执行记忆基座，不作为 Evidence Graph 的事实来源。

七人议会解决的是「单一视角」的问题，但不能假设它能自动解决「证据可靠性」的问题：如果七名科学家读到的是同一份错误摘要、同一篇已撤稿论文或同一条张冠李戴的引用，系统可能把**共享的证据错误**误判为群体共识——七人同时判断错误，看起来比一人判断错误更像是被反复验证过的结论，实际上可靠性并未提高。因此「视角多样性」（七个角色的不同关注维度）和「证据独立性」（证据本身是否来自相互独立的数据源）必须作为两个独立维度分别设计机制：前者由第 4 章的角色设计和第 5.2 节的 Perspective Recall 保证；后者由第 7.6 节的 Evidence Lineage Graph 和第 7.9 节列出的四种机制共同保证。

## 4. 七人完整科学议会

每个研究任务中，7 名科学家全程参与。EpistemoBrain 是无投票权组织脑，不是第 8 名科学家。

**设计决策记录：为什么是「七人全程」而不是「按任务动态选组」。** 早期方案曾设计过一个 `CoalitionScore(T,B) = αCoverage(T,B) + βDiversity(T) + γConflictPotential(T) + δHistorySuccess(T,B) − λRedundancy(T) − μCost(T)`，用于给每个研究任务动态挑选一个「最适合」的科学家子集，以降低成本并体现调度智能。该方案已被否决：任何一次省略某个席位的调度决策，本质上都是在赌「这个维度这次用不上」，而争议问题的盲点恰恰经常出现在被判断为「不相关」的维度上——测量专家看似与因果争议无关，但自报告偏差往往正是该因果争议的真正根源。最终决策是**每个任务七人全程参与，成本通过其他手段控制**：每轮发言设预算（一个主动作 + 一个质询，无新增信息则 `PASS`）、语义去重后才触发重推理、复杂判断路由至强模型/格式化与抽取路由至轻量模型（见 9.4）、七人共享同一份检索与解析缓存（见 7.3）而非各自独立检索。

| 席位 | 核心问题 | 主要职责 |
|---|---|---|
| 理论建构者 | 为什么发生？ | 构建理论机制、竞争解释和可证伪预测 |
| 因果推断专家 | 是否因果？ | 审查混杂、反向因果、选择偏差和识别策略 |
| 测量与构念专家 | 测量了什么？ | 审查操作化、量表效度、自报告偏差和构念漂移 |
| 统计与复现专家 | 结果稳健吗？ | 审查效应量、统计功效、模型敏感性、数据与代码 |
| 边界与情境专家 | 在哪里成立？ | 识别人群、文化、时间、平台和非线性边界 |
| 对抗性证伪者 | 最可能错在哪里？ | 寻找反例、替代理论、隐藏假设和证伪条件 |
| 证据与溯源审计员 | 来源真的支持主张吗？ | 核验来源、原文、蕴含、撤稿和证据独立性 |

### 4.1 允许的科研动作

科学家只能提交以下结构化动作：

- `PROPOSE`：提出主张或理论机制；
- `SUPPORT`：提交支持性证据；
- `CHALLENGE`：攻击主张、方法或证据；
- `QUALIFY`：添加适用边界；
- `FORK`：提出竞争解释；
- `REQUEST`：提出证据需求；
- `REVISE`：根据证据修正判断；
- `DISSENT`：签署无法消解的异议。

每项动作必须包含 actor、target、statement、evidence_refs、confidence、falsification_condition 和 novelty。缺少证据的事实性断言、无目标评论、重复观点和没有新增信息的同意不得写入正式事件账本。

### 4.2 议会运行协议

问题原子化属于初始化阶段。正式议会采用 7 轮协议：

1. **独立预承诺：** 7 人在不可见他人答案的情况下提交初始判断、置信度、盲点和更新条件。
2. **专业取证：** 7 人根据角色关注点并行阅读共享候选池。
3. **证据交换：** 只交换结构化证据，不交换完整私有推理历史。
4. **交叉质询：** 针对因果、测量、统计、边界、来源和隐藏假设进行定向质询。
5. **盲点悬赏：** 根据影响、不确定性、可调查性、新颖度和成本排序盲点。
6. **联合建模：** 生成共同认识、正反立场、铰链变量、未解决冲突和判别条件。
7. **最终复判：** 7 人再次独立判断，记录证据驱动的信念更新和少数异议。

### 4.3 回应动作

收到质询后，科学家必须选择：

- `DEFEND`：补充证据；
- `REVISE`：修正主张；
- `NARROW`：缩小适用范围；
- `WITHDRAW`：撤回主张；
- `DISSENT`：保留有证伪条件的异议。

### 4.4 条件化共识

系统不以简单多数投票决定真理。主张必须先通过 Evidence Gate：

- 存在可追溯证据；
- 引用通过核验；
- 声明适用边界；
- 存在证伪条件；
- 没有尚未回应的致命攻击。

最终输出同时包含多数判断、少数异议、真正分歧和解决分歧所需证据。

### 4.5 JOINT_MODELING 前的人类方向性引导检查点

盲点悬赏（第 5 轮）结束、联合建模（第 6 轮）开始之前，存在一个**唯一、固定**的可选检查点，供研究者提供方向性引导。这不是通用的"议会任意时点快照/暂停/恢复"（那需要重构 `CouncilOrchestrator` 的阶段循环与事务边界，超出本版范围，见第 15 章与 README「已知缺口」），而是复用 Research Contract 确认流程已验证过的"状态置为等待态 → 人类调用 HTTP 接口 → 置回 `QUEUED` → Worker 重新认领"模式。

**状态机：**

```text
... → BLINDSPOT_BOUNTY 完成
    → AWAITING_COUNCIL_INPUT（新增任务状态；进入有界的人类响应宽限期）
    → 宽限期内由人类提交（或明确留空）引导意见
      / 宽限期届满后由服务端自动选择“无方向性干预”
    → QUEUED（Worker 重新认领）
    → JOINT_MODELING → FINAL_REJUDGMENT → REPORTING
```

`AWAITING_COUNCIL_INPUT` 期间，编排器已跑过的阶段产物（各阶段的 `carry` 累积状态）会被序列化保存，供续跑时重建联合建模所需的上下文，不需要从头重跑前 5 轮。默认宽限期为 120 秒，可通过 `POLISCOPE_COUNCIL_INPUT_GRACE_SECONDS` 配置；配置必须是有限数字且不得短于 30 秒。Worker 在服务端轮询中过期等待任务，因此浏览器进入后台或关闭不会阻止研究继续。

自动续跑只把仍处于 `AWAITING_COUNCIL_INPUT` 且具有合法 checkpoint 的任务原子地置回 `QUEUED`；checkpoint 内容保持不变，`guidance` 保持 `None`，不得伪装成研究者明确提交的空意见。研究者在宽限期内提交的意见优先：人工路径先把状态置为 `QUEUED` 后，自动路径的条件更新不再匹配。所有状态变更必须刷新 `updated_at`，使宽限从真正进入等待态的时刻开始计算。该服务端调度行为不写 Evidence Graph、不改变 Evidence Gate，也不把待调查盲点冒充已证实结论。

**API 契约：**

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/tasks/{id}/council-preview` | 只读，复用工作台的按席位聚合逻辑，展示 7 名科学家在盲点悬赏结束时的立场（预承诺、置信度、已提出质询），不新建节点类型 |
| `POST` | `/api/tasks/{id}/council-guidance` | 接受人类的方向性文字意见（可为空字符串，代表"不干预，直接继续"），记一条 Process Graph 事件（不是正式 Evidence Graph 节点），随后把任务状态从 `AWAITING_COUNCIL_INPUT` 置回 `QUEUED` |

**不裁决真理的具体落实：** 联合建模构造 prompt 时，若存在人类引导意见，将其作为一段独立、明确标注来源的文本注入（例如"[研究者方向性备注，非科学判断]: {guidance_text}"）。它只允许改变"接下来重点讨论哪些悬而未决的冲突/边界条件"这类调度性内容，不允许影响 Evidence Gate 六阶段审核的任何判定逻辑，不作为任何 `Claim` 的 `source_refs`，不进入 `DebateCapsule`/`DissentCertificate` 的构造字段。同一份证据输入，带引导与不带引导两次运行必须产出完全一致的 `Claim` 采纳状态、`DissentCertificate` 与 Evidence Gate 判定，仅讨论顺序/prompt 上下文可以不同——这一性质由测试直接断言。

CLI 与 Web 均提供该检查点的对应入口（`poliscope council-preview`/`council-guidance` 子命令；Web 端一个展示席位立场卡片 + 引导意见输入框 + 继续按钮的检查点视图），两者共享同一 API 契约。

## 5. EpistemoBrain 与执行记忆

### 5.1 三层记忆

1. **Private MemoBrain：** 每名科学家的工具调用、失败路线、私人假设和当前计划。
2. **Collective Process Memory：** 议会任务、质询、决策、盲点悬赏和阶段性胶囊。
3. **Evidence Graph：** 面向用户的正式、稳定、可审计科研知识。

### 5.2 记忆操作

#### Quarantine

科研中的少数假设和当前不支持观点不得静默删除。节点可经历：

```text
PROPOSED → SUPPORTED → CONTESTED → QUARANTINED → RESURRECTED / REJECTED
```

隔离记录必须包含原因、攻击者、缺失证据和复活条件。

#### Dialectical Fold

争议只能在保留以下字段后压缩：

- 共同认识；
- 最强支持证据；
- 最强反对证据；
- 铰链变量；
- 适用边界；
- 未解决冲突；
- 证伪条件；
- 来源与异议证书。

#### Perspective Recall

角色上下文定义为：

```text
Context_i = ProcessRecall_i + EvidenceProjection_i
```

不同科学家从同一证据图获得不同投影，避免全员读取相同摘要后观点同质化。

#### Fork、Merge、Resurrect

- `Fork`：证据无法区分解释时创建平行研究路径；
- `Merge`：找到可同时解释冲突结果的条件变量时执行条件化合并；
- `Resurrect`：新证据满足隔离节点复活条件时重新激活旧假设。

### 5.3 认识显著性

节点保留优先级综合考虑：

- 对核心主张的影响；
- 证据质量；
- 少数意见价值；
- 未解决程度；
- 溯源完整性；
- 与现有节点的冗余度。

高价值反例、边界条件和未解决盲点优先于冗长工具轨迹。

## 6. 双图架构

### 6.1 Research Process Graph

过程图回答「科学家怎样完成研究」。它复用 MemoBrain 的 ReasoningGraph，并扩展：

- `DEBATE`：交叉质询；
- `DECISION`：接受、修正、缩窄或撤回；
- `ASSIGNMENT`：盲点悬赏和分工。

过程图支持普通 Flush、Fold 和 Recall。

### 6.2 Controversy Evidence Graph

证据图回答「当前知道什么」。首版节点包括：

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

首版核心边包括：

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

### 6.3 关键图约束

1. `StudyFinding` 必须 `DERIVED_FROM Source`。
2. 正式 Finding 必须包含原文片段和位置。
3. Claim 必须包含 scope 和 claim_type。
4. 被反驳或隔离节点不得物理删除。
5. DebateCapsule 必须保存正反双方、未解决问题和证伪条件。
6. 正式图修改必须来自 Scientific Event Ledger。
7. 普通 Fold 只能压缩过程图；证据图只能执行可回溯的 Dialectical Fold。

### 6.4 Scientific Event Ledger

7 名科学家不能直接修改正式证据图，只能提交候选事件。事件依次经过：

```text
Schema Validator
→ Semantic Deduplicator
→ Source Verifier
→ Citation Entailment Verifier
→ Evidence Auditor
→ Graph Consistency Checker
→ Graph Projector
```

事件必须幂等、可重放、可撤销，并记录创建者、审核者和状态变化。

## 7. 论文检索与证据治理

### 7.1 证据需求矩阵

检索前由议会建立 Evidence Demand Matrix，至少覆盖：

- 相关性证据；
- 因果与反向因果证据；
- 测量证据；
- 稳健性与复现证据；
- 人群和情境边界；
- 理论机制；
- 零结果与反例。

### 7.2 数据源

首版优先接入：

- OpenAlex；
- Crossref；
- Semantic Scholar；
- Unpaywall；
- 用户上传 PDF；
- 在健康主题中按需接入 PubMed。

网页搜索只用于寻找论文、数据和代码入口，普通网页不得直接支撑高等级因果结论。

### 7.3 共享候选池

7 人分别提出证据需求，Query Planner 合并检索意图并统一执行。论文只下载、解析和核验一次。多名科学家发现同一论文只提高筛选优先级，不构成多份独立证据。

### 7.4 Paper Evidence Packet

每篇深读论文生成结构化证据包，至少包含：

- 规范化来源和版本；
- 研究问题；
- 每项 Study 的样本、设计、变量和分析；
- 每项 StudyFinding 的效应方向、效应量和不确定性；
- 精确原文、页码和章节；
- 作者明确结论；
- 作者局限；
- AI 推导及其创建者；
- 数据、代码和预注册状态。

必须区分「原文事实」「作者解释」和「AI 推导」。

### 7.5 三层证据审计

1. **来源真实性：** DOI、标题、作者、版本、撤稿和 PDF 身份。
2. **引用蕴含：** 原文是否支持主张，是否将相关误写为因果，是否遗漏限定词。
3. **方法质量：** 直接性、设计强度、测量有效性、统计精度、可复现性和外部效度。

方法质量采用多维判断，不用单一「论文总分」替代理由。

### 7.6 证据谱系

Evidence Lineage Graph 至少识别：

- `SAME_DATASET`；
- `OVERLAPPING_SAMPLE`；
- `SAME_RESEARCH_TEAM`；
- `PREPRINT_VERSION_OF`；
- `EXTENSION_OF`；
- `REANALYSIS_OF`；
- `CITES_WITHOUT_NEW_DATA`；
- `META_ANALYSIS_INCLUDES`。

报告必须同时显示论文数量和独立证据簇数量。

### 7.7 证据等级

- **Level A：** 全文可得，原文精确定位，可支撑核心结论；
- **Level B：** 只有摘要和可靠元数据，只能作为低权重候选；
- **Level C：** 综述中的二手描述，只用于发现原始研究；
- **Level D：** 网页或新闻线索，不进入正式证据图。

无法获取全文、表格解析不确定或双抽取冲突时必须显式降级。

### 7.8 停止条件

检索以 Evidence Saturation 为停止条件。连续一轮没有新增主张、反例、边界、研究设计、独立数据源或显著信念更新时，才允许停止相关证据分支。

### 7.9 防止共享证据错误的四种机制

7.6 节的 Evidence Lineage Graph 解决的是「同一份证据被多篇论文重复讲述」；以下四种机制解决的是另一个更隐蔽的风险——七名科学家可能共享同一个证据判断错误（同一份错误摘要、同一篇已撤稿论文、同一条张冠李戴的引用），从而把「共享错误」误判为「独立验证过的共识」（见 3.3 节）。这四种机制目前均为首版设计，未在 MVP 验收范围内强制要求（见第 16 章），后续版本按优先级接入：

1. **盲证据评审（Blind Evidence Review）：** 科学家对证据做首次实质判断时，隐藏论文的作者、期刊和被引次数等声誉信号，只呈现方法论细节和原文内容，避免声誉光环替代方法论审查。
2. **独立双抽取（Independent Extraction）：** 对高影响力的 `StudyFinding`（即将支撑 Level A 因果结论或将被写入 DiscriminatingStudy 的证据），由两个独立的抽取路径分别产出候选 Finding；两者不一致时升级为人工或强模型审计，而不是简单取其一。
3. **来源多样性约束（Source Diversity Constraint）：** 若某一 `Claim` 的全部支持证据共享同一数据集、同一研究团队、同一国家或同一研究设计，自动生成一个 `Blindspot`，标记该结论目前缺乏来源多样性，而不是让证据数量掩盖来源单一的事实。
4. **对抗式检索（Adversarial Retrieval）：** 除常规的支持性检索外，为每个核心主张并行执行反驳、零结果、替代理论、测量批评、复现失败与边界反转六类检索意图，确保取证阶段本身不会只找「支持已有判断」的论文。

## 8. 产品与交互设计

### 8.1 三层体验

1. **Research Brief：** 30 秒理解当前判断、证据强弱、反例、盲点和下一项研究。
2. **Controversy Map：** 探索主张、发现、边界、冲突、盲点和证据谱系。
3. **Audit Trail：** 下钻至论文原文、科学家动作和结论演变。

### 8.2 Research Contract

任务创建时确定：

- 研究问题；
- 人群、时间、地区和语言；
- 证据类型优先级；
- 预印本规则；
- 时间和 API 预算；
- 用户提供的 DOI、BibTeX 或 PDF。

系统先建议原子主张，用户确认后再正式运行。

### 8.3 主界面

采用三栏科研工作台：

- 左栏：7 名科学家的职责、任务、审查量、质询和置信度变化；
- 中栏：争议证据地图；
- 右栏：选中对象的原文、方法、来源和审计状态。

不得展示模型私有思维链，只展示结构化科研动作和可审计决策摘要。

### 8.4 地图视图

- Claim View；
- Debate View；
- Evidence Lineage View；
- Evolution View。

图形不能只用颜色传达语义，还应使用线型、箭头、标签和形状。

### 8.5 核心特色组件

#### Blindspot Radar

按潜在影响和可调查性排列盲点，点大小表示不确定性，类型使用不同符号。

#### DiscriminatingStudy Card

显示研究目标、推荐设计、关键数据、竞争性预测、可解决盲点和预期信息增益。

#### Dissent Certificate

记录异议作者、不同意内容、理由、证据和撤回异议所需条件。

### 8.6 导出报告

报告至少包含：

- 研究范围与检索策略；
- 执行摘要；
- 原子主张；
- 支持、反驳和限定证据；
- 因果、测量、统计复现和边界审查；
- 关键盲点；
- 异议证书；
- 判别研究；
- 审计覆盖和系统局限；
- 完整参考文献和事件附录。

### 8.6.1 重新研究模式（round-12）

`FAILED` 或 `CANCELLED` 任务可以重新研究。`POST /api/tasks/{id}/re-research` 接受可选 `mode`：

- `full`：清除议会 checkpoint，整个研究从 PRECOMMITMENT 重新执行；账本幂等键保证已完成阶段的事件不会重复写入，但每个席位都会被重新征询，过程流会展示整轮重跑；
- `first_gap`（默认）：从**第一个未完成阶段**重新执行——该阶段因失败从未写入其正式事件，重跑会真正补齐；它之前以及之后**已完成**的阶段保持原事件与 carried 状态（不重复写账本、不重复花模型预算）。没有记录到断点时自动退化为 `full`。

两种模式对深度研究与论文审查任务同样生效（同一 Worker 续跑路径）。checkpoint 为每个已运行阶段保存状态快照（`phase_snapshots`），重跑回退到第一个失败阶段之前的状态；`restart_from` 标记由 Service 在首次 gap 时写入，Worker 恢复时据此回退。Web 工作台点击「重新研究」会先弹出模式选择；「继续研究」仍只用于 `PAUSED`（`/resume`）。

### 8.6.2 整合结论详细化（round-12）

最终论文（FinalPaper）必须把争议写清楚，而不是折叠成单一声音：

- `standpoints`：每一方的立场（哪些席位主张什么）、其薄弱之处（weakness）、其依赖的已采纳证据（supporting_evidence）与分歧点（disagreement）；
- `overall_conclusion`：议会是否形成了总体观点、观点是什么——若未形成，明确说明原因；
- `conclusion_evidence`：总体观点所依赖的已采纳证据。

综合模型的 prompt 显式要求输出这些字段；无模型或模型失败时的确定性降级路径同样组装这些字段（从账本中的独立复判与条件化共识）。字段全部可选，旧模型输出不产出时解析器以空值兜底，仍生成合法论文。

### 8.6.3 停止研究即时生效与思考时间实时可见（round-13）

生产故障修复（盲点悬赏阶段表现为「卡死」：时间冻结在 60 秒、停止按钮无效）：

- **停止即时生效**：取消通道（`task_cancel_requests`）原只在议会阶段边界被轮询，而一个慢阶段（盲点悬赏 7 席位串行、每席位最多 120s×2 次重试）可能持续 15 分钟——研究者点「停止研究」后要等整个阶段耗尽才生效。修复：`GatewayDeliberator` 在**每个在飞模型调用**外以 `_await_or_cancel` 每 1s 轮询同一取消通道；检测到停止请求即取消调用并抛 `CouncilCancelled`，orchestrator 将其转为 `CANCELLED` 报告——不写 `PHASE_FAILED`、不记录未完成槽位（停止是重定向，不是对工作的裁决），也绝不 fallback 到一次新的模型调用。停止因此最快 1 秒内生效。
- **思考时间实时可见**：`seat_working` 心跳事件原只在收到模型 token delta 触发的 flush 时落库；模型长思考（无 delta）期间心跳全部积压在内存 buffer，前端「思考中… 已等待 Ns」冻结在最后一次 flush 的时刻。修复：每次心跳 emit 后立即 flush。`ProcessStreamWriter.flush` 以 `asyncio.Lock` 串行化，避免心跳 flush 与 token flush 并发分配重复 `seq`。

### 8.7 多入口产品交付

Poliscope 保留完整 Web 科研工作台作为主要产品，同时提供稳定 API、CLI 和 Agent Skill 入口。四种入口共享同一套 Research Service、EpistemoBrain、7 人议会、MemoBrain、Evidence Gate、Scientific Event Ledger 与 Evidence Graph，不复制科研逻辑：

1. **Web 工作台：** 创建 Research Contract、确认原子主张、观察议会状态，并交互查看 Brief、Map、Audit、Blindspot、Lineage 与 Evolution；
2. **HTTP/SSE API：** 提供任务创建、原子主张确认、启动/暂停/恢复、状态、可恢复事件流和只读产物导出；
3. **Poliscope CLI：** 为自动化和 Skill 提供稳定命令，至少支持 `research start`、`research confirm-claims`、`research status`、`research watch` 与 `research export`；
4. **Claude Code / Codex Skill：** 只负责解析用户意图、生成待确认 Research Contract、调用 CLI/API、展示进度并把产物保存到用户指定目录。

Skill 是薄适配层，不是第二套 Poliscope。它不得直接调用模型或论文数据源，不得直接写数据库或 Evidence Graph，不得绕过原子主张确认和 Evidence Gate，也不得将未审计内容包装为正式结论。Claude Code 与 Codex 可以使用各自要求的清单和入口文件，但必须调用同一 CLI/API Contract，并通过共享适配层避免科研逻辑分叉。

本地 Skill 默认只发送研究问题、Research Contract 和用户明确提供的材料；不得未经确认扫描代码仓库、上传文件或把 PDF、签名 URL、本地绝对路径与私有思维链写入日志和导出。长任务必须返回 `task_id`，允许用户退出调用端后通过 `status`、`watch` 和 `export` 恢复。

## 9. 工程架构

### 9.1 架构选择

首版采用模块化单体和异步 Worker，不采用微服务。

推荐技术栈：

- 后端：Python 3.12、FastAPI、Pydantic、SQLAlchemy、Alembic、pytest；
- 前端：React、TypeScript、React Flow、TanStack Query、Zustand；
- 数据：PostgreSQL、pgvector、Redis、S3 兼容对象存储；
- 实时更新：Server-Sent Events；
- PDF：PyMuPDF 为基础，GROBID 或 Docling 作为结构解析增强；
- 工作流：显式状态机；可使用 LangGraph 编排，但数据库是事实来源。

### 9.2 核心模块

- Research Task Service；
- EpistemoBrain Orchestrator；
- Seven-Seat Council Runtime；
- MemoBrain Adapter；
- Evidence Engine；
- Paper Intelligence Pipeline；
- Model Gateway；
- Tool Gateway；
- Report Generator；
- Evaluation Harness；
- Poliscope CLI 与 Agent Skill Adapter（仅调用应用服务/API，不承载科研判断）。

### 9.3 七人运行方式

7 名科学家共享同一个 Runtime 和模型网关，但拥有：

- 独立角色规格；
- 独立私人状态；
- 独立 MemoBrain；
- 不同 Evidence Projection；
- 不同质询规则和证据排序权重。

不得为 7 名科学家复制 7 套业务代码，也不得部署 7 个服务。

### 9.4 模型路由

- 强推理模型：理论、因果、证伪、联合建模和高影响争议；
- 中型模型：论文抽取、测量边界信息、Query Planning 和 DebateCapsule；
- 轻量模型或规则：元数据规范化、Schema 修复、去重和格式转换。

所有调用统一记录 token、费用、延迟、重试、输入证据和输出 Schema 状态。

### 9.5 可靠性

- 单个席位失败不得中止整个任务；
- 全文失败时降级证据等级；
- 结构化输出失败时修复，仍失败则隔离；
- 模型调用的总时限必须覆盖单次传输、传输重试、`Retry-After` 等待和 Schema 修复；数字 `Retry-After` 必须设上限；
- 正式图由单一 Graph Projector 顺序投影；
- 成本超限时优先停止低影响检索和质询，并报告未完成槽位；
- watchdog 或未预期 Worker 异常发生后，必须等待原任务事务完成取消/回滚，再用独立数据库会话仅对仍为 `RUNNING` 的任务写入失败终态；
- 终态写入与确定性紧急 fallback 使用相互独立的会话，fallback 自身失败不得回滚终态或卡住 Worker；紧急产物必须标注失败原因、只覆盖最后持久化 checkpoint 的范围和证据缺口，不得覆盖已完成终态；
- 深度研究 fallback 输出 `FinalPaper`，论文审查 fallback 输出 `PaperReviewReport`，两者都属于可审计的表达层产物，不得写成正式证据节点；
- 比赛演示同时提供 Live、Replay 和固定快照模式。

## 10. 数据模型与项目结构

### 10.1 建议核心表

```text
research_tasks
research_scopes
council_rounds
scientist_runs
scientific_events
graph_nodes
graph_edges
sources
source_versions
studies
findings
citation_anchors
blindspots
debate_capsules
dissent_certificates
model_calls
tool_calls
memory_snapshots
```

类型特有字段可使用 JSONB，但 ID、type、status、task_id、source_id、created_by、verified_by、confidence 和 provenance 应使用可查询列。

### 10.2 建议目录

```text
poliscope/
├─ apps/
│  ├─ api/
│  ├─ worker/
│  ├─ cli/
│  └─ web/
├─ skills/
│  └─ poliscope/          # 共享 Skill 说明、Claude Code/Codex 清单与薄适配入口
├─ packages/
│  ├─ council/
│  ├─ epistemo/
│  ├─ memory/
│  ├─ evidence/
│  ├─ papers/
│  ├─ models/
│  ├─ tools/
│  ├─ reports/
│  └─ evaluation/
├─ migrations/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ golden/
│  └─ evals/
├─ docs/
├─ demo/
└─ docker-compose.yml
```

## 11. 评测设计

### 11.1 ForesightBlindspot Benchmark

使用时间切片争议案例：系统只能读取截止日前语料，预测后来研究确认的混杂、测量、边界、复现和理论盲点。

首版聚焦数字行为、社交媒体和心理健康：

- 3 个完整精标案例；
- 5 个轻量案例；
- 1 个核心演示案例。

金标准盲点必须满足：

- 截止日后有高质量研究确认；
- 截止日前存在合理先兆；
- 不依赖完全不可预见的新平台、事件或方法。

### 11.2 核心指标

#### 盲点

- Blindspot Recall；
- Blindspot Precision；
- 高影响盲点召回；
- 具体性；
- 可行动性；
- 前瞻距离。

#### 证据

- Citation Existence Accuracy；
- Citation Entailment Accuracy；
- Evidence Independence Accuracy；
- Causal Overclaim Rate；
- 全文证据覆盖率。

#### 记忆

- Context Compression Ratio；
- Scientific Backbone Retention；
- Contradiction Retention；
- Long-Horizon Drift；
- Recovery Quality。

#### 协作

- Role Diversity；
- Debate Utility；
- Belief Update Quality；
- Dissent Preservation Rate；
- False Consensus Rate。

#### 成本

- Cost per Valid Blindspot；
- Token per Admitted Finding；
- Redundant Work Ratio；
- 每轮边际增益。

### 11.3 基线

1. Single-Agent Deep Research；
2. Fixed Multi-Agent Debate；
3. Council + Linear Context；
4. Council + MemoBrain，但无 Evidence Engine；
5. 完整 Poliscope。

### 11.4 关键消融

优先执行：

- 去掉独立预承诺；
- 去掉对抗性证伪者；
- 去掉证据审计员；
- 普通 Fold 替代 Dialectical Fold；
- 去掉证据谱系；
- 去掉 MemoBrain。

### 11.5 标注与统计

- 至少 2 名独立标注者和 1 名仲裁者；
- 对系统身份盲评；
- 报告 Cohen’s Kappa 或 Krippendorff’s Alpha；
- 使用配对比较、Bootstrap 置信区间和适当的非参数检验；
- 报告逐案例结果和效应量，不只报告平均分。

### 11.6 泄漏防护

- 评测使用截止日前本地封闭语料；
- 禁止读取未来引用关系和现代检索排名；
- 关键案例可匿名化变量；
- 所有输出必须绑定截止日前证据；
- 明确声明基础模型参数可能包含未来知识，不能宣称完全消除该风险。

## 12. 8 周开发计划

### 第 1 周：单科学家垂直切片

实现 PDF → StudyFinding → Scientific Event → Evidence Graph 的最小闭环。

### 第 2 周：MemoBrain 与过程图

接入私人记忆、episode、Recall 和快照，验证过程 Fold 不损伤正式证据。

### 第 3 周：七人议会

实现 7 名角色、独立预承诺、Evidence Request、共享候选池和轮次状态机。

### 第 4 周：证据核验

实现 DOI、原文锚点、引用蕴含、证据等级、Event Ledger 和 Quarantine。

### 第 5 周：争议与盲点

实现交叉质询、盲点评分、Dialectical Fold、Dissent Certificate 和判别研究。

### 第 6 周：前端核心体验

实现 Research Brief、议会状态、Evidence Map、审计抽屉、盲点雷达和演化时间线。

### 第 7 周：评测与案例

完成核心案例、基线、关键消融、人工标注和成本统计。

### 第 8 周：稳定与提交

完成测试、Replay、固定快照、一键启动、文档和演示视频。

## 13. 测试策略

### 13.1 单元测试

覆盖：

- Claim 与 Finding 状态机；
- Event Ledger 幂等性；
- Graph Projector；
- Evidence Gate；
- Fold 后依赖关系；
- 证据等级；
- Blindspot 评分；
- 预算和停止条件。

### 13.2 集成测试

覆盖：

- PDF → Paper Evidence Packet；
- Scientific Event → Evidence Graph；
- MemoBrain Recall → 角色上下文；
- 七人预承诺 → 共享候选池；
- 质询 → Claim 更新；
- Quarantine → Resurrect。

### 13.3 Golden Tests

固定论文、原文、研究设计、Finding、支持关系和已知盲点。每次修改 Prompt、模型或解析器都必须运行。

### 13.4 端到端测试

固定核心案例，验证：

```text
创建任务
→ 上传 PDF
→ 七人议会
→ 引用审计
→ 发现盲点
→ 结论更新
→ 生成报告
```

## 14. 演示视频设计

5 分钟视频围绕以下叙事：

1. 普通 Deep Research 给出统一但不可审计的结论；
2. 7 名科学家提交不同初始判断；
3. 证据图随取证生长；
4. 审计员发现高被引论文没有支持 Agent 的因果表述，结论降级；
5. 测量专家和证伪者发现自报告偏差；
6. 证据谱系揭示多篇论文共享同一数据集；
7. 盲点雷达发布高影响悬赏；
8. 新日志研究使结论变为条件化支持；
9. 少数异议被保留；
10. 系统生成可区分竞争解释的判别研究。

必须展示的 4 个画面：

- 7 人初始判断不同；
- 引用审计导致结论降级；
- 论文数量与独立证据簇数量不同；
- 盲点转化为判别研究。

## 15. 风险与局限

1. 现代模型可能包含未来论文知识；
2. 全文获取受开放访问限制；
3. PDF 和表格解析存在错误；
4. 专家标注存在主观性；
5. 未来确认不等于绝对真理；
6. 多智能体不能替代领域专家；
7. 正式证据图仍依赖抽取与审计质量；
8. 社会科学结论具有强情境性；
9. 7 人全程参与成本较高；
10. 首版只验证有限领域和案例。

系统必须显式展示未知项、证据等级、未调查槽位和失败原因，不得用流畅文本掩盖缺失证据。

## 16. 验收标准

MVP 只有同时满足以下条件才视为完成：

1. 7 名科学家均完成独立预承诺和最终复判；
2. 每名科学家拥有独立 Private MemoBrain；
3. Process Graph 与 Evidence Graph 分离；
4. 所有正式图修改来自 Event Ledger；
5. 正式 StudyFinding 绑定精确原文位置；
6. 至少发生一轮有效交叉质询；
7. 证据审计导致至少一次主张修正或证据降级；
8. 系统发现并记录至少一个高价值 Blindspot；
9. Dialectical Fold 保留正反证据和未解决问题；
10. 至少生成一份 Dissent Certificate；
11. 至少生成一项 DiscriminatingStudy；
12. 产品提供 Research Brief、Controversy Map 和 Audit Trail；
13. 至少完成一个普通多智能体基线对比；
14. 核心案例可通过 Replay 模式稳定复现；
15. 同一核心案例可从 Web、CLI 和 HTTP API 创建并导出等价的已审计结果；
16. Claude Code 与 Codex Skill 均只通过稳定 CLI/API Contract 调用 Poliscope，保留用户确认、安全门和审计语义。
