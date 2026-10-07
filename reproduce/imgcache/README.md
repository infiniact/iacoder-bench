# imgcache

评测镜像缓存。DeepSWE / Terminal-Bench 的任务镜像来自 public.ecr.aws 与 Docker Hub；大并发首拉会撞匿名限流
（`429 Too Many Requests` / `Rate exceeded`），trial 在构建阶段就失败，被记成异常而不是 agent 的失败。
有缓存后只在镜像新增或更新时才访问 registry。

```bash
# 列出任务需要的镜像（task.toml docker_image、docker-compose image、无预构建镜像时的 Dockerfile FROM）
python3 imgcache.py list <tasks-dir>
# 查看 load 会做什么（不改动任何东西）；tag 引用会对 registry 发 HEAD 请求比对 digest（不计入 Docker Hub 拉取额度）
python3 imgcache.py check <tasks-dir> --refs-file harness-extra.refs
# 确保每个镜像都已存档（缺的或有更新的先拉再存）
IMGCACHE_DIR=<cache-dir> python3 imgcache.py sync <tasks-dir> --refs-file harness-extra.refs
# 在新主机上从缓存恢复
IMGCACHE_DIR=<cache-dir> sh imgcache-bootstrap.sh [<tasks-dir>]
```

- 存储：`IMGCACHE_DIR`（默认 `./image-cache`），每个镜像一个 zstd 压缩的 `docker save` 存档，加一个 `manifest.json`（ref、digest、文件、sha256）。
- 可选 OSS 后端：设置 `OSS_BUCKET`、`OSS_ENDPOINT`、`OSS_REGION`、`OSS_PREFIX` 与认证（AccessKey 或 `OSS_ECS_ROLE=1` 使用实例 RAM 角色），
  可直接放环境变量，或写进文件并用 `OSS_ENV_FILE` 指向。未设置时只用本地目录。示例中的 `<bucket>`、`<ram-role>` 都是占位符。
- containerd 镜像存储：`docker load` 之后镜像只认 tag；任务里按 `repo@sha256:<digest>` 引用的镜像会因此回到 registry 去拉。
  恢复时用 `ctr -n moby images tag` 补上 `repo@digest` 名字，保证按 digest 引用也命中本地。
- `harness-extra.refs`：不在任务定义里、但 harness 运行时会用到的镜像（如 Pier egress 代理的 `ubuntu:24.04` 基础镜像——
  BuildKit 拉过 FROM 也不一定留下带 tag 的本地副本，需单独存档）。
- 需要 `docker`、`zstd`；OSS 后端另需 `ossutil` v2。
