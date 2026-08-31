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

    lease: timedelta = Field(default=timedelta(minutes=10), gt=timedelta())
    """
    How long a pass may take before another worker may take the session over.

    It has to exceed the longest a turn can honestly run, and nothing can work that out for you:
    set it too short and a slow model call is fenced mid-flight and re-run, too long and a
    crashed process leaves its session waiting that long. Ten minutes is a generous bound on one
    model call and short enough to be worth waiting out after a crash.
    """
