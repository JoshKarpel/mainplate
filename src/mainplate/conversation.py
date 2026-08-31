# A chat session as one durable workflow, and the two pure readings of its checkpoint.
#
# The workflow id *is* the session id, and the body below is the whole of what a session is: wait
# to be told what the person said, answer it, wait again. Nothing ends it, so a session's every
# pass comes back `Waiting`, which is the honest report: a conversation is never finished, only
# between turns.
#
# What that buys is the property the whole design turns on, that **the checkpoint is the
# conversation**. There is no messages table and no session state held in the server: what has
# been said is what has been recorded, so the page renders the checkpoint, a crash resumes from
# it, and a second process reading the same file sees exactly what the first one did.
#
# Three keys per turn, and the whole scheme is here so that the code that writes them and the two
# functions that read them cannot drift apart:
#
#     turn:{n}:prompt      what the person said, written from outside the pass by `arrive`
#     turn:{n}:model:{i}   the i-th model response of that turn, written by `StepwiseDurability`
#     turn:{n}:messages    the messages the agent run produced, which is the turn's own answer
#
# `messages` is what makes resuming cheap. The stepwise mechanism re-runs the code *between*
# steps, so a body that looped over every past turn would re-drive the agent graph for all of
# them on every pass: no provider calls, since those are recorded, but the graph's own work, once
# per turn per pass. Recording each turn's new messages instead means `reached` can reconstruct
# the history by reading, and the pass drives the agent exactly once, for the turn actually being
# answered.

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Never

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.run import AgentRunResult
from without_durability.stepwise import Run
from without_durability.stepwise import StepKey

from mainplate.durability import stepping


def turn_prefix(turn: int) -> str:
    return f"turn:{turn}"


def prompt_key(turn: int) -> StepKey:
    return f"{turn_prefix(turn)}:prompt"


def messages_key(turn: int) -> StepKey:
    return f"{turn_prefix(turn)}:messages"


def parse_prompt(recorded: object) -> str:
    """
    What somebody typed, or a loud failure if the checkpoint holds something else under that key.

    The one value here that crossed a trust boundary: a step's result was produced by code in
    this process, where this was written into the checkpoint by an HTTP handler on behalf of
    whoever posted the form.
    """
    if not isinstance(recorded, str):
        raise TypeError(f"a prompt must be text, not {recorded!r}")
    return recorded


def parse_messages(recorded: object) -> tuple[ModelMessage, ...]:
    return tuple(ModelMessagesTypeAdapter.validate_python(recorded))


@dataclass(frozen=True, slots=True)
class Reached:
    """
    How far a conversation has been answered, as the turn to run next and the history to run it on.

    A pure function of the checkpoint, so a pass starts by reading rather than by replaying.
    """

    turn: int
    history: tuple[ModelMessage, ...]


def reached(recorded: Mapping[str, object]) -> Reached:
    """
    The first turn with no answer, and every message from the turns before it.

    Consecutive by construction: turn *n* is only reached once turn *n-1* recorded its messages,
    so the scan stops at the first gap rather than searching for the highest key. A checkpoint
    with a hole in it is not a state this body can produce.
    """
    history: list[ModelMessage] = []
    turn = 0
    while (answered := recorded.get(messages_key(turn))) is not None:
        history.extend(parse_messages(answered))
        turn += 1
    return Reached(turn=turn, history=tuple(history))


@dataclass(frozen=True, slots=True)
class Exchange:
    """One thing asked and the answer it got, as the page shows it."""

    prompt: str
    reply: str


@dataclass(frozen=True, slots=True)
class Transcript:
    """
    A conversation as a reader sees it: what has been answered, and what is still being answered.

    `pending` is honestly a separate field rather than an `Exchange` with an empty reply, because
    the two are different states and the page renders them differently: one is a finished
    exchange, the other is a question with a spinner under it and a poll that will replace it.

    Several of them, because a person can type again while a reply is still coming. Those
    messages are recorded in the turns after the one in flight and are answered in order, so a
    transcript that showed only the first would be hiding a message somebody had already sent.
    """

    exchanges: tuple[Exchange, ...]
    pending: tuple[str, ...]


def spoken(response: ModelResponse) -> str:
    """The parts of a response a person reads, which for now is its text and nothing else."""
    return "".join(part.content for part in response.parts if isinstance(part, TextPart))


def replied(messages: Sequence[ModelMessage]) -> str:
    return "\n\n".join(said for message in messages if isinstance(message, ModelResponse) and (said := spoken(message)))


def transcript(recorded: Mapping[str, object]) -> Transcript:
    """
    The whole conversation, read out of the checkpoint that is the only record of it.

    Two walks rather than one, because turns are answered in order: everything up to the first
    unanswered turn is a finished exchange, and everything from there on is a message waiting for
    a reply. Written as one loop with a branch, the second case would read as though a turn could
    be answered after an unanswered one, which the body cannot produce.

    A reply is read from the turn's recorded messages rather than from its model steps, because
    those messages are what the *agent* concluded the turn was: with tools in the picture a turn
    is several model responses and several tool returns, and the message list is already the shape
    that says which is which.
    """
    exchanges: list[Exchange] = []
    turn = 0
    while (asked := recorded.get(prompt_key(turn))) is not None:
        answered = recorded.get(messages_key(turn))
        if answered is None:
            break
        exchanges.append(Exchange(prompt=parse_prompt(asked), reply=replied(parse_messages(answered))))
        turn += 1
    pending: list[str] = []
    while (waiting := recorded.get(prompt_key(turn))) is not None:
        pending.append(parse_prompt(waiting))
        turn += 1
    return Transcript(exchanges=tuple(exchanges), pending=tuple(pending))


def recording(answered: AgentRunResult[str]) -> Callable[[], Awaitable[object]]:
    """
    What a finished turn writes into the checkpoint, as the effect `Run.step` takes.

    A function of the result rather than a closure written at the call site, so the value it
    reads is the one passed in: a closure built inside the conversation's loop would capture the
    loop's variable and read whatever it holds when the step gets around to calling it.
    """

    async def record() -> object:
        return ModelMessagesTypeAdapter.dump_python(answered.new_messages(), mode="json")

    return record


def conversing(agent: Agent[None, str]) -> Callable[[Run], Awaitable[Never]]:
    """
    The workflow body every session runs, closed over the agent that answers them.

    A closure rather than an argument because `work` takes one body for every workflow, which is
    the right shape here: a session differs from another only in its id, and `Run.workflow` is
    already that. What an agent *is* stays the composition root's decision.
    """

    async def converse(run: Run) -> Never:
        at = reached(run.recorded)
        while True:
            prompt = await run.awaiting(prompt_key(at.turn), parse_prompt)
            # The turn's *prefix* rather than the run: the requests this block makes are numbered
            # from zero within it, so a turn's keys do not depend on how many turns preceded it in
            # this pass. A pass that resumes mid-conversation issues its first request under
            # `turn:7:model:0` exactly as the pass that first reached turn 7 did.
            with stepping(run, turn_prefix(at.turn)):
                answered = await agent.run(prompt, message_history=list(at.history))
            said = await run.step(messages_key(at.turn), recording(answered), parse_messages)
            at = Reached(turn=at.turn + 1, history=(*at.history, *said))

    return converse
