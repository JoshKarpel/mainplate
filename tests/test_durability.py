from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal

import pytest
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import Scripted
from conftest import calls
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RequestUsage
from without_durability.interfaces import claimed
from without_durability.memory import MemoryCheckpointer
from without_durability.stepwise import Run

from mainplate.conversation import turn_of
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
            assert (scope.key("model"), scope.key("tree"), scope.key("model")) == (
                "turn:0:model:0",
                "turn:0:tree:0",
                "turn:0:model:1",
            )

    async def test_a_step_whose_order_is_not_fixed_is_named_by_its_own_identity(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        Tool calls in one batch run concurrently, so a counter would name a record by whichever
        won a race. Asking twice gives the same name, which a counter could never do.
        """
        async with a_pass(checkpointer) as run:
            scope = Stepping(run=run, prefix="turn:3")
            assert scope.identified("tool", "toolu_017") == scope.identified("tool", "toolu_017")
            assert scope.identified("tool", "toolu_017") == "turn:3:tool:toolu_017"

    async def test_a_tool_key_still_says_which_turn_it_belongs_to(self, checkpointer: MemoryCheckpointer) -> None:
        """What a fork reads to carry a prefix of a conversation across without knowing the kinds."""
        async with a_pass(checkpointer) as run:
            assert turn_of(Stepping(run=run, prefix="turn:7").identified("tool", "toolu_017")) == 7

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


class TestPricingARecordedRequest:
    """
    That a turn's cost is in the checkpoint, and in the record a turn being *watched* is read from.

    Pydantic AI prices a response too, in the agent graph, which runs after the step has already
    written. So without this the cost reaches `turn:{n}:messages` and never `turn:{n}:model:{i}`,
    and a turn has no cost until the instant it ends.
    """

    async def test_the_recorded_response_carries_what_it_cost(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pricer=lambda usage: Decimal("0.25")):
                await provider.agent().run("hello")
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        assert parse_model_response(recorded).usage.cost == Decimal("0.25")

    async def test_the_price_is_asked_of_the_usage_the_provider_reported(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        The whole usage rather than a total, because what a rate is applied to is four counts: a
        pricer handed only a sum could not charge cached tokens differently from fresh ones.

        Asserted against what the *record* ended up holding rather than against a figure written
        here, so the test says the two are the same usage instead of restating what a stand-in model
        happens to report.
        """
        seen: list[RequestUsage] = []

        def note(usage: RequestUsage) -> Decimal | None:
            seen.append(usage)
            return Decimal(1)

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pricer=note):
                await provider.agent().run("hello")
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        assert len(seen) == 1
        assert seen[0].output_tokens == parse_model_response(recorded).usage.output_tokens

    async def test_a_model_nothing_can_price_records_no_cost_rather_than_a_zero(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """`free` and `nobody published a price` are different claims, and only one may be drawn."""
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pricer=lambda usage: None):
                await provider.agent().run("hello")
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        assert parse_model_response(recorded).usage.cost is None

    async def test_a_cost_the_wire_itself_reported_is_never_overwritten(self, checkpointer: MemoryCheckpointer) -> None:
        """
        No provider says what it charged today, and the day one does its answer is the true one.

        Pydantic AI's own filling is written to leave an existing cost alone for exactly this, so
        this console must not be the layer that clobbers it with an estimate.
        """
        billed = ModelResponse(parts=[TextPart("answered")], usage=RequestUsage(cost=Decimal("9.99")))
        async with a_pass(checkpointer) as run:
            Stepping(run=run, prefix="turn:0", pricer=lambda usage: Decimal("0.01")).price(billed)
        assert billed.usage.cost == Decimal("9.99")

    async def test_with_no_pricer_a_response_is_left_exactly_as_it_arrived(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """A console with no reference configured records what it always did, which is no cost."""
        answered = ModelResponse(parts=[TextPart("answered")])
        async with a_pass(checkpointer) as run:
            Stepping(run=run, prefix="turn:0").price(answered)
        assert answered.usage.cost is None


class TestPuttingAMessageIntoARunningTurn:
    """
    Steering: what a person says into a turn that is already being answered.

    Pydantic AI's `enqueue` is what delivers it and is in-memory, so the whole risk is a resumed pass
    finding an empty queue and asking a different question than the one recorded. What these pin is
    that the step, and not the queue, is what decides.
    """

    async def test_what_a_request_was_told_is_recorded(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        async def waiting(already: int) -> Sequence[str]:
            return ("actually, check the tests too",)[already:]

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pending=waiting):
                await provider.agent().run("hello")
        assert (await checkpointer.load(WORKFLOW))["turn:0:heard:0"] == ["actually, check the tests too"]

    async def test_a_request_told_nothing_records_an_empty_list(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        Recorded even when empty, like the tree beside it: a request nobody steered is a request that
        ran, and the store already tells a recorded `[]` from a key nobody wrote.
        """

        async def waiting(already: int) -> Sequence[str]:
            return ()

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pending=waiting):
                await provider.agent().run("hello")
        assert (await checkpointer.load(WORKFLOW))["turn:0:heard:0"] == []

    async def test_a_resumed_pass_says_what_the_first_one_said_and_not_what_is_queued_now(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        The reason this is a step at all. Between two passes a person goes on typing, so a live read
        would hand the second pass a different queue - and `turn:0:model:0` is the answer to a
        question, so a replay that asked a different one would be pairing an answer with a prompt
        nobody ever gave.
        """
        queued = ["the first thing"]
        asked: list[list[str]] = []

        async def waiting(already: int) -> Sequence[str]:
            asked.append(list(queued))
            return queued[already:]

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pending=waiting):
                await provider.agent().run("hello")
        # A steer the agent was about to stop without hearing redirects it into one more request, so
        # a turn asks more times than it otherwise would. That is Pydantic AI's own behaviour and the
        # reason `enqueue` is used rather than the messages being spliced by hand.
        first = len(asked)
        queued.append("typed while it was thinking")

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pending=waiting):
                await provider.agent().run("hello")

        assert (await checkpointer.load(WORKFLOW))["turn:0:heard:0"] == ["the first thing"]
        assert len(asked) == first, "the second pass replayed the record rather than reading the queue"

    async def test_outside_a_scope_nothing_is_steered(self, provider: Provider) -> None:
        """A steer needs a checkpoint to have been written to, so an agent run bare has none."""
        answered = await provider.agent().run("hello")
        assert answered.output == "answer 1"


