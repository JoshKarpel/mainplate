from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from conftest import Provider
from pydantic_ai.messages import TextPart
from pydantic_ai.models import ModelRequestParameters
from without_durability.interfaces import claimed
from without_durability.memory import MemoryCheckpointer
from without_durability.stepwise import Run

from mainplate.durability import CheckpointedModel
from mainplate.durability import Stepping
from mainplate.durability import StepwiseDurability
from mainplate.durability import StreamingNotRecorded
from mainplate.durability import current_stepping
from mainplate.durability import parse_model_response
from mainplate.durability import stepping

WORKFLOW = "a-workflow"


@pytest.fixture
def checkpointer() -> MemoryCheckpointer:
    """Dicts rather than a file: what is under test here is the naming and the replay, not a store."""
    return MemoryCheckpointer()


@asynccontextmanager
async def a_pass(checkpointer: MemoryCheckpointer) -> AsyncIterator[Run]:
    """One claimed pass, released on the way out so the next one in a test can take the workflow."""
    holder = await claimed(checkpointer, WORKFLOW)
    try:
        yield Run(holder=holder, checkpointer=checkpointer, recorded=await checkpointer.load(WORKFLOW))
    finally:
        await checkpointer.release(holder)


class TestNamingASteppingScope:
    async def test_requests_are_numbered_within_the_scope(self, checkpointer: MemoryCheckpointer) -> None:
        async with a_pass(checkpointer) as run:
            scope = Stepping(run=run, prefix="turn:3")
            assert [scope.key("model") for _ in range(3)] == ["turn:3:model:0", "turn:3:model:1", "turn:3:model:2"]

    async def test_each_kind_is_numbered_on_its_own(self, checkpointer: MemoryCheckpointer) -> None:
        async with a_pass(checkpointer) as run:
            scope = Stepping(run=run, prefix="turn:0")
            assert (scope.key("model"), scope.key("tool"), scope.key("model")) == (
                "turn:0:model:0",
                "turn:0:tool:0",
                "turn:0:model:1",
            )

    async def test_a_scope_is_only_in_force_inside_its_block(self, checkpointer: MemoryCheckpointer) -> None:
        async with a_pass(checkpointer) as run:
            assert current_stepping.get() is None
            with stepping(run, "turn:0") as scope:
                assert current_stepping.get() is scope
            assert current_stepping.get() is None


class TestRecordingAModelRequest:
    async def test_the_provider_is_reached_once_and_replayed_afterwards(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        agent = provider.agent()
        for _ in range(3):
            async with a_pass(checkpointer) as run:
                with stepping(run, "turn:0"):
                    answered = await agent.run("hello")
            assert answered.output == "answer 1"
        assert provider.asked == 1

    async def test_the_recorded_value_is_what_the_store_will_take(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """A `ModelResponse` is not JSON, so what is recorded has to already be lowered to it."""
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0"):
                await provider.agent().run("hello")
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        assert parse_model_response(recorded).parts == [TextPart("answer 1")]

    async def test_outside_a_scope_the_capability_does_nothing(self, provider: Provider) -> None:
        agent = provider.agent()
        first = await agent.run("hello")
        second = await agent.run("hello")
        assert (first.output, second.output) == ("answer 1", "answer 2")
        assert provider.asked == 2

    async def test_a_streamed_request_is_refused_rather_than_left_unrecorded(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        async with a_pass(checkpointer) as run:
            model = CheckpointedModel(provider.model(), scope=Stepping(run=run, prefix="turn:0"))
            with pytest.raises(StreamingNotRecorded):
                async with model.request_stream([], None, ModelRequestParameters()):
                    pass  # pragma: no cover - the refusal happens on the way in


class TestTheCapabilityItself:
    def test_it_is_innermost_so_every_other_capability_has_already_run(self) -> None:
        assert StepwiseDurability().get_ordering().position == "innermost"

    def test_it_is_not_loadable_from_a_spec(self) -> None:
        """A spec cannot carry a checkpoint, so an agent built from one would record nothing."""
        assert StepwiseDurability.get_serialization_name() is None
