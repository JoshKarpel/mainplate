from __future__ import annotations

import asyncio
import os
import socket
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import WHEN
from conftest import Provider
from conftest import already
from conftest import passing
from conftest import started
from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset
from without_asgi import Inventory
from without_durability.interfaces import inbox_key

from mainplate import records
from mainplate.agent import Choice
from mainplate.app import build_app
from mainplate.commands import UNFINISHED
from mainplate.console import opened_at
from mainplate.conversation import ARCHIVED_KEY
from mainplate.conversation import Command
from mainplate.conversation import Job
from mainplate.conversation import Result
from mainplate.conversation import conversing
from mainplate.conversation import entry_of
from mainplate.conversation import listening_key
from mainplate.conversation import parse_result
from mainplate.conversation import posted_in
from mainplate.conversation import recorded_job
from mainplate.conversation import recorded_listening
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
from mainplate.conversation import started_by
from mainplate.conversation import transcript
from mainplate.conversation import wanted_in
from mainplate.forge import Workspaces
from mainplate.jobs import RESTARTED
from mainplate.jobs import Jobs
from mainplate.plugins.asking import Declaring
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.running import Spawned
from mainplate.service import Service
from mainplate.snapshots import branch_named
from mainplate.tools import JobsInTurn
from mainplate.tools import job_tools

PATIENCE = timedelta(seconds=20)

# A range of ports per worker, so tests running side by side never race each other for one: the
# reconciler skips a port something holds, but between finding one free and listening on it another
# worker could take it, and that is a refusal rather than a retry.
WORKER = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw") or 0)
LOWEST = 39000 + 40 * WORKER
HIGHEST = LOWEST + 39

# The port the servers below listen on inside, which is outside every worker's range on purpose: the
# sandbox's network is its own, so any port is free in there, and what the console listens on outside
# is then a port of its own choosing rather than the same number.
INSIDE = 8000

# A page the server below serves, so a fetch that came back with it came back from inside.
PAGE = "hello from inside the sandbox"
SERVING = (
    f"mkdir -p built && printf '{PAGE}' > built/index.html && cd built && python3 -m http.server $PORT --bind 127.0.0.1"
)


@pytest.fixture
async def running(service: Service, workspaces: Workspaces) -> AsyncIterator[Service]:
    """A console that keeps jobs, stopped at the end without recording an end, as a console stopping does."""
    jobs = Jobs(
        checkpointer=service.checkpointer,
        workspaces=workspaces,
        host="127.0.0.1",
        lowest=LOWEST,
        highest=HIGHEST,
        delivering=service.durable.deliver,
    )
    try:
        yield replace(service, workspaces=workspaces, jobs=jobs)
    finally:
        await jobs.aclose()


def jobs_of(service: Service) -> Jobs:
    if service.jobs is None:
        raise RuntimeError("this console keeps no jobs")
    return service.jobs


async def planted(service: Service, workspaces: Workspaces, chosen: Choice | None = None) -> str:
    """A session whose checkout is on disk, without driving a pass to plant it; see `test_commands`."""
    chosen = chosen if chosen is not None else replace(DEFAULT_CHOICE, repository=FIXTURE)
    session = await started(service, "hello", chosen)
    await workspaces.plant(session.id, FIXTURE, branch=chosen.branch or branch_named(session.id))
    return session.id


async def serving(service: Service, session: str, said: str = SERVING, port: int = INSIDE) -> str:
    """One job that serves, started as the model's `start_job` would start it."""
    return await jobs_of(service).start(session, said, port, None, None)


async def fetched(port: int) -> str:
    """
    What the server answers on `port` of this host, waited for until it is up.

    A poll on the actual signal, the server answering, rather than a sleep: how long a Python
    server takes to start is the machine's business.
    """

    def get() -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as answered:
            return answered.read().decode()

    async with asyncio.timeout(PATIENCE.total_seconds()):
        while True:
            try:
                return await asyncio.to_thread(get)
            except urllib.error.URLError, ConnectionError, TimeoutError:
                await asyncio.sleep(0.05)


