# What replaying a turn costs, measured over the model-and-tool loop rather than argued about.
#
# `docs/design/durability.md` says a turn of *n* requests replays O(n²) steps across its passes, and
# that the loop's own work between steps is small. Both halves are claims, and this is what holds
# them against a clock: one turn of `--requests` round trips, driven twice over the same fixture
# repository and the same stand-in provider. Once unbounded, where a single pass makes every live
# request and replays nothing; once at the allowance the console ships, where pass *k* replays the
# *k-1* requests already recorded before making its own. The gap between the two totals is what the
# cut costs, and the per-pass series is what says how much of that gap grows with the turn.
#
# It reaches no provider and costs nothing to run, which is the point: a change to `loop.py` or
# `durability.py` can be measured before it is argued for.
#
# **The stand-in states its own usage**, and that is a control rather than a detail. Pydantic AI's
# `FunctionModel` estimates usage for a response that carries none by splitting every message in the
# history with a regular expression, which is itself quadratic across a turn: left to it, the
# dominant term this prints would be the fixture's own and not the console's.

from __future__ import annotations

import argparse
import asyncio
import cProfile
import pstats
import statistics
import tempfile
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Final

from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from without_durability.interfaces import claimed
from without_durability.stepwise import Completed
from without_durability.stepwise import Run
from without_durability.stepwise import resume

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.agent import Wires
from mainplate.app import open_store
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import Offering
from mainplate.conversation import PLUGINS_KEY
from mainplate.conversation import REPOSITORY_PLUGINS_KEY
from mainplate.conversation import Ended
from mainplate.conversation import conversing
from mainplate.forge import Clones
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.plugins.asking import recorded_registration
from mainplate.service import Service
from mainplate.snapshots import Worktree

INSTRUCTIONS: Final = "Answer as a fixture would."

ENDPOINT: Final = "here"
MODEL: Final = "ripe/fast"

CHOICE: Final = Choice(endpoint=ENDPOINT, model=MODEL)

OFFERED: Final = (Listed(id=MODEL, label="Fast", provider="ripe"),)

CATALOGUES: Final = Catalogues(
    current=Catalogue(
        offered={ENDPOINT: Offering(endpoint=ENDPOINT, format="anthropic", url=None, models=OFFERED)},
        default=CHOICE,
    )
)
"""
What the console would have discovered, written down instead.

Nothing a pass does reads it - what answers a session is the wire, and the wire here is a stand-in -
but a `Service` is not constructible without one, and a catalogue built from nothing would be a
console that offers no models at all.
"""

FORGE: Final = "test"
KEY: Final = "fixture"
REPOSITORY: Final = f"{FORGE}:{KEY}"

READ: Final = "src/kept.txt"

LINES: Final = 200
"""
How much the one tool hands back, as lines of the file the model asks to read.

A replayed return is parsed out of the record on every later pass, so how big one is is part of what
is being measured and a one-line fixture would measure nothing.
"""

USAGE: Final = RequestUsage(input_tokens=4000, output_tokens=120)
"""What each stand-in answer says it used, stated for the reason at the top of this file."""

LEASE: Final = timedelta(minutes=30)
"""
Long enough that no pass here is ever fenced for taking too long.

A turn of several hundred requests is minutes of passes on a slow machine, and a lease that elapsed
under one would turn a measurement into a redelivery.
"""


@dataclass(slots=True)
class Stand:
    """
    The one stand-in endpoint, which satisfies `Endpoint` structurally rather than by inheritance.

    That is the whole point of it being a protocol: measuring the loop needs no provider, no client
    and no network, only something `agent_for` will build an agent over.
    """

    responding: FunctionModel

    def model(self, name: str) -> FunctionModel:
        return self.responding

    async def listed(self) -> tuple[Listed, ...]:
        return (Listed(id=MODEL, label="Fast", provider="ripe"),)

    def caching(self) -> ModelSettings:
        return ModelSettings()


@dataclass(slots=True)
class Reading:
    """
    A stand-in provider whose turn is `requests - 1` reads and then an answer.

    A tool is what makes a turn worth more than one request, and `read` is the cheapest real one:
    what is being measured is the loop around it rather than the tool.

    Each call carries an id of its own, because a tool step is keyed by the call id within the turn:
    a hundred calls under one id would be one record written a hundred times rather than a hundred
    records, which is a replay of something else entirely.

    `asked` counts live requests and also numbers those ids, which works because a live request
    happens exactly once across every pass, in order: a replayed one never reaches here.
    """

    requests: int
    asked: int = 0

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            at = self.asked
            self.asked += 1
            if at >= self.requests - 1:
                return ModelResponse(parts=[TextPart("read them")], usage=USAGE)
            call = ToolCallPart(tool_name="read", args={"path": READ}, tool_call_id=f"call-{at}")
            return ModelResponse(parts=[call], usage=USAGE)

        return FunctionModel(respond)

    def endpoints(self) -> Wires:
        return Wires(by_endpoint={ENDPOINT: Stand(responding=self.model())})


