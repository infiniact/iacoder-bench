# 计分与不确定性方法

规则原文见 [RULES.md](../RULES.md)；实现见 [scoring/](../scoring/)。本文说明本仓库如何把规则落成计算，以及哪里做了保守选择。

## 输入

一次计分只接受**一个** Harbor / Pier job 目录：`<job>/<task>__<id>/result.json`（iacoder 的 trial 另有
`agent/session.jsonl`）。脚本拒绝多个目录，`./bench` 在一个输出根目录下发现多个 job 时也拒绝。
题名取 `result.json` 的 `task_name`（去掉数据集前缀）。

## DeepSWE

**trial 分类。** 未结束 → 运行中；`VerifierTimeoutError` 等判分错误 → 剔除（不重采样）；
无异常且 reward = 1 → 通过；其余 → 失败。其中 `AgentTimeoutError`、`ContextLengthExceededError` 是规则点名计失败的；
其他异常（如 agent 非零退出、环境构建失败）可能来自模型服务或网络，但 Harbor / Pier 的异常类型不足以区分，
**主分数按失败计**，只在"敏感性"一栏给出"把它们全部剔除"后的 pass@1，作为上界参考，不作成绩。

**pass@1。** 主分数与站点在线榜单（v1.1）一致：pooled = 通过的计分 attempt / 计分 attempt 总数。
论文 §5.3 的宏平均（各题通过比例的平均）作为补充给出；每题次数接近时两者只差零点几个百分点。

**±。** 与站点榜单一致：每题的 trial 按 `started_at` 排序，第 r 次 attempt 组成第 r 轮，sᵣ = 该轮的 pooled 通过率；
SE = 样本标准差(s₁..s_R)/√R；报告 ±1.96·SE。这是"重跑整套题"的波动，不包含题目抽样的不确定性；
R≈4 时区间偏窄（论文 §5.5 已说明）。

**pass@4。** 至少一次通过的题 / 全部题（没有计分 trial 的题算未通过）。

**合规。** 每题已完成次数（计分 + 剔除）≥ 4；题数 = 113；没有运行中的 trial；`timeout_multiplier` = 1.0，
各分项倍率、超时与资源均未覆盖；全 job 同一 agent 与模型。剔除不重采样，所以计分次数可以合法地少于 4。

## Terminal-Bench 4.0

照官方 leaderboard 代码：成功 = reward > 0；出错 trial 记 0，不剔除；accuracy = 成功 / 全部 trial；
SE² = (1/n²)·Σᵢ pᵢ(1−pᵢ)/(kᵢ−1)，报告 ±1.96·SE；pass@2..5 用无偏估计 1 − C(n−c,k)/C(n,k) 按题平均。
合规：66 题、每题 ≥ 5 次、倍率与超时 / 资源未覆盖。

## 成本、token、步数

`scoring/usage.py` 对**计分 trial** 逐个统计，给出均值与中位数：

- iacoder：累加 `session.jsonl` 的 `Usage` 事件（未命中缓存输入、命中缓存输入、输出）；步数 = Usage 事件数（模型调用次数）。
- 其他 agent：`result.json` 的 `agent_result`。
- 成本两列：按每次调用的实际时段计价（`cost_usd`），以及全部按高峰价（`cost_peak_usd`，对应 DeepSWE 在线榜单）。
  单价见 `scoring/pricing/`，带来源与抓取日期。

论文用中位数，在线榜单用均值，两者都报告。不同端点（官方 / 中转）之间的耗时不可比。

## 不做的事

- 不合并多个 job，不为单题取多次中最好的一次，不用重跑结果替换原始 trial。
- 不在计分时修改或补全 trial 数据；数据有缺口就判不合规。
