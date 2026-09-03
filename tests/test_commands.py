from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import already
from conftest import run
from without_asgi import ASGIApp

from mainplate.agent import Choice
from mainplate.app import build_app
from mainplate.commands import UNFINISHED
from mainplate.commands import Commands
from mainplate.conversation import Command
from mainplate.conversation import Panel
from mainplate.conversation import Result
from mainplate.conversation import command_key
from mainplate.conversation import commands_in
from mainplate.conversation import messages_key
from mainplate.conversation import parse_result
from mainplate.conversation import prompt_key
from mainplate.conversation import reached
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
from mainplate.conversation import transcript
from mainplate.forge import Workspaces
from mainplate.pages import BRANCHES_ID
from mainplate.service import Service
from mainplate.snapshots import Worktree

# Nothing here is slow on purpose, so a bound well under the suite's own is what a runaway command
# hits rather than the test timeout.
PATIENCE = timedelta(seconds=20)

# A branch list with nothing in it, asserted as the whole element rather than as the absence of any
# `<option>` on the page: the cards above carry a completion list of their own over the card names,
# so "no options anywhere" is a question about that one.
EMPTY_BRANCHES = f'<datalist id="{BRANCHES_ID}"></datalist>'


@pytest.fixture
def running(service: Service, workspaces: Workspaces) -> Service:
    """A console that can run a command, which needs somewhere to run one and something to run it."""
    return replace(
        service, workspaces=workspaces, commands=Commands(checkpointer=service.checkpointer, patience=PATIENCE)
    )


@pytest.fixture
def on_fixture() -> Choice:
    return replace(DEFAULT_CHOICE, repository=FIXTURE)


@pytest.fixture
def caller_form() -> dict[str, str]:
    """The least a start form can carry, so a test naming one more field says only what it is about."""
    return {
        "prompt": "hello",
        "endpoint": DEFAULT_CHOICE.endpoint,
        "model": DEFAULT_CHOICE.model,
        "workspace": FIXTURE,
    }


async def planted(service: Service, workspaces: Workspaces, chosen: Choice) -> str:
    """
    A session whose worktree is actually on disk, without driving a pass to get it there.

    Planted directly rather than through `conversing`, because what these tests are about is what
    happens in the worktree and not how it came to exist: a pass would bring a stand-in provider, an
    agent and a whole turn along with it to produce one directory.
    """
    session = await service.start("hello", chosen)
    await workspaces.plant(session.id, FIXTURE)
    return session.id


async def settled(service: Service, session: str, at: int = 0, turn: int = 0) -> Result:
    """
    What one command came to, waited for rather than slept on.

    The record is written by a task nobody holds a handle to, so what a test synchronises on is the
    key appearing. A poll rather than a sleep, because any fixed duration is either racy or wasted.
    """
    async with asyncio.timeout(PATIENCE.total_seconds()):
        while True:
            held = (await service.checkpointer.load(session)).get(result_key(turn, at))
            if held is not None:
                return parse_result(held)
            await asyncio.sleep(0.01)


class TestWhatTheStoreHolds:
    def test_the_keys_are_the_shape_the_scheme_says(self) -> None:
        """
        Literal strings, exactly as the other key tests are, and for the same reason: a session on
        disk was written by whatever this file said at the time and has to keep reading back.
        """
        assert command_key(3, 1) == "turn:3:command:1"
        assert result_key(3, 1) == "turn:3:result:1"

    def test_a_result_round_trips_through_what_the_codec_takes(self) -> None:
        came = Result(status=2, output="no such file\n", took=timedelta(milliseconds=250))
        assert parse_result(recorded_result(came)) == came

    def test_a_result_the_console_could_not_time_reads_back_as_untimed(self) -> None:
        """Absent is a state the page draws, not one to default to a duration of zero."""
        assert parse_result({"status": 0, "output": "", "took": None}).took is None

    @pytest.mark.parametrize(
        "held",
        [
            pytest.param({"output": "hi"}, id="no status"),
            pytest.param({"status": 0}, id="no output"),
            pytest.param({"status": True, "output": ""}, id="a boolean is not an exit status"),
            pytest.param("ok", id="not a mapping at all"),
        ],
    )
    def test_a_record_that_is_not_a_result_fails_loudly(self, held: object) -> None:
        with pytest.raises(TypeError):
            parse_result(held)

    def test_a_command_with_no_result_beside_it_is_one_still_running(self) -> None:
        assert commands_in({command_key(0, 0): "sleep 30"}, 0) == (Command(text="sleep 30"),)

    def test_the_walk_stops_at_the_first_slot_nobody_claimed(self) -> None:
        """Consecutive from zero, like every other numbered kind: `Service.run` leaves no gap."""
        recorded = {command_key(0, 0): "git status", command_key(0, 2): "never written by this console"}
        assert [ran.text for ran in commands_in(recorded, 0)] == ["git status"]


