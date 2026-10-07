#!/bin/sh
# ADR-0232 — mechanical no-regression gate wrapper.
#
# Two modes:
#   baseline  — run the repo's pre-existing check ONCE at run start (repo still
#               pristine) and record the set of currently-passing check
#               identifiers. This mode NEVER blocks the run (always exit 0): it
#               only captures a fact.
#   check     — re-run the same command at closeout, diff the pass-set against
#               the baseline, and exit non-zero if any identifier that passed at
#               baseline no longer passes (green -> red = regression). Injected
#               as `[verify] command`, so the engine runs it itself as the
#               PreFinal gate (ADR-0007) and a non-zero exit blocks completion —
#               independent of whatever command the agent bound.
#
# The gate is deliberately runner-agnostic: it only does set operations on the
# command's stdout. The framework-specific part — "how do I list the passing
# pre-existing checks, one identifier per line" — is the COMMAND's job, supplied
# by the bench (host policy), never encoded here. That is why this script names
# no language, framework, or test runner (Article I / feedback_no_overfit).
#
# Command source (first non-empty wins):
#   1. $IACODER_NO_REGRESSION_COMMAND (env; the per-task override)
#   2. the file at $IACODER_NOREG_COMMAND_FILE (default /iacoder-cfg/no_regression_command)
#
# Contract: the command's STDOUT is the set of passing pre-existing check
# identifiers, one per line. Its exit code is not consulted for the verdict —
# the pass-set difference is — so a command that legitimately lists a partial
# pass-set (some pre-existing checks already red at baseline) is handled
# correctly: only checks green at baseline can regress.

set -u

mode="${1:-}"
baseline_file="${IACODER_NOREG_BASELINE:-/iacoder-cfg/no_regression_baseline}"
command_file="${IACODER_NOREG_COMMAND_FILE:-/iacoder-cfg/no_regression_command}"
# `/logs/agent`, not `/logs`: the trial harvests `agent`, `artifacts` and
# `verifier`. A file at the `/logs` root is collected by nothing, which would
# leave D3 ("make protection countable") producing zero countable records.
status_file="${IACODER_NOREG_STATUS:-/logs/agent/no-regression-status}"

# ADR-0255 D1/D3: say which of the three outcomes this run reached.
#
# `check` used to exit 0 for BOTH "ran, nothing regressed" and "could not run at
# all" — so a gate that no-opped was indistinguishable from a gate that passed,
# in the exit code AND in every log the engine keeps. A whole 113-task batch was
# read as protected on that basis; it was not, and the only tell was a 2 ms hook
# duration that happens to be recorded for unrelated reasons.
#
# The line goes to stderr (visible in any captured output) and, when the path is
# writable, is appended to a file the trial harvests — so "how many tasks were
# actually protected" becomes something to count rather than something to infer
# from the absence of a string that was never guaranteed to be present.
emit_status() {
  echo "no-regression: $*" >&2
  if [ -n "${status_file}" ]; then
    dir=$(dirname "$status_file")
    [ -d "$dir" ] || mkdir -p "$dir" 2>/dev/null || return 0
    echo "$*" >> "$status_file" 2>/dev/null || true
  fi
}

setsize() { [ -s "$1" ] && wc -l < "$1" | tr -d " " || echo 0; }

# Resolve the check command.
cmd="${IACODER_NO_REGRESSION_COMMAND:-}"
if [ -z "$cmd" ] && [ -f "$command_file" ]; then
  cmd=$(cat "$command_file")
fi
if [ -z "$cmd" ]; then
  # No command configured. Nothing to enforce — this bench did not opt in.
  # Never block on a gate that was never armed.
  emit_status "status=unchecked reason=no-command mode=${mode}"
  echo "no-regression gate: no command configured; nothing to check." >&2
  exit 0
fi

