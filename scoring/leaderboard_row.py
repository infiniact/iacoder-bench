#!/usr/bin/env python3
"""输出与 DeepSWE v1.1 在线榜单同列、同显示精度的一行：Pass@1 ±、Avg cost、Out tok、Steps。

站点榜单口径（主）：
- Pass@1 = pooled（通过的计分 attempt / 计分 attempt），整数百分比；± = 95% run-to-run，round(100·half)%。
- Avg cost / Out tok / Steps = 全部计分 attempt 的**均值**；成本按**高峰价**（"Costs use the peak rates"）。
- 显示精度照站点前端：成本 $x.xx；token ≥10k 取整 k、1k–10k 一位小数 k、≥1M 一位小数 M；步数取整。
  例：`71% ±3%  $0.45  187k  163`。
补充（不进主行）：论文宏平均 pass@1、各项中位数、按实际时段计价的成本，在 md 输出的"补充"行与 JSON 中。

不合规的 job 也会输出，但带 NON-COMPLIANT 标记，且退出码为 2；这样的行不得对外发布。

用法：
    python3 scoring/leaderboard_row.py <job-dir> --min-trials 4 --expected-tasks 113 \
        --pricing scoring/pricing/deepseek-flash.toml --label "iacoder v0.7.64 + deepseek-flash" [--format md|line|tsv|json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import score_job, site_ci, site_pct  # noqa: E402
from usage import fmt_cost, fmt_steps, fmt_tokens, usage_job  # noqa: E402

HEADER = ["Model", "Pass@1", "±", "Avg cost", "Out tok", "Steps", "PASS@K", "COMPLIANT"]


def build_row(score: dict, usage: dict, label: str) -> dict:
    s = usage["summary"]
    half = score["ci95_half_width"]
    row = {
        "agent": label,
        "method": score.get("primary_method"),
        "pass_at_1": score["pass_at_1"],
        "ci95_half_width": half,
        "k": score["k"],
        "pass_at_k": score["pass_at_k"],
        "cost_usd": s["cost_peak_usd"],
        "cost_time_of_use_usd": s["cost_usd"],
        "output_tokens": s["output_tokens"],
        "steps": s["steps"],
        "compliant": score["compliant"],
        "supplementary": {"pass_at_1_macro_paper": score.get("pass_at_1_macro"),
                          "rounds": score.get("rounds")},
    }
    row["site_display"] = {
        "pass_at_1": site_pct(row["pass_at_1"]),
        "ci95": site_ci(half),
        "avg_cost": fmt_cost(s["cost_peak_usd"]["mean"]),
        "out_tok": fmt_tokens(s["output_tokens"]["mean"]),
        "steps": fmt_steps(s["steps"]["mean"]),
    }
    d = row["site_display"]
    row["site_line"] = f"{d['pass_at_1']} {d['ci95']}  {d['avg_cost']}  {d['out_tok']}  {d['steps']}"
    return row


def supplementary_text(row: dict) -> str:
    sup = row.get("supplementary") or {}
    macro = sup.get("pass_at_1_macro_paper")
    med = lambda key, f: "n/a" if row[key]["median"] is None else f(row[key]["median"])  # noqa: E731
    tou = row["cost_time_of_use_usd"]["mean"]
    parts = []
    if macro is not None:
        parts.append(f"论文宏平均 pass@1 {100 * macro:.1f}%")
    parts.append("中位 " + " / ".join([med("cost_usd", lambda v: f"${v:.3f}"),
                                      med("output_tokens", lambda v: f"{v / 1000:.1f}k"),
                                      med("steps", lambda v: f"{v:.0f}")]))
    if tou is not None:
        parts.append(f"按实际时段计价的成本均值 ${tou:.3f}")
    return "补充（不作主分数）：" + "；".join(parts)


def render(row: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(row, ensure_ascii=False, indent=2)
    if fmt == "line":
        return row["site_line"] + ("" if row["compliant"] else "  NON-COMPLIANT")
    d = row["site_display"]
    pk = "n/a" if row["pass_at_k"] is None else f"{100 * row['pass_at_k']:.1f}%"
    cells = [row["agent"], d["pass_at_1"], d["ci95"], d["avg_cost"], d["out_tok"], d["steps"], pk,
             "yes" if row["compliant"] else "NON-COMPLIANT"]
    header = [h.replace("PASS@K", f"PASS@{row['k']}") for h in HEADER]
    if fmt == "tsv":
        return "\t".join(header) + "\n" + "\t".join(cells)
    return ("站点榜单口径（主）：Pass@1 pooled、±95% run-to-run、均值、成本按高峰价\n\n"
            "| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n| "
            + " | ".join(cells) + " |\n\n" + supplementary_text(row))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job", type=Path)
    p.add_argument("--rules", choices=("deepswe", "tbench4"), default="deepswe")
    p.add_argument("--min-trials", type=int, required=True)
    p.add_argument("--expected-tasks", type=int, default=None)
    p.add_argument("--pricing", type=Path, default=None)
    p.add_argument("--label", default="iacoder")
    p.add_argument("--format", choices=("md", "line", "tsv", "json"), default="md")
    a = p.parse_args(argv)
    score = score_job(a.job, a.min_trials, a.expected_tasks, a.rules)
    usage = usage_job(a.job, a.pricing)
    print(render(build_row(score, usage, a.label), a.format))
    return 0 if score["compliant"] else 2


if __name__ == "__main__":
    sys.exit(main())
