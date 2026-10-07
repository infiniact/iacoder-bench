#!/bin/sh
# Derive the per-task outer timeout for a DeepSWE task set.
#
# The adapter carries `_TASK_OUTER_TIMEOUT_SEC = 5400` as a constant. That
# number is a *fact about one task set* ("uniform 5400 across all 113"), not a
# constraint — swap the task set and it silently stops matching, and nothing
# reports it. The layer that knows the task set is this one, so derive it here
# and hand it over via IACODER_TASK_OUTER_TIMEOUT_SEC (the override the adapter
# already prefers over its constant).
#
# Refuses to guess: a task set whose tasks disagree gets no answer, because a
# single number cannot describe it and picking one silently is how the current
# mismatch became invisible in the first place.
#
# Usage:  resolve-outer-cap.sh <tasks-dir> [timeout-multiplier]
# Prints the resolved cap on stdout; exits non-zero with a reason otherwise.
set -eu

tasks_dir="${1:?usage: resolve-outer-cap.sh <tasks-dir> [multiplier]}"
multiplier="${2:-1.0}"

[ -d "$tasks_dir" ] || { echo "no such task dir: $tasks_dir" >&2; exit 2; }

# `[agent] timeout_sec` is the binding window. Other sections carry their own
# timeout_sec (build, verifier), so the section matters — a bare grep for
# timeout_sec picks up all three and silently averages nonsense.
values=$(
  for t in "$tasks_dir"/*/task.toml; do
    [ -f "$t" ] || continue
    awk '
      /^\[/          { in_agent = ($0 ~ /^\[agent\]/) }
      in_agent && /^[[:space:]]*timeout_sec[[:space:]]*=/ {
        gsub(/[^0-9.]/, "", $0); print; exit
      }
    ' "$t"
  done | sort -u
)

[ -n "$values" ] || { echo "no [agent] timeout_sec found under $tasks_dir" >&2; exit 3; }

count=$(printf '%s\n' "$values" | grep -c .)
if [ "$count" -ne 1 ]; then
  echo "task set is not uniform — [agent] timeout_sec takes $count distinct values:" >&2
  printf '  %s\n' $values >&2
  echo "a single IACODER_TASK_OUTER_TIMEOUT_SEC cannot describe this set; set it per task instead" >&2
  exit 4
fi

# Multiplier scales the real window; the adapter cannot observe it, which is
# exactly why it has to be applied here. Floor rather than round: the declared
# budget must never exceed the real one.
awk -v base="$values" -v m="$multiplier" 'BEGIN {
  if (m <= 0) { print "multiplier must be > 0" > "/dev/stderr"; exit 5 }
  printf "%d\n", int(base * m)
}'
