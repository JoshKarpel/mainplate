from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Never

import pytest
from conftest import DEFAULT_CHOICE
from conftest import Provider
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import FilePart
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.settings import ThinkingLevel
from without_durability.interfaces import claimed
from without_durability.stepwise import Completed
from without_durability.stepwise import Run
from without_durability.stepwise import Sleeping
from without_durability.stepwise import Waiting
from without_durability.stepwise import resume

from mainplate.agent import Choice
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import NeverStarted
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reached
from mainplate.conversation import Reasoning
from mainplate.conversation import Returned
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.conversation import blocks_of
from mainplate.conversation import messages_key
from mainplate.conversation import panelled
from mainplate.conversation import parse_choice
from mainplate.conversation import parse_prompt
from mainplate.conversation import prompt_key
from mainplate.conversation import reached
from mainplate.conversation import recorded_choice
from mainplate.conversation import transcript
from mainplate.conversation import turn_prefix
from mainplate.durability import stepping
from mainplate.service import Service

SESSION = "a-session"


def spoken(said: Transcript) -> list[tuple[str, str]]:
    """
    A transcript as who said what, in order.

    The tests below are about a session being answered rather than about how a panel is cut, so
    they assert on this rather than on whole `Panel` values; the cutting has its own tests above.
    """
    return [
        (panel.kind, block.text)
        for panel in said.panels
        for block in panel.blocks
        if isinstance(block, Prose | Reasoning)
    ]


async def started(service: Service, said: str, session: str = SESSION) -> None:
    """
    A session recorded on the default choice, with its first message waiting.

    The choice before the prompt, in the order `Service.start` writes them: the prompt is what
    queues a session, so a worker taking it before the choice landed would find nothing to answer
    on.
    """
    await service.checkpointer.supply(session, CHOICE_KEY, recorded_choice(DEFAULT_CHOICE))
    await service.say(session, turn=0, said=said)


async def pass_at(
    service: Service, body: Callable[[Run], Awaitable[Never]], session: str = SESSION
) -> Completed[Never] | Sleeping | Waiting:
    """One pass at a session, claimed and released the way the worker does it."""
    holder = await claimed(service.checkpointer, session)
    try:
        return await resume(holder, service.checkpointer, body)
    finally:
        await service.checkpointer.release(holder)