async def ended(service: Service, session: str, entry: str) -> Result:
    """What one job came to, waited for on the record appearing; see `test_commands.settled`."""
    async with asyncio.timeout(PATIENCE.total_seconds()):
        while True:
            held = (await service.checkpointer.load(session)).get(result_key(entry))
            if held is not None:
                return parse_result(held)
            await asyncio.sleep(0.01)


async def port_of(service: Service, session: str, entry: str) -> int:
    port = await service.opened_on(session, entry)
    assert port is not None, "a job that serves records the port it is opened on"
    return port


class TestWhatTheStoreHolds:
    def test_the_port_is_named_after_the_entry(self) -> None:
        """Literal, for the reason every key test is: a session on disk keeps the shape it was written in."""
        assert listening_key(inbox_key(7)) == "listening:inbox:00000000000000000007"

    def test_a_port_travels_with_the_entry_it_is_about(self) -> None:
        """What carries it into a fork with the job it belongs to, by shape, as a result is carried."""
        assert entry_of(listening_key(inbox_key(7))) == inbox_key(7)

    def test_a_job_with_no_result_is_one_that_should_be_running(self) -> None:
        recorded = {
            inbox_key(1): recorded_job("make serve", port=8000),
            listening_key(inbox_key(1)): recorded_listening(3917),
        }
        (wanted,) = wanted_in(recorded)
        assert (wanted.entry, wanted.said, wanted.port) == (inbox_key(1), "make serve", 3917)

    def test_a_job_with_a_result_is_one_that_has_ended(self) -> None:
        recorded = {
            inbox_key(1): recorded_job("make serve"),
            result_key(inbox_key(1)): recorded_result(Result(status=0, output="")),
        }
        assert wanted_in(recorded) == ()

    def test_an_archived_session_wants_no_jobs(self) -> None:
        recorded = {inbox_key(1): recorded_job("make serve"), ARCHIVED_KEY: records.Archived(at=WHEN).recorded()}
        assert wanted_in(recorded) == ()

    def test_a_command_recorded_before_there_were_jobs_is_never_started_again(self) -> None:
        """One an older console was killed under has no result, and running it now would surprise everybody."""
        recorded = {inbox_key(1): records.Command(said="sleep 30").recorded()}
        assert wanted_in(recorded) == ()

    def test_a_job_draws_as_a_command_with_where_it_is_opened(self) -> None:
        recorded = {
            inbox_key(1): recorded_job("make serve", port=8000, asked="0:call-1"),
            listening_key(inbox_key(1)): recorded_listening(3917),
        }
        (block,) = transcript(recorded).panels[-1].blocks
        assert isinstance(block, Command)
        assert block.job == Job(port=3917, started_by="the model")

    @pytest.mark.parametrize(
        ("job", "said"),
        [
            pytest.param(records.Job(), None, id="the person"),
            pytest.param(records.Job(asked="0:call-1"), "the model", id="the model"),
            pytest.param(
                records.Job(asked="setup:s:repository:setup:0", plugin="repository:setup"),
                "repository:setup",
                id="a plugin",
            ),
        ],
    )
    def test_what_started_a_job_is_read_off_what_the_start_recorded(self, job: records.Job, said: str | None) -> None:
        assert started_by(job) == said


class TestWhereABrowserOpensIt:
    @pytest.mark.parametrize(
        ("scheme", "sent", "opened"),
        [
            pytest.param("http", ((b"host", b"vm.example:8100"),), "http://vm.example:3917/", id="the host it reached"),
            pytest.param(
                "http",
                ((b"host", b"127.0.0.1:8100"), (b"x-forwarded-host", b"vm.exe.xyz"), (b"x-forwarded-proto", b"https")),
                "https://vm.exe.xyz:3917/",
                id="the host and scheme a proxy says the browser used",
            ),
            pytest.param(
                "http", ((b"host", b"[::1]:8100"),), "http://[::1]:3917/", id="an IPv6 host keeps its brackets"
            ),
        ],
    )
    def test_the_host_is_the_browser_s_and_the_port_the_job_s(
        self, scheme: str, sent: tuple[tuple[bytes, bytes], ...], opened: str
    ) -> None:
        assert opened_at(scheme, sent, 3917) == opened


