# Keeping a session's jobs running: the one thing a session runs that outlives the call, the press
# or the turn that asked for it. A job is a command a person typed into `Run`, one the model started
# with `start_job`, or one a plugin's setup declared, and all three are kept by this module the same
# way.
#
# **The checkpoint says which jobs should be running and this makes the processes agree.** A job is a
# command entry carrying `job`, and one with no `result:{entry}` beside it is one that should be up;
# `reconcile` reads exactly that and starts what is missing and stops what is not wanted any more. So
# a console that restarts finds the same entries and starts them again, an archived session wants
# nothing and has its jobs stopped, and stopping one is writing its result. Nothing is queued and
# nothing has to be undone.
#
# **A job must therefore be idempotent**, and that is the cost of there being one behaviour rather
# than a setting: a job a console restart interrupted runs again from the top, in the checkout as it
# is then, with a line at the head of its output saying so. A server or a watcher is that already; a
# migration has to be written to be. The other cost is that the reconciler looks at a session when this
# process writes to it and at startup, so a job recorded by a worker in another process is noticed on
# the next look rather than at once.
#
# **A job's network is the one the session's commands have, and a server's port is handed in.** A
# session with the network off runs a job behind `--unshare-net`, where the only thing a server can
# listen on is its own loopback, which nothing outside can reach. So this console makes the listening
# socket itself, on its own host and a port of its own choosing, and passes it into the sandbox as an
# open file; a relay inside accepts on it and connects to the server's port there. A socket stays in
# the network namespace it was made in, so the relay holds one on the host's network without being
# able to make one, and the sandbox still reaches nothing. What crosses is that one descriptor, made
# here and handed over; nothing comes back for this side to act on, which is the line
# `docs/design/security.md` draws. A session with the network on shares the host's, so its server
# listens on the host directly and there is no relay.
#
# The other way to do this was a Unix socket the server listens on in a directory the sandbox can
# write, which this side would then connect to. It works, and it has this process opening a path a
# sandbox can plant a link at.

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from typing import Final

from without_durability.interfaces import Checkpointer

from mainplate.agent import reaching
from mainplate.commands import UNFINISHED
from mainplate.commands import drain
from mainplate.commands import kill
from mainplate.commands import trimmed
from mainplate.conversation import Result
from mainplate.conversation import Wanted
from mainplate.conversation import asked_in
from mainplate.conversation import choice_of
from mainplate.conversation import environment_in
from mainplate.conversation import jobs_in
from mainplate.conversation import listening_in
from mainplate.conversation import listening_key
from mainplate.conversation import recorded_job
from mainplate.conversation import recorded_listening
from mainplate.conversation import recorded_result
from mainplate.conversation import recorded_steer
from mainplate.conversation import result_in
from mainplate.conversation import result_key
from mainplate.conversation import wanted_in
from mainplate.conversation import working_in
from mainplate.forge import Workspaces
from mainplate.processes import reaped
from mainplate.records import Listening
from mainplate.sandbox import Confinement
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.sandbox import home_in
from mainplate.sandbox import scratch_of
from mainplate.sandbox import starting_at
from mainplate.tools.jobs import Listed

logger = logging.getLogger(__name__)

# What carries a connection from the listening socket this console handed in to the server's own
# port inside the sandbox. Run by the machine's `python3` rather than this project's interpreter,
# which is not in the sandbox, so it is written to the grammar the bundled plugins are held to.
#
# It tries IPv4 loopback and then IPv6, because "localhost" is either to a server, and a dev server
# that bound `::1` is one that answers nobody on `127.0.0.1`. A connection it cannot carry is closed,
# which a browser shows as the server not answering, which is what it is.
RELAY: Final = r"""
import socket
import sys
import threading

listening = socket.socket(fileno=int(sys.argv[1]))
port = int(sys.argv[2])


def pipe(source, sink):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            sink.sendall(data)
    except OSError:
        pass
    try:
        sink.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def inward():
    for host in ("127.0.0.1", "::1"):
        try:
            return socket.create_connection((host, port))
        except OSError:
            continue
    return None


def carry(outside):
    inside = inward()
    if inside is None:
        outside.close()
        return
    there = threading.Thread(target=pipe, args=(outside, inside), daemon=True)
    back = threading.Thread(target=pipe, args=(inside, outside), daemon=True)
    there.start()
    back.start()
    there.join()
    back.join()
    outside.close()
    inside.close()


while True:
    outside, _ = listening.accept()
    threading.Thread(target=carry, args=(outside,), daemon=True).start()
"""

