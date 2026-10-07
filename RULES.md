# 评测规则

本仓库的每一个对外成绩都必须同时满足：(1) 评测组织的现行规则；(2) 本仓库的附加规定（见文末）。
下面的英文引文保持原样，中文是说明。抓取日期均为 **2026-10-07**，规则以来源页面的最新版本为准；
来源变化时更新本文件并在末尾"变更记录"中写明。

计分实现见 [scoring/](scoring/)，口径说明见 [docs/methodology.md](docs/methodology.md)。

---

## DeepSWE（Datacurve）

### 来源

| 来源 | 版本 / 时间 |
|---|---|
| 论文 *DeepSWE*，[arXiv:2607.07946](https://arxiv.org/abs/2607.07946) §5.2–5.6、§6.2 | v1 |
| [Run DeepSWE](https://deepswe.datacurve.ai/run) | 抓取 2026-10-07 |
| 博客 [DeepSWE](https://deepswe.datacurve.ai/blog/deepswe)、[DeepSWE v1.1](https://deepswe.datacurve.ai/blog/deepswe-v1-1) | 2026-05-26、2026-06-14 |
| 在线榜单数据 [leaderboard-live.json](https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json) | generated_at 2026-09-22T06:27:15Z |
| 数据集仓库 [datacurve-ai/deep-swe](https://github.com/datacurve-ai/deep-swe) | main `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea`（2026-08-26） |

### 规则

| 项 | 规则 | 原文与出处 |
|---|---|---|
| 数据集 | `datacurve/deep-swe-1-1`，113 题；本仓库固定在数据集 commit `0b9fabbb` | "113 tasks · updated September 22, 2026"（首页）；`tasks/dataset.toml`、`tasks/manifest.json` |
| 框架 | Pier；v1.1 起评分使用独立 verifier 环境，需要 Pier 0.3.0 以上（本仓库用 0.3.1） | "Since v1.1, grading uses Harbor's separate verifier environment, requiring Pier newer than 0.3.0."（仓库 README） |
| 每题次数 | 约 4 次 rollout，全部题约 452 次 | "We draw approximately four rollouts per task per configuration, yielding on the order of 4×113 ≈ 452 rollouts per configuration."（论文 §5.4）；在线榜单每个配置 `n_runs: 4`、`n_attempted: 452` |
| 时限 | 以数据集 `task.toml` 为准：`[agent] timeout_sec = 10800`（commit `0b9fabbb` 由 5400 改为 10800），verifier 1800 s，2 CPU / 8192 MB / 无 GPU。论文榜单那一轮用的是 9000 s。超时倍率未见官方说明，本仓库固定为 1.0、不覆盖 | "The only limit on a rollout is a generous wall-clock timeout of 9,000 seconds (2.5 hours) … we set no step or cost cap."（论文 §5.2） |
| 步数 / 费用上限 | 不设 | 同上 |
| rollout 结束条件 | 提交补丁、耗尽模型原生上下文窗口、超时三者先到者；后两种计失败 | "A rollout ends when the agent submits a patch, exhausts the model's native context window, or hits the timeout, whichever comes first; the latter two are scored as failures (§5.6)."（论文 §5.2） |
| 剔除 | 模型服务错误、判分错误、瞬态网络错误从分子分母同时剔除，**不重采样**；上下文耗尽与 agent 超时计失败 | "Rollouts terminated by a model-provider error, a verifier or grading error, or a transient network error … we exclude them from both the numerator and the denominator of every metric rather than scoring them as failures. Excluded rollouts are not resampled"（论文 §5.6）；"Context-window failures and agent timeouts are scored failures; provider/verifier/network errors are excluded."（在线榜单 `unit`） |
| pass@1 | 论文：各题通过比例的宏平均。在线榜单 v1.1：按计分 rollout 合并计算（pooled）。论文指出两者只差零点几个百分点 | "pass@1 is the macro-average of this per-task pass fraction over the corpus"（论文 §5.3）；"pass@1 is attempt pass rate over scored rollout attempts."（在线榜单 v1.1 `unit`） |
| ± | 按开始时间把每题的 rollout 排成 R 轮，得到 s1..sR；±1.96·SE，SE = std(s1..sR)/√R | "we order a task's rollouts by start time … pass@1 ± 1.96 SE, where the standard error of the mean is SE = std(s1, …, sR)/√R."（论文 §5.5）；"95% run-to-run: SE across repeated whole-benchmark passes (1.96 * std(runs)/sqrt(R))"（在线榜单 `ci_method`） |
| pass@4 | 至少一次通过的题 / 全部题 | "pass@4 is the fraction of the N = 113 attempted tasks solved by at least one of that task's (up to four) rollouts"（论文 §5.3） |
| 成本类指标 | 论文：每 trial 中位数（输出 token、墙钟、美元成本）。在线榜单：Avg cost / Out tok / Steps 列为均值，成本按高峰价计 | "median output tokens, median wall-clock duration, and median dollar cost per trial"（论文 §6.2）；"Efficiency aggregates are over every scored attempt."（`unit`）；"Costs use the peak rates"（[changelog](https://deepswe.datacurve.ai/changelog)） |
| 网络 | 任务的 agent 容器默认无网络，Pier 按 agent 声明的白名单放行（本仓库只放行模型端点）。未见"禁止网页搜索"的明文 | "Pier adds per-agent network allowlists, giving agents only the network access they need while keeping the task environment isolated."（/run）；`task.toml` 中 `network_mode = "no-network"` |
| 榜单 harness | 官方榜单的所有分数都用 mini-swe-agent 跑出；也接受其他 agent 的提交 | "All leaderboard scores were produced with Pier running `mini-swe-agent` on Modal."；"To submit your model or agent to the leaderboard, reach out to … and we'll add your results."（/run） |
| 提交方式 | 通过 [Run DeepSWE](https://deepswe.datacurve.ai/run) 页面上的联系邮箱联系 Datacurve，由对方添加结果 | 同上 |

---

## Terminal-Bench 4.0（当前版本）

Terminal-Bench 已改为带语义化版本号的"持续基准"，没有单独的 terminal-bench-4 仓库或 HF 榜单数据集；
4.0 的提交规则在 `harbor-framework/terminal-bench` 仓库的 `leaderboard/` 目录。

### 来源

| 来源 | 版本 / 时间 |
|---|---|
| [tbench.ai/run](https://www.tbench.ai/run) | 抓取 2026-10-07 |
| [Terminal-Bench 4.0 发布说明](https://www.tbench.ai/news/terminal-bench-4-0)、[Leaderboard integrity update](https://www.tbench.ai/news/leaderboard-integrity-update) | 抓取 2026-10-07 |
| [harbor-framework/terminal-bench](https://github.com/harbor-framework/terminal-bench) release `v4.0.0` | tag commit `452bf305c6daa62fc59061d22133a7cbc7c1572e`（2026-08-26） |
| [`leaderboard/SUBMIT.md`](https://github.com/harbor-framework/terminal-bench/blob/0dd95e2d5453d16eb4ef06a8166546400fbe2dd8/leaderboard/SUBMIT.md) | `0dd95e2d`（2026-08-28） |
| [`leaderboard/src/leaderboard/ci/static_analysis.py`](https://github.com/harbor-framework/terminal-bench/blob/6193dc2bea02660e101223838e7c269928e06635/leaderboard/src/leaderboard/ci/static_analysis.py) | `6193dc2b`（2026-08-28） |
| [`leaderboard/src/leaderboard/core/metrics.py`](https://github.com/harbor-framework/terminal-bench/blob/37d0c6e752365435ac58677d1d425b8e4c07cb31/leaderboard/src/leaderboard/core/metrics.py) | `37d0c6e7`（2026-08-28） |
| [`leaderboard/src/leaderboard/core/hub.py`](https://github.com/harbor-framework/terminal-bench/blob/7fe75af3e84bd767367a15ce4d800f9eb7c3d679/leaderboard/src/leaderboard/core/hub.py) | `7fe75af3`（2026-10-05） |

### 规则

| 项 | 规则 | 原文与出处 |
|---|---|---|
| 当前接收状态 | **社区提交已关闭**，目前只收录维护者运行的结果 | "Community submissions are currently closed for Terminal-Bench 4.0. Only submissions run by the maintainers will be added to the leaderboard at this time."（SUBMIT.md） |
| 数据集 | `harbor run -d terminal-bench/terminal-bench@4.0.0`；榜单固定 `DATASET@DATASET_REF`（`sha256:39d9f44b…7732`），必须原样运行 | "Your jobs must run the exact dataset version pinned in core/hub.py (DATASET@DATASET_REF)"（SUBMIT.md）；`DATASET = "terminal-bench/terminal-bench"`（hub.py） |
| 题数 | 66 题，必须全部覆盖，**不接受子集**（其中 3 题需要 GPU：fp8-rmsnorm-gemm、jax-speedrun-gpu、math-eval-grader） | `EXPECTED_TASK_COUNT = 66`（static_analysis.py）；"Cover every task, ≥ 5 trials each. A submission must include all tasks in the dataset with at least 5 trials per task"（SUBMIT.md）；"Terminal-Bench contains tasks that require GPUs, so you need to run it with a sandbox that has GPU access."（/run） |
| 每题次数 | ≥ 5 | `MIN_TRIALS_PER_TASK = 5`（static_analysis.py） |
| 超时与资源 | `timeout_multiplier` 不设或为 1.0；不得覆盖 agent / verifier 超时；不得覆盖资源。CI 同时拒绝各分项倍率、`override_timeout_sec`、`max_timeout_sec`、`override_cpus/gpus/memory_mb/storage_mb` | "with default execution settings: `timeout_multiplier` unset/`None` or `1.0`, no agent or verifier timeout overrides, no resource overrides. CI rejects anything else."（SUBMIT.md） |
| agent 时限 | 全部 66 题统一为 8 小时（`timeout_sec = 28800`） | "All tasks in TB 4.0 are now set to a flat agent timeout of 8 hours."（4.0 发布说明） |
| 出错的 trial | 记 0，**不剔除** | "Errored trials count as `reward 0` — they are not excluded from the metric."（SUBMIT.md） |
| 指标 | accuracy = 成功 trial / 全部 trial，成功 = reward > 0；SE² = (1/n²)·Σᵢ pᵢ(1−pᵢ)/(kᵢ−1)，显示 accuracy ± 1.96·SE；另有 pass@2..5（无偏估计） | "accuracy = successful / total trials … standard_error from the per-task standard-error formula s^2 = (1/n^2) * sum_i p_i (1 - p_i) / (k_i - 1)"；"A trial succeeds iff reward > 0 (errored trials have none -> failure)."（metrics.py） |
| 提交产物 | 用 `--upload` 把 job 上传到 Harbor hub 且公开可读；每个得分 trial 要有 hub 上的 ATIF 轨迹，供 `/judge` 审查作弊；提交 JSON 的展示字段必填。**不再要求** metadata.yaml | "Run with `--upload` (or upload afterwards) so the trials are on the Harbor hub — CI reads everything from there. Trials must be publicly readable."；"every rewarded trial has a Hub `trajectory_path` (ATIF) so `/judge` can audit for reward hacking"（SUBMIT.md） |
| 提交流程 | `uv run lb submit <job-links...>` → 静态检查 → bot PR → `/judge`（LLM 审查通过的 trial）→ `/apply` → 合并 | SUBMIT.md |
| 禁止访问的站点 | **未找到 4.0 的官方说明。** 2.0 有"不得访问 Terminal-Bench 网站或 GitHub 仓库"的明文（见下方历史版本），4.0 文本中没有。4.0 的 judge 规则把"取得题目对应的现成答案"判为作弊，一般性的网页查询不算 | "Reward hacking will result in a reward of 0 for a trial (e.g. finding solutions on the internet)"（integrity update）；"Flag only when retrieved material supplies the submitted answer." / "General web reference is fine."（`leaderboard/src/leaderboard/ci/analysis-task-template/instruction.md`） |
| 网络 | **未找到作为提交规则的官方说明。** 任务默认可联网，66 题中 2 题设置 `allow_internet = false` | 数据集 `task.toml` |
| 榜单列 | Agent、Model、Effort、Accuracy、Release Date、Agent Org、Model Org、Tokens（合计）、Cost（合计） | `leaderboard/leaderboard.yaml`（`4-0-0`） |

### 历史版本：Terminal-Bench 2.x（不再作为本仓库的默认目标）

来源：[HF harborframework/terminal-bench-2-leaderboard README](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard)
（sha `572b2614`，2026-05-15）、[harbor-framework/terminal-bench-2-1 README](https://github.com/harbor-framework/terminal-bench-2-1)（`7131e437`，2026-08-11）。

- 2.0：**"SUBMISSIONS CLOSED"**。规则："`timeout_multiplier` must equal `1.0`"；"No agent timeout overrides"；
  "No verifier timeout overrides"；"No resource overrides"；"All trial directories must have valid `result.json` files"；
  "Each task must be evaluated with a minimum of five trials"；"Agents cannot access the Terminal-Bench website or GitHub repository (reward hacking)"；
  "Each submission must include a `metadata.yaml` file"。
- 2.1："Community submissions are currently closed for Terminal-Bench 2.1. Only submissions run by the maintainers will be added to the leaderboard at this time."；
  "you must run at least 5 trials per task and upload them to Harbor Hub publicly"。

---

## 本仓库的附加规定

1. **所有评测关闭网页工具。** harness 的运行命令写死 `--no-web`，没有开关。这比两个评测组织的现行规则更严。
2. **只发布严格合规的结果。** 只发布 `scoring/score.py` 判定为 COMPLIANT 的单个 job，并保留可复算的原始 job 目录
   （准入条件见 [results/README.md](results/README.md)）。
3. **不拼接、不取优、不重采样。** 不合并多次运行，不为单题挑选最好的一次，不重跑失败 trial 去替换成绩；
   `pier` / `harbor` 显式传 `--max-retries 0`。
4. **不设步数与费用上限**（适配器默认 `--max-turns=0`，配置里不写 `max_turns`）。
5. DeepSWE 中模型服务错误、瞬态网络错误无法从异常类型可靠识别时，**主分数保守计失败**，只把"按规则剔除"的结果作为敏感性数值另列。

---

## 变更记录

### 2026-10-07（相对此前 README 中的"评测规则"一节）

DeepSWE：

- 补充来源：Run 页面、v1.1 博客、在线榜单数据文件、数据集仓库 commit；原只引论文 §5 与 Run 页面。
- 时限：原写"当前为 10800 秒"。现注明来源是数据集 commit `0b9fabbb`（2026-08-26 由 5400 改为 10800），
  而论文榜单那一轮用的是 9000 s；超时倍率在官方材料中未找到，本仓库固定 1.0。
- 新增：± 的计算方法（按开始时间分轮、1.96·std/√R）、pass@4 定义、成本类指标口径（论文中位数，在线榜单均值、按高峰价）。
- 新增：在线榜单 v1.1 的 pass@1 为 pooled，论文为宏平均；本仓库两者都输出，主分数用宏平均。
- 新增：Pier 版本要求（v1.1 起需 > 0.3.0）、官方榜单 harness 为 mini-swe-agent、agent 容器默认无网络。
- 提交方式不变（联系 Datacurve）；不再在仓库中写对方人员的邮箱，改为指向 Run 页面。

Terminal-Bench：

- 默认目标由 2.x 改为 **4.0**；2.x 规则降为"历史版本"。
- 新增 4.0 规则：数据集 `terminal-bench/terminal-bench@4.0.0`、66 题全覆盖不接受子集（含 3 道 GPU 题）、
  每题 ≥ 5 次、8 小时统一时限、出错 trial 记 0 且不剔除、成功判据为 reward > 0、pooled accuracy 与按题 SE 公式、
  上传 Harbor hub 与 ATIF 轨迹取代 metadata.yaml、`lb submit` 流程。
- 接收状态：4.0 社区提交已关闭（原 README 写的是 2.0 暂停、2.1 仅维护者）。
- "不得访问 TB 网站 / 仓库"：在 4.0 文本中未找到，只保留在 2.0 历史规则中；本仓库因附加规定 1 一律关闭网页工具，不受影响。
