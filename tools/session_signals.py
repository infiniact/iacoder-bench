#!/usr/bin/env python3
"""逐 trial 统计 iacoder session.jsonl 的行为信号，用于归因两次运行的成本 / 时长差异。

每个 trial：chat 槽模型调用数、工具调用数（按工具名）、每次调用的工具数、一次调用多个工具的轮数、
子 agent 内的工具调用数、各事件类型计数（如上下文压缩、收尾送回）。不做任何解读，只计数。

内部诊断工具，输出不得进入公开成绩。

用法：
    python3 tools/session_signals.py <job-dir> [--out signals.json] [--task <题名> ...]
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import iter_events, load_trials  # noqa: E402


def scan(session: Path) -> dict:
    kinds, tools = collections.Counter(), collections.Counter()
    calls = sub_tools = multi = pending = 0
    for d in iter_events(session):
        k = d.get("kind")
        kinds[k] += 1
        if k == "TurnStart":
            multi += pending > 1
            pending = 0
        elif k == "Usage" and d.get("slot", "chat") == "chat":
            calls += 1
        elif k == "ToolCallStart":
            tools[d.get("name") or d.get("tool") or "?"] += 1
            pending += 1
        elif k == "SubagentActivity":
            sub_tools += json.dumps(d).count("ToolCallStart")
    multi += pending > 1
    n = sum(tools.values())
    return {"model_calls": calls, "tool_calls": n, "tools_per_call": round(n / max(1, calls), 2),
            "multi_tool_turns": multi, "subagent_tool_calls": sub_tools,
            "tools": dict(tools.most_common()), "event_kinds": dict(kinds.most_common())}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--task", nargs="*", help="只看这些题")
    a = p.parse_args(argv)
    out = collections.defaultdict(list)
    for t in load_trials(a.job):
        if a.task and t.task not in a.task:
            continue
        s = t.path / "agent" / "session.jsonl"
        if s.is_file():
            out[t.task].append({"trial": t.name, "outcome": t.outcome, **scan(s)})
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n")
        print(f"{len(out)} 题，{sum(map(len, out.values()))} 个 trial → {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
