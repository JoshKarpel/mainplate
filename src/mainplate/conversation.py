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
# One key for the session, and five per turn. The whole scheme is here so that the code that
# writes them and the functions that read them cannot drift apart:
#
#     choice               the endpoint, model, repository and thinking level this session is on,
#                          written once at creation
#     turn:{n}:prompt      what the person said, written from outside the pass by `arrive`
#     turn:{n}:tree:{i}    the worktree as it stood before the i-th model request of that turn
#     turn:{n}:model:{i}   the i-th model response of that turn, written by `StepwiseDurability`
#     turn:{n}:tool:{id}   what one tool call returned, named by the call's own id
#     turn:{n}:messages    the messages the agent run produced, which is the turn's own answer
#
# The two indexed kinds are numbered by *position* within the turn and the tool key deliberately is
# not. A model request happens in a fixed order, so counting them gives a name that is the same on
# every pass; a batch of tool calls runs concurrently, so counting those would name them by whoever
# won a race. A call already carries an id, and that id is part of the model response this
# conversation recorded, so a replay is handed the same one for free.
#
# `choice` is in the checkpoint rather than beside the session's row for the reason everything else
# is: it has to be the same on every pass and after every restart, and the checkpoint is the thing
# that already promises that. It is also why it is written before the first prompt and never
# again, since a session that changed endpoint halfway would replay recorded answers from one and
# continue on another. Forking is how a session's choice changes, and it changes it by making a
# different session rather than by rewriting this one.
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
from typing import Final
from typing import Literal
from typing import Never
from typing import assert_never
from typing import cast

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
from pydantic_ai.settings import ThinkingLevel
from pydantic_core import to_json
from without_durability.stepwise import Run
from without_durability.stepwise import StepKey

from mainplate.agent import Choice
from mainplate.agent import Wires
from mainplate.agent import agent_for
from mainplate.durability import parse_model_response
from mainplate.durability import parse_tree
from mainplate.durability import stepping
from mainplate.forge import Workspaces
from mainplate.snapshots import Workspace
from mainplate.thinking import BY_LEVEL

CHOICE_KEY: StepKey = "choice"

# What the thinking level is called inside the recorded choice. Named once here because the writer
# and the reader are both in this file and must not drift, which is the same reason the keys are.
THINKING_FIELD: Final = "thinking"

REPOSITORY_FIELD: Final = "repository"

# A value no checkpoint can hold, so that "no such key" stays distinguishable from a step that
# recorded `None`. The store keeps those apart deliberately - its `value` column is `NOT NULL` - and
# a tool that returns nothing is an ordinary tool, so a reader that ran both together would draw a
# finished call as one still out for as long as the turn lasted.
NOTHING: Final = object()

# Which endpoint the session is answered on, inside the recorded choice and on the form that starts
# one. Named once here for the reason the turn keys are: the code that writes it and the code that
# reads it are both in this file and must not drift.
ENDPOINT_FIELD: Final = "endpoint"


def turn_prefix(turn: int) -> str:
    return f"turn:{turn}"


def turn_of(key: StepKey) -> int | None:
    """
    Which turn a recorded key belongs to, or nothing at all for one that belongs to the session.

    The inverse of `turn_prefix`, and here beside it for the reason everything else in this file is:
    the code that builds these names and the code that reads them apart must move together.

    It answers about the *shape* rather than about a known list of key kinds, which is what a fork
    needs: `turn:3:messages`, `turn:3:model:1`, `turn:3:tool:toolu_017` and a `turn:3:approval:0`
    nobody has written yet are all turn 3, so copying a prefix of a conversation does not have to be
    taught each new kind of step. Tool keys are the proof rather than the hypothesis: they arrived
    after this was written and needed no change here, which is what the shape test buys.
    """
    marker, _, rest = key.partition(":")
    if marker != "turn":
        return None
    counted, _, _ = rest.partition(":")
    return int(counted) if counted.isdigit() else None


