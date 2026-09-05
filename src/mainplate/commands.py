# What a person runs themselves, beside the conversation rather than inside it.
#
# Every other effect in this console is the model's, and every one of them runs behind
# `sandbox.py`: a mount namespace with the clone bound read-only, no network, and the parent's
# environment cleared. A command here runs behind none of that, and that is the whole point rather
# than a gap. A session's `isolation` bounds what a *model* asked for, and the read-only clone is
# what stops a tool writing a history no panel shows and no fork inherits. `git commit` and
# `git push` are the person's to run, and confining them is what would make this pointless.
#
# The authority that adds is nothing new. A session on `Filesystem.EVERYTHING` already hands a model
# the store, every other conversation, and `config.yaml` with the credentials in it. What it does
# mean is that whoever can reach this console can run anything the service can, so reachability is
# the whole of what guards it - which was already true and is now worth saying out loud.
#
# Nothing here is ever told to a model. The record exists so the page can draw a run and a reload
# can find it again; putting it in the history is a message somebody writes. See the key scheme in
# `conversation.py`.

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from pathlib import Path

from without_durability.interfaces import Checkpointer

from mainplate.conversation import Result
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
from mainplate.settings import DEFAULT_PATIENCE

logger = logging.getLogger(__name__)

# How much of what a command said is kept. Enough for a test run's failures and small enough that a
# runaway loop cannot put a megabyte a second into the store.
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
    What a command said, cut to what the store will hold, keeping the **end**.

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
    test` killed on its own leaves the `pytest` under it running, holding the worktree and the port
    it bound. `start_new_session` is what makes the process its own group leader, so one signal
    reaches the whole tree.

    A process that has already exited raises rather than reporting nothing, which is an ordinary race
    against the wait below rather than a fault.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        return


async def ran(said: str, where: Path, patience: timedelta, into: bytearray) -> Result:
    """
    One command, run in `where` as this process's own user, and what came of it.

    A **shell** and not an argument vector, because what is in the box is what somebody would type:
    `git commit -m 'x' && git push` is one thought and two processes, and splitting it here would
    turn the obvious thing to type into a refusal. The shell is the reason this cannot be an
    allowlist of commands either, which is the same argument `sandbox.py` makes one level up.

    stderr into stdout, in the order they were written, which is what a terminal shows. Kept apart
    they interleave wrongly or not at all, and nobody has ever wanted a build's errors in a second
    column.

    A timeout kills the group and records what the command managed to say, rather than raising: a
    run that hit the bound is a result to read, not an error to explain.
    """
    began = asyncio.get_running_loop().time()
    process = await asyncio.create_subprocess_shell(
        said,
        cwd=where,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # Its own process group, so `kill` can reach whatever the shell started. See `kill`.
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
        into.extend(f"\n[killed after {patience.total_seconds():.0f}s]\n".encode())
    except asyncio.CancelledError:
        # The console is stopping. Kill the group rather than leaving a build orphaned, and let the
        # cancellation carry on: what to record is the caller's, which is where the buffer is.
        kill(process)
        raise
    return Result(
        status=status,
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
    Everything else the console does is a read of the store or a write to it, so two processes over
    one file agree by construction. A running command is a *place*: it belongs to this process, it
    does not survive a restart, and nothing else can see it.

    What keeps that from spreading is that the place holds no answers. The command and its result are
    both in the checkpoint, so a page renders the same thing whichever process is asked, and this set
    exists only so a shutdown can reap what it started.

    Deliberately **not** a worker. The session's own workflow is the conversation and is parked on
    `run.awaiting`, so a command cannot be a step of it; and a queue of its own would be a second
    durable mechanism to justify for something that is over in seconds and pinned to this machine
    anyway, since the worktree is on this disk.
    """

    checkpointer: Checkpointer
    patience: timedelta = DEFAULT_PATIENCE
    running: dict[asyncio.Task[None], Slot] = field(default_factory=dict)

    def start(self, slot: Slot, said: str, where: Path) -> None:
        """
        Run `said` in `where`, and record what came of it under the slot already claimed for it.

        Returns as soon as the command is scheduled, because somebody is waiting on the request that
        posted it and a build is minutes. What the page shows meanwhile is the command with no result
        beside it, which is what `Command.result is None` already means; the record landing is what
        fills it in, and the session's own change token is what tells every open page to look.

        This is the control-plane argument the worker already answers for cloning, one step along: a
        POST records an intention and something else does the slow part.
        """
        task = asyncio.create_task(self.record(slot, said, where), name=f"command {slot.session} {slot.entry}")
        self.running[task] = slot
        task.add_done_callback(lambda done: self.running.pop(done, None))

    async def record(self, slot: Slot, said: str, where: Path) -> None:
        """
        The whole of one run: do it, then say what happened, whichever way it ended.

        The buffer is out here rather than inside `ran` so that a cancelled command still records
        what it managed to say. `ran` fills it as the command writes, so this holds the partial
        output even when the call that was filling it never returned - which is the only reason this
        catches cancellation at all, since `aclose` would otherwise record the same thing without it.

        `shield`, because the write is the point and the store is still open at that moment: the
        tasks are cancelled inside `open_store`'s own `finally`, before the connection is closed.

        A failure to *run* the command at all - a worktree that is not there, a shell that cannot be
        started - is recorded as the result rather than raised. Nothing is watching this task, so an
        exception here would be a log line and a panel that never resolves.
        """
        holding = bytearray()
        try:
            came = await ran(said, where, self.patience, holding)
        except asyncio.CancelledError:
            await asyncio.shield(self.result(slot, self.stopped(holding)))
            raise
        # Named apart from the `OSError` below, because it is the common case rather than an odd one:
        # a session's worktree is planted by its *first pass*, so between creating one and its first
        # reply there is a repository, a `Run` on offer, and nowhere yet to run in. A bare repr says
        # none of that, and what a reader needs is what to do about it.
        #
        # Caught rather than checked for with an `is_dir` beforehand, which is both a syscall on the
        # event loop and a race: the answer could change between the look and the run. `strerror`
        # rides along so a `FileNotFoundError` that is *not* this - a machine with no shell - is not
        # quietly reported as a missing worktree.
        except FileNotFoundError as missing:
            came = self.stopped(
                holding,
                f"there is nothing at {where} to run in: a session's worktree is made on its first"
                f" turn, so a command sent before that has nowhere to go ({missing.strerror})",
            )
        except OSError as failed:
            came = self.stopped(holding, f"this could not be run: {failed!r}")
        await self.result(slot, came)

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
