from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Never

import pytest
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel
from without_asgi import ASGIApp
from without_durability.stepwise import Run

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.agent import Wires
from mainplate.agent import agent_for
from mainplate.app import build_app
from mainplate.app import open_store
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import Offering
from mainplate.config import Config
from mainplate.config import Endpoint
from mainplate.conversation import conversing
from mainplate.forge import Clones
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.service import Service
from mainplate.snapshots import Worktree

# The first moment a test's clock reads, so a test that renders a session's row asserts on a value
# it chose rather than on the wall clock. Not midnight and not the epoch, so a formatting bug that
# drops a field is visible.
WHEN = datetime(2031, 3, 14, 15, 9, 26, tzinfo=UTC)

# How far the test clock moves between reads. It advances rather than standing still because two
# sessions created in one test have to be distinguishable *by time*, which is what the list is
# ordered by: a frozen clock would leave the order to the tiebreak on a random id.
TICK = timedelta(seconds=1)


@dataclass(slots=True)
class Ticking:
    """A clock that starts at `WHEN` and moves one tick every time it is read."""

    reading: datetime = WHEN

    def __call__(self) -> datetime:
        was = self.reading
        self.reading += TICK
        return was


# Long enough that no pass in this suite is ever fenced for taking too long, which would turn a
# slow machine into a failing one.
LEASE = timedelta(seconds=30)

# Two endpoints, so a test can tell "the default" from "a choice somebody made" and so the model
# picker has something to cascade between. No models here, because an endpoint no longer names any:
# what is on offer comes from `OFFERED` below, which is what a stand-in endpoint says when asked.
CONFIG = Config(
    default="here",
    endpoints={
        "here": Endpoint(format="anthropic", api_key=SecretStr("sk-test")),
        "gateway": Endpoint(format="openai", url="https://llm.example.invalid/v1"),
    },
)

# What the stand-in endpoints say they serve. Two families under one endpoint, because grouping the
# picker by family is a rendering with a branch in it, and a single-family fixture would exercise
# the branch without ever showing it doing anything. The same model under both endpoints is the
# real case a gateway produces, where one wire and the other reach the same upstream.
OFFERED: dict[str, tuple[Listed, ...]] = {
    "here": (
        Listed(id="ripe/fast", label="Fast", provider="ripe"),
        Listed(id="ripe/careful", label="Careful", provider="ripe"),
        Listed(id="wide/steady", label="Steady", provider="wide"),
    ),
    "gateway": (Listed(id="wide/steady", label="Steady", provider="wide"),),
}

CHOICES = tuple(Choice(endpoint=name, model=model.id) for name, models in OFFERED.items() for model in models)

# The default is the first thing the default endpoint listed, since `CONFIG` names no `default_model`.
DEFAULT_CHOICE = Choice(endpoint=CONFIG.default, model=OFFERED[CONFIG.default][0].id)


def offering(name: str) -> Offering:
    """One endpoint as the catalogue holds it, taking its endpoint's facts from `CONFIG` itself."""
    declared = CONFIG.endpoints[name]
    return Offering(endpoint=name, format=declared.format, url=declared.url, models=OFFERED[name])


CATALOGUE = Catalogue(offered={name: offering(name) for name in OFFERED}, default=DEFAULT_CHOICE)

INSTRUCTIONS = "Answer as a fixture would."


@dataclass(slots=True)
class Stand:
    """
    One stand-in endpoint: what it says it serves, and the one model everything over it resolves to.

    It satisfies `Endpoint` structurally rather than by inheritance, which is the whole point of
    that being a protocol: a test needs no provider, no client, and no network to be something
    `agent_for` and `discover` can both use.
    """

    offers: tuple[Listed, ...]
    responding: FunctionModel

    def model(self, name: str) -> FunctionModel:
        return self.responding

    async def listed(self) -> tuple[Listed, ...]:
        return self.offers


@dataclass(slots=True)
class Provider:
    """
    A stand-in model, and what was actually asked of it.

    `asked` is the point rather than an extra: what these tests exist to assert is how *often* the
    provider is reached, since a response replayed from the checkpoint must not be a second call,
    and two identical answers could not tell those apart. Every answer names its own ordinal for
    the same reason. `carried` is how many messages each request brought, which is how a test says
    a turn saw the conversation before it rather than starting a fresh one.
    """

    asked: int = 0
    carried: list[int] = field(default_factory=list)

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.asked += 1
            self.carried.append(len(messages))
            return ModelResponse(parts=[TextPart(f"answer {self.asked}")])

        return FunctionModel(respond)

    def endpoints(self) -> Wires:
        """
        Every endpoint `CONFIG` declares, all answered by one stand-in model.

        One model behind every endpoint rather than one each, so `asked` counts calls across the
        whole configuration: what a test wants to know is how often the provider was reached, not
        which of two identical fakes reached it.
        """
        shared = self.model()
        return Wires(by_endpoint={name: Stand(offers=OFFERED[name], responding=shared) for name in CONFIG.endpoints})

    def agent(self) -> Agent[None, str]:
        """
        The agent a pass would build for the default choice, for a test driving one directly.

        Built through `agent_for` rather than assembled here, so a test standing in for half a pass
        is running the capability stack a real pass runs and not a second one that resembles it.
        """
        return agent_for(self.endpoints(), DEFAULT_CHOICE, INSTRUCTIONS)

    def body(self) -> Callable[[Run], Awaitable[Never]]:
        """The workflow body, over the stand-in endpoints."""
        return conversing(self.endpoints(), INSTRUCTIONS)


