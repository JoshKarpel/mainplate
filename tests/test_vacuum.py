"""Rewriting the database file once a day, at a moment nothing is working."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import DEFAULT_CHOICE
from conftest import WHEN
from conftest import started
from without_durability.interfaces import claimed

from mainplate.service import Service
from mainplate.sessions import Delayed
from mainplate.vacuum import file_size
from mainplate.vacuum import last_vacuumed
from mainplate.vacuum import vacuumed_if_due

DAY = timedelta(days=1)


async def settled(service: Service) -> str:
    """A session with something said in it and nothing left for a worker to do, so the console is quiet."""
    session = await started(service, "first", DEFAULT_CHOICE)
    await service.durable.scheduler.cancel(session.id)
    return session.id


async def size(service: Service) -> int:
    return await service.database.run(file_size)


class TestWhenAVacuumIsDue:
    async def test_a_file_never_vacuumed_is_vacuumed_at_the_first_quiet_look(self, service: Service) -> None:
        await settled(service)

        assert await vacuumed_if_due(service, DAY, now=lambda: WHEN)

        assert await last_vacuumed(service.database) == WHEN

    async def test_a_vacuum_inside_the_interval_is_not_due(self, service: Service) -> None:
        await settled(service)
        await vacuumed_if_due(service, DAY, now=lambda: WHEN)

        assert not await vacuumed_if_due(service, DAY, now=lambda: WHEN + DAY - timedelta(seconds=1))

        assert await last_vacuumed(service.database) == WHEN

    async def test_a_vacuum_the_interval_ago_is_due_again(self, service: Service) -> None:
        await settled(service)
        await vacuumed_if_due(service, DAY, now=lambda: WHEN)

        assert await vacuumed_if_due(service, DAY, now=lambda: WHEN + DAY)

        assert await last_vacuumed(service.database) == WHEN + DAY


class TestWaitingForAQuietMoment:
    async def test_a_session_queued_to_holds_it_off(self, service: Service) -> None:
        """A session started and not yet answered is exactly what a worker is about to take."""
        await started(service, "first", DEFAULT_CHOICE)

        assert not await vacuumed_if_due(service, DAY, now=lambda: WHEN)

        assert await last_vacuumed(service.database) is None, "held off, so not attempted either"

    async def test_a_pass_holding_a_session_holds_it_off(self, service: Service) -> None:
        session = await settled(service)
        holder = await claimed(service.checkpointer, session)
        try:
            assert not await vacuumed_if_due(service, DAY, now=lambda: WHEN)
        finally:
            await service.checkpointer.release(holder)

        assert await vacuumed_if_due(service, DAY, now=lambda: WHEN), "and the next look, once it lets go"

    async def test_a_session_deferred_for_later_does_not(self, service: Service) -> None:
        """A provider's minute may be hours away, and nothing is in flight until it comes."""
        session = await settled(service)
        await service.durable.scheduler.schedule(session, datetime.now(UTC) + timedelta(hours=3))
        assert isinstance((await service.attending())[session], Delayed), "the control: it is deferred"

        assert await vacuumed_if_due(service, DAY, now=lambda: WHEN)


class TestWhatAVacuumDoes:
    async def test_it_gives_back_what_deleting_a_session_freed(self, service: Service) -> None:
        """The reason it exists: freed pages are reused but never returned until the file is rewritten."""
        session = await settled(service)
        for each in range(200):
            await service.checkpointer.supply(session, f"filler:{each}", {"said": "x" * 20_000})
        await service.durable.delete(session)
        freed = await size(service)

        await vacuumed_if_due(service, DAY, now=lambda: WHEN)

        assert await size(service) < freed / 4

    async def test_it_gives_back_the_copy_it_wrote_into_the_log(self, service: Service, database: Path) -> None:
        """Under WAL a vacuum writes the whole new file into the log, which SQLite does not shrink alone."""
        session = await settled(service)
        for each in range(200):
            await service.checkpointer.supply(session, f"filler:{each}", {"said": "x" * 20_000})

        await vacuumed_if_due(service, DAY, now=lambda: WHEN)

        assert Path(f"{database}-wal").stat().st_size == 0

    async def test_a_log_another_connection_is_still_reading_is_said_to_be_left_full(
        self, service: Service, database: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        A busy checkpoint answers rather than raising, so nothing but its first column says the log
        still holds the second copy. No busy timeout on the console's side, so the checkpoint gives
        up at once rather than after the store's five seconds.
        """
        session = await settled(service)
        for each in range(200):
            await service.checkpointer.supply(session, f"filler:{each}", {"said": "x" * 20_000})
        await service.database.run(lambda connection: connection.execute("PRAGMA busy_timeout = 0"))
        reader = sqlite3.connect(database, isolation_level=None)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM workflow_checkpoint").fetchone()

            with caplog.at_level(logging.WARNING, logger="mainplate.vacuum"):
                assert await vacuumed_if_due(service, DAY, now=lambda: WHEN)
        finally:
            reader.close()

        assert Path(f"{database}-wal").stat().st_size > 0, "the control: the log really was left full"
        assert "could not empty its write-ahead log" in caplog.text

    async def test_a_vacuum_that_fails_is_recorded_as_attempted_and_not_retried_for_a_day(
        self, service: Service
    ) -> None:
        """
        An open transaction is a vacuum SQLite refuses, which stands in for a disk without room; the
        attempt is written inside that transaction, which this connection still reads.
        """
        await settled(service)
        await service.database.run(lambda connection: connection.execute("BEGIN"))
        try:
            assert not await vacuumed_if_due(service, DAY, now=lambda: WHEN)
            assert await last_vacuumed(service.database) == WHEN
            assert not await vacuumed_if_due(service, DAY, now=lambda: WHEN + timedelta(minutes=1))
        finally:
            await service.database.run(lambda connection: connection.execute("ROLLBACK"))