@dataclass(slots=True)
class Noting:
    """A toolset that records every call it actually performed, so a replay is visible as silence."""

    ran: list[str] = field(default_factory=list)

    def toolset(self) -> FunctionToolset[None]:
        held = self.ran

        async def note(what: str) -> str:
            """
            Note something down.

            Args:
                what: The thing to note.

            """
            held.append(what)
            return f"noted {what}"

        toolset = FunctionToolset[None]()
        toolset.add_function(note)
        return toolset


def calling(scripted: Scripted, tools: Noting) -> Agent[None, str]:
    """An agent over a scripted model and a counting toolset, built the way a pass builds one."""
    return Agent(
        scripted.model(),
        name="mainplate",
        instructions=INSTRUCTIONS,
        capabilities=[StepwiseDurability()],
        toolsets=[tools.toolset()],
    )


class TestRecordingAToolCall:
    async def test_the_tool_runs_once_and_is_replayed_afterwards(self, checkpointer: MemoryCheckpointer) -> None:
        """
        The reason a tool call has to be a step at all. A tool that reads answers differently every
        time it is asked, and a tool that writes has already written, so a pass that re-ran one
        would either resume against a file that moved or repeat an effect.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        tools = Noting()
        agent = calling(scripted, tools)

        for _ in range(3):
            async with a_pass(checkpointer) as run:
                with stepping(run, "turn:0"):
                    answered = await agent.run("go")

        assert answered.output == "done"
        assert tools.ran == ["alpha"], "the tool ran on the first pass and was replayed on the rest"
        assert scripted.asked == 2

    async def test_a_call_is_recorded_under_its_own_id(self, checkpointer: MemoryCheckpointer) -> None:
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0"):
                await calling(scripted, Noting()).run("go")

        assert (await checkpointer.load(WORKFLOW))["turn:0:tool:call-note-0"] == "noted alpha"

    async def test_several_calls_in_one_response_keep_their_results_apart(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        Why the key is the call's id and not its position. A model can ask for several tools at
        once and they run concurrently, so counting them would name a record by whichever won a
        race and hand a later pass somebody else's result.
        """
        wanted = (("note", {"what": "alpha"}), ("note", {"what": "beta"}), ("note", {"what": "gamma"}))
        scripted = Scripted(script=(calls(*wanted), ModelResponse(parts=[TextPart("done")])))
        tools = Noting()

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0"):
                await calling(scripted, tools).run("go")

        recorded = await checkpointer.load(WORKFLOW)
        assert sorted(tools.ran) == ["alpha", "beta", "gamma"]
        assert [recorded[f"turn:0:tool:call-note-{at}"] for at in range(3)] == [
            "noted alpha",
            "noted beta",
            "noted gamma",
        ]

    async def test_a_replayed_call_hands_back_what_the_first_pass_saw(self, checkpointer: MemoryCheckpointer) -> None:
        """Not just that it is silent, but that the conversation continues on the recorded answer."""
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        tools = Noting()
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0"):
                await calling(scripted, tools).run("go")

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0"):
                answered = await calling(scripted, tools).run("go")

        returns = [
            part.content
            for message in answered.all_messages()
            for part in message.parts
            if hasattr(part, "content") and part.part_kind == "tool-return"
        ]
        assert returns == ["noted alpha"]

    async def test_outside_a_scope_a_tool_is_not_recorded(self, checkpointer: MemoryCheckpointer) -> None:
        """
        A durable-capable agent stays an ordinary agent outside a scope, which is what keeps one
        usable in a script or a test. A script apiece, so each run reaches the tool call rather
        than the second one resuming where the first left the sequence.
        """
        tools = Noting()

        for _ in range(2):
            scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
            await calling(scripted, tools).run("go")

        assert tools.ran == ["alpha", "alpha"], "nothing was recorded, so the tool ran both times"
        assert await checkpointer.load(WORKFLOW) == {}


class TestTheCapabilityItself:
    def test_it_is_innermost_so_every_other_capability_has_already_run(self) -> None:
        assert StepwiseDurability().get_ordering().position == "innermost"

    def test_it_is_not_loadable_from_a_spec(self) -> None:
        """A spec cannot carry a checkpoint, so an agent built from one would record nothing."""
        assert StepwiseDurability.get_serialization_name() is None
