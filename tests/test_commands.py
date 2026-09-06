from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import already
from conftest import answered_with
from conftest import ran_at
from conftest import recorded_turn
from conftest import run
from conftest import said_at
from pydantic import ValidationError
from without_asgi import ASGIApp
from without_durability.interfaces import inbox_key

from mainplate.agent import Choice
from mainplate.app import build_app
from mainplate.commands import UNFINISHED
from mainplate.commands import Commands
from mainplate.conversation import Command
from mainplate.conversation import Panel
from mainplate.conversation import Result
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import parse_result
from mainplate.conversation import reached
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
from mainplate.conversation import transcript
from mainplate.forge import Workspaces
from mainplate.pages import BASIS_ID
from mainplate.pages import BRANCHES_ID
from mainplate.service import Service
from mainplate.snapshots import Worktree

# Nothing here is slow on purpose, so a bound well under the suite's own is what a runaway command
# hits rather than the test timeout.
PATIENCE = timedelta(seconds=20)


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


async def settled(service: Service, session: str, entry: str) -> Result:
    """
    What one command came to, waited for rather than slept on.

    The record is written by a task nobody holds a handle to, so what a test synchronises on is the
    key appearing. A poll rather than a sleep, because any fixed duration is either racy or wasted.
    """
    async with asyncio.timeout(PATIENCE.total_seconds()):
        while True:
            held = (await service.checkpointer.load(session)).get(result_key(entry))
            if held is not None:
                return parse_result(held)
            await asyncio.sleep(0.01)


async def ran(service: Service, session: str, command: str) -> Result:
    """One command, run and waited for, which is what almost every test below wants."""
    entry = await service.run(session, command)
    assert entry is not None, "this session has somewhere to run a command"
    return await settled(service, session, entry)


class TestWhatTheStoreHolds:
    def test_the_keys_are_the_shape_the_scheme_says(self) -> None:
        """
        Literal strings, exactly as the other key tests are, and for the same reason: a session on
        disk was written by whatever this file said at the time and has to keep reading back.

        A result is named after the entry its command arrived as, rather than after a turn and a
        slot: which turn a command belongs to is decided by where its entry landed, so a key naming
        one would be a second answer to that question.
        """
        assert result_key(inbox_key(7)) == "result:inbox:00000000000000000007"

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
        with pytest.raises(ValidationError):
            parse_result(held)

    def test_a_command_with_no_result_beside_it_is_one_still_running(self) -> None:
        recorded = {**said_at(0, "have a look"), **ran_at(1, "sleep 30")}
        assert transcript(recorded).panels[-1].blocks == (Command(entry=inbox_key(1), text="sleep 30"),)


class TestWhereACommandIsDrawn:
    """
    Where it was run, in both readings of the turn it ran during.

    Two properties, and the second is what the inbox bought. A command sits after the requests that
    had answered when somebody typed it, because the store files an entry in the order it arrived and
    counting this turn's model records ahead of it says how far the reply had got. And the two
    readings agree: the page morphs one into the other when a turn lands, so a command that moved at
    that moment would be the transcript rewriting itself under whoever was reading it.
    """

    def test_a_command_run_during_a_settled_turn_sits_after_that_turn_s_panels(self) -> None:
        recorded: dict[str, object] = {
            **said_at(0, "have a look"),
            messages_key(0): recorded_turn(),
            **ran_at(1, "git status"),
            result_key(inbox_key(1)): recorded_result(
                Result(status=0, output="nothing to commit\n", took=timedelta(seconds=0.1))
            ),
        }
        said = transcript(recorded)

        assert [(panel.kind, panel.at) for panel in said.panels] == [("prompt", 0), ("command", 1)]

    def test_a_command_run_while_a_turn_is_being_answered_is_drawn_before_it_lands(self) -> None:
        running: dict[str, object] = {**said_at(0, "have a look"), **ran_at(1, "git status")}
        assert transcript(running).panels[-1] == Panel(
            turn=0, at=1, kind="command", blocks=(Command(entry=inbox_key(1), text="git status"),)
        )

    def test_a_command_does_not_move_when_the_turn_it_ran_during_is_answered(self) -> None:
        """
        The property the two readings of a turn rest on, one kind of panel along: a command is placed
        by where its entry sits among the turn's model records, and that is the same reading whether
        the turn is being watched or has landed.
        """
        ran = ran_at(1, "git status")
        running: dict[str, object] = {**said_at(0, "have a look"), **ran}
        landed: dict[str, object] = {**said_at(0, "have a look"), messages_key(0): recorded_turn(), **ran}

        assert transcript(running).panels[-1] == transcript(landed).panels[-1]

    def test_a_command_run_between_two_answers_stays_between_them(self) -> None:
        """
        What filing a command as an entry buys, and the bug it fixes: collected at the end of the
        turn, the panel sank as each later answer landed above it, under a reader who had just run it.
        """
        recorded: dict[str, object] = {
            **said_at(0, "have a look"),
            model_key(0, 0): answered_with({"kind": "response", "parts": [{"part_kind": "text", "content": "one"}]}),
            **ran_at(1, "git status"),
            model_key(0, 1): answered_with({"kind": "response", "parts": [{"part_kind": "text", "content": "two"}]}),
        }
        assert [(panel.kind, panel.at) for panel in transcript(recorded).panels] == [
            ("prompt", 0),
            ("assistant", 1),
            ("command", 2),
            ("assistant", 3),
        ]

    def test_a_command_never_reaches_the_history_a_model_is_given(self) -> None:
        """
        Recorded and not told, which is the whole of what a command is here. `reached` is what a pass
        hands the model, so a command appearing in it would be this console saying something nobody
        wrote to the model.
        """
        recorded: dict[str, object] = {
            **said_at(0, "have a look"),
            messages_key(0): recorded_turn(),
            **ran_at(1, "echo do not tell the model"),
            result_key(inbox_key(1)): recorded_result(
                Result(status=0, output="do not tell the model\n", took=timedelta(seconds=0.1))
            ),
        }
        assert reached(recorded).history == ()