class TestWhereACommandIsDrawn:
    """
    At the end of the turn it ran during, in both readings of that turn.

    The property worth pinning is that the two agree: the page morphs one into the other when a turn
    lands, so a command that moved at that moment would be the transcript rewriting itself under
    whoever was reading it.
    """

    def test_a_command_run_during_a_settled_turn_sits_after_that_turn_s_panels(self) -> None:
        recorded: dict[str, object] = {
            prompt_key(0): "have a look",
            messages_key(0): [],
            command_key(0, 0): "git status",
            result_key(0, 0): {"status": 0, "output": "nothing to commit\n", "took": 0.1},
        }
        said = transcript(recorded)

        assert [(panel.kind, panel.at) for panel in said.panels] == [("person", 0), ("command", 1)]

    def test_a_command_run_while_a_turn_is_being_answered_is_drawn_before_it_lands(self) -> None:
        running: dict[str, object] = {prompt_key(0): "have a look", command_key(0, 0): "git status"}
        assert transcript(running).panels[-1] == Panel(
            turn=0, at=1, kind="command", blocks=(Command(text="git status"),)
        )

    def test_a_command_does_not_move_when_the_turn_it_ran_during_is_answered(self) -> None:
        """
        The whole of why it goes at the end rather than among the model's own panels: nothing records
        which request was in flight, so anywhere else would be a position one reading could not
        reconstruct and the panel would jump as the turn settled.
        """
        ran = {command_key(0, 0): "git status"}
        running: dict[str, object] = {prompt_key(0): "have a look", **ran}
        landed: dict[str, object] = {prompt_key(0): "have a look", messages_key(0): [], **ran}

        assert transcript(running).panels[-1] == transcript(landed).panels[-1]

    def test_a_command_never_reaches_the_history_a_model_is_given(self) -> None:
        """
        Recorded and not told, which is the whole of what a command is here. `reached` is what a pass
        hands the model, so a command appearing in it would be this console saying something nobody
        wrote to the model.
        """
        recorded: dict[str, object] = {
            prompt_key(0): "have a look",
            messages_key(0): [],
            command_key(0, 0): "echo do not tell the model",
            result_key(0, 0): {"status": 0, "output": "do not tell the model\n", "took": 0.1},
        }
        assert reached(recorded).history == ()