# Run the check command and emit its stdout as a sorted-unique line-set.
# stderr is discarded from the pass-set (it is not a pass identifier) but the
# command still runs with a normal shell so it can build/test as needed.
#
# ADR-0232 S1b: each run is inner-`timeout`-bounded when `timeout` is available,
# so a hung suite cannot run past the outer window; a killed/partial run just
# yields a smaller pass-set, and the flaky double-run below keeps that from
# hard-blocking on its own. `IACODER_NOREG_RUN_TIMEOUT` (seconds) tunes it.
# Returns the CHECK COMMAND's exit status, not `sort`'s. In the pipeline form
# `$?` was always `sort`'s, so "the command exploded" and "the command printed
# nothing" were the same observation — and the gate reported the second, a claim
# about output, for a command that had failed.
# ADR-0319 D2: a suite run's budget follows how long the same suite took at
# baseline, in the same container, minutes earlier. A fixed 900 s is 40x what a
# 22 s suite needs; measured, a hung check ran 900 s, looked like a mass
# regression, was re-run for flakiness, and hung another 900 s into the engine's
# hook timeout. clamp(factor x baseline, floor, 900). An explicit
# IACODER_NOREG_RUN_TIMEOUT still wins; with no recorded duration it stays 900.
noreg_run_budget() {
  if [ -n "${IACODER_NOREG_RUN_TIMEOUT:-}" ]; then
    echo "$IACODER_NOREG_RUN_TIMEOUT"
    return
  fi
  cap=900
  secs=$(tr -dc '0-9' < "${baseline_file}.secs" 2>/dev/null)
  if [ -z "$secs" ]; then
    echo "$cap"
    return
  fi
  factor="${IACODER_NOREG_TIMEOUT_FACTOR:-10}"
  floor="${IACODER_NOREG_TIMEOUT_FLOOR:-120}"
  b=$((secs * factor))
  [ "$b" -lt "$floor" ] && b=$floor
  [ "$b" -gt "$cap" ] && b=$cap
  echo "$b"
}

run_passset() {
  run_to=$(noreg_run_budget)
  raw="${TMPDIR:-/tmp}/noreg-passset.$$"
  # The command's stderr used to go to /dev/null. The pass-lister explains
  # itself there — "no known test runner detected in /app", "js path needs jq +
  # local vitest/jest" — and discarding it left every distinct cause wearing the
  # same label. A measured batch had at least three behind one
  # `command-produced-nothing`: an unsupported runner (mocha), an undetected
  # one, and a supported one that still yielded nothing. Keep the last line.
  err="${TMPDIR:-/tmp}/noreg-passset-err.$$"
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 10 "$run_to" sh -c "$cmd" > "$raw" 2>"$err"
  else
    sh -c "$cmd" > "$raw" 2>"$err"
  fi
  cmd_rc=$?
  cmd_why=$(tail -n 1 "$err" 2>/dev/null | tr -d "\r" | cut -c1-160)
  sort -u < "$raw" 2>/dev/null
  rm -f "$raw" "$err"
  return "$cmd_rc"
}

# Regressions = identifiers present in baseline but absent from the given
# pass-set. `comm -23 A B` prints lines only in A (baseline), given sorted
# inputs; the pass-set arrives already sorted from `run_passset`.
regressions_against_baseline() {
  comm -23 "$baseline_file" - <<EOF
$1
EOF
}

