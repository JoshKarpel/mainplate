# What a session takes on disk: which directories are its, and how much they hold.
#
# The checkpoint is the conversation, and nothing on disk is a copy of it: a worktree is what the
# conversation is about, a scratch is what its commands fetched, and a plugin's scratch is what a
# plugin fetched. All of it is the session's, all of it is outside the store, and all of it is what
# archiving a session takes away while the checkpoint stays forkable. So the one place that knows
# where all of it is has to be the place both the figure on a row and that deletion read, or the two
# would be two lists of the same directories kept in step by hand.
#
# The figure is *measured on a timer* rather than walked at render time, and that is a cost that was
# measured rather than guessed. A warm walk over a toolchain is about a hundred milliseconds per
# thirty thousand files (`.venv` here is twenty-eight thousand; a mise directory with a few tools is
# sixteen thousand), and a session that ran `just setup` in its scratch holds both. The sidebar draws
# every session on every page, so a walk per row would put seconds of I/O on the request path of a
# console with a few working sessions. A sweep in a thread costs the same walk once per interval and
# nothing on any request, and what it buys that for is a figure as old as the interval - which the
# page says, beside the number.
#
# It is a holder, rebound and never edited, exactly as the catalogue is, and the argument for it is
# the catalogue's: a reading of the environment that changes under a reader, refreshed by a task that
# answers no requests, and not a word of anything anybody said.

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from without_durability_sqlite import Database

from mainplate.forge import Workspaces
from mainplate.sessions import Footprint
from mainplate.sessions import now_utc
from mainplate.sessions import read_sessions

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Places:
    """
    Where everything a session keeps on disk is, derived from the session and never found.

    Handed the workspaces and the plugins root together, which is the one object that sees both, and
    that is not the confusion the two were kept apart to prevent. They are apart so that a plugin's
    `$HOME` can never sit under a directory the model writes, and what decides that is which root
    each namespace is *bound*; this binds nothing. It reads what the others made, and it says so.

    Derived rather than listed, for the reason `Worktrees.gitdir` gives: a session's worktree is the
    one directory the session can write, so anything that went looking in it for where the rest is
    would be acting on the session's word. Every path here comes from the session id and the roots
    this console was started with.
    """

    workspaces: Workspaces
    plugins: Path
    """
    The root every plugin's own scratch sits under, which `Spawned.scratch_for` puts a session's
    plugins' directories under a directory named after the session. That naming is what makes the
    session's part of it one directory to measure and one to remove.
    """

    def of(self, session: str, repository: str | None) -> tuple[Path, ...]:
        """
        Every directory that is this session's and nothing else's, whether or not it exists yet.

        The worktree and git's own directory for it inside the clone, where the session works in a
        repository; the scratch and the plugins' scratches either way. Not the clone, which every
        session on that repository shares, and not the snapshots, which live in the clone's object
        store under a ref of their own and are what keeps the checkpoint forkable once the rest is
        gone.

        Git's directory is named because it is the session's: `git worktree remove` takes it with
        the tree, and it is where the session's index lives, which on a large repository is real
        space. Named through the same derivation the sandbox trusts rather than by reading the
        worktree's `.git` file, for the reason in the class note.
        """
        worked_in = (
            (
                self.workspaces.at(session),
                self.workspaces.clones.worktrees(repository, self.workspaces.root).gitdir(session),
            )
            if repository is not None
            else ()
        )
        return (*worked_in, self.workspaces.scratch_at(session), self.plugins / session)


def measured(places: Iterable[Path]) -> int:
    """
    Bytes on disk under these directories, counted the way `du` counts them.

    Allocated blocks rather than apparent size, because the question is what the disk is holding
    and what deleting would free, and the two differ on every sparse file and every small one. A
    file linked more than once is counted once across the whole set, since `uv` links a venv in the
    worktree to its cache in the scratch and both are the session's. Symbolic links are counted as
    themselves and never followed, so a link out to the machine cannot make a session look like the
    machine.

    A directory that is not there is nothing, which is the ordinary state of every one of them for a
    session that has not worked yet. An entry that vanishes between being listed and being read is
    skipped for the same reason: a session's own commands are writing while this walks, and the
    sweep is a reading of a moving thing rather than a snapshot of a still one.

    Synchronous and blocking, deliberately: it is a walk, and the caller runs it in a thread.
    """
    allocated = 0
    seen: set[tuple[int, int]] = set()
    for place in places:
        pending = [place]
        while pending:
            here = pending.pop()
            try:
                allocated += os.lstat(here).st_blocks * 512
                with os.scandir(here) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        try:
                            held = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if held.st_nlink > 1:
                            key = (held.st_dev, held.st_ino)
                            if key in seen:
                                continue
                            seen.add(key)
                        allocated += held.st_blocks * 512
            except FileNotFoundError:
                continue
    return allocated


@dataclass(slots=True)
class Footprints:
    """
    What the last sweep measured for every session it found, held so its readers can outlive it.

    Rebound and never edited, as `Catalogues` is, so a page reads one sweep's answer for every row
    and never half of one sweep and half of the next. Empty until the first sweep finishes, which
    a page draws as no figure at all; see `Session.footprint`.
    """

    current: Mapping[str, Footprint] = field(default_factory=dict)


async def swept(holder: Footprints, places: Places, database: Database, now: Callable[[], datetime] = now_utc) -> None:
    """
    Measure every session the index knows and replace what the holder holds.

    Every session the *index* knows, rather than every directory under the roots, because the
    derivation is the one thing here that says which directories are a session's and reading the
    roots would be a second one. A directory nothing in the index names is one this never counts
    and archiving never removes, and that is the cost of having one list.

    A session that cannot be read keeps what the previous sweep said about it and is logged. Kept
    rather than dropped, because dropping it would draw the row as never measured, which is a
    different state from measured and now unreadable; and logged rather than raised, because a
    background task that raises is one that stops quietly until the process exits and this is a
    figure on a row rather than anything a session needs.
    """
    measuring: dict[str, Footprint] = {}
    for session in await read_sessions(database):
        try:
            allocated = await asyncio.to_thread(measured, places.of(session.id, session.repository))
        except OSError as unread:
            logger.warning(f"could not measure what session {session.id} takes on disk: {unread!r}")
            if (kept := holder.current.get(session.id)) is not None:
                measuring[session.id] = kept
            continue
        measuring[session.id] = Footprint(allocated=allocated, measured_at=now())
    holder.current = measuring


async def measuring(holder: Footprints, places: Places, database: Database, every: timedelta) -> None:
    """
    Sweep on a timer, for as long as this is running.

    Sweeps *first*, unlike the two refreshers beside it, because nothing has filled the holder before
    this starts: the catalogue is read before the server is ready since a session cannot start
    without one, where a figure on a row is nothing a session needs and is not worth holding the
    server for. So the console is ready with no figures and has them a walk later.
    """
    while True:
        await swept(holder, places, database)
        await asyncio.sleep(every.total_seconds())
