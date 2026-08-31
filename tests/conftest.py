from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
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
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel
from without_asgi import ASGIApp
from without_durability.stepwise import Run

from mainplate.agent import Choice
from mainplate.agent import Endpoints
from mainplate.agent import Listed
from mainplate.agent import agent_for
from mainplate.app import build_app
from mainplate.app import open_store
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.conversation import conversing
from mainplate.profiles import Config
from mainplate.profiles import Profile
from mainplate.service import Service

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

# Two profiles, so a test can tell "the default" from "a choice somebody made" and so the model
# picker has something to cascade between. No models here, because a profile no longer names any:
# what is on offer comes from `OFFERED` below, which is what a stand-in endpoint says when asked.
CONFIG = Config(
    default="here",
    profiles={
        "here": Profile(provider="anthropic", api_key=SecretStr("sk-test")),
        "gateway": Profile(provider="openai", base_url="https://llm.example.invalid/v1"),
    },
)

# What the stand-in endpoints say they serve. Two families under one profile, because grouping the
# picker by family is a rendering with a branch in it, and a single-family fixture would exercise
# the branch without ever showing it doing anything. The same model under both profiles is the
# real case a gateway produces, where one wire and the other reach the same upstream.
OFFERED: dict[str, tuple[Listed, ...]] = {
    "here": (
        Listed(id="ripe/fast", label="Fast", family="ripe"),
        Listed(id="ripe/careful", label="Careful", family="ripe"),
        Listed(id="wide/steady", label="Steady", family="wide"),
    ),
    "gateway": (Listed(id="wide/steady", label="Steady", family="wide"),),
}

CHOICES = tuple(Choice(profile=name, model=model.id) for name, models in OFFERED.items() for model in models)

# The default is the first thing the default profile listed, since `CONFIG` names no `default_model`.
DEFAULT_CHOICE = Choice(profile=CONFIG.default, model=OFFERED[CONFIG.default][0].id)

CATALOGUE = Catalogue(offered=OFFERED, default=DEFAULT_CHOICE)

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

    def endpoints(self) -> Endpoints:
        """
        Every profile `CONFIG` declares, all answered by one stand-in model.

        One model behind every profile rather than one each, so `asked` counts calls across the
        whole configuration: what a test wants to know is how often the provider was reached, not
        which of two identical fakes reached it.
        """
        shared = self.model()
        return Endpoints(by_profile={name: Stand(offers=OFFERED[name], responding=shared) for name in CONFIG.profiles})

    def agent(self) -> Agent[None, str]:
        """
        The agent a pass would build for the default choice, for a test driving one directly.

        Built through `agent_for` rather than assembled here, so a test standing in for half a pass
        is running the capability stack a real pass runs and not a second one that resembles it.
        """
        return agent_for(self.endpoints(), DEFAULT_CHOICE, INSTRUCTIONS)

    def body(self) -> Callable[[Run], Awaitable[Never]]:
        """The workflow body, over a catalogue that offers exactly what the stand-ins serve."""
        return conversing(self.endpoints(), Catalogues(current=CATALOGUE), INSTRUCTIONS)


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


def already(service: Service) -> Callable[[], AbstractAsyncContextManager[Service]]:
    """A lifespan over a service somebody else opened, so the fixture owns the file's lifetime."""

    @asynccontextmanager
    async def opening() -> AsyncIterator[Service]:
        yield service

    return opening