class TestReadingACheckpoint:
    def test_an_untouched_session_starts_at_the_first_turn(self) -> None:
        assert reached({}) == Reached(turn=0, history=())

    def test_a_turn_with_a_prompt_but_no_answer_is_still_the_turn_to_run(self) -> None:
        assert reached({prompt_key(0): "hello"}) == Reached(turn=0, history=())

    def test_history_is_every_answered_turn_in_order(self) -> None:
        recorded = {
            messages_key(0): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "first"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "one"}]},
            ],
            messages_key(1): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "second"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "two"}]},
            ],
        }
        at = reached(recorded)
        assert at.turn == 2
        assert [type(message) for message in at.history] == [ModelRequest, ModelResponse, ModelRequest, ModelResponse]

    def test_an_empty_checkpoint_is_an_empty_transcript(self) -> None:
        assert transcript({}) == Transcript(panels=(), awaiting=False, turns=0)

    def test_a_prompt_with_no_answer_yet_is_the_persons_panel_and_a_turn_still_awaited(self) -> None:
        assert transcript({prompt_key(0): "what is it"}) == Transcript(
            panels=(Panel(turn=0, at=0, kind="person", blocks=(Prose(text="what is it"),)),),
            awaiting=True,
            turns=1,
        )

    def test_an_answered_turn_is_the_question_and_the_answer_as_two_panels(self) -> None:
        recorded = {
            prompt_key(0): "what is it",
            messages_key(0): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "what is it"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "a mainplate"}]},
            ],
        }
        assert transcript(recorded) == Transcript(
            panels=(
                Panel(turn=0, at=0, kind="person", blocks=(Prose(text="what is it"),)),
                Panel(turn=0, at=1, kind="assistant", blocks=(Prose(text="a mainplate"),)),
            ),
            awaiting=False,
            turns=1,
        )

    def test_a_panel_is_a_run_of_one_kind_in_the_order_the_model_worked(self) -> None:
        """Reasoning, then a call, then the answer: three panels in that sequence, not one of each hoisted."""
        turn: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="go")]),
            ModelResponse(parts=[ThinkingPart(content="have a look"), ToolCallPart("read", {"path": "x"}, "c1")]),
            ModelRequest(parts=[ToolReturnPart("read", "the body", "c1")]),
            ModelResponse(parts=[TextPart("it says hello")]),
        ]
        assert tuple(panelled(3, blocks_of(turn))) == (
            Panel(turn=3, at=1, kind="thinking", blocks=(Reasoning(text="have a look"),)),
            Panel(
                turn=3,
                at=2,
                kind="tool",
                blocks=(
                    ToolUse(
                        tool="read",
                        arguments='{"path":"x"}',
                        returned=Returned(outcome="success", content="the body"),
                    ),
                ),
            ),
            Panel(turn=3, at=3, kind="assistant", blocks=(Prose(text="it says hello"),)),
        )

    def test_a_call_with_no_result_recorded_is_still_out(self) -> None:
        """The run ended between the call and its return, which is what a reader has to be able to see."""
        turn: list[ModelMessage] = [ModelResponse(parts=[ToolCallPart("grep", {"q": "z"}, "c9")])]
        assert blocks_of(turn) == (ToolUse(tool="grep", arguments='{"q":"z"}', returned=None),)

    def test_a_failed_call_carries_why_rather_than_a_bare_flag(self) -> None:
        """
        What the *model* was handed, which for a failure is the error wrapped as Pydantic AI wraps
        it. A transcript showing something the model never saw would be a second account of the
        turn rather than a reading of it.
        """
        turn: list[ModelMessage] = [
            ModelResponse(parts=[ToolCallPart("write", {}, "c2")]),
            ModelRequest(parts=[ToolReturnPart("write", "no such directory", "c2", outcome="failed")]),
        ]
        assert blocks_of(turn) == (
            ToolUse(
                tool="write",
                arguments="{}",
                returned=Returned(outcome="failed", content='{"error":"no such directory"}'),
            ),
        )

    def test_a_part_this_console_cannot_draw_is_passed_over_rather_than_refused(self) -> None:
        """A provider adding a part kind must not break a console that never asked for one."""
        turn: list[ModelMessage] = [
            ModelResponse(parts=[FilePart(content=BinaryContent(b"\x00", media_type="image/png")), TextPart("and")])
        ]
        assert blocks_of(turn) == (Prose(text="and"),)

    def test_a_prompt_that_is_not_text_is_refused_rather_than_rendered(self) -> None:
        with pytest.raises(TypeError):
            parse_prompt({"content": "nice try"})


class TestChoosingATurn:
    def test_the_first_message_goes_into_the_first_turn(self) -> None:
        assert transcript({}).turns == 0

    def test_a_message_after_an_answer_goes_into_the_next_turn(self) -> None:
        recorded = {
            prompt_key(0): "a",
            messages_key(0): [{"kind": "response", "parts": [{"part_kind": "text", "content": "b"}]}],
        }
        assert transcript(recorded).turns == 1

    def test_a_slot_already_asked_in_is_spoken_for_even_unanswered(self) -> None:
        """Otherwise a second message posted while the first is in flight would overwrite it."""
        recorded = {
            prompt_key(0): "a",
            messages_key(0): [{"kind": "response", "parts": [{"part_kind": "text", "content": "b"}]}],
            prompt_key(1): "c",
            prompt_key(2): "d",
        }
        assert transcript(recorded).turns == 3


class TestTheRecordedChoice:
    """
    The written shape, asserted against literals rather than round-tripped through the writer.

    A round trip through `recorded_choice` would agree with itself however the scheme moved, which
    is exactly what these exist to catch: a session on disk was written by an older version of this
    file and has to keep reading back as the same choice.
    """

    def test_a_choice_is_recorded_as_the_three_things_it_is(self) -> None:
        chosen = Choice(profile="gateway", model="wide/steady", thinking="high")
        assert recorded_choice(chosen) == {"profile": "gateway", "model": "wide/steady", "thinking": "high"}

    def test_saying_nothing_about_thinking_is_recorded_rather_than_left_out(self) -> None:
        """Stated, so a reader can tell "asked for nothing" from "written before there was a knob"."""
        recorded = recorded_choice(Choice(profile="here", model="ripe/fast"))
        assert recorded == {"profile": "here", "model": "ripe/fast", "thinking": None}

    def test_a_choice_written_before_thinking_existed_still_parses(self) -> None:
        """The compatibility that matters: no `thinking` key at all is the level that asks nothing."""
        assert parse_choice({"profile": "here", "model": "ripe/fast"}) == Choice(profile="here", model="ripe/fast")

    @pytest.mark.parametrize("level", [False, True, "minimal", "low", "medium", "high", "xhigh"])
    def test_every_level_survives_the_checkpoint(self, level: ThinkingLevel) -> None:
        chosen = Choice(profile="gateway", model="wide/steady", thinking=level)
        assert parse_choice(recorded_choice(chosen)) == chosen

    def test_a_level_the_checkpoint_should_not_hold_is_refused_loudly(self) -> None:
        with pytest.raises(TypeError, match="not 'ferocious'"):
            parse_choice({"profile": "here", "model": "ripe/fast", "thinking": "ferocious"})


