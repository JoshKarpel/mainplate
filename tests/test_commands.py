from __future__ import annotations

import asyncio
import shlex
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

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
from conftest import started
from pydantic import ValidationError
from without_asgi import ASGIApp
from without_asgi import Inventory
from without_durability.interfaces import inbox_key

from mainplate import records
from mainplate.agent import Choice
from mainplate.app import build_app
from mainplate.commands import UNFINISHED
from mainplate.commands import Commands
from mainplate.conversation import SETUP_ENVIRONMENT_KEY
from mainplate.conversation import Command
from mainplate.conversation import Panel
from mainplate.conversation import Result
from mainplate.conversation import commit_command
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import parse_result
from mainplate.conversation import reached
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_push
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
from mainplate.conversation import transcript
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Workspaces
from mainplate.pages import BASIS_ID
from mainplate.pages import BRANCHES_ID
from mainplate.service import Service
from mainplate.snapshots import branch_named

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
    session = await started(service, "hello", chosen)
    await workspaces.plant(session.id, FIXTURE, branch=chosen.branch or branch_named(session.id))
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

    def test_a_push_recorded_before_pushes_named_their_branch_reads_as_it_always_did(self) -> None:
        """
        A checkpoint value is durable, so the shape an earlier build wrote a push in has to keep
        loading: a command whose text is `push` and nothing else, which is all it ever said.
        """
        recorded = {**said_at(0, "have a look"), inbox_key(1): {"kind": "command", "said": "push", "online": False}}
        assert transcript(recorded).panels[-1].blocks == (Command(entry=inbox_key(1), text="push"),)


class TestWhereACommandIsDrawn:
    """
    Where it was run, in both readings of the turn it ran during.

    Two properties, and the second is what the inbox bought. A command sits after the requests that
    had answered when somebody typed it, because the database files an entry in the order it arrived and
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
        session = await started(service, "have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_command("git status --short"))
        await service.checkpointer.supply(
            session.id,
            result_key(entry.key),
            recorded_result(Result(status=0, output=" M pages.py\n", took=timedelta(seconds=0.1))),
        )

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert f'<details class="ran" id="ran-{entry.key}" open data-opens="open">' in drawn.text
        assert " M pages.py" in drawn.text

    async def test_a_url_in_command_output_is_a_link(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_command("git push"))
        url = "https://github.com/JoshKarpel/mainplate/compare/main...feature"
        await service.checkpointer.supply(
            session.id,
            result_key(entry.key),
            recorded_result(Result(status=0, output=f"Create a pull request: {url}\n", took=timedelta(seconds=0.1))),
        )

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert f'<a href="{url}" referrerpolicy="no-referrer">{url}</a>' in drawn.text

    async def test_a_command_that_said_nothing_says_so_rather_than_drawing_an_empty_box(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_command("git diff --quiet"))
        await service.checkpointer.supply(
            session.id,
            result_key(entry.key),
            recorded_result(Result(status=1, output="", took=timedelta(seconds=0.08))),
        )

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert "no output" in drawn.text
        assert "<pre>" not in drawn.text

    async def test_a_command_still_running_has_no_body_at_all(self, app: ASGIApp, service: Service) -> None:
        """
        Nothing to say either way yet: `no output` is a claim about a finished command, and a
        console that made it about a running one would be reporting an absence it cannot know about.
        """
        session = await started(service, "have a look", DEFAULT_CHOICE)
        await service.checkpointer.append(session.id, recorded_command("just test"))

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        assert "just test" in drawn.text
        assert "no output" not in drawn.text
        assert 'class="ran__body"' not in drawn.text

    async def test_a_push_is_drawn_naming_the_branch_it_pushed(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_push("try-it-this-way"))

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        line = drawn.text.split(f'id="ran-{entry.key}"', 1)[1].split("</summary>", 1)[0]
        assert 'class="ran__pushed"' in line
        assert '<code class="ran__line">try-it-this-way</code>' in line

    async def test_a_command_whose_text_is_push_is_drawn_as_the_line_somebody_typed(
        self, app: ASGIApp, service: Service
    ) -> None:
        """The other half of the one above: the text alone must not make a command look like a push."""
        session = await started(service, "have a look", DEFAULT_CHOICE)
        entry = await service.checkpointer.append(session.id, recorded_command("push"))

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        line = drawn.text.split(f'id="ran-{entry.key}"', 1)[1].split("</summary>", 1)[0]
        assert 'class="ran__pushed"' not in line
        assert '<code class="ran__line">push</code>' in line


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

    async def test_a_git_write_lands_in_the_worktree(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """The point of the whole thing: the worktree's git is the session's, so a commit commits."""
        session = await planted(running, workspaces, on_fixture)
        where = workspaces.at(session)
        (where / "src" / "kept.txt").write_text("edited by hand\n")

        came = await ran(
            running,
            session,
            "git commit -aqm 'from the console'",
        )

        assert came.status == 0
        assert await run("git", "log", "-1", "--format=%s", cwd=where) == "from the console"

    async def test_a_hook_the_model_left_runs_where_the_model_could_already_reach(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice, tmp_path: Path
    ) -> None:
        """
        Why a person's command is confined at all. A hook is a file in the worktree's `.git`, which
        the model writes; a person's `git commit` run as the service user would run it with
        everything that user holds. The hook runs, since that is what hooks are for, and what it
        tries to reach outside the sandbox is not there.
        """
        session = await planted(running, workspaces, on_fixture)
        where = workspaces.at(session)
        escaped = tmp_path / "escaped"
        hook = where / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\necho the hook ran\ntouch {escaped}\n")
        hook.chmod(0o755)

        came = await ran(running, session, "git commit -q --allow-empty -m x")

        assert "the hook ran" in came.output, "the control: the hook is one git actually ran"
        assert not escaped.exists()

    async def test_a_command_runs_under_what_the_setup_recorded(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        As the model's own commands do, so the toolchain a repository's setup installed is on the
        `PATH` of a command the person types too.
        """
        session = await planted(running, workspaces, on_fixture)
        await running.checkpointer.supply(
            session, SETUP_ENVIRONMENT_KEY, records.Environment(values={"GREETING": "set up"}).recorded()
        )

        came = await ran(running, session, 'echo "$GREETING"')

        assert came.output.strip() == "set up"

    async def test_the_person_s_home_is_not_in_there(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)
        assert Path.home().is_dir(), "the control: this path really is there outside the sandbox"

        came = await ran(running, session, f"ls {Path.home()} >/dev/null 2>&1 && echo VISIBLE || echo DENIED")

        assert came.output.strip() == "DENIED"

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
        lose the loser's command to the database's keep-the-first rule; the database names an entry, so
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
        session = await started(running, "hello", on_fixture)

        came = await ran(running, session.id, "git status")

        assert came.status == UNFINISHED
        assert "worktree is made on its first turn" in came.output

    async def test_a_session_with_no_files_has_nowhere_to_run_one(self, running: Service) -> None:
        """`None` rather than a raise: it is a state the page can explain, not a fault."""
        session = await started(running, "hello", replace(DEFAULT_CHOICE, repository=None))

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
        tell from one that is. The write is shielded for exactly this, and the database outlives the
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


