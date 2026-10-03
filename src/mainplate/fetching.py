# Keeping each store's idea of its remote current while sessions are working in it.
#
# A session's `git fetch` reads the store rather than the forge, because `origin` in a checkout is
# the store: most sessions have no network, and on exe.dev being on the network is the credential, so
# the one remote a checkout names is one that needs neither. So what `origin/main` means inside a
# session is whatever the store last fetched, and planting a checkout is only one moment. A session
# rebasing onto `origin/main` an hour in wants the `main` of now, and a reviewer's commit pushed to the
# session's own branch is invisible to it until something fetches.
#
# This is the control plane's answer: a loop that fetches every repository some unarchived session
# works in, on a timer, and no request ever waiting on it. It runs once per console, which is once
# per machine, since the database is one SQLite file with one writer.
#
# **Two intervals, chosen by whether anything is working in the repository.** A session a pass or a
# command holds is the one about to read `origin/main`, so its repository is fetched every
# `fetch_held_every`; one nobody is working in reads nothing, so `fetch_every` only keeps the copy
# from falling far behind between turns. Read off the same live state the reconciler reads
# (`Service.holding`), rather than anything telling this loop that work started: the worker and the
# command runner stay unaware there is a loop at all, and a pass that starts is noticed within one
# short interval rather than at once. That gap is what this costs: a session coming back to life
# after an idle stretch can run `git fetch` before the first round that sees it held, and get a copy
# up to `fetch_every` old, which is only ever missing what the forge took in those last minutes. A
# turn's first model call covers some of the gap; a command typed into an idle session covers none.
#
# Planting fetches as well, and the two are not a fallback for each other. Planting is the moment
# somebody asked for current code and is waiting on it; the loop is what keeps it current after. The
# cost, stated: a repository somebody is working in is fetched every `fetch_held_every` whether or
# not anything moved, which is one round trip that transfers nothing when nothing did.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta

from mainplate.forge import Fetched
from mainplate.forge import Workspaces
from mainplate.service import Service
from mainplate.sessions import Session
from mainplate.sessions import read_sessions

logger = logging.getLogger(__name__)


def working_in(sessions: Iterable[Session]) -> frozenset[str]:
    """Every repository some session not yet archived works in, which is what is worth fetching."""
    return frozenset(
        session.repository for session in sessions if session.repository is not None and session.archived is None
    )


def due(
    sessions: Iterable[Session],
    held: frozenset[str],
    fetches: Mapping[str, Fetched],
    now: datetime,
    every: timedelta,
    held_every: timedelta,
) -> frozenset[str]:
    """
    Every repository this round should fetch: worth fetching, and fetched longer ago than its interval.

    A repository's interval is `held_every` while any of its live sessions is in `held`, and `every`
    otherwise. One this console has not fetched since it started is due at once, since a console
    that was down for a day comes back with every store a day behind. A failed fetch counts as one,
    so a forge that is down is asked again at the same pace rather than every round.

    Pure, so which repository is fetched when is testable from sessions, a set and a clock, with no
    store and no forge anywhere near it.
    """
    live = tuple(sessions)
    working = {session.repository for session in live if session.id in held and session.archived is None}
    return frozenset(
        repository
        for repository in working_in(live)
        if (last := fetches.get(repository)) is None
        or now - last.at >= (held_every if repository in working else every)
    )


async def fetched(service: Service, workspaces: Workspaces, every: timedelta, held_every: timedelta) -> None:
    """
    One round: every repository that is `due`, fetched into its store, all at once.

    A repository not cloned yet is skipped, since the first pass on it clones and a store just cloned
    is current by construction; one no forge reaches is skipped, since there is nowhere to fetch it
    from. Neither is logged, because both are ordinary and would be said every round.
    """
    sessions = await read_sessions(service.database)
    wanted = due(sessions, await service.holding(), workspaces.fetches.current, workspaces.clock(), every, held_every)
    reached = [
        repository
        for identifier in sorted(wanted)
        if workspaces.clones.cloned(identifier) and (repository := workspaces.named(identifier)) is not None
    ]
    await asyncio.gather(*(workspaces.refresh(repository) for repository in reached))


async def fetching(service: Service, workspaces: Workspaces, every: timedelta, held_every: timedelta) -> None:
    """
    Round after round, for as long as this is running, first at once.

    Asleep for the shorter interval between rounds, counted from the end of one, so a round never
    overlaps the last: two fetches of one store at once would race for its configuration lock (see
    `Clones.refresh`). Counting from the end is also what keeps `due` exact, since every fetch in a
    round is stamped before the sleep starts.
    """
    while True:
        await fetched(service, workspaces, every, held_every)
        await asyncio.sleep(min(every, held_every).total_seconds())
