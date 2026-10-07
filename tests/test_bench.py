"""./bench 的单测：命令生成（--print-command）、禁用项、preflight 的配置与二进制检查、status / score 输出。

运行：python3 -m unittest discover -s tests   或   uv run --with pytest pytest tests
"""
from __future__ import annotations

import io
import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scoring" / "tests"))

import bench  # noqa: E402
from jobfixture import make_trial  # noqa: E402


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = bench.main(argv)
        except SystemExit as e:  # argparse
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        os.environ["BENCH_RUNS_DIR"] = str(self.t / "runs")
        self.tasks = self.t / "tasks"
        for i in range(113):
            d = self.tasks / f"task-{i:03d}"
            d.mkdir(parents=True)
            (d / "task.toml").write_text('[agent]\ntimeout_sec = 10800.0\n[environment]\ncpus = 2\nmemory_mb = 8192\n')
        self.rng = random.Random(3)

    def tearDown(self):
        os.environ.pop("BENCH_RUNS_DIR", None)
        self.tmp.cleanup()


class TestCommand(Base):
    def common(self, bench_name, *extra):
        argv = ["run", "--benchmark", bench_name, "--version", "v0.7.64", "--model", "deepseek-flash",
                "--concurrency", "8", "--binary-url", "http://bin.invalid/x.tar.gz",
                "--jobs-root", str(self.t / "jobs"), "--print-command", *extra]
        if bench_name == "deepswe":
            argv += ["--tasks-dir", str(self.tasks)]
        return run(argv)

    def test_deepswe_command_is_compliant(self):
        code, out, err = self.common("deepswe", "--agent-env", "IACODER_REASONING_EFFORT=max")
        self.assertEqual(code, 0, err)
        self.assertIn("--n-attempts 4", out)
        self.assertIn("--timeout-multiplier 1.0", out)
        self.assertIn("--n-tasks 113", out)
        self.assertIn("iacoder_pier:IacoderPierAgent", out)
        self.assertIn("IACODER_REASONING_EFFORT=max", out)
        self.assertIn("IACODER_BINARY_URL=", out)
        self.assertIn("--max-retries 0", out)
        self.assertNotIn("max-turns", out)

    def test_tbench_command_is_tb4(self):
        code, out, err = self.common("tbench")
        self.assertEqual(code, 0, err)
        self.assertIn("-d terminal-bench/terminal-bench@4.0.0", out)
        self.assertIn("-k 5", out)
        self.assertIn("--timeout-multiplier 1.0", out)
        self.assertIn("iacoder_tbench:IacoderAgent", out)
        self.assertIn("IACODER_DEV_BINARY_URL=", out)
        self.assertIn("--max-retries 0", out)
        self.assertNotIn(" -i ", out)  # 不选子集

    def test_forbidden_env(self):
        code, _, err = self.common("deepswe", "--agent-env", "IACODER_MAX_TURNS=100")
        self.assertEqual(code, 2)
        self.assertIn("步数上限", err)
        code, _, err = self.common("deepswe", "--agent-env", "IACODER_TASK_OUTER_TIMEOUT_SEC=99999")
        self.assertEqual(code, 2)
        code, _, err = self.common("deepswe", "--agent-env", "SOMETHING_ELSE=1")
        self.assertEqual(code, 2)
        self.assertIn("不允许透传", err)

    def test_no_noncompliant_options(self):
        for flag in ("--resume", "--best-of", "--n-tasks", "--task", "--k", "--n-attempts",
                     "--timeout-multiplier", "--max-turns", "--merge", "--retry"):
            code, _, _ = self.common("deepswe", flag, "1")
            self.assertEqual(code, 2, f"{flag} 不应被接受")
        help_text = bench.build_parser().format_help()
        for word in ("best", "resume", "merge"):
            self.assertNotIn(word, help_text.lower())

    def test_deepswe_requires_tasks_dir(self):
        code, _, err = run(["run", "--benchmark", "deepswe", "--version", "v0.7.64", "--model", "m",
                            "--concurrency", "1", "--print-command"])
        self.assertEqual(code, 2)
        self.assertIn("--tasks-dir", err)


