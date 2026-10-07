"""Timeout arithmetic for the harbor adapters — pure, dependency-free.

Split out of ``base.py`` so the invariant tests can run without a harbor
venv (``python3 test_timeouts.py``), and so the constants have exactly one
authoritative definition (the gate-path triad post-mortem: a duplicated
condition is a drift surface).

Provenance of the constants:

- ``SECS_PER_TURN = 12`` — empirical mean seconds/turn from the original
  terminal-bench measurement (pre-advisor era; re-justify on the bench
  whose task.toml budget is tightest before bumping).
- ``TURN_OVERHEAD_SEC = 120`` — non-turn overhead reserve (binary
  startup, config copy, final flush). It is the inverse of the
  per-bench ``_DEFAULT_MAX_TURNS`` sizing formula
  ``(timeout_sec - 120) / 12``, so exec-timeout reconstruction and
  turn-budget sizing stay exact inverses.
- ``EXEC_FLOOR_SEC = 900`` — original t-bench tuning floor protecting
  small-``max_turns`` tasks from an unreasonably tight docker-exec kill
  window. **The floor is an inner-safety-net concept only**: it must
  never push the window past the task's own outer cap (that is the bug
  this module exists to prevent — bfcl's 300 s outer cap vs a 900 s
  "floor" meant the declared budget was fiction and the notice line
  never fired before death).
"""

from __future__ import annotations

import os

SECS_PER_TURN: int = 12
TURN_OVERHEAD_SEC: int = 120
EXEC_FLOOR_SEC: int = 900
# ADR-0405: retain the prior TB4 500-turn command's wall-clock window when
# turns are unlimited and no per-task cap is available. This is elapsed time,
# not a hidden replacement turn count; known task caps still take precedence.
UNLIMITED_EXEC_FALLBACK_SEC: int = 6120

# ADR-0216 D5: per-task outer-cap env channel. The orchestrator (pier /
# harbor host) parses each task.toml to set its own outer wait; it
# exports the true per-task cap here so the adapter can beat its own
# bench-level constant fallback.
TASK_OUTER_TIMEOUT_ENV: str = "IACODER_TASK_OUTER_TIMEOUT_SEC"


def resolve_outer_cap(bench_default: int | None) -> int | None:
    """ADR-0216 D5: the per-task env override, else the bench default.

    ``IACODER_TASK_OUTER_TIMEOUT_SEC`` (positive int seconds) is the true
    outer kill window for *this* task, injected per-trial. Per-task
    timeouts are not uniform, so a bench-level constant is the wrong
    granularity in both directions. Garbage/absent/non-positive → fall
    back to the bench default (never silently pick a value nobody set).
    """
    raw = os.environ.get(TASK_OUTER_TIMEOUT_ENV)
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return bench_default


def exec_timeout_for(max_turns: int, outer_timeout_sec: int | None) -> int:
    """Docker-exec hard timeout for the agent command.

    **Cap-priority (ADR-0216 D6, user ruling "有外层帽时不看 turns"):**
    when the outer cap is known it IS the window — the turn formula does
    not participate. The cap is a fact; ``max_turns × 12 + 120`` is an
    estimator on a stale pre-advisor seconds/turn constant, and letting
    an estimator shrink a known real window (the old ``min``) only
    injected the estimator's error (low declaration = notice line fires
    early, budget under-used). Turn exhaustion has its own carrier —
    ADR-0211's turn axis reads ``max_turns`` directly — so it need not be
    folded into the wall-clock declaration.

    The budget the agent is *told* about (``--deadline-secs`` = this
    value − 30) is therefore the true binding window: ``= cap`` when
    known, else the formula. A cap-derived inner window is also strictly
    better ordered — the in-container ``timeout`` fires before harbor's
    outer ``asyncio.wait_for`` (officially unsafe to rely on, cpython
    #139373).

    ``outer_timeout_sec`` is the resolved per-task cap (see
    ``resolve_outer_cap``): the env override if present, else the bench's
    **unscaled** task.toml ``[agent] timeout_sec`` base. Harbor may scale
    the real window up at run time (multiplier) — not observable from an
    installed agent — so the unscaled base is the conservative side (the
    declaration is never *larger* than the real window).

    ``None`` means no per-task cap is known (env unset AND terminal-bench
    varies per task). Positive turn limits use the existing fallback
    estimator. Unlimited runs use the independent 6120-second fallback
    (ADR-0405), preserving TB4's prior command window.
    """
    if outer_timeout_sec is not None:
        return outer_timeout_sec
    if max_turns == 0:
        return UNLIMITED_EXEC_FALLBACK_SEC
    return max(EXEC_FLOOR_SEC, max_turns * SECS_PER_TURN + TURN_OVERHEAD_SEC)
