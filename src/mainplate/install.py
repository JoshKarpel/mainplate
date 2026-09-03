# Putting mainplate on a machine, which on a Linux box means a user systemd unit.
#
# The unit names the interpreter that ran the install. That is the whole mechanism and everything
# else follows from it: `mainplate install` is invoked *by* the installed CLI, so `sys.executable`
# is already the absolute path of an interpreter holding this package and its dependencies.
# Nothing has to be looked up on `PATH`, derived from a login shell, or guessed, and every way of
# installing the package (`uv tool`, `pipx`, a checkout) is served by the same rendering.
#
# It is also why this never writes `uv run` into a unit. A `uv run` process holds a shared lock on
# the uv cache for its entire lifetime, so a service started that way blocks `uv cache prune` on
# the machine for as long as it is up; and a service with dependencies cannot use `--no-cache` to
# escape that without re-downloading them on every start. Naming an interpreter that already has
# the packages sidesteps both.
#
# What this deliberately does not do is fetch, build, or manage a Python environment. Whoever put
# the package here already chose a version; this only points systemd at it.

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

from mainplate.config import BadConfig
from mainplate.config import config_path
from mainplate.config import read_config
from mainplate.exe import Gateway
from mainplate.reference import MODELS_DEV

SERVICE: Final = "mainplate"

# Long enough for a loaded machine and short enough that a wedged `systemctl` is a failure rather
# than a hang. Every call here is a local query or a unit operation on an already-written file.
PATIENCE: Final = timedelta(seconds=30)

UNIT: Final = """\
[Unit]
Description=mainplate: a coding agent that keeps its own sessions, served on port {port}
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
# A user unit inherits none of a login shell's PATH, so this is the standard system set and
# nothing more.
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Any MAINPLATE_* process setting, and whichever key an SDK falls back to reading for itself.
# Provider credentials live in config.yaml instead, where they are read from the file rather than
# put in the environment. Not optional (`-`), so a deleted file is a start that fails loudly.
EnvironmentFile={environment}
ExecStart={executable} -m mainplate serve --host {host} --port {port} --database {database}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

# What a fresh environment file says, which is what it is for and nothing that is a value. It is
# written once and never overwritten, so this is the only chance to say what belongs in it.
#
# It is no longer where the credential goes: `config.yaml` is, per endpoint, and a key read from a
# file and handed to the SDK never enters this process's environment at all. What is left here is
# the fallback each SDK does for itself, for an endpoint naming neither a key nor an endpoint, and
# any `MAINPLATE_*` process setting.
ENVIRONMENT: Final = """\
# Read by the mainplate user service, and by nothing else on this machine.
#
# systemd parses this itself: NAME=value per line, no `export`, no shell quoting or expansion.
# Keep it 0600.
#
# Provider credentials belong in config.yaml, per endpoint, where they are read from the file and
# never enter the environment. These are only the fallback for an endpoint that names no key and no
# endpoint of its own, which is what each SDK reads for itself: one per wire.

# ANTHROPIC_API_KEY=
# OPENAI_API_KEY=

# Any setting `mainplate serve --help` lists can go here as MAINPLATE_<NAME>. The host, port, and
# database are on the unit's own ExecStart instead, because the install chose those.
# MAINPLATE_INSTRUCTIONS=You are a helpful assistant.
"""

# The configuration a machine gets when it has no gateway to point at: one endpoint, commented out,
# so the file says what an endpoint is and refuses to start until somebody means it. Refusing is the
# point, since a default endpoint with a placeholder key would start a console that fails on the
# first message instead of at boot.
TEMPLATE: Final = """\
# Which endpoints mainplate can answer on, and which one a new session starts on.
#
# An endpoint is where requests go, which API format is spoken to it, and how to authenticate. The
# models are not part of one and are not listed here: mainplate asks each endpoint what it serves
# and offers whatever comes back. A session records an endpoint *and* a model, and both are fixed
# for its life.
#
# Uncomment one and set `default` to its name. This file holds credentials, so keep it 0600.

