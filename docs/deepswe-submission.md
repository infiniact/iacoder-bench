# DeepSWE submission: iacoder v0.7.64 + deepseek-flash

One-page summary for Datacurve. Everything below links to files in this repository; after merge the same paths are at
`https://github.com/infiniact/iacoder-bench/blob/main/<path>` (directories: `https://github.com/infiniact/iacoder-bench/tree/main/<path>`).

**Status:** ready for submission to Datacurve — not yet on the official DeepSWE leaderboard.

## Result

| AGENT | PASS@1 | ±95% | PASS@4 | COST peak-rate (mean / median) | OUT TOK (mean / median) | STEPS (mean / median) | COMPLIANT |
|---|---|---|---|---|---|---|---|
| iacoder v0.7.64 + deepseek-flash | 71% (70.82%) | ±2% (±2.30) | 91.2% | $0.454 / $0.421 | 187.0k / 177.8k | 163 / 154 | yes |

| | |
|---|---|
| pass@1 (DeepSWE site leaderboard v1.1: pooled over scored attempts) — **primary** | **70.82% ± 2.30** (95% run-to-run, R = 4 rounds: 70.54 / 70.80 / 68.14 / 73.87); site display **71% ±2%** |
| pass@1 (macro-average, paper §5.3) — supplementary | 70.65% |
| pass@1 (pooled, leaderboard v1.1 `unit`) | 70.8% |
| Rounds s1..s4 | 69.9%, 71.7%, 67.9%, 73.9% |
| pass@4 | 91.2% |
| Median per trial (paper §6.2) | cost $0.421 at peak rates ($0.210 as billed, off-peak), 177.8k output tokens, 154 steps, 25.3 min agent time |
| Mean per trial (leaderboard columns; cost at peak rates) | cost $0.454, 187.0k output tokens, 163 steps |
| Trials | 452 = 318 pass + 131 fail + 3 excluded (grading error, not resampled) |

Full report: [runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/report.md](../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/report.md)
— <https://github.com/infiniact/iacoder-bench/blob/main/runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/report.md>

## Configuration

| | |
|---|---|
| Agent | **iacoder v0.7.64** (INFINIACT's agent engine, Rust). Prebuilt Linux musl binary from the official release: [bin/v0.7.64/](../bin/v0.7.64/), checksums in [bin/v0.7.64/SHA256SUMS](../bin/v0.7.64/SHA256SUMS); unpacked x86_64 binary sha256 `6dcf86e2…c065` ([bin/README.md](../bin/README.md)) |
| Pier adapter | [harness/deep-swe/](../harness/deep-swe/) + [harness/common/](../harness/common/), byte-identical snapshot of `infiniact/iacoder@v0.7.64` `eval/` ([harness/README.md](../harness/README.md)); import path `iacoder_pier:IacoderPierAgent` |
| Model | `deepseek-flash`, DeepSeek official API, reasoning effort `max`, 1M context ([configs/deepseek-flash.toml](../configs/deepseek-flash.toml)) |
| Prompt template | `mini-swe-agent-custom` (see "Differences from mini-swe-agent") |
| Runner | Pier `datacurve-pier 0.3.1`, Docker, one job, 32 concurrent trials, 64 vCPU / 121 GiB host |
| Dataset | `datacurve/deep-swe-1-1`, 113 tasks, `datacurve-ai/deep-swe@0b9fabbb`, `task.toml` unchanged |

## Compliance checklist

Rules as quoted in [RULES.md](../RULES.md) (paper arXiv:2607.07946 §5.2–5.6, §6.2; Run page; live leaderboard v1.1).

| Rule | This run | Evidence |
|---|---|---|
| ≈4 rollouts per task, ≈452 per configuration (§5.4) | `--n-attempts 4`, 452 trials, all finished | [manifest.json](../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/manifest.json) `job_config_rule_fields.n_attempts`; [score.json](../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/score.json) `score.compliant` |
| All 113 tasks | 113 | score.json `score.n_tasks` |
| One configuration, one run | single Pier job; one agent, one model in every trial; nothing merged, re-run, or best-of | score.json; [scoring/score.py](../scoring/score.py) refuses multiple jobs |
| Time limit from `task.toml`, no step / cost cap (§5.2) | multiplier 1.0, no timeout or resource overrides, `max_turns = 0` | manifest.json `job_config_rule_fields`, `max_turns` |
| Timeouts and context exhaustion are failures (§5.2, §5.6) | `AgentTimeoutError` counted as failure (even one with reward = 1) | report.md "Exceptions and exclusions" |
| Provider / grading / network errors excluded, not resampled (§5.6) | **only** grading errors excluded (3); `max_retries = 0`; other errors conservatively counted as failures, sensitivity reported | report.md; manifest.json `max_retries` |
| pass@1 macro-average; ±1.96·SE over rounds ordered by start time (§5.3, §5.5) | both macro and pooled reported | [scoring/score.py](../scoring/score.py), [docs/methodology.md](methodology.md) |
| pass@4 (§5.3) | reported | score.json `score.pass_at_k` |
| Efficiency: median (§6.2) and leaderboard means at peak rates | both reported | [scoring/usage.py](../scoring/usage.py), [scoring/pricing/deepseek-flash.toml](../scoring/pricing/deepseek-flash.toml) |
| Pier > 0.3.0 (separate verifier environment) | 0.3.1 | manifest.json `runner` |
| Network | agent web tools disabled (`--no-web`); task network = Pier per-agent allowlist (model endpoint only) | [harness/](../harness/), [RULES.md](../RULES.md) "本仓库的附加规定" |

## Differences from the standard mini-swe-agent configuration

All leaderboard rows use `mini-swe-agent`. This entry is a different agent and should be listed as an agent configuration.

- **Agent loop:** iacoder's engine runs the loop instead of mini-swe-agent's Python loop. The engine retries transient
  model-API errors inside a trial; no trial is ever re-run or replaced.
- **Prompt:** mini-swe-agent 2.4.6 `mini.yaml` ([commit 04d809ce](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/config/mini.yaml)),
  verbatim except the workflow section: batch independent commands as parallel tool calls; optional subagents for
  independent work; before editing, turn the requirements into verification cases (both sides of changed branches),
  rerun them after the change, keep existing tests' identities; check coverage with the `verify` tool before submitting.
  Same submit sentinel, format-error reply, and 60 s command timeout as mini-swe-agent.
- **Tools:** `bash` with mini-swe-agent's exact schema, plus `verify` (the agent lists each stated requirement with the
  check and observed result; uncovered requirements are sent back before the submission is accepted).
