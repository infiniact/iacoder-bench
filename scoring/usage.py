#!/usr/bin/env python3
"""统计单个 job 目录中每个 trial 的 token、步数、墙钟时长与成本，输出均值与中位数。

站点榜单口径（主）：DeepSWE v1.1 在线榜单的 Avg cost / Out tok / Steps 是全部计分 attempt 的**均值**，
成本按**高峰价**（"Costs use the peak rates"），文本输出第一行按站点显示精度给出这三列。
论文 §6.2 用"每 trial 中位数"，作为补充一并输出。

数据来源：
- iacoder 的 trial：累加 `agent/session.jsonl` 中 `"kind":"Usage"` 事件。
  usage.input_tokens = 未命中缓存的输入，usage.cached_input_tokens = 命中缓存的输入，
  usage.output_tokens = 输出；步数 = Usage 事件数（即模型调用次数）。
  成本按每个事件的时间戳（ts_ms，UTC）分高峰 / 非高峰计价（cost_usd）；另给全部按高峰价计的
  cost_peak_usd——DeepSWE 在线榜单注明 "Costs use the peak rates"，与榜单对比时用它。
- 其他 agent：回落到 result.json 的 agent_result
  （n_input_tokens 含缓存、n_cache_tokens、n_output_tokens、cost_usd、n_agent_steps）。
  cost_usd 缺失时按 trial 开始时间计价。
- 墙钟：result.json 的 agent_execution 起止（agent 时长）与 trial 起止（总时长）。

默认只统计计分 trial（通过 + 失败，不含被剔除的判分错误与仍在运行的）；--all 统计全部已结束 trial。

用法：
    python3 scoring/usage.py <job-dir> --pricing scoring/pricing/deepseek-flash.toml [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import load_trials, parse_time  # noqa: E402

METRICS = ("cost_usd", "cost_peak_usd", "output_tokens", "steps", "input_tokens", "cached_input_tokens",
           "agent_wall_s", "trial_wall_s")


class Pricing:
    """按 UTC 时刻区分高峰 / 非高峰的单价（美元 / 百万 token）。"""

    def __init__(self, data: dict):
        self.data = data
        self.peak = data.get("peak")
        self.off = data.get("off_peak") or data.get("peak")
        hours = data.get("peak_hours") or {}
        self.weekdays = set(hours.get("weekdays", []))
        self.windows = [tuple(w) for w in hours.get("windows", [])]
        # 顶层键；也接受误写进 [peak_hours] 表里的同名键（TOML 中写在表之后的键属于该表）。
        self.off_dates = set(data.get("off_peak_dates", [])) | set(hours.get("off_peak_dates", []))

    @classmethod
    def load(cls, path: Path | None):
        if path is None:
            return None
        data = tomllib.loads(path.read_text())
        if not data.get("off_peak") and not data.get("peak"):
            return None  # 价格未填写（TODO）
        return cls(data)

    def rates(self, when: datetime | None) -> dict:
        if when is None or not self.peak:
            return self.off
        when = when.astimezone(timezone.utc)
        if when.strftime("%Y-%m-%d") in self.off_dates or when.weekday() not in self.weekdays:
            return self.off
        if any(lo <= when.hour < hi for lo, hi in self.windows):
            return self.peak
        return self.off

    def cost(self, when, uncached: int, cached: int, output: int, peak: bool = False) -> float:
        r = (self.peak or self.off) if peak else self.rates(when)
        return (uncached * r["cache_miss_input"] + cached * r["cache_hit_input"]
                + output * r["output"]) / 1e6


def _duration(section: dict | None) -> float | None:
    if not section:
        return None
    a, b = parse_time(section.get("started_at")), parse_time(section.get("finished_at"))
    return (b - a).total_seconds() if a and b else None


def from_session(path: Path, pricing: Pricing | None) -> dict | None:
    """累加 iacoder session.jsonl 的 Usage 事件；文件不存在或没有 Usage 事件时返回 None。"""
    if not path.is_file():
        return None
    uncached = cached = output = steps = 0
    cost = cost_peak = 0.0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"Usage"' not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("kind") != "Usage":
                continue
            u = ev.get("usage") or {}
            i, c, o = (int(u.get("input_tokens") or 0), int(u.get("cached_input_tokens") or 0),
                       int(u.get("output_tokens") or 0))
            uncached += i
            cached += c
            output += o
            steps += 1
            if pricing:
                ts = ev.get("ts_ms")
                when = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
                cost += pricing.cost(when, i, c, o)
                cost_peak += pricing.cost(when, i, c, o, peak=True)
    if steps == 0:
        return None
    return {"source": "session.jsonl", "input_tokens": uncached, "cached_input_tokens": cached,
            "output_tokens": output, "steps": steps, "cost_usd": cost if pricing else None,
            "cost_peak_usd": cost_peak if pricing else None}


def from_result(data: dict, pricing: Pricing | None) -> dict:
    """回落：result.json 的 agent_result。"""
    ar = data.get("agent_result") or {}
    total_in, cache, out = ar.get("n_input_tokens"), ar.get("n_cache_tokens"), ar.get("n_output_tokens")
    uncached = (total_in - (cache or 0)) if total_in is not None else None
    steps = ar.get("n_agent_steps") or data.get("n_agent_steps")
    cost = ar.get("cost_usd")
    cost_peak = None
    if pricing and total_in is not None:
        when = parse_time(data.get("started_at"))
        if cost is None:
            cost = pricing.cost(when, uncached or 0, cache or 0, out or 0)
        cost_peak = pricing.cost(when, uncached or 0, cache or 0, out or 0, peak=True)
    return {"source": "result.json", "input_tokens": uncached, "cached_input_tokens": cache,
            "output_tokens": out, "steps": steps, "cost_usd": cost, "cost_peak_usd": cost_peak}


def trial_usage(trial, pricing: Pricing | None) -> dict:
    row = from_session(trial.path / "agent" / "session.jsonl", pricing) or from_result(trial.data, pricing)
    row.update({"trial": trial.name, "task": trial.task, "outcome": trial.outcome,
                "agent_wall_s": _duration(trial.data.get("agent_execution")),
                "trial_wall_s": _duration(trial.data)})
    return row


def summarize(rows: list[dict]) -> dict:
    out = {"n_trials": len(rows)}
    for m in METRICS:
        vals = [r[m] for r in rows if r.get(m) is not None]
        out[m] = {"n": len(vals),
                  "mean": statistics.fmean(vals) if vals else None,
                  "median": statistics.median(vals) if vals else None}
    return out


def usage_job(job: Path, pricing_path: Path | None = None, include_all: bool = False) -> dict:
    pricing = Pricing.load(pricing_path)
    keep = ("pass", "fail", "excluded") if include_all else ("pass", "fail")
    rows = [trial_usage(t, pricing) for t in load_trials(job) if t.outcome in keep]
    return {"job": str(job), "pricing": str(pricing_path) if pricing else None,
            "scope": "all-finished" if include_all else "scored", "summary": summarize(rows),
            "trials": rows}


def fmt_cost(v: float | None) -> str:
    """站点 Avg cost 列：$x.xx；不足 1 美分时保留 1 位有效数字。"""
    if v is None:
        return "—"
    return f"${v:.2f}" if v >= 0.01 else f"${v:.1g}"


def fmt_tokens(v: float | None) -> str:
    """站点 Out tok 列（前端 kt()）：≥1M → x.xM；≥10k → 整数 k；≥1k → 一位小数 k；否则整数。"""
    if v is None:
        return "—"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.{0 if v >= 1e4 else 1}f}k"
    return f"{v:.0f}"


def fmt_steps(v: float | None) -> str:
    """站点 Steps 列：Math.round。"""
    return "—" if v is None else str(math.floor(v + 0.5))


def _fmt(v, kind):
    if v is None:
        return "n/a"
    if kind.startswith("cost"):
        return f"${v:.4f}"
    if kind.endswith("_s"):
        return f"{v / 60:.1f} min"
    return f"{v:,.0f}"


def format_text(r: dict) -> str:
    s = r["summary"]
    site = (f"【站点榜单口径（主）】均值、成本按高峰价：Avg cost {fmt_cost(s['cost_peak_usd']['mean'])}  "
            f"Out tok {fmt_tokens(s['output_tokens']['mean'])}  Steps {fmt_steps(s['steps']['mean'])}")
    lines = [site, "【补充】下表含中位数（论文 §6.2）与按实际时段计价的 cost_usd。",
             f"job: {r['job']}", f"统计范围: {r['scope']}，trial 数 {s['n_trials']}，计价: {r['pricing'] or '无'}",
             f"{'指标':<22}{'均值':>16}{'中位数':>16}{'有值 trial':>12}"]
    for m in METRICS:
        lines.append(f"{m:<22}{_fmt(s[m]['mean'], m):>16}{_fmt(s[m]['median'], m):>16}{s[m]['n']:>12}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job", type=Path)
    p.add_argument("--pricing", type=Path, default=None, help="scoring/pricing/<model>.toml")
    p.add_argument("--all", action="store_true", help="包含被剔除的 trial")
    p.add_argument("--json", action="store_true")
    p.add_argument("--per-trial", action="store_true", help="JSON 中保留逐 trial 明细（默认只保留汇总）")
    a = p.parse_args(argv)
    if not a.job.is_dir():
        print(f"不是目录：{a.job}", file=sys.stderr)
        return 2
    r = usage_job(a.job, a.pricing, a.all)
    if a.json:
        if not a.per_trial:
            r.pop("trials")
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(format_text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