def before(recorded: Mapping[str, object], turn: int) -> dict[str, object]:
    """
    Everything a fork inherits: every recorded key belonging to a turn before `turn`.

    By turn rather than by key kind, so the whole of a shared past comes across whether it is a
    prompt, the messages a turn produced, or a step some later version records. What is left behind
    is `choice`, which the fork is about to answer differently, and that is the only key here that
    belongs to the session rather than to one of its turns.
    """
    return {key: value for key, value in recorded.items() if (at := turn_of(key)) is not None and at < turn}


def prompt_key(turn: int) -> StepKey:
    return f"{turn_prefix(turn)}:prompt"


def messages_key(turn: int) -> StepKey:
    return f"{turn_prefix(turn)}:messages"


def tree_key(turn: int, at: int) -> StepKey:
    """
    What the workspace looked like before the `at`-th model request of this turn.

    One per model request rather than one per turn, because with tools the worktree changes
    *during* a turn and a single snapshot at the top would describe only the state the first
    request saw. A model request is also the only honest place to take one: it is the boundary at
    which every tool of the previous batch has returned, where a capture between two calls of the
    same batch would record a tree the other calls were still writing to.

    Written by `Stepping.snapshot`, which builds the same name from the turn's prefix, so the
    numbering here and the numbering of `turn:{n}:model:{i}` advance together.
    """
    return f"{turn_prefix(turn)}:tree:{at}"


def model_key(turn: int, at: int) -> StepKey:
    """
    The `at`-th model response of this turn, as `StepwiseDurability` recorded it.

    Read rather than merely written, because a turn's responses land one at a time while the turn
    is still running and its `messages` do not land until it ends. That is the whole of what lets a
    reader watch a turn happen: the record is already there, a request at a time.

    Built here and by `Stepping.key("model")` there, from opposite ends, with nothing enforcing that
    the two agree - the same standing hazard `tree_key` carries, and answered the same way, by
    asserting the literal string in `test_conversation.py`.
    """
    return f"{turn_prefix(turn)}:model:{at}"


def tool_key(turn: int, call: str) -> StepKey:
    """
    What one call of this turn came back with, named by the call's own id.

    By id and not by position, because a batch of calls runs concurrently and counting them would
    name a record by whichever won a race. The id is asked for here rather than searched for: it is
    part of the model response already read out of `model_key`, so a reader is handed the same id
    the writer used and never has to scan the checkpoint for keys of this shape.

    The counterpart of `Stepping.identified("tool", id)`, with the same drift hazard as `model_key`.
    """
    return f"{turn_prefix(turn)}:tool:{call}"


def opening_tree_key(turn: int) -> StepKey:
    """
    The tree a turn *started* on, which is the one two other things mean by "this turn's tree".

    A fork plants its worktree at it, so a branch re-asks its question against the files that
    question was asked about; and the person's panel shows it, because that is where the fork link
    already is and what going back to this turn would put on disk. Both want the state before the
    turn did anything, which is the snapshot taken before its first model request.
    """
    return tree_key(turn, 0)


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


def parse_thinking(recorded: object) -> ThinkingLevel | None:
    """
    A recorded thinking level, or a loud failure if the checkpoint holds something else.

    Absent reads as `None`, which is the level meaning "say nothing about thinking". That is not a
    default papering over a parser that forgot the key: it is what every session recorded before
    this setting existed asked for, and what those sessions must keep asking for on the pass that
    resumes them.
    """
    if recorded is None or isinstance(recorded, bool) or recorded in BY_LEVEL:
        return cast(ThinkingLevel | None, recorded)
    raise TypeError(f"a thinking level must be a boolean or an effort, not {recorded!r}")


