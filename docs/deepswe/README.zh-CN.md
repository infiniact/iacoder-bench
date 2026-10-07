# DeepSWE

[English](README.md) | **简体中文** · [返回 Bench](../../README.zh-CN.md)

[DeepSWE](https://deepswe.datacurve.ai/)（Datacurve）用 Pier 在 113 道题上评测软件工程 agent。本页是 IACoder 的
DeepSWE 结果与复现方法。IACoder 是 INFINIACT CO., LTD.（以下简称"INFINIACT"）开发的 AI agent 引擎。

## 规则摘要

完整条文、原文引用与来源见 [RULES.md](../../RULES.md)（抓取日期 2026-10-07）。

| 项目 | 规则 |
|---|---|
| 题数 | 113（数据集 commit `0b9fabbb`） |
| 每题次数 | 约 4 次；单个 job，不合并、不取优 |
| 时限 | 以各题 `task.toml` 为准，倍率 1.0；不设步数与费用上限 |
| Pass@1（站点榜单 v1.1） | pooled：通过的计分 attempt / 计分 attempt 总数 |
| ± | 95% run-to-run：各题 attempt 按开始时间排成 s1..sR 轮，1.96·std(s1..sR)/√R |
| 平均成本 / 输出 token / 步数 | 所有计分 attempt 的均值；成本按高峰价 |
| 剔除 | 服务商、判分、瞬态网络错误剔除，不重采样；agent 超时与上下文耗尽计失败 |
| 提交 | 联系 Datacurve（[Run DeepSWE](https://deepswe.datacurve.ai/run)） |

本仓库另外规定：所有评测关闭网页工具；服务商错误或网络错误无法与其他失败区分时一律计失败（只会让分数偏低）。

## 结果

**IACoder v0.7.64 + deepseek-flash [max]**：DeepSeek 官方端点，113 题 × 4 次，单个 job，合规（COMPLIANT）。

| 模型 | Pass@1 | ± | Pass@4 | 平均成本 | 输出 token | 步数 |
|---|---|---|---|---|---|---|
| IACoder v0.7.64 + deepseek-flash [max] | **71%** | **±2%** | 91.2% | $0.454 | 187k | 163 |

| 明细 | 数值 |
|---|---|
| Pass@1，pooled（主分数） | 70.82%（318 / 449 个计分 attempt） |
| 95% 半宽 | 2.30 个百分点；4 轮分别为 70.54% / 70.80% / 68.14% / 73.87% |
| Pass@1，宏平均（论文 §5.3，补充） | 70.65% |
| attempt | 共 452：通过 318、失败 131、剔除 3（判分超时，不重采样） |
| 每 attempt 中位数 | 成本 $0.421（高峰价）/ $0.210（实际计费），输出 178k token，154 步，25.3 分钟 |

状态：已具备提交条件，待发送给 Datacurve；**尚未进入官方榜单**。
文件：[结果](../../results/deepswe-v1.1-iacoder-v0.7.64-deepseek-flash-2026-10-07.md) ·
[运行档案](../../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/) ·
[提交摘要](../deepswe-submission.md) · [二进制](../../bin/v0.7.64/) · [harness](../../harness/)。

## 复现

```bash
./bench preflight --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench run       --benchmark deepswe --version v0.7.64 --model deepseek-flash --concurrency 16 --tasks-dir <tasks-dir>
./bench status <run-id> --watch 60
./bench score  <run-id> --leaderboard-row
```

`./bench run` 只会生成合规命令（`-k 4`、`--timeout-multiplier 1.0`、不重试、关闭网页工具）。
计分见 [scoring/README.md](../../scoring/README.md)，方法见 [docs/methodology.md](../methodology.md)。
