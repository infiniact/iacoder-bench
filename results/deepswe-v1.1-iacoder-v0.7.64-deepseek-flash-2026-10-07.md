# DeepSWE v1.1 — iacoder v0.7.64 + deepseek-flash (2026-10-07)

- **Status:** ready for submission to Datacurve (not yet sent) — **not yet on the official DeepSWE leaderboard**.
- **Run:** [runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/](../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/)
  (single Pier job, 113 tasks × 4 rollouts, `scoring/score.py` COMPLIANT).
- **Rules:** [RULES.md](../RULES.md), fetched 2026-10-07 (paper arXiv:2607.07946 §5.2–5.6, §6.2; live leaderboard v1.1).
- **Submission page:** [docs/deepswe-submission.md](../docs/deepswe-submission.md).

| AGENT | PASS@1 | ±95% | PASS@4 | COST peak-rate (mean / median) | OUT TOK (mean / median) | STEPS (mean / median) | COMPLIANT |
|---|---|---|---|---|---|---|---|
| iacoder v0.7.64 + deepseek-flash | 71% (70.82%) | ±2% (±2.30) | 91.2% | $0.454 / $0.421 | 187.0k / 177.8k | 163 / 154 | yes |

pass@1 is the macro-average over 113 tasks (paper §5.3), ± is 1.96·SE over 4 rounds ordered by start time (§5.5);
pooled pass@1 (leaderboard v1.1 `unit`) = 70.8%. COST / OUT TOK / STEPS are mean / median per scored trial;
cost at DeepSeek peak rates, as the leaderboard does. iacoder is a different agent from the leaderboard's
mini-swe-agent — see the submission page for the differences.
