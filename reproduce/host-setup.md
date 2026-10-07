# 主机准备（通用步骤）

以下以 DeepSWE（Pier）为主，Terminal-Bench 4.0（Harbor）的差异单列。所有尖括号内容都是占位符。
完成后用 `./bench preflight` 逐项核对，见 [docs/quickstart.md](../docs/quickstart.md)。

## 1. 系统与 Docker

- Linux x86_64（二进制另有 aarch64 版）。参考配置：64 vCPU / 121 GiB 内存跑 32 并发时，主机内存实际只用 8–14 GiB。
- Docker Engine（参考版本 29.x）+ Compose v2 + **buildx 插件**，cgroup v2。Ubuntu 的 `docker.io` 包不带 buildx，需另装 `docker-buildx`（各评测主机版本保持一致，参考 0.30.1）；缺它时需要构建出网管控等辅助镜像的题会在建环境阶段失败（`unknown flag: --file`），trial 记 0。`/etc/docker/daemon.json` 关键项：
  ```json
  {
    "default-address-pools": [{"base": "<私有网段A>/16", "size": 24}, {"base": "<私有网段B>/16", "size": 24}],
    "log-driver": "local",
    "log-opts": {"max-size": "10m", "max-file": "3"}
  }
  ```
  - 每个 trial 建一个 compose 网络。默认地址池太小时，大并发或批次被中断后残留的网络会耗尽地址池，
    之后每个 trial 都报 "all predefined address pools have been fully subnetted"。地址池选两个与主机现有网络不冲突的私有 /16，每个子网 /24。
  - 批次前执行 `docker network prune -f` 回收没有容器挂载的孤儿网络（`./bench run` 已内置）。
- 建议加 swap（如 16 GiB）缓冲编译 / 测试峰值；任务自身的资源限额不变。
- 磁盘：DeepSWE 每题镜像约 4 GB，全量建议预留 ≥ 400 GiB。

## 2. Runner 版本锁

**DeepSWE**：`datacurve-pier==0.3.1`（依赖 `harbor==0.23.0`）。不加约束直接装会解析到更新的 harbor 和三十多个漂移的依赖，
所以用本目录的锁文件：

```bash
uv tool install --python 3.12 --constraints reproduce/pier-constraints.txt \
  --with-editable harness/common --with-editable harness/deep-swe datacurve-pier==0.3.1
uv pip freeze --python "$(uv tool dir)/datacurve-pier/bin/python" | grep -v "^-e" | diff - reproduce/pier-constraints.txt   # 装完核对
```

锁文件来自一台已跑过全量的评测主机的 `uv pip freeze`。要更新锁：在一个干净环境里按上面的命令安装，
`uv pip freeze` 导出，diff 后连同原因一起提交。

