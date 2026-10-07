"""IacoderHarborBase — shared `BaseInstalledAgent` for all iacoder eval adapters.

Pulled out of the two-adapter duplication (terminal-bench + swebench-verified)
once the third adapter was scheduled — see ADR 0043 § Triggers, which cashes
in the "共享代码：先复制后抽象" promise from ADR 0042.

Subclasses declare three things:
  * `name()`              — Harbor adapter registry key
  * `_DEFAULT_MAX_TURNS`  — sized from the bench's `task.toml [agent] timeout_sec`
  * `CLI_FLAGS`           — call `IacoderHarborBase._build_cli_flags(default_max_turns=…)`

Everything else (install template, config-toml pass-through, shell-timeout
wrap, ATIF stub) is inherited verbatim.
"""

from __future__ import annotations

import json

import base64
import os
import re
import shlex
import tomllib
from abc import abstractmethod
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


# Timeout arithmetic lives in `timeouts.py` (single authoritative
# definition, venv-free tests). `_SECS_PER_TURN` is re-exported for the
# per-bench sizing comments that reference it.
from .timeouts import SECS_PER_TURN as _SECS_PER_TURN
from .timeouts import exec_timeout_for as _exec_timeout_for
from .timeouts import resolve_outer_cap as _resolve_outer_cap
from .private_files import parse_identity, private_files_command

_INSTALL_TIMEOUT_SEC: int = 900

# Where iacoder looks for config inside the container. iacoder resolves
# its config dir via the `directories` crate = ``$XDG_CONFIG_HOME/iacoder``
# on Linux; there is no path-override env. So we write the copied config
# to ``<_CONTAINER_XDG>/iacoder/config.toml`` and set ``XDG_CONFIG_HOME``
# to this dir in the agent exec env — works regardless of which user
# runs the agent (root or a task user), since the path is explicit, not
# ``$HOME``-relative.
_CONTAINER_XDG: str = "/iacoder-cfg"
# ADR-0373 D1: non-secret process env (egress proxy, log level), sourced.
_PROCESS_ENV_PATH: str = f"{_CONTAINER_XDG}/process.env"

# ADR-0373 D2: the commit identity the agent's own commits carry. The
# abnormal-exit harvest keeps its own `harvest@iacoder` marker so the two stay
# distinguishable in analysis.
_GIT_IDENTITY_SETUP: str = (
    "git config --global user.name iacoder-eval >/dev/null 2>&1; "
    "git config --global user.email eval@iacoder.invalid >/dev/null 2>&1; "
)

# `${ENV:VAR}` indirections inside config.toml are resolved by iacoder
# from its *process* env at request time. A verbatim copy therefore
# needs those exact vars forwarded from the host (e.g. the chat slot's
# ``${ENV:ZAI_API_KEY}``), or the slot can't resolve its key. We forward
# only the vars the config names.
_ENV_REF_RE = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")

# ADR-0232: wall-clock cap (seconds) for the mechanical no-regression check —
# used both for the baseline capture at run start and for the injected
# `[verify] command` PreFinal gate. Generous because it may re-run the repo's
# pre-existing suite, but bounded (Article III): an unbounded gate hangs the
# closeout it is meant to protect.
_NO_REGRESSION_TIMEOUT_SEC: int = 1800

# ADR-0232: env var the run driver may set to inject the per-task pre-existing
# check command (its stdout must list the passing check identifiers, one per
# line). Beats the bench's `NO_REGRESSION_COMMAND` class attribute. Reading it
# from the *host* env at install/run time is what lets a per-task command reach
# the container without a code change.
_NO_REGRESSION_COMMAND_ENV: str = "IACODER_NO_REGRESSION_COMMAND"

# ADR-0232: container-side paths for the no-regression wrapper (shipped with
# this package, uploaded at install), the file carrying the bench's check
# command, and the captured baseline pass-set. Named here so `install` and
# `run` agree on them (a rename in one place is a silent gate failure).
_NO_REGRESSION_WRAPPER: str = f"{_CONTAINER_XDG}/no-regression-gate.sh"
_NO_REGRESSION_COMMAND_FILE: str = f"{_CONTAINER_XDG}/no_regression_command"

# ADR-0232 S1b: container path of the shipped auto-detecting pass-lister — the
# HOST-POLICY script that discovers the repo's own test runner (cargo / go /
# pytest / vitest) and emits one passing-test identifier per line. It is the
# default VALUE a bench arms the gate with. Uploaded at install alongside the
# neutral wrapper whenever the gate is armed.
_NO_REGRESSION_PASSLIST: str = f"{_CONTAINER_XDG}/no-regression-passlist.sh"

# ADR-0232 S1b: the ready-to-arm command value — run the shipped pass-lister.
# A bench arms the mechanical gate by setting `NO_REGRESSION_COMMAND` to this
# (see `IacoderSweBenchAgent`); the run driver may still override per-task via
# the `IACODER_NO_REGRESSION_COMMAND` env var. Exported so adapter subclasses
# reference the container path in one place instead of re-spelling it.
NO_REGRESSION_PASSLIST_COMMAND: str = f"sh {_NO_REGRESSION_PASSLIST}"



def _find_repo_local_config(start: Path) -> Path | None:
    """Walk up from `start` looking for `.iacoder/config.toml`.

    Matches the project-root discovery pattern used by cargo, git, etc.
    Smoke runner `cd`s into `eval/<bench>/` before invoking harbor, so
    the trial subprocess's CWD is the per-bench dir — we need to climb
    up to the repo root where `.iacoder/config.toml` actually lives.

    Stops at the filesystem root. Returns the first match or None.
    """
    current = start.resolve()
    while True:
        candidate = current / ".iacoder" / "config.toml"
        if candidate.is_file():
            return candidate
        if current.parent == current:  # at filesystem root
            return None
        current = current.parent


