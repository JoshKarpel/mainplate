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
# What it refuses to do is take a checkout from under a pass. A pass reads the checkpoint at its top
# and never sees an archive written after, so a session the worker holds is left for the next round;
# the pass that follows reads the key and stops. A command a person is still running is the same case
# from the other side. Both are read off live state rather than recorded, since both are true only at
# the instant they are read.
#
# Deleting is the same loop one step further, on the same diff: a session somebody pressed delete on
# wants nothing on the disk *and* nothing in the database. Its files come off first, exactly as an
# archived session's do, and only a session holding nothing and held by nothing is taken out, so the
# order the press could not wait for is the order the round keeps.

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from collections.abc import Callable
from datetime import datetime
from datetime import timedelta

from mainplate.conversation import ARCHIVED_TREE_KEY
from mainplate.durability import recorded_snapshot
from mainplate.footprint import Footprints
from mainplate.footprint import Places
from mainplate.footprint import measured
from mainplate.service import Service
from mainplate.sessions import Footprint
from mainplate.sessions import Session
from mainplate.sessions import now_utc
from mainplate.sessions import unenrol
from mainplate.snapshots import SnapshotFailed

logger = logging.getLogger(__name__)


def holding(places: Places, session: Session) -> bool:
    """Whether any of this session's directories is still on the disk, which is what there is to do."""
    return any(place.is_symlink() or place.exists() for place in places.of(session.id, session.repository))


async def taken_off(service: Service, places: Places, session: Session) -> None:
    """
    Every directory that is this session's, off the disk, with the checkout's last state recorded first.

    The checkout's last state is captured first, under `archived:tree`, so a fork from the end of
    this session plants at the commit, the branch and the files it actually ended with; the capture
    puts the tree and the commit in the store, and the store is not the session's, so both outlive
    the checkout.

    **A capture that fails is logged and the files go anyway.** The usual reason is a session that
    broke its own `.git`, which no later round would fix, so retrying would keep its files for ever.
    Without the key, a fork from the end plants at the newest tree a turn recorded, and what changed
    after that is lost with the checkout.

    Then everything `Places.of` names that is still there, which is the checkout, the scratch and
    the plugins' scratches.
    """
    workspaces = places.workspaces
    if session.repository is not None and workspaces.clones.cloned(session.repository):
        checkouts = workspaces.checkouts(session.repository)
        if checkouts.planted(session.id):
            try:
                ending = await checkouts.checkout(session.id).capture(f"archived {session.id}")
            except SnapshotFailed as uncaptured:
                logger.warning(
                    f"archiving {session.id} without its last tree, which could not be captured: {uncaptured}"
                )
            else:
                await service.checkpointer.supply(session.id, ARCHIVED_TREE_KEY, recorded_snapshot(ending))
            await checkouts.uproot(session.id)
    for place in places.of(session.id, session.repository):
        if place.is_symlink():
            await asyncio.to_thread(place.unlink)
        elif place.exists():
            await asyncio.to_thread(shutil.rmtree, place)


async def taken_out(service: Service, session: str) -> None:
    """
    The session's rows out of the database: its checkpoint and its queue, then its row in the index.

    **Checkpoint first and row last**, because the row is what says the session was deleted, and a
    crash between the two has to leave a round something to find. The checkpoint goes in one commit
    with the session's wakeups and a raised fence, so no pass can write a record back after it; a row
    left behind with nothing under it is still on `read_deleted`, its directories are already gone,
    and the next round's `durable.delete` finds nothing and the `unenrol` after it finishes.

    **Its claim stays**, superseded rather than removed, because the claim row *is* the fence: the
    store raises its token and keeps it, so a pass that comes back holding an older one is refused at
    its next write. Removing it would take that ordering away and is the store's to decide, not this
    console's. The cost, stated: one small row per deleted session that a pass ever took, read by
    every `attending` and never by anything that draws it.

    The session's artifacts and its snapshot refs in the store are not its rows and stay, which is
    `Service.delete`'s to say.
    """
    await service.durable.delete(session)
    await unenrol(service.database, session)


async def reconciled(
    service: Service, places: Places, footprints: Footprints, now: Callable[[], datetime] = now_utc
) -> None:
    """
    One round: every archived session still holding directories, taken off the disk where nothing
    holds it, and every deleted one holding none taken out of the database.

    A session that will not come off the disk or out of the database is logged and left for the next
    round, which is the retry a reconciler gets for free: nothing here is recorded, so nothing has to
    be undone. The figure on its row is re-measured afterwards rather than left for the next sweep,
    since the whole point of the press was to get the space back and a row saying otherwise for five
    minutes reads as the press having failed.

    A deleted session is on neither list a page reads, so it is read on its own and goes through the
    same first loop: pressed a moment after archiving, its files may still be on the disk. Its last
    tree is captured on the way out like any archived session's, which a deleted session will never
    fork from; one path rather than a second that skips the capture, at the cost of a capture nobody
    reads.
    """
    deleted = await service.deleted()
    for session in (*await service.listed(), *deleted):
        if session.archived is None or not holding(places, session):
            continue
        if await service.held(session.id):
            logger.info(f"{session.id} is archived and still held, so its files stay until the next round")
            continue
        try:
            await taken_off(service, places, session)
        except (OSError, SnapshotFailed, sqlite3.Error) as unremoved:
            logger.warning(f"could not take archived session {session.id} off the disk: {unremoved!r}")
            continue
        logger.info(f"{session.id} is archived and its files are off the disk")
        allocated = await asyncio.to_thread(measured, places.of(session.id, session.repository))
        footprints.current = {**footprints.current, session.id: Footprint(allocated=allocated, measured_at=now())}
    for session in deleted:
        # Not `session.archived`: a round that fell over between the two halves of `taken_out` left a
        # row whose checkpoint, and so whose archive key, is already gone.
        if holding(places, session) or await service.held(session.id):
            continue
        # A write lock another process holds past the busy timeout is the case: left for the next
        # round like a session whose files would not come off, since an error out of here would end
        # the loop for every session until the console restarts.
        try:
            await taken_out(service, session.id)
        except sqlite3.Error as unremoved:
            logger.warning(f"could not take deleted session {session.id} out of the database: {unremoved!r}")
            continue
        logger.info(f"{session.id} is deleted and out of the database")
        footprints.current = {each: footprint for each, footprint in footprints.current.items() if each != session.id}


async def reconciling(service: Service, places: Places, footprints: Footprints, every: timedelta) -> None:
    """Round after round, for as long as this is running. First at once, since a press may already be waiting."""
    while True:
        await reconciled(service, places, footprints)
        await asyncio.sleep(every.total_seconds())
