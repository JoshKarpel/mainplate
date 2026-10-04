# Rewriting the database file, once a day, at a moment nothing is working.
#
# `VACUUM` is what gives the file back what deleting a session freed, and what nothing else does:
# SQLite reuses a freed page for the next write but never returns it, and it never undoes the
# fragmentation of a table appended to one row at a time for months. Rewriting is the whole of the
# remedy and it is a whole-file rewrite, so it is control-plane work on a timer, deliberately never
# a consequence of a request or a press.
#
# **It goes through the console's own connection, and that is the decision this module rests on.**
# Every statement this process makes queues on `Database.run`'s guard, so for the seconds a vacuum
# takes, a page, a stream's poll and a pass's write all wait their turn and then succeed. On a second
# connection the same vacuum leaves readers alone and fails writers instead: measured against a copy
# of a real 389 MB database it took 8.2 seconds, and a write from another connection gave up at the
# store's five-second busy timeout with `database is locked`. A stall is the better of the two,
# because a pass's record that waits is a record and one that fails is a pass redelivered and a press
# that answers with an error. The cost, stated: the whole console holds still while it runs, and a
# second process sharing the file, which is not the deployment this is for, still sees the failure
# whenever the vacuum outlasts its busy timeout.
#
# Which is why it waits for a quiet moment rather than firing on the hour: no pass holding a session,
# none queued to, and no command a person is running. A session deferred until a provider's minute is
# not work in flight and does not hold it off, since it may wait for hours.

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Final

from without_durability_sqlite import Database

from mainplate.service import Service
from mainplate.sessions import Queued
from mainplate.sessions import now_utc

logger = logging.getLogger(__name__)

# One row, which is when a vacuum was last attempted. Recorded in the file rather than kept in the
# process, because a console restarted more often than the interval - which is every console its own
# sessions are working on - would otherwise never be due.
SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS vacuumed (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    at TEXT NOT NULL
) STRICT;
"""

# The `CHECK` is what keeps it one row, so this replaces the attempt before it rather than adding one.
ATTEMPTED: Final = "INSERT OR REPLACE INTO vacuumed (id, at) VALUES (0, ?)"

# How often a vacuum that is due looks for a quiet moment. A minute, for `archive_every`'s reason: it
# is short against how long somebody leaves a console alone and long against one indexed read.
LOOK: Final = timedelta(minutes=1)


async def prepare(database: Database) -> None:
    """Create the table beside the session index's and the artifacts', idempotently, every boot."""
    await database.run(lambda connection: connection.executescript(SCHEMA))


async def last_vacuumed(database: Database) -> datetime | None:
    """When a vacuum was last attempted on this file, or nothing where none ever has been."""

    def query(connection: sqlite3.Connection) -> datetime | None:
        row = connection.execute("SELECT at FROM vacuumed").fetchone()
        return None if row is None else datetime.fromisoformat(str(row[0]))

    return await database.run(query)


async def quiet(service: Service) -> bool:
    """
    Whether nothing is working right now: no pass holding a session, none queued, no command running.

    One reading of `attending` for both questions, so a session handed from the queue to a pass
    between two readings cannot be missed by both.
    """
    attending = await service.attending()
    if service.holding_from(attending):
        return False
    return not any(isinstance(attention, Queued) for attention in attending.values())


@dataclass(frozen=True, slots=True)
class Vacuumed:
    """
    What one vacuum came to: the file's size before and after, and whether the log was given back.

    The sizes are the file's alone, since `page_count` does not see the log, which is why `truncated`
    is a field rather than something the sizes could be read for: a log left full is a second copy of
    the database that neither figure includes.
    """

    before: int
    after: int
    truncated: bool


def vacuumed(connection: sqlite3.Connection, at: datetime) -> Vacuumed:
    """
    Rewrite the file, give the WAL's copy of it back, and record the attempt.

    The checkpoint is half the job rather than tidying. Under WAL a vacuum writes the whole new file
    into the log first, so without `TRUNCATE` the disk holds a second copy of the database until
    some later checkpoint happens to reset the log, which SQLite does not shrink on its own.

    **A checkpoint that cannot finish does not raise**: another connection still reading the old
    file, the `sqlite3` shell or a backup, is a busy checkpoint, and SQLite says so in the first
    column it returns and leaves the log as it was. So the column is read rather than trusted to an
    exception, and the next checkpoint after that reader lets go gives the log back.
    """
    before = file_size(connection)
    # The attempt first, so the checkpoint below is the last write and the log is left empty.
    connection.execute(ATTEMPTED, (at.isoformat(),))
    connection.execute("VACUUM")
    (busy, _, _) = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return Vacuumed(before=before, after=file_size(connection), truncated=busy == 0)


def file_size(connection: sqlite3.Connection) -> int:
    """How large the database file is, in bytes, as SQLite counts it."""
    (pages,) = connection.execute("PRAGMA page_count").fetchone()
    (size,) = connection.execute("PRAGMA page_size").fetchone()
    return int(pages) * int(size)


async def vacuumed_if_due(service: Service, every: timedelta, now: Callable[[], datetime] = now_utc) -> bool:
    """
    One look: a vacuum, where the last attempt was `every` ago or there never was one and nothing is working.

    **A vacuum that fails is recorded as attempted**, and so is not tried again for a day. What fails
    one is ordinarily a disk without room for the second copy it writes, which a minute does not fix;
    retried every look, it would write the whole file once a minute into a disk that is already full.
    The cost, stated: a failure that a minute *would* fix waits a day, and says so in the log.

    Whether it vacuumed, for the caller and the tests; nothing reads it as a record.
    """
    last = await last_vacuumed(service.database)
    at = now()
    if last is not None and at - last < every:
        return False
    if not await quiet(service):
        return False
    started = time.monotonic()
    try:
        done = await service.database.run(lambda connection: vacuumed(connection, at))
    except sqlite3.Error as failed:
        logger.warning(f"could not vacuum the database, and will try again in {every}: {failed!r}")
        return False
    logger.info(
        f"vacuumed the database in {time.monotonic() - started:.1f}s,"
        f" from {done.before / 1e6:.0f} MB to {done.after / 1e6:.0f} MB"
    )
    if not done.truncated:
        logger.warning(
            "vacuumed the database but could not empty its write-ahead log, which another connection"
            " was still reading; it holds a second copy of the file until the next checkpoint"
        )
    return True


async def vacuuming(service: Service, every: timedelta) -> None:
    """
    Look after look, for as long as this is running. First at once, since one may already be due.

    **A look that raises is logged and the next one comes a minute later**, rather than ending the
    loop: a background task that raises stops quietly until the process exits, so one bad look would
    be a console that never vacuums again with nothing in the log to say so. Everything, rather than
    the errors a look is known to meet, for `Jobs.looked`'s reason, which is the same loop shape; the
    cost is a fault nobody anticipated logged once a minute instead of once.
    """
    while True:
        try:
            await vacuumed_if_due(service, every)
        except Exception:
            logger.exception(f"could not look for a moment to vacuum the database, and will look again in {LOOK}")
        await asyncio.sleep(LOOK.total_seconds())
