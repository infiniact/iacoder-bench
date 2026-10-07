#!/usr/bin/env bash
# terminal-bench runner for iacoder
#
# Usage:
#   ./run-tbench.sh                    - List available tasks
#   ./run-tbench.sh list               - List available tasks
#   ./run-tbench.sh run <task>         - Run single task with iacoder
#   ./run-tbench.sh run <task> --all   - Run single task with iacoder + oracle baseline
#   ./run-tbench.sh run --all          - Run all tasks (WARNING: 89 tasks)
#   ./run-tbench.sh run --failed       - Re-run previously failed tasks
#   ./run-tbench.sh oracle <task>      - Run single task with oracle baseline only
#   ./run-tbench.sh failed [--retry]   - List failed tasks or re-run them
#
# Requirements:
#   - Docker/Colima running
#   - iacoder CLI available
#   - iacoder config should have API keys configured
#
# Proxy configuration (for tasks that need external network access):
#   export all_proxy=http://127.0.0.1:7890   # or socks5://127.0.0.1:1080
#   ./run-tbench.sh run <task>
#
# Known environment issues (terminal-bench test infrastructure, NOT iacoder):
#   - Some tasks (sparql-university, etc.) download from astral.sh; use proxy if needed

set -euo pipefail

# ---- paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$(cd "$SCRIPT_DIR/dist" && pwd)"
JOBS_DIR="$(cd "$SCRIPT_DIR/jobs" && pwd)"
DATASET_DIR="$SCRIPT_DIR/terminal-bench"
VENV_BIN="$SCRIPT_DIR/.venv/bin"
FAILED_TASKS_FILE="$SCRIPT_DIR/.failed_tasks.txt"