def _read_host_config() -> str | None:
    """Read the host's iacoder config.toml verbatim, or None if absent.

    Resolution order (first match wins, no merge):

      1. **Repo-local** ``.iacoder/config.toml`` discovered by walking up
         from CWD. This is the highest-priority slot — workspaces pin
         their own provider / model setup (e.g. enabling a vision slot
         only for benches that need it) without touching the operator's
         global config.
      2. ``IACODER_CONFIG`` env (explicit file path).
      3. ``XDG_CONFIG_HOME/iacoder/config.toml``.
      4. ``~/.config/iacoder/config.toml`` (Linux convention).
      5. macOS app-support path.

    The harbor host is where ``harbor run`` executes, so any of these is
    the operator's own iacoder config — the install template copies the
    resolved bytes verbatim into the container.
    """
    repo_local = _find_repo_local_config(Path.cwd())
    candidates: list[Path] = []
    if repo_local:
        candidates.append(repo_local)
    explicit = os.environ.get("IACODER_CONFIG")
    if explicit:
        candidates.append(Path(explicit))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "iacoder" / "config.toml")
    home = Path.home()
    candidates.append(home / ".config" / "iacoder" / "config.toml")
    candidates.append(
        home / "Library" / "Application Support"
        / "com.infiniact.iacoder" / "config.toml"
    )
    for c in candidates:
        try:
            if c.is_file():
                return c.read_text()
        except OSError:
            continue
    return None


#: A real ``[verify]`` table header, anchored to its own line. A `[verify]`
#: written inside a comment or a string is not a table, and the substring test
#: this replaces could not tell the difference (ADR-0270 §1).
_VERIFY_HEADER = re.compile(r"^[ \t]*\[verify\][ \t]*(?:#.*)?$", re.M)


