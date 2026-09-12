# 论文：EpistemoBrain（docs/paper/）

英文论文稿，仿照 MemoBrain 论文（*MemoBrain: Executive Memory as an
Agentic Brain for Reasoning*，ACL Findings 2026，
https://aclanthology.org/2026.findings-acl.127/ ）的章节结构与文风撰写。

## 文件

- `epistemobrain.tex` — 论文正文（标题、作者、摘要、正文、参考文献、附录都在此文件内），是 self-contained body：不含 `\documentclass`，编译时接入官方 ACL 模板的 `.sty`
- `epistemobrain_main.tex` — 编译驱动（wrapper）：提供 ACL 模板 preamble 与 `\begin{document}`，`\input{epistemobrain}` 引入正文
- `acl.sty` — 官方 ACL 模板（来自 https://github.com/acl-org/acl-style-files ，仓库根目录），编译依赖
- `epistemobrain_main.pdf` — 已编译的 PDF（ACL Findings 2026 版式，11 页，由 `epistemobrain_main.tex` 编译生成；旧的独立构建 `epistemobrain.pdf` 已删除）
- `README.md` — 本文件

## 作者信息（按要求填写）

- 第一作者：**马珠淇**（中国人民大学 / Renmin University of China）— 2025201699@ruc.edu.cn
- 通讯作者：**崔颢蓬**（清华大学 / Tsinghua University，†）— chp25@mails.tsinghua.edu.cn

仓库已通过 Zenodo 归档并获得 DOI：`10.5281/zenodo.22541350`（已写入论文 3.8 节）。作者邮箱已填入作者块；本稿已无待填占位内容。

## 编译

仓库已自带官方 `acl.sty`（来自 https://github.com/acl-org/acl-style-files ，
与正文同目录）。编译 wrapper（两遍，因 `\maketitle` 在正文内、交叉引用需重排）：

```bash
# 在本目录下：
pdflatex -interaction=nonstopmode epistemobrain_main
pdflatex -interaction=nonstopmode epistemobrain_main
```

生成 `epistemobrain_main.pdf`。本稿不使用 `.bib` 文件（参考文献以
`thebibliography` 内联），因此 `bibtex` 步骤可省略。

正文（`epistemobrain.tex`）的 `\title` / `\author` 之后必须紧跟 `\maketitle`——
ACL 模板的 `\maketitle` 会建立跨双栏的标题区并切入双栏模式，abstract 环境依赖它。
作者行中的 `\dagger` 必须写作 `$\dagger$`（`\dagger` 是数学模式命令，裸用在文本
模式会触发连锁 Missing \$ 错误）。

## 结构与设计要点（对照 MemoBrain 论文格式）

| 本稿章节 | 对应内容 | 对应 MemoBrain 论文 |
|---|---|---|
| Abstract | 四段式：问题 → 方法 → 机制 → 评测 | Abstract |
| 1 Introduction | 问题动机（复现危机文献）+ 系统概述 + 三点贡献 | 1 Introduction |
| 2 Related Work | 多智能体辩论 / 记忆 / RAG 与事实核验 / 科学智能体 | 2 Related Work |
| 3 Method | 3.1 概述与形式化 / 3.2 **泛化执行记忆**（单智能体↔多智能体推广对照，Table 1）/ 3.3 集体协议与非投票聚合 / 3.4 双图治理 / 3.5 准入闸门 / 3.6 缺口驱动路由 / 3.7 **参考实例：科学争议审查**（七角色表 Table 2）/ 3.8 工程实现 | 3 Method |
| 4 Experiments | 4.1 ForesightBlindspot 基准设计 / 4.2 基线构造正确性（评测管道发现的修复）/ 4.3 脚本化案例结果（5 变体对比表）/ 4.4 消融分析（6 变体消融表）/ 4.5 真实模型 + 语义裁判结果（5 变体 × 3 次重复取均值）/ 4.6 权衡的部署含义 / 4.7 代价与协议负担（账本事件量 vs 盲点产出）/ 4.8 系统验证 | 4 Experiment |
| 5 Conclusion | 结论 | 5 Conclusion |
| Limitations | 六条边界：**通用性是论证出来的、不是测出来的** / 证据等级上限 / 模型强度依赖 / 测量工具在建 / 单案例无留出集 / 上游记忆接入 | Limitations（MemoBrain 独立成节） |
| Appendix A | 实现细节：确定性 harness、权限即设计、可复现性 | Appendix A |

### 图表清单

