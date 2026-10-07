"""Shared base for iacoder Harbor `BaseInstalledAgent` adapters.

All five iacoder eval adapters (terminal-bench, swebench-verified,
aider-polyglot, crustbench, bfcl) share the same install path, the
same config.toml pass-through, and the same shell-`timeout` wrap. This
package owns that shared logic once; per-bench adapters subclass and
override only the three constants that differ — Harbor `name()`,
`_DEFAULT_MAX_TURNS`, and (optionally) `CLI_FLAGS` defaults.

See `docs/decisions/0043-eval-harness-system.md` for the ADR.
"""

from iacoder_harbor_base.base import (
    NO_REGRESSION_PASSLIST_COMMAND,
    IacoderHarborBase,
)

__all__ = ["IacoderHarborBase", "NO_REGRESSION_PASSLIST_COMMAND"]
__version__ = "0.1.0"