# How many IPv4 routes a command can see: none in a network namespace of its own, and the machine's
# where it shares the host's. Routes rather than interfaces, because tunnel devices like `gre0` are
# created in every new namespace on a machine with the module loaded.
ROUTES = "tail -n +2 /proc/net/route | wc -l"


class TestRunningOneOnline:
    """
    `Run` with the network on for one command, in a session whose commands otherwise have it off.

    The person's call to make, so what these pin is that it does what it says and that the record,
    and so the page, keeps saying it happened.
    """

    async def test_it_reaches_the_network_the_session_s_own_commands_do_not(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)
        confined = await ran(running, session, ROUTES)
        assert confined.output.strip() == "0", "the control: a session's own command has no route anywhere"

        entry = await running.run(session, ROUTES, online=True)

        assert entry is not None
        assert int((await settled(running, session, entry)).output) > 0

    async def test_it_is_recorded_as_having_run_online(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        entry = await running.run(session, "true", online=True)

        assert entry is not None
        assert (await running.checkpointer.load(session))[entry] == recorded_command("true", online=True)

    async def test_in_a_session_whose_network_is_already_on_it_is_an_ordinary_run(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """Nothing was turned on that the session had not already chosen, so there is nothing to mark."""
        connected = replace(on_fixture, isolation=replace(on_fixture.isolation, network=True))
        session = await planted(running, workspaces, connected)

        entry = await running.run(session, "true", online=True)

        assert entry is not None
        assert (await running.checkpointer.load(session))[entry] == recorded_command("true")

    async def test_the_panel_says_it_ran_online(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "have a look", DEFAULT_CHOICE)
        online = await service.checkpointer.append(session.id, recorded_command("npm install", online=True))
        offline = await service.checkpointer.append(session.id, recorded_command("just test"))

        async with calling(app) as caller:
            drawn = await caller.get(f"/sessions/{session.id}")

        panels = {
            entry.key: drawn.text.split(f'id="ran-{entry.key}"', 1)[1].split("</summary>", 1)[0]
            for entry in (online, offline)
        }
        assert 'class="ran__online"' in panels[online.key]
        assert 'class="ran__online"' not in panels[offline.key]


class TestPushingOne:
    """
    The session's branch reaching its repository, recorded where a command's result would be.

    The fixture's origin is a path, which takes a push the way a forge would.
    """

    async def test_a_push_sends_the_branch_and_records_what_git_said(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice, origin: Path
    ) -> None:
        session = await planted(running, workspaces, replace(on_fixture, branch="try-it-this-way"))
        await ran(running, session, "git commit -q --allow-empty -m x")
        made = await run("git", "rev-parse", "HEAD", cwd=workspaces.at(session))

        entry = await running.push(session)

        assert entry is not None
        came = await settled(running, session, entry)
        assert came.status == 0, came.output
        assert "try-it-this-way" in came.output
        assert await run("git", "rev-parse", "refs/heads/try-it-this-way", cwd=origin) == made
        assert (await running.checkpointer.load(session))[entry] == recorded_push("try-it-this-way")

    async def test_a_push_records_what_a_command_whose_text_is_push_does_not(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        A person running `push` in the sandbox and the console pushing both say `push`, so the
        record has to carry what tells them apart, or the page cannot.
        """
        session = await planted(running, workspaces, replace(on_fixture, branch="try-it-this-way"))

        typed = await running.run(session, "push")
        pushed = await running.push(session)

        assert typed is not None
        assert pushed is not None
        await settled(running, session, typed)
        await settled(running, session, pushed)
        recorded = await running.checkpointer.load(session)
        assert records.Command.model_validate(recorded[typed]).pushed is None
        assert records.Command.model_validate(recorded[pushed]).pushed == "try-it-this-way"

    async def test_a_push_the_remote_refuses_is_a_result_rather_than_a_fault(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice, origin: Path
    ) -> None:
        """Never forced, so a branch that moved on at the remote comes back as git's own refusal."""
        session = await planted(running, workspaces, replace(on_fixture, branch="main"))
        (origin / "src" / "kept.txt").write_text("moved on at the remote\n")
        await run("git", "commit", "-aqm", "elsewhere", cwd=origin)
        await ran(running, session, "git commit -q --allow-empty -m x")

        entry = await running.push(session)

        assert entry is not None
        came = await settled(running, session, entry)
        assert came.status != 0
        assert "rejected" in came.output

    async def test_a_session_on_a_repository_nobody_reaches_has_nowhere_to_push(
        self, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)
        gone = replace(running, workspaces=replace(workspaces, reaching=Reaching(current=Reachable(repositories=()))))

        assert await gone.push(session) is None


class TestThroughTheConsole:
    """
    The composer's `Run`, which is one more answer to what happens to what you typed rather than a
    control of its own.
    """

    @pytest.fixture
    def app(self, running: Service, assets: Inventory) -> ASGIApp:
        return build_app(already(running), assets)

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
        session = await started(running, "hello", replace(DEFAULT_CHOICE, repository=None))

        async with calling(app) as caller:
            answer = await caller.post(
                f"/sessions/{session.id}/messages", {"prompt": "echo hello", "disposition": "run"}
            )

        assert answer.status == 422

    async def test_the_menu_offers_running_only_where_there_are_files_to_run_in(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        with_files = await planted(running, workspaces, on_fixture)
        without = await started(running, "hello", replace(DEFAULT_CHOICE, repository=None))

        async with calling(app) as caller:
            offered = await caller.get(f"/sessions/{with_files}")
            plain = await caller.get(f"/sessions/{without.id}")

        assert 'value="run"' in offered.text
        assert 'value="push"' in offered.text
        assert 'value="run"' not in plain.text
        assert 'value="push"' not in plain.text
        # The sentence over a command box is where the branch a commit lands on is read, now that
        # nothing under the box names it.
        assert f"Run it in {running.repository_of(on_fixture)} @ {branch_named(with_files)}," in offered.text

    async def test_the_menu_offers_online_only_where_the_network_is_off(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        offline = await planted(running, workspaces, on_fixture)
        connected = await planted(
            running, workspaces, replace(on_fixture, isolation=replace(on_fixture.isolation, network=True))
        )

        async with calling(app) as caller:
            offered = await caller.get(f"/sessions/{offline}")
            plain = await caller.get(f"/sessions/{connected}")

        assert 'value="online"' in offered.text
        assert 'value="run"' in plain.text, "the control: the other session has a menu at all"
        assert 'value="online"' not in plain.text

    async def test_running_it_online_through_the_console_records_that_it_did(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": "echo hi", "disposition": "online"})

        assert answer.status == 200
        assert recorded_command("echo hi", online=True) in (await running.checkpointer.load(session)).values()

    async def test_pushing_through_the_console_takes_an_empty_box(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": "", "disposition": "push"})

        assert answer.status == 200
        assert recorded_push(branch_named(session)) in (await running.checkpointer.load(session)).values()

    async def test_pushing_with_something_in_the_box_is_refused_rather_than_dropped(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": "hello", "disposition": "push"})

        assert answer.status == 422
        assert recorded_push(branch_named(session)) not in (await running.checkpointer.load(session)).values()


class TestCommittingThroughTheConsole:
    """
    `/commit`, which is a `Run` of `git commit` with the box as the message: a shortcut that commits
    what is staged and stages nothing. What git did is read off the recorded result, which is what a
    reader sees too, rather than by running git against the worktree from out here.
    """

    MESSAGE = "Scroll a long line inside its block\n\nIt's the block that scrolls, and never the page."

    @pytest.fixture
    def app(self, running: Service, assets: Inventory) -> ASGIApp:
        return build_app(already(running), assets)

    async def ran(self, app: ASGIApp, running: Service, session: str, said: str, disposition: str) -> Result:
        async with calling(app) as caller:
            answer = await caller.post(f"/sessions/{session}/messages", {"prompt": said, "disposition": disposition})
        assert answer.status == 200
        recorded = await running.checkpointer.load(session)
        wanted = recorded_command(commit_command(said) if disposition == "commit" else said)
        entry = next(key for key, held in recorded.items() if held == wanted)
        return await settled(running, session, entry)

    async def test_what_is_staged_is_committed_with_the_box_as_its_message(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)
        staged = await self.ran(app, running, session, "echo scrolled > scrolled.txt && git add scrolled.txt", "run")
        assert staged.status == 0, staged.output

        committed = await self.ran(app, running, session, self.MESSAGE, "commit")

        assert committed.status == 0, committed.output
        assert "Scroll a long line inside its block" in committed.output
        assert "1 file changed" in committed.output

    async def test_nothing_staged_is_gits_own_refusal_and_nothing_is_staged_for_you(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await planted(running, workspaces, on_fixture)
        unstaged = await self.ran(app, running, session, "echo stray > stray.txt", "run")
        assert unstaged.status == 0, unstaged.output

        committed = await self.ran(app, running, session, "Commit the stray file", "commit")

        assert committed.status != 0
        assert "stray.txt" in committed.output, "git names the file it was not told to commit"

    async def test_the_menu_offers_it_where_there_are_files(
        self, app: ASGIApp, running: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        with_files = await planted(running, workspaces, on_fixture)

        async with calling(app) as caller:
            offered = await caller.get(f"/sessions/{with_files}")

        assert 'value="commit"' in offered.text

    async def test_the_menu_of_a_session_with_no_files_does_not_offer_it(self, app: ASGIApp, running: Service) -> None:
        without = await started(running, "hello", replace(DEFAULT_CHOICE, repository=None))

        async with calling(app) as caller:
            plain = await caller.get(f"/sessions/{without.id}")

        assert 'value="forget"' in plain.text, "the control: the session has a menu at all"
        assert 'value="commit"' not in plain.text

    async def test_a_session_with_no_files_refuses_it_and_records_nothing(self, app: ASGIApp, running: Service) -> None:
        without = await started(running, "hello", replace(DEFAULT_CHOICE, repository=None))

        async with calling(app) as caller:
            refused = await caller.post(
                f"/sessions/{without.id}/messages", {"prompt": "a message", "disposition": "commit"}
            )

        assert refused.status == 422
        recorded = (await running.checkpointer.load(without.id)).values()
        assert recorded_command(commit_command("a message")) not in recorded


@pytest.mark.parametrize(
    "message",
    ["plain", "it's quoted", 'a "double" one', "several\n\nlines", "$(touch pwned) `and` $HOME"],
)
def test_a_commit_message_is_one_argument_whatever_it_holds(message: str) -> None:
    assert shlex.split(commit_command(message)) == ["git", "commit", "-m", message]


class TestStartingSomewhereThroughTheForm:
    """
    The base and the branch as the form carries them, which is where they become `git` arguments.

    Refused rather than dropped, which is the split `posted_workspace` already makes: a blank box is
    somebody taking the default, where anything else is somebody who meant a particular thing and
    would otherwise get a session quietly started somewhere else.
    """

    @pytest.fixture
    def app(self, running: Service, assets: Inventory) -> ASGIApp:
        return build_app(already(running), assets)

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
    def app(self, running: Service, assets: Inventory) -> ASGIApp:
        return build_app(already(running), assets)

    async def test_picking_a_repository_offers_its_branches(self, app: ASGIApp, origin: Path) -> None:
        await run("git", "branch", "release/2.1", cwd=origin)

        async with calling(app) as caller:
            answer = await caller.get(f"/fragments/branches?workspace={FIXTURE}")

        assert answer.status == 200
        assert 'value="main"' in answer.text
        assert 'value="release/2.1"' in answer.text

    async def test_a_workspace_that_is_not_a_repository_has_no_fields_to_offer_for(self, app: ASGIApp) -> None:
        """
        A base and a branch are answers about a repository, so `only scratch` takes the fields themselves
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
