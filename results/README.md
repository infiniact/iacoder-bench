# results/

只放**严格合规**的对外结果。目前为空。

## 准入条件（全部满足才能放进来）

1. **单个 job。** 结果来自一个 job 目录，没有合并、拼接、取优、重跑替换。
2. **计分合规。** `./bench score <run-id>`（即 `scoring/score.py`）输出 COMPLIANT、退出码 0：
   题数与每题次数达标、无运行中 trial、倍率 1.0、超时与资源未覆盖、agent 与模型一致。
3. **规则全符合。** 满足 [RULES.md](../RULES.md) 中该评测的全部现行规则与本仓库附加规定（关闭网页工具、不设步数上限、不重采样）。
4. **可复算。** 对应的 `runs/<run-id>/` 档案齐全（`manifest.json`、`score.json`、`report.md`、`artifacts.json`），
   原始 job 目录按 `artifacts.json` 可取回，校验和一致，任何人用本仓库的 `scoring/` 能复算出相同数字。
5. **二进制与 harness 可追溯。** `manifest.json` 中的二进制 sha256 与 `bin/<version>/SHA256SUMS` 一致，harness 指纹与本仓库快照一致。

## 格式

每个结果一个文件 `results/<benchmark>-<version>-<model>-<date>.md`：榜单行（`--leaderboard-row`）、
指向 `runs/<run-id>/` 的链接、规则版本（RULES.md 的抓取日期）、提交给评测组织的状态。
