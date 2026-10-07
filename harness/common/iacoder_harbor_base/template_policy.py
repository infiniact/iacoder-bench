"""Which prompt templates get the host's closeout policy (ADR-0384).

A template opts into iacoder's closeout mechanism in its own manifest
(`[mechanisms] closeout = true`, ADR-0369). The harness reads that declaration
instead of guessing from the template's name: a template that asked for the
closeout comparison gets the same host goals as the `iacoder` template (hand-off
plus the mechanical no-regression gate); one that did not is left exactly as
its upstream wrote it.

Standard library only, so the tests run without the bench virtualenv.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

#: The engine's own prompt — the CLI default (`--prompt-template` unset).
DEFAULT_TEMPLATE = "iacoder"

#: Bundled templates, at the same place relative to the harness as in the
#: repository. A deployment copies `crates/iacoder-prompts/templates/` next to
#: `eval/` (ADR-0384 D2).
BUNDLED_TEMPLATES = (
    Path(__file__).resolve().parents[3] / "crates" / "iacoder-prompts" / "templates"
)


def template_manifest(name: str, bundled: Path = BUNDLED_TEMPLATES) -> Path:
    """The manifest the CLI would load for `name`: a bundled template first,
    then a directory of that name (the CLI's own resolution order).

    Raises `FileNotFoundError` when neither exists: a harness that cannot see
    the template must not quietly pick a policy for it.
    """
    bundled_manifest = bundled / name / "manifest.toml"
    if "/" not in name and bundled_manifest.is_file():
        return bundled_manifest
    local_manifest = Path(name) / "manifest.toml"
    if local_manifest.is_file():
        return local_manifest
    raise FileNotFoundError(
        f"prompt template '{name}': no manifest at {bundled_manifest} or {local_manifest} "
        "(deploy crates/iacoder-prompts/templates/ next to eval/, ADR-0384 D2)"
    )


def opts_into_host_closeout(name: str | None, bundled: Path = BUNDLED_TEMPLATES) -> bool:
    """Whether the run's template takes the host's closeout goals.

    `None`/empty or `iacoder` → yes. Any other template → what its manifest's
    `[mechanisms] closeout` says (absent = no).
    """
    name = (name or "").strip()
    if not name or name == DEFAULT_TEMPLATE:
        return True
    with template_manifest(name, bundled).open("rb") as f:
        manifest = tomllib.load(f)
    return manifest.get("mechanisms", {}).get("closeout", False) is True