# default: anthropic
#
# # Optional: which of the default endpoint's models a new session starts on. Left out, it is
# # whichever the endpoint lists first, which for most gateways is their newest.
# default_model: claude-opus-5
#
# endpoints:
#   anthropic:
#     format: anthropic
#     # omit api_key to fall back to ANTHROPIC_API_KEY in the environment
#     api_key: sk-ant-...
{reference}"""

# What a discovered exe.dev gateway is written as. No key, because there is nothing to write: the
# credential is injected at exe.dev's edge and the VM never holds one.
GATEWAY: Final = """\
# Written by `mainplate install`, which found this VM's exe.dev LLM integration.
#
# No credential: exe.dev injects one at its own edge, so this VM holds no key to store or rotate.
# Nothing lists models either: mainplate asks each endpoint what it serves, at startup and on a
# timer after that, and offers whatever comes back.
#
# One hostname, two endpoints, because exe.dev answers both API formats there and each reaches
# models the other does not. The Anthropic format serves every Claude and the Fireworks models; the
# OpenAI format serves GPT, Grok, and the Fireworks models again. Note the `/v1`, which only the
# OpenAI SDK wants: it appends `/chat/completions` where the Anthropic SDK appends `/v1/messages`.
#
# The *provider* of a model (anthropic, fireworks, xai) is not configured here and is not a level of
# this file: it is discovered, and the same provider shows up under both endpoints.
#
# A team integration answers at `https://<name>.team.exe.xyz` rather than `.int.`.

default: {default}

endpoints:
{endpoints}{reference}"""

# The one thing in a written configuration file that reaches a third party, so it is written with
# the sentence that turns it off directly above it. Active rather than commented out, because the
# facts it fetches are most of what the model picker shows and a setting nobody knows about is a
# setting nobody turns on; deletable in four lines, because a console pointed at a private
# repository on a machine with no outbound access is a case this project means to support.
REFERENCE: Final = """
# Where to look up what the endpoints do not publish: what a model costs, how much it reads, and
# what it can do. No gateway reached so far publishes a price at all, so without this the model
# cards show a name and an id and nothing else.
#
# `source` is fetched when it is a URL and read when it is a path, so a machine with no outbound
# access can point at a file it already has. Delete these four lines and mainplate calls nobody but
# the endpoints above.
model_reference:
  source: {source}
  format: models.dev
"""

# Two entries per gateway, indented to sit under `endpoints:`.
GATEWAY_ENDPOINTS: Final = """\
  {name}-anthropic:
    format: anthropic
    url: {base_url}

  {name}-openai:
    format: openai
    url: {base_url}/v1
"""


# What systemctl is asked, as the only shape of call this makes: arguments after `--user`, and the
# whole result back. Injected rather than reached for, so a test drives convergence without a
# service manager and asserts on what was asked rather than on what happened.
type Systemctl = Callable[[tuple[str, ...]], Awaitable[Ran]]


class ProgramFailed(RuntimeError):
    """
    A program that was expected to succeed did not.

    Carries the whole result rather than a message, because what a caller wants to do about a
    failure usually depends on the output: `systemctl`'s stderr names the unit it could not load,
    and that is the diagnosis rather than a detail of it.
    """

    def __init__(self, ran: Ran) -> None:
        self.ran = ran
        super().__init__(f"{ran.summary} exited {ran.exit_code}: {ran.stderr.strip() or ran.stdout.strip()}")


@dataclass(frozen=True, slots=True)
class Ran:
    """What one program did: its own report, decoded, plus whether it ran out of time."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def summary(self) -> str:
        return " ".join(self.command)

    def checked(self) -> Ran:
        """The same result when the program succeeded, or a failure carrying it when it did not."""
        if not self.ok:
            raise ProgramFailed(self)
        return self


