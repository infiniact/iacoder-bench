# iacoder × DeepSWE（Pier 适配器，快照）

来源与更新方法见 [../README.md](../README.md)。

Pier 与 Harbor 的两处生命周期差异是这个适配器存在的原因：

1. 二进制在**镜像构建期**安装（`install_spec()` 内联成 Dockerfile `RUN`），读取 `IACODER_BINARY_URL`；
2. agent 容器在 Squid egress 代理之后，可访问的模型域名由 `network_allowlist()` 声明。

`run.sh` 先用 `resolve-outer-cap.sh` 从任务集的 `task.toml` 推导外层墙钟窗口（任务集不统一时直接拒绝，退出码 4），
再启动 `pier run`。注意 `run.sh` 本身**不传** `-k/--n-attempts`，按评测规则跑每题 4 次时请用
[`../../bench`](../../bench) 的 `run` 子命令，它生成的命令带 `--n-attempts 4`。
