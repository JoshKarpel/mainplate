from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import WHEN
from conftest import Scripted
from conftest import already
from conftest import snapshotted
from conftest import started
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from test_console import answered
from test_console import taken
from test_conversation import pass_at
from without_asgi import ASGIApp
from without_durability.interfaces import claimed
from without_durability.stepwise import Completed

from mainplate.app import build_app
from mainplate.archive import reconciled
from mainplate.conversation import ARCHIVED_KEY
from mainplate.conversation import ARCHIVED_TREE_KEY
from mainplate.conversation import Archived
from mainplate.conversation import conversing
from mainplate.conversation import latest_tree
from mainplate.conversation import messages_key
from mainplate.conversation import opening_tree_key
from mainplate.conversation import parse_archived
from mainplate.conversation import tree_key
from mainplate.durability import parse_tree
from mainplate.footprint import Footprints
from mainplate.footprint import Places
from mainplate.forge import Workspaces
from mainplate.service import Service
from mainplate.sessions import Footprint
from mainplate.sessions import read_session
from mainplate.snapshots import Worktree


class TestArchivingASession:
    async def test_archiving_records_when_and_the_row_reads_it_back(self, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        archived = await service.archive(session.id)

        assert archived is not None
        assert archived.archived is not None
        recorded = await service.checkpointer.load(session.id)
        assert parse_archived(recorded[ARCHIVED_KEY]).at == archived.archived

    async def test_archiving_twice_is_the_first_press_twice_over(self, service: Service) -> None:
        """Write-once, so nothing un-archives a session and nothing moves the time it was archived at."""
        session = await started(service, "first", DEFAULT_CHOICE)
        first = await service.archive(session.id)
        second = await service.archive(session.id)

        assert first is not None
        assert second is not None
        assert second.archived == first.archived

    async def test_a_session_nobody_started_cannot_be_archived(self, service: Service) -> None:
        assert await service.archive("nothing-here") is None

    async def test_a_fork_of_an_archived_session_is_a_live_one(self, service: Service) -> None:
        """The key is session-level, so `before` leaves it behind and the branch carries every turn."""
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)
        await service.archive(session.id)

        forked = await service.fork(session.id, at=1, chosen=DEFAULT_CHOICE)

        assert forked is not None
        branch = await read_session(service.database, forked.id)
        assert branch is not None
        assert branch.archived is None
        assert messages_key(0) in await service.checkpointer.load(forked.id)


class TestWhatAPassDoesWithAnArchivedSession:
    async def test_a_pass_stops_before_planting_anything(self, service: Service, workspaces: Workspaces) -> None:
        planting = replace(service, workspaces=workspaces)
        session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await planting.archive(session.id)
        scripted = Scripted(script=(ModelResponse(parts=[TextPart("one")]),))

        made = await pass_at(planting, conversing(scripted.endpoints(), INSTRUCTIONS, workspaces), session.id)

        assert made == Completed(Archived())
        assert not workspaces.at(session.id).exists()
        assert messages_key(0) not in await planting.checkpointer.load(session.id)


class TestTheNewestTree:
    def test_the_last_request_of_the_last_turn_wins_over_earlier_ones(self) -> None:
        recorded = {
            tree_key(0, 0): snapshotted("aaa"),
            tree_key(0, 1): snapshotted("bbb"),
            messages_key(0): [],
            tree_key(1, 0): snapshotted("ccc"),
            tree_key(1, 1): snapshotted("ddd"),
            messages_key(1): [],
        }

        assert latest_tree(recorded) == snapshotted("ddd")

    def test_a_turn_still_being_answered_counts_where_it_has_captured_anything(self) -> None:
        recorded = {tree_key(0, 0): snapshotted("aaa"), messages_key(0): [], tree_key(1, 0): snapshotted("bbb")}

        assert latest_tree(recorded) == snapshotted("bbb")

    def test_the_tree_captured_on_archiving_wins_over_every_request(self) -> None:
        recorded = {tree_key(0, 0): snapshotted("aaa"), messages_key(0): [], ARCHIVED_TREE_KEY: snapshotted("zzz")}

        assert latest_tree(recorded) == snapshotted("zzz")

    def test_a_session_that_captured_nothing_has_none(self) -> None:
        assert latest_tree({messages_key(0): []}) is None

    async def test_a_fork_from_the_end_plants_at_the_newest_tree(self, service: Service) -> None:
        """Nothing is re-asked, so there is no opening tree to carry, and the head would be wrong."""
        session = await started(service, "first", DEFAULT_CHOICE)
        await service.checkpointer.supply(session.id, tree_key(0, 0), snapshotted("aaa"))
        await service.checkpointer.supply(session.id, tree_key(0, 1), snapshotted("bbb"))
        await answered(service, session.id)

        forked = await service.fork(session.id, at=1, chosen=DEFAULT_CHOICE)

        assert forked is not None
        assert (await service.checkpointer.load(forked.id))[opening_tree_key(1)] == snapshotted("bbb")

    async def test_a_fork_from_the_end_of_an_archived_session_plants_at_the_tree_it_ended_with(
        self, service: Service, workspaces: Workspaces, places: Places
    ) -> None:
        """The end the rule offers, and the one where the files a person left are in the branch."""
        planting, session = await working(service, workspaces, places)
        await answered(planting, session)
        (workspaces.at(session) / "src" / "after.txt").write_text("run by hand after the turn\n")
        await planting.archive(session)
        await reconciled(planting, places, Footprints())

        forked = await planting.fork(session, at=1, chosen=DEFAULT_CHOICE)

        assert forked is not None
        ending = parse_tree((await planting.checkpointer.load(forked.id))[opening_tree_key(1)])
        assert ending is not None
        assert "src/after.txt" in await Worktree(root=workspaces.clones.at(FIXTURE)).paths(ending)

    async def test_a_fork_of_a_turn_still_plants_at_that_turns_own_opening(self, service: Service) -> None:
        """The control: re-asking turn 0 sees the files turn 0 saw, not the newest ones."""
        session = await started(service, "first", DEFAULT_CHOICE)
        await service.checkpointer.supply(session.id, tree_key(0, 0), snapshotted("aaa"))
        await service.checkpointer.supply(session.id, tree_key(0, 1), snapshotted("bbb"))
        await answered(service, session.id)

        forked = await service.fork(session.id, at=0, chosen=DEFAULT_CHOICE)

        assert forked is not None
        assert (await service.checkpointer.load(forked.id))[opening_tree_key(0)] == snapshotted("aaa")