@dataclass(frozen=True, slots=True)
class Timed:
    """One drive of a whole turn: how long each pass took, and how often the provider was reached."""

    passes: tuple[float, ...]
    asked: int

    @property
    def total(self) -> float:
        return sum(self.passes)


async def git(*arguments: str, cwd: Path) -> None:
    """One git command, run for its effect, raising what it said if it did not work."""
    process = await asyncio.create_subprocess_exec(
        "git", *arguments, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"git {arguments} failed: {out.decode()}")


async def planted(root: Path) -> Worktree:
    """
    A real repository, because what a snapshot costs is what git actually does.

    A stand-in for git would leave the per-request snapshot out of the measurement, and that
    snapshot is the one thing in a pass that leaves the process.
    """
    (root / "src").mkdir(parents=True)
    await git("init", "-q", "-b", "main", cwd=root)
    await git("config", "user.email", "probe@example.invalid", cwd=root)
    await git("config", "user.name", "probe", cwd=root)
    (root / READ).write_text("".join(f"line {at} of a file worth reading\n" for at in range(LINES)))
    await git("add", "-A", cwd=root)
    await git("commit", "-qm", "first", cwd=root)
    return Worktree(root=root)


async def drive(root: Path, requests: int, allowance: int | None) -> Timed:
    """
    Every pass it takes to answer one turn of `requests` round trips, timed a pass at a time.

    Each drive gets a store, a clone and a worktree of its own, so nothing one leaves behind is read
    by the next and the two allowances are compared over identical work.
    """
    provider = Reading(requests=requests)
    origin = await planted(root / "origin")
    workspaces = Workspaces(
        clones=Clones(root=root / "clones"),
        root=root / "worktrees",
        scratch=root / "scratch",
        reaching=Reaching(
            current=Reachable(repositories=(Repository(forge=FORGE, key=KEY, name="me/fixture", url=str(origin.root)),))
        ),
    )
    async with open_store(root / "mainplate.db", LEASE, CATALOGUES) as opened:
        service = Service(
            database=opened.database,
            durable=opened.durable,
            checkpointer=opened.checkpointer,
            catalogues=CATALOGUES,
            workspaces=workspaces,
            now=lambda: datetime.now(UTC),
        )
        session = await ready(service)
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces, allowance=allowance)
        took: list[float] = []
        while True:
            started = perf_counter()
            outcome = await one_pass(service, session, body)
            took.append(perf_counter() - started)
            if not isinstance(outcome, Completed):
                return Timed(passes=tuple(took), asked=provider.asked)


async def ready(service: Service) -> str:
    """
    A session with a message in it and its settings step already answered, as three calls.

    The registration is written rather than pressed for, which is what `conftest.registered` does
    and for the same reason: a session holding a message and no registration is one still owing an
    answer, and what is being measured is the turn after that answer.
    """
    session = await service.start(replace(CHOICE, repository=REPOSITORY))
    await service.say(session.id, "read the file")
    await service.checkpointer.supply(session.id, PLUGINS_KEY, recorded_registration(()))
    await service.checkpointer.supply(session.id, REPOSITORY_PLUGINS_KEY, recorded_registration(()))
    return session.id


async def one_pass(service: Service, session: str, body: Callable[[Run], Awaitable[Ended]]) -> object:
    """One pass at a session, claimed and released the way the worker does it."""
    holder = await claimed(service.checkpointer, session)
    try:
        return await resume(holder, service.checkpointer, body)
    finally:
        await service.checkpointer.release(holder)


@dataclass(frozen=True, slots=True)
class Fitted:
    """The per-pass series as a line: what one more recorded request costs, and what a pass costs."""

    per_request: float
    per_pass: float


