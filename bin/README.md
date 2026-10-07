# bin/

评测用的 iacoder 预编译二进制，按版本分目录存放。全部是 Linux musl 静态 release 构建，
来自 [infiniact/iacoder](https://github.com/infiniact/iacoder) 的官方 release。
iacoder 源码仓库不公开，这里放二进制是为了让任何人都能用**同一份字节**复现本仓库记录的运行。

| 版本 | 来源 tag（commit） | 文件 | SHA256 | 对应的运行 |
|---|---|---|---|---|
| v0.7.64 | `v0.7.64`（`24228eac7ae08f11cccc0d6f24028c3bdcd39cdc`） | `iacoder-x86_64-unknown-linux-musl.tar.gz` | `019d16a21513ea1ccbe921c447d6fe728912f43c0bf25b836712fec07430ac2f` | [runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash](../runs/2026-10-07-deepswe-k4-iacoder-v0.7.64-deepseek-flash/)（DeepSWE 113 题 × 4，待提交 Datacurve） |
| | | 解包后 `iacoder`（x86_64） | `6dcf86e28ca517d755d24e8dde243829bc0a86a67b0b896932bfe50aa058c065` | |
| | | `iacoder-aarch64-unknown-linux-musl.tar.gz` | `7a9f42afac2b7d9fdc13e9c661cc113e9ff461c2180b8371ff14d6e5f29c1833` | 无 |
| | | 解包后 `iacoder`（aarch64） | `50409855be294b66570567faed554943ade4cfca690483efabe8aee90153daeb` | |
| v0.5.12 | `v0.5.12`（`ae7b7edcaec454462acb153357a602d652dccc30`） | `iacoder`（x86_64，未压缩） | `5613f7351481de10da17e22023c743a800e3789ad05f524dbc15817f21c1ff5f` | 只对应已撤回的结果（见 [docs/withdrawn-2026-10-05.md](../docs/withdrawn-2026-10-05.md)），仅作历史保留 |

## 校验

```bash
cd bin/v0.7.64
shasum -a 256 -c SHA256SUMS      # 或 sha256sum -c SHA256SUMS
shasum -a 256 -c checksums.txt   # release 自带的校验文件，内容应与 SHA256SUMS 一致
tar -xzf iacoder-x86_64-unknown-linux-musl.tar.gz && shasum -a 256 iacoder
```

## 体积与来源规则

- 单文件都小于 50 MB，不用 Git LFS。每加一个版本仓库约增大 20 MB，所以只放**实际用于某一轮运行**的版本。
- 新版本只从官方 release 下载，校验 release 自带的 `checksums.txt` 通过后再放进来，
  并在上表补一行：tag、tag 指向的 commit、压缩包和解包后二进制的 SHA256、对应的 `runs/` 条目。
- 已放进来的二进制不覆盖、不重打包；某个版本作废时在表里注明，不删除文件（历史运行需要它复算）。
