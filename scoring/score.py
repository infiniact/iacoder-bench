#!/usr/bin/env python3
"""对**单个** Harbor / Pier job 目录按评测组织规则计分（本仓库唯一的计分入口）。

规则依据见仓库根目录 RULES.md。两套口径用 --rules 选择：

--rules deepswe（默认）：
- pass@1 = 各题通过比例（通过次数 / 计分次数）的宏平均，每题权重相同（论文 §5.3）。
  同时输出 pooled（总通过 / 总计分，在线榜单 v1.1 的口径），两者通常只差零点几个百分点。
- 不确定性：把每题的计分 trial 按开始时间排序，第 r 次组成第 r 轮，得到 s1..sR；
  SE = 样本标准差(s1..sR) / sqrt(R)，报告 pass@1 ± 1.96·SE。
- pass@k：至少一次通过的题占全部题数的比例（k 取 --min-trials，DeepSWE 为 pass@4）。
- 剔除（从分子分母同时去掉、不重采样）：只剔除能明确识别的判分错误。
  模型服务错误、瞬态网络错误按规则也可剔除，但 Harbor / Pier 的异常类型无法可靠区分它们，
  因此**主分数保守地计为失败**，另外单列一个"若剔除全部可疑基础设施异常"的敏感性数值，不作主分数。
- agent 超时、上下文耗尽、agent 非零退出等一律计失败；有异常的 trial 即使 reward=1 也计失败。
- 合规检查：只接受一个 job 目录；每题已完成次数（计分 + 剔除）>= --min-trials；
  题数 == --expected-tasks；没有仍在运行的 trial；timeout_multiplier 为 1.0 且未覆盖超时与资源；
  全部 trial 使用同一 agent 与模型。不合规时以退出码 2 结束。

--rules tbench4（Terminal-Bench 4.0 官方 leaderboard 代码的口径）：
- 成功 = reward > 0；出错的 trial 记 0，**不剔除任何 trial**。
- accuracy = 成功 trial / 全部 trial（pooled）；SE^2 = (1/n^2)·Σ_i p_i(1-p_i)/(k_i-1)，报告 ±1.96·SE。
- pass@k（k=2..5）用无偏估计 1 - C(n-c,k)/C(n,k) 按题平均。
- 合规：66 题全覆盖、每题 >= 5 次、超时与资源未覆盖。

用法：
    python3 scoring/score.py <job-dir> --min-trials 4 --expected-tasks 113          # DeepSWE
    python3 scoring/score.py <job-dir> --rules tbench4 --min-trials 5 --expected-tasks 66   # Terminal-Bench 4.0
    python3 scoring/score.py <job-dir> --min-trials 4 --expected-tasks 113 --json    # 机器可读输出

只用标准库。可作为模块导入：load_trials()、score_job()。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 判分侧错误：评测组织规则明确允许剔除。
GRADING_ERRORS = frozenset({
    "VerifierTimeoutError",
    "VerifierOutputParseError",
    "RewardFileNotFoundError",
    "RewardFileEmptyError",
    "VerifierRuntimeError",
})
# 规则明确计失败、不可剔除的 agent 侧结果。
AGENT_FAILURES = frozenset({
    "AgentTimeoutError",
    "ContextLengthExceededError",
})
TRIAL_DIR = re.compile(r"^(?P<task>.+?)__[A-Za-z0-9]{6,8}$")

# result.json config 中不得被覆盖的字段（为 None 表示未覆盖）。
_OVERRIDE_FIELDS = {
    "agent": ("override_timeout_sec", "override_setup_timeout_sec", "max_timeout_sec"),
    "environment": ("override_cpus", "override_memory_mb", "override_storage_mb", "override_gpus"),
    "verifier": ("override_timeout_sec", "max_timeout_sec"),
}
_MULTIPLIER_FIELDS = (
    "agent_timeout_multiplier",
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
)


def parse_time(value):
    """解析 result.json 中的 ISO 时间（可能带 Z）；无法解析返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Trial:
    task: str
    name: str
    path: Path
    outcome: str  # pass | fail | excluded | running
    error: str | None
    reward: float | None
    started_at: datetime | None
    suspect_infra: bool = False
    data: dict = field(default_factory=dict, repr=False)


