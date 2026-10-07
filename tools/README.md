# tools/

通用分析工具，用于排查失败、对比两次运行、归因成本与时长差异。

> **内部诊断，不得进入公开成绩。** 公开成绩只用 [`scoring/`](../scoring/)。这些工具的输出可以帮助定位问题，
> 但不得用来拼接多次运行、为单题挑选最好的一次，或替换原始运行的成绩。

全部只用标准库，参数都是 job 目录（`<job-dir>/<task>__<id>/result.json`）。token、步数、时长的口径与
`scoring/usage.py` 相同（共用 `_common.py`）。

| 工具 | 作用 |
|---|---|
| `dump_trials.py` | 一个或多个 job 的逐 trial 数据导出为 JSON |
| `job_progress.py` | 单个 job 的进度、异常分布、调用与 token 概况；可与 `dump_trials.py` 的参考输出在同题上对比 |
| `ab_compare.py` | 两个 job 的整体与同题对比（任意 agent；非 iacoder 回落 `agent_result`） |
| `tool_counts.py` | 指定题的逐 trial 工具调用统计（按工具名），可同时看多个 job |
| `session_signals.py` | 逐 trial 的 session.jsonl 行为信号计数（调用、工具、子 agent、事件类型） |
| `call_speed.py` | 每次模型调用的首 token 等待、流式时长、字符/秒（判断端点快慢） |

```bash
python3 tools/job_progress.py <job-dir> --expected-trials 452
python3 tools/ab_compare.py <job-A> <job-B> --labels v0.7.63 v0.7.64 --per-task
python3 tools/tool_counts.py --job A=<job-A> --job B=<job-B> <task-name>
```

不同模型端点（官方 / 中转）之间单次调用耗时可能相差数倍，绝对时长和分数只在同机、同端点的 A/B 中可比。