# ---- helpers ----------------------------------------------------------------
log()  { printf '\033[1;36m[tbench]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

check_docker() {
  docker info >/dev/null 2>&1 || fail "docker daemon not reachable — start docker/colima and retry"
}

# Check and pull missing terminal-bench images
check_images() {
  local dataset_dir="$1"
  local missing=()
  local cached=0
  local total=0

  if [ ! -d "$dataset_dir" ]; then
    return
  fi

  log "Checking terminal-bench images..."

  # Get all cached images once into a temp file for fast lookup
  local cached_list=$(mktemp)
  docker images --format "{{.Repository}}:{{.Tag}}" 2>/dev/null > "$cached_list"

  for task_dir in "$dataset_dir"/*; do
    if [ -d "$task_dir" ]; then
      local task=$(basename "$task_dir")
      local image="alexgshaw/$task:20251031"
      total=$((total + 1))

      if grep -qxF "$image" "$cached_list" 2>/dev/null; then
        cached=$((cached + 1))
      else
        missing+=("$image")
      fi
    fi
  done

  rm -f "$cached_list"
  log "Images: $cached/$total cached"

  if [ ${#missing[@]} -gt 0 ]; then
    log "Pulling ${#missing[@]} missing images (this may take a while)..."
    local batch_size=5  # Reduced from 10 to avoid rate limiting
    for i in $(seq 0 $batch_size ${#missing[@]}); do
      local batch=("${missing[@]:$i:$batch_size}")
      if [ ${#batch[@]} -gt 0 ]; then
        for img in "${batch[@]}"; do
          if ! docker pull "$img" 2>&1 | grep -q "Pull complete\|Image is up to date\|already exists"; then
            warn "Failed to pull $img, retrying..."
            docker pull "$img" >/dev/null 2>&1 || warn "Still failed to pull $img"
          fi &
        done
        wait
      fi
    done
    log "All images cached."
  fi
}

# Pre-build environments for tasks that need heavy package downloads
# This runs the test.sh once to cache pip packages in the container
pre_build_environments() {
  local dataset_dir="$1"
  local tasks_to_prebuild=(
    "torch-pipeline-parallelism"
    "torch-tensor-parallelism"
  )

  if [ ! -d "$dataset_dir" ]; then
    return
  fi

  log "Pre-building environments for heavy tasks..."

  for task in "${tasks_to_prebuild[@]}"; do
    local task_dir="$dataset_dir/$task"
    if [ ! -d "$task_dir" ]; then
      continue
    fi

    local base_image="alexgshaw/$task:20251031"
    local baked_image="alexgshaw/$task:cached-20251031"

    # Check if baked image already exists
    if docker image inspect "$baked_image" >/dev/null 2>&1; then
      log "  $task: using cached image $baked_image"
      docker tag "$baked_image" "$base_image" >/dev/null 2>&1
      continue
    fi

    # Check if base image exists
    if ! docker image inspect "$base_image" >/dev/null 2>&1; then
      log "  Skipping $task (base image not cached)"
      continue
    fi

    # Get package requirements from test.sh
    local test_script="$task_dir/tests/test.sh"
    if [ ! -f "$test_script" ]; then
      continue
    fi

    log "  Pre-building $task (this may take 10-20 minutes)..."

    # Extract uvx packages from test.sh (e.g., -w torch==2.7.0)
    local packages=$(grep -o '\-w [^ ]*' "$test_script" | tr '\n' ' ')

    # Get proxy settings for container
    local gateway_ip=$(docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || echo "172.17.0.1")
    local proxy_val="${all_proxy:-${ALL_PROXY:-}}"
    # Add Ubuntu repos and common domains to no_proxy for apt
    local no_proxy_val="localhost,127.0.0.1,$gateway_ip,archive.ubuntu.com,security.ubuntu.com,${no_proxy:-${NO_PROXY:-}}"
    proxy_val="${proxy_val/socks:\/\//socks5:\/\/}"  # Normalize socks:// to socks5://

    # Run uvx to cache packages, then commit
    local container_id="tbench-prebuild-$task-$$"

    # Build docker run command with proxy
    local docker_cmd="docker run --name $container_id --network host"
    if [ -n "$proxy_val" ]; then
      docker_cmd="$docker_cmd -e all_proxy=$proxy_val -e ALL_PROXY=$proxy_val"
      docker_cmd="$docker_cmd -e http_proxy=$proxy_val -e HTTP_PROXY=$proxy_val"
      docker_cmd="$docker_cmd -e https_proxy=$proxy_val -e HTTPS_PROXY=$proxy_val"
      docker_cmd="$docker_cmd -e no_proxy=$no_proxy_val -e NO_PROXY=$no_proxy_val"
    fi
    docker_cmd="$docker_cmd $base_image bash -c '
      apt-get update -qq &&
      apt-get install -y curl &&
      curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh &&
      source \$HOME/.local/bin/env &&
      uvx -p 3.13 $packages --help >/dev/null 2>&1
    '"

    # Install curl and uv, then pre-cache packages
    eval "$docker_cmd" >/dev/null 2>&1

    # Always commit (packages are cached even if tests fail)
    docker commit "$container_id" "$baked_image" >/dev/null 2>&1
    docker tag "$baked_image" "$base_image" >/dev/null 2>&1
    docker rm "$container_id" >/dev/null 2>&1

    log "  $task: pre-build complete (image: $baked_image)"
  done

  log "Pre-build complete."
}

# ---- preflight --------------------------------------------------------------
require_cmd docker
require_cmd python3

# Normalize proxy URLs: httpx doesn't accept socks://, only socks5://
# This affects both Harbor's httpx client and container envs
if [ -n "${all_proxy:-}" ]; then
  export all_proxy="${all_proxy/socks:\/\//socks5:\/\/}"
fi
if [ -n "${ALL_PROXY:-}" ]; then
  export ALL_PROXY="${ALL_PROXY/socks:\/\//socks5:\/\/}"
fi

# Auto-detect and export API keys for iacoder providers
# This allows iacoder to use API keys from the user's environment
# Keys are passed to containers via Harbor's --ek flag
for provider_var in GLM_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY IACODER_API_KEY; do
  if [ -n "${!provider_var:-}" ]; then
    export "$provider_var"
  fi
done

# Activate venv if exists
if [ -f "$VENV_BIN/activate" ]; then
  # Set ZSH_VERSION to avoid "unbound variable" error in set -u mode
  ZSH_VERSION=${ZSH_VERSION:-} source "$VENV_BIN/activate"
  HARBOR="$VENV_BIN/harbor"
elif [ -f "$VENV_BIN/harbor" ]; then
  HARBOR="$VENV_BIN/harbor"
elif command -v harbor >/dev/null 2>&1; then
  HARBOR="harbor"
else
  fail "harbor not found. Run 'uv pip install -e .' in $SCRIPT_DIR first"
fi

# Use harbor function wrapper
# Preserve proxy settings for Harbor and containers
harbor() {
  "$HARBOR" "$@"
}

# Get proxy settings for container
get_proxy_envs() {
  local env_args=""
  # Get gateway IP for no_proxy (Harbor containers may not have host.docker.internal)
  local gateway_ip=$(docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || echo "172.17.0.1")

  # Handle proxy settings
  if [ -n "${all_proxy:-}" ] || [ -n "${ALL_PROXY:-}" ]; then
    local proxy_val="${all_proxy:-${ALL_PROXY:-}}"
    local no_proxy_val="${no_proxy:-${NO_PROXY:-}}"
    # Normalize socks:// to socks5:// for httpx compatibility
    proxy_val="${proxy_val/socks:\/\//socks5:\/\/}"
    # Add localhost and gateway IP to no_proxy
    no_proxy_val="localhost,127.0.0.1,$gateway_ip,$no_proxy_val"

    env_args="$env_args --ek all_proxy=$proxy_val"
    env_args="$env_args --ek ALL_PROXY=$proxy_val"
    env_args="$env_args --ek http_proxy=$proxy_val"
    env_args="$env_args --ek HTTP_PROXY=$proxy_val"
    env_args="$env_args --ek https_proxy=$proxy_val"
    env_args="$env_args --ek HTTPS_PROXY=$proxy_val"
    env_args="$env_args --ek no_proxy=$no_proxy_val"
    env_args="$env_args --ek NO_PROXY=$no_proxy_val"
  elif [ -n "${no_proxy:-}" ] || [ -n "${NO_PROXY:-}" ]; then
    local no_proxy_val="${no_proxy:-${NO_PROXY:-}}"
    no_proxy_val="localhost,127.0.0.1,$gateway_ip,$no_proxy_val"
    env_args="$env_args --ek no_proxy=$no_proxy_val"
    env_args="$env_args --ek NO_PROXY=$no_proxy_val"
  fi

  # Forward API keys to container (iacoder needs them to call LLM providers)
  # Priority: provider-specific key (GLM_API_KEY, etc.) > generic IACODER_API_KEY
  for key_var in GLM_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY IACODER_API_KEY; do
    if [ -n "${!key_var:-}" ]; then
      env_args="$env_args --ek $key_var=${!key_var}"
    fi
  done

  # Forward IACODER_MAX_TURNS if set
  if [ -n "${IACODER_MAX_TURNS:-}" ]; then
    env_args="$env_args --ek IACODER_MAX_TURNS=${IACODER_MAX_TURNS}"
  fi

  echo "$env_args"
}

# Save failed task to file
save_failed_task() {
  local task="$1"
  mkdir -p "$(dirname "$FAILED_TASKS_FILE")"
  # Avoid duplicates
  if ! grep -qx "^$task$" "$FAILED_TASKS_FILE" 2>/dev/null; then
    echo "$task" >> "$FAILED_TASKS_FILE"
  fi
}

# Get list of failed tasks
get_failed_tasks() {
  if [ -f "$FAILED_TASKS_FILE" ]; then
    grep -v '^[[:space:]]*$' "$FAILED_TASKS_FILE" 2>/dev/null || true
  fi
}

# Clear failed tasks file
clear_failed_tasks() {
  rm -f "$FAILED_TASKS_FILE"
}

# Count failed tasks
count_failed_tasks() {
  local count=0
  if [ -f "$FAILED_TASKS_FILE" ]; then
    count=$(grep -c '^[[:space:]]*$' -v "$FAILED_TASKS_FILE" 2>/dev/null || echo "0")
  fi
  echo "$count"
}

# Ensure dataset is downloaded
if [ ! -d "$DATASET_DIR" ]; then
  log "Downloading terminal-bench@2.0 dataset..."
  harbor download terminal-bench@2.0 || fail "Failed to download dataset"
fi

# Check if iacoder CLI exists
# Try in order: env var, PATH, repo release dir, repo debug dir
IACODER_BIN="${IACODER_BIN:-}"
if [ -z "$IACODER_BIN" ]; then
  if IACODER_BIN="$(command -v iacoder 2>/dev/null)"; then
    : # found - command -v returns full path
  elif [ -x "$REPO_ROOT/target/release/iacoder" ]; then
    IACODER_BIN="$REPO_ROOT/target/release/iacoder"
  elif [ -x "$REPO_ROOT/target/debug/iacoder" ]; then
    IACODER_BIN="$REPO_ROOT/target/debug/iacoder"
  fi
fi

if [ -z "$IACODER_BIN" ] || [ ! -x "$IACODER_BIN" ]; then
  warn "iacoder CLI not found. Will use Harbor to install from binary."
  IACODER_NEED_BINARY=1
else
  IACODER_VERSION=$("$IACODER_BIN" --version 2>/dev/null || echo "unknown")
  log "Found iacoder: $IACODER_VERSION ($IACODER_BIN)"
fi

# Install socksio if SOCKS proxy is set
if [ -n "${all_proxy:-}" ] || [ -n "${ALL_PROXY:-}" ]; then
  proxy_val="${all_proxy:-${ALL_PROXY:-}}"
  if [[ "$proxy_val" == socks5://* ]]; then
    if ! "$VENV_BIN/python" -c "import socksio" 2>/dev/null; then
      log "Installing socksio for SOCKS proxy support..."
      if command -v uv >/dev/null 2>&1; then
        uv pip install socksio -q
      else
        log "WARNING: Cannot install socksio. Install it manually: pip install socksio"
      fi
    fi
  fi
fi

# ---- commands ---------------------------------------------------------------

list_tasks() {
  log "Available tasks in terminal-bench@2.0 (89 tasks):"
  echo ""
  ls -1 "$DATASET_DIR" | sort | nl -w3 -s'. '
  echo ""
  log "Usage: $0 run <task-name>"
  echo ""
  log "Sample tasks:"
  echo "  - chess-best-move       (vision task)"
  echo "  - adaptive-rejection-sampler"
  echo "  - compile-compcert"
  echo "  - db-wal-recovery"
  echo "  - regex-log"
}

run_oracle() {
  local task="$1"
  check_docker
  check_images "$DATASET_DIR"

  local oracle_jobs="$JOBS_DIR/oracle-$(date +%Y%m%d-%H%M%S)"
  log "Running oracle baseline for task: $task"
  log "Jobs dir: $oracle_jobs"

  harbor run \
    -d terminal-bench@2.0 \
    -a oracle \
    -i "$task" \
    -n 1 \
    --jobs-dir "$oracle_jobs" \
    --quiet

  # Find and show result
  local run_dir="$(ls -1dt "$oracle_jobs"/*/ 2>/dev/null | head -n 1 || true)"
  local reward_file="$(find "${run_dir:-$oracle_jobs}" -name reward.txt -print -quit 2>/dev/null || true)"

  if [ -n "$reward_file" ]; then
    local reward="$(cat "$reward_file")"
    echo ""
    log "Oracle result:"
    echo "  Task: $task"
    echo "  Reward: $reward"
    echo "  File: $reward_file"
  else
    warn "No reward.txt found. Check logs in $oracle_jobs"
  fi
}