class TestPreflightPieces(Base):
    def test_config_checks(self):
        rep = bench.Report()
        cfg = self.t / "c.toml"
        cfg.write_text('[providers.x]\napi_key = "sk-literal"\n[settings]\nmax_turns = 600\n')
        bench.check_config(rep, cfg, "m")
        details = " ".join(d for lvl, _, d in rep.items if lvl == "FAIL")
        self.assertIn("密钥不得写进文件", details)
        self.assertIn("max_turns", details)
        rep = bench.Report()
        cfg.write_text('[providers.x]\napi_key = "${ENV:BENCH_TEST_UNSET_KEY}"\n[models.m]\nprovider="x"\n')
        os.environ.pop("BENCH_TEST_UNSET_KEY", None)
        bench.check_config(rep, cfg, "m")
        self.assertTrue(rep.failed)
        os.environ["BENCH_TEST_UNSET_KEY"] = "v"
        rep = bench.Report()
        bench.check_config(rep, cfg, "m")
        self.assertFalse(rep.failed, rep.text())
        self.assertNotIn("v", [d for _, _, d in rep.items])
        os.environ.pop("BENCH_TEST_UNSET_KEY")

    def test_repo_configs_have_no_keys(self):
        for cfg in (ROOT / "configs").glob("*.toml"):
            rep = bench.Report()
            bench.check_config(rep, cfg, "x")
            self.assertFalse(any("密钥不得写进文件" in d or "max_turns" in d for _, _, d in rep.items), cfg)

    def test_binary_sha(self):
        rep = bench.Report()
        bench.check_binary(rep, "v0.7.64", None)
        self.assertTrue(any(lvl == "OK" and "x86_64" in d for lvl, _, d in rep.items))
        self.assertTrue(any(lvl == "FAIL" and "URL" in n for lvl, n, _ in rep.items))
        rep = bench.Report()
        bench.check_binary(rep, "v9.9.9", "http://x")
        self.assertTrue(rep.failed)

    def test_dataset_count(self):
        rep = bench.Report()
        bench.check_dataset(rep, bench.PROFILES["deepswe"], self.tasks)
        self.assertTrue(any(lvl == "OK" and "113 题" in d for lvl, _, d in rep.items), rep.text())
        self.assertTrue(any("10800" in d for _, _, d in rep.items), rep.text())
        rep = bench.Report()
        (self.tasks / "task-000" / "task.toml").unlink()
        bench.check_dataset(rep, bench.PROFILES["deepswe"], self.tasks)
        self.assertTrue(rep.failed)

    def test_manifest_command_has_no_local_paths(self):
        args = bench.build_parser().parse_args([
            "run", "--benchmark", "deepswe", "--version", "v0.7.64", "--model", "deepseek-flash",
            "--concurrency", "4", "--tasks-dir", str(self.tasks), "--binary-url", "http://binhost.invalid:1/x.tgz",
            "--jobs-root", str(self.t / "jobs")])
        bench.finish_run_args(args)
        jobs = args.jobs_root / "rid"
        launch = bench.build_launch(bench.PROFILES["deepswe"], args, "rid", jobs, "10800")
        text = bench.public_command(launch, args, jobs)
        self.assertNotIn(str(self.t), text)
        self.assertNotIn("binhost.invalid", text)
        self.assertIn("<tasks-dir>", text)
        self.assertIn("<binary-url>", text)


class TestStatusScore(Base):
    def make_job(self, complete=True):
        job = self.t / "jobs" / "rid" / "2026-10-07__01-02-03"
        job.mkdir(parents=True)
        (job / "result.json").write_text("{}")
        n = 113 if complete else 3
        for i in range(n):
            for k in range(4):
                make_trial(job, f"task-{i:03d}", start_min=k * 30, reward=1 if (i + k) % 2 else 0,
                           usage=[{"input_tokens": 100, "cached_input_tokens": 900, "output_tokens": 50}],
                           rng=self.rng)
        if not complete:
            make_trial(job, "task-999", reward=None, finished=False, rng=self.rng)
        return job

    def write_run(self, job):
        rdir = self.t / "runs" / "rid"
        rdir.mkdir(parents=True)
        (rdir / "manifest.json").write_text(json.dumps({
            "run_id": "rid", "rules": "deepswe", "model": "deepseek-flash", "concurrency": 2,
            "binary": {"version": "v0.7.64"}, "finished_at": None}))
        (rdir / "local.json").write_text(json.dumps({"jobs_dir": str(job.parent)}))
        return rdir

    def test_status_partial(self):
        job = self.make_job(complete=False)
        self.write_run(job)
        code, out, err = run(["status", "rid", "--json"])
        self.assertEqual(code, 0, err)
        d = json.loads(out)
        self.assertEqual(d["finished"], 12)
        self.assertEqual(d["expected"], 452)
        self.assertEqual(d["running"], 1)
        self.assertAlmostEqual(d["pass_at_1"], 0.5)
        self.assertIsNotNone(d["eta_s"])
        self.assertIn("steps", d["usage"])
        code, out, _ = run(["status", str(job)])
        self.assertIn("进度 12/452", out)

    def test_score_writes_runs_entry(self):
        job = self.make_job(complete=True)
        rdir = self.write_run(job)
        code, out, err = run(["score", "rid", "--leaderboard-row"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("COMPLIANT", out)
        self.assertIn("PASS@4", out)
        data = json.loads((rdir / "score.json").read_text())
        self.assertTrue(data["score"]["compliant"])
        self.assertAlmostEqual(data["score"]["pass_at_1"], 0.5)
        self.assertIsNotNone(data["usage"]["summary"]["cost_peak_usd"]["mean"])
        self.assertIsNotNone(json.loads((rdir / "manifest.json").read_text())["finished_at"])

    def test_score_non_compliant_exit_2(self):
        job = self.make_job(complete=False)
        code, out, _ = run(["score", str(job.parent), "--benchmark", "deepswe"])
        self.assertEqual(code, 2)
        self.assertIn("NON-COMPLIANT", out)

    def test_two_jobs_refused(self):
        job = self.make_job(complete=False)
        other = job.parent / "2026-10-08__00-00-00"
        make_trial(other, "task-000", rng=self.rng)
        code, _, err = run(["score", str(job.parent)])
        self.assertEqual(code, 2)
        self.assertIn("不得合并", err)


if __name__ == "__main__":
    unittest.main()
