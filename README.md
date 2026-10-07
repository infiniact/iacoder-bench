# Bench

**English** | [简体中文](README.zh-CN.md)

**Bench** is the evaluation repository for **IACoder**, the AI agent engine developed by
**INFINIACT CO., LTD.** (hereinafter "**INFINIACT**").
It currently supports **DeepSWE** and **Terminal-Bench**, and is designed to take on more benchmarks over time.

Everything needed to check a published number lives here: the IACoder binaries, a snapshot of the evaluation harness,
the scoring code, reproduction steps and the archive of every run.

## Benchmarks

| Benchmark | Status | Page |
|---|---|---|
| DeepSWE (Datacurve) | **1 publishable result** | [DeepSWE](docs/deepswe/README.md) |
| Terminal-Bench 4.0 | Internal runs only (community submissions are closed) | [Terminal-Bench](docs/terminal-bench/README.md) |

## Published results

| Benchmark | Agent + model | Pass@1 | ± (95%) | Pass@4 | Avg cost | Out tok | Steps | Status |
|---|---|---|---|---|---|---|---|---|
| DeepSWE v1.1 | IACoder v0.7.64 + deepseek-flash [max] | **71%** (70.82%) | ±2% (2.30) | 91.2% | $0.454 | 187k | 163 | Ready for submission to Datacurve; not yet on the official leaderboard |

Scored the way the DeepSWE site leaderboard scores (pooled Pass@1, 95% run-to-run interval, per-attempt means,
cost at peak rates). Details: [DeepSWE page](docs/deepswe/README.md) ·
[result file](results/deepswe-v1.1-iacoder-v0.7.64-deepseek-flash-2026-10-07.md) ·
[run archive](runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/).

Only results that follow the organizers' rules are published here — see [RULES.md](RULES.md) and
[results/README.md](results/README.md). Every evaluation runs with web tools disabled; runs are never merged,
cherry-picked or resampled.

> **Withdrawn (2026-10-05):** the DeepSWE figure "97/117 = 82.9%" listed earlier was accumulated over several reruns,
> was not pass@1, and ran with web search enabled. See [docs/withdrawn-2026-10-05.md](docs/withdrawn-2026-10-05.md).

## Quick start

```bash
./bench preflight --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench run       --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench status <run-id> --watch 60
./bench score  <run-id> --leaderboard-row
```

For Terminal-Bench use `--benchmark tbench`. Host preparation, keys, binary URL and images:
[docs/quickstart.md](docs/quickstart.md).

## Layout

```
bench/
├── bench, bench.py  # single entry point: preflight / run / status / score
├── RULES.md         # organizer rules, this repository's extra rules, change log
├── bin/<version>/   # IACoder release binaries with SHA256SUMS
├── harness/         # snapshot of the IACoder evaluation adapters
├── configs/         # IACoder evaluation config templates (no secrets)
├── scoring/         # the only scoring entry point (score.py, usage.py, leaderboard_row.py) + tests
├── runs/            # per-run archives (manifest / score / report / artifacts)
├── results/         # published, rule-compliant results only
├── reproduce/       # generic environment setup and tools (image cache, version locks)
├── tools/           # analysis tools (internal diagnostics, never part of a published score)
├── tests/           # tests for bench itself
└── docs/            # per-benchmark pages, quick start, methodology, withdrawal record
```

Adding a benchmark: a page under `docs/<benchmark>/` (English and Chinese), its rules in RULES.md, a `--rules`
mode in `scoring/score.py`, and a harness snapshot.

## Tests

```bash
python3 -m unittest discover -s scoring/tests && python3 -m unittest discover -s tests
```

© 2026 INFINIACT CO., LTD. ("INFINIACT"). IACoder is developed by INFINIACT.