run_iacoder() {
  local task="$1"
  local model="${IACODER_MODEL:-glm-coding}"  # Default model (uses config alias)

  check_docker
  check_images "$DATASET_DIR"

  # Always setup binary for Harbor container (it can't use host binary directly)
  if [ -z "${IACODER_DEV_BINARY_URL:-}" ]; then
    # Build and serve binary
    setup_binary
  fi

  local iacoder_jobs="$JOBS_DIR/iacoder-$(date +%Y%m%d-%H%M%S)"
  log "Running iacoder for task: $task"
  log "Jobs dir: $iacoder_jobs"

  # Export IACODER_DEV_BINARY_URL for Harbor
  export IACODER_DEV_BINARY_URL

  # Get proxy settings
  local proxy_envs=$(get_proxy_envs)

  harbor run \
    -d terminal-bench@2.0 \
    --agent-import-path iacoder_tbench:IacoderAgent \
    -m "$model" \
    -i "$task" \
    -n 1 \
    --jobs-dir "$iacoder_jobs" \
    $proxy_envs \
    --quiet

  # Find and show result
  local run_dir="$(ls -1dt "$iacoder_jobs"/*/ 2>/dev/null | head -n 1 || true)"
  local reward_file="$(find "${run_dir:-$iacoder_jobs}" -name reward.txt -print -quit 2>/dev/null || true)"

  if [ -n "$reward_file" ]; then
    local reward="$(cat "$reward_file")"
    echo ""
    log "iacoder result:"
    echo "  Task: $task"
    echo "  Reward: $reward"
    echo "  File: $reward_file"

    # Save failed task (reward != 1.0)
    if [ "$reward" != "1.0" ]; then
      save_failed_task "$task"
      log "Saved to failed tasks list"
    fi
  else
    warn "No reward.txt found. Check logs in $iacoder_jobs"
  fi
}

