"""IacoderPierAgent — Pier (DeepSWE) adapter for iacoder.

Bridges iacoder's Harbor base agent (``IacoderHarborBase``) to Pier's
lifecycle, which differs from Harbor in two ways:

1. Agent binaries are installed **at image build time** via
   ``install_spec()`` (inlined as Dockerfile RUN steps), not at container
   start via ``install()``.
2. Agent containers are air-gapped behind a Squid egress proxy; the LLM
   API domains the agent may reach must be declared via
   ``network_allowlist()``.

The binary tarball is served from a local HTTP server on the Pier host
(``IACODER_BINARY_URL`` env). config.toml + secrets.env are still
provisioned at runtime via the base class's upload flow.
"""
from __future__ import annotations
import base64
import os
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.network import NetworkAllowlist
from iacoder_harbor_base import IacoderHarborBase, NO_REGRESSION_PASSLIST_COMMAND
from iacoder_harbor_base.template_policy import opts_into_host_closeout
from iacoder_harbor_base.base import _with_closeout_goals, _CONTAINER_XDG, _PROCESS_ENV_PATH, _config_env, _read_host_config, _write_process_env_file, _write_secrets_file
# ADR-0405: no adapter-imposed turn budget. The task.toml 5400 s outer window
# (``_TASK_OUTER_TIMEOUT_SEC`` below) remains the binding constraint; a
# positive host override stays available via ``--ae max_turns=N``.
_DEFAULT_MAX_TURNS = 0
_LLM_DOMAINS = {'api.deepseek.com', 'open.bigmodel.cn', 'openrouter.ai'}

