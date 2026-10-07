#!/bin/sh
# Launch a DeepSWE run with the outer cap derived from the task set.
#
# The adapter's `_TASK_OUTER_TIMEOUT_SEC` constant is a fallback. This script
# resolves the real value from the task set (and the multiplier, which the
# installed adapter cannot observe) and exports it, so the budget the engine is
# told about tracks the window it actually has instead of a number that happened
# to match one task set.
#
# Usage:
#   IACODER_BINARY_URL=... ./run.sh <tasks-dir> <model> <out-dir> [n-tasks] [multiplier] [n-concurrent]
set -eu

tasks_dir="${1:?usage: run.sh <tasks-dir> <model> <out-dir> [n-tasks] [multiplier] [n-concurrent]}"
model="${2:?model required}"
out_dir="${3:?out dir required}"
n_tasks="${4:-113}"
multiplier="${5:-1.0}"
concurrent="${6:-1}"

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

: "${IACODER_BINARY_URL:?IACODER_BINARY_URL must point at an iacoder musl tarball}"

# Refuses to guess on a non-uniform set (exit 4) rather than pick one silently.
# Not falling back to the adapter constant here is deliberate: a set the script
# cannot describe is exactly the case where the constant is wrong, so inheriting
# it would reintroduce the failure this script exists to prevent.
outer_cap=$("$here/resolve-outer-cap.sh" "$tasks_dir" "$multiplier")
export IACODER_TASK_OUTER_TIMEOUT_SEC="$outer_cap"
echo "[run] outer cap = ${outer_cap}s (task set: $tasks_dir, multiplier: $multiplier)" >&2

export PYTHONPATH="$here:$here/../common${PYTHONPATH:+:$PYTHONPATH}"

# Refuse to start without credentials in the environment.
#
# The engine is configured by environment alone here, so a shell that never
# sourced the profile produces an agent that dies 0.3s into every task with
# "environment variable ... is not set". That failure is invisible until the
# whole set has burned: a non-interactive ssh command does not read the
# profile, and 11 tasks were spent discovering it once already. One cheap check
# up front is worth more than N identical post-mortems.
if ! env | grep -qE '^[A-Z0-9_]*(API_KEY|AUTH_TOKEN)='; then
  echo "no provider credential in the environment — the engine is configured by env here," >&2
  echo "so every task would fail at startup. Source the profile or export the key." >&2
  exit 7
fi

# Resolve the harness binary. Hardcoding an install path would bake one host's
# layout into a checked-in script; honour an explicit override, then PATH, and
# fail with what was tried rather than a bare "not found" from exec.
pier_bin="${PIER_BIN:-}"
if [ -z "$pier_bin" ]; then
  pier_bin=$(command -v pier 2>/dev/null || true)
fi
if [ -z "$pier_bin" ] || [ ! -x "$pier_bin" ]; then
  echo "cannot find the pier binary: set PIER_BIN, or put pier on PATH" >&2
  echo "  PIER_BIN=${PIER_BIN:-<unset>}  PATH=$PATH" >&2
  exit 6
fi

# Reclaim orphaned compose networks before the batch.
#
# Each trial gets its own compose network. When a batch is killed mid-flight —
# or when anyone removes its containers without removing the networks — those
# networks survive with nothing attached. Docker's predefined address pools are
# finite: enough orphans and the daemon answers every subsequent `network
# create` with "all predefined address pools have been fully subnetted", which
# surfaces as EVERY task failing to start. That happened: 73 tasks launched,
# 70 EXC, none of them the agent's doing.
#
# `prune` only removes networks with no containers attached, so a concurrently
# running batch is not affected. Failure is non-fatal: a daemon that refuses to
# prune is not a reason to refuse to run.
docker network prune -f >/dev/null 2>&1 || true

# Warm only the first wave's base images, one at a time.
#
# Every trial builds its environment `FROM` a registry image. Only the *start*
# of a cold batch bursts: the first `n-concurrent` trials all issue their
# manifest request in the same instant, and a public registry answers the burst
# with `429 Too Many Requests`:
#
#   ERROR: unexpected status from HEAD request to https://public.ecr.aws/v2/...
#          429 Too Many Requests
#   failed to solve: ... failed to resolve source metadata
#
# The trial dies in `docker compose build`, before the agent starts — scored as
# an exception, not as a task the agent failed. Two tasks were lost that way in
# the first eleven minutes of one batch.
#
# After that first wave the batch self-staggers: a new trial starts only when an
# earlier one finishes, so its pull is already spread out in time. Warming the
# whole set would therefore pay a long serial pull for images the batch will
# fetch harmlessly on its own — the burst is the problem, not the total.
#
# Rate limits are per-client and time-windowed, so serialising just this wave is
# enough; no credentials are needed.
#
# Non-fatal by design: an image that cannot be pre-pulled (private, renamed,
# already local) is left to the trial, which fails exactly as it would today.
# A warm-up that refuses to run is not a reason to refuse to run the batch.
warm_wanted="$concurrent"
warm_seen=""
warm_count=0
warm_failed=0
for task_dir in "$tasks_dir"/*/; do
  [ "$warm_count" -ge "$warm_wanted" ] && break
  [ -d "$task_dir" ] || continue
  for img in $(
    find "$task_dir" -name Dockerfile -print0 2>/dev/null |
      xargs -0 -r grep -hE '^[[:space:]]*FROM[[:space:]]' 2>/dev/null |
      awk '{print $2}' |
      grep -vE '^(scratch$|\$)'
  ); do
    [ "$warm_count" -ge "$warm_wanted" ] && break
    # Skip images this loop already handled; `case` keeps the membership test
    # POSIX and avoids re-pulling a base shared by several tasks.
    case " $warm_seen " in *" $img "*) continue ;; esac
    warm_seen="$warm_seen $img"
    docker image inspect "$img" >/dev/null 2>&1 && continue
    warm_count=$((warm_count + 1))
    docker pull -q "$img" >/dev/null 2>&1 || warm_failed=$((warm_failed + 1))
  done
done
if [ "$warm_count" -gt 0 ]; then
  echo "[run] pre-pulled $warm_count base image(s) serially for the first wave" >&2
  [ "$warm_failed" -gt 0 ] &&
    echo "[run] $warm_failed could not be pre-pulled; their trials will pull inline" >&2
fi

exec "$pier_bin" run \
  -p "$tasks_dir" \
  --agent-import-path iacoder_pier:IacoderPierAgent \
  --model "$model" \
  --n-tasks "$n_tasks" \
  --n-concurrent "$concurrent" \
  --timeout-multiplier "$multiplier" \
  -o "$out_dir"