**Terminal-Bench 4.0**：`harbor`（Harbor CLI）。本仓库没有锁定 TB4 用的 harbor 版本；按
[terminal-bench 仓库 leaderboard 说明](https://github.com/harbor-framework/terminal-bench/tree/main/leaderboard)
安装能运行 `terminal-bench/terminal-bench@4.0.0` 的版本，把 `harbor --version` 记进该轮 manifest（`./bench run` 自动记录）。

## 3. 数据集

**DeepSWE**：`git clone https://github.com/datacurve-ai/deep-swe <eval-root>/deep-swe && git -C <eval-root>/deep-swe checkout 0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea`，
任务目录为仓库中的 `tasks/`（113 个含 `task.toml` 的子目录）。不要修改任何 `task.toml`：
`[agent] timeout_sec = 10800`、verifier 1800 s、每题 2 CPU / 8 GiB，`./bench preflight` 会核对题数与超时统一性。

**Terminal-Bench 4.0**：由 harbor 按 `-d terminal-bench/terminal-bench@4.0.0` 拉取，66 题，其中 3 题需要 GPU。
官方不接受子集；没有 GPU 的主机上这 3 题会失败并按 0 计。

## 4. 二进制分发

- 用 [`bin/<version>/`](../bin/) 中的官方 release 压缩包，`shasum -a 256 -c SHA256SUMS` 校验。
- 容器在构建（Pier）或安装（Harbor）阶段通过 HTTP 下载压缩包：在主机上起一个只绑 Docker 网桥地址的静态文件服务，
  例如 `python3 -m http.server <port> --bind <docker-bridge-ip> --directory bin/v0.7.64`，
  然后 `--binary-url http://<docker-bridge-ip>:<port>/iacoder-x86_64-unknown-linux-musl.tar.gz`。
  绑在网桥上即可，不必对外网卡开放。`./bench preflight` 会下载该 URL 并与 SHA256SUMS 比对。
- 压缩包根目录只有一个 `iacoder` 文件，harness 的安装步骤按这个结构解包。

## 5. 模型端点与出网

- 密钥只放环境变量（如 `DEEPSEEK_API_KEY`），配置文件里写 `${ENV:DEEPSEEK_API_KEY}`；见 [configs/](../configs/)。
  非交互 shell（如 ssh 远程命令）不会读 profile，启动前确认变量已导出，否则每个 trial 都会在启动时失败。
- Pier 的 agent 容器在 Squid egress 代理之后，只能访问 `network_allowlist()` 声明的域名
  （v0.7.64 harness：`api.deepseek.com`、`open.bigmodel.cn`、`openrouter.ai`）。换其他端点要先把域名加进
  `harness/deep-swe/iacoder_pier/agent.py` 的 `_LLM_DOMAINS`（在本地副本里改，并记进该轮 manifest），
  再到 proxy 容器的环境变量 `ALLOWLIST_DOMAINS` 核实。查看 proxy 配置时不要整份打印 compose 文件，其中有单 trial 的代理令牌。
- **网页工具一律关闭**：harness 运行命令写死 `--no-web`。冒烟时在 `agent/iacoder.txt` 中确认 `web-tools-disabled`。
- 端点探测：`./bench preflight` 对配置里每个 provider 请求一次 `GET <base_url>/models`，只报状态码。
  需要更细的能力探测（工具调用、图像、长上下文）用 `python3 harness/common/capability_probe.py`。
- 不同端点（官方 / 中转）单次调用耗时可差数倍，绝对分数与时长只在同机、同端点之间可比。

## 6. 镜像预拉 / 缓存

大并发的第一波 trial 会同时向 registry 发 manifest 请求，public.ecr.aws 会对匿名突发返回
`429 Too Many Requests`，trial 在 `docker compose build` 阶段失败，记成异常。发射前把所有基础镜像准备好：

```bash
python3 reproduce/imgcache/imgcache.py list <tasks-dir> --refs-file reproduce/imgcache/harness-extra.refs   # 共约 115 个
IMGCACHE_DIR=<cache-dir> python3 reproduce/imgcache/imgcache.py sync <tasks-dir> --refs-file reproduce/imgcache/harness-extra.refs
```

或在已有缓存的情况下 `sh reproduce/imgcache/imgcache-bootstrap.sh <tasks-dir>` 恢复。`./bench preflight` 会报告缺失的镜像数。

- Pier egress 代理镜像 `FROM ubuntu:24.04` 再 apt 装 squid，依赖 BuildKit 缓存；不要随手 `docker builder prune`。
- Pier 默认 `delete=True`：trial 结束时执行 `docker compose down --rmi all --volumes --remove-orphans`，
  **只删本 trial 构建出的 session 镜像**，父镜像（任务基础镜像）保留。不要改这个默认，也不要在批次中 `docker image prune`。

## 7. 并发与资源观察

- 每个 DeepSWE trial 限额 2 CPU / 8 GiB；实测 main 容器内存峰值中位约 0.5 GiB，少数 Go 构建类任务会碰到 8 GiB 限额。
  限额合计可以超过物理内存，靠的是实际占用远低于限额；按主机核数和 `./bench status` 中的负载调并发。
- `/proc/vmstat` 的 `oom_kill` 计数包含单容器 cgroup OOM（任务进程超出自身限额），不代表主机内存不足；
  用 `journalctl -k | grep CONSTRAINT_MEMCG` 区分。
- 运行中可开 `OUT=<dir> sh reproduce/resource-sampler.sh &` 每分钟记录主机与容器资源。
- 停止批次：pier 对 SIGINT 可能不退出，用 SIGTERM（必要时 SIGKILL），然后对每个 compose project 执行
  `docker compose -p <project> down -t 10 --remove-orphans`，最后 `docker network prune -f`。
  被中断的批次**不能**通过重跑失败题来补齐成绩。

## 8. 冒烟

正式发射前，用同一套二进制、配置和 harness 手工跑 1 题（在 `./bench` 之外，单独的输出目录；冒烟结果不进任何成绩）。核对：

- `result.json` 有 reward、`exception_info` 为空；
- `agent/iacoder.txt` 中有 `web-tools-disabled`、上下文窗口与 reasoning effort 的解析结果；
- `agent/session.jsonl` 第一条 `SessionStart` 的 `max_turns` 为 0。
