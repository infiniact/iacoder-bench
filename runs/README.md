# runs/

每一轮评测运行的档案，一轮一个目录：`runs/<run-id>/`。`./bench run` 自动创建并写 `manifest.json`，
`./bench score <run-id>` 写 `score.json`。档案只放**小文件**；原始 job 目录（GB 级）不进仓库，用 `artifacts.json` 记录位置与校验和。

格式模板见 [`_template/`](_template/)。

## 目录格式

| 文件 | 提交 | 内容 |
|---|---|---|
| `manifest.json` | 是 | 二进制版本与 sha256、harness 来源 commit 与目录指纹、配置文件名与 sha256、模型、数据集（名称、来源、固定 commit、题数）、每题次数、倍率、并发、透传的非密钥变量、runner 与 Docker 版本、主机规格（CPU 数、内存、OS / 内核、架构；**不含**主机名、地址、账号）、命令（本机路径与二进制 URL 已替换为 `<tasks-dir>`、`<jobs-dir>`、`<binary-url>` 等占位符）、起止时间 |
| `score.json` | 是 | `./bench score` 的输出：合规结论、pass@1 ±、按轮分数、pass@k、异常分布、逐题结果、usage 汇总、榜单行 |
| `report.md` | 是 | 人写的说明：这一轮为什么跑、和哪一轮比、异常怎么处理、结论 |
| `artifacts.json` | 是 | 原始 job 目录的存放位置（占位，如 `<archive>/<run-id>.tar.zst`）、压缩包 sha256 与大小、打包时间 |
| `launch.sh`、`local.json`、`pid`、`launch.log` | 否（gitignore） | 本机启动脚本、本机路径、进程号、日志 |

## 规则

- 一轮 = 一个 job。中途换二进制、换配置、换 harness 都是新的一轮。
- 档案里不得出现密钥、IP、主机名、云账号 / 实例 / bucket / 角色等身份信息，路径一律用占位符。
- 进行中的轮次可以先提交 `manifest.json`；结果未发布前 `report.md` 要写明"未发布"。
- 不合规的轮次也保留档案（写明不合规原因），但不能进入 [results/](../results/)。
