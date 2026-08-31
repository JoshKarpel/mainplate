from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Never

import pytest
from conftest import Provider
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import UserPromptPart
from without_durability.interfaces import claimed
from without_durability.stepwise import Completed
from without_durability.stepwise import Run
from without_durability.stepwise import Sleeping
from without_durability.stepwise import Waiting
from without_durability.stepwise import resume

from mainplate.conversation import Exchange
from mainplate.conversation import Reached
from mainplate.conversation import Transcript
from mainplate.conversation import conversing
from mainplate.conversation import messages_key
from mainplate.conversation import parse_prompt
from mainplate.conversation import prompt_key
from mainplate.conversation import reached
from mainplate.conversation import replied
from mainplate.conversation import transcript
from mainplate.conversation import turn_prefix
from mainplate.durability import stepping
from mainplate.service import Service

SESSION = "a-session"


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
        assert transcript({}) == Transcript(exchanges=(), pending=())

    def test_a_prompt_with_no_answer_yet_is_pending(self) -> None:
        assert transcript({prompt_key(0): "what is it"}) == Transcript(exchanges=(), pending=("what is it",))

    def test_an_answered_turn_is_an_exchange(self) -> None:
        recorded = {
            prompt_key(0): "what is it",
            messages_key(0): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "what is it"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "a mainplate"}]},
            ],
        }
        assert transcript(recorded) == Transcript(
            exchanges=(Exchange(prompt="what is it", reply="a mainplate"),), pending=()
        )

    def test_a_reply_is_every_response_the_turn_produced(self) -> None:
        turn: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="ask")]),
            ModelResponse(parts=[TextPart("first")]),
            ModelResponse(parts=[TextPart("second")]),
        ]
        assert replied(turn) == "first\n\nsecond"

    def test_a_prompt_that_is_not_text_is_refused_rather_than_rendered(self) -> None:
        with pytest.raises(TypeError):
            parse_prompt({"content": "nice try"})


class TestAnsweringASession:
    async def test_a_new_session_waits_to_be_told_something(self, service: Service, provider: Provider) -> None:
        assert await pass_at(service, conversing(provider.agent())) == Waiting(key=prompt_key(0))
        assert provider.asked == 0

    async def test_a_message_is_answered_and_the_session_waits_again(
        self, service: Service, provider: Provider
    ) -> None:
        await service.say(SESSION, turn=0, said="hello")
        assert await pass_at(service, conversing(provider.agent())) == Waiting(key=prompt_key(1))
        assert transcript(await service.checkpointer.load(SESSION)) == Transcript(
            exchanges=(Exchange(prompt="hello", reply="answer 1"),), pending=()
        )

    async def test_a_later_pass_replays_the_recorded_answer_rather_than_asking_again(
        self, service: Service, provider: Provider
    ) -> None:
        body = conversing(provider.agent())
        await service.say(SESSION, turn=0, said="hello")
        await pass_at(service, body)
        await pass_at(service, body)
        await pass_at(service, body)
        assert provider.asked == 1

    async def test_a_second_message_is_answered_without_re_asking_the_first(
        self, service: Service, provider: Provider
    ) -> None:
        body = conversing(provider.agent())
        await service.say(SESSION, turn=0, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, turn=1, said="again")
        await pass_at(service, body)
        assert provider.asked == 2
        assert transcript(await service.checkpointer.load(SESSION)).exchanges == (
            Exchange(prompt="hello", reply="answer 1"),
            Exchange(prompt="again", reply="answer 2"),
        )

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
        await service.say(SESSION, turn=0, said="hello")
        holder = await claimed(service.checkpointer, SESSION)
        run = Run(holder=holder, checkpointer=service.checkpointer, recorded=await service.checkpointer.load(SESSION))
        with stepping(run, turn_prefix(0)):
            await agent.run("hello")
        await service.checkpointer.release(holder)
        recorded = await service.checkpointer.load(SESSION)
        assert messages_key(0) not in recorded

        assert await pass_at(service, conversing(agent)) == Waiting(key=prompt_key(1))
        assert provider.asked == 1

    async def test_a_turn_carries_the_conversation_so_far_to_the_model(
        self, service: Service, provider: Provider
    ) -> None:
        """A second turn must reach the model with the first exchange behind it, or it is a fresh chat."""
        body = conversing(provider.agent())
        await service.say(SESSION, turn=0, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, turn=1, said="again")
        await pass_at(service, body)
        assert provider.carried == [1, 3]

    async def test_two_sessions_do_not_see_each_other(self, service: Service, provider: Provider) -> None:
        body = conversing(provider.agent())
        await service.say("one", turn=0, said="first session")
        await service.say("two", turn=0, said="second session")
        await pass_at(service, body, session="one")
        await pass_at(service, body, session="two")
        assert transcript(await service.checkpointer.load("one")).exchanges == (
            Exchange(prompt="first session", reply="answer 1"),
        )
        assert transcript(await service.checkpointer.load("two")).exchanges == (
            Exchange(prompt="second session", reply="answer 2"),
        )
