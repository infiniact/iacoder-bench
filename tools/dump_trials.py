#!/usr/bin/env python3
"""把一个或多个 job 目录的逐 trial 数据导出为 JSON：结果、异常、agent 分钟、token、调用数、成本。

内部诊断工具，输出不得进入公开成绩；公开成绩只用 scoring/。

用法：
    python3 tools/dump_trials.py <job-dir> [<job-dir> ...] [--pricing scoring/pricing/deepseek-flash.toml] > trials.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import trial_rows  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("jobs", nargs="+", type=Path)
    p.add_argument("--pricing", type=Path)
    a = p.parse_args(argv)
    out = {str(j): trial_rows(j, a.pricing) for j in a.jobs}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
