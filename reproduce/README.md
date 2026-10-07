# reproduce/

在一台新主机上复现评测环境所需的通用步骤与工具。全部用占位符，不含任何主机、账号、云资源信息。

| 文件 | 作用 |
|---|---|
| [host-setup.md](host-setup.md) | 主机准备：Docker、runner 版本锁、数据集、镜像预拉、出网、并发与资源观察 |
| `pier-constraints.txt` | DeepSWE runner 环境（`datacurve-pier==0.3.1`、`harbor==0.23.0` 及全部依赖）的 `uv pip freeze` 锁 |
| `imgcache/` | 镜像缓存：把任务需要的镜像存档、在新主机恢复、检查更新；可选 OSS 后端 |
| `resource-sampler.sh` | 只读的主机 / 容器资源采样 |
