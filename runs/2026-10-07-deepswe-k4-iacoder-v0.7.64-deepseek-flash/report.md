# 2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash

- **Status:** completed; COMPLIANT (`scoring/score.py` exit 0); ready for submission to Datacurve (not yet sent) — **not on the official DeepSWE leaderboard**.
- **Purpose:** a DeepSWE v1.1 run that follows the official protocol (≈4 rollouts per task, single job, no step or cost cap,
  task timeouts untouched, no retries), to submit iacoder as an agent configuration.
- **Agent / model:** iacoder v0.7.64 ([`bin/v0.7.64/`](../../bin/v0.7.64/)) + `deepseek-flash` on the DeepSeek official API, reasoning effort `max`.
- **Rules:** [RULES.md](../../RULES.md) (fetched 2026-10-07). Scoring: [`scoring/`](../../scoring/) at this commit.

## Method

| Item | Value | Evidence |
|---|---|---|
| Dataset | `datacurve/deep-swe-1-1`, 113 tasks, `datacurve-ai/deep-swe@0b9fabbb`; `task.toml` unchanged (agent 10800 s, verifier 1800 s, 2 CPU / 8 GB) | [manifest.json](manifest.json) `dataset` |
| Runner | Pier `datacurve-pier 0.3.1`, Docker backend, one job | `runner`, `job_config_rule_fields` |
| Rollouts | `--n-attempts 4` → 452 trials | `trials_per_task` |
| Timeout | `--timeout-multiplier 1.0`; no agent / verifier / setup / build overrides; no resource overrides | `job_config_rule_fields` |
| Retries | `retry.max_retries = 0` (no trial was re-run or replaced) | `job_config_rule_fields` |
| Step / cost cap | none (`max_turns = 0`; the config has no `max_turns`) | `max_turns`, [configs/deepseek-flash.toml](../../configs/deepseek-flash.toml) |
| Web | iacoder's web tools are disabled (`--no-web` hard-coded in the harness); the task container's network is Pier's per-agent allowlist, which only admits the model endpoint | [harness/](../../harness/) |
| Prompt template | `mini-swe-agent-custom` (built into the v0.7.64 binary) | `agent_env` |
| Concurrency | 32 trials on one 64 vCPU / 121 GiB host | `concurrency`, `host` |
| Wall clock | 2026-10-06T18:54:57.444716Z → 2026-10-07T04:24:52.101258Z | `started_at`, `finished_at` |

Before this job, the same host ran smoke tests and two single-pass runs through a relay endpoint; those were stopped
after ~25 trials each when we switched to the official endpoint and the official k=4 protocol. They are not used
anywhere. This job is the only DeepSWE run of v0.7.64 on the official endpoint, and it is reported whole.

## Results

Scored with `python3 scoring/score.py <job> --min-trials 4 --expected-tasks 113`, `scoring/usage.py`,
`scoring/leaderboard_row.py` (full output in [score.json](score.json)).

Leaderboard-style row (same columns as the DeepSWE leaderboard; cost at DeepSeek **peak** rates, as the leaderboard does):

| AGENT | PASS@1 | ±95% | PASS@4 | COST peak-rate (mean / median) | OUT TOK (mean / median) | STEPS (mean / median) | COMPLIANT |
|---|---|---|---|---|---|---|---|
| iacoder v0.7.64 + deepseek-flash | 71% (70.82%) | ±2% (±2.30) | 91.2% | $0.454 / $0.421 | 187.0k / 177.8k | 163 / 154 | yes |

| Metric | Value |
|---|---|
| pass@1, DeepSWE site leaderboard v1.1 (pooled, 318/449 scored attempts) — **primary** | **70.82% ± 2.30** (95% run-to-run; rounds 70.54 / 70.80 / 68.14 / 73.87); site display **71% ±2%** |
| pass@1, macro-average over 113 tasks (paper §5.3) — supplementary | 70.65% |
| pass@1, pooled over scored rollouts (live leaderboard v1.1 `unit`) | 70.8% |
| Per-round scores s1..s4 (rollouts ordered by start time, §5.5) | 69.9%, 71.7%, 67.9%, 73.9% |
| pass@4 (§5.3) | 91.2% (103/113 tasks) |
| Trials | 452 total: 318 pass, 131 fail, 3 excluded (grading error) |

Efficiency, per scored trial (paper §6.2 uses medians; the live leaderboard columns are means):

| Metric | Median | Mean |
|---|---|---|
| Cost, DeepSeek peak rates (leaderboard basis) | $0.421 | $0.454 |
| Cost, time-of-use (all off-peak: holiday) | $0.210 | $0.227 |
| Output tokens | 177,821 | 186,969 |
| Steps (model calls) | 154 | 163 |
| Uncached input tokens | 135,550 | 150,176 |
| Cached input tokens | 26,603,520 | 30,787,767 |
| Agent wall-clock | 25.3 min | 28.9 min |
| Trial wall-clock (incl. setup and verifier) | 26.5 min | 30.4 min |

