# A chat session as one durable workflow, and the pure readings of its checkpoint.
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
# One key for the session, and three per turn. The whole scheme is here so that the code that
# writes them and the functions that read them cannot drift apart:
#
#     choice               which profile and model this session is on, written once at creation
#     turn:{n}:prompt      what the person said, written from outside the pass by `arrive`
#     turn:{n}:model:{i}   the i-th model response of that turn, written by `StepwiseDurability`
#     turn:{n}:messages    the messages the agent run produced, which is the turn's own answer
#
# `choice` is in the checkpoint rather than beside the session's row for the reason everything else
# is: it has to be the same on every pass and after every restart, and the checkpoint is the thing
# that already promises that. It is also why it is written before the first prompt and never
# again, since a session that changed endpoint halfway would replay recorded answers from one and
# continue on another.
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
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Literal
from typing import Never
from typing import assert_never

from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.run import AgentRunResult
from without_durability.stepwise import Run
from without_durability.stepwise import StepKey

from mainplate.agent import Agents
from mainplate.agent import Choice
from mainplate.durability import stepping

CHOICE_KEY: StepKey = "choice"


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


def parse_choice(recorded: object) -> Choice:
    """
    The profile and model a session was started on, or a loud failure if the record is not one.

    Strict about shape and silent about whether the pair is still *configured*, which is a
    different question with a different answer: this says what the session chose, and `Agents`
    says whether that is still something to answer with.
    """
    if not isinstance(recorded, dict):
        raise TypeError(f"a choice must be a mapping, not {recorded!r}")
    profile, model = recorded.get("profile"), recorded.get("model")
    if not isinstance(profile, str) or not isinstance(model, str):
        raise TypeError(f"a choice must name a profile and a model, not {recorded!r}")
    return Choice(profile=profile, model=model)


def recorded_choice(chosen: Choice) -> dict[str, str]:
    """A choice as the JSON-native value the store's codec will take."""
    return {"profile": chosen.profile, "model": chosen.model}


def choice_of(recorded: Mapping[str, object]) -> Choice | None:
    """
    What a session is on, or nothing at all for one that has not been started yet.

    Absent is an ordinary state rather than a fault: a workflow id nobody enrolled has an empty
    checkpoint, and so does a session in the instant between its row and its first message.
    """
    written = recorded.get(CHOICE_KEY)
    return None if written is None else parse_choice(written)


class NeverStarted(LookupError):
    """
    A workflow was queued that no session creation ever wrote a choice into.

    `Service.start` writes the choice before the prompt precisely so this cannot happen, so
    reaching it means a workflow id was queued by something other than this console. Loud rather
    than defaulted to some profile, because guessing which endpoint an unknown conversation
    belongs on is exactly the decision nothing here should make on somebody's behalf.
    """


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


# What became of a call, as Pydantic AI's own `ToolReturnPart` states it. Carried rather than
# reduced to a boolean, because the three ways a call can fail to succeed are different things to
# read: a tool that raised, one a person refused, and one that was cut off partway.
type Outcome = Literal["success", "failed", "denied", "interrupted"]

# Which pigment a panel is drawn in, and the axis the palette runs on: `person` is what reached the
# model and the rest is what it produced. A new kind takes its side from that rather than a colour
# chosen for it.
type Kind = Literal["person", "assistant", "thinking", "tool"]


@dataclass(frozen=True, slots=True)
class Prose:
    """Something said in words: the person's message, or the model's own answer."""

    text: str


@dataclass(frozen=True, slots=True)
class Reasoning:
    """What the model worked through on the way to an answer."""

    text: str


@dataclass(frozen=True, slots=True)
class Returned:
    """What a call came back with, which exists only once it has come back."""

    outcome: Outcome
    content: str


@dataclass(frozen=True, slots=True)
class ToolUse:
    """
    A call the model made, and its result once there is one.

    `returned is None` is the whole of "still out", rather than a separate flag beside a result
    that would then have to be kept in step with it. It is also the only thing on this console
    that is genuinely in flight *within* a turn, so it is what a spinner is drawn from.
    """

    tool: str
    arguments: str
    returned: Returned | None


type Block = Prose | Reasoning | ToolUse


@dataclass(frozen=True, slots=True)
class Panel:
    """
    A run of blocks of one kind, which is the unit the page draws an edge down.

    `at` is the panel's position within its turn, so a panel's identity is `turn` and `at` and
    nothing else. That is what a permalink can be built on: a turn's panels only ever grow at the
    end, where a position in the whole transcript would shift under a reader whenever an earlier
    turn they had typed past was answered.
    """

    turn: int
    at: int
    kind: Kind
    blocks: tuple[Block, ...]

    @property
    def anchor(self) -> str:
        return f"panel-{self.turn}-{self.at}"


@dataclass(frozen=True, slots=True)
class Transcript:
    """
    A conversation as a reader sees it, and whether anything is still being answered.

    `awaiting` is not derivable from the panels, which is why it is a field: a turn with a message
    and no answer looks exactly like an answered turn whose model said nothing. It is what decides
    whether the page polls, so it is read from the checkpoint rather than guessed at from a
    rendering.

    `turns` is how many turns have been started, which is also the turn a new message goes into.
    Counted by the same walk that built the panels rather than recovered from them, and counted
    from what is recorded rather than from a number the server keeps: a slot with a prompt in it
    is spoken for whether or not it has been answered, so two messages posted at once land in
    different slots and neither overwrites the other.
    """

    panels: tuple[Panel, ...]
    awaiting: bool
    turns: int


