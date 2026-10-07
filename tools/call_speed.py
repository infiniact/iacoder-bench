#!/usr/bin/env python3
"""从 iacoder session.jsonl 统计每次模型调用的速度：首 token 等待、流式时长、流式字符数、字符/秒。

每个 TurnStart 视为一次调用开始；之后的 ReasoningDelta / AssistantTextDelta 为流式输出。
用于判断端点快慢（不同端点之间的绝对分数不可比，只能同机同端点 A/B）。

内部诊断工具，输出不得进入公开成绩。

用法：
    python3 tools/call_speed.py <job-dir> [<job-dir> ...]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import iter_events  # noqa: E402

DELTAS = {"ReasoningDelta", "AssistantTextDelta", "TextDelta"}


def scan(session: Path, acc: dict):
    state = {"start": None, "first": None, "last": None, "chars": 0}

    def close():
        s, f, l = state["start"], state["first"], state["last"]  # noqa: E741
        if s is not None and f is not None and l is not None:
            acc["wait"].append((f - s) / 1000)
            acc["stream"].append((l - f) / 1000)
            acc["total"].append((l - s) / 1000)
            acc["chars"].append(state["chars"])
            if l > f:
                acc["rate"].append(state["chars"] / ((l - f) / 1000))

    for d in iter_events(session):
        k = d.get("kind")
        if k == "TurnStart":
            close()
            state.update(start=d.get("ts_ms"), first=None, last=None, chars=0)
        elif k in DELTAS and state["start"] is not None and "ts_ms" in d:
            state["first"] = state["first"] or d["ts_ms"]
            state["last"] = d["ts_ms"]
            state["chars"] += d.get("len", 0)
    close()


def q(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(p * len(v)))], 1) if v else None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("jobs", nargs="+", type=Path)
    a = p.parse_args(argv)
    files = [f for j in a.jobs for f in sorted(j.glob("*/agent/session.jsonl"))]
    acc = {k: [] for k in ("wait", "stream", "total", "chars", "rate")}
    for f in files:
        scan(f, acc)
    print(json.dumps({
        "files": len(files), "calls": len(acc["total"]),
        "first_token_wait_s": {"p50": q(acc["wait"], .5), "p90": q(acc["wait"], .9)},
        "stream_s": {"p50": q(acc["stream"], .5), "p90": q(acc["stream"], .9)},
        "call_total_s": {"p50": q(acc["total"], .5), "p90": q(acc["total"], .9)},
        "streamed_chars": {"p50": q(acc["chars"], .5), "p90": q(acc["chars"], .9)},
        "chars_per_s": {"p50": q(acc["rate"], .5)},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
