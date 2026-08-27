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

pier run -p <eval-root>/tasks \
         --agent-import-path iacoder_pier:IacoderPierAgent \
         --model glm-5.3 --n-tasks 117 --n-concurrent 2 --force-build
```

## 关键说明

- **模型**：GLM-5.3（智谱 Coding 端点 `api/coding/paas/v4`）；WebSearch 用 `glm-5v`（标准端点，需独立资源包）
- **全自动批准**：`IACODER_DANGEROUSLY_ALLOW_ALL=true`（任务沙箱内危险命令直接放行）
- **二进制**：musl 静态 release 构建，来自 infiniact/iacoder 官方发布
- **断点续跑**：Harbor `--resume <job-dir>` + `--no-teardown`
- **失败归因**：`agent/iacoder.txt` 即标准 trajectory（含 done site / turns / usage）

## 评测结果参考

| 基准 | 配置 | 通过率 |
|------|------|--------|
| DeepSWE V1.1 (117 任务) | iacoder × glm-5.3，多轮重跑累计 | 97/117 = 82.9% |
| Terminal-Bench 2.1 (89 任务) | iacoder × glm-5.3, `-k 5` | 进行中（见 results/） |

© 2026 INFINIACT CO., LTD.  Agent engine: IACoder
