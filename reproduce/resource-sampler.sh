#!/bin/sh
# 只读的主机资源采样器，每 INTERVAL 秒一行（默认 60），写到 OUT 目录（默认 ./resources）：
#   host.tsv:      ts  mem_used_MiB  mem_avail_MiB  swap_used_MiB  load1  load5  ncpu  disk_used_GiB  disk_avail_GiB  containers  oom_kills_total
#   container.tsv: ts  name  memory.peak  memory.current  cpu_usage_usec   （cgroup v2 + systemd cgroup driver）
# oom_kills_total 来自 /proc/vmstat，包含单容器 memcg OOM（任务自身超出限额），不等于主机内存不足；
# 用 `journalctl -k | grep CONSTRAINT_MEMCG` 区分。
# 用法：OUT=<dir> INTERVAL=60 sh resource-sampler.sh &
OUT=${OUT:-./resources}
INTERVAL=${INTERVAL:-60}
mkdir -p "$OUT"
[ -s "$OUT/host.tsv" ] || printf 'ts\tmem_used_MiB\tmem_avail_MiB\tswap_used_MiB\tload1\tload5\tncpu\tdisk_used_GiB\tdisk_avail_GiB\tcontainers\toom_kills_total\n' > "$OUT/host.tsv"
while true; do
  ts=$(date -u +%FT%TZ)
  mem=$(free -m | awk '/^Mem:/{u=$3; a=$7} /^Swap:/{s=$3} END{printf "%s\t%s\t%s", u, a, s}')
  load=$(cut -d' ' -f1,2 /proc/loadavg | tr ' ' '\t')
  disk=$(df -BG --output=used,avail / | tail -n 1 | tr -d 'G' | awk '{printf "%s\t%s", $1, $2}')
  n=$(docker ps -q | wc -l)
  oom=$(awk '/^oom_kill /{print $2}' /proc/vmstat)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$mem" "$load" "$(nproc)" "$disk" "$n" "$oom" >> "$OUT/host.tsv"
  for c in $(docker ps -q); do
    id=$(docker inspect -f '{{.Id}}' "$c" 2>/dev/null) || continue
    nm=$(docker inspect -f '{{.Name}}' "$c" 2>/dev/null)
    f=/sys/fs/cgroup/system.slice/docker-$id.scope
    [ -d "$f" ] || continue
    printf '%s\t%s\t%s\t%s\t%s\n' "$ts" "$nm" "$(cat "$f/memory.peak" 2>/dev/null)" "$(cat "$f/memory.current" 2>/dev/null)" "$(grep usage_usec "$f/cpu.stat" | cut -d' ' -f2)" >> "$OUT/container.tsv"
  done
  sleep "$INTERVAL"
done
