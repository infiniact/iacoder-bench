#!/usr/bin/env python3
"""输出与 DeepSWE 公开榜单同列的一行：PASS@1 ±、COST、OUT TOK、STEPS（均值 / 中位数都给）。

在线榜单的 Avg cost / Out tok / Steps 是均值，成本按高峰价计（"Costs use the peak rates"），
所以 COST 列用 cost_peak_usd；按实际时段计价的成本在 JSON 的 cost_time_of_use_usd 中。

不合规的 job 也会输出，但行首带 NON-COMPLIANT 标记，且退出码为 2；这样的行不得对外发布。

用法：
    python3 scoring/leaderboard_row.py <job-dir> --min-trials 4 --expected-tasks 113 \
        --pricing scoring/pricing/deepseek-flash.toml --label "iacoder v0.7.64 + deepseek-flash" [--format md|tsv|json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import score_job  # noqa: E402
from usage import usage_job  # noqa: E402

HEADER = ["AGENT", "PASS@1", "±95%", "PASS@K", "COST peak-rate (mean / median)", "OUT TOK (mean / median)",
          "STEPS (mean / median)", "COMPLIANT"]


def _pair(stat, fmt):
    m, d = stat["mean"], stat["median"]
    if m is None:
        return "n/a"
    return f"{fmt(m)} / {fmt(d)}"


def build_row(score: dict, usage: dict, label: str) -> dict:
    s = usage["summary"]
    return {
        "agent": label,
        "pass_at_1": score["pass_at_1"],
        "ci95_half_width": score["ci95_half_width"],
        "k": score["k"],
        "pass_at_k": score["pass_at_k"],
        "cost_usd": s["cost_peak_usd"],
        "cost_time_of_use_usd": s["cost_usd"],
        "output_tokens": s["output_tokens"],
        "steps": s["steps"],
        "compliant": score["compliant"],
    }


def render(row: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(row, ensure_ascii=False, indent=2)
    p1 = "n/a" if row["pass_at_1"] is None else f"{100 * row['pass_at_1']:.1f}%"
    ci = "n/a" if row["ci95_half_width"] is None else f"±{100 * row['ci95_half_width']:.1f}"
    pk = "n/a" if row["pass_at_k"] is None else f"{100 * row['pass_at_k']:.1f}%"
    cells = [
        row["agent"], p1, ci, pk,
        _pair(row["cost_usd"], lambda v: f"${v:.3f}"),
        _pair(row["output_tokens"], lambda v: f"{v / 1000:.1f}k"),
        _pair(row["steps"], lambda v: f"{v:.0f}"),
        "yes" if row["compliant"] else "NON-COMPLIANT",
    ]
    header = [h.replace("PASS@K", f"PASS@{row['k']}") for h in HEADER]
    if fmt == "tsv":
        return "\t".join(header) + "\n" + "\t".join(cells)
    return ("| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n| "
            + " | ".join(cells) + " |")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job", type=Path)
    p.add_argument("--rules", choices=("deepswe", "tbench4"), default="deepswe")
    p.add_argument("--min-trials", type=int, required=True)
    p.add_argument("--expected-tasks", type=int, default=None)
    p.add_argument("--pricing", type=Path, default=None)
    p.add_argument("--label", default="iacoder")
    p.add_argument("--format", choices=("md", "tsv", "json"), default="md")
    a = p.parse_args(argv)
    score = score_job(a.job, a.min_trials, a.expected_tasks, a.rules)
    usage = usage_job(a.job, a.pricing)
    print(render(build_row(score, usage, a.label), a.format))
    return 0 if score["compliant"] else 2


if __name__ == "__main__":
    sys.exit(main())