Cost is computed from the token counts the API reported for each call (`agent/session.jsonl` `Usage` events) and the
[DeepSeek price list](../../scoring/pricing/deepseek-flash.toml). "Time-of-use" prices each call at the rate in force
when it was made: this whole run fell inside China's 2026 National Day holiday (1–7 Oct, Beijing time), which DeepSeek
bills entirely at off-peak rates, so time-of-use = off-peak = half of the peak-rate column.
Steps = model calls (one per `Usage` event).

## Exceptions and exclusions

| Trial | Exception | Verifier reward | Treatment |
|---|---|---|---|
| `effect-sse-httpapi-streaming__5duotmw` | `AgentTimeoutError` | 1 | failure (agent timeout) |
| `effect-sse-httpapi-streaming__hJY6We6` | `AgentTimeoutError` | 0 | failure (agent timeout) |
| `koota-query-predicates__wG4qcg7` | `VerifierTimeoutError` | — | excluded (grading error), not resampled |
| `pwntools-tube-multiplexing__YHYEbpt` | `VerifierTimeoutError` | — | excluded (grading error), not resampled |
| `pwntools-tube-multiplexing__hKLHBiJ` | `VerifierTimeoutError` | — | excluded (grading error), not resampled |

- **Excluded (paper §5.6):** only verifier / grading errors (`VerifierTimeoutError`). They are removed from numerator and
  denominator and **not resampled**: `pwntools-tube-multiplexing` has 2 scored rollouts and `koota-query-predicates` 3; every
  other task has 4 (449 scored rollouts in total).
- **Counted as failures:** `AgentTimeoutError` (the rules score agent timeouts as failures). One of the two such trials
  left a patch that the verifier graded reward = 1; it is still counted as a failure.
- **Sensitivity:** the rules also allow excluding model-provider and transient network errors. We cannot tell those apart
  reliably from Pier's exception types, so the primary score counts every non-grading exception as a failure.
  In this run there were no such other exceptions (only `AgentTimeoutError` and `VerifierTimeoutError` occurred), so the
  sensitivity value equals the primary score.

## Reference: same model under mini-swe-agent (not compliant, context only)

Earlier, with the same model (`deepseek-flash`) and the same 113 tasks, an internal run of the standard
`mini-swe-agent` scaffold solved **74/113 (65.5%)**. That run had **one rollout per task**, so it is not a DeepSWE
pass@1 and does not satisfy the k≈4 rule; its artifacts are not in this repository. It is mentioned only for
context — it is not a controlled comparison (different date, one sample per task). A compliant
mini-swe-agent baseline under the same settings has **not** been run.

## Known upstream grading issues

Several grading defects in DeepSWE v1.1 at the pinned commit are reported upstream and still open
([datacurve-ai/deep-swe#105](https://github.com/datacurve-ai/deep-swe/issues/105),
[#107](https://github.com/datacurve-ai/deep-swe/issues/107),
[#108](https://github.com/datacurve-ai/deep-swe/issues/108),
[#109](https://github.com/datacurve-ai/deep-swe/issues/109)).
We grade with the unmodified official verifier, as the leaderboard does, and do not adjust any score.
Our rollouts on the two tasks with task-specific reports:

- `claude-code-by-agents-recursive-delegation` (datacurve-ai/deep-swe#107, #108): 2/4 of our rollouts passed; 1 of the passing rollouts show the vitest out-of-memory marker from #107 in `test-stdout.txt`.
- `csstree-shorthand-expansion-compression` (datacurve-ai/deep-swe#109): 3/4 of our rollouts passed.

If the one passing `claude-code-by-agents-recursive-delegation` rollout with the #107 out-of-memory marker were
re-graded as a failure, macro pass@1 would drop by 0.25/113 ≈ 0.2 points (to ≈ 70.4%); we report the official
grade unchanged.

## Limitations

- **Different agent from the leaderboard.** All official leaderboard rows use `mini-swe-agent`; this row is iacoder.
  Agent and model effects are confounded; the row is meant to be listed as an agent configuration.
- **Infrastructure.** Run on our own Docker host (32 concurrent trials, 2 CPU / 8 GB per task as `task.toml` specifies),
  not on Modal as the official runs are. Wall-clock times depend on host and API latency.
- **CI width.** The ± is the run-to-run interval from R = 4 rounds (paper §5.5); it does not include task-sampling
  uncertainty and is narrow with R = 4.
- **Prompt template.** `mini-swe-agent-custom` is iacoder's own template modelled on mini-swe-agent's instructions; it is
  part of the configuration under test, not the official mini-swe-agent prompt.
- **Raw trajectories** are not in this repository (≈9 GB). See [artifacts.json](artifacts.json) for the checksum
  manifest and availability.