def kind_of(block: Block) -> Kind:
    match block:
        case Prose():
            return "assistant"
        case Reasoning():
            return "thinking"
        case ToolUse():
            return "tool"
        case _ as unreachable:
            assert_never(unreachable)


def returns_in(messages: Sequence[ModelMessage]) -> dict[str, Returned]:
    """
    Every call's result in a turn, by the call id that names which call it answers.

    A result arrives in the *request* after the response that asked for it, so pairing them is a
    walk over the whole turn rather than something a single message can answer. A retry is a
    failure told to the model in a different shape, and reads as one here.
    """
    found: dict[str, Returned] = {}
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                found[part.tool_call_id] = Returned(outcome=part.outcome, content=part.model_response_str())
            elif isinstance(part, RetryPromptPart) and part.tool_call_id is not None:
                found[part.tool_call_id] = Returned(outcome="failed", content=part.model_response())
    return found


def blocks_of(messages: Sequence[ModelMessage]) -> tuple[Block, ...]:
    """
    What a turn's messages are worth reading as, in the order the model produced them.

    A part this console has no rendering for is passed over rather than refused. That is not a
    swallowed error: the provider and Pydantic AI are both free to add a part kind, and a console
    that crashed on one it had never heard of would be broken by somebody else's release. What is
    *required* here is the text, and a turn that produced none renders as a turn that said
    nothing, which is the honest report.
    """
    returned = returns_in(messages)
    blocks: list[Block] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            match part:
                case TextPart(content=said) if said.strip():
                    blocks.append(Prose(text=said))
                case ThinkingPart(content=thought) if thought.strip():
                    blocks.append(Reasoning(text=thought))
                case ToolCallPart(tool_name=tool, tool_call_id=call):
                    blocks.append(ToolUse(tool=tool, arguments=part.args_as_json_str(), returned=returned.get(call)))
                case _:
                    continue
    return tuple(blocks)


def panelled(turn: int, blocks: Sequence[Block]) -> Iterator[Panel]:
    """
    One turn's blocks cut into panels, a panel per run of blocks of the same kind.

    Consecutive rather than gathered, so the page shows the order the model worked in: a reply
    that reasoned, called a tool, and then answered is three panels in that sequence, not a
    reasoning panel and a tool panel hoisted above the answer.
    """
    for at, (kind, run) in enumerate(groupby(blocks, key=kind_of)):
        yield Panel(turn=turn, at=at + 1, kind=kind, blocks=tuple(run))


def said_by(turn: int, prompt: str) -> Panel:
    """A turn's opening panel, which is the person's own message and is always its first."""
    return Panel(turn=turn, at=0, kind="person", blocks=(Prose(text=prompt),))


def transcript(recorded: Mapping[str, object]) -> Transcript:
    """
    The whole conversation, read out of the checkpoint that is the only record of it.

    Two walks rather than one, because turns are answered in order: everything up to the first
    unanswered turn is a finished turn, and everything from there on is a message waiting for a
    reply. Written as one loop with a branch, the second case would read as though a turn could be
    answered after an unanswered one, which the body cannot produce.

    A turn is read from its recorded messages rather than from its model steps, because those
    messages are what the *agent* concluded the turn was: with tools in the picture a turn is
    several model responses and several tool returns, and the message list is already the shape
    that says which is which.
    """
    panels: list[Panel] = []
    turn = 0
    while (asked := recorded.get(prompt_key(turn))) is not None:
        answered = recorded.get(messages_key(turn))
        if answered is None:
            break
        panels.append(said_by(turn, parse_prompt(asked)))
        panels.extend(panelled(turn, blocks_of(parse_messages(answered))))
        turn += 1
    # Several, because a person can type again while a reply is still coming. Those messages are
    # recorded in the turns after the one in flight and are answered in order, so a transcript
    # showing only the first would be hiding a message somebody had already sent.
    awaiting = False
    while (waiting := recorded.get(prompt_key(turn))) is not None:
        panels.append(said_by(turn, parse_prompt(waiting)))
        awaiting = True
        turn += 1
    return Transcript(panels=tuple(panels), awaiting=awaiting, turns=turn)


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


def conversing(agents: Agents) -> Callable[[Run], Awaitable[Never]]:
    """
    The workflow body every session runs, closed over every agent this process can answer with.

    A closure rather than an argument because `work` takes one body for every workflow. What
    differs between sessions is not the body but which agent it reaches for, and that is read from
    the session's own checkpoint rather than passed in: `run.workflow` names the session, and the
    session names its profile.

    The agent is resolved once per pass rather than once per turn, because a session's choice
    cannot change: reading it again on the second turn would be asking a question whose answer is
    already recorded. Resolving it before the first `awaiting` is what makes a removed profile a
    failure the console can explain rather than one discovered mid-turn.
    """

    async def converse(run: Run) -> Never:
        chosen = choice_of(run.recorded)
        if chosen is None:
            raise NeverStarted(f"{run.workflow} records no profile, so it was never started by this console")
        agent = agents.for_choice(chosen)
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