def task_name(trial_dir: Path, data: dict) -> str | None:
    """题名优先取 result.json 的 task_name（去掉数据集前缀），否则取目录名 `<task>__<id>` 的前半段。"""
    name = data.get("task_name")
    if name:
        return str(name).rsplit("/", 1)[-1]
    match = TRIAL_DIR.match(trial_dir.name)
    return match["task"] if match else None


RULES = ("deepswe", "tbench4")


def classify(data: dict, rules: str = "deepswe") -> tuple[str, str | None, float | None, bool]:
    """返回 (outcome, 异常类型, reward, 是否可疑基础设施异常)。"""
    if not data.get("finished_at"):
        return "running", None, None, False
    error = (data.get("exception_info") or {}).get("exception_type")
    rewards = (data.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    if rules == "tbench4":
        # 官方 metrics.is_success：reward > 0 即成功；出错 trial 没有 reward → 0；不剔除。
        ok = reward is not None and float(reward) > 0
        return ("pass" if ok else "fail"), error, reward, False
    if error in GRADING_ERRORS:
        return "excluded", error, reward, False
    if error is None and reward is not None and float(reward) == 1.0:
        return "pass", None, reward, False
    # 其余全部计失败。除规则点名计失败的 agent 侧结果外，带异常的失败可能来自
    # 模型服务或网络，无法可靠区分，标为"可疑"供敏感性分析。
    suspect = error is not None and error not in AGENT_FAILURES
    return "fail", error, reward, suspect


def load_trials(job: Path, rules: str = "deepswe") -> list[Trial]:
    """读取 job 目录下每个 trial 子目录的 result.json。"""
    trials = []
    for result in sorted(job.glob("*/result.json")):
        trial_dir = result.parent
        if not TRIAL_DIR.match(trial_dir.name):
            continue
        try:
            data = json.loads(result.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        task = task_name(trial_dir, data)
        if task is None:
            continue
        outcome, error, reward, suspect = classify(data, rules)
        trials.append(Trial(task, trial_dir.name, trial_dir, outcome, error, reward,
                            parse_time(data.get("started_at")), suspect, data))
    return trials


def _macro(per_task_scored: dict[str, list[bool]]) -> float | None:
    fractions = [sum(v) / len(v) for v in per_task_scored.values() if v]
    return sum(fractions) / len(fractions) if fractions else None


def round_scores(per_task_scored: dict[str, list[bool]]) -> list[float]:
    """第 r 轮分数 s_r = 拥有第 r 次计分 trial 的各题在该次的通过率（trial 已按开始时间排序）。"""
    rounds = max((len(v) for v in per_task_scored.values()), default=0)
    scores = []
    for r in range(rounds):
        hits = [v[r] for v in per_task_scored.values() if len(v) > r]
        if hits:
            scores.append(sum(hits) / len(hits))
    return scores


def config_problems(trials: list[Trial]) -> list[str]:
    """检查每个 trial 的 config：倍率 1.0、未覆盖超时与资源、agent 与模型一致。"""
    problems, identities = [], set()
    for t in trials:
        cfg = t.data.get("config") or {}
        if not cfg:
            continue
        mult = cfg.get("timeout_multiplier")
        if mult is not None and float(mult) != 1.0:
            problems.append(f"{t.name}: timeout_multiplier={mult}")
        for key in _MULTIPLIER_FIELDS:
            if cfg.get(key) not in (None, 1, 1.0):
                problems.append(f"{t.name}: {key}={cfg.get(key)}")
        for section, keys in _OVERRIDE_FIELDS.items():
            sec = cfg.get(section) or {}
            for key in keys:
                if sec.get(key) is not None:
                    problems.append(f"{t.name}: {section}.{key}={sec.get(key)}")
        agent = cfg.get("agent") or {}
        identities.add((agent.get("import_path") or agent.get("name"), agent.get("model_name")))
    if len(identities) > 1:
        problems.append(f"job 内混有多个 agent/模型：{sorted(map(str, identities))}")
    return problems


def tbench_se(per_task: dict[str, list[bool]]) -> float | None:
    """TB4 官方：SE^2 = (1/n^2)·Σ p_i(1-p_i)/(k_i-1)，k_i<2 的题跳过。"""
    n = len(per_task)
    if not n:
        return None
    var = 0.0
    for v in per_task.values():
        k = len(v)
        if k < 2:
            continue
        p = sum(v) / k
        var += p * (1 - p) / (k - 1)
    return math.sqrt(var / (n * n))


def unbiased_pass_at_k(per_task: dict[str, list[bool]], k: int) -> float | None:
    """按题平均的无偏 pass@k：1 - C(n-c,k)/C(n,k)；n<k 的题跳过（与 TB4 官方代码一致）。"""
    vals = []
    for v in per_task.values():
        n, c = len(v), sum(v)
        if n < k:
            continue
        vals.append(1.0 if n - c < k else 1 - math.comb(n - c, k) / math.comb(n, k))
    return sum(vals) / len(vals) if vals else None


def score_job(job: Path, min_trials: int, expected_tasks: int | None = None,
              rules: str = "deepswe") -> dict:
    """计算一个 job 目录的全部指标与合规结论，返回可 JSON 序列化的 dict。"""
    if rules not in RULES:
        raise ValueError(f"unknown rules {rules!r}")
    trials = load_trials(job, rules)
    tasks: dict[str, list[Trial]] = {}
    for t in trials:
        tasks.setdefault(t.task, []).append(t)

    far = datetime.max.replace(tzinfo=None)

    def key(t: Trial):
        return (t.started_at.replace(tzinfo=None) if t.started_at else far, t.name)

    per_task_scored: dict[str, list[bool]] = {}
    per_task_sensitivity: dict[str, list[bool]] = {}
    counts = {"pass": 0, "fail": 0, "excluded": 0, "running": 0}
    errors: dict[str, int] = {}
    suspect = 0
    reward_one_with_error = 0
    per_task_rows = []
    short, unscored = [], []
    for task, ts in sorted(tasks.items()):
        ts.sort(key=key)
        c = {"pass": 0, "fail": 0, "excluded": 0, "running": 0}
        for t in ts:
            c[t.outcome] += 1
            counts[t.outcome] += 1
            if t.error:
                errors[t.error] = errors.get(t.error, 0) + 1
            if t.suspect_infra:
                suspect += 1
            if t.error and t.reward is not None and float(t.reward) == 1.0:
                reward_one_with_error += 1
        scored = [t.outcome == "pass" for t in ts if t.outcome in ("pass", "fail")]
        per_task_scored[task] = scored
        per_task_sensitivity[task] = [t.outcome == "pass" for t in ts
                                      if t.outcome in ("pass", "fail") and not t.suspect_infra]
        attempts = c["pass"] + c["fail"] + c["excluded"]
        if c["running"]:
            short.append(f"{task}（{c['running']} 个仍在运行）")
        elif attempts < min_trials:
            short.append(f"{task}（已完成 {attempts} 次）")
        if not scored:
            unscored.append(task)
        per_task_rows.append({"task": task, **c, "scored": len(scored),
                              "pass_fraction": (sum(scored) / len(scored)) if scored else None})

    macro = _macro(per_task_scored)
    n_scored = sum(len(v) for v in per_task_scored.values())
    pooled = sum(sum(v) for v in per_task_scored.values()) / n_scored if n_scored else None
    rounds = round_scores(per_task_scored)
    n_tasks = len(tasks)
    if rules == "tbench4":
        pass1 = pooled
        se = tbench_se(per_task_scored) if per_task_scored else None
        se_method = "tbench4: SE^2=(1/n^2)·Σ p_i(1-p_i)/(k_i-1)"
        pass_k = unbiased_pass_at_k(per_task_scored, min_trials)
        extra_k = {f"pass_at_{k}": unbiased_pass_at_k(per_task_scored, k) for k in (2, 3, 4, 5)}
    else:
        pass1 = macro
        se = statistics.stdev(rounds) / math.sqrt(len(rounds)) if len(rounds) >= 2 else None
        se_method = "deepswe: std(s1..sR)/sqrt(R)，按开始时间分轮"
        solved = sum(1 for v in per_task_scored.values() if any(v))
        pass_k = solved / n_tasks if n_tasks else None
        extra_k = {}
    sens = _macro(per_task_sensitivity)

    problems = []
    if not trials:
        problems.append("没有找到 trial（参数应为单个 job 目录，其下是 <task>__<id>/result.json）")
    if short:
        problems.append(f"{len(short)} 题未达到 {min_trials} 次已完成 trial 或仍在运行")
    if unscored:
        problems.append(f"{len(unscored)} 题没有任何计分 trial")
    if expected_tasks is not None and n_tasks != expected_tasks:
        problems.append(f"题数 {n_tasks}，应为 {expected_tasks}")
    cfg_problems = config_problems(trials)
    if cfg_problems:
        problems.append(f"{len(cfg_problems)} 处超时/资源覆盖或 agent 不一致")

    return {
        "job": str(job),
        "rules": rules,
        "min_trials": min_trials,
        "expected_tasks": expected_tasks,
        "tasks": n_tasks,
        "trials": counts,
        "pass_at_1": pass1,
        "pass_at_1_macro": macro,
        "pass_at_1_pooled": pooled,
        "se_method": se_method,
        "rounds": rounds,
        "n_rounds": len(rounds),
        "se": se,
        "ci95_half_width": 1.96 * se if se is not None else None,
        "pass_at_k": pass_k,
        "k": min_trials,
        **extra_k,
        "exception_types": dict(sorted(errors.items(), key=lambda kv: -kv[1])),
        "suspect_infra_trials": suspect,
        "reward_one_with_exception": reward_one_with_error,
        "sensitivity_pass_at_1_excluding_suspect": sens,
        "compliant": not problems,
        "problems": problems,
        "problem_details": (short + cfg_problems)[:50],
        "per_task": per_task_rows,
    }


def pct(x):
    return "n/a" if x is None else f"{100 * x:.2f}%"


def format_text(r: dict) -> str:
    t = r["trials"]
    ci = f" ± {100 * r['ci95_half_width']:.2f}（95%）" if r["ci95_half_width"] is not None else ""
    lines = [f"job: {r['job']}  口径: {r['rules']}",
             f"题数: {r['tasks']}  trial: 通过={t['pass']} 失败={t['fail']} 剔除(判分错误)={t['excluded']} 运行中={t['running']}"]
    if r["rules"] == "tbench4":
        lines += [f"accuracy（全部 trial pooled）: {pct(r['pass_at_1'])}{ci}",
                  "pass@k（无偏）: " + ", ".join(f"{k[-1]}={pct(r[k])}" for k in
                                               ("pass_at_2", "pass_at_3", "pass_at_4", "pass_at_5"))]
    else:
        lines += [f"pass@1（{sum(1 for p in r['per_task'] if p['scored'])} 题宏平均）: {pct(r['pass_at_1'])}{ci}"
                  f"  [pooled {pct(r['pass_at_1_pooled'])}]",
                  f"按轮分数 s1..sR（R={r['n_rounds']}）: " + ", ".join(pct(s) for s in r["rounds"]),
                  f"pass@{r['k']}: {pct(r['pass_at_k'])}"]
    if r["exception_types"]:
        lines.append("异常类型: " + ", ".join(f"{k}={v}" for k, v in r["exception_types"].items()))
    if r["rules"] == "deepswe":
        lines.append(f"敏感性（不作主分数）：剔除 {r['suspect_infra_trials']} 个可疑基础设施异常后 pass@1 = "
                     f"{pct(r['sensitivity_pass_at_1_excluding_suspect'])}")
    if r["reward_one_with_exception"] and r["rules"] == "deepswe":
        lines.append(f"注意：{r['reward_one_with_exception']} 个 trial 带异常但 reward=1，已按失败计")
    if r["compliant"]:
        lines.append("COMPLIANT：符合次数、题数、超时与资源规则。")
    else:
        lines.append("NON-COMPLIANT: " + "；".join(r["problems"]))
        lines.extend(f"  - {d}" for d in r["problem_details"][:20])
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job", nargs="+", type=Path, help="单个 job 目录")
    parser.add_argument("--rules", choices=RULES, default="deepswe", help="计分口径（默认 deepswe）")
    parser.add_argument("--min-trials", type=int, required=True,
                        help="每题至少完成的 trial 数（DeepSWE 4；Terminal-Bench 4.0 为 5）")
    parser.add_argument("--expected-tasks", type=int, default=None, help="官方题数；不一致即不合规")
    parser.add_argument("--json", action="store_true", help="向 stdout 输出 JSON")
    parser.add_argument("--out", type=Path, default=None, help="同时把 JSON 写到该文件（如 runs/<id>/score.json）")
    args = parser.parse_args(argv)
    if len(args.job) != 1:
        print("拒绝对多个 job 目录计分：不同运行的结果不得合并。", file=sys.stderr)
        return 2
    job = args.job[0]
    if not job.is_dir():
        print(f"不是目录：{job}", file=sys.stderr)
        return 2
    result = score_job(job, args.min_trials, args.expected_tasks, args.rules)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))
    return 0 if result["compliant"] else 2


if __name__ == "__main__":
    sys.exit(main())
