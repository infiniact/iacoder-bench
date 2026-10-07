# Terminal-Bench

[English](README.md) | **简体中文** · [返回 Bench](../../README.zh-CN.md)

[Terminal-Bench](https://www.tbench.ai/) 用 Harbor 在终端环境里评测 agent 完成高难任务的能力，当前版本是 **4.0**（66 题）。
本页是 IACoder 的 Terminal-Bench 状态与运行方法。IACoder 是 INFINIACT CO., LTD.（以下简称"INFINIACT"）开发的 AI agent 引擎。

## 规则摘要（4.0）

完整条文、原文引用与来源见 [RULES.md](../../RULES.md)（抓取日期 2026-10-07；2.x 规则作为历史版本保留在那里）。

| 项目 | 规则 |
|---|---|
| 数据集 | `terminal-bench/terminal-bench@4.0.0`，66 题全覆盖（其中 3 题需要 H100 GPU）；不接受子集 |
| 每题次数 | 至少 5 次 |
| 限制 | agent 时限 8 小时；`timeout_multiplier` 不设或为 1.0；不得覆盖超时和资源 |
| 分数 | accuracy = 成功 trial / 全部 trial（成功：reward > 0），± 1.96·SE（按题公式） |
| 出错 | 出错的 trial 记 0，不剔除 |
| 提交 | 把 job 上传到 Harbor Hub（公开可读）并附 ATIF 轨迹，再向排行榜开 PR |
| 现状 | **社区提交目前关闭**，排行榜只收维护者自己跑的结果 |

## 现状

目前还没有发布 Terminal-Bench 成绩。IACoder 在内部跑 Terminal-Bench 4.0，内部运行不作为公开成绩。

为将来提交已经准备好的部分：
- job 按注册表引用数据集（`terminal-bench/terminal-bench@4.0.0`），agent 名为 `iacoder`。
- harness 为每个 trial 生成 ATIF 轨迹（`agent/trajectory.json`），格式与 Harbor 内置的 Claude Code / Codex 适配器对齐。
- 63 道 CPU 题和 3 道 GPU 题分成两个 job，使用同一个二进制、模型与推理强度（Harbor 的 docker 环境无法启动 GPU 题，
  一道 GPU 题会让整个 job 中止）。

## 运行

```bash
./bench preflight --benchmark tbench --version <version> --model deepseek-flash --concurrency 12
./bench run       --benchmark tbench --version <version> --model deepseek-flash --concurrency 12
./bench status <run-id> --watch 60
./bench score  <run-id>
```

主机准备与镜像缓存见 [docs/quickstart.md](../quickstart.md) 与 [reproduce/](../../reproduce/)。