@dataclass(slots=True)
class Scripted:
    """
    A stand-in model that answers with responses written out in advance.

    What `Provider` cannot do, and what a turn with tools in it needs: a response holding
    `ToolCallPart`s, followed by a different response once the results come back. The script is
    consumed in order and the last entry answers every request after it, so a test says what the
    interesting responses are and not how many times the agent will loop.

    `asked` is the point, exactly as it is on `Provider`: a replayed request must not reach the
    model again, and only a count can tell a replay from a second identical answer.
    """

    script: tuple[ModelResponse, ...]
    asked: int = 0

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            answering = self.script[min(self.asked, len(self.script) - 1)]
            self.asked += 1
            return answering

        return FunctionModel(respond)

    def endpoints(self) -> Wires:
        shared = self.model()
        return Wires(by_endpoint={name: Stand(offers=OFFERED[name], responding=shared) for name in CONFIG.endpoints})


def calls(*wanted: tuple[str, Mapping[str, object]]) -> ModelResponse:
    """
    One response asking for a batch of tool calls, each named by an id a test can assert on.

    A `Mapping` rather than a `dict`, because `dict` is invariant in its value type and every
    caller here writes a literal of strings: typed as `dict[str, object]` this would refuse
    `{"what": "alpha"}` at every call site.
    """
    return ModelResponse(
        parts=[
            ToolCallPart(tool_name=tool, args=dict(arguments), tool_call_id=f"call-{tool}-{at}")
            for at, (tool, arguments) in enumerate(wanted)
        ]
    )


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def catalogues() -> Catalogues:
    """A holder of its own per test, so one test replacing what it holds cannot reach another."""
    return Catalogues(current=CATALOGUE)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "mainplate.db"


@pytest.fixture
async def service(database: Path, catalogues: Catalogues) -> AsyncIterator[Service]:
    """A store on its own file, with no worker: nothing answers a session unless a test does."""
    async with open_store(database, LEASE, catalogues) as opened:
        yield Service(
            database=opened.database,
            durable=opened.durable,
            checkpointer=opened.checkpointer,
            catalogues=catalogues,
            now=Ticking(),
        )


@pytest.fixture
def app(service: Service) -> ASGIApp:
    """
    The console over a store nothing is working, which is what makes these tests deterministic.

    A worker beside them would answer a session at a moment no test chose, so an assertion about a
    pending turn would pass or fail on how fast the machine is. What the worker does is tested
    where it can be driven a pass at a time, in `test_conversation`.
    """
    return build_app(already(service))


async def run(*arguments: str, cwd: Path) -> str:
    """One command, run for its effect, raising what it said if it did not work."""
    process = await asyncio.create_subprocess_exec(
        *arguments, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{arguments} failed: {out.decode()}")
    return out.decode().strip()


# What a stand-in forge reaches, which is the repository below. `git clone` takes a path as readily
# as a URL, so a test needs no server to exercise the whole path a real session takes: reach a
# repository, clone it, plant a worktree of the clone.
FIXTURE = "test:fixture"

# And what a card for it is *called*, which is what a browser test presses: the radio inside a card
# is a pixel at zero opacity with no pointer events, so the label is the whole of the control.
FIXTURE_NAME = "me/fixture"


@pytest.fixture
async def worktree(tmp_path: Path) -> Worktree:
    """
    A real repository, because everything worth checking against one is what git actually does.

    A stand-in for git would be a second implementation of the thing under test, and the questions
    asked of this - does a gitignored file come across, does the reader's index move, does a tree
    survive `gc`, does a branch name resolve to today's commit - are exactly the ones only git can
    answer.

    Here rather than beside the snapshot tests because two suites need it now: what a command a
    person runs does to a worktree is the same kind of question, asked from the other end.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    await run("git", "init", "-q", "-b", "main", cwd=root)
    await run("git", "config", "user.email", "probe@example.invalid", cwd=root)
    await run("git", "config", "user.name", "probe", cwd=root)
    (root / ".gitignore").write_text(".env\nbuilt/\n")
    (root / "src" / "kept.txt").write_text("original\n")
    (root / ".env").write_text("SECRET=shh\n")
    (root / "built").mkdir()
    (root / "built" / "artifact.bin").write_text("generated\n")
    await run("git", "add", "-A", cwd=root)
    await run("git", "commit", "-qm", "first", cwd=root)
    return Worktree(root=root)


@pytest.fixture
async def workspaces(worktree: Worktree, tmp_path: Path) -> Workspaces:
    """
    Somewhere to clone the repository above and to plant each session's worktree of it.

    Both outside the repository deliberately, and these tests would not notice if they were not: a
    worktree planted *inside* it would be captured by the snapshots it exists to take, so every
    session would hold a copy of every other session's files.
    """
    reaching = Reaching(
        current=Reachable(
            repositories=(Repository(forge="test", key="fixture", name="me/fixture", url=str(worktree.root)),)
        )
    )
    return Workspaces(
        clones=Clones(root=tmp_path / "clones"),
        root=tmp_path / "worktrees",
        scratch=tmp_path / "scratch",
        reaching=reaching,
    )


def already(service: Service) -> Callable[[], AbstractAsyncContextManager[Service]]:
    """A lifespan over a service somebody else opened, so the fixture owns the file's lifetime."""

    @asynccontextmanager
    async def opening() -> AsyncIterator[Service]:
        yield service

    return opening