def parse_choice(recorded: object) -> Choice:
    """
    What a session was started on, or a loud failure if the record is not a choice.

    Strict about shape and silent about whether the pair is still *available*, which is a
    different question with a different answer: this says what the session chose, and `Catalogue`
    says whether that is still something to answer with.
    """
    if not isinstance(recorded, dict):
        raise TypeError(f"a choice must be a mapping, not {recorded!r}")
    endpoint, model = recorded.get(ENDPOINT_FIELD), recorded.get("model")
    if not isinstance(endpoint, str) or not isinstance(model, str):
        raise TypeError(f"a choice must name an endpoint and a model, not {recorded!r}")
    repository = recorded.get(REPOSITORY_FIELD)
    if repository is not None and not isinstance(repository, str):
        raise TypeError(f"a repository must be an id or nothing, not {repository!r}")
    return Choice(
        endpoint=endpoint,
        model=model,
        repository=repository,
        thinking=parse_thinking(recorded.get(THINKING_FIELD)),
    )


def recorded_choice(chosen: Choice) -> dict[str, object]:
    """
    A choice as the JSON-native value the store's codec will take.

    The thinking level goes in whatever it is, `None` included, so what a session asked for is
    stated rather than inferred from a key's absence. Absence still reads back as `None`, because
    the sessions recorded before this existed have no such key and are answerable exactly as they
    were.
    """
    return {
        ENDPOINT_FIELD: chosen.endpoint,
        "model": chosen.model,
        REPOSITORY_FIELD: chosen.repository,
        THINKING_FIELD: chosen.thinking,
    }


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
    than defaulted to some endpoint, because guessing which endpoint an unknown conversation
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

    tree: str | None = None
    """
    The worktree this turn started on, for the panel that opens one, and nothing for the rest.

    On the person's panel because that is where the snapshot is taken and where the fork link
    already is: the two are the same point, so a reader deciding to go back to a turn can see what
    going back would put on disk. Absent everywhere else, and absent altogether where no workspace
    is configured, since a hash for a directory nobody chose would be a fact about nothing.
    """

    settled: bool = True
    """
    Whether what is behind this panel has stopped changing, which decides if it offers its record.

    False only for the panels of the turn currently being answered, which are read from that turn's
    recorded steps rather than from its messages. A call still out gets its result, and the response
    after it has not been made, so there is no settled value for a disclosure to fetch once and
    keep. The person's panel is settled even there, because a prompt is written before the turn runs
    and nothing ever rewrites one.

    A field rather than a question asked of the transcript, because the panel is what the page has
    in hand when it decides whether to draw the disclosure.
    """

    @property
    def anchor(self) -> str:
        return f"panel-{self.turn}-{self.at}"

    @property
    def short_tree(self) -> str | None:
        """The hash as a person reads one, which is the first several characters and no more."""
        return None if self.tree is None else self.tree[:8]

    @property
    def label(self) -> str:
        """
        What a panel is called where somebody reads it, which is its whole position and not half.

        The turn alone names four things in a turn that reasoned, called a tool, and answered, so a
        reader following one permalink out of four had no way to tell which they were looking at
        and no way to say which they meant. `at` is already what makes the anchor unique; this is
        the same pair, said out loud.
        """
        return f"{self.turn}.{self.at}"


@dataclass(frozen=True, slots=True)
class Transcript:
    """
    A conversation as a reader sees it, and whether anything is still being answered.

    `awaiting` is not derivable from the panels, which is why it is a field: a turn with a message
    and no answer looks exactly like an answered turn whose model said nothing. It is what decides
    whether the page says an answer is still coming, so it is read from the checkpoint rather than
    guessed at from a rendering.

    `turns` is how many turns have been started, which is also the turn a new message goes into.
    Counted by the same walk that built the panels rather than recovered from them, and counted
    from what is recorded rather than from a number the server keeps: a slot with a prompt in it
    is spoken for whether or not it has been answered, so two messages posted at once land in
    different slots and neither overwrites the other.
    """

    panels: tuple[Panel, ...]
    awaiting: bool
    turns: int

    def asked_at(self, turn: int) -> str | None:
        """
        What the person said to open `turn`, or nothing where the conversation never reached it.

        What a fork needs, and the reason it is read off the transcript rather than the checkpoint:
        forking a turn offers its message back for re-sending, so the text the page puts in the box
        has to be the text the page is showing above it.
        """
        for panel in self.panels:
            if panel.turn == turn and panel.kind == "person":
                return "\n".join(block.text for block in panel.blocks if isinstance(block, Prose))
        return None


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