class TestHowACommandIsDrawn:
    """
    Open, and saying what it said - which for plenty of commands is nothing at all.

    The panel is the whole of what a person gets back from `Run`, so what these pin is that reading
    one costs no clicks and that a command whose only answer was its exit status says so rather than
    drawing an empty pane.
    """

    async def test_the_output_is_open_rather_than_folded_away(self, app: ASGIApp, service: Service) -> None:
        session = await service.start("have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_command("git status --short"))
        await service.checkpointer.supply(
            session.id,
            result_key(entry.key),
            recorded_result(Result(status=0, output=" M pages.py\n", took=timedelta(seconds=0.1))),
        )

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert f'<details class="ran" id="ran-{entry.key}" open>' in drawn.text
        assert " M pages.py" in drawn.text

    async def test_a_command_that_said_nothing_says_so_rather_than_drawing_an_empty_box(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await service.start("have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_command("git diff --quiet"))
        await service.checkpointer.supply(
            session.id,
            result_key(entry.key),
            recorded_result(Result(status=1, output="", took=timedelta(seconds=0.08))),
        )

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert "said nothing" in drawn.text
        assert "<pre>" not in drawn.text

    async def test_a_command_still_running_has_no_body_at_all(self, app: ASGIApp, service: Service) -> None:
        """
        Nothing to say either way yet: `said nothing` is a claim about a finished command, and a
        console that made it about a running one would be reporting an absence it cannot know about.
        """
        session = await service.start("have a look", DEFAULT_CHOICE)
        await service.checkpointer.append(session.id, recorded_command("just test"))

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert "just test" in drawn.text
        assert "said nothing" not in drawn.text
        assert 'class="ran__body"' not in drawn.text


class TestRunningOne:
    async def test_a_command_runs_in_the_session_s_own_worktree(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        came = await ran(running, session, "pwd")

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

        came = await ran(running, session, "echo first; echo second >&2; echo third")

        assert came.output.splitlines() == ["first", "second", "third"]

    async def test_an_exit_status_is_recorded_as_the_number_rather_than_as_a_failure(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        `git diff --quiet` exits 1 to mean there *are* changes, so flattening a status to a boolean
        would have this console report a command doing its job as one that failed.
        """
        session = await planted(running, workspaces, on_fixture)

        assert (await ran(running, session, "exit 3")).status == 3

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

        came = await ran(
            running,
            session,
            "git -c user.email=probe@example.invalid -c user.name=probe commit -aqm 'from the console'",
        )

        assert came.status == 0
        assert await run("git", "log", "-1", "--format=%s", cwd=where) == "from the console"

    async def test_a_command_that_will_not_stop_is_killed_and_says_so(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        impatient = replace(
            running, commands=Commands(checkpointer=running.checkpointer, patience=timedelta(milliseconds=200))
        )
        session = await planted(impatient, workspaces, on_fixture)

        came = await ran(impatient, session, "echo starting; sleep 30")

        assert "starting" in came.output, "what it managed to say survives being killed"
        assert "killed after" in came.output

    async def test_three_commands_posted_at_once_each_take_an_entry_of_their_own(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        What the queue removed the need to check for. Two writers picking a number by trying could
        lose the loser's command to the store's keep-the-first rule; the store names an entry, so
        three appends are three entries and there is nothing to race for.
        """
        session = await planted(running, workspaces, on_fixture)

        taken = await asyncio.gather(*(running.run(session, f"echo {which}") for which in ("one", "two", "three")))

        assert len(set(taken)) == 3, "three commands, three entries"
        for entry in taken:
            assert entry is not None
            assert (await settled(running, session, entry)).status == 0

    async def test_a_command_is_drawn_in_the_turn_that_was_in_hand_when_it_ran(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Which is where it happened, and it is read rather than recorded: the entry sits after the
        message that opened the turn in hand, so nothing had to decide a turn at the moment it was
        posted. A message queued behind it has not opened a turn yet, so a command run afterwards is
        still in the last turn anybody opened.
        """
        session = await planted(running, workspaces, on_fixture)
        await running.say(session, "and another thing")

        entry = await running.run(session, "echo late")

        assert entry is not None
        assert (await running.checkpointer.load(session))[entry] == recorded_command("echo late")

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

        came = await ran(running, session.id, "git status")

        assert came.status == UNFINISHED
        assert "worktree is made on its first turn" in came.output

    async def test_a_session_with_no_files_has_nowhere_to_run_one(self, running: Service) -> None:
        """`None` rather than a raise: it is a state the page can explain, not a fault."""
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
        entry = await running.run(session, "sleep 30")
        assert entry is not None
        assert running.commands is not None

        await running.commands.aclose()

        came = await settled(running, session, entry)
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
        recorded = await running.checkpointer.load(session)
        entry = next(key for key, held in recorded.items() if held == recorded_command("echo hello"))
        assert (await settled(running, session, entry)).output.strip() == "hello"

    async def test_the_command_is_drawn_rather_than_sent_as_a_message(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """A command is not a prompt, so it must not queue a turn for a worker to answer."""
        session = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": "echo hello", "disposition": "run"})

        assert "echo hello" in answer.text
        found = await running.read(session)
        assert found is not None
        assert found.said.turns == 1, "the command opened no turn of its own and queued no message"

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

    async def test_a_workspace_that_is_not_a_repository_has_no_fields_to_offer_for(self, app: ASGIApp) -> None:
        """
        A base and a branch are answers about a repository, so `no files` takes the fields themselves
        off rather than leaving two boxes asking a question the session does not have. Answered with
        the block all the same, since the previous repository's fields are on the page until this
        swap replaces them.
        """
        async with calling(app) as caller:
            answer = await caller.get("/fragments/branches?workspace=nothing")

        assert answer.status == 200
        assert 'name="base"' not in answer.text
        assert 'name="branch"' not in answer.text
        assert f'id="{BASIS_ID}"' in answer.text

    async def test_a_workspace_this_console_does_not_know_is_refused(self, app: ASGIApp) -> None:
        """
        The one refusal here, because it is a malformed request rather than an answer about an
        environment. The card's own `hx-status:4xx` is what leaves the block standing.
        """
        async with calling(app) as caller:
            answer = await caller.get("/fragments/branches?workspace=neither-a-level-nor-an-id")

        assert answer.status == 422

    async def test_the_page_asks_where_to_start_only_once_a_repository_is_picked(
        self, app: ASGIApp, caller_form: dict[str, str]
    ) -> None:
        """
        A new session starts on no repository, so a first render has nothing for those fields to be
        about and draws neither them nor any completions. Picking a card is what fetches the one list
        that matters: reaching every reachable repository to draw a page on which all but one of
        those lists is never looked at is several network calls for nothing.
        """
        async with calling(app) as caller:
            answer = await caller.get("/")

        assert 'name="base"' not in answer.text
        assert 'name="branch"' not in answer.text
        # The *branches* list specifically: the group above it has a completion list of its own, over
        # the card names, so asking whether the page holds any `<option>` at all answers about that.
        assert f'id="{BRANCHES_ID}"' not in answer.text
