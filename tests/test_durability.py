from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import Scripted
from conftest import calls
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RequestUsage
from without_durability.interfaces import claimed
from without_durability.memory import MemoryCheckpointer
from without_durability.stepwise import Run
from without_durability.stepwise import extending

from mainplate.conversation import draining_inbox
from mainplate.conversation import recorded_steer
from mainplate.conversation import returned_step
from mainplate.conversation import returns_in
from mainplate.conversation import turn_of
from mainplate.durability import TOOK
from mainplate.durability import Allowance
from mainplate.durability import AllowanceSpent
from mainplate.durability import Stepping
from mainplate.durability import ending_turn
from mainplate.durability import parse_model_response
from mainplate.durability import parse_returned
from mainplate.durability import stepping
from mainplate.loop import Agent
from mainplate.loop import CannotGoOn

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
        yield Run(
            holder=holder,
            checkpointer=checkpointer,
            recorded=await checkpointer.load(WORKFLOW),
            extend=extending(checkpointer),
        )
    finally:
        await checkpointer.release(holder)


async def delivered(checkpointer: MemoryCheckpointer, *said: str) -> tuple[str, ...]:
    """Messages put in the workflow's inbox the way `Service.send` puts them, and the keys they took."""
    return tuple([(await checkpointer.append(WORKFLOW, recorded_steer(text))).key for text in said])


def output_of(messages: tuple[object, ...]) -> str:
    response = next(message for message in reversed(messages) if isinstance(message, ModelResponse))
    return response.text or ""


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


