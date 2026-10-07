# Bench

[English](README.md) | **简体中文**

**Bench** 是 **IACoder** 的评测仓库。IACoder 是 **INFINIACT CO., LTD.**（以下简称"**INFINIACT**"）开发的 AI agent 引擎。
本仓库目前支持 **DeepSWE** 与 **Terminal-Bench** 两个评测，后续可扩展更多评测。

核对一个已发布数字所需的一切都在这里：IACoder 二进制、评测适配器快照、计分代码、复现步骤和每一轮运行的档案。

## 评测

| 评测 | 状态 | 页面 |
|---|---|---|
| DeepSWE（Datacurve） | **1 个可发布结果** | [DeepSWE](docs/deepswe/README.zh-CN.md) |
| Terminal-Bench 4.0 | 仅内部评测（社区提交目前关闭） | [Terminal-Bench](docs/terminal-bench/README.zh-CN.md) |

## 已发布结果

| 评测 | agent + 模型 | Pass@1 | ±（95%） | Pass@4 | 平均成本 | 输出 token | 步数 | 状态 |
|---|---|---|---|---|---|---|---|---|
| DeepSWE v1.1 | IACoder v0.7.64 + deepseek-flash [max] | **71%**（70.82%） | ±2%（2.30） | 91.2% | $0.454 | 187k | 163 | 已具备提交条件，待发送给 Datacurve；尚未进入官方榜单 |

计分口径与 DeepSWE 站点榜单一致：Pass@1 按计分 attempt 合并计算（pooled），± 为 95% run-to-run 区间，
成本、输出 token、步数取每个 attempt 的均值，成本按高峰价。详见 [DeepSWE 页面](docs/deepswe/README.zh-CN.md) ·
[结果文件](results/deepswe-v1.1-iacoder-v0.7.64-deepseek-flash-2026-10-07.md) ·
[运行档案](runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/)。

本仓库只发布严格遵守评测组织规则的结果，见 [RULES.md](RULES.md) 与 [results/README.md](results/README.md)。
所有评测都关闭网页工具，不拼接、不取优、不重采样。

> **撤回说明（2026-10-05）**：此前列出的 DeepSWE「97/117 = 82.9%」由多轮重跑累计得出，不是 pass@1，
> 且运行时开启了网页搜索，已撤下。详见 [docs/withdrawn-2026-10-05.md](docs/withdrawn-2026-10-05.md)。

## 快速开始

```bash
./bench preflight --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench run       --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench status <run-id> --watch 60
./bench score  <run-id> --leaderboard-row
```

Terminal-Bench 用 `--benchmark tbench`。主机准备、密钥、二进制 URL、镜像见 [docs/quickstart.md](docs/quickstart.md)。

## 目录

```
bench/
├── bench, bench.py  # 统一入口：preflight / run / status / score
├── RULES.md         # 评测组织规则、本仓库附加规定、变更记录
├── bin/<version>/   # IACoder 发布版二进制，带 SHA256SUMS
├── harness/         # IACoder 评测适配器快照
├── configs/         # 无密钥的 IACoder 评测配置模板
├── scoring/         # 唯一计分入口（score.py、usage.py、leaderboard_row.py）与单测
├── runs/            # 每轮运行档案（manifest / score / report / artifacts）
├── results/         # 只放合规的对外结果
├── reproduce/       # 通用环境准备步骤与工具（镜像缓存、版本锁）
├── tools/           # 分析工具（内部诊断，不进入公开成绩）
├── tests/           # bench 自身的单测
└── docs/            # 各评测页面、快速开始、方法论、撤回记录
```

新增一个评测：在 `docs/<评测>/` 下加页面（中英文各一份），在 RULES.md 写规则，在 `scoring/score.py` 加一个
`--rules` 口径，并放入 harness 快照。

## 测试

```bash
python3 -m unittest discover -s scoring/tests && python3 -m unittest discover -s tests
```

© 2026 INFINIACT CO., LTD.（"INFINIACT"）。IACoder 由 INFINIACT 开发。