async def run(*command: str, env: Mapping[str, str] | None = None) -> Ran:
    """
    Run `command` to completion and report what happened, including running out of time.

    Every program this runs is short, quiet, and expected to finish on its own, so this is
    `communicate` with a bound around it rather than a process supervisor. A timeout comes back as
    a value with `timed_out` set, which `checked()` turns into a failure for the callers that want
    one.
    """
    started = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=None if env is None else dict(env),
    )
    try:
        async with asyncio.timeout(PATIENCE.total_seconds()):
            out, err = await started.communicate()
    except TimeoutError:
        started.kill()
        await started.wait()
        return Ran(command=command, exit_code=-1, stdout="", stderr="", timed_out=True)
    return Ran(
        command=command,
        exit_code=started.returncode if started.returncode is not None else -1,
        stdout=out.decode(errors="replace"),
        stderr=err.decode(errors="replace"),
        timed_out=False,
    )


@dataclass(frozen=True, slots=True)
class Unit:
    """
    One installed mainplate: where its files live, and what the service it starts is told.

    Everything that can differ between a machine and another is here, so the rendering is a pure
    function of this value and a test asserts on the text without a service manager anywhere near
    it.
    """

    config_home: Path
    executable: Path
    host: str
    port: int
    database: Path

    @property
    def path(self) -> Path:
        return self.config_home / "systemd" / "user" / f"{SERVICE}.service"

    @property
    def config(self) -> Path:
        """Where the endpoints live, which is the file a person edits to add an endpoint or a key."""
        return config_path(self.config_home)

    @property
    def environment(self) -> Path:
        """
        Where the credential lives, which is beside the configuration and not inside the unit.

        The unit is world-readable by design (systemd reads it, and so does anyone listing your
        units); this is 0600. What a key put here does not buy is the thing the secrets guidance
        actually wants, since the value becomes a process environment variable, inherited by every
        child and readable at `/proc/<pid>/environ`. That is why an endpoint's `api_key` is the way
        to hand one over: it is read from `config.yaml` at the point of use and never enters this
        environment at all. What is left here is each SDK's own fallback, for an endpoint naming
        neither a key nor an endpoint, and any `MAINPLATE_*` process setting.
        """
        return self.config_home / SERVICE / "environment"

    @property
    def text(self) -> str:
        """
        The unit file this install would have.

        Deliberately unhardened: no `ProtectSystem`, no `ReadWritePaths`, no `NoNewPrivileges`.

        The agent edits repositories, so the paths it legitimately writes are the worktree root and
        everything beneath it, which is exactly what a `ReadWritePaths` would have to name. The
        boundary that actually holds is `Files.resolved`, which resolves every path a tool is given
        and refuses one landing outside the session's own worktree; a unit sandbox loose enough to
        permit that root protects nothing the tools do not already refuse.
        """
        return UNIT.format(
            executable=self.executable,
            environment=self.environment,
            host=self.host,
            port=self.port,
            database=self.database,
        )


@dataclass(frozen=True, slots=True)
class Converged:
    """What an install actually changed, which after the first run is usually nothing."""

    unit: Unit
    unit_changed: bool
    environment_created: bool
    config_created: bool
    gateways: tuple[Gateway, ...]
    startable: bool


@dataclass(frozen=True, slots=True)
class Removed:
    """What an uninstall took away, and what it deliberately left."""

    unit: Unit
    unit_removed: bool


class NoInterpreter(RuntimeError):
    """
    There is no interpreter path to put in the unit, or the one there is cannot run mainplate.

    Raised at install time on purpose. The alternative is a unit that installs cleanly and then
    fails at every start with `No module named mainplate`, restarting every five seconds,
    discovered whenever somebody next looks at the machine.
    """


