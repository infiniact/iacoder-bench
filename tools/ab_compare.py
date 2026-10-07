#!/usr/bin/env python3
"""两个 job 的同题对比：整体与"两边都有计分 trial 的题"上的通过数、agent 时长、调用数、token、缓存命中。

适用于任意 agent：iacoder 读 session.jsonl 的 Usage 事件，其他 agent 回落 result.json 的 agent_result。
同一题有多个 trial 时按 trial 计；--per-task 打印逐题的通过数 / 分钟中位数 / 调用数。

内部诊断工具（A/B、定位差异），输出不得进入公开成绩；公开成绩只用 scoring/。

用法：
    python3 tools/ab_compare.py <job-A> <job-B> [--labels A B] [--per-task] [--task-order order.txt]
"""
import argparse
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import trial_rows  # noqa: E402
from job_progress import summary  # noqa: E402


def by_task(rows):
    out = collections.defaultdict(list)
    for x in rows:
        if x["outcome"] in ("pass", "fail"):
            out[x["task"]].append(x)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job_a", type=Path)
    p.add_argument("job_b", type=Path)
    p.add_argument("--labels", nargs=2, default=["A", "B"])
    p.add_argument("--per-task", action="store_true")
    p.add_argument("--task-order", type=Path, help="每行一个题名，只看前面这些题并按此顺序输出")
    a = p.parse_args(argv)
    A, B = by_task(trial_rows(a.job_a)), by_task(trial_rows(a.job_b))
    both = sorted(set(A) & set(B))
    if a.task_order:
        order = [line.strip() for line in a.task_order.read_text().splitlines() if line.strip()]
        both = [t for t in order if t in A and t in B]
    la, lb = a.labels
    flat = lambda d, ts: [x for t in ts for x in d[t]]  # noqa: E731
    print(f"## 全部：{la} {len(A)} 题 / {lb} {len(B)} 题")
    print(f"{la:>10}", summary(flat(A, A)))
    print(f"{lb:>10}", summary(flat(B, B)))
    print(f"## 两边都有计分 trial 的题：{len(both)}")
    print(f"{la:>10}", summary(flat(A, both)))
    print(f"{lb:>10}", summary(flat(B, both)))
    rate = lambda xs: sum(x["outcome"] == "pass" for x in xs) / len(xs)  # noqa: E731
    print(f"只有 {la} 通过率更高的题: {sum(rate(A[t]) > rate(B[t]) for t in both)}；"
          f"只有 {lb} 更高: {sum(rate(B[t]) > rate(A[t]) for t in both)}")
    if a.per_task:
        for t in both:
            cells = []
            for lab, d in ((la, A), (lb, B)):
                xs = d[t]
                mins = [x["agent_min"] for x in xs if x["agent_min"] is not None]
                cells.append(f"{lab} {sum(x['outcome'] == 'pass' for x in xs)}/{len(xs)} "
                             f"{statistics.median(mins) if mins else float('nan'):.0f}min "
                             f"{statistics.median([x['calls'] or 0 for x in xs]):.0f}calls")
            print(f"  {t}: " + " | ".join(cells))


if __name__ == "__main__":
    main()
