# What a person runs themselves, beside the conversation rather than inside it.
#
# **Behind the same sandbox as the model's own `bash`**, and that is forced rather than chosen. A
# session's checkout owns its `.git`, so its configuration is the model's to write, and several of
# its keys name a program git runs: a hook, `core.fsmonitor`, a filter. A person's `git commit` run
# here unconfined would run whatever the model last put there, as the service user, with everything
# that user holds. So a command here reaches the checkout, its store read-only and the scratch, under
# the session's own network answer and the environment its setup recorded, exactly as the model's
# would; what differs is who typed it and that no model is told.
#
# What that takes away is the person's credentials, so `git push` in here has nothing of theirs to
# push with; with the network off it reaches nothing, and on exe.dev with it on it reaches the
# repository as this console does. Pushing is `Commands.push`'s instead: the branch crosses into the
# store as a bundle and the store pushes it, as this console, reading no configuration the session
# wrote.
#
# Nothing here is ever told to a model. The record exists so the page can draw a run and a reload
# can find it again; putting it in the history is a message somebody writes. See the key scheme in
# `conversation.py`.

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from pathlib import Path

from without_durability.interfaces import Checkpointer

from mainplate import records
from mainplate.conversation import Result
from mainplate.conversation import command_tree_key
from mainplate.conversation import posted_in
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
from mainplate.conversation import unfinished_in
from mainplate.durability import recorded_snapshot
from mainplate.ownership import owning
from mainplate.processes import reaped
from mainplate.sandbox import InACheckout
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.settings import DEFAULT_PATIENCE
from mainplate.snapshots import Checkout
from mainplate.snapshots import SnapshotFailed

logger = logging.getLogger(__name__)

# How much of what a command said is kept. Enough for a test run's failures and small enough that a
# runaway loop cannot put a megabyte a second into the database.
MOST_OUTPUT = 200_000

# Read in blocks rather than lines, so a command that writes a progress bar with no newline in it
# still fills the buffer instead of blocking until it finishes.
BLOCK = 64 * 1024

CUT = "[…output above this point was dropped]\n"

# What a command that was killed before it could exit is recorded as. Outside the range a process
# can exit with (0-255) and outside the negatives a signal produces, so it is not mistakable for
# either: it says the console never learned, which is a different thing from the command failing.
UNFINISHED = 1000


def trimmed(said: bytes) -> str:
    """
    What a command said, cut to what the database will hold, keeping the **end**.

    The end rather than the beginning, because the reason to cap at all is a command that ran away
    and what is worth reading about one of those is where it got to. The cost is real and is the
    other way round for a listing, where the first lines are the ones somebody wanted; a marker says
    which end went so a reader is never shown a fragment that looks like the whole.

    Decoded leniently, because this is whatever a program wrote to a pipe: a build tool that emits a
    stray byte must not be able to fail the recording of its own run.
    """
    text = said.decode("utf-8", errors="replace")
    return text if len(said) <= MOST_OUTPUT else f"{CUT}{text[-MOST_OUTPUT:]}"


async def drain(stream: asyncio.StreamReader, into: bytearray) -> None:
    """
    Everything a command writes, accumulated as it writes it, trimmed to a bound as it goes.

    Read into a buffer the *caller* owns, which is what makes a killed command still say something.
    A `communicate()` cancelled halfway hands back nothing at all, so the partial output of a run
    that timed out or that the console was stopped during would be lost exactly when it is most
    worth having.

    Trimmed at twice the bound rather than at every read, so a long run costs one copy per hundred
    blocks instead of one per block.
    """
    while chunk := await stream.read(BLOCK):
        into.extend(chunk)
        if len(into) > MOST_OUTPUT * 2:
            del into[: len(into) - MOST_OUTPUT]