@pytest.fixture
def places(workspaces: Workspaces, tmp_path: Path) -> Places:
    return Places(workspaces=workspaces, plugins=tmp_path / "plugins")


async def working(service: Service, workspaces: Workspaces, places: Places) -> tuple[Service, str]:
    """A session on the fixture repository with its worktree planted and something in every directory."""
    planting = replace(service, workspaces=workspaces)
    session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
    await workspaces.plant(session.id, FIXTURE)
    (workspaces.at(session.id) / "src" / "made.txt").write_text("made in the worktree\n")
    scratch = workspaces.scratch_at(session.id)
    scratch.mkdir(parents=True)
    (scratch / "fetched.bin").write_bytes(b"f" * 30_000)
    plugin = places.plugins / session.id / "repository" / "pre-commit"
    plugin.mkdir(parents=True)
    (plugin / "env").write_text("PATH=/x\n")
    return planting, session.id


class TestTakingAnArchivedSessionOffTheDisk:
    async def test_every_directory_goes_and_git_stops_naming_the_worktree(
        self, service: Service, workspaces: Workspaces, places: Places
    ) -> None:
        planting, session = await working(service, workspaces, places)
        await planting.archive(session)
        holder = Footprints(current={session: Footprint(allocated=30_000, measured_at=WHEN)})

        await reconciled(planting, places, holder, now=lambda: WHEN)

        assert not any(place.exists() for place in places.of(session, FIXTURE))
        worktrees = workspaces.clones.worktrees(FIXTURE, workspaces.root)
        assert workspaces.at(session) not in await worktrees.planted()
        assert holder.current[session] == Footprint(allocated=0, measured_at=WHEN)

    async def test_the_files_it_ended_with_are_captured_before_the_worktree_goes(
        self, service: Service, workspaces: Workspaces, places: Places
    ) -> None:
        """What a fork from the end of an archived session plants at, and the reason the tree is kept."""
        planting, session = await working(service, workspaces, places)
        await planting.archive(session)

        await reconciled(planting, places, Footprints())

        recorded = await planting.checkpointer.load(session)
        ending = parse_tree(recorded[ARCHIVED_TREE_KEY])
        assert ending is not None
        clone = Worktree(root=workspaces.clones.at(FIXTURE))
        assert "src/made.txt" in await clone.paths(ending)

    async def test_a_session_a_pass_holds_keeps_its_files_until_the_claim_ends(
        self, service: Service, workspaces: Workspaces, places: Places
    ) -> None:
        planting, session = await working(service, workspaces, places)
        await planting.archive(session)
        holder = await claimed(planting.checkpointer, session)
        try:
            await reconciled(planting, places, Footprints())
            assert workspaces.at(session).exists(), "held by a pass, so left for the next round"
        finally:
            await planting.checkpointer.release(holder)

        await reconciled(planting, places, Footprints())

        assert not workspaces.at(session).exists()

    async def test_a_session_nobody_archived_is_left_alone(
        self, service: Service, workspaces: Workspaces, places: Places
    ) -> None:
        planting, session = await working(service, workspaces, places)

        await reconciled(planting, places, Footprints())

        assert all(place.exists() for place in places.of(session, FIXTURE))

    async def test_a_round_with_nothing_left_to_do_records_nothing(
        self, service: Service, workspaces: Workspaces, places: Places
    ) -> None:
        """Converged is converged: the second round finds no directory and writes no second tree."""
        planting, session = await working(service, workspaces, places)
        await planting.archive(session)
        await reconciled(planting, places, Footprints())
        before = await planting.checkpointer.load(session)

        await reconciled(planting, places, Footprints())

        assert await planting.checkpointer.load(session) == before