class TestRunningOne:
    async def test_a_command_runs_in_the_session_s_own_worktree(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        assert await running.run(session, "pwd") == 0

        came = await settled(running, session)
        assert came.status == 0
        assert came.output.strip() == str(workspaces.at(session))

    async def test_what_a_command_says_on_both_streams_comes_back_in_the_order_it_was_written(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        One stream, because that is what a terminal shows. Kept apart they interleave wrongly or not
        at all, and nobody has ever wanted a build's errors in a second column.
        """
        session = await planted(running, workspaces, on_fixture)

        await running.run(session, "echo first; echo second >&2; echo third")

        assert (await settled(running, session)).output.splitlines() == ["first", "second", "third"]

    async def test_an_exit_status_is_recorded_as_the_number_rather_than_as_a_failure(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        `git diff --quiet` exits 1 to mean there *are* changes, so flattening a status to a boolean
        would have this console report a command doing its job as one that failed.
        """
        session = await planted(running, workspaces, on_fixture)

        await running.run(session, "exit 3")

        assert (await settled(running, session)).status == 3

    async def test_a_git_write_lands_in_the_worktree_because_nothing_confines_it(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        The point of the whole thing. A session's `isolation` bounds what a *model* asked for, and
        the clone is bound read-only inside that sandbox precisely so no tool can write history;
        `git commit` is the person's, so it runs outside all of it and actually commits.
        """
        session = await planted(running, workspaces, on_fixture)
        where = workspaces.at(session)
        (where / "src" / "kept.txt").write_text("edited by hand\n")

        await running.run(
            session,
            "git -c user.email=probe@example.invalid -c user.name=probe commit -aqm 'from the console'",
        )

        assert (await settled(running, session)).status == 0
        assert await run("git", "log", "-1", "--format=%s", cwd=where) == "from the console"

    async def test_a_command_that_will_not_stop_is_killed_and_says_so(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        impatient = replace(
            running, commands=Commands(checkpointer=running.checkpointer, patience=timedelta(milliseconds=200))
        )
        session = await planted(impatient, workspaces, on_fixture)

        await impatient.run(session, "echo starting; sleep 30")

        came = await settled(impatient, session)
        assert "starting" in came.output, "what it managed to say survives being killed"
        assert "killed after" in came.output

    async def test_two_commands_posted_at_once_each_take_a_slot_of_their_own(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        The store keeps the value a key was first given, so two writers racing for one number would
        leave the loser's command under a key nothing reads: run, and never rendered.
        """
        session = await planted(running, workspaces, on_fixture)

        taken = await asyncio.gather(*(running.run(session, f"echo {which}") for which in ("one", "two", "three")))

        assert sorted(at for at in taken if at is not None) == [0, 1, 2], "three commands, three slots"
        for at in taken:
            assert at is not None
            assert (await settled(running, session, at)).status == 0

    async def test_a_command_is_recorded_against_the_last_turn_started(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Which is where it happened: everything said so far is above it and nothing has been said
        since. A turn queued behind a reply in flight counts as started, so a command run then lands
        under the message somebody typed ahead rather than above it.
        """
        session = await planted(running, workspaces, on_fixture)
        await running.say(session, turn=1, said="and another thing")

        assert await running.run(session, "echo late") == 0

        recorded = await running.checkpointer.load(session)
        assert recorded.get(command_key(1, 0)) == "echo late"
        assert recorded.get(command_key(0, 0)) is None

    async def test_a_command_before_the_first_turn_says_the_worktree_is_not_there_yet(
        self, running: Service, on_fixture: Choice
    ) -> None:
        """
        The common case rather than an odd one. A worktree is planted by the session's *first pass*,
        because a clone is a network fetch and creating a session is a POST somebody is waiting on -
        so between creating one and its first reply there is a repository, a `Run` on offer, and
        nowhere yet to run in. What that must not be is a `FileNotFoundError` repr.
        """
        session = await running.start("hello", on_fixture)

        assert await running.run(session.id, "git status") == 0

        came = await settled(running, session.id)
        assert came.status == UNFINISHED
        assert "worktree is made on its first turn" in came.output

    async def test_a_session_with_no_files_has_nowhere_to_run_one(self, running: Service) -> None:
        """
        `None` rather than a raise, matching what `send` does with a turn that stopped listening: it
        is a state the page can explain, not a fault.
        """
        session = await running.start("hello", replace(DEFAULT_CHOICE, repository=None))

        assert await running.run(session.id, "echo nowhere") is None

    async def test_a_console_built_without_a_runner_runs_nothing(
        self, service: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        without = replace(service, workspaces=workspaces)
        session = await planted(without, workspaces, on_fixture)

        assert await without.run(session, "echo nowhere") is None

    async def test_stopping_the_console_records_that_it_stopped_rather_than_leaving_a_panel_running(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        A record left unwritten is a command that says it is still going for ever, which nobody can
        tell from one that is. The write is shielded for exactly this, and the store outlives the
        cancellation because `open_store` closes the runner inside its own `finally`.
        """
        session = await planted(running, workspaces, on_fixture)
        await running.run(session, "sleep 30")
        assert running.commands is not None

        await running.commands.aclose()

        came = await settled(running, session)
        assert came.status == UNFINISHED
        assert "the console stopped" in came.output


class TestThroughTheConsole:
    """
    The composer's `Run`, which is one more answer to what happens to what you typed rather than a
    control of its own.
    """

    @pytest.fixture
    def app(self, running: Service) -> ASGIApp:
        return build_app(already(running))

    async def test_running_it_records_a_command_and_answers_with_the_conversation(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": "echo hello", "disposition": "run"})

        assert answer.status == 200
        assert (await running.checkpointer.load(session)).get(command_key(0, 0)) == "echo hello"
        assert (await settled(running, session)).output.strip() == "hello"

    async def test_the_command_is_drawn_rather_than_sent_as_a_message(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """A command is not a prompt, so it must not queue a turn for a worker to answer."""
        session = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": "echo hello", "disposition": "run"})

        assert "echo hello" in answer.text
        assert (await running.read(session)) is not None
        assert (await running.checkpointer.load(session)).get(prompt_key(1)) is None

    async def test_a_session_with_no_files_refuses_rather_than_swallowing_it(
        self, app: ASGIApp, running: Service
    ) -> None:
        """
        A command that vanished would be indistinguishable from one that ran and did nothing, which
        is the state nobody can diagnose.
        """
        session = await running.start("hello", replace(DEFAULT_CHOICE, repository=None))

        async with calling(app) as caller:
            answer = await caller.post(
                f"/sessions/{session.id}/messages", {"prompt": "echo hello", "disposition": "run"}
            )

        assert answer.status == 422

    async def test_the_menu_offers_running_only_where_there_are_files_to_run_in(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        with_files = await planted(running, workspaces, on_fixture)
        without = await running.start("hello", replace(DEFAULT_CHOICE, repository=None))

        async with calling(app) as caller:
            offered = await caller.get(f"/sessions/{with_files}")
            plain = await caller.get(f"/sessions/{without.id}")

        assert 'value="run"' in offered.text
        assert 'value="run"' not in plain.text


class TestStartingSomewhereThroughTheForm:
    """
    The base and the branch as the form carries them, which is where they become `git` arguments.

    Refused rather than dropped, which is the split `posted_workspace` already makes: a blank box is
    somebody taking the default, where anything else is somebody who meant a particular thing and
    would otherwise get a session quietly started somewhere else.
    """

    @pytest.fixture
    def app(self, running: Service) -> ASGIApp:
        return build_app(already(running))

    async def test_a_base_and_a_branch_are_recorded_on_the_session(
        self, app: ASGIApp, running: Service, caller_form: dict[str, str]
    ) -> None:
        async with calling(app) as caller:
            answer = await caller.post("/sessions", {**caller_form, "base": "main", "branch": "try-it"})

        assert answer.status == 303
        listed = await running.listed()
        started = await running.read(listed[0].id)
        assert started is not None
        assert started.chosen is not None
        assert (started.chosen.base, started.chosen.branch) == ("main", "try-it")

    @pytest.mark.parametrize(
        ("field", "written"),
        [
            pytest.param("base", "--upload-pack=whatever", id="a base that is an option"),
            pytest.param("base", "main..other", id="a base with a range in it"),
            pytest.param("branch", "-f", id="a branch that is an option"),
            pytest.param("branch", "try it", id="a branch with a space in it"),
            pytest.param("branch", "feature.lock", id="a branch git itself refuses"),
            pytest.param("branch", "what^ever", id="a branch with revision syntax in it"),
        ],
    )
    async def test_something_that_is_not_a_ref_is_refused(
        self, app: ASGIApp, caller_form: dict[str, str], field: str, written: str
    ) -> None:
        async with calling(app) as caller:
            answer = await caller.post("/sessions", {**caller_form, field: written})

        assert answer.status == 422

    async def test_an_empty_box_is_the_default_rather_than_a_refusal(
        self, app: ASGIApp, caller_form: dict[str, str]
    ) -> None:
        async with calling(app) as caller:
            answer = await caller.post("/sessions", {**caller_form, "base": "", "branch": "  "})

        assert answer.status == 303


class TestOfferingWhereToStart:
    """
    The completions beside the field, swapped in when a workspace card is picked.

    The same shape the model group already has under the endpoint cards, and it exists for a reason
    a still cannot show: what a repository's branches are is a question with a different answer per
    card, so a page that serialized one list would be offering the wrong repository's the moment
    somebody changed their mind.
    """

    @pytest.fixture
    def app(self, running: Service) -> ASGIApp:
        return build_app(already(running))

    async def test_picking_a_repository_offers_its_branches(self, app: ASGIApp, worktree: Worktree) -> None:
        await run("git", "branch", "release/2.1", cwd=worktree.root)

        async with calling(app) as caller:
            answer = await caller.get(f"/fragments/branches?workspace={FIXTURE}")

        assert answer.status == 200
        assert 'value="main"' in answer.text
        assert 'value="release/2.1"' in answer.text

    async def test_a_workspace_that_is_not_a_repository_offers_nothing(self, app: ASGIApp) -> None:
        """
        And still answers with the block, so picking `no files` after a repository takes the previous
        repository's branches back off the page rather than leaving them to complete a field they no
        longer describe.
        """
        async with calling(app) as caller:
            answer = await caller.get("/fragments/branches?workspace=nothing")

        assert answer.status == 200
        assert 'name="base"' in answer.text
        assert EMPTY_BRANCHES in answer.text

    async def test_a_workspace_this_console_does_not_know_is_refused(self, app: ASGIApp) -> None:
        """
        The one refusal here, because it is a malformed request rather than an answer about an
        environment. The card's own `hx-status:4xx` is what leaves the block standing.
        """
        async with calling(app) as caller:
            answer = await caller.get("/fragments/branches?workspace=neither-a-level-nor-an-id")

        assert answer.status == 422

    async def test_the_page_offers_none_until_a_repository_is_picked(
        self, app: ASGIApp, caller_form: dict[str, str]
    ) -> None:
        """
        A first render asks no repository anything. Reaching every one of them to draw a page on
        which all but one of those lists is never looked at is several network calls for nothing.
        """
        async with calling(app) as caller:
            answer = await caller.get("/")

        assert 'name="base"' in answer.text
        # The *branches* list specifically: the group above it has a completion list of its own, over
        # the card names, so asking whether the page holds any `<option>` at all answers about that.
        assert EMPTY_BRANCHES in answer.text
