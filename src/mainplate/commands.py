# Pushing a session's branch, and what reading a running process into a record takes, which `jobs.py`
# shares.
#
# A command a person types is a job, kept by `jobs.py` in the session's sandbox; see there for why it
# is confined. What is left here is the one run that is not in a sandbox at all: a push needs this
# console's credential, and nothing that holds one may read the configuration a session wrote. So the
# branch crosses into the store as a bundle and the store pushes it, as this console, and the result
# is recorded where a command's would be.
#
# Nothing here is ever told to a model. The record exists so the page can draw a push and a reload
# can find it again. See the key scheme in `conversation.py`.

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta

from without_durability.interfaces import Checkpointer

from mainplate.conversation import Result
from mainplate.conversation import recorded_result
from mainplate.conversation import result_key
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
    The pushes this process currently has running, one of the two places it holds work in flight;
    `Jobs` in `jobs.py` is the other, on the same terms.

    That is a genuine exception to what `Service` otherwise is, and it is stated rather than hidden.
    Everything else the console does is a read of the database or a write to it, so two processes over
    one file agree by construction. A running push is a *place*: it belongs to this process, it does
    not survive a restart, and nothing else can see it.

    What keeps that from spreading is that the place holds no answers. The push and its result are
    both in the checkpoint, so a page renders the same thing whichever process is asked, and this set
    exists only so a shutdown can reap what it started.

    **A push is not a job**, which is why it is still here: it runs against the store in this process
    rather than in a session's sandbox, and it is not idempotent in the sense a job has to be, so a
    console stopping records it as unfinished rather than pushing again on the next start.
    """

    checkpointer: Checkpointer
    running: dict[asyncio.Task[None], Slot] = field(default_factory=dict)

    def push(self, slot: Slot, checkout: Checkout, url: str, branch: str) -> None:
        """
        Push the session's branch to the repository, and record what came of it like a command.

        Its own arm rather than a command somebody types, because the one thing a command in the
        sandbox cannot do is the one thing this is: reach the repository as the person. The branch
        crosses into the store and the store pushes it, so no configuration the session wrote is read
        by anything holding a credential. See `Checkout.push`.
        """
        self.scheduled(slot, lambda holding: pushing(checkout, url, branch, holding))

    def scheduled(self, slot: Slot, work: Callable[[bytearray], Awaitable[Result]]) -> None:
        """
        Start one push as a task this holds until it ends.

        Held by slot so `aclose` can say what became of a task that never started, and dropped by the
        task's own callback so what is held is only ever what is still running.
        """
        task = asyncio.create_task(self.record(slot, work), name=f"command {slot.session} {slot.entry}")
        self.running[task] = slot
        task.add_done_callback(lambda done: self.running.pop(done, None))

    async def record(self, slot: Slot, work: Callable[[bytearray], Awaitable[Result]]) -> None:
        """
        The whole of one run: do it, then say what happened, whichever way it ended.

        The buffer is out here rather than inside `pushing` so that a cancelled push still records
        what it managed to say, which is the only reason this catches cancellation at all, since
        `aclose` would otherwise record the same thing without it.

        `shield`, because the write is the point and the database is still open at that moment: the
        tasks are cancelled inside `open_store`'s own `finally`, before the connection is closed.

        A failure to push at all is recorded as the result rather than raised. Nothing is watching
        this task, so an exception here would be a log line and a panel that never resolves.
        """
        holding = bytearray()
        try:
            came = await work(holding)
        except asyncio.CancelledError:
            await asyncio.shield(self.result(slot, self.stopped(holding)))
            raise
        except SnapshotFailed as failed:
            came = self.stopped(holding, f"this could not be pushed: {failed}")
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