def running_executable() -> Path:
    """
    The interpreter to name in the unit, which is this process's own.

    `sys.executable` rather than `sys.argv[0]`: the former is documented as the absolute path of
    the running interpreter, while the latter is a console-script shim that can be relocated
    independently of the environment it points into.

    Deliberately *not* resolved. A virtualenv's `bin/python` is a symlink to the base interpreter
    it was built from, and that base has none of the venv's packages on its path, so resolving it
    yields an interpreter that cannot import this one. The symlink is what makes the venv a venv,
    so following it is exactly wrong.
    """
    if not sys.executable:
        raise NoInterpreter("this Python reports no executable path, so there is nothing to put in a unit")
    return Path(sys.executable)


async def can_run_mainplate(executable: Path) -> bool:
    """
    Whether `executable` can actually import this package, asked rather than assumed.

    True by construction for the ordinary install, since the command doing the asking is running on
    that interpreter. Asked anyway because it is one cheap subprocess against a failure that is
    silent at install and loud only much later, and because the assumption does not hold for a
    resolved venv symlink.
    """
    return (await run(str(executable), "-c", "import mainplate")).ok


def data_home(environ: Mapping[str, str]) -> Path:
    """
    Where a session's contents live, which is data rather than state or cache.

    Not the working directory, which is where the foreground command defaults: a service has no
    meaningful one, and a database that moved with whatever directory somebody happened to install
    from would be a different set of sessions every time.
    """
    return Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def default_database(data_home: Path) -> Path:
    return data_home / SERVICE / f"{SERVICE}.db"


def systemctl_for(environ: Mapping[str, str]) -> Systemctl:
    """
    `systemctl --user` on this machine, with the one variable it cannot do without.

    A non-login context (a hook, a provisioning script, a command over ssh) usually lacks
    `XDG_RUNTIME_DIR`, and without it every call fails with "Failed to connect to bus: No medium
    found", which names nothing a person could act on.
    """
    env = {**os.environ, "XDG_RUNTIME_DIR": environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"}

    async def call(arguments: tuple[str, ...]) -> Ran:
        return await run("systemctl", "--user", *arguments, env=env)

    return call


async def is_lingering(user: str) -> bool:
    """
    Whether this user's manager starts at boot rather than at first login.

    Worth asking because the failure without it is silent in the worst way: the unit is enabled,
    the file is correct, and after a reboot nothing is running until somebody happens to log in.
    """
    shown = await run("loginctl", "show-user", user, "--property=Linger")
    return shown.ok and shown.stdout.strip().endswith("=yes")


def write_unit(path: Path, wanted: str) -> bool:
    """
    Put `wanted` at `path` if it is not already there, and say whether anything changed.

    The returned bool is what `converge` gates `daemon-reload` on, which is the only reason the
    comparison happens at all: the manager needs re-reading exactly when the file it read has
    changed, and never otherwise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == wanted:
        return False
    path.write_text(wanted)
    path.chmod(0o644)
    return True


def write_environment(path: Path) -> bool:
    """
    Create the environment file if it is not there, and say whether this call created it.

    Never overwritten, which is the whole of its handling: it is the one file here a person edits,
    and it holds the credential. An install that rewrote it would delete the key on every upgrade.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ENVIRONMENT)
    path.chmod(0o600)
    return True


def render_config(gateways: Sequence[Gateway]) -> str:
    """
    The configuration file for a machine with these gateways, or the template when it has none.

    Rendered rather than serialized, because what a first configuration file is *for* is being
    read and edited: a `yaml.safe_dump` round trip would produce a valid file with none of the comments
    explaining what an endpoint is, which is most of what a person opening it needs.
    """
    reference = REFERENCE.format(source=MODELS_DEV)
    if not gateways:
        return TEMPLATE.format(reference=reference)
    endpoints = "\n".join(
        GATEWAY_ENDPOINTS.format(name=gateway.name, base_url=gateway.base_url) for gateway in gateways
    )
    # The Anthropic wire is the default of the two, because its list is the one written for a
    # person to read: every entry carries a display name, and none of them is an embedding model
    # or the same model under a second id.
    return GATEWAY.format(default=f"{gateways[0].name}-anthropic", endpoints=endpoints, reference=reference)


