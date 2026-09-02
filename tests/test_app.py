from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from calling import calling
from conftest import CONFIG
from conftest import DEFAULT_CHOICE
from conftest import OFFERED
from conftest import Stand
from conftest import already
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel

from mainplate.agent import Wires
from mainplate.app import build_app
from mainplate.app import open_console
from mainplate.settings import Settings

# A bound on each half of the exchange rather than a wait for it. Every assertion below is an
# event, so this only turns a wiring that never connects into a failure instead of a hung suite.
# It has to sit under this test's own timeout, or a wiring failure kills the xdist worker instead
# of reporting which wait went unanswered.
PATIENCE = 5


@pytest.mark.timeout(30)
async def test_a_message_posted_to_the_console_is_answered_by_the_worker(database: Path) -> None:
    """
    The one test of the wiring: the console records, the queue delivers, and the worker answers.

    Both halves are tested on their own elsewhere. What only this can catch is the two being
    assembled without being connected, which is a silent failure: the console would look exactly
    the same and no session would ever be answered.

    The signal is the model being reached, twice. The second one is what proves the *first* turn
    was recorded rather than merely started: the body only moves past turn 0 once `turn:0:messages`
    is in the checkpoint, and the model response it already recorded is replayed rather than asked
    again, so a second call can only be turn 1.

    It waits on a semaphore rather than sleeping, so the test runs as fast as the scheduler's poll
    allows and does not depend on how quick the machine is.

    The endpoints are stand-ins, so `open_console`'s own discovery runs for real over them: what
    this asserts includes that a console whose models are asked for rather than configured still
    reaches the point of taking traffic.
    """
    answered = asyncio.Semaphore(0)

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        answered.release()
        return ModelResponse(parts=[TextPart("an answer")])

    shared = FunctionModel(respond)
    endpoints = Wires(by_endpoint={name: Stand(offers=OFFERED[name], responding=shared) for name in CONFIG.endpoints})

    async with open_console(Settings(database=database), CONFIG, endpoints) as service:
        async with calling(build_app(already(service))) as caller:
            started = await caller.post(
                "/sessions",
                {"prompt": "hello", "endpoint": DEFAULT_CHOICE.endpoint, "model": DEFAULT_CHOICE.model},
            )
            assert started.status == 303
            session = started.location.rsplit("/", 1)[-1]
            async with asyncio.timeout(PATIENCE):
                await answered.acquire()

            # `next` rather than a plain Send, which now decides for itself: the first turn may not
            # have recorded its messages by the time the model has answered, and a message that
            # steered it would reach the pass already running rather than starting a second one.
            said = await caller.post(f"/sessions/{session}/messages", {"prompt": "and again", "disposition": "next"})
            assert said.status == 200
            async with asyncio.timeout(PATIENCE):
                await answered.acquire()