run_all() {
  local model="${IACODER_MODEL:-glm-coding}"  # Default model (uses config alias)

  check_docker
  check_images "$DATASET_DIR"
  pre_build_environments "$DATASET_DIR"  # Cache pip packages for heavy tasks

  # Always setup binary for Harbor container
  if [ -z "${IACODER_DEV_BINARY_URL:-}" ]; then
    # Build and serve binary
    setup_binary
  fi

  local all_jobs="$JOBS_DIR/all-$(date +%Y%m%d-%H%M%S)"
  log "Running iacoder on ALL 89 tasks (this will take a while!)"
  log "Jobs dir: $all_jobs"
  log "Concurrency: ${IACODER_CONCURRENCY:-4}"
  log "Model: $model"
  log "Max turns: ${IACODER_MAX_TURNS:-200}"

  # Export IACODER_DEV_BINARY_URL for Harbor
  export IACODER_DEV_BINARY_URL

  # Get proxy settings
  local proxy_envs=$(get_proxy_envs)

  harbor run \
    -d terminal-bench@2.0 \
    --agent-import-path iacoder_tbench:IacoderAgent \
    -m "$model" \
    -n "${IACODER_CONCURRENCY:-4}" \
    --jobs-dir "$all_jobs" \
    $proxy_envs

  # Save failed tasks from result.json
  local result_file="$all_jobs/result.json"
  if [ -f "$result_file" ]; then
    log "Parsing results to save failed tasks..."
    # Extract tasks with reward 0.0 using python or jq
    if command -v python3 >/dev/null 2>&1; then
      python3 -c "
import json
import sys
try:
    with open('$result_file') as f:
        data = json.load(f)
    rewards = data.get('stats', {}).get('evals', {}).get('iacoder__glm-coding__terminal-bench', {}).get('reward_stats', {}).get('reward', {})
    failed = rewards.get('0.0', [])
    for task in failed:
        # Strip the unique ID suffix if present (task__ID)
        task_name = task.split('__')[0]
        print(task_name)
except Exception as e:
    pass
" 2>/dev/null | while read -r task; do
        save_failed_task "$task"
      done
      log "Failed tasks saved to $FAILED_TASKS_FILE"
    fi
  fi
}