- **Time budget:** the agent is told its wall-clock budget (the task's agent timeout); commands still running at the
  limit are reported.
- **No web access** and no step cap, as above.

## Reproduce

Setup (Docker, Pier pinned by [reproduce/pier-constraints.txt](../reproduce/pier-constraints.txt), dataset at `0b9fabbb`,
image cache, binary served on the Docker bridge, `DEEPSEEK_API_KEY` in the environment):
[reproduce/host-setup.md](../reproduce/host-setup.md), [docs/quickstart.md](quickstart.md).

```bash
export IACODER_BINARY_URL=http://<docker-bridge-ip>:<port>/iacoder-x86_64-unknown-linux-musl.tar.gz
ARGS="--benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 32 --tasks-dir <deep-swe>/tasks \
      --agent-env IACODER_PROMPT_TEMPLATE=mini-swe-agent-custom --agent-env IACODER_REASONING_EFFORT=max"
./bench preflight $ARGS
./bench run $ARGS
./bench status <run-id> --watch 60
./bench score <run-id> --leaderboard-row
```

Re-scoring our job directory (once you have it, see below):

```bash
python3 scoring/score.py <job-dir> --min-trials 4 --expected-tasks 113
python3 scoring/leaderboard_row.py <job-dir> --min-trials 4 --expected-tasks 113 \
    --pricing scoring/pricing/deepseek-flash.toml --label "iacoder v0.7.64 + deepseek-flash"
```

## Raw trajectories

The full Pier job directory (every trial's `result.json`, agent session and transcript, verifier output; ≈9 GB) is kept
by us and is not in this repository. [artifacts.json](../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/artifacts.json)
records the file count, size and a sha256 manifest digest so a copy can be verified. We will provide it in whatever
form Datacurve prefers.

## Links

| | Repository path | URL after merge |
|---|---|---|
| This page | `docs/deepswe-submission.md` | https://github.com/infiniact/iacoder-bench/blob/main/docs/deepswe-submission.md |
| Run archive | `runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/` | https://github.com/infiniact/iacoder-bench/tree/main/runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash |
| Report | `…/report.md` | https://github.com/infiniact/iacoder-bench/blob/main/runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/report.md |
| Score | `…/score.json` | https://github.com/infiniact/iacoder-bench/blob/main/runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/score.json |
| Binary | `bin/v0.7.64/`, `bin/v0.7.64/SHA256SUMS` | https://github.com/infiniact/iacoder-bench/tree/main/bin/v0.7.64 |
| Adapter | `harness/` | https://github.com/infiniact/iacoder-bench/tree/main/harness |
| Rules | `RULES.md` | https://github.com/infiniact/iacoder-bench/blob/main/RULES.md |
| Scoring | `scoring/` | https://github.com/infiniact/iacoder-bench/tree/main/scoring |
