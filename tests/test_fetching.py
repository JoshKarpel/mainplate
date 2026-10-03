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
from without_durability.interfaces import claimed

from mainplate.fetching import due
from mainplate.fetching import fetched
from mainplate.fetching import working_in
from mainplate.forge import Fetched
from mainplate.forge import Fetches
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.service import Service
from mainplate.sessions import Session
from mainplate.snapshots import branch_named

CREATED = datetime(2031, 3, 14, 15, 9, tzinfo=UTC)
EVERY = timedelta(minutes=5)
HELD_EVERY = timedelta(seconds=15)


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


class TestWhichRepositoriesAreDue:
    """
    Which interval a repository is on, and whether it has run out.

    Ages either side of both intervals, so a repository on the wrong one is due where it should not
    be or not due where it should.
    """

    @pytest.mark.parametrize(
        ("held", "ago", "is_due"),
        [
            pytest.param(frozenset(), timedelta(seconds=40), False, id="idle, fetched under five minutes ago"),
            pytest.param(frozenset(), timedelta(minutes=6), True, id="idle, fetched over five minutes ago"),
            pytest.param(frozenset({"b"}), timedelta(seconds=10), False, id="held, fetched under fifteen seconds ago"),
            pytest.param(frozenset({"b"}), timedelta(seconds=40), True, id="held, fetched over fifteen seconds ago"),
        ],
    )
    def test_a_repository_is_due_once_its_interval_has_passed(
        self, held: frozenset[str], ago: timedelta, is_due: bool
    ) -> None:
        sessions = (one("a", "exe:blog"), one("b", "exe:blog"))
        fetches = {"exe:blog": Fetched(at=CREATED - ago)}

        assert due(sessions, held, fetches, CREATED, EVERY, HELD_EVERY) == ({"exe:blog"} if is_due else set())

    def test_a_repository_never_fetched_since_the_console_started_is_due_at_once(self) -> None:
        assert due((one("a", "exe:blog"),), frozenset(), {}, CREATED, EVERY, HELD_EVERY) == {"exe:blog"}

    def test_a_failed_fetch_waits_out_its_interval_like_one_that_worked(self) -> None:
        fetches = {"exe:blog": Fetched(at=CREATED - timedelta(seconds=40), failed="HTTP 502")}

        assert due((one("a", "exe:blog"),), frozenset(), fetches, CREATED, EVERY, HELD_EVERY) == frozenset()

    def test_each_repository_is_on_its_own_interval(self) -> None:
        sessions = (one("a", "exe:blog"), one("b", "exe:tools"))
        fetches = {name: Fetched(at=CREATED - timedelta(seconds=40)) for name in ("exe:blog", "exe:tools")}

        assert due(sessions, frozenset({"b"}), fetches, CREATED, EVERY, HELD_EVERY) == {"exe:tools"}

    def test_an_archived_session_still_held_does_not_hurry_its_repository(self) -> None:
        """Its pass is finishing; the session nobody is working in beside it is what is left to read."""
        sessions = (one("a", "exe:blog", archived=CREATED), one("b", "exe:blog"))
        fetches = {"exe:blog": Fetched(at=CREATED - timedelta(seconds=40))}

        assert due(sessions, frozenset({"a"}), fetches, CREATED, EVERY, HELD_EVERY) == frozenset()


async def moved_on(origin: Path) -> str:
    """A commit on the origin's `main` that no clone has seen, and its hash."""
    (origin / "src" / "kept.txt").write_text("moved on at the remote\n")
    await run("git", "commit", "-aqm", "elsewhere", cwd=origin)
    return await run("git", "rev-parse", "HEAD", cwd=origin)