@dataclass(frozen=True, slots=True)
class Source:
    """
    Where in a turn's recorded messages a block was read from, as two indices into the stored value.

    Indices rather than the part itself, because what they are for is showing somebody the JSON the
    checkpoint *holds*. A parsed part dumped again states what this console's Pydantic AI would
    write today, which is a different claim and the weaker one: it agrees with the record until a
    release changes a default or renames a field, and then it disagrees silently, which is the one
    thing a reader looking at raw state cannot afford.
    """

    message: int
    part: int


type Sourced = tuple[Block, Source]


def blocks_in(response: ModelResponse, returned: Mapping[str, Returned]) -> Iterator[tuple[Block, int]]:
    """
    One response's parts as blocks, each with the index of the part it was read from.

    The one rule about what a part is worth reading as, so the two readings of a turn cannot come to
    disagree about it. A finished turn is read from its messages and the turn in flight is read from
    its recorded responses, and both arrive here: what differs between them is only where the
    responses came from and how a call's result was found, never what a part becomes.

    A part this console has no rendering for is passed over rather than refused. That is not a
    swallowed error: the provider and Pydantic AI are both free to add a part kind, and a console
    that crashed on one it had never heard of would be broken by somebody else's release. What is
    *required* here is the text, and a turn that produced none renders as a turn that said
    nothing, which is the honest report.

    A call whose id is not in `returned` is one still out, which `ToolUse` has always been able to
    say and which nothing could previously produce: by the time a turn's messages are recorded every
    call has an answer. Reading a turn as it runs is what finally reaches that state.
    """
    for at, part in enumerate(response.parts):
        match part:
            case TextPart(content=said) if said.strip():
                yield Prose(text=said), at
            case ThinkingPart(content=thought) if thought.strip():
                yield Reasoning(text=thought), at
            case ToolCallPart(tool_name=tool, tool_call_id=call):
                yield ToolUse(tool=tool, arguments=part.args_as_json_str(), returned=returned.get(call)), at
            case _:
                continue


def parted(messages: Sequence[ModelMessage]) -> tuple[Sourced, ...]:
    """
    What a turn's messages are worth reading as, each with where it was read from.

    The indices are this walk's to hand out because it is the walk that decides which parts become
    blocks at all. Recovered by a second pass they would be a guess at what this one did, and the
    skipping in `blocks_in` is exactly what makes that guess wrong.
    """
    returned = returns_in(messages)
    return tuple(
        (block, Source(message=index, part=at))
        for index, message in enumerate(messages)
        if isinstance(message, ModelResponse)
        for block, at in blocks_in(message, returned)
    )


def blocks_of(messages: Sequence[ModelMessage]) -> tuple[Block, ...]:
    """What a turn's messages are worth reading as, in the order the model produced them."""
    return tuple(block for block, _ in parted(messages))


def returned_step(recorded: object) -> Returned:
    """
    What one recorded tool step is as a reader sees it, in the words the settled reading would use.

    The text has to match what `ToolReturnPart.model_response_str` produces for the same value, and
    matching it is the point rather than a nicety: the same call is read from this step while the
    turn runs and from the turn's messages once it ends, so any difference here is a result that
    silently rewrites itself under the reader the moment the turn lands. Hence `to_json` rather than
    the standard library's `dumps`, whose spacing differs.

    Always a success, because a tool that raised recorded no step at all: a `ModelRetry` propagates
    out of the step and the call stays out until the retry lands, then reads as failed once the
    turn's messages say so. A `ToolReturn` carrying metadata would be unwrapped by the settled
    reading and not by this one, which is a difference to fix in the tool rather than here if one
    is ever written.
    """
    if recorded is None:
        return Returned(outcome="success", content="")
    said = recorded if isinstance(recorded, str) else to_json(recorded).decode()
    return Returned(outcome="success", content=said)


