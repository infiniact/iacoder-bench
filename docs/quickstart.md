# 快速开始：准备 → preflight → run → status → score → 归档

`./bench` 是唯一入口（Python 3.11+ 标准库，无第三方依赖）。它只会生成**合规命令**：
DeepSWE 全部 113 题、每题 4 次；Terminal-Bench 4.0 全部 66 题、每题 5 次；`--timeout-multiplier 1.0`、`--max-retries 0`、
不覆盖超时与资源、不设步数上限，网页工具由 harness 写死关闭。没有子集、续跑替换、取优或拼接的选项。
每个子命令的完整参数见 `./bench <子命令> --help`。规则见 [RULES.md](../RULES.md)。

## 0. 准备

按 [reproduce/host-setup.md](../reproduce/host-setup.md) 完成：

1. Docker（daemon.json 地址池、日志）与 buildx 插件（`docker buildx version` 必须可用）；
2. runner：DeepSWE 用 `pier`（`datacurve-pier==0.3.1`，按 `reproduce/pier-constraints.txt` 安装），TB4 用 `harbor`；
   不在 PATH 时设 `PIER_BIN` / `HARBOR_BIN` 或传 `--runner-bin`；
3. 数据集：DeepSWE 克隆 `datacurve-ai/deep-swe` 并 checkout `0b9fabbb…`，`--tasks-dir` 指向其中的 `tasks/`；
   TB4 由 harbor 拉取 `terminal-bench/terminal-bench@4.0.0`；
4. 镜像：用 `reproduce/imgcache` 预拉或恢复，避免首波撞 registry 限流；
5. 二进制 URL：在 Docker 网桥地址上起静态文件服务，提供 `bin/v0.7.64/iacoder-x86_64-unknown-linux-musl.tar.gz`；
6. 密钥：只导出环境变量，例如 `export DEEPSEEK_API_KEY=…`；配置用 `configs/deepseek-flash.toml`（默认按 `--model` 选 `configs/<model>.toml`）。

## 1. preflight

```bash
export IACODER_BINARY_URL=http://<docker-bridge-ip>:<port>/iacoder-x86_64-unknown-linux-musl.tar.gz
./bench preflight --benchmark deepswe --version v0.7.64 --model deepseek-flash \
    --concurrency 16 --tasks-dir <eval-root>/deep-swe/tasks \
    --agent-env IACODER_PROMPT_TEMPLATE=mini-swe-agent-custom --agent-env IACODER_REASONING_EFFORT=max
```

逐项输出 `OK / WARN / FAIL`，有任一 FAIL 退出码 1：

| 项 | 硬性（FAIL） | 提示（WARN） |
|---|---|---|
| 二进制 | `bin/<ver>/SHA256SUMS` 校验失败；二进制 URL 下载不到或 sha256 不在清单中 | `--no-binary-fetch` 跳过下载 |
| harness | 快照缺文件 | |
| 配置 | `api_key` 不是 `${ENV:VAR}`；变量未设置；`max_turns` 为正数 | 模型名不在 `[models]` 中 |
| 数据集 | 题数 ≠ 113 / 66；DeepSWE `task.toml` 超时不统一 | 不是固定的数据集 commit |
| Docker / runner | daemon 不可用；缺 buildx 插件；找不到 runner | runner 版本与锁不一致 |
| 镜像 | 加 `--require-images` 时缺镜像 | 缺镜像 |
| 模型端点 | `GET <base_url>/models` 非 2xx 或连不上（只报状态码，不打印密钥） | `--no-endpoint-probe` 跳过 |
| 资源 | | CPU 限额超过核数、可用内存低、磁盘不足、TB4 无 GPU |

`--agent-env` 只接受白名单内的非密钥变量（模板、reasoning effort、采样参数、日志）；`IACODER_MAX_TURNS`、
`IACODER_TASK_OUTER_TIMEOUT_SEC` 被明确拒绝。

## 2. run

```bash
./bench run --benchmark deepswe --version v0.7.64 --model deepseek-flash \
    --concurrency 16 --tasks-dir <eval-root>/deep-swe/tasks \
    --agent-env IACODER_PROMPT_TEMPLATE=mini-swe-agent-custom --agent-env IACODER_REASONING_EFFORT=max
# Terminal-Bench 4.0
export IACODER_DEV_BINARY_URL=http://<docker-bridge-ip>:<port>/iacoder-x86_64-unknown-linux-musl.tar.gz
./bench run --benchmark tbench --version v0.7.64 --model deepseek-flash --concurrency 8
```

- 先自动跑 preflight，不通过就不启动。
- 先看命令不启动：加 `--print-command`。
- 后台启动（新会话，关闭终端不影响），打印 run id，例如 `deepswe-v0.7.64-deepseek-flash-20261007T010203Z`。
- 生成 `runs/<run-id>/`：`manifest.json`（提交到仓库；本机路径和 URL 已换成占位符）、`launch.sh`、`local.json`、`pid`、`launch.log`（本机文件，已 gitignore）。
- job 输出在 `jobs/<run-id>/`（已 gitignore，可用 `--jobs-root` 换到大磁盘）。

## 3. status

```bash
./bench status <run-id>              # 一次
./bench status <run-id> --watch 60   # 每 60 秒刷新，结束或进程退出后停止
./bench status <job-dir> --json      # 也可以直接给 job 目录
```

输出：已结束 / 总 trial 数、在跑数、进程是否存活；当前 pass@1 与 ±、按轮分数（运行未完成时**不是成绩**）；
异常类型计数；agent 时长、步数、输出 token、成本的均值 / 中位；按已结束 trial 平均时长与并发估算的剩余时间；
主机负载、内存、磁盘、`oom_kill` 计数。

## 4. score

```bash
./bench score <run-id> --leaderboard-row
./bench score <run-id> --json
```

- 按 benchmark 自动选口径（`deepswe` / `tbench4`）、次数（4 / 5）与题数（113 / 66），调 `scoring/score.py` 与 `scoring/usage.py`。
- 写 `runs/<run-id>/score.json`；合规时把最后一个 trial 的结束时间写进 manifest 的 `finished_at`。
- **不合规退出码 2**，输出 `NON-COMPLIANT` 与原因；这样的数字不得对外发布。
- 计价文件默认 `scoring/pricing/<model>.toml`，可用 `--pricing` 指定。

## 5. 归档

1. 打包原始 job 目录（如 `tar --zstd -cf <archive>/<run-id>.tar.zst -C jobs <run-id>`），记 sha256 与大小，
   写 `runs/<run-id>/artifacts.json`（存放位置用占位符）。
2. 按 [runs/_template/report.md](../runs/_template/report.md) 写 `runs/<run-id>/report.md`。
3. 提交 `runs/<run-id>/` 下的 `manifest.json`、`score.json`、`report.md`、`artifacts.json`，提交前确认没有密钥、地址、主机名。
4. 满足 [results/README.md](../results/README.md) 的全部准入条件后，才能在 `results/` 和 README 中发布。

## 排查

内部诊断工具在 [tools/](../tools/)（同题对比、工具调用统计、调用速度等），输出不进入公开成绩。