class TestRecordingAModelRequest:
    async def test_the_provider_is_reached_once_and_replayed_afterwards(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        agent = provider.agent()
        for _ in range(3):
            async with a_pass(checkpointer) as run:
                with stepping(run, "turn:0") as scope:
                    answered = await agent.run("hello", (), scope)
            assert output_of(answered) == "answer 1"
        assert provider.asked == 1

    async def test_the_recorded_value_is_what_the_store_will_take(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """A `ModelResponse` is not JSON, so what is recorded has to already be lowered to it."""
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await provider.agent().run("hello", (), scope)
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        assert parse_model_response(recorded).parts == [TextPart("answer 1")]


class TestPricingARecordedRequest:
    """
    That a turn's cost is in the checkpoint, and in the record a turn being *watched* is read from.

    Pricing happens before the model response step lands, so a running turn and its settled messages
    read the same cost.
    """

    async def test_the_recorded_response_carries_what_it_cost(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pricer=lambda usage: Decimal("0.25")) as scope:
                await provider.agent().run("hello", (), scope)
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
            with stepping(run, "turn:0", pricer=note) as scope:
                await provider.agent().run("hello", (), scope)
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        assert len(seen) == 1
        assert seen[0].output_tokens == parse_model_response(recorded).usage.output_tokens

    async def test_a_model_nothing_can_price_records_no_cost_rather_than_a_zero(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """`free` and `nobody published a price` are different claims, and only one may be drawn."""
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", pricer=lambda usage: None) as scope:
                await provider.agent().run("hello", (), scope)
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


class TestTimingWhatAPassDid:
    """
    How long a round trip and a tool call took, recorded where each of them can be.

    Two homes for one word, because the values differ: a response is a thing this console fills in
    before recording it and has `metadata` for exactly this, where a tool return is somebody else's
    value of an unknown shape and needs a key beside it.
    """

    async def test_the_recorded_response_says_how_long_it_took(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        In the step and not only in the turn's messages, which is what lets a rule report it while
        the turn is still running.
        """
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await provider.agent().run("hello", (), scope)
        recorded = (await checkpointer.load(WORKFLOW))["turn:0:model:0"]
        assert isinstance(recorded, dict)
        took = parse_model_response(recorded).metadata
        assert took is not None
        assert isinstance(took[TOOK], float)

    async def test_a_duration_already_on_a_response_is_never_overwritten(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        A duration is a fact about the request that was actually made, and a resumed pass did not
        make it: what a replay hands on is what the first pass timed.
        """
        answered = ModelResponse(parts=[TextPart("answered")], metadata={TOOK: 12.5})
        async with a_pass(checkpointer) as run:
            Stepping(run=run, prefix="turn:0").stamp(answered, timedelta(seconds=0.1))
        assert answered.metadata == {TOOK: 12.5}

    async def test_a_stamp_leaves_whatever_else_the_response_carries(self, checkpointer: MemoryCheckpointer) -> None:
        """`metadata` is a shared slot, so writing into it must not be writing over it."""
        answered = ModelResponse(parts=[TextPart("answered")], metadata={"something": "else"})
        async with a_pass(checkpointer) as run:
            Stepping(run=run, prefix="turn:0").stamp(answered, timedelta(seconds=2))
        assert answered.metadata == {"something": "else", TOOK: 2.0}

    async def test_a_call_is_timed_in_the_record_that_says_what_it_returned(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        One record and one write, where this was a key of its own for as long as a tool's return was
        stored bare: a duration beside somebody else's value would have been indistinguishable from a
        tool that returned a field of that name, and the envelope is what removes the objection.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await calling(scripted, Noting()).run("go", (), scope)

        recorded = await checkpointer.load(WORKFLOW)
        assert not [key for key in recorded if ":took:" in key], "a duration has no key of its own"
        assert parse_returned(recorded["turn:0:tool:call-note-0"]).took is not None

    async def test_a_replayed_call_keeps_the_time_the_first_pass_took(self, checkpointer: MemoryCheckpointer) -> None:
        """
        The reason it is recorded rather than measured on whichever pass draws the page: a replayed
        call runs no tool at all, so a second pass has nothing to time and must not say so.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        tools = Noting()
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await calling(scripted, tools).run("go", (), scope)
        first = (await checkpointer.load(WORKFLOW))["turn:0:tool:call-note-0"]

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await calling(scripted, tools).run("go", (), scope)

        assert (await checkpointer.load(WORKFLOW))["turn:0:tool:call-note-0"] == first

    async def test_a_tool_that_refused_is_timed_like_one_that_answered(self, checkpointer: MemoryCheckpointer) -> None:
        """The tool ran either way, and its refusal is a result the model is sent, so it is recorded like one."""
        scripted = Scripted(script=(calls(("refuse", {})), ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await declining(scripted, Declining()).run("go", (), scope)

        held = parse_returned((await checkpointer.load(WORKFLOW))["turn:0:tool:call-refuse-0"])
        assert held.outcome == "failed"
        assert held.took is not None


class TestPuttingAMessageIntoARunningTurn:
    """
    Steering: what a person says into a turn that is already being answered.

    The whole risk is a resumed pass reading a queue somebody has gone on adding to and asking a
    different question than the one recorded. What these pin is that the *cursor*, and not the queue,
    is what decides.
    """

    async def test_what_a_request_was_told_is_recorded(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        (entry,) = await delivered(checkpointer, "actually, check the tests too")

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                await provider.agent().run("hello", (), scope)

        assert (await checkpointer.load(WORKFLOW))["turn:0:heard:0"] == entry

    async def test_a_steer_reaches_the_request_it_was_read_for(self, checkpointer: MemoryCheckpointer) -> None:
        """
        The whole of why this appends rather than enqueues, asked of the messages the run produced.

        The steer is appended immediately before the request that read it, so it shapes that answer
        without costing an extra round trip and lands above that answer in the transcript.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        agent = calling(scripted, Noting())
        await delivered(checkpointer, "be brief")

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                answered = await agent.run("hello", (), scope)

        assert scripted.asked == 2, "a steer read at a request must not cost an extra one"
        assert [type(message).__name__ for message in answered] == [
            "ModelRequest",
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
            "ModelResponse",
        ], "the steer is its own message in the request it was read for"

    async def test_a_message_delivered_during_a_pass_is_left_for_the_next_one(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        What deleted the second drain, and with it the marker that used to shut a turn.

        A pass reads the snapshot it loaded on the way in, so nothing can arrive during one: the
        drain before the first request already sees everything this pass ever will. There is no
        window at the end of a turn to lose a message in, because a message nobody took is still in
        the queue for whichever turn opens next.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        agent = calling(scripted, Noting())

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                await delivered(checkpointer, "typed while it was thinking")
                answered = await agent.run("hello", (), scope)

        assert scripted.asked == 2, "the run was not redirected into a request to carry it"
        assert [type(message).__name__ for message in answered] == [
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
            "ModelResponse",
        ]

    async def test_a_cursor_is_recorded_per_request_and_nowhere_else(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        `tree:{i}`, `heard:{i}` and `model:{i}` are three parts of one request, so anything else
        writing a `heard` would drift it off the two keys it names a request alongside.
        """
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                await provider.agent().run("hello", (), scope)

        recorded = await checkpointer.load(WORKFLOW)
        assert sorted(key for key in recorded if ":heard:" in key) == ["turn:0:heard:0"]
        assert sorted(key for key in recorded if ":model:" in key) == ["turn:0:model:0"]

    async def test_a_request_told_nothing_still_records_where_it_had_read_to(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        Recorded even when it found nothing, like the tree beside it: a request nobody steered is a
        request that ran, and a replay has to start from where this one stopped rather than from the
        top of a queue that has grown since.
        """
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                await provider.agent().run("hello", (), scope)

        assert "turn:0:heard:0" in await checkpointer.load(WORKFLOW)

    async def test_a_resumed_pass_says_what_the_first_one_said_and_not_what_is_queued_now(
        self, checkpointer: MemoryCheckpointer, provider: Provider
    ) -> None:
        """
        The reason this is a step at all. Between two passes a person goes on typing, so a live read
        would hand the second pass a different queue - and `turn:0:model:0` is the answer to a
        question, so a replay that asked a different one would be pairing an answer with a prompt
        nobody ever gave.
        """
        (first,) = await delivered(checkpointer, "the first thing")
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                await provider.agent().run("hello", (), scope)

        await delivered(checkpointer, "typed while it was thinking")
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", draining=draining_inbox(run, 0)) as scope:
                await provider.agent().run("hello", (), scope)

        assert (await checkpointer.load(WORKFLOW))["turn:0:heard:0"] == first
        assert provider.asked == 1, "the second pass replayed the request rather than making another"


@dataclass(slots=True)
class Noting:
    """A toolset that records every call it actually performed, so a replay is visible as silence."""

    ran: list[str] = field(default_factory=list)

    ends: bool = False
    """
    Whether this toolset also offers a `stop`, which asks for the turn to end the way a plugin does.

    A second tool rather than `note` doing both, so a test can call one beside the other and ask what
    each of their records says. It calls `ending_turn` directly, which is what `asking_through` does
    with an `end`: what is under test here is the recording and the replay, and a plugin subprocess
    in front of it would be the same assertions made slowly.
    """

    waits: bool = False
    """
    Whether `note` holds until `stop` has asked, which pins how the two interleave in one response.

    **A test of what each of their records says proves nothing without it.** Neither tool awaits
    anything real, so whichever task is scheduled first runs to completion, and a `note` that
    finished before its neighbour asked records the right thing even under a design that shares one
    flag between every concurrent call. Pinning the order is what makes the assertion an assertion:
    `note` reads its own answer at the one moment a shared flag would hold somebody else's.

    An event rather than a sleep, for the reason the suite gives everywhere else: any duration is
    either racy or wasted, and this is the actual signal. Only the one test that calls both tools at
    once sets it, since a `note` waiting for a `stop` nobody called waits for ever.
    """

    ended: asyncio.Event = field(default_factory=asyncio.Event)

    def toolset(self) -> FunctionToolset[None]:
        held = self.ran
        ended = self.ended
        waits = self.waits

        async def note(what: str) -> str:
            """
            Note something down.

            Args:
                what: The thing to note.

            """
            held.append(what)
            if waits:
                await ended.wait()
            return f"noted {what}"

        async def stop(what: str) -> str:
            """
            Note something down, and end the turn.

            Args:
                what: The thing to note.

            """
            held.append(what)
            ending_turn()
            ended.set()
            return f"noted {what}, and that is the turn"

        toolset = FunctionToolset[None]()
        toolset.add_function(note)
        if self.ends:
            toolset.add_function(stop)
        return toolset


def calling(scripted: Scripted, tools: Noting) -> Agent:
    """An agent over a scripted model and a counting toolset, built the way a pass builds one."""
    return Agent(
        model=scripted.model(),
        instructions=INSTRUCTIONS,
        settings=ModelSettings(),
        toolsets=(tools.toolset(),),
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
                with stepping(run, "turn:0") as scope:
                    answered = await agent.run("go", (), scope)

        assert output_of(answered) == "done"
        assert tools.ran == ["alpha"], "the tool ran on the first pass and was replayed on the rest"
        assert scripted.asked == 2

    async def test_a_call_is_recorded_under_its_own_id(self, checkpointer: MemoryCheckpointer) -> None:
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await calling(scripted, Noting()).run("go", (), scope)

        held = parse_returned((await checkpointer.load(WORKFLOW))["turn:0:tool:call-note-0"])
        assert held.returned == "noted alpha"

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
            with stepping(run, "turn:0") as scope:
                await calling(scripted, tools).run("go", (), scope)

        recorded = await checkpointer.load(WORKFLOW)
        assert sorted(tools.ran) == ["alpha", "beta", "gamma"]
        assert [parse_returned(recorded[f"turn:0:tool:call-note-{at}"]).returned for at in range(3)] == [
            "noted alpha",
            "noted beta",
            "noted gamma",
        ]

    async def test_a_replayed_call_hands_back_what_the_first_pass_saw(self, checkpointer: MemoryCheckpointer) -> None:
        """Not just that it is silent, but that the conversation continues on the recorded answer."""
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        tools = Noting()
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await calling(scripted, tools).run("go", (), scope)

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                answered = await calling(scripted, tools).run("go", (), scope)

        returns = [
            part.content
            for message in answered
            for part in message.parts
            if hasattr(part, "content") and part.part_kind == "tool-return"
        ]
        assert returns == ["noted alpha"]

    async def test_a_call_that_ended_its_turn_says_so_on_the_pass_that_asked_and_on_every_replay(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        **The whole reason `end` rides on the record rather than in a flag.** The plugin that asks is
        consulted once: every later pass replays this call from its step without running the tool
        again, so a turn ended in memory alone would stop here on the first pass and run on past this
        point on every pass after it. That is the one disagreement between two passes this mechanism
        exists not to have.

        What the scope carries is read back from the record on both, which is what makes the two
        agree; the tool running once is what says the second reading was a replay.
        """
        ending = Noting(ends=True)
        halted: list[bool] = []
        asked: list[int] = []
        for _ in range(2):
            scripted = Scripted(script=(calls(("stop", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
            async with a_pass(checkpointer) as run:
                with stepping(run, "turn:0") as scope:
                    messages = await calling(scripted, ending).run("go", (), scope)
                halted.append(scope.halted)
                asked.append(scripted.asked)

        assert ending.ran == ["alpha"], "the tool ran on the first pass and was replayed on the second"
        assert parse_returned((await checkpointer.load(WORKFLOW))["turn:0:tool:call-stop-0"]).ended is True
        assert halted == [True, True]
        assert asked == [1, 0], "the model was not asked again after the call that ended the turn, on either pass"
        assert isinstance(messages[-1], ModelRequest), "and the turn's last word is the call's return"

    async def test_a_call_beside_one_that_ended_the_turn_records_nothing_of_it(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        Why the asking is a context variable and not a field on the scope, which every call shares.

        A model can ask for several tools in one response and the loop runs each in a task of its
        own, so a shared flag would have whichever call finished after the ending one claim to be
        where the turn stopped. What the turn does is the same either way, which is exactly why this
        needs an assertion: the record is the only place the difference shows.
        """
        wanted = (("note", {"what": "alpha"}), ("stop", {"what": "beta"}))
        scripted = Scripted(script=(calls(*wanted), ModelResponse(parts=[TextPart("done")])))

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                await calling(scripted, Noting(ends=True, waits=True)).run("go", (), scope)

        recorded = await checkpointer.load(WORKFLOW)
        assert scope.halted is True, "the turn stops, because one of them asked"
        assert parse_returned(recorded["turn:0:tool:call-stop-1"]).ended is True
        assert parse_returned(recorded["turn:0:tool:call-note-0"]).ended is False, "and its neighbour did not"


@dataclass(slots=True)
class Declining:
    """A toolset with one tool that turns the call down and one that fails at it, each counting its runs."""

    ran: list[str] = field(default_factory=list)

    def toolset(self) -> FunctionToolset[None]:
        held = self.ran

        async def refuse() -> str:
            """Turn the call down, correctably."""
            held.append("refuse")
            raise ModelRetry("try something else")

        async def fail() -> str:
            """Fail at the call, for good."""
            held.append("fail")
            raise ToolFailed("there is no such thing")

        toolset = FunctionToolset[None]()
        toolset.add_function(refuse)
        toolset.add_function(fail)
        return toolset


def declining(scripted: Scripted, tools: Declining) -> Agent:
    return Agent(
        model=scripted.model(), instructions=INSTRUCTIONS, settings=ModelSettings(), toolsets=(tools.toolset(),)
    )


def results_in(messages: tuple[object, ...]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


class TestACallThatWentWrong:
    """
    A refusal, a failure and a return are one record with an outcome, and the model sees one shape.

    What is being pinned is durability rather than wording: whatever the model was sent about a call
    is in the checkpoint, so a resumed pass hands the loop the same words instead of running the tool
    again to hear what it would say against a worktree the rest of the batch has since written to.
    """

    @pytest.mark.parametrize(("tool", "said"), [("refuse", "try something else"), ("fail", "there is no such thing")])
    async def test_it_is_recorded_and_replayed_rather_than_run_again(
        self, checkpointer: MemoryCheckpointer, tool: str, said: str
    ) -> None:
        scripted = Scripted(script=(calls((tool, {})), ModelResponse(parts=[TextPart("done")])))
        tools = Declining()
        for _ in range(3):
            async with a_pass(checkpointer) as run:
                with stepping(run, "turn:0") as scope:
                    messages = await declining(scripted, tools).run("go", (), scope)

        assert tools.ran == [tool], "it ran on the first pass and was replayed on the rest"
        (result,) = results_in(messages)
        assert (result.outcome, result.content) == ("failed", said)
        assert scripted.asked == 2, "the model was told once and answered once"

    @pytest.mark.parametrize("tool", ["refuse", "fail"])
    async def test_both_readings_of_it_agree(self, checkpointer: MemoryCheckpointer, tool: str) -> None:
        """The record a running turn is drawn from and the messages a settled one is drawn from say the same."""
        scripted = Scripted(script=(calls((tool, {})), ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await declining(scripted, Declining()).run("go", (), scope)

        held = parse_returned((await checkpointer.load(WORKFLOW))[f"turn:0:tool:call-{tool}-0"])
        assert returned_step(held) == returns_in(messages)[f"call-{tool}-0"]

    async def test_nothing_counts_how_many_times_a_tool_turned_the_model_down(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        The graph ended a turn on a second refusal of one tool. A refusal is an answer, and a model
        that keeps getting a call wrong is bounded by what bounds a model that keeps calling a tool
        that keeps failing, which is not a count.
        """
        scripted = Scripted(
            script=(
                ModelResponse(parts=[ToolCallPart("refuse", {}, "call-1"), ToolCallPart("refuse", {}, "call-2")]),
                ModelResponse(parts=[ToolCallPart("refuse", {}, "call-3")]),
                ModelResponse(parts=[ToolCallPart("refuse", {}, "call-4")]),
                ModelResponse(parts=[TextPart("done")]),
            )
        )
        tools = Declining()
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await declining(scripted, tools).run("go", (), scope)

        assert output_of(messages) == "done"
        assert tools.ran == ["refuse"] * 4
        assert scripted.asked == 4


class TestTheModelAndToolLoop:
    async def test_an_unknown_tool_is_a_failed_result(self, checkpointer: MemoryCheckpointer) -> None:
        scripted = Scripted(script=(calls(("absent", {})), ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await calling(scripted, Noting()).run("go", (), scope)

        (result,) = results_in(messages)
        assert (result.tool_name, result.outcome) == ("absent", "failed")
        assert "absent" in str(result.content)
        assert scripted.asked == 2

    async def test_invalid_arguments_are_a_failed_result_that_says_what_was_wrong(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """Deterministic on the recorded call, so it is not recorded, and a replay reaches the same words."""
        scripted = Scripted(script=(calls(("note", {})), ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await calling(scripted, Noting()).run("go", (), scope)

        (result,) = results_in(messages)
        assert result.outcome == "failed"
        assert "what" in str(result.content), "the missing argument is named"
        assert "turn:0:tool:call-note-0" not in await checkpointer.load(WORKFLOW)
        assert scripted.asked == 2

    async def test_empty_output_is_retried_once(self, checkpointer: MemoryCheckpointer) -> None:
        scripted = Scripted(script=(ModelResponse(parts=[]), ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await calling(scripted, Noting()).run("go", (), scope)

        assert output_of(messages) == "done"
        assert scripted.asked == 2

    async def test_repeated_empty_output_fails_loudly(self, checkpointer: MemoryCheckpointer) -> None:
        scripted = Scripted(script=(ModelResponse(parts=[]),))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(UnexpectedModelBehavior):
                await calling(scripted, Noting()).run("go", (), scope)

    async def test_a_tool_call_cut_off_at_the_output_limit_is_raised_rather_than_retried(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        Truncated arguments fail validation like any other bad call, so left to the retry path the
        loop would ask the model to try again with a prompt that says the arguments were wrong. They
        were not: the model was cut off writing them, and `conversing` stalls the turn on this
        exception rather than spending another request on it.
        """
        cut_off = ModelResponse(parts=[ToolCallPart("note", '{"what": "al', "call-note-0")], finish_reason="length")
        scripted = Scripted(script=(cut_off, ModelResponse(parts=[TextPart("done")])))
        tools = Noting()
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(CannotGoOn, match="in the middle of a tool call"):
                await calling(scripted, tools).run("go", (), scope)

        assert scripted.asked == 1, "the model was not asked to try again"
        assert tools.ran == [], "and nothing ran on half an argument"

    async def test_a_thinking_only_answer_cut_off_at_the_limit_names_the_number_that_was_sent(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """The number is what somebody looks up, and its absence is what a session with none should say."""
        cut_off = ModelResponse(parts=[ThinkingPart(content="let me think about")], finish_reason="length")
        scripted = Scripted(script=(cut_off,))
        capped = Agent(
            model=scripted.model(),
            instructions=INSTRUCTIONS,
            settings=ModelSettings(max_tokens=4096),
            toolsets=(),
        )
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(CannotGoOn, match="output limit of 4096 tokens"):
                await capped.run("go", (), scope)
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:1") as scope, pytest.raises(CannotGoOn, match="default output limit"):
                await calling(scripted, Noting()).run("go", (), scope)

    async def test_an_answer_the_content_filter_emptied_cannot_go_on(self, checkpointer: MemoryCheckpointer) -> None:
        """Recorded and deterministic, so a correction would spend a request to be filtered again."""
        scripted = Scripted(script=(ModelResponse(parts=[], finish_reason="content_filter"),))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(CannotGoOn, match="content filter"):
                await calling(scripted, Noting()).run("go", (), scope)
        assert scripted.asked == 1

    async def test_a_filtered_answer_that_still_thought_is_corrected_instead(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """Pydantic AI's line, kept: thinking is content, so the filter did not empty the answer."""
        thought = ModelResponse(parts=[ThinkingPart(content="hmm")], finish_reason="content_filter")
        scripted = Scripted(script=(thought, ModelResponse(parts=[TextPart("done")])))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await calling(scripted, Noting()).run("go", (), scope)
        assert output_of(messages) == "done"
        assert scripted.asked == 2

    async def test_a_whole_call_in_an_answer_cut_off_after_it_still_runs(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """The control: the limit was reached after the call was written out, and the call is usable."""
        cut_off = ModelResponse(parts=[ToolCallPart("note", {"what": "alpha"}, "call-note-0")], finish_reason="length")
        scripted = Scripted(script=(cut_off, ModelResponse(parts=[TextPart("done")])))
        tools = Noting()
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope:
                messages = await calling(scripted, tools).run("go", (), scope)

        assert tools.ran == ["alpha"]
        assert output_of(messages) == "done"

    async def test_duplicate_call_ids_cannot_go_on(self, checkpointer: MemoryCheckpointer) -> None:
        """
        Two calls under one id would be one record, so neither can be answered. The response is
        already recorded, so this is settled rather than a failure a redelivery could get past.
        """
        response = ModelResponse(
            parts=[
                ToolCallPart("note", {"what": "one"}, "same"),
                ToolCallPart("note", {"what": "two"}, "same"),
            ]
        )
        scripted = Scripted(script=(response,))
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(CannotGoOn, match="one id"):
                await calling(scripted, Noting()).run("go", (), scope)


class TestABatchOfCalls:
    async def test_a_sibling_is_cancelled_and_waited_for_when_a_call_raises(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """
        A raise no arm of the loop reads ends the pass, and a sibling left running would write its
        record after the pass had released the session. So the sibling is cancelled and the raise
        waits for it, and what is pinned is that the cancellation reached the tool before the raise
        reached the test.
        """
        cancelled: list[str] = []
        never = asyncio.Event()

        async def slow() -> str:
            """Wait for something that does not come."""
            try:
                await never.wait()
            except asyncio.CancelledError:
                cancelled.append("slow")
                raise
            return "never"  # pragma: no cover - the wait does not end

        async def broken() -> str:
            """Fail in a way no arm of the loop reads."""
            raise RuntimeError("a bug in the tool")

        toolset = FunctionToolset[None]()
        toolset.add_function(slow)
        toolset.add_function(broken)
        scripted = Scripted(script=(calls(("slow", {}), ("broken", {})),))
        agent = Agent(model=scripted.model(), instructions=INSTRUCTIONS, settings=ModelSettings(), toolsets=(toolset,))

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(RuntimeError, match="a bug in the tool"):
                await agent.run("go", (), scope)

        assert cancelled == ["slow"]
        assert "turn:0:tool:call-slow-0" not in await checkpointer.load(WORKFLOW)

    async def test_a_validation_error_from_inside_a_tool_is_the_tools_fault(
        self, checkpointer: MemoryCheckpointer
    ) -> None:
        """Caught as a correction it would tell the model its arguments were wrong when they were fine."""

        async def parse() -> str:
            """Parse something badly."""
            TypeAdapter(int).validate_python("not a number")
            return "unreachable"  # pragma: no cover - the parse raises

        toolset = FunctionToolset[None]()
        toolset.add_function(parse)
        scripted = Scripted(script=(calls(("parse", {})), ModelResponse(parts=[TextPart("done")])))
        agent = Agent(model=scripted.model(), instructions=INSTRUCTIONS, settings=ModelSettings(), toolsets=(toolset,))

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0") as scope, pytest.raises(ValidationError):
                await agent.run("go", (), scope)

        assert scripted.asked == 1, "the model was not told anything was wrong with its call"


class TestBoundingWhatOnePassDoes:
    """
    The allowance: how many live model requests one pass may make before it hands the turn back.

    What it is for is the lease. A pass that was a whole conversation had to fit inside one, which
    made the lease a bet on the longest turn anybody would ever ask for; a pass that is one round
    trip and the tool batch after it is a bound that can be reasoned about.
    """

    def test_an_allowance_refuses_the_request_that_would_go_past_it(self) -> None:
        spending = Allowance(limit=2)
        spending.take()
        spending.take()
        with pytest.raises(AllowanceSpent):
            spending.take()

    def test_no_allowance_is_an_unbounded_one(self) -> None:
        """What a pass was before there was a number here, and what a block outside a worker wants."""
        spending = Allowance(limit=None)
        for _ in range(50):
            spending.take()

    async def test_a_pass_stops_at_the_request_past_its_allowance(self, checkpointer: MemoryCheckpointer) -> None:
        """
        The unwind, seen from the store: the request it refused left no record, and neither did the
        tree that would have stood in front of it.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        agent = calling(scripted, Noting())

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", allowance=Allowance(limit=1)) as scope, pytest.raises(AllowanceSpent):
                await agent.run("go", (), scope)

        recorded = await checkpointer.load(WORKFLOW)
        assert scripted.asked == 1, "the second request is what it stopped at"
        assert "turn:0:model:0" in recorded
        assert "turn:0:model:1" not in recorded
        assert "turn:0:tree:1" not in recorded, "a request that was never made leaves no tree in front of it"

    async def test_the_request_a_pass_refused_records_nothing_at_all(self, checkpointer: MemoryCheckpointer) -> None:
        """
        Including the cursor, which is the half that is easy to get wrong and costs a round trip.

        `Agent.before_request` runs before the model does and records how far this turn has read, so
        a pass that drained and *then* refused would leave a cursor for a request nobody made. The
        next pass replays it, so a message delivered meanwhile waits for the request after the one it
        should have reached - or, if the turn ends first, opens a turn of its own. Found by driving a
        real worker rather than by reading, which is why it is pinned here.
        """
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        agent = calling(scripted, Noting())

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", allowance=Allowance(limit=1), draining=draining_inbox(run, 0)) as scope:
                with pytest.raises(AllowanceSpent):
                    await agent.run("go", (), scope)
        # The message arrives while the refused request is what the next pass will make.
        (entry,) = await delivered(checkpointer, "be brief")
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", allowance=Allowance(limit=1), draining=draining_inbox(run, 0)) as scope:
                answered = await agent.run("go", (), scope)

        assert (await checkpointer.load(WORKFLOW))["turn:0:heard:1"] == entry, "the very next request took it"
        assert [type(message).__name__ for message in answered] == [
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
            "ModelRequest",
            "ModelResponse",
        ], "and put it to the model as its own message, beside the tool results going the same way"

    async def test_a_replayed_request_does_not_spend_the_allowance(self, checkpointer: MemoryCheckpointer) -> None:
        """
        Which is the whole of why a resumed pass gets further rather than stopping where the last one
        did. A replayed request pays nobody and takes no time worth bounding, so only a live one
        counts, and the pass reaches the request its predecessor refused.
        """
        tools = Noting()
        scripted = Scripted(script=(calls(("note", {"what": "alpha"})), ModelResponse(parts=[TextPart("done")])))
        agent = calling(scripted, tools)

        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", allowance=Allowance(limit=1)) as scope, pytest.raises(AllowanceSpent):
                await agent.run("go", (), scope)
        async with a_pass(checkpointer) as run:
            with stepping(run, "turn:0", allowance=Allowance(limit=1)) as scope:
                answered = await agent.run("go", (), scope)

        assert scripted.asked == 2, "one live request each, and the first was replayed rather than re-asked"
        assert tools.ran == ["alpha"], "the tool between them was replayed too"
        assert output_of(answered) == "done"
