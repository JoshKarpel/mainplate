# Everything the process is told about *itself*, read once, here.
#
# What it does not hold is anything about which model to talk to or how to authenticate to one.
# That is per-endpoint and lives in `config.yaml`, because a session chooses among endpoints and a
# process-wide answer would be a second answer to a question each session already answers.

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from mainplate.config import config_home

# What a fresh checkout gets with nothing set. A file under the working directory rather than
# under the user's data directory, because this is a tool you point at a project.
DEFAULT_DATABASE = Path("mainplate.db")

DEFAULT_INSTRUCTIONS = "You are a helpful assistant, working with a software engineer. Be concise and direct."

# How often a live connection looks for something new. Named here rather than written twice,
# because `Service` carries the value a handler reads and this is where the configured one enters
# the process: two defaults for one interval is a pair that can come to disagree.
DEFAULT_WATCHING = timedelta(milliseconds=200)

# How long a command a person runs may take before it is killed. Here rather than in `commands.py`
# for the reason `DEFAULT_WATCHING` is here: `Commands` carries the value it was built with and this
# is where the configured one enters the process, so two defaults for one bound is a pair that can
# come to disagree.
DEFAULT_PATIENCE = timedelta(minutes=10)


class Settings(BaseSettings):
    """
    The process's configuration, parsed from the environment at startup.

    Every field is settable and every field has a default that runs, so the shortest way to a
    working console is `mainplate serve` with nothing set but a `config.yaml` naming one endpoint.
    """

    model_config = SettingsConfigDict(env_prefix="MAINPLATE_", frozen=True)

    database: Path = DEFAULT_DATABASE
    """The SQLite file holding every session's checkpoint, its queue, and the session index."""

    config_home: Path = Field(default_factory=lambda: config_home(os.environ))
    """
    Where to look for `mainplate/config.yaml`, which is where the endpoints are.

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

    reference_every: timedelta = Field(default=timedelta(hours=12), gt=timedelta())
    """
    How often to re-read the model reference database, where one is configured.

    Hours where `refresh` is minutes, because the two are watching different things change. A model
    appears at a gateway when somebody attaches an integration, which is a thing that happens while
    you are looking at the console; a price moves or a record is filled in when somebody upstream
    ships a release, which is not. Re-reading a four-megabyte document every fifteen minutes would
    spend real bandwidth re-learning a value that changes a few times a month.
    """

    watching: timedelta = Field(default=DEFAULT_WATCHING, gt=timedelta())
    """
    How often a page's live connection asks whether its session has recorded anything new.

    The whole of what decides how soon a reader sees a reply take shape, because everything else on
    that path is already immediate: a step is recorded the moment it happens and the page morphs
    whatever arrives. Two hundred milliseconds is under what a person reads as a delay and well
    above what the check costs, which is a count over one session's rows and no decoding at all.

    It bounds the *staleness* rather than the work: a connection that finds nothing new sends
    nothing, so a quiet console with ten tabs open makes fifty of those counts a second and no
    renders and no bytes.
    """

    @property
    def workspace_root(self) -> Path:
        """
        Where clones and worktrees go, which is what was named or a directory beside the database.

        Beside the database because the two are the halves of one session: the checkpoint says what
        was said and the worktree holds what it was said about, so a console pointed at another
        database gets its own worktrees rather than sharing the first one's.

        **Absolute, always.** A relative path here is not a place, it is a place *plus* whatever
        directory the process happens to be in, and everything below this runs `git` with a `cwd` of
        its own choosing: a clone is made from the clones root, a worktree is added from the
        repository. Handing either a relative destination means git resolves it under that `cwd`
        rather than under this root, so the clone lands at `workspaces/clones/workspaces/clones/...`
        and the worktree lands inside the repository. Worse, the checks that make both operations
        idempotent then look at the path that was *asked for*, never find it, and every pass tries
        again - which is a `SnapshotFailed` on a session's second turn.

        Resolved here rather than in each of those callers because this is where a configured path
        enters the process, and one absolute value cannot be got wrong by the next consumer. The
        default is relative to begin with (`mainplate.db` in the working directory is what a fresh
        checkout gets), so this is the common case rather than the odd one.
        """
        named = self.workspaces if self.workspaces is not None else self.database.parent / "workspaces"
        return named.resolve()

    patience: timedelta = Field(default=DEFAULT_PATIENCE, gt=timedelta())
    """
    How long a command a person runs may take before it is killed.

    Bounded because nothing else bounds it: a command that never returns would hold its panel open
    until the console restarted, and the process tree under it would keep the worktree busy. Ten
    minutes, because the commands worth typing here are builds and test suites rather than one-liners
    and the alternative to waiting is running the thing in a terminal instead.

    Not the agent's: `bash` has its own bound inside the sandbox, and the two are answering different
    questions. That one is how long a *model* may sit on a call before the turn moves on; this is how
    long somebody is willing to watch their own command.
    """

    lease: timedelta = Field(default=timedelta(minutes=10), gt=timedelta())
    """
    How long a pass may take before another worker may take the session over.

    It has to exceed the longest a turn can honestly run, and nothing can work that out for you:
    set it too short and a slow model call is fenced mid-flight and re-run, too long and a
    crashed process leaves its session waiting that long. Ten minutes is a generous bound on one
    model call and short enough to be worth waiting out after a crash.
    """