- **Figure 1**（`fig:overview`）：架构图。TikZ 绘制，版式参照 MemoBrain 论文 Figure 1（顶部横向主流程 + 左下放大对照面板 + 右侧编号操作栈 + 彩色圆点图例）。顶部为 $n$ 个角色化 agent 的集体与各自的私人召回；左下对照「单个成员持有的工作上下文」与「集体持有的共享图」；右下为三个泛化操作与 committed plane 流水线（Event Ledger → Admission Gate → Graph Projector → Committed Graph）。**替换了早期的 `\fbox` 占位框，图内不含任何「议会／七席」表述。**
- **Figure 2**（`fig:tradeoff`）：双面板结果图。面板 (a) 语义裁判下的召回/精度分组柱状图，面板 (b) 未填充槽位数。用 pgfplots 绘制，数据取自 `data/live_ablation_repeat.md`。
- **Table 1**（`tab:generalization`，单智能体↔多智能体推广对照）、**Table 2**（`tab:roles`，参考实例的七个角色）、**Table 3**（五基线脚本化）、**Table 4**（六消融脚本化）、**Table 5**（真实模型重复运行）、**Table 6**（代价与协议负担）。Table 3–5 均标注每列最优值（加粗）；Table 4 的谱系消融 `Indep.` 列用 $\dagger$ 标注「数值越高反而越糟」。

## 方法定位（重要）

本稿的核心定位是：**EpistemoBrain 是一套领域无关的通用多智能体记忆压缩与治理方法，是 MemoBrain 执行记忆机制从单智能体到多智能体的推广。Poliscope 是它的一个应用实例，不是方法本身。**

- **EpistemoBrain** ＝ 集体执行记忆与治理层；机制本身不出现任何领域名，只依赖三样由场景注入的东西：**角色规格**、**准入策略**、**共享图 schema**
- **Poliscope** ＝ 该机制在「科学争议审查」场景下的开源实现；七人议会是**本项目填入的角色规格取值**，是被机制管理的对象，不是 EpistemoBrain 本身
- 三个原生操作泛化：Flush→Quarantine、Fold→Dialectical Fold、Recall→Perspective Recall
- 单智能体场景不会出现的两条硬约束：记忆由私有变公共、过程与证据必须分图

论文按「通用机制在前、参考实例在后」组织：§3.1–§3.6 讲机制，§3.7 才落到参考实例，§3.8 是工程实现；§3.2 含对照表 `tab:generalization`。实验章开头明确声明「本节测的是参考实例」；Limitations 第一条为「通用性是论证出来的、不是测出来的」。依次对应：论文 §3.2、§3.7、§2.2，技术白皮书 §2.1 与图 1（`docs/tech/figures/memobrain-generalization.drawio`），以及本文件（`docs/paper/README.md`）。

> 绘图依赖 `tikz` / `pgfplots`，已加入 `epistemobrain_main.tex` preamble 并定义 `epBlue` / `epTeal` / `epAmber` / `epGrey` / `epLight` 五个配色。图表全部**内联绘制**，不需要任何外部图片文件，论文仍是自包含的。

## 实验数字的来源（诚实性声明）

第 4.3 节（五基线表）与第 4.4 节（六消融表）中的数字来自
`scripts/arbor_eval.py`（`--all` 一次跑出 11 变体）在脚本化 demo case
（无模型调用、无网络、无数据库）上的确定性测量，属于受控脚本化素材上的
**B_dev** 结果；`packages/evaluation/` 的评测 harness、scoring 函数与
`tests/unit/test_evaluation_ablations.py` 可逐项复现。消融表的数字
解读：三个消融有机制级效应（去证伪者/去审计员 → 盲点 recall 下降；
去谱系 → 证据独立性被抬高为 1.0），两个消融在脚本化素材上无差异
（去预承诺、去 MemoBrain）——论文如实标注为测量边界而非机制无效。
真实模型受控对照实验**已经运行**：`scripts/live_ablation.py`
（`--semantic --repeat 3`，DeepSeek-V4-Flash 于官方 DeepSeek API 端点，
5 个关键变体各 3 次重复取均值），结果记入论文 4.5 节
（`\subsection{Real-Model Evaluation and the Semantic Judge}`）与
`docs/tech/ch10_evaluation.tex`。均值聚合表随本仓库归档于
`docs/paper/data/live_ablation_repeat.md`，论文 4.5 节数字可逐行核验
（逐 run 原始日志与早期弃用扫描的备份属临时文件，已清理；如需复查
可从 git 历史恢复）。核心结论：关键词版盲点 recall 在自由
文本下塌陷为 0；语义裁判（`packages/evaluation/semantic_blindspot.py`）
修复了测量；降方差后呈现稳定的精度—召回权衡而非噪声——完整系统
precision 1.000 / recall 0.286（3 次一致），所有简化变体（含单 agent）
recall 0.809--1.000 但 precision 0.406--0.586，去预承诺 3 次全部复现
recall 1.000；unfilled 12.3 vs 1.0--1.7 表明协议负担是 flash 级模型的
硬约束。早期 11 变体单次扫描因端点限流两天、12 个 run 被污染而弃用，
不参与论文任何数字。

## 评测实验的演进记录（Arbor 会话）

评测管道的建立与两轮修正实验记录在
`.arbor/sessions/foresight-blindspot-001/REPORT.md`（idea tree、
experiment reports、executor prompts 均在对应目录下）：

- 节点 1.1「基线正确化」：修正单 agent 席位选择与最终复判全席遍历
- 节点 2.1「素材多席位化」：7 席各按专业给出盲点答案，gold 关键词 2→7
- 节点 1.2「sanity guard」：变体差异回归保护测试
