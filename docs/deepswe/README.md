# DeepSWE

**English** | [简体中文](README.zh-CN.md) · [Back to Bench](../../README.md)

[DeepSWE](https://deepswe.datacurve.ai/) (Datacurve) evaluates software-engineering agents on 113 tasks run with
Pier. This page covers IACoder's DeepSWE results and how to reproduce them. IACoder is the AI agent engine developed by
INFINIACT CO., LTD. ("INFINIACT").

## Rules in brief

Full text, quotes and sources: [RULES.md](../../RULES.md) (fetched 2026-10-07).

| Item | Rule |
|---|---|
| Tasks | 113 (dataset commit `0b9fabbb`) |
| Attempts | about 4 per task; one job, no merging or best-of |
| Time limit | as set in each `task.toml`, multiplier 1.0; no step or cost cap |
| Pass@1 (site leaderboard v1.1) | pooled: passed scored attempts / scored attempts |
| ± | 95% run-to-run: attempts ordered by start time form rounds s1..sR; 1.96·std(s1..sR)/√R |
| Avg cost / Out tok / Steps | means over every scored attempt; cost at peak rates |
| Exclusions | provider, grader and transient network errors are excluded and not resampled; agent timeouts and context exhaustion count as failures |
| Submission | contact Datacurve ([Run DeepSWE](https://deepswe.datacurve.ai/run)) |

This repository also disables web tools for every run and, when a provider or network error cannot be told apart
from other failures, counts it as a failure (the score can only be understated).

## Result

**IACoder v0.7.64 + deepseek-flash [max]** — DeepSeek official endpoint, 113 tasks × 4 attempts, one job, COMPLIANT.

| Model | Pass@1 | ± | Pass@4 | Avg cost | Out tok | Steps |
|---|---|---|---|---|---|---|
| IACoder v0.7.64 + deepseek-flash [max] | **71%** | **±2%** | 91.2% | $0.454 | 187k | 163 |

| Detail | Value |
|---|---|
| Pass@1, pooled (primary) | 70.82% (318 / 449 scored attempts) |
| 95% half-width | 2.30 points; rounds 70.54% / 70.80% / 68.14% / 73.87% |
| Pass@1, macro-average (paper §5.3, supplementary) | 70.65% |
| Attempts | 452: 318 passed, 131 failed, 3 excluded (verifier timeouts, not resampled) |
| Medians per attempt | cost $0.421 (peak) / $0.210 (billed), 178k output tokens, 154 steps, 25.3 min |

Status: ready for submission to Datacurve; **not yet on the official leaderboard**.
Files: [result](../../results/deepswe-v1.1-iacoder-v0.7.64-deepseek-flash-2026-10-07.md) ·
[run archive](../../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/) ·
[submission summary](../deepswe-submission.md) · [binary](../../bin/v0.7.64/) · [harness](../../harness/).

## Reproduce

```bash
./bench preflight --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench run       --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench status <run-id> --watch 60
./bench score  <run-id> --leaderboard-row
```

`./bench run` only emits a compliant command (`-k 4`, `--timeout-multiplier 1.0`, no retries, web tools off).
Scoring: [scoring/README.md](../../scoring/README.md); method: [docs/methodology.md](../methodology.md).