case "$mode" in
  baseline)
    # ADR-0255 D2. Three outcomes, and the first is why this is not a one-liner:
    # redirecting into a path without first checking it is writable means that
    # when the redirect fails the command never runs AND the gate reports
    # `command-produced-nothing` — a claim about the output of a command that
    # never executed. That is the exact defect this ADR exists to remove,
    # reintroduced by an earlier version of its own fix.
    # The subshell is load-bearing: `:` is a POSIX *special* builtin, and a
    # redirection error on one is fatal to a non-interactive shell. Written as
    # `! : > "$f"`, dash aborts the whole script with status 2 instead of taking
    # this branch — which CI caught and a bash-as-sh dev box did not.
    if ! ( : > "$baseline_file" ) 2>/dev/null; then
      emit_status "status=baseline-error reason=cannot-write-baseline"
      exit 0
    fi
    # ADR-0319 D2: no stale duration may bound this run, and this run's own
    # duration is what bounds every check after it.
    rm -f "${baseline_file}.secs"
    t0=$(date +%s 2>/dev/null)
    run_passset > "$baseline_file" 2>/dev/null
    base_rc=$?
    t1=$(date +%s 2>/dev/null)
    if [ -n "$t0" ] && [ -n "$t1" ]; then
      echo $((t1 - t0)) > "${baseline_file}.secs" 2>/dev/null || true
    fi
    if [ -s "$baseline_file" ]; then
      emit_status "status=baseline-captured count=$(setsize "$baseline_file")"
    elif [ "$base_rc" != 0 ]; then
      emit_status "status=baseline-empty reason=command-failed rc=${base_rc} why=${cmd_why:-<no-stderr>}"
    else
      emit_status "status=baseline-empty reason=command-produced-nothing why=${cmd_why:-<no-stderr>}"
    fi
    # Capturing a baseline must never fail the run: it records a fact before the
    # agent has done anything, and an empty capture (command not runnable yet)
    # simply means the gate has nothing to protect. Always succeed.
    exit 0
    ;;
  check)
    if [ ! -s "$baseline_file" ]; then
      # No baseline pass-set (empty or absent): the pre-existing check produced
      # nothing to compare against, so there is no green->red to detect. Do not
      # block — a gate with no baseline is a no-op, not a failure (documented
      # limitation, ADR-0232 §粒度的诚实交代).
      emit_status "status=unchecked reason=no-baseline"
      echo "no-regression gate: empty baseline pass-set; nothing to compare — not blocking." >&2
      exit 0
    fi
    base_n=$(setsize "$baseline_file")
    cur=$(run_passset)
    cur_n=$(printf "%s\n" "$cur" | sed "/^$/d" | wc -l | tr -d " ")
    reg1=$(regressions_against_baseline "$cur")
    if [ -z "$reg1" ]; then
      emit_status "status=checked baseline=${base_n} current=${cur_n} regressions=0 budget=$(noreg_run_budget)"
      exit 0
    fi
    # ADR-0232 S1b flaky/transient guard: a suspected regression is confirmed
    # only if the identifier is STILL missing on an independent second run. A
    # flaky test (or a transient closeout infra hiccup that briefly empties the
    # pass-set) shows up in one run but not the other, so the intersection of
    # the two regression sets drops it. A REAL regression — and the catastrophic
    # whole-suite build break — is missing in both runs, so it survives the
    # intersection and blocks. Cost: one extra suite run, only when a regression
    # is already suspected.
    reg2=$(regressions_against_baseline "$(run_passset)")
    t1=$(mktemp)
    t2=$(mktemp)
    printf '%s\n' "$reg1" | sed '/^$/d' | sort -u > "$t1"
    printf '%s\n' "$reg2" | sed '/^$/d' | sort -u > "$t2"
    regressions=$(comm -12 "$t1" "$t2")
    rm -f "$t1" "$t2"
    if [ -n "$regressions" ]; then
      emit_status "status=regressed budget=$(noreg_run_budget) baseline=${base_n} current=${cur_n} regressions=$(printf "%s\n" "$regressions" | sed "/^$/d" | wc -l | tr -d " ") first=$(printf "%s\n" "$regressions" | sed "/^$/d" | head -1)"
      echo "no-regression gate: the following pre-existing checks passed at baseline but fail on two independent re-runs (regression introduced):" >&2
      echo "$regressions" >&2
      echo "Fix the cause so these pass again; this gate blocks the run from finishing." >&2
      exit 1
    fi
    emit_status "status=checked reason=suspected-not-reproduced budget=$(noreg_run_budget) baseline=${base_n} current=${cur_n} regressions=0"
    echo "no-regression gate: suspected regressions did not reproduce on re-run (flaky/transient) — not blocking." >&2
    exit 0
    ;;
  *)
    echo "usage: $0 baseline|check" >&2
    exit 2
    ;;
esac
