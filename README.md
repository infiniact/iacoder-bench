# bench

**IACoder**（INFINIACT 的 AI agent 引擎）在 DeepSWE 与 Terminal-Bench 上的评测仓库：
二进制、评测适配器快照、计分代码、复现步骤与每轮运行档案。

## 规则摘要

完整规则、原文与来源见 [RULES.md](RULES.md)（抓取日期 2026-10-07）。

| | DeepSWE | Terminal-Bench 4.0 |
|---|---|---|
| 题数 | 113（数据集 commit `0b9fabbb`） | 66，全覆盖，不接受子集 |
| 每题次数 | 4 | ≥ 5 |
| 时限 | 以 `task.toml` 为准，倍率 1.0，不覆盖；无步数 / 费用上限 | 统一 8 小时，倍率 1.0，不覆盖超时与资源 |
| 主指标 | pass@1 宏平均 ± 1.96·SE（按轮）、pass@4 | accuracy（pooled）± 1.96·SE（按题公式） |
| 出错的 trial | 服务商 / 判分 / 瞬态网络错误剔除，不重采样；超时、上下文耗尽计失败 | 一律记 0，不剔除 |
| 提交 | 联系 Datacurve | 社区提交目前关闭 |

本仓库附加规定：所有评测关闭网页工具（`--no-web`）；只发布严格合规的结果；不拼接、不取优、不重采样。

## 已发布结果

**目前没有符合规则的已发布结果。** 准入条件见 [results/README.md](results/README.md)。

> 撤回说明（2026-10-05）：此前列出的 DeepSWE「97/117 = 82.9%」已撤下——它由多轮重跑累计得出，不是 pass@1，
> 且运行时开启了网页搜索。详见 [docs/withdrawn-2026-10-05.md](docs/withdrawn-2026-10-05.md)。

## 快速开始

```bash
./bench preflight --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench run       --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench status <run-id> --watch 60
./bench score  <run-id> --leaderboard-row
```

Terminal-Bench 4.0 把 `--benchmark deepswe … --tasks-dir <tasks-dir>` 换成 `--benchmark tbench`。
准备工作（主机、密钥、二进制 URL、镜像）与完整流程见 [docs/quickstart.md](docs/quickstart.md)。

## 目录

```
bench/
├── bench, bench.py  # 统一入口：preflight / run / status / score
├── RULES.md         # 评测组织规则 + 本仓库附加规定 + 变更记录
├── bin/<version>/   # iacoder 预编译二进制（官方 release，带 SHA256SUMS）
├── harness/         # iacoder 评测适配器快照（iacoder@v0.7.64，iacoder 源码不公开）
├── configs/         # 无密钥的 iacoder 评测配置模板
├── scoring/         # 唯一计分入口 score.py + usage.py + leaderboard_row.py + 单测
├── runs/            # 每轮运行档案（manifest / score / report）
├── results/         # 只放合规的对外结果
├── reproduce/       # 复现环境的通用步骤与工具（镜像缓存、版本锁）
├── tools/           # 通用分析工具（内部诊断，不进公开成绩）
├── tests/           # bench 的单测
└── docs/            # 快速开始、方法论、撤回记录
```

## 测试

```bash
python3 -m unittest discover -s scoring/tests && python3 -m unittest discover -s tests
```

© 2026 INFINIACT CO., LTD.  Agent engine: IACoder
