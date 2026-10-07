#!/usr/bin/env python3
"""bench：按评测组织规则一键检查、启动、监控、计分（零第三方依赖，Python 3.11+）。

子命令：
  preflight  启动前检查：二进制 sha256、harness、配置与密钥变量、数据集与题数、Docker 与 runner 版本、
             本地镜像、模型端点（只报状态码）、主机资源。任一硬性项失败退出码 1。
  run        先跑 preflight，再在后台启动**合规命令**，写 runs/<id>/manifest.json，打印 run id。
             DeepSWE：pier run --n-attempts 4 --timeout-multiplier 1.0，全部 113 题，单 job。
             Terminal-Bench 4.0：harbor run -d terminal-bench/terminal-bench@4.0.0 -k 5 --timeout-multiplier 1.0，单 job。
             --no-web 由 harness 写死；步数不设上限。不提供子集、重跑、续跑替换、取优、拼接等选项。
  status     进度、当前 pass@1 与 ±、异常分类、耗时 / 步数 / token / 成本、预计剩余时间、主机资源。
  score      调 scoring/score.py + usage.py：合规判定（不合规退出码 2）、pass@1 ±、pass@k、榜单行；
             写 runs/<id>/score.json。

示例见 docs/quickstart.md；每个子命令 --help 有完整参数。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scoring"))
from score import TRIAL_DIR, load_trials, score_job  # noqa: E402
from usage import usage_job  # noqa: E402
from leaderboard_row import build_row, render  # noqa: E402

HARNESS_SOURCE = {"repo": "infiniact/iacoder", "tag": "v0.7.64",
                  "commit": "24228eac7ae08f11cccc0d6f24028c3bdcd39cdc"}

PROFILES = {
    "deepswe": {
        "title": "DeepSWE",
        "runner": "pier", "runner_pin": "0.3.1", "runner_env": "PIER_BIN",
        "harness": "harness/deep-swe", "agent": "iacoder_pier:IacoderPierAgent",
        "agent_file": "iacoder_pier/agent.py",
        "k": 4, "tasks": 113, "rules": "deepswe",
        "binary_env": "IACODER_BINARY_URL",
        "dataset": "datacurve/deep-swe-1-1",
        "dataset_source": "https://github.com/datacurve-ai/deep-swe",
        "dataset_commit": "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea",
        "needs_tasks_dir": True,
    },
    "tbench": {
        "title": "Terminal-Bench 4.0",
        "runner": "harbor", "runner_pin": None, "runner_env": "HARBOR_BIN",
        "harness": "harness/terminal-bench", "agent": "iacoder_tbench:IacoderAgent",
        "agent_file": "iacoder_tbench/agent.py",
        "k": 5, "tasks": 66, "rules": "tbench4",
        "binary_env": "IACODER_DEV_BINARY_URL",
        "dataset": "terminal-bench/terminal-bench@4.0.0",
        "dataset_source": "https://github.com/harbor-framework/terminal-bench",
        "dataset_commit": "452bf305c6daa62fc59061d22133a7cbc7c1572e",
        "needs_tasks_dir": False,
    },
}

# run 允许透传给 agent 的非密钥环境变量。其余一律拒绝。
AGENT_ENV_ALLOWED = {
    "IACODER_PROMPT_TEMPLATE", "IACODER_REASONING_EFFORT", "IACODER_EVAL_DECLARE_DEADLINE",
    "IACODER_TEMPERATURE", "IACODER_TOP_P", "IACODER_CONTEXT_WINDOW",
    "IACODER_MAX_OUTPUT_TOKENS_PER_TURN", "IACODER_EVAL_TRANSCRIPT", "IACODER_EVAL_LOG_SHELL_COMMANDS",
    "RUST_LOG",
}
# 明确禁止：违反规则或由本工具计算。
AGENT_ENV_FORBIDDEN = {
    "IACODER_MAX_TURNS": "评测规则不设步数上限",
    "IACODER_TASK_OUTER_TIMEOUT_SEC": "由 resolve-outer-cap.sh 从 task.toml 推导，不得手工覆盖",
}
ENV_REF = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")


class BenchError(Exception):
    pass


def runs_dir() -> Path:
    return Path(os.environ.get("BENCH_RUNS_DIR", ROOT / "runs"))


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(root: Path) -> str:
    """目录内容指纹：按相对路径排序，对 (路径, 文件 sha256) 再求 sha256。"""
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(f"{p.relative_to(root)}\0{sha256_file(p)}\n".encode())
    return h.hexdigest()


def sh(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


# --------------------------------------------------------------------------- checks
class Report:
    def __init__(self):
        self.items: list[tuple[str, str, str]] = []

    def add(self, level: str, name: str, detail: str):
        self.items.append((level, name, detail))

    @property
    def failed(self) -> bool:
        return any(level == "FAIL" for level, _, _ in self.items)

    def text(self) -> str:
        return "\n".join(f"[{lvl:4}] {name}: {detail}" for lvl, name, detail in self.items)


def sha_entries(version: str) -> dict[str, str]:
    sums = ROOT / "bin" / version / "SHA256SUMS"
    if not sums.is_file():
        raise BenchError(f"bin/{version}/SHA256SUMS 不存在")
    out = {}
    for line in sums.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out[parts[-1].lstrip("*")] = parts[0]
    return out


def check_binary(rep: Report, version: str, binary_url: str | None, fetch: bool = True):
    try:
        entries = sha_entries(version)
    except BenchError as e:
        rep.add("FAIL", "二进制", str(e))
        return
    for name, digest in entries.items():
        path = ROOT / "bin" / version / name
        if not path.is_file():
            rep.add("FAIL", "二进制", f"bin/{version}/{name} 缺失")
        elif sha256_file(path) != digest:
            rep.add("FAIL", "二进制", f"bin/{version}/{name} sha256 不符")
        else:
            rep.add("OK", "二进制", f"bin/{version}/{name} sha256 {digest[:12]}…")
    if not binary_url:
        rep.add("FAIL", "二进制 URL", "未提供 --binary-url（容器从这个 URL 取二进制）")
        return
    if not fetch:
        rep.add("WARN", "二进制 URL", "跳过下载校验")
        return
    try:
        with urllib.request.urlopen(binary_url, timeout=60) as r:
            h = hashlib.sha256()
            for chunk in iter(lambda: r.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        name = next((n for n, d in entries.items() if d == digest), None)
        if name:
            rep.add("OK", "二进制 URL", f"可下载，内容与 bin/{version}/{name} 一致")
        else:
            rep.add("FAIL", "二进制 URL", f"可下载，但 sha256 {digest[:12]}… 不在 bin/{version}/SHA256SUMS 中")
    except (urllib.error.URLError, OSError, ValueError) as e:
        rep.add("FAIL", "二进制 URL", f"无法下载：{type(e).__name__}")


def check_harness(rep: Report, prof: dict):
    h = ROOT / prof["harness"]
    common = ROOT / "harness" / "common" / "iacoder_harbor_base" / "base.py"
    if (h / prof["agent_file"]).is_file() and common.is_file():
        rep.add("OK", "harness", f"{prof['harness']} + harness/common（{HARNESS_SOURCE['tag']}）")
    else:
        rep.add("FAIL", "harness", f"{prof['harness']}/{prof['agent_file']} 或 harness/common 缺失")


def read_config(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def env_refs(value) -> list[str]:
    """配置值（不含注释）里引用的 ${ENV:VAR} 变量名。"""
    if isinstance(value, dict):
        return [v for x in value.values() for v in env_refs(x)]
    if isinstance(value, list):
        return [v for x in value for v in env_refs(x)]
    return ENV_REF.findall(value) if isinstance(value, str) else []


def check_config(rep: Report, config: Path | None, model: str) -> dict | None:
    if config is None or not config.is_file():
        rep.add("FAIL", "配置", f"配置文件不存在：{config}")
        return None
    text = config.read_text()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        rep.add("FAIL", "配置", f"TOML 解析失败：{e}")
        return None
    ok = True
    for name, prov in (data.get("providers") or {}).items():
        key = prov.get("api_key")
        if key and not ENV_REF.fullmatch(str(key).strip()):
            rep.add("FAIL", "配置", f"providers.{name}.api_key 不是 ${{ENV:VAR}} 引用（密钥不得写进文件）")
            ok = False
    for var in sorted(set(env_refs(data))):
        if os.environ.get(var):
            rep.add("OK", "密钥变量", f"{var} 已设置（不显示值）")
        else:
            rep.add("FAIL", "密钥变量", f"{var} 未设置")
            ok = False
    turns = (data.get("settings") or {}).get("max_turns")
    if turns not in (None, 0):
        rep.add("FAIL", "配置", f"[settings] max_turns = {turns}：评测规则不设步数上限，请删除")
        ok = False
    if model not in (data.get("models") or {}) and "/" not in model:
        rep.add("WARN", "配置", f"[models] 中没有 {model}")
    if ok:
        rep.add("OK", "配置", f"{config.name} sha256 {sha256_file(config)[:12]}…")
    return data


def task_dirs(tasks_dir: Path) -> list[Path]:
    return sorted(p.parent for p in tasks_dir.glob("*/task.toml"))


def outer_cap(tasks_dir: Path) -> tuple[int, str]:
    cp = sh([str(ROOT / "harness/deep-swe/resolve-outer-cap.sh"), str(tasks_dir), "1.0"])
    return cp.returncode, (cp.stdout.strip() or cp.stderr.strip())


def check_dataset(rep: Report, prof: dict, tasks_dir: Path | None):
    if tasks_dir is None:
        if prof["needs_tasks_dir"]:
            rep.add("FAIL", "数据集", "需要 --tasks-dir（DeepSWE 任务目录）")
        else:
            rep.add("OK", "数据集", f"由 {prof['runner']} 拉取 {prof['dataset']}（官方 {prof['tasks']} 题）")
        return
    if not tasks_dir.is_dir():
        rep.add("FAIL", "数据集", f"任务目录不存在：{tasks_dir}")
        return
    n = len(task_dirs(tasks_dir))
    rep.add("OK" if n == prof["tasks"] else "FAIL", "数据集", f"{n} 题（官方 {prof['tasks']}）")
    cp = sh(["git", "-C", str(tasks_dir), "rev-parse", "HEAD"])
    if cp.returncode == 0:
        commit = cp.stdout.strip()
        lvl = "OK" if commit == prof["dataset_commit"] else "WARN"
        rep.add(lvl, "数据集版本", f"git {commit[:12]}（固定版本 {prof['dataset_commit'][:12]}）")
    else:
        rep.add("WARN", "数据集版本", "任务目录不在 git 仓库内，无法核对版本")
    if prof["runner"] == "pier":
        code, out = outer_cap(tasks_dir)
        rep.add("OK" if code == 0 else "FAIL", "task.toml 超时", f"[agent] timeout_sec → {out}")


def check_docker_runner(rep: Report, prof: dict, runner_bin: str | None) -> dict:
    info = {}
    cp = sh(["docker", "version", "--format", "{{.Server.Version}}"])
    if cp.returncode == 0:
        info["docker"] = cp.stdout.strip()
        rep.add("OK", "Docker", f"server {info['docker']}")
    else:
        rep.add("FAIL", "Docker", "docker daemon 不可用")
    exe = runner_bin or os.environ.get(prof["runner_env"]) or shutil.which(prof["runner"])
    if not exe:
        rep.add("FAIL", prof["runner"], f"找不到 {prof['runner']}（设置 {prof['runner_env']} 或加入 PATH）")
        return info
    info["runner_bin"] = exe
    cp = sh([exe, "--version"])
    ver = (cp.stdout + cp.stderr).strip().splitlines()[-1] if cp.returncode == 0 else ""
    info["runner_version"] = ver
    if cp.returncode != 0:
        rep.add("FAIL", prof["runner"], f"{exe} --version 失败")
    elif prof["runner_pin"] and prof["runner_pin"] not in ver:
        rep.add("WARN", prof["runner"], f"版本 {ver!r}，固定版本 {prof['runner_pin']}")
    else:
        rep.add("OK", prof["runner"], ver)
    return info


def check_images(rep: Report, tasks_dir: Path | None, require: bool):
    if tasks_dir is None or not tasks_dir.is_dir():
        rep.add("WARN", "镜像", "没有本地任务目录，跳过镜像检查")
        return
    sys.path.insert(0, str(ROOT / "reproduce" / "imgcache"))
    from imgcache import refs_from_tasks  # noqa: E402
    extra = (ROOT / "reproduce/imgcache/harness-extra.refs").read_text().split()
    refs = refs_from_tasks([str(tasks_dir)], [r for r in extra if r.startswith("ubuntu:")])
    missing = [r for r in refs if sh(["docker", "image", "inspect", r], timeout=30).returncode != 0]
    if not missing:
        rep.add("OK", "镜像", f"{len(refs)} 个所需镜像均在本地")
    else:
        rep.add("FAIL" if require else "WARN", "镜像",
                f"{len(missing)}/{len(refs)} 个不在本地（如 {missing[0]}）；大并发首拉易被 registry 限流，"
                "先用 reproduce/imgcache 预拉或恢复")


def check_endpoint(rep: Report, config: dict | None, probe: bool):
    if not probe:
        rep.add("WARN", "模型端点", "已跳过探测（--no-endpoint-probe）")
        return
    for name, prov in ((config or {}).get("providers") or {}).items():
        base = prov.get("base_url")
        m = ENV_REF.fullmatch(str(prov.get("api_key", "")).strip())
        key = os.environ.get(m.group(1)) if m else None
        if not base or not key:
            rep.add("WARN", "模型端点", f"providers.{name}：缺 base_url 或密钥，未探测")
            continue
        req = urllib.request.Request(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except (urllib.error.URLError, OSError) as e:
            rep.add("FAIL", "模型端点", f"providers.{name}：连接失败 {type(e).__name__}")
            continue
        rep.add("OK" if 200 <= code < 300 else "FAIL", "模型端点", f"providers.{name} GET /models → HTTP {code}")


def host_spec() -> dict:
    """主机规格（不含主机名、地址等身份信息）。"""
    spec = {"os": platform.system(), "kernel": platform.release(), "arch": platform.machine(),
            "cpus": os.cpu_count()}
    mem = meminfo()
    if mem:
        spec["mem_total_gib"] = round(mem["MemTotal"] / 2**20, 1)
    return spec


def meminfo() -> dict | None:
    try:
        out = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0])  # KiB
        return out
    except OSError:
        cp = sh(["sysctl", "-n", "hw.memsize"])
        return {"MemTotal": int(cp.stdout) // 1024} if cp.returncode == 0 and cp.stdout.strip() else None


def task_limits(tasks_dir: Path | None) -> tuple[float, float, int]:
    """任务 [environment] cpus / memory_mb 的最大值，以及需要 GPU 的题数。"""
    cpus = mem = 0.0
    gpus = 0
    for d in task_dirs(tasks_dir) if tasks_dir and tasks_dir.is_dir() else []:
        env = (read_config(d / "task.toml").get("environment") or {})
        cpus = max(cpus, float(env.get("cpus") or 0))
        mem = max(mem, float(env.get("memory_mb") or 0))
        gpus += 1 if (env.get("gpus") or 0) else 0
    return cpus, mem, gpus


def check_resources(rep: Report, prof: dict, concurrency: int, tasks_dir: Path | None, jobs_root: Path):
    spec = host_spec()
    cpus, mem_mb, gpus = task_limits(tasks_dir)
    if cpus:
        rep.add("WARN" if concurrency * cpus > (spec["cpus"] or 0) else "OK", "CPU",
                f"{concurrency} 并发 × 每 trial {cpus:g} CPU 限额 = {concurrency * cpus:g}，主机 {spec['cpus']} 核")
    else:
        rep.add("OK", "CPU", f"主机 {spec['cpus']} 核，{concurrency} 并发")
    m = meminfo() or {}
    if m:
        avail = m.get("MemAvailable", m.get("MemTotal", 0)) / 2**20
        need = concurrency * (mem_mb / 1024 if mem_mb else 0)
        detail = f"可用约 {avail:.0f} GiB"
        if need:
            detail += f"；限额合计 {need:.0f} GiB（实际占用通常远低于限额，DeepSWE 实测中位约 0.5 GiB/trial）"
        rep.add("WARN" if avail < concurrency * 1.0 else "OK", "内存", detail)
    jobs_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(jobs_root).free / 2**30
    want = 400 if prof["runner"] == "pier" else 100
    rep.add("WARN" if free < want else "OK", "磁盘", f"{jobs_root.name}/ 所在分区可用 {free:.0f} GiB（建议 ≥ {want}）")
    if prof["rules"] == "tbench4":
        has_gpu = shutil.which("nvidia-smi") is not None
        rep.add("OK" if has_gpu else "WARN", "GPU",
                "检测到 nvidia-smi" if has_gpu else
                "未检测到 GPU：TB 4.0 有 3 题需要 GPU，这些题会失败并按 0 计；官方不接受子集，结果仍须覆盖全部 66 题")
    elif gpus:
        rep.add("WARN", "GPU", f"{gpus} 题声明需要 GPU")


def preflight(args, prof: dict) -> tuple[Report, dict]:
    rep = Report()
    info: dict = {}
    if args.concurrency < 1:
        rep.add("FAIL", "并发", "--concurrency 必须 ≥ 1")
    check_binary(rep, args.version, args.binary_url, fetch=not args.no_binary_fetch)
    check_harness(rep, prof)
    cfg = check_config(rep, args.config, args.model)
    check_dataset(rep, prof, args.tasks_dir)
    info.update(check_docker_runner(rep, prof, args.runner_bin))
    check_images(rep, args.tasks_dir, args.require_images)
    check_endpoint(rep, cfg, not args.no_endpoint_probe)
    check_resources(rep, prof, args.concurrency, args.tasks_dir, args.jobs_root)
    return rep, info


# --------------------------------------------------------------------------- command
def parse_agent_env(items: list[str]) -> dict[str, str]:
    env = {}
    for item in items or []:
        if "=" not in item:
            raise BenchError(f"--agent-env 需要 KEY=VALUE：{item}")
        k, v = item.split("=", 1)
        if k in AGENT_ENV_FORBIDDEN:
            raise BenchError(f"禁止设置 {k}：{AGENT_ENV_FORBIDDEN[k]}")
        if k not in AGENT_ENV_ALLOWED:
            raise BenchError(f"不允许透传 {k}；允许的变量：{', '.join(sorted(AGENT_ENV_ALLOWED))}")
        env[k] = v
    return env


def build_launch(prof: dict, args, run_id: str, jobs_dir: Path, outer: str | None = None) -> dict:
    """生成合规命令与非密钥环境变量。所有规则相关参数在这里写死，不接受外部覆盖。

    --max-retries 0 显式写出：重试会用新 trial 替换失败 trial，等于重采样（harbor 默认即 0）。
    """
    harness = ROOT / prof["harness"]
    env = {
        "PYTHONPATH": f"{harness}:{ROOT / 'harness' / 'common'}",
        "IACODER_CONFIG": str(args.config.resolve()),
        "IACODER_VERSION": args.version,
        prof["binary_env"]: args.binary_url or "",
    }
    env.update(parse_agent_env(args.agent_env))
    runner = args.runner_bin or os.environ.get(prof["runner_env"]) or prof["runner"]
    if prof["runner"] == "pier":
        if outer:
            env["IACODER_TASK_OUTER_TIMEOUT_SEC"] = outer
        env["PIER_BIN"] = runner
        cmd = [runner, "run", "-p", str(args.tasks_dir.resolve()),
               "--agent-import-path", prof["agent"], "--model", args.model,
               "--n-tasks", str(prof["tasks"]), "--n-concurrent", str(args.concurrency),
               "--n-attempts", str(prof["k"]), "--timeout-multiplier", "1.0",
               "--max-retries", "0", "-o", str(jobs_dir)]
    else:
        cmd = [runner, "run", "-d", prof["dataset"],
               "--agent-import-path", prof["agent"], "-m", args.model,
               "-k", str(prof["k"]), "-n", str(args.concurrency), "--timeout-multiplier", "1.0",
               "--max-retries", "0",
               "--jobs-dir", str(jobs_dir.parent), "--job-name", jobs_dir.name]
    return {"cmd": cmd, "env": env}


def public_command(launch: dict, args, jobs_dir: Path) -> str:
    """去掉本机路径的命令文本，写进 manifest。"""
    subs = {str(jobs_dir): "<jobs-dir>", str(jobs_dir.parent): "<jobs-root>", str(ROOT): "<bench>"}
    if args.tasks_dir:
        subs[str(args.tasks_dir.resolve())] = "<tasks-dir>"
    if args.binary_url:
        subs[args.binary_url] = "<binary-url>"

    def scrub(s: str) -> str:
        for a, b in sorted(subs.items(), key=lambda kv: -len(kv[0])):
            s = s.replace(a, b)
        return s

    env = " ".join(f"{k}={shlex.quote(scrub(v))}" for k, v in sorted(launch["env"].items()))
    runner = Path(launch["cmd"][0]).name
    return scrub(env + " " + " ".join(shlex.quote(c) for c in [runner, *launch["cmd"][1:]]))


def shell_script(launch: dict) -> str:
    lines = ["#!/bin/sh", "# 由 ./bench run 生成；只含非密钥环境变量，密钥从启动者的环境继承。", "set -eu"]
    lines += [f"export {k}={shlex.quote(v)}" for k, v in sorted(launch["env"].items())]
    lines += ["# 回收无容器挂载的孤儿 compose 网络（不影响正在运行的批次）",
              "docker network prune -f >/dev/null 2>&1 || true",
              "exec " + " ".join(shlex.quote(c) for c in launch["cmd"])]
    return "\n".join(lines) + "\n"


def make_run_id(bench: str, version: str, model: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return re.sub(r"[^A-Za-z0-9._-]", "-", f"{bench}-{version}-{model}-{stamp}")


def cmd_preflight(args) -> int:
    prof = PROFILES[args.benchmark]
    rep, _ = preflight(args, prof)
    print(f"preflight：{prof['title']}，iacoder {args.version}，模型 {args.model}，{args.concurrency} 并发")
    print(rep.text())
    print("结论：" + ("FAIL，拒绝启动" if rep.failed else "通过"))
    return 1 if rep.failed else 0


def cmd_run(args) -> int:
    prof = PROFILES[args.benchmark]
    run_id = make_run_id(args.benchmark, args.version, args.model)
    jobs_dir = (args.jobs_root / run_id).resolve()
    if args.print_command:
        launch = build_launch(prof, args, run_id, jobs_dir, outer="<由 resolve-outer-cap.sh 推导>"
                              if prof["runner"] == "pier" else None)
        print(shell_script(launch), end="")
        return 0
    rep, info = preflight(args, prof)
    print(rep.text())
    if rep.failed:
        print("preflight 未通过，拒绝启动。", file=sys.stderr)
        return 1
    outer = None
    if prof["runner"] == "pier":
        code, outer = outer_cap(args.tasks_dir)
        if code != 0:
            print(f"无法推导外层超时：{outer}", file=sys.stderr)
            return 1
    launch = build_launch(prof, args, run_id, jobs_dir, outer)
    rdir = runs_dir() / run_id
    rdir.mkdir(parents=True)
    if prof["runner"] == "pier":
        jobs_dir.mkdir(parents=True, exist_ok=True)  # harbor 自己创建 --job-name 目录
    script = rdir / "launch.sh"
    script.write_text(shell_script(launch))
    script.chmod(0o700)
    log = rdir / "launch.log"
    with open(log, "ab") as out:
        proc = subprocess.Popen(["/bin/sh", str(script)], stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(ROOT))
    (rdir / "pid").write_text(f"{proc.pid}\n")
    (rdir / "local.json").write_text(json.dumps({
        "jobs_dir": str(jobs_dir), "tasks_dir": str(args.tasks_dir.resolve()) if args.tasks_dir else None,
        "log": str(log), "pid": proc.pid}, indent=2) + "\n")
    entries = sha_entries(args.version)
    manifest = {
        "run_id": run_id,
        "benchmark": prof["title"],
        "rules": prof["rules"],
        "binary": {"version": args.version, "sha256": entries},
        "harness": {**HARNESS_SOURCE, "path": prof["harness"],
                    "tree_sha256": tree_sha256(ROOT / "harness")},
        "config": {"file": args.config.name, "sha256": sha256_file(args.config)},
        "model": args.model,
        "dataset": {"name": prof["dataset"], "source": prof["dataset_source"],
                    "pinned_commit": prof["dataset_commit"],
                    "tasks": len(task_dirs(args.tasks_dir)) if args.tasks_dir else prof["tasks"]},
        "trials_per_task": prof["k"],
        "timeout_multiplier": 1.0,
        "concurrency": args.concurrency,
        "agent_env": parse_agent_env(args.agent_env),
        "outer_timeout_sec": outer,
        "runner": {"name": prof["runner"], "version": info.get("runner_version")},
        "docker": info.get("docker"),
        "host": host_spec(),
        "command": public_command(launch, args, jobs_dir),
        "started_at": now_utc(),
        "finished_at": None,
    }
    (rdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"run id: {run_id}")
    print(f"  pid {proc.pid}，日志 {log}")
    print(f"  job 输出 {jobs_dir}")
    print(f"  查看进度：./bench status {run_id} --watch 60")
    return 0


# --------------------------------------------------------------------------- status / score
def find_job_dir(path: Path) -> Path:
    """接受 job 目录本身，或其上一层（pier -o / harbor --jobs-dir）；后者必须恰好含一个 job。"""
    def has_trials(d: Path) -> bool:
        return any(c.is_dir() and TRIAL_DIR.match(c.name) for c in d.iterdir())

    if not path.is_dir():
        return path
    if has_trials(path):
        return path
    jobs = [p for p in sorted(path.iterdir()) if p.is_dir() and has_trials(p)]
    if len(jobs) == 1:
        return jobs[0]
    if not jobs:
        return path  # 还没有 trial
    raise BenchError(f"{path} 下有 {len(jobs)} 个 job，一次只看一个（不同 job 不得合并）")


def resolve_target(target: str) -> tuple[Path, dict | None, Path | None]:
    """run id → (job 目录, manifest, runs/<id>)；路径 → (job 目录, None, None)。"""
    rdir = runs_dir() / target
    if (rdir / "manifest.json").is_file():
        manifest = json.loads((rdir / "manifest.json").read_text())
        local = json.loads((rdir / "local.json").read_text()) if (rdir / "local.json").is_file() else {}
        jobs = Path(local.get("jobs_dir", ""))
        return (find_job_dir(jobs) if jobs.is_dir() else jobs), manifest, rdir
    path = Path(target)
    if not path.exists():
        raise BenchError(f"既不是 run id 也不是目录：{target}")
    return find_job_dir(path), None, None


def profile_for(manifest: dict | None, job: Path, override: str | None) -> dict:
    if override:
        return PROFILES[override]
    if manifest:
        return next(p for p in PROFILES.values() if p["rules"] == manifest.get("rules"))
    for t in load_trials(job):
        path = ((t.data.get("config") or {}).get("agent") or {}).get("import_path") or ""
        for p in PROFILES.values():
            if path == p["agent"]:
                return p
    return PROFILES["deepswe"]


def model_of(manifest: dict | None, job: Path) -> str | None:
    if manifest:
        return manifest.get("model")
    for t in load_trials(job):
        m = ((t.data.get("config") or {}).get("agent") or {}).get("model_name")
        if m:
            return m
    return None


def pricing_for(model: str | None, override: Path | None) -> Path | None:
    if override:
        return override
    if model:
        p = ROOT / "scoring" / "pricing" / f"{model.rsplit('/', 1)[-1]}.toml"
        return p if p.is_file() else None
    return None


def host_status(job: Path) -> dict:
    out = {}
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        pass
    m = meminfo() or {}
    if "MemAvailable" in m:
        out["mem_used_gib"] = round((m["MemTotal"] - m["MemAvailable"]) / 2**20, 1)
        out["mem_avail_gib"] = round(m["MemAvailable"] / 2**20, 1)
    if job.exists():
        du = shutil.disk_usage(job)
        out["disk_free_gib"] = round(du.free / 2**30)
    try:
        for line in Path("/proc/vmstat").read_text().splitlines():
            if line.startswith("oom_kill "):
                out["oom_kill_total"] = int(line.split()[1])
    except OSError:
        pass
    return out


def pid_alive(rdir: Path | None) -> bool | None:
    if not rdir or not (rdir / "pid").is_file():
        return None
    try:
        os.kill(int((rdir / "pid").read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def status_data(target: str, override: str | None = None, concurrency: int | None = None) -> dict:
    job, manifest, rdir = resolve_target(target)
    prof = profile_for(manifest, job, override)
    expected = prof["tasks"] * prof["k"]
    trials = load_trials(job, prof["rules"]) if job.is_dir() else []
    finished = [t for t in trials if t.outcome != "running"]
    started_dirs = [p for p in job.iterdir() if p.is_dir() and TRIAL_DIR.match(p.name)] if job.is_dir() else []
    running = len(started_dirs) - len(finished)
    sc = score_job(job, prof["k"], prof["tasks"], prof["rules"]) if trials else None
    us = usage_job(job, pricing_for(model_of(manifest, job), None)) if trials else None
    conc = concurrency or (manifest or {}).get("concurrency") or max(running, 1)
    eta = None
    if us and us["summary"]["trial_wall_s"]["mean"]:
        remaining = max(expected - len(finished), 0)
        eta = remaining * us["summary"]["trial_wall_s"]["mean"] / conc
    return {
        "time": now_utc(), "job": str(job), "run_id": (manifest or {}).get("run_id"),
        "benchmark": prof["title"], "rules": prof["rules"],
        "finished": len(finished), "expected": expected, "running": max(running, 0),
        "process_alive": pid_alive(rdir),
        "pass_at_1": sc and sc["pass_at_1"], "ci95_half_width": sc and sc["ci95_half_width"],
        "rounds": sc and sc["rounds"], "pass_at_k": sc and sc["pass_at_k"],
        "exception_types": sc and sc["exception_types"], "trials": sc and sc["trials"],
        "usage": us and us["summary"], "eta_s": eta, "host": host_status(job),
    }


def _p(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def status_text(d: dict) -> str:
    lines = [f"[{d['time']}] {d['benchmark']}  {d['run_id'] or d['job']}",
             f"进度 {d['finished']}/{d['expected']} 个 trial 已结束，{d['running']} 个在跑"
             + ("" if d["process_alive"] is None else f"，进程{'存活' if d['process_alive'] else '已退出'}")]
    if d["pass_at_1"] is not None:
        ci = f" ±{100 * d['ci95_half_width']:.1f}" if d["ci95_half_width"] is not None else ""
        lines.append(f"当前 pass@1 {_p(d['pass_at_1'])}{ci}（站点榜单口径 pooled；未完成，不是成绩）；按轮 "
                     + ", ".join(_p(s) for s in d["rounds"] or []))
    if d["exception_types"]:
        lines.append("异常: " + ", ".join(f"{k}={v}" for k, v in d["exception_types"].items()))
    u = d["usage"]
    if u:
        def mm(key, f):
            s = u[key]
            return "n/a" if s["mean"] is None else f"{f(s['mean'])} / {f(s['median'])}"
        lines.append("均值/中位: agent " + mm("agent_wall_s", lambda v: f"{v / 60:.0f}min")
                     + "，步数 " + mm("steps", lambda v: f"{v:.0f}")
                     + "，输出 token " + mm("output_tokens", lambda v: f"{v / 1000:.1f}k")
                     + "，成本（高峰价）" + mm("cost_peak_usd", lambda v: f"${v:.3f}"))
    if d["eta_s"] is not None:
        lines.append(f"预计剩余约 {d['eta_s'] / 3600:.1f} 小时（按已结束 trial 的平均时长与并发估算）")
    h = d["host"]
    if h:
        lines.append("主机: " + ", ".join(f"{k}={v}" for k, v in h.items()))
    return "\n".join(lines)


def cmd_status(args) -> int:
    while True:
        d = status_data(args.target, args.benchmark, args.concurrency)
        print(json.dumps(d, ensure_ascii=False, indent=2) if args.json else status_text(d), flush=True)
        if not args.watch:
            return 0
        if d["finished"] >= d["expected"] and not d["running"]:
            return 0
        if d["process_alive"] is False and not d["running"]:
            print("运行进程已退出。")
            return 0
        time.sleep(args.watch)


def cmd_score(args) -> int:
    job, manifest, rdir = resolve_target(args.target)
    prof = profile_for(manifest, job, args.benchmark)
    sc = score_job(job, prof["k"], prof["tasks"], prof["rules"])
    model = model_of(manifest, job)
    us = usage_job(job, pricing_for(model, args.pricing))
    label = args.label or (f"iacoder {manifest['binary']['version']} + {model}" if manifest else f"iacoder + {model}")
    row = build_row(sc, us, label)
    result = {"score": {k: v for k, v in sc.items() if k != "per_task"}, "per_task": sc["per_task"],
              "usage": {k: v for k, v in us.items() if k != "trials"}, "leaderboard_row": row}
    out = args.out or (rdir / "score.json" if rdir else None)
    if out:
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if rdir and manifest and sc["compliant"] and not manifest.get("finished_at"):
        ends = [t.data.get("finished_at") for t in load_trials(job) if t.data.get("finished_at")]
        manifest["finished_at"] = max(ends) if ends else None
        (rdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        from score import format_text
        print(format_text(sc))
        if args.leaderboard_row:
            print()
            print(render(row, "md"))
        if out:
            print(f"\n已写入 {out}")
    return 0 if sc["compliant"] else 2


# --------------------------------------------------------------------------- CLI
def add_run_args(p: argparse.ArgumentParser):
    p.add_argument("--benchmark", choices=sorted(PROFILES), required=True,
                   help="deepswe（113 题 × 4）或 tbench（Terminal-Bench 4.0，66 题 × 5）")
    p.add_argument("--version", required=True, help="bin/ 下的版本目录，如 v0.7.64")
    p.add_argument("--model", required=True, help="传给 runner 的模型名（与配置中的 [models] 对应）")
    p.add_argument("--concurrency", type=int, required=True, help="并发 trial 数")
    p.add_argument("--config", type=Path, default=None, help="iacoder 配置，默认 configs/<model>.toml")
    p.add_argument("--binary-url", default=None,
                   help="容器取二进制的 URL（默认取 IACODER_BINARY_URL / IACODER_DEV_BINARY_URL）")
    p.add_argument("--tasks-dir", type=Path, default=None, help="本地任务目录（DeepSWE 必需）")
    p.add_argument("--jobs-root", type=Path, default=ROOT / "jobs", help="job 输出根目录（默认 ./jobs，已 gitignore）")
    p.add_argument("--runner-bin", default=None, help="pier / harbor 可执行文件路径")
    p.add_argument("--agent-env", action="append", default=[], metavar="KEY=VALUE",
                   help="透传给 agent 的非密钥变量（白名单），如 IACODER_REASONING_EFFORT=max")
    p.add_argument("--no-endpoint-probe", action="store_true", help="不探测模型端点")
    p.add_argument("--no-binary-fetch", action="store_true", help="不下载二进制 URL 做 sha256 校验")
    p.add_argument("--require-images", action="store_true", help="镜像不全时视为失败")


def finish_run_args(args):
    prof = PROFILES[args.benchmark]
    if args.config is None:
        args.config = ROOT / "configs" / f"{args.model.rsplit('/', 1)[-1]}.toml"
    if args.binary_url is None:
        args.binary_url = os.environ.get(prof["binary_env"])
    args.jobs_root = args.jobs_root.resolve()
    if prof["needs_tasks_dir"] and args.tasks_dir is None:
        raise BenchError(f"{prof['title']} 需要 --tasks-dir（本地任务目录，固定版本见 RULES.md）")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bench", description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight", allow_abbrev=False, help="启动前检查")
    add_run_args(p)
    p.set_defaults(func=cmd_preflight)
    p = sub.add_parser("run", allow_abbrev=False, help="preflight 通过后在后台启动合规运行")
    add_run_args(p)
    p.add_argument("--print-command", action="store_true", help="只打印将要执行的启动脚本，不检查、不启动")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("status", allow_abbrev=False, help="查看进度")
    p.add_argument("target", help="run id 或 job 目录")
    p.add_argument("--watch", type=int, default=0, metavar="SECONDS", help="每隔 N 秒刷新，直到结束")
    p.add_argument("--benchmark", choices=sorted(PROFILES), default=None)
    p.add_argument("--concurrency", type=int, default=None, help="估算剩余时间用（默认取 manifest）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("score", allow_abbrev=False, help="按规则计分")
    p.add_argument("target", help="run id 或 job 目录")
    p.add_argument("--benchmark", choices=sorted(PROFILES), default=None)
    p.add_argument("--pricing", type=Path, default=None, help="默认 scoring/pricing/<model>.toml")
    p.add_argument("--label", default=None, help="榜单行的 agent 名")
    p.add_argument("--out", type=Path, default=None, help="score.json 路径（run id 时默认 runs/<id>/score.json）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--leaderboard-row", action="store_true")
    p.set_defaults(func=cmd_score)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd in ("preflight", "run"):
            finish_run_args(args)
            parse_agent_env(args.agent_env)
        return args.func(args)
    except BenchError as e:
        print(f"bench: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
