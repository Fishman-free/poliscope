# ARBOR_CONTRACT — foresight-blindspot-001

## Target
- 仓库：poliscope-mvp（本目录），当前分支 `worktree-poliscope-mvp`
- 说明：Poliscope 科研智能体（EpistemoBrain），ForesightBlindspot 评测体系已存在

## Metric
- 主 metric：**Blindspot F1**（盲点发现的 Recall/Precision 调和平均），**maximize**
- eval 命令：`python scripts/arbor_eval.py --variant <variant> [--run-name <node_id>]`
  - 输出 `score: <f1>` 行 + JSON 明细（blindspot_recall/precision/f1、citation_entailment、evidence_independence、dissent_preservation、causal_overclaim、events、model_cost_usd）
- 辅助指标全部记录于 metrics.json（不进入主 score）

## Baseline anchor
- 原始测量（基线语义 bug 修正前）：F1 = 0.8（5 变体全同，**不可信**）
- INIT 已确认根因：`_seats_for(SINGLE_AGENT)` 选中字母序首个席位（adversarial_falsifier），且 `FinalRejudgmentHandler` 遍历全部 `Seat`
- **修正后重新测量作为真实 baseline**（预期 SINGLE_AGENT 显著下降，变体差异显现）

## Ambition
- 建立可区分 5 基线的可信评测基线；在预算内推进消融/改进实验，为系统论文提供真实实验数字

## Scope preference
- effect-leaning（先建立可信测量，再做消融对比；novelty 实验视预算）

## Dev/test discipline
- B_dev = demo case（当前唯一可运行案例）
- **无独立 B_test**：时间切片语料未采集；merge 判定退化为「dev 验证 + 人工确认」，不宣称有测试拆分

## Hard constraints
- 不改动评测打分函数来刷分（scoring.py 的规则复用生产逻辑，是度量本身）
- 数据/评测管道受保护；solution 类修改走 executor worktree
- 不 commit、不 push（除非用户明确要求）
- 不跑真实模型调用（需凭证与用户确认）

## Edit surface
- 允许：`packages/evaluation/`、`scripts/arbor_eval.py`、实验 worktree 内的 `packages/council/`、`packages/epistemo/` 等解决方案代码
- 保护：`packages/evaluation/scoring.py` 的度量语义（修复 bug 除外，须说明）

## Budget
- 本轮：smoke 起步，目标 2–3 轮实验（基线正确化 → 消融/区分度 → 视结果）
- real 运行（真实模型 + 语料）须单独确认

## Interaction mode
- review：每个实验 idea 在 executor 派遣前由用户确认；合并/继续决策向用户汇报

## Unresolved caveats
- demo case 的 scripted gateway 只对 adversary 席位返回盲点答案——变体区分度依赖素材；素材升级是候选实验
- score_causal_overclaim 在 demo 上为 None（无 Fork 产生因果主张），因果过度推断指标在该案例上不可测
