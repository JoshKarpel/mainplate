# Taking an archived session off the disk, which is the half of archiving the press does not do.
#
# The press writes one key and redirects; this loop reads that key off every row and makes the disk
# agree with it. It is a reconciler rather than a job the press queues, deliberately: what it does is
# diff the desired state (an archived session holds no directories) against the actual one (what
# `Places.of` names exists or does not) and converge, so a console that died halfway through, or was
# pressed while a pass still held the session, finishes on its next round with nothing to be told.
# The cost is one index read per interval on a console with nothing to do, and a session pressed
# archived keeping its files for up to `archive_every`.
#
# What it refuses to do is take a worktree from under a pass. A pass reads the checkpoint at its top
# and never sees an archive written after, so a session the worker holds is left for the next round;
# the pass that follows reads the key and stops. A command a person is still running is the same case
# from the other side. Both are read off live state rather than recorded, since both are true only at
# the instant they are read.

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from datetime import datetime
from datetime import timedelta

from mainplate import records
from mainplate.conversation import ARCHIVED_TREE_KEY
from mainplate.footprint import Footprints
from mainplate.footprint import Places
from mainplate.footprint import measured
from mainplate.service import Service
from mainplate.sessions import Footprint
from mainplate.sessions import Session
from mainplate.sessions import now_utc
from mainplate.snapshots import SnapshotFailed

logger = logging.getLogger(__name__)


def holding(places: Places, session: Session) -> bool:
    """Whether any of this session's directories is still on the disk, which is what there is to do."""
    return any(place.is_symlink() or place.exists() for place in places.of(session.id, session.repository))


async def taken_off(service: Service, places: Places, session: Session) -> None:
    """
    Every directory that is this session's, off the disk, with the worktree's last tree recorded first.

    The worktree goes through git rather than `rmtree`, because git keeps its own directory for a
    linked worktree inside the clone and its own list of them: a tree deleted behind its back is one
    `git worktree list` names for ever and `Worktrees.plant` refuses to reuse. `uproot` is that call,
    and it copes with a directory already gone. The tree is captured just before, under
    `archived:tree`, so a fork from the end of this session plants at the files it actually ended
    with; the snapshots themselves live in the clone's object store and outlive the worktree.

    Then everything `Places.of` names that is still there, which is the scratch, the plugins' scratches
    and, on a console whose clone has gone, the worktree itself.
    """
    workspaces = places.workspaces
    if session.repository is not None and workspaces.clones.cloned(session.repository):
        worktrees = workspaces.clones.worktrees(session.repository, workspaces.root)
        if worktrees.at(session.id) in await worktrees.planted():
            ending = await worktrees.worktree(session.id).capture(f"archived {session.id}")
            await service.checkpointer.supply(session.id, ARCHIVED_TREE_KEY, records.Tree(tree=ending).recorded())
            await worktrees.uproot(session.id)
    for place in places.of(session.id, session.repository):
        if place.is_symlink():
            await asyncio.to_thread(place.unlink)
        elif place.exists():
            await asyncio.to_thread(shutil.rmtree, place)


async def reconciled(
    service: Service, places: Places, footprints: Footprints, now: Callable[[], datetime] = now_utc
) -> None:
    """
    One round: every archived session still holding directories, taken off the disk where nothing holds it.

    A session that will not come off is logged and left for the next round, which is the retry a
    reconciler gets for free: nothing here is recorded, so nothing has to be undone. The figure on its
    row is re-measured afterwards rather than left for the next sweep, since the whole point of the
    press was to get the space back and a row saying otherwise for five minutes reads as the press
    having failed.
    """
    for session in await service.listed():
        if session.archived is None or not holding(places, session):
            continue
        if await service.held(session.id):
            logger.info(f"{session.id} is archived and still held, so its files stay until the next round")
            continue
        try:
            await taken_off(service, places, session)
        except (OSError, SnapshotFailed) as unremoved:
            logger.warning(f"could not take archived session {session.id} off the disk: {unremoved!r}")
            continue
        logger.info(f"{session.id} is archived and its files are off the disk")
        allocated = await asyncio.to_thread(measured, places.of(session.id, session.repository))
        footprints.current = {**footprints.current, session.id: Footprint(allocated=allocated, measured_at=now())}


async def reconciling(service: Service, places: Places, footprints: Footprints, every: timedelta) -> None:
    """Round after round, for as long as this is running. First at once, since a press may already be waiting."""
    while True:
        await reconciled(service, places, footprints)
        await asyncio.sleep(every.total_seconds())
