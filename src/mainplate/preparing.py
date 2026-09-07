# Getting a repository ready to work in, which is running the script it carries for that, once.
#
# **The console ships the mechanism and the repository ships the content.** A repository writing
# `uv sync` in a shell script at `.mainplate/setup` never has to learn the plugin protocol to get
# the one moment with a network and the one place a session's commands can reach, and it generalises
# past Python without changes: `npm ci`, `cargo fetch`, `go mod download` all want the same moment
# and the same place.
#
# **Built in rather than a bundled plugin, and that was tried the other way first.** A plugin that
# runs a repository's script has to be confined like a repository's, with the *session's* scratch as
# `$HOME` unlike any repository's, left off sessions with no worktree, registered by tier rather than
# by confinement, and known to the console by name - five exceptions for one plugin. What the
# console owns instead is one function: run the script behind the namespace `bash` gets, with a
# network, with the session's scratch as `$HOME`, and hand back what it asked to have set.
#
# **What crosses back is a file, and only what the script writes to it.** `MAINPLATE_ENV` names a
# file the script appends `KEY=value` lines to, which is GitHub Actions' `GITHUB_ENV`, and it is an
# allowlist by construction: a `PATH` with a shims directory on the front crosses because the script
# said so, and everything else in the setup environment stays put. The parent reads that file as a
# value, never as a program, which is the line `docs/design/security.md` draws.

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Final

from mainplate.plugins.running import SETTING_UP
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.snapshots import Worktree

logger = logging.getLogger(__name__)

SCRIPT: Final = Path(".mainplate") / "setup"
"""
Where a repository says how to get itself ready, relative to the worktree root.

Beside `mainplate.yaml` in the directory a repository already has for what it says to this console,
and a script rather than a field in that file, because a field would be a second thing to keep
working for ever over what a script already is.
"""

ENV_FILE_NAMED: Final = "MAINPLATE_ENV"
"""The variable naming the file the script appends `KEY=value` lines to, one per line."""

ENV_FILE: Final = ".mainplate-env"
"""
Where that file is, under the session's scratch, which is the one writable place both sides reach.

Inside the namespace the scratch is bound at its own path, so the script and the parent name the
same file the same way; removed once read, so what a session's commands later find in their `$HOME`
is what the script installed and not this console's bookkeeping.
"""

TAIL: Final = 40
"""How many lines of a failed script's output are worth putting on the settings step."""


class PreparationFailed(RuntimeError):
    """
    The repository's setup script did not get the repository ready, with the reason it did not.

    One type for every way that can go: a script that would not start, one that exited non-zero,
    one that ran past its allowance, and one that wrote something to the environment file that is
    not `KEY=value`. All four are the same thing to a reader - this repository could not be set up -
    and all four are the sentence above the switches on the settings step.
    """


def has_setup(root: Path) -> bool:
    """Whether a worktree carries a setup script, which is what the settings step draws a switch for."""
    return (root / SCRIPT).is_file()


def environment_in(text: str) -> dict[str, str]:
    """
    The `KEY=value` lines one env file holds, or a loud failure naming the line that is not one.

    Blank lines and `#` comments are passed over, since a script that echoes a heading into the file
    has done nothing wrong. Anything else that is not `KEY=value` is refused rather than skipped: a
    line meant to set `PATH` that quietly set nothing is a session whose tools are not on it,
    discovered one command at a time.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            raise PreparationFailed(f"{SCRIPT} wrote a line to {ENV_FILE_NAMED} that is not KEY=value: {line!r}")
        found[name.strip()] = value
    return found


async def prepared(worktree: Worktree, scratch: Path, bwrap: str) -> dict[str, str]:
    """
    Run the repository's setup script once, where the session's commands will find what it installs.

    **Behind the namespace `bash` gets, with the network on and the session's scratch as `$HOME`.**
    The namespace because this is the repository's code and runs where the repository's code runs;
    the network because what a setup does is fetch, and it is safe to offer here for the reason it
    is safe at a plugin's `setup`: the tree holds the commit the repository supplied, nothing the
    model wrote exists yet, and no credential of this console's is inside. The scratch as `$HOME`
    because that is what a session's own commands get as theirs, so everything the script fetches
    under it is exactly where a later `just test` looks.

    Bounded by the same allowance a plugin's setup has, and by wall clock rather than by progress,
    because nothing here can see progress. What it printed goes to the log on success and onto the
    settings step on failure, where the thing to do about it is a line away.
    """
    root = worktree.root
    await asyncio.to_thread(lambda: scratch.mkdir(parents=True, exist_ok=True))
    env_path = scratch / ENV_FILE
    await asyncio.to_thread(lambda: env_path.write_text("", encoding="utf-8"))
    sandbox = await confined_by(InAWorktree(worktree=worktree, scratch=scratch))
    argv = (
        bwrap,
        *sandbox.argv(
            at=str(root),
            venue=Venue.CONNECTED,
            home=str(scratch),
            environment={ENV_FILE_NAMED: str(env_path)},
        ),
        str(root / SCRIPT),
    )
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
        except OSError as unreachable:
            raise PreparationFailed(f"{SCRIPT} could not be run: {unreachable}") from unreachable
        try:
            async with asyncio.timeout(SETTING_UP.total_seconds()):
                out, _ = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise PreparationFailed(f"{SCRIPT} did not finish within {SETTING_UP}") from None
        said = out.decode(errors="replace")
        if process.returncode:
            shown = "\n".join(said.strip().splitlines()[-TAIL:])
            raise PreparationFailed(
                f"{SCRIPT} exited {process.returncode}:\n{shown}" if shown else f"{SCRIPT} exited {process.returncode}"
            )
        if said.strip():
            logger.info(f"{SCRIPT} said: {said.strip()}")
        return environment_in(await asyncio.to_thread(lambda: env_path.read_text(encoding="utf-8")))
    finally:
        await asyncio.to_thread(lambda: env_path.unlink(missing_ok=True))