class IacoderPierAgent(IacoderHarborBase):
    # PORT v0.7.26/ADR-0232 S1b (mirrors iacoder_swebench agent.py:128):
    # auto-detecting pass-lister arms the mechanical no-regression gate.
    NO_REGRESSION_COMMAND = NO_REGRESSION_PASSLIST_COMMAND

    """Pier adapter for iacoder on DeepSWE."""
    _DEFAULT_MAX_TURNS = _DEFAULT_MAX_TURNS
    # task.toml [agent] timeout_sec — uniform 5400 across all 113 tasks (PR #600 clamp):
    # the declared budget (--deadline-secs) min-clamps to this true binding window.
    _TASK_OUTER_TIMEOUT_SEC = 5400
    CLI_FLAGS = IacoderHarborBase._build_cli_flags(default_max_turns=_DEFAULT_MAX_TURNS)
    HANDOFF_GATE = "every change this run produced is committed to the repository's history, and nothing that is not part of the work is: work left uncommitted has not been delivered, and unrelated files swept in are not part of what was asked for"
    NO_REGRESSION_GATE = "every check that passed before this run's changes still passes on the final state of the work, and any pre-existing expectation this run believes is genuinely wrong is flagged explicitly rather than silently changed"
    if os.environ.get('IACODER_EVAL_CLOSEOUT_GATES', 'on') == 'off':
        # ADR-0258 D3. This branch used to null HANDOFF and NO_REGRESSION but
        # re-assign SPEC_COVERAGE to a full inline copy of the gate text — so
        # the one switch named "off" left on exactly the criterion that has
        # never once judged a binding adequate, and the copy had already
        # diverged from the ADR-0235 single source. Any A/B that used this as
        # its control arm was not comparing against gates-off; it was comparing
        # against "two gates off, the third still on with older wording".
        # (ADR-0331 D4 later deleted the spec-coverage goal altogether.)
        HANDOFF_GATE = None
        NO_REGRESSION_GATE = None

    @staticmethod
    def name() -> str:
        return 'iacoder'

    def install_spec(self) -> AgentInstallSpec:
        """Build-time steps: fetch the prebuilt musl binary into the image."""
        binary_url = os.environ.get('IACODER_BINARY_URL', '')
        if not binary_url:
            raise RuntimeError('IACODER_BINARY_URL must point at an iacoder-<arch>-unknown-linux-musl tarball')
        return AgentInstallSpec(agent_name='iacoder', version=os.environ.get('IACODER_VERSION', 'v0.6.5'), steps=[InstallStep(run=f"set -eux; command -v curl >/dev/null 2>&1 || {{ apt-get update; apt-get install -y curl ca-certificates tar; }}; curl -fsSL -o /tmp/iacoder.tar.gz '{binary_url}'; mkdir -p /usr/local/bin; tar -xzf /tmp/iacoder.tar.gz -C /tmp; install -m 0755 /tmp/iacoder /usr/local/bin/iacoder; rm -f /tmp/iacoder.tar.gz /tmp/iacoder; iacoder --version", user='root')], verification_command='iacoder --version')

    def network_allowlist(self) -> NetworkAllowlist:
        return NetworkAllowlist(domains=sorted(_LLM_DOMAINS))

    async def install(self, environment) -> None:
        """Provision config.toml + secrets.env. The binary itself is already
        in the image (install_spec), so no download happens at runtime."""
        await environment.exec(command=f'mkdir -p {_CONTAINER_XDG}/iacoder', user='root')
        # C3 (v0.7.29/ADR-0239 run): align with base.py install() — when the
        # mechanical no-regression command is armed, DROP the prose no-regression
        # goal (mechanical check is authoritative; prose = weaker duplicate that
        # double-judges + one extra false-block face). handoff unaffected.
        # ADR-0331 D4 deleted the prose spec-coverage goal, ADR-0334 the
        # adversary that replaced it; handoff stays.
        # ADR-0356: another agent's prompt template runs without iacoder's own
        # text, and the closeout send-back for host goals is iacoder text — so
        # a template that did not ask for iacoder's closeout declares no host
        # goals and arms no no-regression command. ADR-0384: "asked for" is the
        # template's own `[mechanisms] closeout` declaration, not its name —
        # mini-swe-agent-custom / iacoder-headless / iacoder-lean opt in.
        _tpl = os.environ.get('IACODER_PROMPT_TEMPLATE', '').strip()
        _foreign_template = not opts_into_host_closeout(_tpl)
        _goals = [] if _foreign_template else [('handoff', self.HANDOFF_GATE)]
        if not _foreign_template and not self._no_regression_command():
            _goals.append(('no-regression', self.NO_REGRESSION_GATE))
        host_config = _with_closeout_goals(_read_host_config(), _goals, verify_command=None if _foreign_template else self._verify_command())
        # ADR-0373 D1: provider secrets (the config's ${ENV:VAR} refs) and the
        # process env (egress proxy, log level) travel in separate files. The
        # secrets go to `iacoder --secrets-file`, which reads and deletes them;
        # the process env is sourced, because the HTTP client needs the proxy
        # as a real environment variable.
        merged_env: dict[str, str] = {}
        if host_config is not None:
            merged_env.update(_config_env(host_config))
        try:
            proxy_env = environment.agent_process_env({}) or {}
        except Exception:
            proxy_env = {}
        process_env: dict[str, str] = dict(proxy_env)
        process_env['RUST_LOG'] = 'iacoder=info'
        if host_config is not None:
            cfg_b64 = base64.b64encode(host_config.encode()).decode()
            await environment.exec(command=f"printf '%s' '{cfg_b64}' | base64 -d > {_CONTAINER_XDG}/iacoder/config.toml && chmod 600 {_CONTAINER_XDG}/iacoder/config.toml", user='root')
        # ADR-0232 tooth (v0728b): ship the archive's gate wrappers into the
        # container at the exact paths the [verify] command references.
        # Fixes the v0728 gap: config referenced the scripts but never shipped
        # them, so agents self-authored substitutes. (ADR-0334: the
        # spec-coverage adversary wrapper is gone.)
        await environment.exec(command='mkdir -p /iacoder-cfg', user='root')
        _gates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'common', 'iacoder_harbor_base')
        for _gate in ('no-regression-gate.sh', 'no-regression-passlist.sh'):
            _gp = os.path.join(_gates_dir, _gate)
            if os.path.isfile(_gp):
                await environment.upload_file(_gp, f'/iacoder-cfg/{_gate}')
        # Arming a gate has to provision it. The Harbor installer writes the
        # command file the wrapper reads; the Pier path shipped the wrapper but
        # never the command, and `no-regression-gate.sh` cannot tell "this bench
        # did not opt in" from "it opted in and nobody installed the command" —
        # it no-ops for both. Meanwhile `install()` above DROPS the prose
        # no-regression goal precisely because the mechanical command is armed,
        # so the two paths cancelled each other and nothing enforced it at all.
        _noreg_cmd = None if _foreign_template else self._no_regression_command()
        if _noreg_cmd:
            import tempfile
            from pathlib import Path as _Path
            _fd, _tmp = tempfile.mkstemp(prefix='iacoder-noreg.', suffix='.cmd')
            _cmd_path = _Path(_tmp)
            try:
                with os.fdopen(_fd, 'w') as _f:
                    _f.write(_noreg_cmd)
                await environment.upload_file(_cmd_path, f'{_CONTAINER_XDG}/no_regression_command')
            finally:
                _cmd_path.unlink(missing_ok=True)
        process_path = _write_process_env_file(process_env)
        try:
            await environment.upload_file(process_path, _PROCESS_ENV_PATH)
        finally:
            process_path.unlink(missing_ok=True)
        if not any(k != 'XDG_CONFIG_HOME' for k in merged_env):
            return
        secrets_path = _write_secrets_file(merged_env)
        try:
            await environment.upload_file(secrets_path, f'{_CONTAINER_XDG}/secrets.env')
        finally:
            secrets_path.unlink(missing_ok=True)

    def to_agent_info(self) -> 'AgentInfo':
        """Pier-native AgentInfo (harbor fork's AgentInfo is a different class)."""
        return _pier_agent_info(self)

def _parse_model(model_name: str) -> tuple[str | None, str | None]:
    """Split 'provider/model' into (provider, name); bare name → (None, name)."""
    if '/' in model_name:
        provider, name = model_name.split('/', maxsplit=1)
        return (provider, name)
    return (None, model_name or None)

def _pier_agent_info(agent) -> 'AgentInfo':
    """Build a pier-native AgentInfo, tolerating harbor-fork attribute gaps."""
    from pier.models.trial.result import AgentInfo, ModelInfo
    model_name = getattr(agent, 'model_name', None) or ''
    provider, name = _parse_model(model_name)
    try:
        version = agent.version() or 'unknown'
    except Exception:
        version = 'unknown'
    return AgentInfo(name=agent.name(), version=version, model_info=ModelInfo(name=name, provider=provider) if name else None)
# pier-side lineage: pier checks isinstance(agent, pier.BaseInstalledAgent) before
# calling populate_context_post_run; our base comes from the harbor package.
# Register as a virtual subclass so the usage hook fires for pier jobs.
try:
    from pier.agents.installed.base import BaseInstalledAgent as _PierBaseInstalledAgent
    _PierBaseInstalledAgent.register(IacoderPierAgent)
except ImportError:
    pass
