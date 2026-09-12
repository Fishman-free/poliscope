# Poliscope

> **阅读语言 / Language / 語言：** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> 七位 AI 科学家，一场没有和稀泥的科研审查。欢迎来到现场。
>
> **在线体验：** [https://poliscope.tech/](https://poliscope.tech/) — 作者部署的公开实例，打开即用，无需安装
>
> **学术论文：** [《EpistemoBrain: Generalizing Executive Memory to Multi-Agent Systems》](docs/paper/epistemobrain_main.pdf)（ACL Findings 2026 格式；LaTeX 源见 `docs/paper/`）
>
> **方法基座：** EpistemoBrain 源自 **MemoBrain** — [论文](https://aclanthology.org/2026.findings-acl.127/) · [代码](https://github.com/qhjqhj00/MemoBrain)

想象一下：你正在审阅一篇「社交媒体导致青少年抑郁」的研究——结论很漂亮，引用很齐全，但没有人告诉你这篇论文和另外五篇用的是同一份数据，没有人告诉你因果表述其实只是相关。Poliscope 就是为这个场景造的：面向计算社会科学争议问题的**深度研究智能体**——**7 名各有专长的 AI 科学家**独立取证、交叉质询、专门找反例，产出一张**结论与局限并排、证据可溯源、异议不删除**的争议证据地图。

## 一、设计思路

### 1. 解决问题

让 7 个 Agent 轮流发言、再让第 8 个写摘要，分歧会在总结那一步被拍平，解决不了任何真实的科研盲点。Poliscope 反着来：先设计「一个可靠的多智能体集体该有的组织结构与记忆机制」，再把 7 个角色放进去。这套机制叫 **EpistemoBrain**——它本身是**领域无关**的通用方法，只依赖三样由场景注入的东西：角色规格、准入策略、共享图 schema。科学争议审查是它的参考实例，Poliscope 是这个实例的开源实现。

### 2. 七人全程参与

7 个席位（理论建构者、因果推断专家、测量与构念专家、统计与复现专家、边界与情境专家、对抗性证伪者、证据与溯源审计员）在每项正式研究中**全程参与**：每轮发言有预算、没有新信息就 `PASS`、共享同一份检索缓存。每个席位有独立角色规格、私人状态、私人记忆与证据排序权重，取证阶段互不可见，先各自下判断再交换证据——交叉质询才是真的交叉质询。在 Poliscope 这一实例中，EpistemoBrain 是无投票权的组织脑，不是第 8 名科学家。

### 3. 三层科研记忆

**Private Brain**（每位科学家自己的探索过程）→ **Collective MemoBrain**（谁提出了什么、谁攻击了什么、哪些争议未解决、下一步该查什么）→ **Evidence Graph**（可审计的争议证据地图，只存经过校验的知识，不含内部思考）。

### 4. 异议保真，议会不投票

**禁止多数投票裁决科研真理。** 七轮协议：独立预承诺 → 专业取证 → 证据交换 → 交叉质询 → 盲点悬赏 → 联合建模 → 最终复判。被反驳的观点不消失、可追溯；真正的分歧写成具名**异议证书（Dissent Certificate）**，而不是被多数意见吞掉。

### 5. 双图治理

过程与结论分开存放：七人的取证、犯错、走弯路记入 **Process Graph**；正式结论必须经科研事件账本、由唯一写入者 **Graph Projector** 审核后，才落地到 **Evidence Graph**。这一步在数据库权限层面强制——七名科学家根本没有写证据图的权限。

### 6. 证据门

每条正式结论过六道审核：Schema → 语义去重 → 来源核验 → 引用蕴含 → 方法质量 → 图一致性。证据分 A–D 四级，摘要不许单独支撑高置信因果结论；引言假设、讨论段推测不许写成实证结果；每个 `StudyFinding` 绑定来源、章节、页码与原文引用。

### 7. 论文数量 ≠ 独立证据数量

六篇共享同一数据集的论文只是一份证据讲了六遍。证据谱系自动识别同数据集 / 重叠样本 / 预印本与正式版等依赖，报告同时显示「论文数量」与「独立证据簇数量」。

### 8. 认识论路由

证据图暴露的缺口生成盲点悬赏，按影响、不确定性、可调查性打分排序，由七人认领——是证据状态驱动下一步调查，而不是主持人按台本点名。Blindspot 是产品的一等公民对象，不是报告末尾的「局限性」文字。

### 9. 方法与出处：从 MemoBrain 到 EpistemoBrain

EpistemoBrain 不是凭空设计的，它的工程基座是 **MemoBrain** —— [论文](https://aclanthology.org/2026.findings-acl.127/)（ACL Findings 2026）· [代码](https://github.com/qhjqhj00/MemoBrain)。

MemoBrain 为**单个**推理智能体提供执行记忆：一张依赖感知的推理图，用 `Flush` 剪掉无效路径、`Fold` 压缩已完成的子轨迹、`Recall` 在固定 token 预算内重建高显著上下文。它解决的是「一个 Agent 跑长了会忘事、上下文被低价值内容占满」的问题。

EpistemoBrain 把它推广到多智能体：当记忆的所有者从「写它的人」变成「没有写它的同伴」，三个操作的含义必须重写——`Flush` → `Quarantine`、`Fold` → `Dialectical Fold`、`Recall` → `Perspective Recall`，再适配进七人议会的决策框架。

**省在哪。** 收益分两层：

- **继承自 MemoBrain**：长程上下文不膨胀。完成的子轨迹折叠成摘要节点，无效路径不再占窗口，推理骨架始终留在预算内。
- **多智能体新增**：**共享检索缓存**——七席证据需求合并去重，同一篇论文只下载、解析、验证 DOI 一次而不是七次；**发言预算**——没有新信息就 `PASS`；**语义去重**——重复内容不触发重新推理；**分层模型**——复杂判断走强模型，格式化与抽取走轻量模型；**召回预算**——每席 800 字符，保住骨架但不重放转录。

**实测到的那一条**：脚本化对照案例上，完整系统每找到一个金标盲点消耗 **8.6** 个账本事件，单智能体需要 **34.0** 个——覆盖率是它的 7 倍，单位成本反而低约 4 倍。

> **诚实说明**：系统对每次模型与工具调用都逐笔记录 input/output token、费用、延迟与重试（八层成本控制见[技术白皮书](docs/tech/tech.pdf)第 8 章），但论文尚未把「token 节省率」作为独立指标做对照实验。上面两层是机制层面的论证，只有第三段是实测数字。

## 二、应用场景

- **写综述前**：先画一张「哪些证据支持、哪些反对、哪里同源重复、哪里空白」的地图，带着缺口清单开始阅读。
- **评估一篇论文**：上传 PDF 做论文审查，议会逐项指出论证不严谨、证据不充分、测量与样本代表性问题，并给出改进建议。
- **设计下一步研究**：未被解决的盲点被翻译成具体可执行的判别研究（DiscriminatingStudy）。
- **评审与答辩**：每个关键判断都能点回原文位置，展示的是一个可追溯、可证伪的科研过程。
- **敏感议题**：涉及心理健康的问题，报告明确标注「科研辅助，不是临床诊断」。

## 三、产品优势

- **每个关键判断都能点回原文**：绑定来源、章节、页码、原文引用，过三层审核才进图。
- **论文数量 ≠ 独立证据数量**：两个数字分开显示，看得出「共识」是七次独立观察还是一次观察讲了七遍。
- **诚实报告缺口**：模型没配好、预算耗尽、席位缺席都逐条列出，任务显式标记「带缺口完成」——空白不等于没问题。
- **证据强度分级**：Level A（全文可得）到 Level D（新闻线索）全标出，低级别证据不许单独支撑高置信因果结论。
- **异议永久保留**：不靠多数票压下去，异议者拿到具名 `DissentCertificate`。
- **盲点驱动下一步调查**：Blindspot 是一等公民对象，不是报告末尾的「局限性」。
- **思考链路可见，思维链不裸奔**：原始推理折叠在轮次下，标注「过程数据，不是证据」。
- **相关不自动升级为因果**：横截面数据 + 因果主张的组合被证据门拦截。
- **跑完有一份可下载的最终论文**：整合七席终审与参考文献，可导出 Markdown。
- **四个入口，一套内核**：网页 / API / CLI / Agent Skill 走同一条研究契约，没有第二条绕过证据门的路径。

## 四、产品能力

产品中心是**证据地图**，不是聊天框。八个视图：

| 视图 | 回答的问题 |
|---|---|
| Research Brief | 30 秒了解当前判断、证据强弱、反例与盲点 |
| Controversy Map | 主张、发现、冲突与盲点组成的可交互证据地图 |
| Audit Trail | 完整事件账本：谁在什么时候做了什么判断 |
| Council | 7 名科学家的完整链路与条件化共识面板 |
| Blindspot Radar | 按影响与可调查性排列的盲点雷达 |
| Evolution View | 主张与盲点随证据演化的时间线 |
| 最终论文 | 可下载的论文式产出 |
| 知识库 | 你的长期记忆，检索命中直接参与议会推理 |

## 五、使用方法

### 1. 网页

打开 [https://poliscope.tech/](https://poliscope.tech/)，注册即用。两个功能：

- **深度研究**：输入争议问题，七人议会独立取证、交叉质询，产出可审计的证据地图；
- **论文审查**：上传论文（PDF / Word / PPT / Excel / HTML / TXT，≤ 20 MB），议会逐项审查论证严谨性与证据充分性，完成后可在「补充提问」继续追问。

深度研究在联合建模前有一个**有界的人类方向性检查点**：7 名科学家完成前 5 轮后暂停，等你提交方向性备注（或留空继续）；宽限期（默认 15 分钟）内未操作，服务端自动以「无方向性干预」继续——研究不会因你走开而卡死。

### 2. 编程 Agent（Skill）

在 Claude Code / Codex 里直接调用，无需切换网页。**只需 Node 18+，不用克隆、不用建 Python 环境。**

**装 skill（一条命令）：**

```bash
npx github:Fishman-free/poliscope install-skill
```

装到 `~/.claude/skills/poliscope/`。**重启 Agent**，然后输入 `/poliscope` 就能用。

**怎么调用：**

```
/poliscope 帮我研究：青少年社交媒体使用是否导致抑郁症状？
```

Skill 会带着你走完整条流水线：整理成 Research Contract → **先给你确认，不直接提交** → `poliscope start` → `confirm-claims` 挑要查的主张 → `watch` 跟到跑完 → 把证据地图、议会记录、每位科学家的立场导出到你的仓库。

连线上实例（`https://poliscope.tech`）前先登录一次：

```bash
npx github:Fishman-free/poliscope login --base-url https://poliscope.tech
```

**不装 skill、只用命令行也行：**

```bash
npx github:Fishman-free/poliscope --help          # 全部子命令
npx github:Fishman-free/poliscope health          # 探活
npx github:Fishman-free/poliscope status <task>   # 看任务
npx github:Fishman-free/poliscope export <task>   # 导出结果
```

**装到别处 / 覆盖已有版本：**

```bash
npx github:Fishman-free/poliscope install-skill --dir <技能目录>
npx github:Fishman-free/poliscope install-skill --force
```

完整用法见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)。

## 六、请诚实对待它的结论

- **不是医疗建议。** 涉及心理健康的问题，报告明确标注「科研辅助，不是临床诊断」。
- **不编结论。** 模型没配好、预算耗尽、席位缺席，都会显式标记——空白不等于没问题。
- **模型置信度不是统计证据。** 模型判断、作者原话与统计推断分开标注。
- **异议不删除。** 被反驳、被隔离的观点保留原文出处，可追溯。

## 七、部署与文档

- 自建部署（Docker Compose 一键起、接域名）与完整 CLI / API 用法：[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- 正式设计规格：[`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)
- 技术白皮书：[《Poliscope 技术白皮书》](docs/tech/tech.pdf)

## 许可证

Poliscope 自身代码以 [MIT 许可证](LICENSE) 开源；执行记忆基座 [MemoBrain](https://github.com/qhjqhj00/MemoBrain) 保留其自身许可证，归属细节见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属)。