def so_far(recorded: Mapping[str, object], turn: int) -> tuple[Block, ...]:
    """
    What the turn being answered has produced up to now, read from its steps rather than its
    messages.

    The second of the two readings of a turn, and the reason a reader watches one happen instead of
    waiting for the whole of it: `turn:{n}:messages` is written when the turn *ends*, where the
    responses and the results behind it are written as they arrive. Nothing here is a second copy of
    anything - these are the records the durability capability already keeps so that a resumed pass
    does not pay for the same request twice.

    Consecutive from zero, so the scan stops at the first response not yet made rather than
    searching for the highest key, exactly as `reached` walks turns. A pass numbers its requests
    from zero within the turn, so this cannot be fooled by how many turns came before.

    What comes out is a *prefix* of what `blocks_of` will produce once the turn is answered: the
    same responses, in the same order, cut by the same rule, with the results that have not arrived
    yet still out. That is what lets the page morph one into the other without a panel ever moving.
    """
    responses: list[ModelResponse] = []
    while (answered := recorded.get(model_key(turn, len(responses)))) is not None:
        responses.append(parse_model_response(answered))
    returned = {
        part.tool_call_id: returned_step(held)
        for response in responses
        for part in response.parts
        if isinstance(part, ToolCallPart)
        and (held := recorded.get(tool_key(turn, part.tool_call_id), NOTHING)) is not NOTHING
    }
    return tuple(block for response in responses for block, _ in blocks_in(response, returned))


def runs[T](items: Sequence[T], kind: Callable[[T], Kind]) -> Iterator[tuple[int, Kind, tuple[T, ...]]]:
    """
    One turn's items cut into runs of a single kind, each with the position that names it.

    Consecutive rather than gathered, so the page shows the order the model worked in: a reply
    that reasoned, called a tool, and then answered is three runs in that sequence, not a
    reasoning run and a tool run hoisted above the answer.

    Generic over the item because two callers need the same cut of the same sequence: the page
    wants the blocks in each panel and `sourced_at` wants where those blocks came from. Written
    twice, the second would eventually disagree about which panel `at` names, and a reader would
    be shown the record of a panel they were not looking at with nothing saying so.
    """
    for at, (of_kind, run) in enumerate(groupby(items, key=kind)):
        yield at + 1, of_kind, tuple(run)


def panelled(turn: int, blocks: Sequence[Block], settled: bool = True) -> Iterator[Panel]:
    """
    One turn's blocks cut into panels, a panel per run of blocks of the same kind.

    `settled` is the reading these blocks came out of rather than anything about the blocks: an
    answered turn is read from its messages and is settled, and the turn in flight is read from its
    steps and is not. It is passed down rather than worked out here because this is a cut of a
    sequence and knows nothing about where the sequence came from.
    """
    for at, kind, run in runs(blocks, kind_of):
        yield Panel(turn=turn, at=at, kind=kind, blocks=run, settled=settled)


def said_by(turn: int, prompt: str, tree: str | None = None) -> Panel:
    """A turn's opening panel, which is the person's own message and is always its first."""
    return Panel(turn=turn, at=0, kind="person", blocks=(Prose(text=prompt),), tree=tree)


def transcript(recorded: Mapping[str, object]) -> Transcript:
    """
    The whole conversation, read out of the checkpoint that is the only record of it.

    Two walks rather than one, because turns are answered in order: everything up to the first
    unanswered turn is a finished turn, and everything from there on is a message waiting for a
    reply. Written as one loop with a branch, the second case would read as though a turn could be
    answered after an unanswered one, which the body cannot produce.

    An answered turn is read from its recorded messages rather than from its model steps, because
    those messages are what the *agent* concluded the turn was: with tools in the picture a turn is
    several model responses and several tool returns, and the message list is already the shape
    that says which is which.

    The turn being answered has no such list yet, so it is read from the steps behind it instead,
    which is the only place its progress exists until it ends. Only the first unanswered turn, and
    not the ones queued behind it: one reply is actually being written, and a queued turn has been
    said and not yet started.
    """
    panels: list[Panel] = []
    turn = 0
    while (asked := recorded.get(prompt_key(turn))) is not None:
        answered = recorded.get(messages_key(turn))
        if answered is None:
            break
        panels.append(said_by(turn, parse_prompt(asked), parse_tree(recorded.get(opening_tree_key(turn)))))
        panels.extend(panelled(turn, blocks_of(parse_messages(answered))))
        turn += 1
    # Several, because a person can type again while a reply is still coming. Those messages are
    # recorded in the turns after the one in flight and are answered in order, so a transcript
    # showing only the first would be hiding a message somebody had already sent.
    awaiting = False
    while (waiting := recorded.get(prompt_key(turn))) is not None:
        panels.append(said_by(turn, parse_prompt(waiting), parse_tree(recorded.get(opening_tree_key(turn)))))
        if not awaiting:
            panels.extend(panelled(turn, so_far(recorded, turn), settled=False))
        awaiting = True
        turn += 1
    return Transcript(panels=tuple(panels), awaiting=awaiting, turns=turn)