def write_config(path: Path, gateways: Sequence[Gateway]) -> bool:
    """
    Create the configuration file if it is not there, and say whether this call created it.

    Never overwritten, for the reason the environment file is not: it is a file a person edits and
    it holds credentials. That is also why discovery cannot *update* an existing file to add a
    gateway found later - the install says what it found and leaves the editing to whoever owns
    the file.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(gateways))
    path.chmod(0o600)
    return True


def usable_config(path: Path) -> bool:
    """
    Whether the configuration file is one the service can actually start on.

    Asked by parsing it rather than by looking for a line, because a configuration file is
    structured and the failure worth catching is "this will not boot": the template parses as
    valid TOML and declares no endpoints, which is exactly the state a fresh install leaves and
    exactly the state worth mentioning.
    """
    try:
        read_config(path)
    except BadConfig:
        return False
    return True


async def converge(unit: Unit, systemctl: Systemctl, gateways: Sequence[Gateway] = ()) -> Converged:
    """
    Make the service match this interpreter: the file it is written from, and the code it runs.

    Comparing the unit text is not enough, and stopping there is wrong in the worst way available.
    The text is a function of an interpreter path and the settings the install was given, so an
    upgrade in place renders identically, and an install that restarted only on a difference would
    report success while leaving the old code serving.

    So the restart is unconditional and the reload is not: the manager needs re-reading only when
    the file it read has changed. Restarting costs whatever pass was in flight, which is nothing
    that is lost: a cancelled pass leaves its session in the queue, and the worker that comes back
    resumes it from the checkpoint.

    Enabling is unconditional because enabling an already-enabled unit changes nothing, and a unit
    that is installed but not enabled is the failure this exists to prevent.

    The database's directory is created here rather than left to the first write, because the
    service's own start is a poor place to discover that `~/.local/share` is not writable.

    `gateways` is passed in rather than discovered here, for the reason `systemctl` is: discovery
    is a request to a hostname that exists only inside an exe.dev VM, so a `converge` that made it
    itself would behave differently depending on which machine the test suite ran on.
    """
    unit.database.parent.mkdir(parents=True, exist_ok=True)
    configured = write_config(unit.config, gateways)
    created = write_environment(unit.environment)
    changed = write_unit(unit.path, unit.text)
    if changed:
        (await systemctl(("daemon-reload",))).checked()

    (await systemctl(("enable", SERVICE))).checked()
    (await systemctl(("restart", SERVICE))).checked()
    return Converged(
        unit=unit,
        unit_changed=changed,
        environment_created=created,
        config_created=configured,
        gateways=tuple(gateways),
        startable=usable_config(unit.config),
    )


async def remove(unit: Unit, systemctl: Systemctl) -> Removed:
    """
    Take the service off this machine, and leave everything the machine owns where it is.

    What stays is the configuration file, the environment file, and the database. Removing those
    is a decision about your credentials and your conversations rather than a step in getting rid
    of a unit file, and a reinstall picks all of them back up.

    Whether there is anything to remove is decided by looking, which is what makes running this
    twice unremarkable rather than an error: `systemctl` refuses `disable` for a unit whose file is
    gone. The order is what the manager needs rather than what reads well, since it cannot disable
    a unit whose file has been deleted: disable, then delete, then reload. `reset-failed` is
    unchecked, alone here, because it clears a service that died in a way that would otherwise sit
    in `--failed` forever, and failing an uninstall that has already stopped the service over a
    leftover listing would leave a person worse off than saying nothing.
    """
    installed = unit.path.exists()
    if installed:
        (await systemctl(("disable", "--now", SERVICE))).checked()
        await systemctl(("reset-failed", SERVICE))
        unit.path.unlink()
        (await systemctl(("daemon-reload",))).checked()
    return Removed(unit=unit, unit_removed=installed)
