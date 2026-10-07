"""tools/ 共用：读取 job 目录的 trial 与 session.jsonl。复用 scoring/ 的解析，口径一致。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scoring"))
from score import load_trials  # noqa: E402,F401
from usage import trial_usage  # noqa: E402,F401


def iter_events(session: Path):
    """逐行产出 session.jsonl 中的事件 dict；坏行跳过。"""
    if not session.is_file():
        return
    with session.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def trial_rows(job: Path, pricing: Path | None = None) -> list[dict]:
    """job 中每个已结束 trial 的一行：题名、结果、异常、agent 分钟、token、调用数、成本。"""
    from usage import Pricing
    price = Pricing.load(pricing) if pricing else None
    rows = []
    for t in load_trials(job):
        if t.outcome == "running":
            continue
        u = trial_usage(t, price)
        rows.append({
            "task": t.task, "trial": t.name, "outcome": t.outcome, "reward": t.reward, "exception": t.error,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "agent_min": u["agent_wall_s"] / 60 if u["agent_wall_s"] is not None else None,
            "calls": u.get("steps"), "uncached_in": u.get("input_tokens"),
            "cached_in": u.get("cached_input_tokens"), "output": u.get("output_tokens"),
            "cost_usd": u.get("cost_usd"), "source": u.get("source"),
        })
    return rows