class TestServingFromASandboxWithNoNetwork:
    async def test_a_server_inside_answers_on_the_port_outside(self, running: Service, workspaces: Workspaces) -> None:
        """
        The whole mechanism: the session's network is off, so the server can only listen on the
        sandbox's own loopback, and what answers on this host is the listener handed in to it.
        """
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        assert PAGE in await fetched(await port_of(running, session, entry))

    async def test_it_is_handed_the_port_it_listens_on_inside(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session, "echo listening on $PORT")
        assert f"listening on {INSIDE}" in (await ended(running, session, entry)).output

    async def test_a_job_that_serves_nothing_is_handed_no_port(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, 'echo "port:${PORT:-none}"', None, None, None)
        assert "port:none" in (await ended(running, session, entry)).output
        assert await running.opened_on(session, entry) is None

    async def test_one_that_exits_on_its_own_records_how(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "echo up; exit 3", None, None, None)
        came = await ended(running, session, entry)
        assert (came.status, came.output.strip()) == (3, "up")

    async def test_stopping_one_records_what_it_printed_and_why(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        await fetched(await port_of(running, session, entry))
        assert await running.stop(session, entry)
        came = await ended(running, session, entry)
        assert "[stopped from the console]" in came.output

    async def test_stopping_one_frees_its_port(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        port = await port_of(running, session, entry)
        await fetched(port)
        await running.stop(session, entry)
        with pytest.raises((urllib.error.URLError, ConnectionError)):
            await asyncio.to_thread(urllib.request.urlopen, f"http://127.0.0.1:{port}/", timeout=2)

    async def test_stopping_one_that_is_not_running_says_so(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        assert not await running.stop(session, inbox_key(99))

    async def test_a_port_already_taken_is_the_job_s_result(self, running: Service, workspaces: Workspaces) -> None:
        """
        A recorded port is listened on again rather than moved, so a link somebody has open keeps
        working; one somebody else holds by then is a job that cannot come back, said as its result
        rather than as a panel that says it is running for ever.
        """
        session = await planted(running, workspaces)
        # A short wait for the port, since nothing here is letting go of it and the default would be
        # the whole of this test's time.
        impatient = replace(jobs_of(running), release=timedelta(milliseconds=100))
        with socket.create_server(("127.0.0.1", 0)) as holding:
            taken = holding.getsockname()[1]
            appended = await running.checkpointer.append(session, recorded_job(SERVING, port=INSIDE))
            await running.checkpointer.supply(session, listening_key(appended.key), recorded_listening(taken))
            await impatient.reconcile(session)
            came = await ended(running, session, appended.key)
        assert came.status == UNFINISHED
        assert f"port {taken} could not be listened on" in came.output

    async def test_one_before_the_checkout_is_planted_says_so(self, running: Service) -> None:
        session = await started(running, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        entry = await jobs_of(running).start(session.id, "true", None, None, None)
        came = await ended(running, session.id, entry)
        assert came.status == UNFINISHED
        assert "checkout is made on its first turn" in came.output


class TestWhatOutlivesTheConsole:
    async def test_a_console_stopping_does_not_end_a_job(self, running: Service, workspaces: Workspaces) -> None:
        """The record says it should be running, so the next console must find it still wanted."""
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        await fetched(await port_of(running, session, entry))
        await jobs_of(running).aclose()
        assert [each.entry for each in wanted_in(await running.checkpointer.load(session))] == [entry]

    async def test_the_next_console_starts_it_again_on_the_same_port_and_says_so(
        self, running: Service, workspaces: Workspaces
    ) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        port = await port_of(running, session, entry)
        await fetched(port)
        await jobs_of(running).aclose()
        again = Jobs(
            checkpointer=running.checkpointer, workspaces=workspaces, host="127.0.0.1", lowest=LOWEST, highest=HIGHEST
        )
        try:
            await again.reconcile(session, restarted=True)
            assert PAGE in await fetched(port)
            printed = again.output(session, entry)
        finally:
            await again.aclose()
        assert printed is not None
        assert printed.startswith(RESTARTED)

    async def test_archiving_a_session_stops_its_jobs(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        await fetched(await port_of(running, session, entry))
        await running.archive(session)
        await jobs_of(running).reconcile(session)
        came = await ended(running, session, entry)
        assert "[its session was archived]" in came.output

    async def test_a_fork_does_not_start_its_parent_s_job(self, running: Service, workspaces: Workspaces) -> None:
        """A second process on a second port, in a checkout the branch has not planted, is not a fork."""
        session = await planted(running, workspaces)
        await serving(running, session)
        forked = await running.fork(session, at=1, chosen=replace(DEFAULT_CHOICE, repository=FIXTURE))
        assert forked is not None
        assert wanted_in(await running.checkpointer.load(forked.id)) == ()


def tools_of(service: Service, session: str) -> FunctionToolset[None]:
    return job_tools(JobsInTurn(jobs=jobs_of(service), session=session, turn=0))


class TestTheModelsJobs:
    async def test_a_start_run_again_finds_the_job_it_started(self, running: Service, workspaces: Workspaces) -> None:
        """A pass that fell over before the call's return was recorded runs the call again; one job, not two."""
        session = await planted(running, workspaces)
        first = await jobs_of(running).start(session, "sleep 30", None, "0:call-1", None)
        second = await jobs_of(running).start(session, "sleep 30", None, "0:call-1", None)
        assert first == second

    async def test_a_listing_says_what_a_job_printed(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "echo hello from a job", None, "0:call-1", None)
        await ended(running, session, entry)
        (listed,) = await jobs_of(running).listed(session)
        assert (listed.entry, listed.ended) == (entry, 0)
        assert "hello from a job" in listed.output

    async def test_waiting_on_one_returns_once_it_has_ended(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "sleep 0.2; echo done", None, None, None)
        waited = await jobs_of(running).waited(session, entry, PATIENCE)
        assert waited is not None
        assert (waited.ended, waited.output.strip()) == (0, "done")

    async def test_waiting_on_one_that_goes_on_says_it_is_still_running(
        self, running: Service, workspaces: Workspaces
    ) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "echo started; sleep 30", None, None, None)
        waited = await jobs_of(running).waited(session, entry, timedelta(milliseconds=200))
        assert waited is not None
        assert waited.ended is None

    async def test_the_tools_refuse_a_job_this_session_does_not_have(
        self, running: Service, workspaces: Workspaces
    ) -> None:
        """A refusal the model can act on, naming where the real ids are, rather than a fault."""
        session = await planted(running, workspaces)
        read_job = tools_of(running, session).tools["read_job"].function
        with pytest.raises(ModelRetry, match="list_jobs"):
            await read_job("inbox:nonsense")  # type: ignore[arg-type]


async def steers(service: Service, session: str) -> list[str]:
    """What was steered into a session, which is where a job's ending is told to the model."""
    return [
        at.what.said for at in posted_in(await service.checkpointer.load(session)) if isinstance(at.what, records.Steer)
    ]


class TestTellingTheModelAJobEnded:
    """
    So a model that started a job and ended its turn without waiting is woken by the job ending,
    rather than leaving a session nobody answers until somebody types.
    """

    async def test_one_the_model_started_is_told_when_it_ends(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "echo the tests passed", None, "0:call-1", None)
        await ended(running, session, entry)

        (told,) = await steers(running, session)
        assert f"job {entry}" in told
        assert "exit 0" in told
        assert "the tests passed" in told

    async def test_one_the_model_stopped_itself_is_not_news(self, running: Service, workspaces: Workspaces) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "sleep 30", None, "0:call-1", None)

        assert await jobs_of(running).stop(session, entry, "stopped by the model", False)

        assert await steers(running, session) == []

    async def test_one_the_person_ran_is_never_told(self, running: Service, workspaces: Workspaces) -> None:
        """Recorded and not told, as everything the person runs is."""
        session = await planted(running, workspaces)
        entry = await running.run(session, "echo mine")
        assert entry is not None
        await ended(running, session, entry)

        assert await steers(running, session) == []


class TestTheConsoleRoutes:
    async def test_opening_one_is_sent_to_its_port_on_this_host(
        self, running: Service, workspaces: Workspaces, assets: Inventory
    ) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session)
        port = await port_of(running, session, entry)
        async with calling(build_app(already(running), assets)) as caller:
            answered = await caller.get(f"/sessions/{session}/jobs/{entry}")
        assert (answered.status, answered.location) == (303, f"http://testserver:{port}/")

    async def test_opening_one_that_has_ended_says_so(
        self, running: Service, workspaces: Workspaces, assets: Inventory
    ) -> None:
        session = await planted(running, workspaces)
        entry = await serving(running, session, "exit 0")
        await ended(running, session, entry)
        async with calling(build_app(already(running), assets)) as caller:
            answered = await caller.get(f"/sessions/{session}/jobs/{entry}")
        assert answered.status == 404

    async def test_what_a_running_job_has_printed_is_plain_text(
        self, running: Service, workspaces: Workspaces, assets: Inventory
    ) -> None:
        session = await planted(running, workspaces)
        entry = await jobs_of(running).start(session, "echo '<b>printed</b>'; sleep 30", None, None, None)
        async with asyncio.timeout(PATIENCE.total_seconds()):
            while "printed" not in (running.printed(session, entry) or ""):
                await asyncio.sleep(0.01)
        async with calling(build_app(already(running), assets)) as caller:
            answered = await caller.get(f"/sessions/{session}/jobs/{entry}/output")
        assert answered.headers["content-type"] == "text/plain; charset=utf-8"
        assert "<b>printed</b>" in answered.text

    async def test_a_session_with_its_network_on_serves_on_the_host_itself(
        self, running: Service, workspaces: Workspaces
    ) -> None:
        """No relay: the job shares the host's network, so it listens on the port it is opened on."""
        chosen = replace(DEFAULT_CHOICE, repository=FIXTURE, isolation=replace(DEFAULT_CHOICE.isolation, network=True))
        session = await planted(running, workspaces, chosen)
        entry = await serving(running, session, port=HIGHEST)
        assert await port_of(running, session, entry) == HIGHEST
        assert PAGE in await fetched(HIGHEST)


class TestAJobASetupDeclares:
    """A plugin's setup declaring a job, through a real pass, which is how a session gets one without asking."""

    def declaring(self, tmp_path: Path, declared: str) -> Declaring:
        plugin = tmp_path / "declares"
        plugin.write_text(f"#!/bin/sh\ncat >/dev/null\necho '{declared}'\n")
        plugin.chmod(0o755)
        return Declaring(
            console=(Installed(tier=Tier.USER, name="declares", path=plugin),), speaking=Spawned(environ={})
        )

    async def set_up(self, service: Service, declaring: Declaring) -> str:
        """A session past its settings step, by the three moments `test_plugins.set_up` drives."""
        session = await service.start(DEFAULT_CHOICE)
        body = conversing(Provider().endpoints(), INSTRUCTIONS, declaring=declaring, jobs=service.jobs)
        await passing(service, session.id, body)
        await replace(service, declaring=declaring).settle(session.id, {}, 0)
        await passing(service, session.id, body)
        return session.id

    async def test_it_is_started_once_the_session_is_set_up(self, running: Service, tmp_path: Path) -> None:
        declaring = self.declaring(tmp_path, '{"jobs": [{"command": "echo from setup; sleep 30"}]}')

        session = await self.set_up(running, declaring)

        (wanted,) = wanted_in(await running.checkpointer.load(session))
        assert wanted.job.plugin == "user:declares"
        async with asyncio.timeout(PATIENCE.total_seconds()):
            while "from setup" not in (running.printed(session, wanted.entry) or ""):
                await asyncio.sleep(0.01)

    async def test_a_later_pass_does_not_start_it_again(self, running: Service, tmp_path: Path) -> None:
        declaring = self.declaring(tmp_path, '{"jobs": [{"command": "sleep 30"}]}')
        session = await self.set_up(running, declaring)

        await passing(
            running,
            session,
            conversing(Provider().endpoints(), INSTRUCTIONS, declaring=declaring, jobs=running.jobs),
        )

        assert len(wanted_in(await running.checkpointer.load(session))) == 1
