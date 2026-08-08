# 论文：EpistemoBrain（docs/paper/）

英文论文稿，仿照 MemoBrain 论文（*MemoBrain: Executive Memory as an
Agentic Brain for Reasoning*，ACL Findings 2026，
https://aclanthology.org/2026.findings-acl.127/ ）的章节结构与文风撰写。

## 文件

- `epistemobrain.tex` — 完整 LaTeX 稿件（标题、作者、摘要、正文、参考文献、附录都在此文件内；编译时接入官方 ACL 模板的 `.sty` 即可）
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

需要官方 ACLPUB 2026 模板（`acl.sty` / `acl_natbib.sty` 等）。拿到模板后：

```bash
# 把官方模板的 .sty 与本文件放在同一目录，然后：
pdflatex epistemobrain
bibtex  epistemobrain      # 本稿使用 thebibliography 环境，可跳过
pdflatex epistemobrain
pdflatex epistemobrain
```

本稿不使用 `.bib` 文件（参考文献以 `thebibliography` 内联），因此
`bibtex` 步骤可省略。

## 结构与设计要点（对照 MemoBrain 论文格式）

| 本稿章节 | 对应内容 | 对应 MemoBrain 论文 |
|---|---|---|
| Abstract | 四段式：问题 → 方法 → 机制 → 评测 | Abstract |
| 1 Introduction | 问题动机（复现危机文献）+ 系统概述 + 三点贡献 | 1 Introduction |
| 2 Related Work | 多智能体辩论 / 记忆 / RAG 与事实核验 / 科学智能体 | 2 Related Work |
| 3 Method | 3.1 Overview / 3.2 七席议会 / 3.3 分层记忆 / 3.4 双图治理 / 3.5 证据门 / 3.6 认识论路由 / 3.7 人类检查点 / 3.8 工程实现 | 3 Method |
| 4 Experiments | 4.1 ForesightBlindspot 基准设计 / 4.2 五基线 / 4.3 基线构造正确性（评测管道发现的修复）/ 4.4 脚本化案例结果（5 变体对比表）/ 4.5 系统验证 | 4 Experiment |
| 5 Conclusion | 结论与未来工作 | 5 Conclusion |
| Appendix A | 实现细节：确定性 harness、权限即设计、可复现性 | Appendix A |

## 实验数字的来源（诚实性声明）

第 4.4 节表格中的数字来自 `scripts/arbor_eval.py` 在脚本化 demo case
（无模型调用、无网络、无数据库）上的确定性测量，属于受控脚本化素材上的
**B_dev** 结果；`packages/evaluation/` 的评测 harness 与 scoring 函数可
直接复现。以下内容论文中如实标注为未来工作，**没有编造任何数字**：

- 时间切片语料（截止日前封闭语料）尚未采集 → 无独立 B_test
- 真实模型 + 真实语料的五基线受控对照实验尚未运行（需模型凭证与语料采集）
- 人工标注（金标准盲点的 Kappa/Alpha 一致性）尚未产生标注数据

## 评测实验的演进记录（Arbor 会话）

评测管道的建立与两轮修正实验记录在
`.arbor/sessions/foresight-blindspot-001/REPORT.md`（idea tree、
experiment reports、executor prompts 均在对应目录下）：

- 节点 1.1「基线正确化」：修正单 agent 席位选择与最终复判全席遍历
- 节点 2.1「素材多席位化」：7 席各按专业给出盲点答案，gold 关键词 2→7
- 节点 1.2「sanity guard」：变体差异回归保护测试
