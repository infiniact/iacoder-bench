"""测试用：在临时目录里按 Harbor / Pier 的真实格式构造 job 目录（result.json + agent/session.jsonl）。"""
from __future__ import annotations

import json
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)  # 周六，全天非高峰


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def make_trial(job: Path, task: str, *, start_min: float = 0, minutes: float = 30,
               reward: float | None = 1, exception: str | None = None, finished: bool = True,
               usage: list[dict] | None = None, agent_result: dict | None = None,
               config: dict | None = None, model: str = "deepseek-flash", rng=random) -> Path:
    suffix = "".join(rng.choice(string.ascii_letters) for _ in range(7))
    d = job / f"{task}__{suffix}"
    (d / "agent").mkdir(parents=True)
    started = BASE + timedelta(minutes=start_min)
    ended = started + timedelta(minutes=minutes)
    cfg = {
        "timeout_multiplier": 1.0, "agent_timeout_multiplier": None,
        "verifier_timeout_multiplier": None, "agent_setup_timeout_multiplier": None,
        "environment_build_timeout_multiplier": None,
        "agent": {"name": None, "import_path": "iacoder_pier:IacoderPierAgent", "model_name": model,
                  "override_timeout_sec": None, "override_setup_timeout_sec": None, "max_timeout_sec": None},
        "environment": {"type": "docker", "override_cpus": None, "override_memory_mb": None,
                        "override_storage_mb": None, "override_gpus": None, "delete": True},
        "verifier": {"override_timeout_sec": None, "max_timeout_sec": None, "disable": False},
    }
    for section, values in (config or {}).items():
        if isinstance(values, dict):
            cfg[section].update(values)
        else:
            cfg[section] = values
    data = {
        "task_name": f"datacurve/{task}",
        "trial_name": d.name,
        "config": cfg,
        "agent_info": {"name": "iacoder", "version": "iacoder 0.7.64"},
        "agent_result": agent_result or {"n_input_tokens": None, "n_cache_tokens": None,
                                         "n_output_tokens": None, "cost_usd": None, "n_agent_steps": None},
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": None if exception is None else {
            "exception_type": exception, "exception_message": "x", "exception_traceback": "",
            "occurred_at": iso(ended)},
        "started_at": iso(started),
        "finished_at": iso(ended) if finished else None,
        "agent_execution": {"started_at": iso(started + timedelta(minutes=1)),
                            "finished_at": iso(ended - timedelta(minutes=1))},
    }
    (d / "result.json").write_text(json.dumps(data))
    if usage is not None:
        lines = [{"kind": "SessionStart", "max_turns": 0, "model": model,
                  "ts_ms": int(started.timestamp() * 1000)}]
        for i, u in enumerate(usage):
            ts = u.pop("ts", started + timedelta(minutes=1 + i))
            lines.append({"kind": "Usage", "slot": "chat", "duration_ms": 1000,
                          "ts_ms": int(ts.timestamp() * 1000),
                          "usage": {"cache_creation_input_tokens": 0, "reasoning_tokens": 0, **u}})
            lines.append({"kind": "TurnStart", "turn": i + 1, "ts_ms": int(ts.timestamp() * 1000)})
        (d / "agent" / "session.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return d


def write_job_result(job: Path) -> None:
    """job 根目录自带的 result.json / config.json（应被计分忽略）。"""
    (job / "result.json").write_text(json.dumps({"id": "job", "n_total_trials": 0}))
    (job / "config.json").write_text(json.dumps({"n_attempts": 4}))
