# iacoder × Terminal-Bench 2.x（Harbor 适配器，快照）

来源与更新方法见 [../README.md](../README.md)。

`iacoder_tbench/agent.py` 只声明 bench 身份；安装与运行逻辑全部来自 `../common/iacoder_harbor_base`。
Terminal-Bench 各题 `task.toml` 的超时不统一，所以适配器不声明统一的外层上限，以 Harbor 按题下发的超时为准。

`run-tbench.sh` 是上游的本地开发脚本（单题调试、oracle 对照），会尝试从 iacoder 源码目录构建二进制，
在本仓库里只作参考；按评测规则的全量运行请用 [`../../bench`](../../bench) 的 `run` 子命令（`-k 5`、`timeout_multiplier=1.0`）。