# The relay in the background and the server in the foreground of one shell, so the server is the
# namespace's main process: when it exits, `bwrap` does, and `--unshare-pid` takes the relay with it.
# Positional parameters rather than interpolation, so nothing in the command is ever parsed twice.
BESIDE: Final = 'python3 -c "$1" "$2" "$3" & exec /bin/sh -c "$4"'

SHELL: Final = "/bin/sh"

# How long a port a server held is waited for once its sandbox is killed. Killing `bwrap` is
# immediate and the namespace behind it is not: the relay holding the listener is reaped a moment
# later, so a port listened on again straight away (a console restarting, a server stopped and
# started again) can still be held by the one that is going. Seconds, because what is being waited
# out is the kernel tearing a namespace down, not a program deciding to exit.
RELEASE: Final = timedelta(seconds=5)

# The line a job's output starts with when a console restart is why it is running, so whoever reads
# what it printed can tell a second run from a first. Said rather than recorded, because it is the
# reader of the output it is for.
RESTARTED: Final = "[started again: the console restarted while this was running]\n"

# Why a job could not start in a session whose checkout is not there yet, which is the common case
# rather than an odd one: a session's checkout is planted by its first pass, so between creating one
# and its first reply there is a repository, a `Run` on offer, and nowhere yet to run in.
UNPLANTED: Final = "there is no checkout to run in yet: a session's checkout is made on its first turn"

# How much of what a job printed rides along in the message telling the model it ended: enough to see
# whether it passed or what broke, short enough to cost little in a history it stays in for good.
ENDING_LINES: Final = 20


@dataclass(slots=True)
class Running:
    """
    One job this process has running: the process, what it has printed, and where it listens.

    A place, deliberately: it belongs to this process and holds what a running process is, and the
    record of it is in the checkpoint. `stopping` is why somebody ended it, set before the kill so
    the result the run writes says so.
    """

    process: asyncio.subprocess.Process
    said: bytearray
    outside: int | None
    began: float
    listens: bool
    """Whether this console made the listener, which is whether the outside port is ours to wait for."""
    entry: str
    line: str
    tells: bool
    """Whether the model is told when this ends, which is whether the model is what started it."""
    stopping: str | None = None
    telling: bool = True
    """Whether how it is being ended is news to the model: not where the model stopped it itself."""


