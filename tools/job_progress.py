#!/usr/bin/env python3
"""单个 job 的进度与资源消耗概况；可选与一份参考数据（dump_trials.py 的输出）在相同题上对比。

内部诊断工具，输出不得进入公开成绩；公开成绩只用 scoring/score.py。

用法：
    python3 tools/job_progress.py <job-dir> [--expected-trials 452] [--reference ref.json]
"""
import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import trial_rows  # noqa: E402


def summary(rows):
    n = len(rows) or 1
    mins = [x["agent_min"] for x in rows if x["agent_min"] is not None]
    tot = lambda k: sum(x[k] or 0 for x in rows)  # noqa: E731
    return {
        "trials": len(rows), "pass": sum(x["outcome"] == "pass" for x in rows),
        "agent_min_median": round(statistics.median(mins), 1) if mins else None,
        "calls_per_trial": round(tot("calls") / n),
        "uncached_in_M": round(tot("uncached_in") / n / 1e6, 3),
        "cached_in_M": round(tot("cached_in") / n / 1e6, 2),
        "output_M": round(tot("output") / n / 1e6, 3),
        "cache_hit_pct": round(100 * tot("cached_in") / max(1, tot("cached_in") + tot("uncached_in")), 2),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job", type=Path)
    p.add_argument("--expected-trials", type=int, help="预期 trial 总数（如 113 题 × 4 = 452）")
    p.add_argument("--reference", type=Path, help="dump_trials.py 输出的参考 JSON（取其第一个 job）")
    a = p.parse_args(argv)
    rows = trial_rows(a.job)
    scored = [x for x in rows if x["outcome"] in ("pass", "fail")]
    exc = collections.Counter(x["exception"] for x in rows if x["exception"])
    print(f"已结束 {len(rows)}" + (f" / {a.expected_trials}" if a.expected_trials else "")
          + f"，计分 {len(scored)}，异常 {dict(exc)}")
    print("全部", json.dumps(summary(scored), ensure_ascii=False))
    if a.reference:
        ref = next(iter(json.loads(a.reference.read_text()).values()))
        ref_tasks = {x["task"] for x in ref if x["outcome"] in ("pass", "fail")}
        both = [x for x in scored if x["task"] in ref_tasks]
        tasks = {x["task"] for x in both}
        ref_rows = [x for x in ref if x["task"] in tasks and x["outcome"] in ("pass", "fail")]
        print(f"相同题 {len(tasks)}")
        print("  本 job", json.dumps(summary(both), ensure_ascii=False))
        print("  参考  ", json.dumps(summary(ref_rows), ensure_ascii=False))


if __name__ == "__main__":
    main()