def kill(process: asyncio.subprocess.Process) -> None:
    """
    The command and everything it started, which is why the group and not the process.

    A shell command is a shell, and what takes the time is almost always something it spawned: `just
    test` killed on its own leaves the `pytest` under it running, holding the checkout and the port
    it bound. `start_new_session` is what makes the process its own group leader, so one signal
    reaches the whole tree.

    A process that has already exited raises rather than reporting nothing, which is an ordinary race
    against the wait below rather than a fault.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        return


@dataclass(frozen=True, slots=True)
class Running:
    """
    Where a person's command runs: the session's own sandbox, as its `bash` would get it.

    `environment` is what the session's setup recorded, so `just test` finds the toolchain the
    repository's plugin installed, as it would for the model.
    """

    confinement: InACheckout
    venue: Venue
    environment: Mapping[str, str]

    @property
    def where(self) -> Path:
        """Where a command starts, which is the checkout's root, as it is for the model's `bash`."""
        return self.confinement.checkout.root


async def ran(said: str, running: Running, patience: timedelta, into: bytearray) -> Result:
    """
    One command, run in the session's sandbox, and what came of it.

    A **shell** and not an argument vector, because what is in the box is what somebody would type:
    `git add -A && git commit -m 'x'` is one thought and two processes, and splitting it here would
    turn the obvious thing to type into a refusal. The shell is the reason this cannot be an
    allowlist of commands either, which is the same argument `sandbox.py` makes one level up.

    stderr into stdout, in the order they were written, which is what a terminal shows. Kept apart
    they interleave wrongly or not at all, and nobody has ever wanted a build's errors in a second
    column.

    `cwd` is the checkout even though `--chdir` is what puts the command there, so a checkout that
    does not exist yet is a `FileNotFoundError` naming it rather than bwrap's own complaint.

    A timeout kills the group and records what the command managed to say, rather than raising: a
    run that hit the bound is a result to read, not an error to explain.
    """
    began = asyncio.get_running_loop().time()
    confinement = running.confinement
    await asyncio.to_thread(confinement.scratch.mkdir, parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        confinement.checkout.bwrap,
        *confined_by(confinement).argv(
            at=str(running.where),
            venue=running.venue,
            home=str(confinement.scratch),
            environment=running.environment,
        ),
        "/bin/sh",
        "-c",
        said,
        cwd=running.where,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # Its own process group, so `kill` can reach bwrap and, through its pid namespace, whatever
        # the shell started. See `kill`.
        start_new_session=True,
    )
    reading = process.stdout
    if reading is None:  # pragma: no cover - a pipe was asked for, so there is one
        raise RuntimeError("a command was started with no way to read what it says")
    try:
        async with asyncio.timeout(patience.total_seconds()):
            await drain(reading, into)
            status = await process.wait()
    except TimeoutError:
        kill(process)
        status = await process.wait()
        # The drain was cancelled part-way, and `wait` closes no pipe: one paused on a full buffer
        # never reads its end-of-file, and is collected later with its descriptor still held.
        reaped(process)
        into.extend(f"\n[killed after {patience.total_seconds():.0f}s]\n".encode())
    except asyncio.CancelledError:
        # The console is stopping. Kill the group rather than leaving a build orphaned, and let the
        # cancellation carry on: what to record is the caller's, which is where the buffer is.
        #
        # Then close the pipes and wait for the exit, because a transport nobody finishes is collected
        # later as a `ResourceWarning` raised into whatever is running then. The pipes are closed
        # synchronously, so a second cancellation landing in the wait still leaves none open; the
        # wait is what lets the subprocess transport see its exit and close itself before the loop
        # it belongs to does. `aclose` cancels each task once, so the wait is not itself cancelled.
        kill(process)
        reaped(process)
        await process.wait()
        raise
    return Result(
        status=status,
        output=trimmed(bytes(into)),
        took=timedelta(seconds=asyncio.get_running_loop().time() - began),
    )


async def pushing(checkout: Checkout, url: str, branch: str, into: bytearray) -> Result:
    """One push of the session's branch, as a result the page draws the way it draws a command's."""
    began = asyncio.get_running_loop().time()
    came = await checkout.push(url, branch)
    into.extend(came.stdout + came.stderr)
    return Result(
        status=came.code,
        output=trimmed(bytes(into)),
        took=timedelta(seconds=asyncio.get_running_loop().time() - began),
    )


@dataclass(frozen=True, slots=True)
class Slot:
    """
    Which recorded command a run is the other half of.

    Held beside the task rather than only inside the coroutine, and that is what makes a shutdown
    honest. A task cancelled *before it has started* never enters its own body, so nothing in there
    ever runs to say what became of it; carried out here, the slot is nameable by whoever is doing
    the cancelling. See `Commands.aclose`.
    """

    session: str
    entry: str
    """
    The inbox entry the command was delivered as, which is the whole of what names its result.

    Not a turn and a number, because a command belongs to whichever turn its entry landed in and
    nothing outside a pass can say which that is: a handler naming one would be answering from a page
    that may have moved on, where the entry is what it is for ever.
    """


@dataclass(slots=True)
class Commands:
    """
    The commands this process currently has running, which is the one place it holds work in flight.

    That is a genuine exception to what `Service` otherwise is, and it is stated rather than hidden.
    Everything else the console does is a read of the database or a write to it, so two processes over
    one file agree by construction. A running command is a *place*: it belongs to this process, it
    does not survive a restart, and nothing else can see it.

    What keeps that from spreading is that the place holds no answers. The command and its result are
    both in the checkpoint, so a page renders the same thing whichever process is asked, and this set
    exists only so a shutdown can reap what it started.

    Deliberately **not** a worker. The session's own workflow is the conversation and is parked on
    `run.awaiting`, so a command cannot be a step of it; and a queue of its own would be a second
    durable mechanism to justify for something that is over in seconds and pinned to this machine
    anyway, since the checkout is on this disk.
    """

    checkpointer: Checkpointer
    patience: timedelta = DEFAULT_PATIENCE
    running: dict[asyncio.Task[None], Slot] = field(default_factory=dict)

    def start(self, slot: Slot, said: str, running: Running) -> None:
        """
        Run `said` in the session's sandbox, and record what came of it under the slot already claimed.

        Returns as soon as the command is scheduled, because somebody is waiting on the request that
        posted it and a build is minutes. What the page shows meanwhile is the command with no result
        beside it, which is what `Command.result is None` already means; the record landing is what
        fills it in, and the session's own change token is what tells every open page to look.

        This is the control-plane argument the worker already answers for cloning, one step along: a
        POST records an intention and something else does the slow part.
        """
        self.scheduled(slot, lambda holding: ran(said, running, self.patience, holding), running.confinement.checkout)

    def push(self, slot: Slot, checkout: Checkout, url: str, branch: str) -> None:
        """
        Push the session's branch to the repository, and record what came of it like a command.

        Its own arm rather than a command somebody types, because the one thing a command in the
        sandbox cannot do is the one thing this is: reach the repository as the person. The branch
        crosses into the store and the store pushes it, so no configuration the session wrote is read
        by anything holding a credential. See `Checkout.push`.
        """
        self.scheduled(slot, lambda holding: pushing(checkout, url, branch, holding), checkout)

    def scheduled(self, slot: Slot, work: Callable[[bytearray], Awaitable[Result]], checkout: Checkout) -> None:
        """
        Start one run as a task this holds until it ends, which is what `start` and `push` share.

        Held by slot so `aclose` can say what became of a task that never started, and dropped by the
        task's own callback so what is held is only ever what is still running.
        """
        task = asyncio.create_task(self.serialized(slot, work, checkout), name=f"command {slot.session} {slot.entry}")
        self.running[task] = slot
        task.add_done_callback(lambda done: self.running.pop(done, None))

    async def serialized(self, slot: Slot, work: Callable[[bytearray], Awaitable[Result]], checkout: Checkout) -> None:
        """
        Wait outside an unfinished turn, then retain ownership through command and capture.

        A pass releases the OS lock between requests. The checkpoint supplies the longer turn
        lifetime, so a command cannot slip into that gap. Cancellation leaves an unfinished result
        rather than starting work that was merely waiting for ownership.
        """
        while True:
            async with owning(checkout.root.parent, slot.session):
                recorded = await self.checkpointer.load(slot.session)
                if "archived" in recorded:
                    await self.result(
                        slot, self.stopped(bytearray(), "the session was archived before this command ran")
                    )
                    return
                earlier = any(
                    isinstance(entry.what, records.Command)
                    and entry.key < slot.entry
                    and result_key(entry.key) not in recorded
                    for entry in posted_in(recorded)
                )
                if not unfinished_in(recorded) and not earlier:
                    came = await self.record(slot, work)
                    try:
                        ending = await checkout.capture(f"command {slot.entry} ended")
                        await self.checkpointer.supply(
                            slot.session, command_tree_key(slot.entry), recorded_snapshot(ending)
                        )
                    except (SnapshotFailed, OSError) as failed:
                        came = Result(
                            status=came.status,
                            output=f"{came.output}\n[ending capture failed: {failed}]\n",
                            took=came.took,
                        )
                    await self.result(slot, came)
                    return
            await asyncio.sleep(0.05)

    async def record(self, slot: Slot, work: Callable[[bytearray], Awaitable[Result]]) -> Result:
        """
        The whole of one run: do it, then say what happened, whichever way it ended.

        The buffer is out here rather than inside `ran` so that a cancelled command still records
        what it managed to say. `ran` fills it as the command writes, so this holds the partial
        output even when the call that was filling it never returned - which is the only reason this
        catches cancellation at all, since `aclose` would otherwise record the same thing without it.

        `shield`, because the write is the point and the database is still open at that moment: the
        tasks are cancelled inside `open_store`'s own `finally`, before the connection is closed.

        A failure to *run* the command at all - a checkout that is not there, a shell that cannot be
        started - is recorded as the result rather than raised. Nothing is watching this task, so an
        exception here would be a log line and a panel that never resolves.
        """
        holding = bytearray()
        try:
            came = await work(holding)
        except asyncio.CancelledError:
            await asyncio.shield(self.result(slot, self.stopped(holding)))
            raise
        # Named apart from the `OSError` below, because it is the common case rather than an odd one:
        # a session's checkout is planted by its *first pass*, so between creating one and its first
        # reply there is a repository, a `Run` on offer, and nowhere yet to run in. A bare repr says
        # none of that, and what a reader needs is what to do about it.
        #
        # Caught rather than checked for with an `is_dir` beforehand, which is both a syscall on the
        # event loop and a race: the answer could change between the look and the run. `strerror`
        # rides along so a `FileNotFoundError` that is *not* this - a machine with no shell - is not
        # quietly reported as a missing checkout.
        except FileNotFoundError as missing:
            came = self.stopped(
                holding,
                f"there is nothing at {missing.filename} to run in: a session's checkout is made on its"
                f" first turn, so a command sent before that has nowhere to go ({missing.strerror})",
            )
        except SnapshotFailed as failed:
            came = self.stopped(holding, f"this could not be pushed: {failed}")
        except OSError as failed:
            came = self.stopped(holding, f"this could not be run: {failed!r}")
        return came

    def stopped(self, holding: bytearray, why: str = "the console stopped while this was running") -> Result:
        """A run that produced no exit status, said as one, with whatever it managed to write."""
        return Result(status=UNFINISHED, output=f"{trimmed(bytes(holding))}\n[{why}]\n")

    async def result(self, slot: Slot, result: Result) -> None:
        """
        What became of one command, written where the command itself already is.

        `supply` is a compare-and-set, so this is safe to call twice for one slot and the first
        answer is the one that stands. That is what lets `aclose` write a blanket record for
        everything outstanding without checking, and lets the fuller record a cancelled run wrote for
        itself win over it.
        """
        await self.checkpointer.supply(slot.session, result_key(slot.entry), recorded_result(result))

    async def aclose(self) -> None:
        """
        Stop everything still running, and make sure each of them says so.

        **The record is written from here rather than left to the tasks**, and that is not belt and
        braces. A task cancelled before it has had a turn on the loop never enters its body at all,
        so the `except` in `record` cannot run and nothing would ever say what became of it: the
        command would show as running for ever, which nobody can tell from one that is. Writing it
        here covers that, and `supply` keeping the first value is what lets the run that *did* get to
        say something for itself - with the partial output it managed - keep its answer.

        Waited on before writing, so those fuller records land first.
        """
        outstanding = dict(self.running)
        for task in outstanding:
            task.cancel()
        for ended in await asyncio.gather(*outstanding, return_exceptions=True):
            if isinstance(ended, BaseException) and not isinstance(ended, asyncio.CancelledError):
                logger.warning(f"a command ended badly as the console stopped: {ended!r}")
        for slot in outstanding.values():
            await self.result(slot, self.stopped(bytearray()))
