# scoring/

本仓库**唯一**可用于对外成绩的计分代码。只用 Python 标准库（3.11+）。规则依据见 [RULES.md](../RULES.md)，
方法说明见 [docs/methodology.md](../docs/methodology.md)。

| 文件 | 作用 |
|---|---|
| `score.py` | 单个 job 目录的 pass@1（宏平均）、按轮 s1..sR 与 ±1.96·SE、pass@k、剔除、合规检查；不合规退出码 2 |
| `usage.py` | 每 trial 的输出 token、步数、墙钟、成本，给均值与中位数 |
| `leaderboard_row.py` | 输出与 DeepSWE 榜单同列的一行：PASS@1 ±、COST、OUT TOK、STEPS |
| `pricing/*.toml` | 模型单价（美元 / 百万 token），带来源 URL 与抓取日期 |
| `tests/` | 用临时目录构造真实格式 job 目录的单测 |

> 改名说明：旧入口 `scripts/score_pass_at_1.py` 已迁为 `scoring/score.py`，参数保持兼容
> （`<job-dir> --min-trials N --expected-tasks M`），新增 `--json` / `--out`。

## 用法

```bash
# DeepSWE：每题 4 次，113 题
python3 scoring/score.py <job-dir> --min-trials 4 --expected-tasks 113
python3 scoring/score.py <job-dir> --min-trials 4 --expected-tasks 113 --json --out runs/<id>/score.json
python3 scoring/usage.py <job-dir> --pricing scoring/pricing/deepseek-flash.toml
python3 scoring/leaderboard_row.py <job-dir> --min-trials 4 --expected-tasks 113 \
    --pricing scoring/pricing/deepseek-flash.toml --label "iacoder v0.7.64 + deepseek-flash"

# Terminal-Bench 4.0：官方口径（pooled accuracy、reward>0 即成功、出错计 0、不剔除），66 题、每题 ≥5 次
python3 scoring/score.py <job-dir> --rules tbench4 --min-trials 5 --expected-tasks 66
```

一般直接用 `./bench score`，它会按 benchmark 自动带上口径、次数和题数。

## 计分口径：DeepSWE（`--rules deepswe`，默认）

- **计分 trial**：已结束、且不是判分错误的 trial。通过 = 无异常且 `verifier_result.rewards.reward == 1`。
- **剔除**：只剔除 `VerifierTimeoutError`、`VerifierOutputParseError`、`RewardFileNotFoundError`、
  `RewardFileEmptyError`、`VerifierRuntimeError`。剔除不重采样。
- **保守计失败**：模型服务错误、瞬态网络错误按规则可以剔除，但从 Harbor / Pier 的异常类型无法可靠区分，
  主分数一律计失败。`AgentTimeoutError`、`ContextLengthExceededError` 以外的其他异常记为"可疑基础设施异常"，
  单独输出"若全部剔除"的敏感性 pass@1，只作参考，不作主分数。
- **主分数是宏平均**（论文 §5.3）。同时输出 pooled（总通过 / 总计分）：在线榜单 v1.1 的说明写的是
  "attempt pass rate over scored rollout attempts"，即 pooled。每题次数接近时两者只差零点几个百分点。
- **按轮**：每题计分 trial 按 `started_at` 排序，第 r 次组成第 r 轮；某题计分次数不足 r 时，第 r 轮只在有数据的题上平均。
- **pass@k**：k = `--min-trials`；分母是全部题数（没有计分 trial 的题算未解出）。
- **合规**：单个 job 目录；每题已完成次数（计分 + 剔除）≥ `--min-trials`；题数等于 `--expected-tasks`；
  没有仍在运行的 trial；`timeout_multiplier == 1.0`，各分项倍率与超时、CPU、内存、存储、GPU 均未覆盖；
  全 job 同一 agent 与模型。

## 计分口径：Terminal-Bench 4.0（`--rules tbench4`）

照官方 leaderboard 代码（`leaderboard/src/leaderboard/core/metrics.py`）实现：

- 成功 = `reward > 0`；出错的 trial 记 0，**不剔除**任何 trial（包括判分错误）。
- accuracy = 成功 trial / 全部 trial；SE² = (1/n²)·Σᵢ pᵢ(1−pᵢ)/(kᵢ−1)；报告 ±1.96·SE。
- pass@2..5：每题无偏估计 1 − C(n−c,k)/C(n,k) 后按题平均。
- 合规：66 题、每题 ≥ 5 次、倍率与超时 / 资源未覆盖。

## usage 口径

- iacoder trial：累加 `agent/session.jsonl` 的 `Usage` 事件（`input_tokens` = 未命中缓存输入、
  `cached_input_tokens`、`output_tokens`）；步数 = Usage 事件数（模型调用次数）；成本按每个事件的 UTC 时间分高峰 / 非高峰计价。
- 其他 agent：`result.json` 的 `agent_result`（`n_input_tokens` 含缓存、`n_cache_tokens`、`n_output_tokens`、`cost_usd`、`n_agent_steps`）。
- 成本给两列：`cost_usd` 按每次调用的实际时段（高峰 / 非高峰）计价；`cost_peak_usd` 全部按高峰价计。
  DeepSWE 在线榜单注明 "Costs use the peak rates"，`leaderboard_row.py` 的 COST 列用后者。
- 在线榜单的 Avg cost / Out tok / Steps 是均值；论文 §6.2 用中位数。两者都输出。
- 默认只统计计分 trial；`--all` 包含被剔除的。
- 价格文件未填写（如 `glm-5.3.toml`）时不输出成本。

## 测试

```bash
python3 -m unittest discover -s scoring/tests
# 或
uv run --with pytest pytest scoring/tests
```
