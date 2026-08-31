from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel
from without_asgi import ASGIApp

from mainplate.app import build_app
from mainplate.app import open_store
from mainplate.durability import StepwiseDurability
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

    def agent(self) -> Agent[None, str]:
        return Agent(self.model(), name="test", capabilities=[StepwiseDurability()])


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "mainplate.db"


@pytest.fixture
async def service(database: Path) -> AsyncIterator[Service]:
    """A store on its own file, with no worker: nothing answers a session unless a test does."""
    async with open_store(database, LEASE) as opened:
        yield Service(
            database=opened.database,
            durable=opened.durable,
            checkpointer=opened.checkpointer,
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
