# Everything the process is told about *itself*, read once, here.
#
# What it does not hold is anything about which model to talk to or how to authenticate to one.
# That is per-profile and lives in `config.toml`, because a session chooses among profiles and a
# process-wide answer would be a second answer to a question each session already answers.

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from mainplate.profiles import config_home

# What a fresh checkout gets with nothing set. A file under the working directory rather than
# under the user's data directory, because this is a tool you point at a project.
DEFAULT_DATABASE = Path("mainplate.db")

DEFAULT_INSTRUCTIONS = "You are a helpful assistant, working with a software engineer. Be concise and direct."


class Settings(BaseSettings):
    """
    The process's configuration, parsed from the environment at startup.

    Every field is settable and every field has a default that runs, so the shortest way to a
    working console is `mainplate serve` with nothing set but a `config.toml` naming one profile.
    """

    model_config = SettingsConfigDict(env_prefix="MAINPLATE_", frozen=True)

    database: Path = DEFAULT_DATABASE
    """The SQLite file holding every session's checkpoint, its queue, and the session index."""

    config_home: Path = Field(default_factory=lambda: config_home(os.environ))
    """
    Where to look for `mainplate/config.toml`, which is where the profiles are.

    A directory rather than the file, so it moves with `XDG_CONFIG_HOME` the way everything else
    under it does, and so the one place that knows the file's name is the module that parses it.
    """

    instructions: str = DEFAULT_INSTRUCTIONS

    workspaces: Path | None = None
    """
    Where this console keeps repositories and the worktrees sessions work in, or beside the
    database when not named.

    There is no setting naming a *repository*, and that is the design rather than an omission: what
    a session works in is picked when it is created, from whatever the forges reach, and recorded
    on the session. A process-wide answer would be a second answer to a question each session
    already answers, exactly as a process-wide model would be.

    Clones and worktrees both live under here, in `clones/` and `worktrees/`, and both are outside
    any repository they hold. A worktree inside its own repository would be captured by the very
    snapshots it exists to take, so every session would hold a copy of every other session's files.
    """

    host: str = "127.0.0.1"

    port: int = 8100

    passes: int = Field(default=8, gt=0)
    """
    How many sessions this process answers at once.

    A pass spends nearly all of its time waiting on the provider, so the useful number is well
    above the core count and is bounded by what the provider will take rather than by this
    machine. Low by the worker's own standards because a personal console rarely has eight
    conversations in flight, and every pass in flight holds the one SQLite connection in turn.
    """

    refresh: timedelta = Field(default=timedelta(minutes=15), gt=timedelta())
    """
    How often to ask every endpoint what models it serves.

    The list is read once before the server is ready and then kept current by a background task, so
    this decides only how long a model added at the gateway stays invisible here. Fifteen minutes
    is short against how often a provider ships one and long enough that a console left open for a
    week makes a few hundred requests rather than a few hundred thousand.
    """

    @property
    def workspace_root(self) -> Path:
        """
        Where clones and worktrees go, which is what was named or a directory beside the database.

        Beside the database because the two are the halves of one session: the checkpoint says what
        was said and the worktree holds what it was said about, so a console pointed at another
        database gets its own worktrees rather than sharing the first one's.
        """
        return self.workspaces if self.workspaces is not None else self.database.parent / "workspaces"

    lease: timedelta = Field(default=timedelta(minutes=10), gt=timedelta())
    """
    How long a pass may take before another worker may take the session over.

    It has to exceed the longest a turn can honestly run, and nothing can work that out for you:
    set it too short and a slow model call is fenced mid-flight and re-run, too long and a
    crashed process leaves its session waiting that long. Ten minutes is a generous bound on one
    model call and short enough to be worth waiting out after a crash.
    """