def _verify_view(text: str) -> tuple[bool, bool, set[str]]:
    """What the host config already declares under ``[verify]``, from a PARSE.

    Returns ``(verify_present, host_set_command, existing_goal_names)``.

    Read from `tomllib`, never from a substring: the harness uses these facts to
    decide whether the operator already owns the verification gate, and getting
    that wrong silently drops either the mechanical command (ADR-0270 §1) or —
    as in the defect that ADR-0270 supersedes — every declarative goal.

    A config that does not parse is one the engine would reject anyway. Fall
    back to a textual reading rather than raising: a malformed host config
    should fail inside the run, where the operator sees it, instead of taking
    down the bench harness before a single task starts.
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return (bool(_VERIFY_HEADER.search(text)), False, set())
    verify = doc.get("verify")
    if not isinstance(verify, dict):
        return (False, False, set())
    names = {
        goal.get("name")
        for goal in verify.get("goals", [])
        if isinstance(goal, dict) and goal.get("name")
    }
    return (True, verify.get("command") is not None, names)


def _with_closeout_goals(
    config_text: str | None,
    goals: list[tuple[str, str | None]],
    verify_command: str | None = None,
) -> str | None:
    """Append the bench's closeout goals to the config copied into the container.

    **Why this lives in the harness and not in the engine.** A benchmark scores
    whatever its collector can see. SWE-bench-shaped harnesses read
    ``git diff <base>..HEAD``, so work left uncommitted scores zero no matter how
    good it is; a harness that tars the working tree has no such requirement, and
    a task with no VCS at all cannot satisfy one. "Hand over the work" is
    therefore a property of *this task's environment*, not of the agent — an
    engine that committed on its own would be guessing at a policy it cannot see
    (ADR-0157 §非目标, Article 0).

    What the engine does provide is the enforcement point: a declared closeout
    goal (ADR-0159) is checked before the run may finish, the failure is fed
    back as the **goal**, and it is **not** subject to the ADR-0069 session
    nudge budget. Past the cap the run still ends, but ADR-0160 makes that
    visible instead of silent (exit code 81 + the unmet goal named). So the
    harness states the requirement once, declaratively, and the engine holds the
    exit.

    **Why a goal and not a command.** The older ``[verify] command`` form is a
    recipe, and every spelling of the recipe was wrong in a different direction.
    ``git diff --quiet && git diff --cached --quiet`` passes on a run whose whole
    contribution is new files, because ``git diff`` only sees tracked ones — an
    armed gate firing blanks. ``test -z "$(git status --porcelain)"`` fixes that
    and breaks something worse: it demands a spotless tree, and the cheapest way
    for an agent to get one is ``git add -A``, which sweeps engine scratch and
    build output into the graded patch. A gate whose easiest satisfying move
    damages the result is not a gate. Stating the OUTCOME leaves the method to
    the agent, which is standing in the environment and can see which files are
    its own work.

    The gate lands in the container's config, which is exactly where
    development-time environment settings belong: nothing here is committed to a
    repository, and the agent's own workspace stays untouched.

    A host-declared ``[verify]`` table is filled, not abandoned (ADR-0270 §2):
    its scalars are the operator's judge/gate policy, and the harness still owes
    the bench its declarative goals. Only a host-set ``command`` wins outright —
    that is an operator's own recipe, and a recipe beats a synthesized one.
    ``goals`` entries with a ``None``/empty text are skipped, so a bench that
    declares only some of the gates pays for exactly those.

    ADR-0232/0236: when ``verify_command`` is set, a ``[verify] command`` is
    synthesized too — the older, *method-shaped* form that the engine expands
    into a mechanical ``PreFinal`` gate (ADR-0007): the engine runs the command
    itself and a non-zero exit blocks completion, independent of whatever
    command the agent bound. This is deliberately a command, not a goal, because
    these checks must be MECHANICAL rather than agent-discovered + LLM-judged (a
    self-selected command runs a subset and full-suite regressions ship; a prose
    coverage claim false-passes its own set-member gaps, ADR-0214). The single
    ``command`` scalar carries the ADR-0232 no-regression ``check``
    (deterministic, cheap). ``None`` (the default) leaves behaviour
    byte-identical — the prose goals, if any, pass in ``goals``.
    """
    goals = [(name, text) for name, text in goals if text]
    if not goals and not verify_command:
        return config_text
    text = config_text or ""
    # A host may provide scalar verify settings (for example
    # ``verifier_advisory = true``) while the harness still needs to add
    # its declarative goals.  Do not treat the table's presence as an opt-out.
    # Existing goal names are preserved and not duplicated.
    verify_present, host_set_command, existing_goal_names = _verify_view(text)
    header = _VERIFY_HEADER.search(text)
    block = (
        "\n# Closeout goals for this bench, appended by the Harbor adapter.\n"
        "# See `_with_closeout_goals` for why the requirements belong to the\n"
        "# harness rather than to the engine, and why they state outcomes\n"
        "# rather than commands.\n"
    )
    # ADR-0232: the `[verify]` table's scalar keys (`command`, `timeout_secs`)
    # must precede the `[[verify.goals]]` array-of-tables — once an
    # array-of-tables opens, subsequent bare keys bind to it, not the parent
    # table. And a table may not be declared twice. Neither constraint says to
    # give up on the command when the host opened the table: put the scalars
    # INTO the table the host already has (ADR-0270 §2), and emit a header of
    # our own only when the document has no `verify` at all.
    scalars = ""
    if verify_command and not host_set_command:
        scalars = (
            # json.dumps, not repr: a TOML basic string takes JSON's escape
            # conventions (see the goal-string note below).
            f"command = {json.dumps(verify_command)}\n"
            f"timeout_secs = {json.dumps(_NO_REGRESSION_TIMEOUT_SEC)}\n"
        )
    if scalars and header:
        # Straight after the host's own header, so the scalars bind to
        # `[verify]` and not to any array-of-tables that follows it. Consume the
        # header's own line break and re-emit it, so a header that ends the file
        # does not get the scalars welded onto it.
        head, rest = text[: header.end()], text[header.end() :]
        text = head + "\n" + scalars + rest.removeprefix("\n")
        scalars = ""
    elif scalars and verify_present:
        # `verify` exists but not as a header we can extend — dotted keys, say.
        # Declaring `[verify]` here would be a second declaration and the whole
        # config would fail to parse. Leave the command out rather than emit a
        # file the engine cannot read; the declarative goals below still land.
        scalars = ""
    elif scalars:
        scalars = "[verify]\n" + scalars
    block += scalars
    for name, goal in goals:
        if name in existing_goal_names:
            continue
        block += (
            "[[verify.goals]]\n"
            f"name = {json.dumps(name)}\n"
            # `json.dumps`, not `repr`: a TOML basic string takes JSON's escapes,
            # whereas Python's repr switches to single quotes around a `"` and then
            # backslash-escapes a `'` if the text has both — which TOML literal
            # strings do not process, so the config would fail to parse. The goal is
            # human-authored prose; quotes in it are a matter of time.
            f"goal = {json.dumps(goal)}\n"
            "required = true\n"
        )
    return text + block


def _config_env(config_text: str) -> dict[str, str]:
    """Env vars the config's ``${ENV:VAR}`` refs need, forwarded from the host.

    Plus ``XDG_CONFIG_HOME`` so iacoder finds the copied config. Only
    vars that are actually set on the host are forwarded; a missing one
    is left for iacoder to report rather than masked here.

    NOTE: this dict carries SECRETS. Do NOT pass it to Harbor's
    ``exec_as_agent(..., env=...)`` — Harbor forwards env via
    ``docker compose exec -e KEY=VALUE``, which puts every value in
    argv (visible via ``ps`` on the host and in any process-list
    capture). Use ``_write_secrets_file`` + ``upload_file`` instead;
    the run command then sources the file inside the container.
    """
    env: dict[str, str] = {"XDG_CONFIG_HOME": _CONTAINER_XDG}
    for var in sorted(set(_ENV_REF_RE.findall(config_text))):
        val = os.environ.get(var)
        if val:
            env[var] = val
    return env


def _write_process_env_file(env: dict[str, str]) -> Path:
    """Serialize non-secret process env (egress proxy, log level) for the run
    command to ``source`` (ADR-0373 D1 split): these must be real environment
    variables — the HTTP client reads the proxy from the environment — and are
    not the provider secrets. Mode 0600, POSIX single-quote escaped.
    """
    import stat
    import tempfile

    fd, path_str = tempfile.mkstemp(prefix="iacoder-process.", suffix=".env")
    path = Path(path_str)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        with os.fdopen(fd, "w") as f:
            for key in sorted(env):
                val = env[key].replace("'", "'\\''")
                f.write(f"{key}='{val}'\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _write_secrets_file(env: dict[str, str]) -> Path:
    """Serialize ``KEY=VALUE`` lines to a host-local tempfile (mode 0600).

    The caller is responsible for ``upload_file``-ing it into the
    container, then deleting the host copy. The file is never sourced by a
    shell: ``iacoder --secrets-file`` reads it verbatim (no interpolation)
    and deletes it (ADR-0373 D1), so values are written unquoted.
    """
    import stat
    import tempfile

    fd, path_str = tempfile.mkstemp(prefix="iacoder-secrets.", suffix=".env")
    path = Path(path_str)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        with os.fdopen(fd, "w") as f:
            # ADR-0373 D1: `.env` format for `iacoder --secrets-file`, which
            # reads it into an in-process overlay and deletes it. No quoting:
            # the reader takes everything after the first `=` verbatim and
            # never interpolates. `XDG_CONFIG_HOME` is not a secret and is
            # passed on the command line instead.
            for key in sorted(env):
                if key == "XDG_CONFIG_HOME":
                    continue
                val = env[key]
                if "\n" in val or "\r" in val:
                    raise ValueError(f"secret {key} contains a line break")
                f.write(f"{key}={val}\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


# ADR-0331 D4 deleted the prose spec-coverage goal (ADR-0234/0235): its LLM
# criterion never once judged a binding adequate (12 observations, one value).
# ADR-0334 deleted the mechanical adversary (ADR-0236) that replaced it, with
# the rest of the probe / judge / nudge stack. Only the hand-off goal and the
# no-regression `[verify] command` remain host-declared; the engine checks
# them once (ADR-0331 D3).


def _permission_flag() -> str:
    """ADR-0319 D1: the benchmark sandbox runs without the permission gate.

    Without a flag, headless iacoder puts an LLM behavioural classifier in front
    of every permission request. Measured over the c314 rounds it made 94-471
    calls per run, returned ``Defer`` 4-121 times, took 2-10% of wall time — and
    never denied a single tool call, because ``Defer`` falls back to allow in the
    headless wiring. ``--dangerously-allow-all`` is documented for exactly this:
    unattended benchmark runs in an isolated sandbox. It has existed since
    v0.1.0, so every binary this harness can launch accepts it.

    Attribution switch, same shape as ``IACODER_EVAL_DECLARE_DEADLINE``:
    ``IACODER_EVAL_PERMISSION_CLASSIFIER=1`` (host env) keeps the classifier, so
    the two arms of an A/B differ only here.
    """
    if os.environ.get("IACODER_EVAL_PERMISSION_CLASSIFIER", "0") == "1":
        return ""
    return "--dangerously-allow-all "


def _shell_command_log_env() -> str:
    """ADR-0321 D3: benchmark runs log the text of every shell command.

    The engine keeps command text out of its log unless the host sets
    ``IACODER_LOG_SHELL_COMMANDS=1`` (a user's commands can carry secrets; a
    benchmark container's do not). Without the text, a run that spends most of
    its wall time in shell calls cannot be broken down: a full-suite run and a
    targeted one look the same.

    Attribution switch, same shape as the others:
    ``IACODER_EVAL_LOG_SHELL_COMMANDS=0`` (host env) leaves the text out.
    """
    if os.environ.get("IACODER_EVAL_LOG_SHELL_COMMANDS", "1") == "0":
        return ""
    return "IACODER_LOG_SHELL_COMMANDS=1 "


def _transcript_env() -> str:
    """ADR-0414 D6: benchmark runs write a content transcript.

    ``session.jsonl`` keeps event kinds with lengths only, and ``iacoder.txt``
    keeps WARN tracing, so a failed trial could not be read turn by turn and an
    INFO mechanism event (ADR-0410's ``shell-output-omitted``) could not be
    attributed. The transcript records assistant text, reasoning, tool calls
    with arguments and results (32 KiB per field, head and tail kept) and the
    INFO events of the iacoder crates, next to ``session.jsonl``.

    The env alias, not the ``--transcript-log`` flag: a binary older than
    ADR-0414 ignores an unknown variable but would reject an unknown flag.

    Attribution switch, same shape as the others:
    ``IACODER_EVAL_TRANSCRIPT=0`` (host env) leaves it off.
    """
    if os.environ.get("IACODER_EVAL_TRANSCRIPT", "1") == "0":
        return ""
    return "IACODER_TRANSCRIPT_LOG=/logs/agent/transcript.jsonl "


class IacoderHarborBase(BaseInstalledAgent):
    """Shared `BaseInstalledAgent` for iacoder Harbor adapters.

    Lifecycle:

      1. ``install()`` runs in the container at task setup. Renders
         ``install-iacoder.sh.j2`` (shipped with this package) and
         executes as root: apt deps + curl tarball + version check.
         Two binary-source paths:

         * **Dev mode** — ``IACODER_DEV_BINARY_URL`` set → skip GitHub
           Releases entirely, pull from a local HTTP server (the smoke
           runner uses ``python3 -m http.server``).
         * **Release mode** — pull
           ``iacoder-<arch>-unknown-linux-musl.tar.gz`` from
           ``https://github.com/<repo>/releases/...``.

      2. ``run()`` invokes ``timeout … iacoder --prompt=<instruction>
         --headless --max-turns N``. The shell-level `timeout` wraps
         iacoder so the subprocess self-terminates inside the container
         — cpython issue #139373 makes harbor's outer
         `asyncio.wait_for(process.communicate(), timeout=…)` officially
         unsafe (~14% chance of running hours past the deadline).

      3. ``populate_context_post_run()`` is a no-op until M-eval.7
         wires ATIF trajectory conversion across all adapters.
    """

    SUPPORTS_ATIF: bool = False

    # What must be TRUE before the run may finish (ADR-0159 closeout goal).
    # Prose, not a command: the agent works out how to check it from the
    # environment it is standing in. `None` (the default) = this bench needs no
    # hand-off step, so nothing is appended and behaviour is unchanged.
    #
    # Set it on benches whose collector only sees *handed-over* work — the
    # SWE-bench family reads `git diff <base>..HEAD`, so an uncommitted fix
    # scores zero. State the requirement here, once, instead of hoping the task
    # prose is obeyed: prose cannot be checked at the exit boundary, a gate can.
    #
    # Keep it about the OUTCOME, and name no tool: a goal mentioning `git`
    # wires one VCS into a declaration that outlives it.
    HANDOFF_GATE: str | None = None

    # Spec-coverage goal (same channel, second entry). `None` (the default) =
    # nothing appended, behaviour unchanged.
    #
    # Set it on benches scored by HIDDEN tests derived from the task statement
    # alone. Evidence for why hand-off is not enough: the 2026-08-29 DeepSWE
    # direct read (docs/evaluations/deepswe-near-miss-harness-analysis-
    # 2026-08-29.md §6.2) found 15 of 25 completed tasks landing at f2p
    # 0.86–0.98 — work handed over, agent's own suite green, and the misses
    # were overwhelmingly REQUIREMENT CLAUSES WITHOUT EVIDENCE (≈12 of 15:
    # an unimplemented CLI surface, recursive refs, boundary/error paths),
    # not verifier quirks. The agent's stopping rule was "my tests pass";
    # the only legitimate extra information source is the task statement
    # itself, so the goal sends the agent back to it once, at the exit.
    #
    # (ADR-0331 D4: the prose spec-coverage goal that used to sit here is gone.)

    # No-regression goal (third entry, same channel). `None` (the default) =
    # nothing appended.
    #
    # Set it on benches that ALSO score pre-existing checks (SWE-bench-family
    # p2p). Evidence: the 2026-08-31 gate-v065on arm surfaced `etree` —
    # f2p 52/52 (every new requirement right) with p2p 0/15 (every
    # pre-existing test destroyed), reward 0. Neither existing gate covers
    # that shape: HANDOFF asks "was the work handed over", SPEC_COVERAGE asks
    # "was the statement covered" — nothing asks "does what already worked
    # still work". The engine's own RegressionIntroduced criterion
    # (ADR-0162) only fires when the agent itself ran the same check green
    # then red; an agent that never runs the pre-existing suite is exactly
    # the blind spot ADR-0162's honest-limits section predicted (vulture /
    # gql, 2026-08-23) — the fix it named was a declared goal that sends the
    # agent to the project's own checks. This is that goal.
    NO_REGRESSION_GATE: str | None = None

    # ADR-0232: MECHANICAL no-regression command (opt-in). The prose
    # `NO_REGRESSION_GATE` above is agent-discovered + LLM-judged: the agent
    # self-selects which command satisfies it, so it runs a SUBSET and
    # full-suite regressions ship (pest shipped a p2p regression; koota broke a
    # pre-existing API). This attribute closes that hole by making the check
    # MECHANICAL — the engine runs a fixed command itself and blocks on a
    # regression, independent of what the agent bound.
    #
    # Its VALUE is a command whose STDOUT lists the identifiers of the
    # currently-passing pre-existing checks, one per line. That "how to list
    # passing checks" is a fact about a specific bench's environment (its test
    # runner, its p2p set), so it belongs in the subclass or is injected
    # per-task via the `IACODER_NO_REGRESSION_COMMAND` env var — never hardcoded
    # in this shared base (Article I / no-overfit): the base carries only the
    # neutral MECHANISM (capture the baseline pass-set at run start; re-run +
    # diff at closeout via the shipped runner-agnostic wrapper).
    #
    # `None` (the default) = not armed: behaviour is byte-identical to before,
    # and the prose `NO_REGRESSION_GATE` goal (if any) is used instead.
    NO_REGRESSION_COMMAND: str | None = None

    # Subclasses MUST override. Used by `_agent_exec_timeout` and the
    # CLI flag default. Sized from the bench's `task.toml [agent]
    # timeout_sec`: `(timeout_sec - 120) / timeouts.SECS_PER_TURN` gives
    # the turn-budget headroom; pick a value with margin against harbor's
    # outer kill.
    _DEFAULT_MAX_TURNS: int = 100

    # The bench's `task.toml [agent] timeout_sec` (unscaled base) — the
    # task-defined outer kill window, and the bench-level fallback for
    # ADR-0216 D5's per-task env override. Same source as
    # `_DEFAULT_MAX_TURNS` (declare them together). When known,
    # `_agent_exec_timeout` uses the cap AS the window (ADR-0216 D6:
    # cap-priority — the turn formula is only the no-cap fallback), so the
    # budget the agent is TOLD (`--deadline-secs`) is the true binding
    # window. `None` = no uniform per-task cap (terminal-bench varies per
    # task); the formula stands, that limitation accepted.
    _TASK_OUTER_TIMEOUT_SEC: int | None = None

    # Subclasses MUST redeclare via `_build_cli_flags(default_max_turns=...)`.
    # CliFlag's `default=` is captured at class-body evaluation time —
    # the parent class can't lazily plug the subclass's value in.
    CLI_FLAGS: list[CliFlag] = []

    @classmethod
    def _build_cli_flags(cls, default_max_turns: int) -> list[CliFlag]:
        """Build the standard flag list (max_turns / preset / no_memory).

        Subclasses call this from their class body to plug their own
        `_DEFAULT_MAX_TURNS` into the `--max-turns default=` slot. The
        ADR 0044 critic / divergence ablation flags that used to live here
        went with the mechanisms they toggled (ADR-0334): the CLI no longer
        accepts ``--no-pre-exit-critic`` / ``--no-advisor-divergence``.
        """
        return [
            CliFlag(
                "max_turns",
                cli="--max-turns",
                type="int",
                default=default_max_turns,
                env_fallback="IACODER_MAX_TURNS",
            ),
            CliFlag("preset", cli="--preset", type="str"),
            CliFlag("no_memory", cli="--no-memory", type="bool"),
            CliFlag(
                "reasoning_effort",
                cli="--reasoning-effort",
                type="str",
                env_fallback="IACODER_REASONING_EFFORT",
            ),
            # Keep numeric sampling as text here: CliFlag's portable grammar
            # is str/int/bool; the CLI owns float/range validation.
            CliFlag(
                "temperature", cli="--temperature", type="str",
                env_fallback="IACODER_TEMPERATURE",
            ),
            CliFlag(
                "top_p", cli="--top-p", type="str",
                env_fallback="IACODER_TOP_P",
            ),
            CliFlag(
                "max_output_tokens_per_turn", cli="--max-output-tokens-per-turn",
                type="int", env_fallback="IACODER_MAX_OUTPUT_TOKENS_PER_TURN",
            ),
            CliFlag(
                "context_window", cli="--context-window", type="int",
                env_fallback="IACODER_CONTEXT_WINDOW",
            ),
        ]

    @staticmethod
    @abstractmethod
    def name() -> str:
        """Harbor adapter registry key. Must be unique across installed adapters."""

    def get_version_command(self) -> str | None:
        return "iacoder --version"

    def _agent_exec_timeout(self) -> int:
        """docker-exec hard timeout for the agent command.

        ADR-0216 D5/D6: the outer cap is resolved per-task (env override
        ``IACODER_TASK_OUTER_TIMEOUT_SEC`` ▷ the bench constant
        ``_TASK_OUTER_TIMEOUT_SEC``) and, when known, IS the window — the
        turn formula only stands as the no-cap fallback. The subprocess
        kill and the ``--deadline-secs`` the agent is told about derive
        from the same value so they never diverge; an inner kill below
        harbor's outer ``asyncio.wait_for`` is also the safer ordering
        (cpython #139373). Full rationale and provenance: ``timeouts.py``.
        """
        max_turns = self._resolved_flags.get("max_turns", self._DEFAULT_MAX_TURNS)
        outer_cap = _resolve_outer_cap(self._TASK_OUTER_TIMEOUT_SEC)
        return _exec_timeout_for(max_turns, outer_cap)

    def _no_regression_command(self) -> str | None:
        """Resolve the ADR-0232 mechanical no-regression command, or `None`.

        Resolution order (first non-empty wins): the `IACODER_NO_REGRESSION_COMMAND`
        host env var (the per-task override the run driver may set) ▷ the bench's
        `NO_REGRESSION_COMMAND` class attribute. `None`/empty from both = not
        armed, and the gate stays entirely inert (byte-identical to pre-0232).

        Read once here so `install` (config injection + wrapper upload) and
        `run` (baseline capture) agree on whether the gate is armed.
        """
        env_cmd = os.environ.get(_NO_REGRESSION_COMMAND_ENV)
        if env_cmd and env_cmd.strip():
            return env_cmd
        attr = self.NO_REGRESSION_COMMAND
        if attr and attr.strip():
            return attr
        return None

    def _verify_command(self) -> str | None:
        """Compose the `[verify] command` from the armed mechanical gate.

        The ADR-0232 no-regression `check` is the only mechanical gate left
        (ADR-0334 deleted the spec-coverage adversary); a non-zero exit blocks
        completion. `None` when it is not armed (byte-identical to pre-0232 for
        a bench that did not opt in).
        """
        if self._no_regression_command():
            return f"sh {_NO_REGRESSION_WRAPPER} check"
        return None

    async def install(self, environment: BaseEnvironment) -> None:
        """Render install template → run as root in the container.

        After the install step succeeds, upload an ``/iacoder-cfg/secrets.env``
        file out-of-band via ``environment.upload_file`` so the agent's
        run command can read and delete it without secrets ever passing
        through ``docker exec -e KEY=VALUE`` (which puts them in argv).
        See the comment on ``_config_env`` for the leak this avoids.
        """
        template_dir = Path(__file__).parent
        # Resolve through the same execution path as run(), not the installer's
        # root identity or a guessed username from the Dockerfile (ADR-0396).
        identity = await self.exec_as_agent(
            environment, command="id -u && id -g", timeout_sec=30,
        )
        agent_uid, agent_gid = parse_identity(identity.stdout)
        jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape([]),  # shell content, not HTML
            keep_trailing_newline=True,
        )
        # Copy the host's config.toml verbatim into the container. base64
        # so arbitrary TOML (quotes, ${ENV:..}, multiline) survives shell
        # embedding intact. Absent host config → empty; the template
        # skips writing it.
        # ADR-0232: when a mechanical no-regression command is armed, the engine
        # enforces the pre-existing checks itself (a `[verify] command` PreFinal
        # gate), so the agent-discovered + LLM-judged prose no-regression goal is
        # DROPPED — the mechanical check is authoritative and the prose goal would
        # be a redundant, weaker second opinion. Not armed = the prose goal stays.
        #
        # ADR-0331 D4 deleted the prose spec-coverage goal; ADR-0334 deleted
        # the mechanical adversary that replaced it. Only the hand-off goal and
        # the no-regression command are declared.
        noreg_cmd = self._no_regression_command()
        goals: list[tuple[str, str | None]] = [
            ("handoff", self.HANDOFF_GATE),
        ]
        if not noreg_cmd:
            goals.append(("no-regression", self.NO_REGRESSION_GATE))
        host_config = _with_closeout_goals(
            _read_host_config(),
            goals,
            verify_command=self._verify_command(),
        )
        config_b64 = (
            base64.b64encode(host_config.encode()).decode()
            if host_config is not None
            else ""
        )

        template = jinja_env.get_template("install-iacoder.sh.j2")
        rendered = template.render(
            iacoder_binary_url=os.environ.get("IACODER_DEV_BINARY_URL", ""),
            iacoder_version=os.environ.get("IACODER_VERSION", "latest"),
            iacoder_repo=os.environ.get("IACODER_REPO", "datiexu/iacoder"),
            iacoder_config_b64=config_b64,
            iacoder_config_xdg=_CONTAINER_XDG,
        )

        await self.exec_as_root(
            environment,
            command=rendered,
            env={"DEBIAN_FRONTEND": "noninteractive"},
            timeout_sec=_INSTALL_TIMEOUT_SEC,
        )

        # Secrets pass-through. We only do this when the host has a
        # config.toml — without one there are no `${ENV:VAR}` refs to
        # forward, so the file would just contain XDG_CONFIG_HOME (not
        # a secret; iacoder picks it up from the install template's own
        # default-path resolution).
        if host_config is not None:
            secrets_env = _config_env(host_config)
            secrets_path = _write_secrets_file(secrets_env)
            try:
                # upload_file copies via the docker daemon's IO channel,
                # NOT via `docker exec -e`. No argv exposure.
                target = f"{_CONTAINER_XDG}/secrets.env"
                await environment.upload_file(secrets_path, target)
            finally:
                secrets_path.unlink(missing_ok=True)

        # ADR-0232: when the mechanical no-regression gate is armed, upload the
        # runner-agnostic wrapper (shipped with this package) and a file
        # carrying the bench's per-task check command. The `[verify] command`
        # injected above runs the wrapper's `check` mode; `run` runs its
        # `baseline` mode before the agent starts. The command travels via a
        # file — NOT `docker exec -e` — for the same reason secrets do (the
        # engine runs the PreFinal hook with its own inherited env, so the env
        # var is not guaranteed to reach the hook; the file always does).
        if noreg_cmd:
            wrapper_src = template_dir / "no-regression-gate.sh"
            await environment.upload_file(wrapper_src, _NO_REGRESSION_WRAPPER)
            # ADR-0232 S1b: also ship the auto-detecting pass-lister. Harmless if
            # a per-task env override binds a different command — the default
            # arming (`NO_REGRESSION_PASSLIST_COMMAND`) invokes exactly this.
            passlist_src = template_dir / "no-regression-passlist.sh"
            if passlist_src.is_file():
                await environment.upload_file(passlist_src, _NO_REGRESSION_PASSLIST)
            import tempfile

            fd, cmd_path_str = tempfile.mkstemp(prefix="iacoder-noreg.", suffix=".cmd")
            cmd_path = Path(cmd_path_str)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(noreg_cmd)
                await environment.upload_file(cmd_path, _NO_REGRESSION_COMMAND_FILE)
            finally:
                cmd_path.unlink(missing_ok=True)

        await self.exec_as_root(
            environment,
            command=private_files_command(_CONTAINER_XDG, agent_uid, agent_gid),
            timeout_sec=30,
        )
        # Fail during setup if the actual agent cannot read its private files
        # or remove secrets after loading. Never print their contents.
        await self.exec_as_agent(
            environment,
            command=(
                f"set -eu; test -w {_CONTAINER_XDG}; "
                f"for f in {_CONTAINER_XDG}/secrets.env {_PROCESS_ENV_PATH} "
                f"{_CONTAINER_XDG}/iacoder/config.toml; do "
                'if [ -f "$f" ]; then test -r "$f"; fi; done'
            ),
            timeout_sec=30,
        )


    def populate_context_post_run(self, context: AgentContext) -> None:
        """No ATIF trajectory yet — wired in M-eval.7 (ADR 0043 milestone table)."""
        return None

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # ADR 0039 Phase A1: use `--prompt=` (= syntax), not space-
        # separated. `iacoder --prompt <value>` is ambiguous when the
        # value starts with `- ` — clap parses the leading dash as
        # another flag. `shlex.quote` handles the shell layer; `=`
        # settles the argv-parsing layer.
        escaped_instruction = shlex.quote(instruction)
        model_flag = ""
        if self.model_name:
            model_flag = f"--model {shlex.quote(self.model_name)} "
        cli_flags = self.build_cli_flags()
        extra_flags = (cli_flags + " ") if cli_flags else ""

        # cpython issue #139373: harbor's `asyncio.wait_for(communicate(),
        # timeout=…)` path is officially unsafe — ~14% of the time it
        # never raises and the subprocess runs hours past the deadline.
        # Shell-level `timeout` self-terminates inside the container,
        # bypassing that path. Fires 30 s before harbor's own deadline;
        # `-k 10` upgrades SIGTERM → SIGKILL after a 10 s grace. Short-
        # form `-k` is accepted by both GNU coreutils and busybox
        # `timeout`, so this is base-image-agnostic. Exit 124 surfaces
        # as `NonZeroAgentExitCodeError` ("agent budget exhausted
        # in-container") instead of the previous ambiguity.
        exec_timeout = self._agent_exec_timeout()
        shell_timeout = max(60, exec_timeout - 30)
        # ADR-0170/0189: declare the harness window to the engine. The
        # binary is killed by the shell `timeout` above at `shell_timeout`,
        # so that IS the run's wall-clock budget — passing it activates the
        # per-command deadline clamp (ADR-0170) and the 80% soft-wrapup
        # notice (ADR-0189 D2, signature `run-budget-soft-wrapup`). The
        # v0.7.0 exception shape (85 min spent, run died mid-closeout on
        # "no-regression not yet checked") is the failure this removes.
        # Verified against the tags this harness launches (`git grep
        # deadline_secs v0.6.5 / v0.6.8` — present in both), so no binary
        # this adapter runs will reject the flag.
        #
        # Attribution switch: `IACODER_EVAL_DECLARE_DEADLINE=0` (host env)
        # drops the flag entirely — the on/off arms of a deadline A/B must
        # differ only here, and a run manifest records which arm ran. Any
        # other value (or unset) declares; declaring is the default because
        # the kill window exists whether or not the engine is told.
        declare_deadline = (
            os.environ.get("IACODER_EVAL_DECLARE_DEADLINE", "1") != "0"
        )
        deadline_flag = f"--deadline-secs={shell_timeout} " if declare_deadline else ""
        # ADR-0356: prompt template, passed as a flag so the argv in the log
        # shows which template ran. Unset keeps the CLI default (`iacoder`).
        prompt_template = os.environ.get("IACODER_PROMPT_TEMPLATE", "").strip()
        prompt_template_flag = (
            f"--prompt-template={shlex.quote(prompt_template)} " if prompt_template else ""
        )

        # Hand the secrets file uploaded during install (`/iacoder-cfg/
        # secrets.env`, mode 0600) to `iacoder --secrets-file`, which reads it
        # into memory and deletes it (ADR-0373 D1). `[ -f ... ]` keeps the
        # path tolerant of a missing host config (smoke without ${ENV:VAR}
        # refs is still useful for exit-code wiring tests).
        #
        # We pass NO `env={…}` to exec_as_agent — Harbor would route any
        # dict through `docker compose exec -e KEY=VALUE`, leaking the
        # secret in argv (visible to `ps`, /proc, harbor's own log).
        secrets_path = f"{_CONTAINER_XDG}/secrets.env"
        # ADR-0217 D1 / ADR-0233 D1: harvest-commit on any abnormal exit.
        # When the agent process ends without leaving a committed diff, any
        # files written but not yet committed score zero — the grader reads
        # `git diff` and an uncommitted tree is a pristine base. The
        # v0.7.14-rerun bifurcation made this exact for the wall-clock-kill
        # subset: every completed task scored, every killed task scored
        # zero, and 152 files of real work across four tasks were erased for
        # want of a commit (koota/kysely, killed *after* committing, scored
        # full). ADR-0217 harvested only the shell-`timeout` kill codes
        # (124, or 137 after the `-k` SIGKILL); ADR-0233 confirmed the same
        # empty-patch → guaranteed-0 funnel on *other* abnormal exits (a
        # DeepSWE batch had three tasks crash out non-124/137 and score 0
        # with real partial work sitting uncommitted in the tree).
        #
        # So on ANY non-zero exit code (widened from `rc ∈ {124,137}` to
        # `rc != 0`), stage the workspace (excluding engine scratch) and
        # commit a WIP snapshot: a dirty patch is strictly better than no
        # patch. A clean exit (rc == 0) is left untouched — the agent's own
        # commits stand and we never auto-commit on success. This is NOT the
        # ADR-0159 `git add -A` that was banned — that was a
        # *gate-satisfaction* move that swept scratch into a graded patch;
        # here the gate is already lost (the run exited abnormally), the
        # choice is between a scratch-including patch and nothing, and we
        # reuse the exact same scoped pathspec (`:!.iacoder :!*.log`) — only
        # the exit-code condition widens.
        #
        # `git diff --cached --quiet ||` short-circuits the commit when the
        # staged tree is empty, so an abnormal exit with a clean working
        # tree never fabricates a bogus commit.
        #
        # POSIX-portable rc capture: the pipeline's own `$?` is `tee`'s exit
        # (always 0), so the agent's real code is echoed to a temp file
        # inside the brace group before the pipe, then re-read. `exit $rc`
        # keeps harbor's exit-code accounting (124 → budget-exhausted, etc.)
        # transparent — the harvest is invisible to it.
        # ADR-0229: three codes mean "something stopped the run, but the
        # work is still there" — 124/137 (the shell `timeout` killed it) and
        # 81 (`EXIT_CLOSEOUT_UNMET`: the engine's own closeout gate judged a
        # declared goal unmet). For all three the adapter harvests, records
        # the real code, and exits 0 so the **verifier** grades what landed.
        #
        # Why exiting 0 matters: harbor turns any non-zero exit into
        # `NonZeroAgentExitCodeError` and never runs the verifier, so the
        # trial scores nothing at all. `textual-kitty-key-phases` scored
        # 23/23 on the previous full run and exited 81 on this one after
        # 4978 s and 153 turns with no stall and no tool timeout — a
        # once-full task reduced to a blank by the engine's own opinion of
        # itself. In an evaluation the verifier is ground truth; an engine
        # saying "I do not think I finished" is a signal worth recording,
        # not grounds for deleting the work from grading. (This is also why
        # ADR-0217 D1's harvest never rescued a score: it committed and then
        # still `exit $rc`.)
        #
        # Every other code propagates unchanged — 76/77 rate-limit/auth,
        # 78 context overflow, 79 dead transport, 80 region block, 1 generic
        # error. Those mean the attempt never got a fair run; scoring them
        # would launder infrastructure noise into a low capability number.
        harvest = (
            "; rc=$(cat /tmp/iacoder_rc 2>/dev/null || echo 0); "
            # ADR-0233: harvest a WIP commit on ANY abnormal exit (dirty patch
            # strictly beats no patch); record the real code for analysis.
            'if [ "$rc" != 0 ]; then '
            "git add -A -- ':!.iacoder' ':!*.log' >/dev/null 2>&1; "
            "git diff --cached --quiet || "
            "git -c user.email=harvest@iacoder -c user.name=harvest "
            'commit -m "wip: harvested at abnormal exit (rc=$rc)" >/dev/null 2>&1 || true; '
            'echo "IACODER_EXIT_CODE=$rc" >> /logs/agent/iacoder.txt; fi; '
            # ADR-0229: for the "work stopped but present" codes (81 closeout-unmet,
            # 124/137 wall-clock kill) exit 0 so the VERIFIER runs and grades what
            # landed — harbor aborts on any non-zero exit and never runs the verifier,
            # so without this the harvested commit is never scored. Other codes
            # (1/76/77/78/79/80 = no fair run) propagate as infra noise, unscored.
            'if [ "$rc" = 81 ] || [ "$rc" = 124 ] || [ "$rc" = 137 ]; then exit 0; fi; '
            "exit $rc"
        )
        # ADR-0232 D1: capture the baseline pass-set BEFORE the agent runs, while
        # the repo is still pristine. Runs the shipped wrapper's `baseline` mode,
        # which records the set of currently-passing pre-existing checks. It is
        # `timeout`-bounded (a hung pre-existing suite must not eat the run) and
        # `|| true` so it NEVER blocks the run — a missing/empty baseline simply
        # makes the closeout gate a no-op (documented in ADR-0232). Emitted only
        # when the gate is armed, so an unconfigured bench is byte-identical.
        baseline_capture = ""
        if self._no_regression_command():
            baseline_capture = (
                f"[ -f {_NO_REGRESSION_WRAPPER} ] && "
                f"timeout -k 10 {_NO_REGRESSION_TIMEOUT_SEC} "
                # ADR-0255 D2: the reason an empty baseline happened travels in
                # the wrapper's own status file (under `/logs/agent`, which the
                # trial harvests), not in this redirect.
                #
                # An earlier version appended stderr to a file here instead.
                # That reintroduced the hazard it was fixing: if the path cannot
                # be opened, the shell skips the whole command, `|| true`
                # swallows it, and the capture silently does not happen.
                f"sh {_NO_REGRESSION_WRAPPER} baseline >/dev/null 2>&1 || true; "
            )
        await self.exec_as_agent(
            environment,
            command=(
                # ADR-0373 D2: a neutral commit identity, so a failed commit
                # never sends the agent to borrow a maintainer's from history.
                f"{_GIT_IDENTITY_SETUP}"
                # ADR-0373 D1: secrets never enter the process environment —
                # iacoder reads the file into memory and deletes it, so the
                # agent's commands neither inherit nor can re-read them.
                f"[ -f {_PROCESS_ENV_PATH} ] && {{ set -a; . {_PROCESS_ENV_PATH}; set +a; }}; "
                f"export XDG_CONFIG_HOME={_CONTAINER_XDG}; "
                # ADR-0389: baseline and closeout checks must see the same
                # non-secret process env (notably proxy and XDG settings).
                f"{baseline_capture}"
                f"SF=; [ -f {secrets_path} ] && SF=--secrets-file={secrets_path}; "
                f"{{ {_transcript_env()}{_shell_command_log_env()}timeout -k 10 {shell_timeout} "
                # ADR-0373 D4: the engine's event log stays out of the repo.
                f"iacoder $SF --session-log=/logs/agent/session.jsonl "
                f"--prompt={escaped_instruction} "
                f"{deadline_flag}"
                f"{prompt_template_flag}"
                f"{_permission_flag()}"
                f"{model_flag}--headless "
                # ADR-0357: every evaluation runs without the web tools — the
                # tasks come from public repositories, so a web search can
                # reach the upstream change. Not switchable on purpose.
                f"--no-web "
                f"{extra_flags}"
                f"2>&1; echo $? > /tmp/iacoder_rc; }} "
                f"| stdbuf -oL tee -a /logs/agent/iacoder.txt"
                f"{harvest}"
            ),
            timeout_sec=exec_timeout,
        )