def fitted(passes: tuple[float, ...]) -> Fitted:
    """
    Theil-Sen over the per-pass series, which is the median of every pair's gradient.

    Least squares rather than this was tried and cannot be trusted here: the series has outliers a
    mean cannot survive - a pass that lands on a git object pack or an sqlite checkpoint costs
    several times its neighbours - and one of those near the start flattens a real trend to zero.
    The cost is that it is quadratic in the number of passes, which at the sizes worth measuring is
    still far below the drive it is summarising.

    The first pass is dropped, because it clones the repository and plants the worktree: a cost a
    session pays once, and not a term in what replay costs.
    """
    points = [(float(at), took) for at, took in enumerate(passes) if at >= 1]
    gradients = [(later - took) / (beyond - at) for at, took in points for beyond, later in points if beyond > at]
    if not gradients:
        return Fitted(per_request=0.0, per_pass=statistics.fmean(took for _, took in points))
    gradient = statistics.median(gradients)
    return Fitted(per_request=gradient, per_pass=statistics.median(took - gradient * at for at, took in points))


def fifths(passes: tuple[float, ...]) -> list[float]:
    """
    The median pass in each fifth of the series, which is the trend with the outliers stepped over.

    Five numbers rather than one per pass, because what the series has to show is whether a pass
    late in a turn costs more than one early in it, and nobody can see that in a row of raw figures.
    """
    band = max(1, len(passes) // 5)
    return [statistics.median(passes[at : at + band]) for at in range(0, band * 5, band)]


async def measure(requests: int, profile: Path | None) -> None:
    """Both drives of one turn size, reported, and the profile of a third where one was asked for."""
    with tempfile.TemporaryDirectory() as whole:
        unbounded = await drive(Path(whole) / "unbounded", requests, allowance=None)
    with tempfile.TemporaryDirectory() as whole:
        cut = await drive(Path(whole) / "cut", requests, allowance=1)
    if unbounded.asked != requests or cut.asked != requests:
        raise SystemExit(f"the provider saw {unbounded.asked} and {cut.asked} requests, not {requests}")

    line = fitted(cut.passes)
    replay = line.per_request * requests * (requests - 1) / 2
    print(f"\n## a turn of {requests} requests")
    print(f"  one pass, nothing replayed  : {unbounded.total:8.3f}s")
    print(f"  a pass per request          : {cut.total:8.3f}s over {len(cut.passes)} passes")
    print(f"  the whole difference        : {cut.total - unbounded.total:8.3f}s")
    print(f"  per already-recorded request: {line.per_request * 1000:8.3f}ms")
    print(f"  per pass, whatever it replays:{line.per_pass * 1000:8.1f}ms")
    print(f"  replay alone, at n(n-1)/2   : {replay:8.3f}s")
    print("  median pass by fifth (ms)   : " + " ".join(f"{band * 1000:.1f}" for band in fifths(cut.passes)))

    if profile is not None:
        # A drive of its own, because a profiled pass is not a pass anybody times: the series above
        # is what the machine does, and this is only what it spent that on.
        with tempfile.TemporaryDirectory() as whole:
            await profiled(Path(whole) / "profiled", requests, profile)
        print(f"\n## where a turn of {requests} requests spends it, a pass each")
        pstats.Stats(str(profile)).sort_stats("cumulative").print_stats(WHERE, 25)


WHERE: Final = r"mainplate/(conversation|durability|loop)\.py|pydantic_ai|sqlite3|stepwise\.py"
"""
Which frames the profile is cut down to: this console's loop, the library under it, and the store.

A filter rather than the whole listing, because everything else in a profiled pass is asyncio's own
scheduling, and a reader looking for what a pass spends its time on has to skip all of it.
"""


async def profiled(root: Path, requests: int, into: Path) -> None:
    """The cut drive again under cProfile, so what the per-pass term is made of can be attributed."""
    watching = cProfile.Profile()
    watching.enable()
    try:
        await drive(root, requests, allowance=1)
    finally:
        watching.disable()
        watching.dump_stats(str(into))


SMALLEST: Final = 10
"""
The shortest turn worth driving, below which the summary is arithmetic on too little.

`fitted` drops the first pass and needs pairs of what is left, and `fifths` needs five bands to put
a median in: a turn of one request has neither and would fail inside `statistics` rather than here.
Ten is where a series first says anything, and a floor that is loud beats a report that is noise.
"""


async def main(requests: list[int], profile: Path | None) -> None:
    too_short = [size for size in requests if size < SMALLEST]
    if too_short:
        raise SystemExit(f"a turn of {too_short} replays too little to measure; ask for {SMALLEST} or more")
    for at, size in enumerate(requests):
        await measure(size, profile if at == len(requests) - 1 else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="What replaying a turn costs.")
    parser.add_argument("--requests", type=int, nargs="+", default=[20, 40, 80, 160], help="turn sizes to drive")
    parser.add_argument("--profile", type=Path, default=None, help="where to write cProfile stats for the last size")
    parsed = parser.parse_args()
    asyncio.run(main(parsed.requests, parsed.profile))
