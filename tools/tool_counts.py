#!/usr/bin/env python3
"""一个或多个 job 中指定题的逐 trial 工具调用统计（模型调用数、工具调用数及按工具名分布）。

内部诊断工具，输出不得进入公开成绩。

用法：
    python3 tools/tool_counts.py --job A=<job-dir> --job B=<job-dir> <题名> [<题名> ...]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_trials  # noqa: E402
from session_signals import scan  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--job", action="append", required=True, help="LABEL=<job-dir>，可重复")
    p.add_argument("tasks", nargs="+")
    a = p.parse_args(argv)
    jobs = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) if "=" in s else (s, Path(s)) for s in a.job]
    trials = {label: load_trials(path) for label, path in jobs}
    for task in a.tasks:
        print(f"== {task}")
        for label, ts in trials.items():
            for t in ts:
                s = t.path / "agent" / "session.jsonl"
                if t.task != task or not s.is_file():
                    continue
                r = scan(s)
                top = dict(list(r["tools"].items())[:8])
                print(f"  {label:6} {t.outcome:4} model_calls {r['model_calls']:4}  tool_calls {r['tool_calls']:4}  "
                      f"per_call {r['tools_per_call']:.2f}  subagent_tool_calls {r['subagent_tool_calls']}  tools {top}")


if __name__ == "__main__":
    main()
