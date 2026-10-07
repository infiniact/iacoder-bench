# harness/

iacoder 评测适配器（Harbor / Pier agent）的**只读快照**。

iacoder 源码仓库不公开，公开仓库无法以子模块或依赖的方式引用它，所以这里直接 vendor
跑评测所必需的文件。快照内的代码文件与上游逐字节一致，没有改动；三个 README 是本仓库新写的
（上游 README 含内部开发记录，没有收录）。

## 来源

| 项 | 值 |
|---|---|
| 上游 | `infiniact/iacoder`（私有） |
| tag | `v0.7.64` |
| commit | `24228eac7ae08f11cccc0d6f24028c3bdcd39cdc` |
| 上游路径 | `eval/common`、`eval/deep-swe`、`eval/terminal-bench` |
| 配套二进制 | [`bin/v0.7.64/`](../bin/v0.7.64/) |

## 目录

```
harness/
├── common/            # iacoder_harbor_base：安装模板、运行命令、config/密钥下发、超时推导；capability_probe.py
├── deep-swe/          # iacoder_pier：Pier（DeepSWE）适配器；run.sh、resolve-outer-cap.sh
└── terminal-bench/    # iacoder_tbench：Harbor（Terminal-Bench 2.x）适配器；run-tbench.sh（本地开发用）
```

`deep-swe/iacoder_pier/agent.py` 按相对路径 `../common/iacoder_harbor_base/` 找到要上传进容器的
`no-regression-*.sh`，所以三个目录必须保持同级。

没有收录的上游文件：测试（`common/tests/`、`terminal-bench/scripts/test_*`）、内部轮次脚本
（`terminal-bench/scripts/` 下的监控、审计、冒烟脚本）、历史结果（`terminal-bench/results/`）、
DeepSWE 的 mini-swe-agent 对照脚本（`deep-swe/mini_swe_agent/`）。它们不影响适配器运行。

## 用法

不安装，直接用 `PYTHONPATH`：

```bash
BENCH=/path/to/bench
# DeepSWE（Pier）
export PYTHONPATH=$BENCH/harness/deep-swe:$BENCH/harness/common
# Terminal-Bench（Harbor）
export PYTHONPATH=$BENCH/harness/terminal-bench:$BENCH/harness/common
```

或者装进 pier / harbor 所在的环境（版本锁见 [reproduce/host-setup.md](../reproduce/host-setup.md)）：

```bash
uv tool install --constraints reproduce/pier-constraints.txt \
  --with-editable harness/common --with-editable harness/deep-swe datacurve-pier==0.3.1
```

agent 导入路径：DeepSWE `iacoder_pier:IacoderPierAgent`，Terminal-Bench `iacoder_tbench:IacoderAgent`。

## 关键默认值（v0.7.64）

| 项 | 值 | 位置 |
|---|---|---|
| 步数上限 | `max_turns=0`，即不限步数；正数只在显式传 `--ak max_turns=N` / `IACODER_MAX_TURNS` 时生效 | `iacoder_pier/agent.py`、`iacoder_tbench/agent.py` 的 `_DEFAULT_MAX_TURNS` |
| 网页工具 | 运行命令写死 `--no-web`，没有开关 | `common/iacoder_harbor_base/base.py` |
| 墙钟上限 | 以任务 `task.toml` 的 `[agent] timeout_sec` 为准；DeepSWE 的 `run.sh` 用 `resolve-outer-cap.sh` 从任务集推导并导出 `IACODER_TASK_OUTER_TIMEOUT_SEC`，适配器内常量只作兜底 | `deep-swe/run.sh` |
| 二进制来源 | Pier：`IACODER_BINARY_URL`（镜像构建期下载）；Harbor：`IACODER_DEV_BINARY_URL`，未设置时从 GitHub release 下载 | `iacoder_pier/agent.py`、`install-iacoder.sh.j2` |
| 模型配置 | `IACODER_CONFIG` 指向的 config.toml；其中 `${ENV:VAR}` 引用的密钥从宿主环境变量读取，经单独文件下发并在读取后删除，不进命令行 | `base.py` |
| egress 白名单（Pier） | `api.deepseek.com`、`open.bigmodel.cn`、`openrouter.ai` | `iacoder_pier/agent.py` 的 `_LLM_DOMAINS` |

使用其他模型端点时，Pier 的 egress 代理会拒绝不在白名单里的域名。需要在本地副本里把域名加进
`_LLM_DOMAINS`，并在该轮 `manifest.json` 里记下改动（快照本身保持不改）。

## 更新快照

```bash
TAG=v0.7.65   # 新版本
TMP=$(mktemp -d)
git -C <iacoder 仓库> archive "$TAG" eval/common eval/deep-swe eval/terminal-bench | tar -x -C "$TMP"
# 只复制上表"目录"中列出的运行文件，覆盖 harness/ 对应文件；不要复制 README、tests、scripts、results
git -C <iacoder 仓库> rev-parse "$TAG^{commit}"   # 更新本文件"来源"表
```

更新后逐文件按公开仓库红线检查（密钥、内网地址、主机名、账号信息），并把 `diff -r` 的摘要写进 PR。
harness 一换就是新的评测环境：不同 harness 版本的运行结果不能放在一起比较。
