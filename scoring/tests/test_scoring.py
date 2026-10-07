"""scoring/ 的单测：站点榜单口径（pooled、按 attempt 分轮 ±、显示精度）、论文宏平均（补充）、pass@k、剔除、合规退出码、usage 统计、榜单行。

运行：python3 -m unittest discover -s scoring/tests   或   uv run --with pytest pytest scoring/tests
"""
from __future__ import annotations

import io
import json
import math
import random
import statistics
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import leaderboard_row  # noqa: E402
import score  # noqa: E402
import usage  # noqa: E402
from jobfixture import make_trial, write_job_result  # noqa: E402

PRICING = HERE.parent / "pricing" / "deepseek-flash.toml"


class JobCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name) / "2026-10-07__00-00-00"
        self.job.mkdir()
        write_job_result(self.job)
        self.rng = random.Random(7)

    def tearDown(self):
        self.tmp.cleanup()

    def trial(self, task, **kw):
        return make_trial(self.job, task, rng=self.rng, **kw)

    def run_main(self, mod, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = mod.main(args)
        return code, buf.getvalue()


class TestScore(JobCase):
    def build_two_tasks(self):
        # 题 a：按开始时间 通过、失败、通过、通过（3/4）；题 b：失败、失败、失败、通过（1/4）
        for i, r in enumerate([1, 0, 1, 1]):
            self.trial("task-a", start_min=10 * i, reward=r)
        # 故意乱序写入，验证按 start time 排序
        for i, r in reversed(list(enumerate([0, 0, 0, 1]))):
            self.trial("task-b", start_min=10 * i + 1, reward=r)

    def test_pooled_rounds_and_ci(self):
        self.build_two_tasks()
        r = score.score_job(self.job, min_trials=4, expected_tasks=2)
        self.assertTrue(r["compliant"], r["problems"])
        self.assertEqual(r["primary_method"], "deepswe-site-leaderboard-v1.1")
        self.assertAlmostEqual(r["pass_at_1"], 4 / 8)
        self.assertEqual((r["n_passed"], r["n_scored"]), (4, 8))
        self.assertEqual(r["rounds"], [0.5, 0.0, 0.5, 1.0])
        self.assertEqual(r["round_scored_counts"], [2, 2, 2, 2])
        se = statistics.stdev([0.5, 0.0, 0.5, 1.0]) / math.sqrt(4)  # 样本标准差，与榜单 ci_half 一致
        self.assertAlmostEqual(r["se"], se)
        self.assertAlmostEqual(r["ci95_half_width"], 1.96 * se)
        self.assertAlmostEqual(r["ci95_lo"], 0.5 - 1.96 * se)
        self.assertEqual(r["pass_at_k"], 1.0)
        self.assertEqual(r["site_display"], {"pass_at_1": "50%", "ci95": "±40%"})

    def test_pooled_primary_macro_supplementary(self):
        # a: 1/1 计分（另 3 个判分错误剔除），b: 0/4 → 主分数 pooled 0.2，论文宏平均 0.5 只作补充
        self.trial("a", reward=1)
        for i in range(3):
            self.trial("a", start_min=i + 1, reward=None, exception="VerifierTimeoutError")
        for i in range(4):
            self.trial("b", start_min=i, reward=0)
        r = score.score_job(self.job, 4, 2)
        self.assertAlmostEqual(r["pass_at_1"], 0.2)
        self.assertAlmostEqual(r["pass_at_1_pooled"], 0.2)
        self.assertAlmostEqual(r["pass_at_1_macro"], 0.5)
        self.assertEqual(r["trials"]["excluded"], 3)
        self.assertTrue(r["compliant"], "剔除不重采样：已完成次数达标即合规")
        self.assertEqual(r["pass_at_k"], 0.5)

    def test_excluded_attempt_keeps_its_round(self):
        # a 的第 1 次被剔除：后面的 attempt 不前移，第 1 轮只有 b
        self.trial("a", start_min=0, reward=None, exception="VerifierTimeoutError")
        for i in range(1, 4):
            self.trial("a", start_min=i, reward=1)
        for i in range(4):
            self.trial("b", start_min=i, reward=0)
        r = score.score_job(self.job, 4, 2)
        self.assertEqual(r["rounds"], [0.0, 0.5, 0.5, 0.5])
        self.assertEqual(r["round_scored_counts"], [1, 2, 2, 2])
        self.assertAlmostEqual(r["pass_at_1"], 3 / 7)

    def test_site_display_rounding(self):
        self.assertEqual(score.site_pct(318 / 449), "71%")
        self.assertEqual(score.site_pct(0.7064), "71%")
        self.assertEqual(score.site_ci(0.025), "±3%")      # Math.round：半数向上
        self.assertEqual(score.site_ci(0.0249), "±2%")
        self.assertEqual(score.site_ci(0.02865), "±3%")    # 榜单 gpt-6-astra xhigh：ci_half 0.02865 → ±3%
        self.assertEqual(usage.fmt_tokens(186_969), "187k")
        self.assertEqual(usage.fmt_tokens(29_557), "30k")   # 榜单显示 30k
        self.assertEqual(usage.fmt_tokens(5_432), "5.4k")
        self.assertEqual(usage.fmt_tokens(1_234_567), "1.2M")
        self.assertEqual(usage.fmt_cost(0.45414), "$0.45")
        self.assertEqual(usage.fmt_cost(26.3999), "$26.40")  # 榜单显示 $26.40
        self.assertEqual(usage.fmt_steps(162.9), "163")
        self.assertEqual(usage.fmt_steps(28.75), "29")

    def test_failures_and_sensitivity(self):
        self.trial("a", start_min=0, reward=1)
        self.trial("a", start_min=1, reward=0, exception="AgentTimeoutError")
        self.trial("a", start_min=2, reward=1, exception="AgentTimeoutError")  # 超时即使 reward=1 也计失败
        self.trial("a", start_min=3, reward=None, exception="NonZeroAgentExitCodeError")  # 可疑
        r = score.score_job(self.job, 4, 1)
        self.assertAlmostEqual(r["pass_at_1"], 0.25)
        self.assertEqual(r["suspect_infra_trials"], 1)
        self.assertAlmostEqual(r["sensitivity_pass_at_1_excluding_suspect"], 1 / 3)
        self.assertEqual(r["reward_one_with_exception"], 1)
        self.assertEqual(r["exception_types"]["AgentTimeoutError"], 2)

    def test_non_compliant_exit_2(self):
        self.build_two_tasks()
        code, out = self.run_main(score, [str(self.job), "--min-trials", "5", "--expected-tasks", "2"])
        self.assertEqual(code, 2)
        self.assertIn("NON-COMPLIANT", out)
        code, _ = self.run_main(score, [str(self.job), "--min-trials", "4", "--expected-tasks", "3"])
        self.assertEqual(code, 2)
        code, out = self.run_main(score, [str(self.job), "--min-trials", "4", "--expected-tasks", "2"])
        self.assertEqual(code, 0, out)

    def test_running_trial_non_compliant(self):
        self.build_two_tasks()
        self.trial("task-a", start_min=50, reward=None, finished=False)
        r = score.score_job(self.job, 4, 2)
        self.assertFalse(r["compliant"])
        self.assertEqual(r["trials"]["running"], 1)

    def test_overrides_non_compliant(self):
        for i in range(4):
            self.trial("a", start_min=i, reward=1,
                       config={"timeout_multiplier": 2.0} if i == 0 else None)
        r = score.score_job(self.job, 4, 1)
        self.assertFalse(r["compliant"])
        for i in range(4):
            self.trial("b", start_min=i, reward=1, config={"environment": {"override_cpus": 8}})
        self.assertTrue(any("override_cpus" in d for d in score.score_job(self.job, 4, 2)["problem_details"]))

    def test_mixed_models_non_compliant(self):
        for i in range(4):
            self.trial("a", start_min=i, reward=1, model="m1" if i else "m2")
        self.assertFalse(score.score_job(self.job, 4, 1)["compliant"])

    def test_refuses_multiple_jobs_and_empty(self):
        code, _ = self.run_main(score, [str(self.job), str(self.job), "--min-trials", "4"])
        self.assertEqual(code, 2)
        code, out = self.run_main(score, [str(self.job), "--min-trials", "4"])
        self.assertEqual(code, 2)
        self.assertIn("没有找到 trial", out)

    def test_json_and_out_file(self):
        self.build_two_tasks()
        out_file = Path(self.tmp.name) / "runs" / "x" / "score.json"
        code, out = self.run_main(score, [str(self.job), "--min-trials", "4", "--expected-tasks", "2",
                                          "--json", "--out", str(out_file)])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(json.loads(out_file.read_text())["pass_at_1"], data["pass_at_1"])
        self.assertEqual(data["k"], 4)


class TestTbench4(JobCase):
    def test_pooled_se_and_no_exclusion(self):
        # a: 5 次，其中 reward 0.5 也算成功（官方 reward>0），1 次判分错误按 0 计
        for i, (r, e) in enumerate([(1, None), (0.5, None), (0, None), (None, "VerifierTimeoutError"),
                                    (1, "AgentTimeoutError")]):
            self.trial("a", start_min=i, reward=r, exception=e)
        for i in range(5):
            self.trial("b", start_min=i, reward=0)
        r = score.score_job(self.job, 5, 2, rules="tbench4")
        self.assertTrue(r["compliant"], r["problems"])
        self.assertEqual(r["trials"]["excluded"], 0)
        self.assertAlmostEqual(r["pass_at_1"], 3 / 10)
        pa = 3 / 5
        self.assertAlmostEqual(r["se"], math.sqrt((pa * (1 - pa) / 4) / 4))
        self.assertAlmostEqual(r["pass_at_5"], 0.5)
        self.assertAlmostEqual(r["pass_at_2"], (1 - math.comb(2, 2) / math.comb(5, 2)) / 2)

    def test_tbench_needs_all_tasks(self):
        for i in range(5):
            self.trial("a", start_min=i, reward=1)
        code, out = self.run_main(score, [str(self.job), "--rules", "tbench4", "--min-trials", "5",
                                          "--expected-tasks", "66"])
        self.assertEqual(code, 2)
        self.assertIn("应为 66", out)


class TestUsage(JobCase):
    def test_session_usage_and_pricing(self):
        sat = datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)   # 周六 02:00 → 非高峰
        mon = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)  # 周一 02:00 → 高峰（非节假日）
        self.trial("a", reward=1, minutes=30, usage=[
            {"input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 0, "ts": sat},
            {"input_tokens": 0, "cached_input_tokens": 1_000_000, "output_tokens": 1_000_000, "ts": mon},
        ])
        self.trial("a", start_min=40, reward=0, minutes=10, usage=[
            {"input_tokens": 10, "cached_input_tokens": 20, "output_tokens": 30, "ts": sat},
            {"input_tokens": 10, "cached_input_tokens": 20, "output_tokens": 30, "ts": sat},
            {"input_tokens": 10, "cached_input_tokens": 20, "output_tokens": 30, "ts": sat},
            {"input_tokens": 10, "cached_input_tokens": 20, "output_tokens": 30, "ts": sat},
        ])
        # 判分错误被剔除，不进统计
        self.trial("a", start_min=60, reward=None, exception="VerifierTimeoutError",
                   usage=[{"input_tokens": 999, "cached_input_tokens": 0, "output_tokens": 999}])
        r = usage.usage_job(self.job, PRICING)
        s = r["summary"]
        self.assertEqual(s["n_trials"], 2)
        self.assertEqual(s["steps"]["mean"], 3)
        self.assertEqual(s["output_tokens"]["median"], (1_000_000 + 120) / 2)
        first = sorted(r["trials"], key=lambda t: -t["output_tokens"])[0]
        self.assertAlmostEqual(first["cost_usd"], 0.15 + 0.006 + 1.20)  # 非高峰未命中 + 高峰命中 + 高峰输出
        self.assertAlmostEqual(first["cost_peak_usd"], 0.30 + 0.006 + 1.20)
        self.assertEqual(first["source"], "session.jsonl")
        self.assertAlmostEqual(s["agent_wall_s"]["median"], ((28 + 8) / 2) * 60)
        self.assertEqual(usage.usage_job(self.job, PRICING, include_all=True)["summary"]["n_trials"], 3)
        first_line = usage.format_text(r).splitlines()[0]
        self.assertIn("站点榜单口径（主）", first_line)
        self.assertIn(usage.fmt_cost(s["cost_peak_usd"]["mean"]), first_line)  # 均值、高峰价
        self.assertIn("Steps 3", first_line)

    def test_fallback_agent_result(self):
        self.trial("a", reward=1, agent_result={"n_input_tokens": 1000, "n_cache_tokens": 400,
                                                "n_output_tokens": 50, "cost_usd": 0.5, "n_agent_steps": 7})
        row = usage.usage_job(self.job, PRICING)["trials"][0]
        self.assertEqual(row["source"], "result.json")
        self.assertEqual(row["input_tokens"], 600)
        self.assertEqual(row["cost_usd"], 0.5)
        self.assertEqual(row["steps"], 7)

    def test_pricing_todo_gives_no_cost(self):
        self.trial("a", reward=1, usage=[{"input_tokens": 1, "cached_input_tokens": 1, "output_tokens": 1}])
        r = usage.usage_job(self.job, HERE.parent / "pricing" / "glm-5.3.toml")
        self.assertIsNone(r["summary"]["cost_usd"]["mean"])
        self.assertEqual(r["summary"]["steps"]["mean"], 1)

    def test_peak_window_edges(self):
        p = usage.Pricing.load(PRICING)
        tue = lambda h: datetime(2026, 10, 13, h, 0, tzinfo=timezone.utc)
        self.assertIs(p.rates(tue(1)), p.peak)
        self.assertIs(p.rates(tue(4)), p.off)
        self.assertIs(p.rates(tue(5)), p.off)
        self.assertIs(p.rates(tue(9)), p.peak)
        self.assertIs(p.rates(tue(10)), p.off)

    def test_holiday_dates_are_off_peak(self):
        p = usage.Pricing.load(PRICING)
        self.assertIn("2026-10-07", p.off_dates)  # 顶层键真的被读到
        self.assertIs(p.rates(datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc)), p.off)  # 周三、国庆假期
        self.assertIs(p.rates(datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)), p.peak)

    def test_off_peak_dates_inside_peak_hours_table_still_read(self):
        p = usage.Pricing({"peak": {"output": 2}, "off_peak": {"output": 1},
                           "peak_hours": {"weekdays": [2], "windows": [[1, 4]], "off_peak_dates": ["2026-10-14"]}})
        self.assertIs(p.rates(datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)), p.off)


class TestLeaderboardRow(JobCase):
    def test_row(self):
        for i, r in enumerate([1, 0, 1, 1]):
            self.trial("a", start_min=i, reward=r, usage=[
                {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 1000}])
        code, out = self.run_main(leaderboard_row, [str(self.job), "--min-trials", "4", "--expected-tasks", "1",
                                                    "--pricing", str(PRICING), "--label", "x"])
        self.assertEqual(code, 0)
        self.assertIn("| x | 75% | ±49% | $0.00", out)
        self.assertIn("站点榜单口径（主）", out)
        self.assertIn("PASS@4", out)
        self.assertIn("| 1.0k | 1 |", out)
        self.assertIn("论文宏平均 pass@1 75.0%", out)
        code, out = self.run_main(leaderboard_row, [str(self.job), "--min-trials", "4", "--expected-tasks", "1",
                                                    "--pricing", str(PRICING), "--format", "line"])
        self.assertEqual(code, 0)
        self.assertRegex(out.strip(), r"^75% ±49%  \$0\.\d+  1\.0k  1$")
        code, out = self.run_main(leaderboard_row, [str(self.job), "--min-trials", "5", "--format", "json"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["compliant"])


if __name__ == "__main__":
    unittest.main()