setup_binary() {
  local arch="$(uname -m)"
  local target
  case "$arch" in
    x86_64)  target="x86_64-unknown-linux-musl" ;;
    aarch64) target="aarch64-unknown-linux-musl" ;;
    *)       target="x86_64-unknown-linux-musl" ;;
  esac

  local tarball_name="iacoder-${target}.tar.gz"
  local tarball_path="$DIST_DIR/$tarball_name"
  local port="${IACODER_HTTP_PORT:-8765}"

  mkdir -p "$DIST_DIR"

  if [ ! -f "$tarball_path" ] || [ "${IACODER_FORCE_BUILD:-0}" = "1" ]; then
    # Build static binary for maximum compatibility
    log "Building static musl binary..."
    cd "$REPO_ROOT"

    # Install musl target if needed
    if ! rustup target list --installed | grep -q "x86_64-unknown-linux-musl"; then
      log "Installing musl target..."
      rustup target add x86_64-unknown-linux-musl
    fi

    # Build static binary
    RUSTFLAGS="-C target-feature=+crt-static" \
      cargo build --release --target x86_64-unknown-linux-musl -p iacoder-cli

    local bin_path="$REPO_ROOT/target/x86_64-unknown-linux-musl/release/iacoder"

    [ -f "$bin_path" ] || fail "Static binary build failed. Try building manually:
  cd $REPO_ROOT
  rustup target add x86_64-unknown-linux-musl
  RUSTFLAGS='-C target-feature=+crt-static' cargo build --release --target x86_64-unknown-linux-musl -p iacoder-cli"

    # Create temporary directory for packaging
    local tmp_dir="$DIST_DIR/tmp-pack"
    rm -rf "$tmp_dir"
    mkdir -p "$tmp_dir/bin"
    mkdir -p "$tmp_dir/config"

    # Copy binary
    cp "$bin_path" "$tmp_dir/bin/iacoder"

    # Copy config if exists
    if [ -f "$HOME/.config/iacoder/config.toml" ]; then
      cp "$HOME/.config/iacoder/config.toml" "$tmp_dir/config/"
      log "Config included: ~/.config/iacoder/config.toml"
    fi

    # Create tarball
    tar -czf "$tarball_path" -C "$tmp_dir" bin config
    rm -rf "$tmp_dir"

    log "Binary packed: $tarball_path"
    cd "$SCRIPT_DIR"
  fi

  # Start http server if not already running
  if ! curl -fsS "http://127.0.0.1:$port/$tarball_name" -o /dev/null 2>/dev/null; then
    log "Starting http.server on port $port..."
    (cd "$DIST_DIR" && python3 -m http.server "$port" >/dev/null 2>&1) &
    HTTP_PID=$!
    sleep 1

    # Wait for server to be ready
    for _ in $(seq 1 10); do
      if curl -fsS "http://127.0.0.1:$port/$tarball_name" -o /dev/null 2>/dev/null; then
        break
      fi
      sleep 0.5
    done
  fi

  # Use bridge gateway IP instead of host.docker.internal (not always reachable)
  # Harbor containers may not have host.docker.internal configured
  local gateway_ip=$(docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || echo "172.17.0.1")
  export IACODER_DEV_BINARY_URL="http://$gateway_ip:$port/$tarball_name"
  log "IACODER_DEV_BINARY_URL=$IACODER_DEV_BINARY_URL"
}

