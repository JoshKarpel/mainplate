# The one thing you do with mainplate: run it.
#
# The command decides nothing. Every setting is parsed from the environment by `Settings`, and an
# option here overrides one field of it, so there is one description of what is configurable and
# the CLI is a way to say it once rather than a second place it is defined.

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from mainplate.app import DidNotStart
from mainplate.app import serve_until_stopped
from mainplate.settings import Settings

Database = Annotated[
    Path | None,
    typer.Option("--database", "-d", help="the SQLite file holding every session; created if it is not there"),
]

Model = Annotated[
    str | None,
    typer.Option("--model", "-m", help="the model every session talks to, as Pydantic AI's `provider:name`"),
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
def serve(database: Database = None, model: Model = None, host: Host = None, port: Port = None) -> None:
    """
    Run the console and the worker that answers its sessions, until a signal stops the process.

    Every option is optional because every setting has a default that runs; what is not optional
    is the provider's credential, which Pydantic AI reads from the environment itself.
    """
    settings = Settings()
    overridden = settings.model_copy(
        update={
            name: value
            for name, value in (("database", database), ("model", model), ("host", host), ("port", port))
            if value is not None
        }
    )
    try:
        serve_until_stopped(overridden)
    except DidNotStart as unstarted:
        raise typer.BadParameter(str(unstarted)) from unstarted


def main() -> None:
    mainplate()
