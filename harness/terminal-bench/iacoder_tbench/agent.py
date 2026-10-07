"""IacoderAgent — Harbor adapter for iacoder on terminal-bench 2.0.

Per ADR 0043 the install + run logic lives in
`iacoder_harbor_base.IacoderHarborBase`; this module declares only the
per-bench identity (Harbor name + optional host turn policy).

Design refs:
- docs/decisions/0002-terminal-bench-2-as-eval-harness.md (selection)
- docs/decisions/0043-eval-harness-system.md (shared base, this file's shape)
- docs/architecture/eval-terminal-bench.md
"""

from __future__ import annotations

from iacoder_harbor_base import IacoderHarborBase


# ADR-0405: no adapter-imposed turn budget. Positive host overrides remain
# available; official task wall-clock limits are independent.
_DEFAULT_MAX_TURNS = 0


class IacoderAgent(IacoderHarborBase):
    """Harbor adapter for terminal-bench 2.0."""

    _DEFAULT_MAX_TURNS = _DEFAULT_MAX_TURNS
    # terminal-bench has NO uniform per-task timeout_sec (task.toml varies
    # per task), so no outer clamp can be honestly declared here — the
    # exec formula stands unclamped and the declared budget may exceed a
    # tight task's real window (accepted limitation, see base class doc).
    _TASK_OUTER_TIMEOUT_SEC = None
    CLI_FLAGS = IacoderHarborBase._build_cli_flags(default_max_turns=_DEFAULT_MAX_TURNS)

    @staticmethod
    def name() -> str:
        return "iacoder"
