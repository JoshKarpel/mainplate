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
from typing import Any

import pytest
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from without_asgi import ASGIApp
from without_durability.interfaces import claimed
from without_durability.interfaces import inbox_key
from without_durability.stepwise import Run
from without_durability.stepwise import resume

from mainplate import records
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
from mainplate.conversation import PLUGINS_KEY
from mainplate.conversation import REPOSITORY_PLUGINS_KEY
from mainplate.conversation import Ended
from mainplate.conversation import conversing
from mainplate.conversation import heard_key
from mainplate.conversation import opened_key
from mainplate.forge import Clones
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.plugins.asking import Enrolled
from mainplate.plugins.asking import recorded_registration
from mainplate.service import Service
from mainplate.sessions import Session
from mainplate.sessions import read_session
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

    asking: ModelSettings = field(default_factory=ModelSettings)
    """
    What this stand-in's format needs to be told, which for a stand-in is nothing by default.

    A field rather than a second class, because what a test wants to vary is one answer: whether the
    wire contributes settings of its own and whether they reach the request beside the session's.
    """

    def model(self, name: str) -> FunctionModel:
        return self.responding

    async def listed(self) -> tuple[Listed, ...]:
        return self.offers

    def caching(self) -> ModelSettings:
        """
        Answered rather than left off, which is the protocol doing its job.

        A wire that did not answer this would be a type error rather than a session quietly paying
        full input price for the whole conversation on every request.
        """
        return self.asking


class Watching(FunctionModel):
    """
    A stand-in model that records the settings each request was handed.

    Asserting on `Agent.model_settings` would only say the agent was constructed with something. What
    is worth pinning is that the value survives the capability stack and reaches the request, since
    `StepwiseDurability` wraps every model this console builds.

    Here rather than in one suite because two want it now: what a session asked of a model and what
    its wire asked for are two questions with one way of answering them.
    """

    def __init__(self) -> None:
        super().__init__(lambda messages, info: ModelResponse(parts=[TextPart("ok")]))
        self.seen: list[ModelSettings | None] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.seen.append(model_settings)
        return await super().request(messages, model_settings, model_request_parameters)


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

    def body(self, allowance: int | None = None) -> Callable[[Run], Awaitable[Ended]]:
        """
        The workflow body, over the stand-in endpoints.

        Unbounded by default, so a test that is about a conversation drives a whole turn in one pass
        and says nothing about how a pass is cut. What the console ships is one request per pass, and
        the tests that are about *that* ask for it by name; `TestWhatOnePassDoes` is where the two
        are pinned against each other.
        """
        return conversing(self.endpoints(), INSTRUCTIONS, allowance=allowance)


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
    carried: list[int] = field(default_factory=list)
    """
    How many messages each request brought, which is `Provider`'s field for `Provider`'s reason.

    What a turn *records* is only the messages it produced, so a checkpoint cannot answer how much
    history a request carried. This is the only reading that can, and it is what a test asserting on
    a cleared context has to ask.
    """

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            answering = self.script[min(self.asked, len(self.script) - 1)]
            self.asked += 1
            self.carried.append(len(messages))
            return answering

        return FunctionModel(respond)

    def endpoints(self) -> Wires:
        shared = self.model()
        return Wires(by_endpoint={name: Stand(offers=OFFERED[name], responding=shared) for name in CONFIG.endpoints})


@dataclass(slots=True)
class Refusing:
    """
    A stand-in model that answers a while and then will not answer at all.

    `after` is how many requests it takes before the refusal, so a test can put one anywhere in a
    turn rather than only at its first request - which is the case that matters, since the key a
    refusal is recorded under is the refused *request's* and not the turn's.

    `asked` counts what actually reached it, which is the whole assertion for the replay: a pass that
    re-ran a request already known to be refused would show up here and nowhere else.
    """

    status: int
    after: int = 0
    asked: int = 0

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.asked += 1
            if self.asked > self.after:
                raise ModelHTTPError(status_code=self.status, model_name="fixture", body="prompt is too long")
            return calls(("read", {"path": "README.md"}))

        return FunctionModel(respond)

    def endpoints(self) -> Wires:
        shared = self.model()
        return Wires(by_endpoint={name: Stand(offers=OFFERED[name], responding=shared) for name in CONFIG.endpoints})

    def body(self) -> Callable[[Run], Awaitable[Ended]]:
        return conversing(self.endpoints(), INSTRUCTIONS)


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


# Checkpoint values, as the records this console actually writes. A test that wrote a bare string
# under a key would be asserting against a shape the store no longer holds, so these are built with
# the console's own records: a change to one fails here rather than passing quietly.


