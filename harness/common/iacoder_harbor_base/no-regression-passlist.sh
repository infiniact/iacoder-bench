#!/bin/sh
# ADR-0232 S1b — auto-detecting pre-existing-suite pass-lister (HOST POLICY).
#
# Emits to stdout one stable identifier per PASSING pre-existing test found in
# the repo at the current working directory — sorted, machine-readable. This is
# the VALUE consumed by `no-regression-gate.sh` (the neutral wrapper): the
# wrapper snapshots this set at run start (baseline, repo pristine) and re-runs
# it at closeout, then blocks on any identifier that was green at baseline and
# is now missing (green -> red = regression).
#
# Why the runner names live HERE and only here: detecting the repo's test runner
# and speaking its machine-readable dialect is HOST POLICY (ADR-0232 D3). Naming
# cargo / pytest / go / vitest in THIS script is deliberate and allowed — it is
# a fact about the family of repos an eval scores. The neutral wrapper
# (`no-regression-gate.sh`, sort/comm only) and the engine name no runner; only
# this adapter-side script does.
#
# Contract: stdout = passing test identifiers, one per line. Everything else
# (build noise, diagnostics) goes to stderr, which the wrapper discards. A
# runner that cannot build/run emits nothing -> empty pass-set. The wrapper
# treats an empty baseline as a no-op (nothing to protect) and an empty
# closeout after a NON-empty baseline as a whole-suite regression — which is
# exactly the catastrophic-build-break class (the whole pre-existing suite went
# red) the gate must catch.
#
# Testability seam: `IACODER_NOREG_RUNNER_KIND` forces the runner kind and
# `IACODER_NOREG_RUNNER_CMD` forces the raw runner command, so each extractor
# can be unit-tested against canned runner output with no real toolchain.

set -u

kind="${IACODER_NOREG_RUNNER_KIND:-}"
raw="${IACODER_NOREG_RUNNER_CMD:-}"

# ── Detect the runner from the working directory (unless forced) ──────────
if [ -z "$kind" ]; then
  if [ -f Cargo.toml ]; then
    kind=cargo
  elif [ -f go.mod ]; then
    kind=go
  elif [ -f pyproject.toml ] || [ -f pytest.ini ] || [ -f tox.ini ] \
    || [ -f setup.py ] || [ -f setup.cfg ]; then
    kind=pytest
  elif [ -f package.json ]; then
    kind=js
  else
    echo "no-regression pass-lister: no known test runner detected in $(pwd)" >&2
    exit 0
  fi
fi

# ADR-0386 D1: the standard library's unittest discovery, one
# `IACODER_PASS <test id>` line per passing test.
unittest_cmd() {
  printf '%s' "$1 -c 'import sys, unittest
class R(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        print(\"IACODER_PASS \" + test.id(), file=sys.__stdout__, flush=True)
suite = unittest.defaultTestLoader.discover(\".\")
unittest.TextTestRunner(stream=open(\"/dev/null\", \"w\"), resultclass=R, verbosity=0).run(suite)'"
}

# ── Default raw runner command per kind (overridable for tests) ───────────
if [ -z "$raw" ]; then
  case "$kind" in
    cargo)
      raw="cargo test --no-fail-fast"
      ;;
    go)
      raw="go test -json ./..."
      ;;
    pytest)
      py=python
      command -v python >/dev/null 2>&1 || py=python3
      if "$py" -c 'import pytest' >/dev/null 2>&1; then
        raw="$py -m pytest -rA --tb=no -p no:cacheprovider -q"
      else
        # ADR-0386 D1: no pytest in this environment — the standard library's
        # unittest discovery, one `IACODER_PASS <test id>` line per passing test
        # (not `-v` text: docstrings interleave with the `... ok` lines).
        kind=unittest
        raw="$(unittest_cmd "$py")"
      fi
      ;;
    js)
      # Best-effort: needs jq to parse the JSON report + a local runner binary.
      # Missing either -> degrade to no-op (empty pass-set), never a false block.
      if command -v jq >/dev/null 2>&1 && [ -x node_modules/.bin/vitest ]; then
        raw="node_modules/.bin/vitest run --reporter=json"
      elif command -v jq >/dev/null 2>&1 && [ -x node_modules/.bin/jest ]; then
        raw="node_modules/.bin/jest --json"
      elif command -v jq >/dev/null 2>&1 && [ -x node_modules/.bin/mocha ]; then
        # ADR-0386 D2: mocha's JSON reporter.
        kind=mocha
        raw="node_modules/.bin/mocha --reporter json"
      else
        echo "no-regression pass-lister: js path needs jq + local vitest/jest; degrading to no-op" >&2
        exit 0
      fi
      ;;
    unittest)
      py=python
      command -v python >/dev/null 2>&1 || py=python3
      raw="$(unittest_cmd "$py")"
      ;;
    mocha)
      raw="node_modules/.bin/mocha --reporter json"
      ;;
    *)
      echo "no-regression pass-lister: unknown runner kind '$kind'" >&2
      exit 0
      ;;
  esac
fi

# ── Extract passing identifiers from the runner's stdout (stdin here) ─────
extract() {
  case "$kind" in
    cargo)
      # libtest human format: `test <name> ... ok`
      sed -n 's/^test \(.*\) \.\.\. ok$/\1/p'
      ;;
    go)
      # `go test -json`: one JSON object per line. A per-test pass event carries
      # both "Action":"pass" and a "Test" field (a package-level pass has none).
      # ADR-0389: t.TempDir() puts a random directory component into some
      # subtest names (outDir=/tmp/TestSave<random>/001). Keep the rest of the
      # subtest path, but normalize that component for baseline comparison.
      grep '"Action":"pass"' | grep '"Test":"' \
        | sed -n 's/.*"Package":"\([^"]*\)".*"Test":"\([^"]*\)".*/\1::\2/p' \
        | sed -E 's@(outDir=/tmp/[^/]*[^/0-9])[0-9]{6,}(/|$)@\1<temp>\2@g'
      ;;
    pytest)
      # `-rA` short test summary: `PASSED <nodeid>`
      sed -n 's/^PASSED \(.*\)$/\1/p'
      ;;
    unittest)
      sed -n 's/^IACODER_PASS \(.*\)$/\1/p'
      ;;
    mocha)
      jq -r '(.passes // [])[]? | ((.file // "") + "::" + (.fullTitle // .title // ""))' 2>/dev/null
      ;;
    js)
      # vitest/jest `--json`: a single JSON blob. Pull fully-qualified names of
      # passed assertions. jq guarded above.
      jq -r '
        (.testResults // [])[]?
        | . as $f
        | ($f.assertionResults // $f.tests // [])[]?
        | select((.status // .state) == "passed")
        | (($f.name // $f.filepath // "")
           + "::"
           + (((.ancestorTitles // []) + [(.title // .name // "")]) | join(" > ")))
      ' 2>/dev/null
      ;;
  esac
}

sh -c "$raw" 2>/dev/null | extract | sort -u
