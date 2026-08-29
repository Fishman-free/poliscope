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

- 第一作者：**马珠淇**（中国人民大学 / Renmin University of China）
- 通讯作者：**崔颢蓬**（清华大学 / Tsinghua University，†）

投稿前需替换的占位内容（文件中已标注 `Placeholders to fill before
submission`）：

1. 作者邮箱（`\texttt{\{zhuqi.ma, haopeng.cui\}@example.edu}`）
2. 开源仓库 DOI（论文引用 `Fishman-free/poliscope` 时建议补 DOI）
3. 致谢与基金信息（可选）

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
| 3 Method | 3.1 Overview / 3.2 七席议会 / 3.3 分层记忆 / 3.4 双图治理 / 3.5 证据门 / 3.6 认识论路由 / 3.7 人类检查点 / 3.8 工程实现 | 3 Method |
| 4 Experiments | 4.1 ForesightBlindspot 基准设计 / 4.2 五基线 / 4.3 基线构造正确性（评测管道发现的修复）/ 4.4 脚本化案例结果（5 变体对比表）/ 4.5 消融分析（6 变体消融表，round-15 新增）/ 4.6 真实模型 + 语义裁判结果（5 个关键变体 × 3 次重复取均值，round-16 新增）/ 4.7 系统验证 | 4 Experiment |
| 5 Conclusion | 结论与未来工作 | 5 Conclusion |
| Appendix A | 实现细节：确定性 harness、权限即设计、可复现性 | Appendix A |

## 实验数字的来源（诚实性声明）

第 4.4 节（五基线表）与第 4.5 节（六消融表）中的数字来自
`scripts/arbor_eval.py`（`--all` 一次跑出 11 变体）在脚本化 demo case
（无模型调用、无网络、无数据库）上的确定性测量，属于受控脚本化素材上的
**B_dev** 结果；`packages/evaluation/` 的评测 harness、scoring 函数与
`tests/unit/test_evaluation_ablations.py` 可逐项复现。消融表的数字
解读：三个消融有机制级效应（去证伪者/去审计员 → 盲点 recall 下降；
去谱系 → 证据独立性被抬高为 1.0），两个消融在脚本化素材上无差异
（去预承诺、去 MemoBrain）——论文如实标注为测量边界而非机制无效。
真实模型受控对照实验**已经运行**：`scripts/live_ablation.py`
（`--semantic --repeat 3`，DeepSeek-V4-Flash 于官方 DeepSeek API 端点，
5 个关键变体各 3 次重复取均值），结果记入论文 4.6 节
（`\subsection{Results with a Real Model and a Semantic Judge}`）与
`docs/tech/ch10_evaluation.tex`。均值聚合表随本仓库归档于
`docs/paper/data/live_ablation_repeat.md`，论文 4.6 节数字可逐行核验
（逐 run 原始日志与早期弃用扫描的备份属临时文件，已清理；如需复查
可从 git 历史恢复）。核心结论：关键词版盲点 recall 在自由
文本下塌陷为 0；语义裁判（`packages/evaluation/semantic_blindspot.py`）
修复了测量；降方差后呈现稳定的精度—召回权衡而非噪声——完整系统
precision 1.000 / recall 0.286（3 次一致），所有简化变体（含单 agent）
recall 0.809--1.000 但 precision 0.406--0.586，去预承诺 3 次全部复现
recall 1.000；unfilled 12.3 vs 1.0--1.7 表明协议负担是 flash 级模型的
硬约束。早期 11 变体单次扫描因端点限流两天、12 个 run 被污染而弃用，
不参与论文任何数字。

以下内容论文中如实标注为未来工作，**没有编造任何数字**：

- 时间切片语料（截止日前封闭语料）尚未采集 → 无独立 B_test
- 人工标注（金标准盲点的 Kappa/Alpha 一致性）尚未产生标注数据
- 真实模型的多次重复降方差实验已对 5 个关键变体完成（n=3 均值，
  DeepSeek 官方端点），其余 6 变体的重复实验留作后续工作（早期单次
  扫描数据因端点限流弃用）

## 评测实验的演进记录（Arbor 会话）

评测管道的建立与两轮修正实验记录在
`.arbor/sessions/foresight-blindspot-001/REPORT.md`（idea tree、
experiment reports、executor prompts 均在对应目录下）：

- 节点 1.1「基线正确化」：修正单 agent 席位选择与最终复判全席遍历
- 节点 2.1「素材多席位化」：7 席各按专业给出盲点答案，gold 关键词 2→7
- 节点 1.2「sanity guard」：变体差异回归保护测试