# ---- failed tasks command ----------------------------------------------------
run_failed_tasks() {
  check_docker
  check_images "$DATASET_DIR"
  pre_build_environments "$DATASET_DIR"  # Cache pip packages for heavy tasks

  local failed_tasks=($(get_failed_tasks))
  local count=${#failed_tasks[@]}

  if [ "$count" -eq 0 ]; then
    log "No failed tasks found."
    return 0
  fi

  log "Found $count failed task(s):"
  printf '  - %s\n' "${failed_tasks[@]}"
  echo ""

  if [ "${RETRY_FAILED:-0}" = "1" ]; then
    # Use Harbor batch mode for concurrent execution
    local model="${IACODER_MODEL:-glm-coding}"
    local concurrency="${IACODER_CONCURRENCY:-4}"
    local retry_jobs="$JOBS_DIR/retry-$(date +%Y%m%d-%H%M%S)"

    log "Re-running $count failed task(s) with concurrency: $concurrency"
    log "Jobs dir: $retry_jobs"
    log "Max turns: ${IACODER_MAX_TURNS:-200}"

    # Setup binary if needed
    if [ -z "${IACODER_DEV_BINARY_URL:-}" ]; then
      setup_binary
    fi

    export IACODER_DEV_BINARY_URL
    local proxy_envs=$(get_proxy_envs)

    # Build task list for Harbor
    local task_list=""
    for task in "${failed_tasks[@]}"; do
      task_list="$task_list -i $task"
    done

    # Run all tasks concurrently via Harbor
    harbor run \
      -d terminal-bench@2.0 \
      --agent-import-path iacoder_tbench:IacoderAgent \
      -m "$model" \
      -n "$concurrency" \
      --jobs-dir "$retry_jobs" \
      $proxy_envs \
      $task_list

    # Parse results and update failed tasks list
    local result_file="$retry_jobs/result.json"
    if [ -f "$result_file" ]; then
      # Clear and rebuild failed tasks list from results
      > "$FAILED_TASKS_FILE"

      python3 -c "
import json
import sys
try:
    with open('$result_file') as f:
        data = json.load(f)
    rewards = data.get('stats', {}).get('evals', {}).get('iacoder__glm-coding__terminal-bench', {}).get('reward_stats', {}).get('reward', {})
    failed = rewards.get('0.0', [])
    success = rewards.get('1.0', [])
    for task in failed:
        # Strip the unique ID suffix
        print(task.split('__')[0])
except Exception as e:
    sys.exit(0)
" 2>/dev/null | while read -r task; do
        echo "$task" >> "$FAILED_TASKS_FILE"
      done

      local new_failed=$(count_failed_tasks)
      local success_count=$((count - new_failed))
      echo ""
      log "========================================"
      log "Retry summary:"
      log "  Total: $count"
      log "  Success: $success_count"
      log "  Still failed: $new_failed"
      log "========================================"
    fi
  else
    log "To re-run failed tasks, use:"
    echo "  $0 failed --retry"
    echo "  or: RETRY_FAILED=1 $0 failed"
  fi
}

# ---- main --------------------------------------------------------------------
ACTION="${1:-list}"
shift || true

case "$ACTION" in
  list|ls)
    list_tasks
    ;;
  prebuild)
    check_docker
    check_images "$DATASET_DIR"
    pre_build_environments "$DATASET_DIR"
    ;;
  oracle)
    [ -z "${1:-}" ] && fail "Usage: $0 oracle <task-name>"
    run_oracle "$1"
    ;;
  run)
    if [ -z "${1:-}" ]; then
      fail "Usage: $0 run <task-name> | $0 run --all | $0 run --failed"
    fi

    if [ "$1" = "--all" ]; then
      run_all
    elif [ "$1" = "--failed" ]; then
      # Run failed tasks
      RETRY_FAILED=1 run_failed_tasks
    else
      task="$1"
      shift

      # Check if --all flag is passed (run both oracle and iacoder)
      if [ "${1:-}" = "--all" ] || [ "${RUN_ALL_AGENTS:-0}" = "1" ]; then
        log "Running oracle baseline first..."
        run_oracle "$task"
        echo ""
        log "Running iacoder..."
        run_iacoder "$task"
      else
        run_iacoder "$task"
      fi
    fi
    ;;
  failed)
    if [ "${1:-}" = "--retry" ]; then
      RETRY_FAILED=1 run_failed_tasks
    elif [ "${1:-}" = "--clear" ]; then
      log "Clearing failed tasks list..."
      clear_failed_tasks
      log "Failed tasks list cleared."
    else
      run_failed_tasks
    fi
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo ""
    echo "Usage:"
    echo "  $0 list                  - List available tasks"
    echo "  $0 prebuild              - Pre-build environments (cache pip packages)"
    echo "  $0 oracle <task>         - Run oracle baseline only"
    echo "  $0 run <task>            - Run iacoder only"
    echo "  $0 run <task> --all      - Run both oracle and iacoder"
    echo "  $0 run --all             - Run all 89 tasks with iacoder"
    echo "  $0 run --failed          - Re-run failed tasks"
    echo "  $0 failed [--retry]      - List or re-run failed tasks"
    echo "  $0 failed --clear        - Clear failed tasks list"
    echo ""
    echo "Environment variables:"
    echo "  IACODER_MODEL            - Model to use (default: glm-coding)"
    echo "  IACODER_CONCURRENCY      - Concurrent tasks for --all (default: 4)"
    echo "  IACODER_MAX_TURNS        - Max turns per task (default: 200)"
    echo "  IACODER_DEV_BINARY_URL   - URL to iacoder binary tarball"
    echo "  IACODER_FORCE_BUILD      - Force rebuild binary"
    echo "  all_proxy / ALL_PROXY    - Proxy URL for containers (e.g. socks5://127.0.0.1:7897)"
    echo "  no_proxy / NO_PROXY      - No-proxy list for containers"
    echo ""
    echo "Note: iacoder uses its own config for API keys. Provider-specific API keys"
    echo ""
    exit 1
    ;;
esac
