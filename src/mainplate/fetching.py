# Keeping each store's idea of its remote current while sessions are working in it.
#
# A session's `git fetch` reads the store rather than the forge, because `origin` in a worktree is
# the store: most sessions have no network, and on exe.dev being on the network is the credential, so
# the one remote a worktree names is one that needs neither. So what `origin/main` means inside a
# session is whatever the store last fetched, and planting a worktree is only one moment. A session
# rebasing onto `origin/main` an hour in wants the `main` of now, and a reviewer's commit pushed to the
# session's own branch is invisible to it until something fetches.
#
# This is the control plane's answer: a loop that fetches every repository some unarchived session
# works in, on a timer, and no request ever waiting on it. It runs once per console, which is once
# per machine, since the database is one SQLite file with one writer.
#
# Planting fetches as well, and the two are not a fallback for each other. Planting is the moment
# somebody asked for current code and is waiting on it; the loop is what keeps it current after. The
# cost, stated: a repository with a live session is fetched every `fetch_every` whether or not
# anything moved, which is one round trip that transfers nothing when nothing did.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import timedelta

from without_durability_sqlite import Database

from mainplate.forge import Workspaces
from mainplate.sessions import Session
from mainplate.sessions import read_sessions

logger = logging.getLogger(__name__)


def working_in(sessions: Iterable[Session]) -> frozenset[str]:
    """Every repository some session not yet archived works in, which is what is worth fetching."""
    return frozenset(
        session.repository for session in sessions if session.repository is not None and session.archived is None
    )


async def fetched(workspaces: Workspaces, database: Database) -> None:
    """
    One round: every repository a live session works in, fetched into its store, all at once.

    A repository not cloned yet is skipped, since the first pass on it clones and a store just cloned
    is current by construction; one no forge reaches is skipped, since there is nowhere to fetch it
    from. Neither is logged, because both are ordinary and would be said every round.
    """
    reached = [
        repository
        for identifier in sorted(working_in(await read_sessions(database)))
        if workspaces.clones.cloned(identifier) and (repository := workspaces.named(identifier)) is not None
    ]
    await asyncio.gather(*(workspaces.refresh(repository) for repository in reached))


async def fetching(workspaces: Workspaces, database: Database, every: timedelta) -> None:
    """
    Round after round, for as long as this is running. First at once, since a console that was down
    for a day comes back with every live session's store a day behind.
    """
    while True:
        await fetched(workspaces, database)
        await asyncio.sleep(every.total_seconds())