def starting(workspaces: Workspaces) -> Workspaces:
    """The same workspaces on a console that has fetched nothing since it started, so every repository is due."""
    return replace(workspaces, fetches=Fetches())


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

    @pytest.mark.parametrize(
        "fetching",
        [
            pytest.param(("fetch", "--quiet", "origin"), id="fetching everything"),
            pytest.param(("fetch", "--quiet", "origin", "main"), id="fetching main by name"),
        ],
    )
    async def test_a_live_session_s_fetch_sees_what_the_remote_has_now(
        self, service: Service, workspaces: Workspaces, origin: Path, working: str, fetching: tuple[str, ...]
    ) -> None:
        """
        By name too, which is what a model asked to merge the latest `main` types. A store that kept the
        clone's branches and fetched the remote's beside them answered that one with the clone's `main`.
        """
        now = await moved_on(origin)
        checkout = workspaces.checkout(working, FIXTURE)
        await checkout.demand(*fetching)
        stale = await checkout.demand("rev-parse", "refs/remotes/origin/main")
        assert stale != now, "the control: planting fetched before the origin moved"

        await fetched(service, starting(workspaces), EVERY, HELD_EVERY)

        await checkout.demand(*fetching)
        assert await checkout.demand("rev-parse", "refs/remotes/origin/main") == now

    @pytest.mark.parametrize(
        ("holding", "is_fetched"),
        [
            pytest.param(True, True, id="while a pass holds the session"),
            pytest.param(False, False, id="while nothing does"),
        ],
    )
    async def test_a_repository_somebody_is_working_in_is_fetched_sooner(
        self, service: Service, workspaces: Workspaces, origin: Path, working: str, holding: bool, is_fetched: bool
    ) -> None:
        """Forty seconds after the last fetch, which is past the held interval and well short of the idle one."""
        store = workspaces.clones.store(FIXTURE)
        before = await store.demand("rev-parse", "refs/heads/main")
        now = await moved_on(origin)
        lately = replace(
            workspaces,
            fetches=Fetches(current={FIXTURE: Fetched(at=CREATED)}),
            clock=lambda: CREATED + timedelta(seconds=40),
        )
        holder = await claimed(service.checkpointer, working) if holding else None
        try:
            await fetched(service, lately, EVERY, HELD_EVERY)
        finally:
            if holder is not None:
                await service.checkpointer.release(holder)

        assert await store.demand("rev-parse", "refs/heads/main") == (now if is_fetched else before)

    async def test_a_branch_the_remote_deleted_leaves_the_store(
        self, service: Service, workspaces: Workspaces, origin: Path, working: str
    ) -> None:
        """
        The cost of mirroring, pinned so it is a decision rather than a surprise: the store keeps no
        branch name the remote has dropped. Its commits stay, since the store never prunes.
        """
        await run("git", "branch", "short-lived", cwd=origin)
        await fetched(service, starting(workspaces), EVERY, HELD_EVERY)
        store = workspaces.clones.store(FIXTURE)
        assert await store.commit_at("refs/heads/short-lived") is not None, "the control: the branch was fetched"
        await run("git", "branch", "-D", "-q", "short-lived", cwd=origin)

        await fetched(service, starting(workspaces), EVERY, HELD_EVERY)

        assert await store.commit_at("refs/heads/short-lived") is None

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

        await fetched(service, starting(workspaces), EVERY, HELD_EVERY)

        assert await store.demand("config", "gc.pruneExpire") == "never"

    async def test_a_fetch_that_worked_is_held_as_when_it_happened(
        self, service: Service, workspaces: Workspaces, working: str
    ) -> None:
        later = CREATED + timedelta(minutes=7)
        timed = replace(starting(workspaces), clock=lambda: later)

        await fetched(service, timed, EVERY, HELD_EVERY)

        assert timed.fetches.current[FIXTURE] == Fetched(at=later)

    async def test_a_fetch_that_failed_is_held_with_what_git_said(
        self, service: Service, workspaces: Workspaces, working: str
    ) -> None:
        """The store keeps the refs it last fetched, so this is a note on a card and not a stopped session."""
        gone = replace(
            starting(workspaces),
            reaching=Reaching(
                current=Reachable(
                    repositories=(Repository(forge="test", key="fixture", name="me/fixture", url="/nowhere/at/all"),)
                )
            ),
        )

        await fetched(service, gone, EVERY, HELD_EVERY)

        held = gone.fetches.current[FIXTURE]
        assert held.failed is not None
        assert "/nowhere/at/all" in held.failed

    async def test_a_repository_only_archived_sessions_work_in_is_left_alone(
        self, service: Service, workspaces: Workspaces, origin: Path, working: str
    ) -> None:
        store = workspaces.clones.store(FIXTURE)
        before = await store.demand("rev-parse", "refs/heads/main")
        await service.archive(working)
        await moved_on(origin)

        await fetched(service, starting(workspaces), EVERY, HELD_EVERY)

        assert await store.demand("rev-parse", "refs/heads/main") == before

    async def test_a_repository_nobody_has_cloned_is_not_cloned_by_a_round(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """The first pass clones, because that is where slow work lives; a round only fetches."""
        await started(service, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))

        await fetched(service, starting(workspaces), EVERY, HELD_EVERY)

        assert not workspaces.clones.cloned(FIXTURE)
