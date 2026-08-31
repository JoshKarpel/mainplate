# What you do with mainplate: run it, put it on a machine, and take it off again.
#
# No command here decides anything. `serve` builds the app and runs it; `install` renders a unit
# and converges it; `uninstall` removes the same unit. Every setting is parsed from the environment
# by `Settings`, and an option here overrides one field of it, so there is one description of what
# is configurable and the CLI is a way to say it once rather than a second place it is defined.

from __future__ import annotations

import asyncio
import getpass
import os
import shutil
from pathlib import Path
from typing import Annotated

import typer

from mainplate.app import DidNotStart
from mainplate.app import serve_until_stopped
from mainplate.exe import discover_gateways
from mainplate.install import SERVICE
from mainplate.install import Converged
from mainplate.install import NoInterpreter
from mainplate.install import ProgramFailed
from mainplate.install import Removed
from mainplate.install import Unit
from mainplate.install import can_run_mainplate
from mainplate.install import converge
from mainplate.install import data_home
from mainplate.install import default_database
from mainplate.install import is_lingering
from mainplate.install import remove
from mainplate.install import running_executable
from mainplate.install import systemctl_for
from mainplate.profiles import BadConfig
from mainplate.profiles import config_home
from mainplate.settings import Settings

Database = Annotated[
    Path | None,
    typer.Option("--database", "-d", help="the SQLite file holding every session; created if it is not there"),
]

Host = Annotated[str | None, typer.Option("--host", help="the address to bind")]

Port = Annotated[int | None, typer.Option("--port", "-p", help="the port to serve on")]

mainplate = typer.Typer(
    help="Run a coding agent that keeps its own sessions, with a chat console in front of it.",
    no_args_is_help=True,
    add_completion=False,
)


@mainplate.callback()
def commands() -> None:
    """
    Nothing, and that is its job.

    Typer folds a single-command app into its root, so `mainplate serve` would be spelled
    `mainplate` today and become `mainplate serve` the moment a second command appears, breaking
    every invocation written in between. A callback keeps the subcommand from the start.
    """


@mainplate.command()
def serve(database: Database = None, host: Host = None, port: Port = None) -> None:
    """
    Run the console and the worker that answers its sessions, until a signal stops the process.

    Every option is optional because every setting has a default that runs. What is required is a
    configuration file with at least one profile in it, because a profile is what a session is
    answered on; there is no `--model`, since a session records its own and a process-wide one
    would be a second answer to a question each session already answers.
    """
    settings = Settings()
    overridden = settings.model_copy(
        update={
            name: value for name, value in (("database", database), ("host", host), ("port", port)) if value is not None
        }
    )
    try:
        serve_until_stopped(overridden)
    except BadConfig as unusable:
        # A missing or malformed configuration file is the ordinary first-run state, not a fault:
        # a traceback through `pathlib` says nothing a person can act on, where the file's path
        # and the command that writes one do.
        typer.echo(f"{unusable}\n\nWrite that file, or run `mainplate install` to start one.", err=True)
        raise typer.Exit(1) from None
    except DidNotStart as unstarted:
        raise typer.BadParameter(str(unstarted)) from unstarted


def built_unit(host: str | None = None, port: int | None = None, database: Path | None = None) -> Unit:
    """
    The unit this machine would get, from the interpreter running this command and the XDG paths.

    `Settings()` supplies the host and port so the service and the foreground command default the
    same way, and an explicit flag beats both. The database does *not* come from `Settings`,
    because its default is a relative path meant for a foreground run: a service has no meaningful
    working directory, so an install picks an absolute one under `$XDG_DATA_HOME`.
    """
    settings = Settings()
    homes = data_home(os.environ)
    return Unit(
        config_home=config_home(os.environ),
        executable=running_executable(),
        host=host if host is not None else settings.host,
        port=port if port is not None else settings.port,
        database=(database if database is not None else default_database(homes)).expanduser().absolute(),
    )


async def installing(unit: Unit) -> Converged:
    return await converge(unit, systemctl_for(os.environ), await discover_gateways())


def systemd_here() -> None:
    """Refuse before anything is written where there is no service manager to write it for."""
    if shutil.which("systemctl") is None:
        typer.echo("no systemctl here, so there is no service to install", err=True)
        raise typer.Exit(1)