@dataclass(slots=True)
class Jobs:
    """
    Every job this process has running, and the reconciler that keeps them agreeing with the
    checkpoint.

    **One of the two places this console holds work in flight**, beside `Commands`, which is a push,
    and held to its terms: the answers are in the checkpoint, so a page renders the same thing
    whichever process is asked, and this exists so the processes can be started, stopped, read and
    reaped. What a job is is *wanted* rather than *run*: one that is not running and has no result is
    one to start.

    `host` is where every listener is bound, which is the console's own address; `lowest` and
    `highest` bound which ports may be taken. See `Settings.serving_lowest`.
    """

    checkpointer: Checkpointer
    workspaces: Workspaces
    host: str
    lowest: int
    highest: int
    release: timedelta = RELEASE
    """How long a port is waited for once its job's sandbox is killed; see `RELEASE`."""
    delivering: Callable[[str, object], Awaitable[object]] | None = None
    """
    Where a message telling the model a job ended goes, which is `Durable.deliver`, or nothing for a
    console that tells it nothing.

    `deliver` rather than an append, because the point of the message is to *wake* a session: a model
    that started a test run and ended its turn without waiting is a session nobody will answer until
    somebody types, and the job ending is the thing that should.
    """
    running: dict[tuple[str, str], Running] = field(default_factory=dict)
    tasks: dict[tuple[str, str], asyncio.Task[None]] = field(default_factory=dict)
    looking: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def look(self, session: str) -> None:
        """
        Ask the reconciler to look at one session, for a write that does not start the job itself.

        A message to the loop rather than a reconcile in the caller, so a press answers at once and
        two writes to one session are two looks the lock puts in order.
        """
        self.looking.put_nowait(session)

    async def start(
        self,
        session: str,
        said: str,
        port: int | None,
        asked: str | None,
        plugin: str | None,
        *,
        online: bool = False,
    ) -> str:
        """
        Record that a job should be running, start it, and say which entry it is.

        **Started before this returns**, by reconciling the session here rather than leaving it to
        the loop, so the model's `start_job` can say what port it got and whether it came straight
        back down. A start that names itself in `asked` and was already recorded is handed the entry
        it made rather than a second job; see `records.Job.asked`.
        """
        recorded = await self.checkpointer.load(session)
        if asked is not None and (already := asked_in(recorded, asked)) is not None:
            return already
        entry = await self.checkpointer.append(
            session, recorded_job(said, port=port, asked=asked, plugin=plugin, online=online)
        )
        await self.reconcile(session)
        return entry.key

    async def stop(self, session: str, entry: str, why: str, telling: bool = True) -> bool:
        """
        End one job, so its result is written and nothing starts it again; whether there was one.

        A job this process is running is killed, and its own run writes the result with what it
        printed and `why` beneath it. One that is wanted and not running here, the instant before the
        reconciler starts it or in another process, has its result written from here instead, which
        is what keeps it from being started. `supply` keeps the first answer, so the two cannot both
        land.

        `telling` is whether the model is told it ended, where the model started it: not when the
        model is the one stopping it, since that is news to nobody.
        """
        recorded = await self.checkpointer.load(session)
        if not any(each.entry == entry for each in wanted_in(recorded)):
            return False
        if not await self.ended(session, entry, why, telling=telling):
            await self.checkpointer.supply(
                session, result_key(entry), recorded_result(Result(status=UNFINISHED, output=f"[{why}]\n"))
            )
        return True

    async def listed(self, session: str) -> tuple[Listed, ...]:
        """
        Every job this session has started, oldest first, as the job tools report them.

        Read from the checkpoint, with what a running one has printed so far from this process, since
        that is the one thing about a job that is not recorded until it ends.
        """
        recorded = await self.checkpointer.load(session)
        listed: list[Listed] = []
        for entry, was, job in jobs_in(recorded):
            ended = result_in(recorded, entry)
            outside = listening_in(recorded, entry)
            listed.append(
                Listed(
                    entry=entry,
                    said=was.said,
                    inside=job.port,
                    outside=outside,
                    ended=None if ended is None else ended.status,
                    output=ended.output if ended is not None else self.output(session, entry) or "",
                )
            )
        return tuple(listed)

    async def waited(self, session: str, entry: str, within: timedelta) -> Listed | None:
        """
        One job once it has ended, or as it is when `within` is up, whichever is first.

        Waited on its run's task rather than polled, so what ends the wait is the result being
        written; a job this process is not running is reported as it stands.
        """
        task = self.tasks.get((session, entry))
        if task is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), within.total_seconds())
        return next((each for each in await self.listed(session) if each.entry == entry), None)

    def output(self, session: str, entry: str) -> str | None:
        """What a job this process is running has printed so far, or nothing where none is running."""
        held = self.running.get((session, entry))
        return None if held is None else trimmed(bytes(held.said))

    async def reconcile(self, session: str, *, restarted: bool = False) -> None:
        """
        Make this session's running jobs the ones its checkpoint wants.

        Under one lock for every session, so a look from the loop and a start in a request cannot
        both start one job. Stopping what is no longer wanted comes first, so a port a stopped job
        held is free for one started in the same look. `restarted` is the look at startup, where
        every job started is one a console restart interrupted.
        """
        async with self.lock:
            recorded = await self.checkpointer.load(session)
            wanted = {each.entry: each for each in wanted_in(recorded)}
            for held, entry in list(self.running):
                if held == session and entry not in wanted:
                    await self.ended(session, entry, "its session was archived", telling=False)
            for entry, each in wanted.items():
                if (session, entry) not in self.tasks:
                    await self.started(session, each, recorded, restarted)

    async def ended(self, session: str, entry: str, why: str, *, telling: bool) -> bool:
        """Kill one job this process runs and wait for its run to say so; whether there was one."""
        held = self.running.get((session, entry))
        task = self.tasks.get((session, entry))
        if held is None or task is None:
            return False
        held.stopping = why
        held.telling = telling
        kill(held.process)
        await asyncio.shield(task)
        return True

    async def started(self, session: str, wanted: Wanted, recorded: Mapping[str, object], restarted: bool) -> None:
        """
        Start one wanted job: choose its port where it serves, record it, listen, and spawn its sandbox.

        **Anything that stops it starting is written as its result**, a port somebody else took, a
        session with nowhere to run, a checkout not planted yet, because nobody is waiting on this
        and a job that silently never came up is a panel saying it is running for ever.
        """
        chosen = choice_of(recorded)
        bwrap = self.workspaces.bwrap
        if chosen is None or bwrap is None:
            await self.refuse(session, wanted.entry, "this console has no sandbox to run a job in")
            return
        scratch = self.workspaces.scratch_at(session)
        reach = reaching(chosen.isolation, working_in(self.workspaces, session, chosen), scratch, bwrap)
        if reach.confinement is None:
            await self.refuse(session, wanted.entry, "this session has nowhere to run a job")
            return
        venue = Venue.CONNECTED if wanted.online else chosen.isolation.venue
        environment = dict(environment_in(recorded))
        outside: int | None = None
        listener: socket.socket | None = None
        if wanted.job.port is not None:
            environment["PORT"] = str(wanted.job.port)
            chose = (
                wanted.port
                if wanted.port is not None
                # With the network on there is no relay: the server listens on the host itself, so
                # the port it listens on is the port it is opened on.
                else wanted.job.port
                if venue is Venue.CONNECTED
                else self.free(wanted.job.port)
            )
            if chose is None:
                await self.refuse(session, wanted.entry, f"no port from {self.lowest} to {self.highest} is free")
                return
            kept = await self.checkpointer.supply(session, listening_key(wanted.entry), recorded_listening(chose))
            outside = Listening.model_validate(kept).port
            if venue is Venue.CONFINED:
                try:
                    listener = await listening(self.host, outside, self.release)
                except OSError as taken:
                    await self.refuse(
                        session, wanted.entry, f"port {outside} could not be listened on: {taken.strerror}"
                    )
                    return
        try:
            process = await self.spawned(reach.confinement, bwrap, venue, environment, wanted, listener)
        except FileNotFoundError:
            await self.refuse(session, wanted.entry, UNPLANTED)
            return
        except OSError as unstarted:
            await self.refuse(session, wanted.entry, f"this could not be started: {unstarted!r}")
            return
        finally:
            # The sandbox holds its own copy now, so this side's is closed: what keeps the port is
            # the job's namespace, and it is released when that ends.
            if listener is not None:
                listener.close()
        held = Running(
            process=process,
            said=bytearray(RESTARTED.encode() if restarted else b""),
            outside=outside,
            began=asyncio.get_running_loop().time(),
            listens=listener is not None,
            entry=wanted.entry,
            line=wanted.said,
            # The model's own start, which is the one whose end the model may be waiting on. A
            # setup's job is the session's rather than the model's, and the person's is theirs.
            tells=wanted.job.asked is not None and wanted.job.plugin is None,
        )
        self.running[(session, wanted.entry)] = held
        self.tasks[(session, wanted.entry)] = asyncio.create_task(
            self.watched(session, wanted.entry, held), name=f"job {session} {wanted.entry}"
        )

    def free(self, asked: int) -> int | None:
        """
        A port in the range nothing on this host is listening on, or nothing where every one is taken.

        The port the server listens on inside is tried first where it is in the range, so a server
        on 5173 is opened on 5173 when it can be and the link reads like the project's own.

        A bind to find out, released at once, which is a race with anything else starting on this
        machine in between; the listen in `started` is what decides, and a port lost there is the
        job's result rather than a guess about another one.
        """
        mine = {held.outside for held in self.running.values()}
        for port in (asked, *range(self.lowest, self.highest + 1)):
            if port in mine or not self.lowest <= port <= self.highest:
                continue
            try:
                with socket.create_server((self.host, port)):
                    return port
            except OSError:
                continue
        return None

    async def spawned(
        self,
        confinement: Confinement,
        bwrap: str,
        venue: Venue,
        environment: Mapping[str, str],
        wanted: Wanted,
        listener: socket.socket | None,
    ) -> asyncio.subprocess.Process:
        """
        The job's sandbox, started: the session's own namespace, lasting as long as the job.

        With a listener, the relay goes in beside the server and the listener's descriptor is the one
        thing passed in; without one the job is the whole of it. Its own process group, so `kill`
        reaches the namespace and everything in it.

        The checkout as the working directory as well as `--chdir`, so a checkout that is not there
        yet is a `FileNotFoundError` naming it rather than `bwrap`'s own complaint.
        """
        scratch = scratch_of(confinement)
        if scratch is not None:
            await asyncio.to_thread(lambda: scratch.mkdir(parents=True, exist_ok=True))
        home = home_in(confinement)
        at = starting_at(confinement)
        prefix = confined_by(confinement).argv(
            at=str(at),
            venue=venue,
            home=None if home is None else str(home),
            environment=environment,
        )
        command: tuple[str, ...] = (
            (SHELL, "-c", wanted.said)
            if listener is None or wanted.job.port is None
            else (SHELL, "-c", BESIDE, "job", RELAY, str(listener.fileno()), str(wanted.job.port), wanted.said)
        )
        return await asyncio.create_subprocess_exec(
            bwrap,
            *prefix,
            *command,
            cwd=at,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            pass_fds=() if listener is None else (listener.fileno(),),
        )

    async def watched(self, session: str, entry: str, held: Running) -> None:
        """
        One job's life: read what it prints, wait for it to end, and write its result.

        **A console stopping is not an end**, so cancellation kills the namespace and writes nothing:
        the entry still wants a job, and the next console starts it again. Every other way out,
        exiting on its own, being stopped, its session being archived, is the result, with the
        reason beneath what it printed where somebody ended it.
        """
        reading = held.process.stdout
        try:
            if reading is not None:
                await drain(reading, held.said)
            status = await held.process.wait()
        except asyncio.CancelledError:
            kill(held.process)
            reaped(held.process)
            await held.process.wait()
            self.forget(session, entry)
            raise
        why = "" if held.stopping is None else f"\n[{held.stopping}]\n"
        # Forgotten only once the result is down, so a look that lands in between finds the job
        # still held rather than wanted and not running, which it would start again. And the result
        # only once the port is free, so a job recorded as ended is one whose port can be taken.
        try:
            if held.listens and held.outside is not None:
                await released(self.host, held.outside, self.release)
            came = Result(
                status=status,
                output=trimmed(bytes(held.said)) + why,
                took=timedelta(seconds=asyncio.get_running_loop().time() - held.began),
            )
            await self.checkpointer.supply(session, result_key(entry), recorded_result(came))
        finally:
            self.forget(session, entry)
        # After the result, so a model told it ended and reading it back finds it ended.
        if held.tells and held.telling and self.delivering is not None:
            await self.delivering(session, recorded_steer(ending(held, came)))

    def forget(self, session: str, entry: str) -> None:
        """Let go of a job whose run is over, so what is held is only ever what is running."""
        self.running.pop((session, entry), None)
        self.tasks.pop((session, entry), None)

    async def refuse(self, session: str, entry: str, why: str) -> None:
        """A job that could not be started, ended with the reason as its result."""
        logger.info(f"job {entry} of {session} did not start: {why}")
        await self.checkpointer.supply(
            session, result_key(entry), recorded_result(Result(status=UNFINISHED, output=f"[{why}]\n"))
        )

    async def reconciling(self, sessions: Callable[[], Awaitable[Iterable[str]]]) -> None:
        """
        Every session once at startup, and then each session somebody wrote a job to.

        **A failed look is logged and the loop goes on**, since one session's broken checkpoint must
        not stop every other session's jobs being started; the next write to it looks again.
        """
        for session in await sessions():
            await self.looked(session, restarted=True)
        while True:
            await self.looked(await self.looking.get())

    async def looked(self, session: str, *, restarted: bool = False) -> None:
        try:
            await self.reconcile(session, restarted=restarted)
        except Exception:
            logger.exception(f"could not reconcile the jobs of {session}")

    async def aclose(self) -> None:
        """
        Stop every job this process runs **without recording an end**, so the next console starts
        them again; see `watched`.

        **The processes are ended from here as well as from their runs**, and that is not belt and
        braces: a run cancelled before it has had a turn on the loop never enters its body, so its own
        kill never happens and the namespace would outlive the console, holding its port and its pipes.
        Waited for afterwards, so nothing is left for the loop to collect after it has closed.
        """
        held = list(self.running.values())
        outstanding = list(self.tasks.values())
        for each in held:
            kill(each.process)
            reaped(each.process)
        for task in outstanding:
            task.cancel()
        for ended in await asyncio.gather(*outstanding, return_exceptions=True):
            if isinstance(ended, BaseException) and not isinstance(ended, asyncio.CancelledError):
                logger.warning(f"a job ended badly as the console stopped: {ended!r}")
        for each in held:
            await each.process.wait()


