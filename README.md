# bench

> IACoder 评测相关信息：二进制、评测脚本、评测输出与评测排名。

# IACoder Bench

**IACoder**（INFINIACT 出品的 AI agent 引擎）的评测工具集：DeepSWE 与 Terminal-Bench 2.1 的适配器、启动/分析脚本、预编译二进制与结果工具。

## 目录结构

```
bench/
├── adapters/
│   ├── deep-swe/         # DeepSWE (Pier) 适配器: iacoder_pier.IacoderPierAgent
│   └── terminal-bench/   # Terminal-Bench (Harbor) 适配器: iacoder_tbench.Iacoder
├── bin/                  # 预编译二进制 (musl static, release)
├── scripts/              # 启动 / 聚合 / 指标脚本
├── results/              # 评测结果（稍后发布）
└── docs/                 # 文档
```

## 快速开始

### Terminal-Bench 2.1（Harbor）

```bash
# 适配器依赖 iacoder_harbor_base（位于 iacoder 仓库 eval/common）
export PYTHONPATH=/opt/iacoder/eval/terminal-bench:/opt/iacoder/eval/common
export IACODER_DEV_BINARY_URL=http://<host>:8765/iacoder-x86_64-unknown-linux-musl.tar.gz

harbor run -d terminal-bench/terminal-bench-2-1 \
           --agent iacoder_tbench:Iacoder \
           -m glm-5.3 -k 5 -n 2 \
           --ae IACODER_DANGEROUSLY_ALLOW_ALL=true \
           --no-teardown
```

### DeepSWE（Pier）

```bash
export PYTHONPATH=/opt/iacoder/eval/deep-swe:/opt/iacoder/eval/common
export IACODER_BINARY_URL=http://<host>:8765/iacoder-x86_64-unknown-linux-musl.tar.gz

# 官方 113 题；每题 4 次 rollout；超时以 task.toml 为准（倍率 1.0）
pier run -p <eval-root>/tasks \
         --agent-import-path iacoder_pier:IacoderPierAgent \
         --model glm-5.3 -k 4 --timeout-multiplier 1.0 \
         --n-concurrent 2 --force-build
```

## 关键说明

- **模型**：GLM-5.3（智谱 Coding 端点 `api/coding/paas/v4`）
- **网页工具**：评测一律关闭（`--no-web`），agent 不做任何网络搜索
- **全自动批准**：`IACODER_DANGEROUSLY_ALLOW_ALL=true`（任务沙箱内危险命令直接放行）
- **二进制**：musl 静态 release 构建，来自 infiniact/iacoder 官方发布
- **断点续跑**：Harbor `--resume <job-dir>` + `--no-teardown`；只续跑未完成的 trial，绝不重跑已完成的 trial 去替换成绩
- **失败归因**：`agent/iacoder.txt` 即标准 trajectory（含 done site / turns / usage）

## 评测规则（遵循评测组织的官方规定）

### DeepSWE（Datacurve）

- 框架为 Pier；每题约 4 次 rollout；指标 pass@1 为各题通过比例（通过次数 / 计分 rollout 数）的宏平均。
- 单次 rollout 的时限以官方 `task.toml` 为准（当前为 10800 秒），超时倍率 1.0；不设步数或费用上限。
- rollout 在提交补丁、耗尽模型原生上下文或超时时结束；后两种情况计为失败。
- 只有模型服务错误、判分错误、瞬态网络错误可以排除，排除时同时从分子和分母剔除，且不重新采样。
- 不得拼接多次重跑的结果，也不得取多次中的最好一次。
- 提交方式：联系 Datacurve，由对方添加结果。
- 来源：[DeepSWE 论文 §5](https://arxiv.org/html/2607.07946v1)、[Run DeepSWE](https://deepswe.datacurve.ai/run)

### Terminal-Bench 2.x（Harbor）

- 每题至少 5 次试验（`-k 5`）；`timeout_multiplier` 必须为 1.0；不得覆盖 agent 或 verifier 超时，不得覆盖 CPU、内存、存储资源。
- 每个 trial 须有有效的 `result.json` 和运行产物；提交完整 job 目录和 `metadata.yaml`。
- agent 不得访问 Terminal-Bench 网站或其 GitHub 仓库。
- 现状：2.0 社区提交暂停，等待新流程；2.1 目前只收录维护者运行的结果。
- 来源：[Terminal-Bench 2.0 leaderboard](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard)、[Terminal-Bench 2.1](https://github.com/harbor-framework/terminal-bench-2-1)

### 本仓库的附加规定

- 所有评测关闭网页工具。
- 只发布严格按上述规则产出的结果，并保留可复查的原始 job 目录。

## 评测结果

目前没有符合上述规则的已发布结果。

> **撤回说明（2026-10-05）**：此前此处列出的 DeepSWE「97/117 = 82.9%」已撤下。该数字由多轮重跑累计得出，不是 pass@1；运行时还开启了网页搜索。两点都不符合评测组织的规则。

© 2026 INFINIACT CO., LTD.  Agent engine: IACoder