class TestAnsweringASession:
    async def test_a_started_session_waits_to_be_told_something(self, service: Service, provider: Provider) -> None:
        await service.checkpointer.supply(SESSION, CHOICE_KEY, recorded_choice(DEFAULT_CHOICE))
        assert await pass_at(service, provider.body()) == Waiting(key=prompt_key(0))
        assert provider.asked == 0

    async def test_a_workflow_with_no_recorded_choice_is_refused_rather_than_guessed_at(
        self, service: Service, provider: Provider
    ) -> None:
        """`Service.start` writes the choice first, so reaching this means something else queued it."""
        with pytest.raises(NeverStarted):
            await pass_at(service, provider.body())

    async def test_a_message_is_answered_and_the_session_waits_again(
        self, service: Service, provider: Provider
    ) -> None:
        await started(service, said="hello")
        assert await pass_at(service, provider.body()) == Waiting(key=prompt_key(1))
        said = transcript(await service.checkpointer.load(SESSION))
        assert spoken(said) == [("person", "hello"), ("assistant", "answer 1")]
        assert not said.awaiting

    async def test_a_later_pass_replays_the_recorded_answer_rather_than_asking_again(
        self, service: Service, provider: Provider
    ) -> None:
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await pass_at(service, body)
        await pass_at(service, body)
        assert provider.asked == 1

    async def test_a_second_message_is_answered_without_re_asking_the_first(
        self, service: Service, provider: Provider
    ) -> None:
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, turn=1, said="again")
        await pass_at(service, body)
        assert provider.asked == 2
        assert spoken(transcript(await service.checkpointer.load(SESSION))) == [
            ("person", "hello"),
            ("assistant", "answer 1"),
            ("person", "again"),
            ("assistant", "answer 2"),
        ]

    async def test_a_pass_that_died_after_the_model_answered_does_not_ask_it_again(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The window the capability exists for: recorded by the model step, not by the turn's own.

        A pass that reaches the provider and dies before recording the turn leaves `turn:0:model:0`
        written and `turn:0:messages` absent, so the next pass re-runs the agent for real. Without
        the capability that second run is a second call to the provider, and a paid one.
        """
        agent = provider.agent()
        await started(service, said="hello")
        holder = await claimed(service.checkpointer, SESSION)
        run = Run(holder=holder, checkpointer=service.checkpointer, recorded=await service.checkpointer.load(SESSION))
        with stepping(run, turn_prefix(0)):
            await agent.run("hello")
        await service.checkpointer.release(holder)
        recorded = await service.checkpointer.load(SESSION)
        assert messages_key(0) not in recorded

        assert await pass_at(service, provider.body()) == Waiting(key=prompt_key(1))
        assert provider.asked == 1

    async def test_a_turn_carries_the_conversation_so_far_to_the_model(
        self, service: Service, provider: Provider
    ) -> None:
        """A second turn must reach the model with the first exchange behind it, or it is a fresh chat."""
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, turn=1, said="again")
        await pass_at(service, body)
        assert provider.carried == [1, 3]

    async def test_two_sessions_do_not_see_each_other(self, service: Service, provider: Provider) -> None:
        body = provider.body()
        await started(service, said="first session", session="one")
        await started(service, said="second session", session="two")
        await pass_at(service, body, session="one")
        await pass_at(service, body, session="two")
        assert spoken(transcript(await service.checkpointer.load("one"))) == [
            ("person", "first session"),
            ("assistant", "answer 1"),
        ]
        assert spoken(transcript(await service.checkpointer.load("two"))) == [
            ("person", "second session"),
            ("assistant", "answer 2"),
        ]
