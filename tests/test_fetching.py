from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import run
from conftest import started

from mainplate.fetching import fetched
from mainplate.fetching import working_in
from mainplate.forge import Fetched
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.service import Service
from mainplate.sessions import Session
from mainplate.snapshots import branch_named

CREATED = datetime(2031, 3, 14, 15, 9, tzinfo=UTC)


def one(identifier: str, repository: str | None, archived: datetime | None = None) -> Session:
    return Session(id=identifier, created_at=CREATED, title=identifier, repository=repository, archived=archived)


class TestWhichRepositoriesAreWorthFetching:
    def test_each_repository_a_live_session_works_in_is_named_once(self) -> None:
        sessions = (one("a", "exe:blog"), one("b", "exe:blog"), one("c", "exe:tools"))

        assert working_in(sessions) == {"exe:blog", "exe:tools"}

    def test_a_session_working_in_nothing_names_nothing(self) -> None:
        assert working_in((one("a", None),)) == frozenset()

    def test_an_archived_session_s_repository_is_not_worth_fetching(self) -> None:
        sessions = (one("a", "exe:blog", archived=CREATED), one("b", "exe:tools"))

        assert working_in(sessions) == {"exe:tools"}


async def moved_on(origin: Path) -> str:
    """A commit on the origin's `main` that no clone has seen, and its hash."""
    (origin / "src" / "kept.txt").write_text("moved on at the remote\n")
    await run("git", "commit", "-aqm", "elsewhere", cwd=origin)
    return await run("git", "rev-parse", "HEAD", cwd=origin)


class TestARound:
    """
    What a session's own `git fetch` sees, which reads the store rather than the forge.

    The store is the thing refreshed, and the session's checkout is where the refresh has to show up,
    so the assertion is made from inside a checkout rather than against the store's refs alone.
    """

    @pytest.fixture
    async def working(self, service: Service, workspaces: Workspaces) -> str:
        session = await started(service, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await workspaces.plant(session.id, FIXTURE, branch=branch_named(session.id))
        return session.id

    async def test_a_live_session_s_fetch_sees_what_the_remote_has_now(
        self, service: Service, workspaces: Workspaces, origin: Path, working: str
    ) -> None:
        now = await moved_on(origin)
        checkout = workspaces.checkout(working, FIXTURE)
        await checkout.demand("fetch", "--quiet", "origin")
        stale = await checkout.demand("rev-parse", "refs/remotes/origin/main")
        assert stale != now, "the control: planting fetched before the origin moved"

        await fetched(workspaces, service.database)

        await checkout.demand("fetch", "--quiet", "origin")
        assert await checkout.demand("rev-parse", "refs/remotes/origin/main") == now

    async def test_a_store_cloned_without_the_setting_never_prunes_once_it_is_fetched(
        self, service: Service, workspaces: Workspaces, working: str
    ) -> None:
        """
        A checkout borrows the store's objects without the store knowing which, and a fetch that
        drops a deleted branch's ref then runs git's own `gc`, so a store that may prune can take a
        commit only a checkout still refers to. Set at the fetch, so a store that exists already -
        cloned before the setting was written, which a store just cloned stands in for here - gets
        it the first time it could need it.
        """
        store = workspaces.clones.store(FIXTURE)
        assert not (await store.git("config", "gc.pruneExpire")).ok, "the control: cloning set nothing"

        await fetched(workspaces, service.database)

        assert await store.demand("config", "gc.pruneExpire") == "never"

    async def test_a_fetch_that_worked_is_held_as_when_it_happened(
        self, service: Service, workspaces: Workspaces, working: str
    ) -> None:
        later = CREATED + timedelta(minutes=7)
        timed = replace(workspaces, clock=lambda: later)

        await fetched(timed, service.database)

        assert timed.fetches.current[FIXTURE] == Fetched(at=later)

    async def test_a_fetch_that_failed_is_held_with_what_git_said(
        self, service: Service, workspaces: Workspaces, working: str
    ) -> None:
        """The store keeps the refs it last fetched, so this is a note on a card and not a stopped session."""
        gone = replace(
            workspaces,
            reaching=Reaching(
                current=Reachable(
                    repositories=(Repository(forge="test", key="fixture", name="me/fixture", url="/nowhere/at/all"),)
                )
            ),
        )

        await fetched(gone, service.database)

        held = gone.fetches.current[FIXTURE]
        assert held.failed is not None
        assert "/nowhere/at/all" in held.failed

    async def test_a_repository_only_archived_sessions_work_in_is_left_alone(
        self, service: Service, workspaces: Workspaces, origin: Path, working: str
    ) -> None:
        store = workspaces.clones.store(FIXTURE)
        before = await store.demand("rev-parse", "refs/remotes/origin/main")
        await service.archive(working)
        await moved_on(origin)

        await fetched(workspaces, service.database)

        assert await store.demand("rev-parse", "refs/remotes/origin/main") == before

    async def test_a_repository_nobody_has_cloned_is_not_cloned_by_a_round(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """The first pass clones, because that is where slow work lives; a round only fetches."""
        await started(service, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))

        await fetched(workspaces, service.database)

        assert not workspaces.clones.cloned(FIXTURE)
