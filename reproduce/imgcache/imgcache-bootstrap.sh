#!/bin/sh
# 在一台新主机上恢复评测镜像缓存（可选 OSS 后端），并检查镜像是否有更新。
#
#   1. （可选，只在使用 OSS 时）把 OSS 设置写进一个只有 root 可读的文件，并用 OSS_ENV_FILE 指向它：
#        OSS_BUCKET=<bucket>
#        OSS_ENDPOINT=<oss-endpoint>        # 与主机同地域时用内网 endpoint
#        OSS_REGION=<region>
#        OSS_PREFIX=eval-image-cache
#        # 认证二选一：
#        OSS_ACCESS_KEY_ID=<id>  OSS_ACCESS_KEY_SECRET=<secret>
#        OSS_ECS_ROLE=1                     # 主机已绑定可读写该 bucket 的实例 RAM 角色（<ram-role>）
#   2. IMGCACHE_DIR=<cache-dir> OSS_ENV_FILE=<file> sh imgcache-bootstrap.sh [TASK_DIR ...]
#      不带 TASK_DIR：恢复缓存清单里的全部镜像；带 TASK_DIR：额外把这些任务需要、而缓存里没有的镜像
#      从 registry 拉下来、归档、（如配置了 OSS）上传。
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v zstd >/dev/null || { echo "需要 zstd（apt-get install -y zstd）" >&2; exit 1; }
if [ -n "${OSS_ENV_FILE:-}" ] && ! command -v ossutil >/dev/null; then
  # ossutil v2 官方发布包
  curl -fsSL -o /tmp/ossutil.zip https://gosspublic.alicdn.com/ossutil/v2/2.1.2/ossutil-2.1.2-linux-amd64.zip
  (cd /tmp && python3 -m zipfile -e ossutil.zip ossutil-x && install -m 0755 ossutil-x/*/ossutil /usr/local/bin/ossutil)
fi
exec python3 "$HERE/imgcache.py" load --all --refs-file "$HERE/harness-extra.refs" "$@"