def recorded_turn(*said: object) -> dict[str, object]:
    """
    Message dicts written out by hand, as the record a turn keeps them in.

    Every response is stamped with `WHEN` where it does not stamp itself, which is the same moment
    `Ticking` starts at. `ModelResponse.timestamp` otherwise defaults to the moment it was
    constructed, so a fixture without one is the moment the test ran: `Transcript.answered_at` reads
    it, and any assertion over a whole transcript would be a comparison against the clock.

    Written on the way in rather than patched on the way out, so what the fixture holds is what a
    real record holds and nothing downstream has to know these came from a test.
    """
    stamped = [
        {"timestamp": WHEN.isoformat(), **one} if isinstance(one, dict) and one.get("kind") == "response" else one
        for one in said
    ]
    return records.Messages(messages=stamped).recorded()


def answered_with(response: object) -> dict[str, object]:
    """One response dict, as the record the step that made that request keeps it in."""
    return records.Response(response=response).recorded()


def came_back(returned: object, took: float | None = None) -> dict[str, object]:
    """
    What one call returned and how long it took, as the one record holding both.

    Both in one record rather than two keys, which is what the envelope bought: a duration could not
    sit beside a bare tool return without being indistinguishable from a tool that returned a field
    of that name.
    """
    return records.Returned(returned=returned, took=None if took is None else timedelta(seconds=took)).recorded()


def said_at(turn: int, said: str, *, entry: int | None = None, forget: bool = False) -> dict[str, object]:
    """
    A turn's opening message, which since the inbox is two records rather than one.

    The entry the store filed, and the cursor the turn recorded when it took it. A test writing only
    one of them would be writing a checkpoint no pass could produce: a message nobody opened a turn
    on, or a turn that opened on nothing.

    The entry is numbered after the turn where a test says nothing, which is right for the ordinary
    fixture of one message per turn and is what `entry` is for where it is not.
    """
    at = inbox_key(turn if entry is None else entry)
    return {at: records.Prompt(said=said, forget=forget).recorded(), opened_key(turn): at}


def steered_at(entry: int, said: str) -> dict[str, object]:
    """Something said into a turn already running, as the entry it arrived as."""
    return {inbox_key(entry): records.Steer(said=said).recorded()}


def ran_at(entry: int, said: str) -> dict[str, object]:
    """Something the person ran themselves, as the entry it arrived as."""
    return {inbox_key(entry): records.Command(said=said).recorded()}


def read_to(turn: int, at: int, entry: int) -> dict[str, object]:
    """How far down the inbox a turn had read when it made its `at`-th request, as the cursor saying so."""
    return {heard_key(turn, at): inbox_key(entry)}


def snapshotted(tree: str | None) -> dict[str, object]:
    """What the worktree held before one request, as the record of that snapshot."""
    return records.Tree(tree=tree).recorded()


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


async def registered(service: Service, session: str, *enrolled: Enrolled) -> None:
    """
    What a pass writes when somebody answers a session's settings step.

    **A registration is the whole of what takes a session past that step**, so a suite running without
    a worker has to write one or every page it renders is the step rather than a transcript. Both keys,
    because both are written together, and `enrolled` goes under the console's own: what these tests
    want is a session whose rail draws a card, and which tier it came back under is the fork's question
    rather than any reader's.

    **Write-once, so this has to be the *first* registration a session gets.** The store keeps the
    value a key was first given, so calling it after an empty one records nothing and leaves the rail
    empty with no failure to point at.
    """
    await service.checkpointer.supply(session, PLUGINS_KEY, recorded_registration(enrolled))
    await service.checkpointer.supply(session, REPOSITORY_PLUGINS_KEY, recorded_registration(()))


async def started(
    service: Service,
    said: str,
    chosen: Choice = DEFAULT_CHOICE,
    title: str | None = None,
    *enrolled: Enrolled,
) -> Session:
    """
    A session on `chosen` with `said` in it, which is what creating one used to be in one call.

    **Creating a session and saying the first thing in it are separate calls now**, because a
    repository's plugins cannot be named until its worktree is planted and none of them is run until
    the settings step is answered. Most tests here are about something else entirely and want a
    session with a message in it, so the pair is written once rather than at every call site - and a
    test that is about the split says so by calling `Service.start` itself.

    It skips the step by recording what answering it on a console with nothing declared writes, which
    is two empty registrations. Written rather than implied, because a registration is the whole of
    what takes a session past that step: a session holding turns and no registration is one still
    owing an answer, which is what a fork is and is not what these tests are about. A test that wants
    plugins actually running presses the button, through `set_up` in `test_plugins.py` or `loaded` in
    `test_app.py`, and both start from `Service.start` rather than from here for that reason.
    """
    session = await service.start(chosen, title)
    await service.say(session.id, said)
    await registered(service, session.id, *enrolled)
    found = await read_session(service.database, session.id)
    return found if found is not None else session


async def passing(service: Service, session: str, body: Any) -> Any:
    """
    One pass of a session, claimed and released, which the suites with no worker drive by hand.

    Here rather than in one of them because two now want it: setting a session's plugins up happens
    in a pass, so a test about the *press* has to be able to run the pass the press asked for.
    """
    holder = await claimed(service.checkpointer, session)
    try:
        return await resume(holder, service.checkpointer, body)
    finally:
        await service.checkpointer.release(holder)


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