@mainplate.command()
def install(database: Database = None, host: Host = None, port: Port = None) -> None:
    """
    Converge a user systemd unit and restart mainplate onto this interpreter.

    The unit names the interpreter running this command, so what an install means is "the running
    service is this installation of the package". Run it again after changing the code or
    upgrading: an upgrade in place leaves the unit text identical, so only the restart puts the new
    code in front of anything.

    Safe to run as often as you like. A restarted worker leaves whatever pass was in flight in the
    queue, and the one that comes back resumes it from the checkpoint.
    """
    systemd_here()
    try:
        unit = built_unit(host, port, database)
    except NoInterpreter as missing:
        typer.echo(str(missing), err=True)
        raise typer.Exit(1) from None

    # Asked before anything is written, so a unit that could not start is never installed.
    if not asyncio.run(can_run_mainplate(unit.executable)):
        typer.echo(
            f"{unit.executable} cannot import mainplate, so the service would fail at every start.\n"
            f"Install the package and run `mainplate install` from that installation.",
            err=True,
        )
        raise typer.Exit(1)

    report_install(converged(unit))


def converged(unit: Unit) -> Converged:
    """
    Converge the unit, having first asked this machine whether it is an exe.dev VM with a gateway.

    Discovery is here rather than inside `converge` because it is a request over the network, and
    the one thing an installer should not do is behave differently depending on what a hostname
    resolved to somewhere it was never meant to run.
    """
    try:
        return asyncio.run(installing(unit))
    except ProgramFailed as failed:
        typer.echo(str(failed), err=True)
        raise typer.Exit(1) from None


def report_install(done: Converged) -> None:
    """
    What the install did, and the two things that would otherwise be discovered much later.

    Neither note is a failure, so neither is an exit code: the service is installed and running
    either way. A missing credential shows up as an error on the first message, and lingering
    shows up as nothing running after a reboot, and both are worth a sentence at the moment
    somebody is looking.
    """
    typer.echo(f"{SERVICE} is {'installed' if done.unit_changed else 'unchanged'} and restarted")
    for gateway in done.gateways:
        typer.echo(f"  found    exe.dev llm integration {gateway.name!r} at {gateway.base_url}")
    typer.echo(f"  console  http://{done.unit.host}:{done.unit.port}")
    typer.echo(f"  unit     {done.unit.path}")
    typer.echo(f"  profiles {done.unit.config}")
    typer.echo(f"  settings {done.unit.environment}")
    typer.echo(f"  sessions {done.unit.database}")
    typer.echo(f"  logs     journalctl --user -u {SERVICE} -f")

    if not done.startable:
        typer.echo(
            f"\nnote: {done.unit.config} declares no usable profile, so the service will fail at\n"
            f"startup and restart every five seconds until it does. That is the loud version of a\n"
            f"console that could answer nothing. Uncomment a profile there, then\n"
            f"`systemctl --user restart {SERVICE}`.",
            err=True,
        )

    user = getpass.getuser()
    if not asyncio.run(is_lingering(user)):
        typer.echo(
            f"\nnote: lingering is off for this user, so {SERVICE} starts at your first login rather\n"
            f"than at boot. `loginctl enable-linger {user}` fixes that.",
            err=True,
        )


@mainplate.command()
def uninstall() -> None:
    """
    Stop the service and take its unit off this machine, keeping its settings and its sessions.

    What stays is the environment file and the database, because removing those is a decision about
    your credential and your conversations rather than a step in getting rid of a unit. A later
    install picks both back up.
    """
    systemd_here()
    try:
        unit = built_unit()
    except NoInterpreter as missing:
        typer.echo(str(missing), err=True)
        raise typer.Exit(1) from None
    try:
        report_uninstall(asyncio.run(remove(unit, systemctl_for(os.environ))))
    except ProgramFailed as failed:
        typer.echo(str(failed), err=True)
        raise typer.Exit(1) from None


def report_uninstall(done: Removed) -> None:
    typer.echo(f"{SERVICE} is {'removed' if done.unit_removed else 'not installed'}")
    typer.echo(f"  kept  {done.unit.environment}")
    typer.echo(f"  kept  {done.unit.database}")


def main() -> None:
    mainplate()