def ending(held: Running, came: Result) -> str:
    """
    What the model is told when a job it started ends: which, how, and its last lines.

    **Said as the console**, in the text itself, because a steer is drawn as what the person said:
    nothing else on the page or in the history would tell the two apart. Enough to act on without a
    `read_job`, which is the call a model that forgot the job would not think to make.
    """
    lines = came.output.rstrip("\n").split("\n")[-ENDING_LINES:]
    return (
        f"[The console: job {held.entry}, `{held.line}`, has ended with exit {came.status}. "
        f"Its last lines follow; `read_job` has more.]\n" + "\n".join(lines)
    )


async def listening(host: str, port: int, within: timedelta) -> socket.socket:
    """
    A socket listening on `port`, waiting out a namespace that is still letting go of it.

    The same listen tried again rather than another port, because the link somebody has open names
    this one; a port still held once `within` is up is somebody else's, and the error is raised.
    """
    loop = asyncio.get_running_loop()
    until = loop.time() + within.total_seconds()
    while True:
        try:
            return socket.create_server((host, port))
        except OSError:
            if loop.time() >= until:
                raise
            await asyncio.sleep(0.05)


async def released(host: str, port: int, within: timedelta) -> None:
    """Wait until nothing listens on `port` any more, for at most `within`, which is `listening` turned round."""
    try:
        (await listening(host, port, within)).close()
    except OSError:
        logger.warning(f"port {port} was still held {within.total_seconds():.0f}s after its job ended")