class TestWhatThePageDoesWithAnArchivedSession:
    @pytest.fixture
    def app(self, service: Service) -> ASGIApp:
        return build_app(already(service))

    async def test_the_composer_refuses_the_transcript_says_why_and_the_rail_says_when(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)
        await service.archive(session.id)

        async with calling(app) as caller:
            answered_with = await caller.get(f"/sessions/{session.id}")

        assert answered_with.status == 200
        assert 'name="prompt" rows="3" required autofocus' not in answered_with.text
        assert "disabled" in answered_with.text
        assert '<p class="stalled">Archived Mar 14,' in answered_with.text
        assert '<div class="archive"><div class="archive__head">Archived</div>' in answered_with.text
        assert f'href="/sessions/{session.id}/forks/new?at=1"' in answered_with.text
        assert '<details class="archive">' not in answered_with.text

    async def test_the_row_is_marked_in_the_sidebar(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await service.archive(session.id)

        async with calling(app) as caller:
            page = await caller.get("/")

        assert 'class="session archived"' in page.text
        assert '<span class="archived" title="Archived Mar 14,' in page.text

    async def test_a_live_session_offers_the_card_behind_a_disclosure(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)

        async with calling(app) as caller:
            page = await caller.get(f"/sessions/{session.id}")

        assert '<details class="archive"><summary class="archive__head">Archive</summary>' in page.text
        assert f'action="/sessions/{session.id}/archive"' in page.text
        assert 'class="session current"' in page.text

    async def test_a_message_to_an_archived_session_is_refused(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await service.archive(session.id)

        async with calling(app) as caller:
            said = await caller.post(f"/sessions/{session.id}/messages", {"prompt": "one more thing"})
            forked = await caller.post(
                f"/sessions/{session.id}/messages", {"prompt": "elsewhere", "disposition": "forget"}
            )
            set_up = await caller.post(f"/sessions/{session.id}/setup", {"settle": "settled"})

        assert said.status == 422
        assert forked.status == 422
        assert set_up.status == 422

    async def test_the_press_archives_and_lands_back_on_the_session(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            pressed = await caller.post(f"/sessions/{session.id}/archive", {})
            again = await caller.post(f"/sessions/{session.id}/archive", {})
            missing = await caller.post("/sessions/nothing-here/archive", {})

        assert pressed.status == 303
        assert pressed.location == f"/sessions/{session.id}"
        assert again.status == 303
        assert missing.status == 404
        found = await read_session(service.database, session.id)
        assert found is not None
        assert found.archived is not None

    async def test_an_archived_session_can_still_be_forked_from_its_end(self, app: ASGIApp, service: Service) -> None:
        """The way back, and the reason the conversation is kept at all."""
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)
        await service.archive(session.id)

        async with calling(app) as caller:
            form = await caller.get(f"/sessions/{session.id}/forks/new?at=1")
            branched = await caller.post(
                f"/sessions/{session.id}/forks",
                {"at": "1", "endpoint": "gateway", "model": "wide/steady", "thinking": "low"},
            )

        assert form.status == 200
        assert branched.status == 303
        branch = await read_session(service.database, branched.location.rsplit("/", 1)[-1])
        assert branch is not None
        assert branch.archived is None


class TestForkingFromTheEnd:
    @pytest.fixture
    def app(self, service: Service) -> ASGIApp:
        return build_app(already(service))

    async def test_an_archived_conversation_ends_in_a_rule_forking_from_the_end(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)
        await service.archive(session.id)

        async with calling(app) as caller:
            page = await caller.get(f"/sessions/{session.id}")

        assert '<div class="rule rule--end" id="rule-1">' in page.text
        assert f'href="/sessions/{session.id}/forks/new?at=1"' in page.text

    async def test_a_live_conversation_has_no_such_rule(self, app: ASGIApp, service: Service) -> None:
        """Carrying on a live session is typing into it; the composer's own `fork` is there for the rest."""
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)

        async with calling(app) as caller:
            page = await caller.get(f"/sessions/{session.id}")

        assert 'class="rule rule--end"' not in page.text

    async def test_an_archived_session_with_nothing_answered_has_nothing_to_fork_from(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await taken(service, session.id)
        await service.archive(session.id)

        async with calling(app) as caller:
            page = await caller.get(f"/sessions/{session.id}")

        assert 'class="rule rule--end"' in page.text, "a turn taken is a turn the branch carries and resumes"

    async def test_the_form_for_the_end_says_it_waits_rather_than_asks(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        await answered(service, session.id)

        async with calling(app) as caller:
            form = await caller.get(f"/sessions/{session.id}/forks/new?at=1")

        assert form.status == 200
        assert "then waits for turn 1." in form.text