def stored_part(answered: object, source: Source) -> object:
    """
    One part as the checkpoint holds it, reached by the indices `parted` handed out.

    The narrowing is not defensive: `parse_messages` has already validated this value, so what is
    left is that the checkpoint's type is `object` and indexing it needs the shape stated. It is
    loud rather than lenient for the same reason `parse_prompt` is, because a checkpoint that does
    not have this shape is not something to render half of.
    """
    if not isinstance(answered, list):
        raise TypeError(f"a turn's messages must be a list, not {answered!r}")
    message = answered[source.message]
    if not isinstance(message, dict):
        raise TypeError(f"a message must be a mapping, not {message!r}")
    parts = message.get("parts")
    if not isinstance(parts, list):
        raise TypeError(f"a message's parts must be a list, not {parts!r}")
    return parts[source.part]


def sourced_at(recorded: Mapping[str, object], turn: int, at: int) -> object | None:
    """
    What the checkpoint holds behind one panel, or nothing at all where there is no such panel.

    A panel is a *reading* of the record rather than a thing the record has a key for, and this is
    where the two are put back together. A person's panel is the exception and is one key exactly:
    `turn:{n}:prompt` is that panel and nothing else. Every other panel is a run of parts inside
    `turn:{n}:messages`, so what comes back is the list of those parts as they are stored - the
    slice of one value, not a value of its own, which is the honest thing to show.

    JSON-native and not text, because how to render it is the page's to decide: this says what was
    stored and the page says how wide the indent is.
    """
    if at == 0:
        # `None` here is a turn nobody has reached, not a turn with an empty message: a prompt is
        # refused before it is recorded, so there is no such thing as one that is nothing.
        return recorded.get(prompt_key(turn))
    answered = recorded.get(messages_key(turn))
    if answered is None:
        return None
    for position, _, run in runs(parted(parse_messages(answered)), lambda sourced: kind_of(sourced[0])):
        if position == at:
            return [stored_part(answered, source) for _, source in run]
    return None


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


class NoSuchRepository(LookupError):
    """
    A session names a repository no forge reaches and nothing has ever cloned.

    Its own type for the reason `UnknownChoice` is one: the answer is a person's rather than a
    retry's. A GitHub integration was detached, or the console moved to a machine that cannot see
    it, and the fix is to attach it again. A repository already cloned does *not* reach here, so
    detaching one strands only the sessions whose files were never fetched.
    """


async def planting(workspaces: Workspaces | None, run: Run, chosen: Choice, turn: int) -> Workspace | None:
    """
    The session's own worktree, made to exist before the turn that will work in it.

    A turn already carrying a recorded tree is planted *at* it, which is what a fork is: it
    inherits the tree of the turn it is re-asking, so the branch answers the same question against
    the same files. Every other turn plants at whatever the repository's head is, which only
    happens once because the worktree is then already there.
    """
    if workspaces is None or chosen.repository is None:
        return None
    at = parse_tree(run.recorded.get(opening_tree_key(turn)))
    planted = await workspaces.plant(run.workflow, chosen.repository, tree=at)
    if planted is None:
        raise NoSuchRepository(f"no forge reaches {chosen.repository!r} and it has never been cloned")
    return planted


