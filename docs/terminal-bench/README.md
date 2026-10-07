# Terminal-Bench

**English** | [简体中文](README.zh-CN.md) · [Back to Bench](../../README.md)

[Terminal-Bench](https://www.tbench.ai/) evaluates agents on hard tasks in a terminal, run with Harbor. The current
version is **4.0** (66 tasks). This page covers IACoder's Terminal-Bench status and how to run it. IACoder is the
AI agent engine developed by INFINIACT CO., LTD. ("INFINIACT").

## Rules in brief (4.0)

Full text, quotes and sources: [RULES.md](../../RULES.md) (fetched 2026-10-07; 2.x rules kept there as history).

| Item | Rule |
|---|---|
| Dataset | `terminal-bench/terminal-bench@4.0.0`, all 66 tasks (3 need an H100 GPU); no subsets |
| Attempts | at least 5 per task |
| Limits | 8-hour agent timeout; `timeout_multiplier` unset or 1.0; no timeout or resource overrides |
| Score | accuracy = successful trials / all trials (success: reward > 0), ± 1.96·SE (per-task formula) |
| Errors | errored trials count as 0 and are not excluded |
| Submission | upload jobs to Harbor Hub (publicly readable) with ATIF trajectories, then open a leaderboard PR |
| Status | **community submissions are currently closed**; only maintainer-run results are added |

## Status

No published Terminal-Bench result yet. IACoder runs Terminal-Bench 4.0 internally; internal runs are not
published scores.

What is ready for a future submission:
- Jobs reference the dataset by registry (`terminal-bench/terminal-bench@4.0.0`) and name the agent `iacoder`.
- The harness writes an ATIF trajectory (`agent/trajectory.json`) for every trial, aligned with Harbor's built-in
  Claude Code / Codex adapters.
- The 63 CPU tasks and the 3 GPU tasks run as two jobs with the same binary, model and reasoning effort
  (Harbor's docker environment cannot start GPU tasks, and one such task stops the whole job).

## Run

```bash
./bench preflight --benchmark tbench --version <version> --model deepseek-flash --concurrency 12
./bench run       --benchmark tbench --version <version> --model deepseek-flash --concurrency 12
./bench status <run-id> --watch 60
./bench score  <run-id>
```

Host preparation and image caching: [docs/quickstart.md](../quickstart.md) and [reproduce/](../../reproduce/).