def working_in(workspaces: Workspaces | None, session: str, chosen: Choice) -> Workspace | None:
    """
    Where this session's files are, as a value, without asking whether they are there yet.

    A path rather than a planted worktree, because it is needed *before* the pass reaches the turn
    that plants one: the agent is built once per pass and its file tools are bound to this root,
    and the snapshot scope needs the same root for the same reason. Naming a directory cannot
    fail, and nothing here touches it until a tool is called, which is after `planting` has run.
    """
    if workspaces is None or chosen.repository is None:
        return None
    return workspaces.workspace(session)


def conversing(
    endpoints: Wires, instructions: str, workspaces: Workspaces | None = None
) -> Callable[[Run], Awaitable[Never]]:
    """
    The workflow body every session runs, closed over everything it takes to build an agent.

    A closure rather than an argument because `work` takes one body for every workflow. What
    differs between sessions is not the body but which agent it reaches for, and that is read from
    the session's own checkpoint rather than passed in: `run.workflow` names the session, and the
    session names its endpoint and its model.

    The endpoint is what is checked, and the model deliberately is not. A discovered catalogue says
    what an endpoint *advertises*, which is narrower than what it will *route*: exe.dev's gateway
    answers `claude-sonnet-4-6` perfectly well while listing it as `anthropic/claude-sonnet-4-6`,
    so refusing a pass on a model the catalogue lacks would strand a session the provider would
    have answered. The provider's own refusal is the authoritative answer about a model, and it
    arrives on the turn where it can be read. The endpoint is different: without one there is no
    endpoint to ask at all, so that is a question this can answer and `agent_for` raises on.

    The agent is built once per pass rather than once per turn, because a session's choice cannot
    change: reading it again on the second turn would be asking a question whose answer is already
    recorded. Doing it before the first `awaiting` is what makes a missing endpoint a failure the
    console can explain rather than one discovered mid-turn.
    """

    async def converse(run: Run) -> Never:
        chosen = choice_of(run.recorded)
        if chosen is None:
            raise NeverStarted(f"{run.workflow} records no endpoint, so it was never started by this console")
        # One value for the session's files, used twice: the agent's tools are bound to it, and
        # every snapshot inside a turn is taken of it. A session with no repository has none, and
        # gets an agent with no file tools rather than tools that refuse every call.
        workspace = working_in(workspaces, run.workflow, chosen)
        agent = agent_for(endpoints, chosen, instructions, workspace=workspace)
        at = reached(run.recorded)
        while True:
            prompt = await run.awaiting(prompt_key(at.turn), parse_prompt)
            # Cloning and checking out happen *here* rather than when the session was created,
            # because creating one is a request somebody is waiting on and a clone is a network
            # fetch that can take minutes. A pass is where slow work already lives and where a
            # lease already covers it. Both halves are idempotent, so every later pass reaches this
            # and does nothing.
            #
            # It is an effect outside a step, and that is sound rather than an exception: what it
            # does is make a directory exist, which is the same on every pass, so there is no
            # result to record and nothing for a replay to disagree with.
            await planting(workspaces, run, chosen, at.turn)
            # The turn's *prefix* rather than the run: the requests this block makes are numbered
            # from zero within it, so a turn's keys do not depend on how many turns preceded it in
            # this pass. A pass that resumes mid-conversation issues its first request under
            # `turn:7:model:0` exactly as the pass that first reached turn 7 did.
            #
            # The snapshots are inside this rather than taken here, and that is what the workspace
            # is handed over for. One per model request is the only cadence that holds once tools
            # can write: the first is taken before the model is asked anything, which is the state
            # a rewind to this turn puts back, and each later one records what the previous batch
            # of calls left behind.
            with stepping(run, turn_prefix(at.turn), workspace):
                answered = await agent.run(prompt, message_history=list(at.history))
            said = await run.step(messages_key(at.turn), recording(answered), parse_messages)
            at = Reached(turn=at.turn + 1, history=(*at.history, *said))

    return converse
