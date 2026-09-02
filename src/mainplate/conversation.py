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
# One key for the session, and seven per turn. The whole scheme is here so that the code that
# writes them and the functions that read them cannot drift apart:
#
#     choice               the endpoint, model, repository, isolation and thinking level this
#                          session is on, written once at creation
#     turn:{n}:prompt      what the person said, written from outside the pass by `arrive`
#     turn:{n}:steer:{k}   what the person said *into* the turn while it ran, written from outside
#                          the pass by `Service.steer`
#     turn:{n}:tree:{i}    the worktree as it stood before the i-th model request of that turn
#     turn:{n}:heard:{i}   which steers were put to the model at that request
#     turn:{n}:model:{i}   the i-th model response of that turn, written by `StepwiseDurability`
#     turn:{n}:tool:{id}   what one tool call returned, named by the call's own id
#     turn:{n}:messages    the messages the agent run produced, which is the turn's own answer
#
# `prompt` and `steer` are the only two written from *outside* a pass, because a person acts on a
# turn somebody else is running and their words cannot be a step of it.
#
# The indexed kinds are numbered by *position* within the turn and the tool key deliberately is
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
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from enum import Enum
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
from pydantic_ai.messages import UserPromptPart
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
from mainplate.reference import Prices
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.snapshots import Worktree
from mainplate.thinking import BY_LEVEL

CHOICE_KEY: StepKey = "choice"

# What the thinking level is called inside the recorded choice. Named once here because the writer
# and the reader are both in this file and must not drift, which is the same reason the keys are.
THINKING_FIELD: Final = "thinking"

REPOSITORY_FIELD: Final = "repository"

# The two isolation axes, named here beside the rest of the choice's fields. Both are absent from
# every record written before they existed, and both read back as what those sessions already had:
# `filesystem` from whether a repository was picked, `network` as off, since there was no way for a
# session to reach one at all.
ISOLATION_FIELD: Final = "isolation"
FILESYSTEM_FIELD: Final = "filesystem"
NETWORK_FIELD: Final = "network"

# Where the message in the box is going, which the composer posts and nothing ever records. It is
# named here rather than in `console.py` because the page renders the control and the boundary parses
# it, and `pages.py` cannot import the console without closing a ring.
DISPOSITION_FIELD: Final = "disposition"


class Disposition(Enum):
    """
    Which session's checkpoint the message in the box lands in.

    A request-time instruction and never a recorded value, which is what separates it from
    everything else posted by the composer: a session records what was *said*, and where it was said
    is already answered by which checkpoint holds it.

    One field rather than a control per destination, because every arm takes the same input and
    differs only in where it goes. Each is a call this console already makes.
    """

    HERE = "here"
    """`Service.say` at the next free turn. Queue-for-next-turn: a message typed while a reply is
    still coming lands in the turn after the one in flight and is answered in order, rather than
    reaching the turn being answered."""

    FORK = "fork"
    """`Service.fork` at the end, carrying the whole conversation, with this message asked there.
    The turns are settled by definition, since the branch point is past all of them.

    The same word the rule above every turn uses, because it is the same call with a different `at`.
    A second name for it would be a synonym to keep in step, not a distinction."""

    ASIDE = "aside"
    """The same call, recorded as a step out that is meant to come back.

    Nothing mechanical differs, and saying so is better than inventing a difference: what it buys is
    that the sidebar can draw a digression as one, and that the session knows to offer a way back."""

    STEER = "steer"
    """`Service.steer` into the turn already being answered, rather than the one after it.

    The only disposition that reaches a turn in flight. Everything else appends at a boundary, which
    is why this one needs the agent loop to cooperate: see `Stepping.steering`."""

    PARENT = "parent"
    """`Service.say` into the session this one was forked from, which is how an aside comes back.

    A *message* and not a merge. Splicing an aside's turns into its parent would leave the parent
    holding requests whose context never existed, since those turns were asked against the history at
    the branch point; a message whose text happens to have been written elsewhere falsifies nothing.
    Offered from any fork rather than only an aside, because `Origin.session` is what it needs and
    every fork has one."""


def parse_disposition(named: str) -> Disposition | None:
    """
    One posted value as the disposition it names, or nothing where it names none.

    `None` rather than a default, so the caller decides whether an unreadable value is a refusal or
    an omission. An absent field is `HERE` at the call site, because a form predating this control
    posts a message and means the thing Send has always done.
    """
    try:
        return Disposition(named)
    except ValueError:
        return None


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
    What the worktree looked like before the `at`-th model request of this turn.

    One per model request rather than one per turn, because with tools the worktree changes
    *during* a turn and a single snapshot at the top would describe only the state the first
    request saw. A model request is also the only honest place to take one: it is the boundary at
    which every tool of the previous batch has returned, where a capture between two calls of the
    same batch would record a tree the other calls were still writing to.

    Written by `Stepping.snapshot`, which builds the same name from the turn's prefix, so the
    numbering here and the numbering of `turn:{n}:model:{i}` advance together.
    """
    return f"{turn_prefix(turn)}:tree:{at}"


def steer_key(turn: int, said: int) -> StepKey:
    """
    The `said`-th thing the person put into this turn while it was still being answered.

    Written from *outside* a pass, like `turn:{n}:prompt` and unlike every other key here, because a
    steer is something one person does to a turn somebody else is running. That is also why
    `Service.steer` has to resolve a clash by trying the next number: the store keeps the value a key
    was first given, so two writers racing for one number would lose the loser's message in silence.
    """
    return f"{turn_prefix(turn)}:steer:{said}"


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


def parse_isolation(recorded: object, repository: str | None) -> Isolation:
    """
    How much of the filesystem a session reaches, defaulted from what it is working in.

    A record written before this field existed has no key, and what it must read back as is exactly
    what that session already had: a worktree if it picked a repository and no files if it did not.
    Deriving the default from `repository` rather than picking a constant is what makes that true for
    both kinds of session at once.

    A recorded value is taken as it stands and *not* reconciled with `repository`, deliberately.
    Making the pair agree is `Service`'s job at the moment a session is created, so a disagreement
    here would mean a record this console never writes, and quietly correcting it would hide that.
    """
    if recorded is None:
        return Isolation(filesystem=Filesystem.WORKTREE if repository is not None else Filesystem.NOTHING)
    if not isinstance(recorded, dict):
        raise TypeError(f"an isolation must be a mapping, not {recorded!r}")
    named = recorded.get(FILESYSTEM_FIELD)
    if not isinstance(named, str):
        raise TypeError(f"a filesystem must be a name, not {named!r}")
    try:
        reaching = Filesystem(named)
    except ValueError:
        raise TypeError(f"{named!r} is not a filesystem this console knows") from None
    return Isolation(filesystem=reaching, network=recorded.get(NETWORK_FIELD) is True)


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
        isolation=parse_isolation(recorded.get(ISOLATION_FIELD), repository),
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
        ISOLATION_FIELD: {FILESYSTEM_FIELD: chosen.isolation.filesystem.value, NETWORK_FIELD: chosen.isolation.network},
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

# Which pigment a panel is drawn in, and the axis the palette runs on: `person` and `steering` are
# what reached the model and the rest is what it produced. A new kind takes its side from that rather
# than a colour chosen for it.
#
# `steering` is its own kind rather than a `person` panel with a flag, because the key filters by
# kind and the two are worth filtering apart: reading a long turn back, what somebody said *into* it
# is a different thing from the question that opened it. It takes the person's hue all the same,
# since the axis is about who produced the text and that is the same person.
type Kind = Literal["person", "steering", "assistant", "thinking", "tool"]


@dataclass(frozen=True, slots=True)
class Prose:
    """Something said in words: the person's message, or the model's own answer."""

    text: str


@dataclass(frozen=True, slots=True)
class Steering:
    """
    Something the person said into a turn that was already running.

    Its own type rather than a `Prose` in a person-kind panel, because `panelled` reads a panel's
    kind off its blocks: prose is what the *model* says, and a steer arriving as one would be drawn
    as the model answering itself.
    """

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


type Block = Prose | Steering | Reasoning | ToolUse


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

    Carried on the turn's first panel, which is the person's, and read from there by the rule that
    opens the turn: this is a fact about the turn rather than about the message, and the rule is
    where the fork link that would go back to it lives. Kept here rather than moved onto the rule so
    that where a turn begins is decided once, by the panels, instead of by a second list beside them.

    Absent on every other panel, and absent altogether where no worktree is configured, since a hash
    for a directory nobody chose would be a fact about nothing.
    """

    opens: tuple[int, ...] = ()
    """
    The model requests whose first block landed in this panel, as indices within the turn.

    What the page hangs a tag on, and the reason a tag is anchored to a panel rather than drawn
    between two: a request is a round trip and a panel is a run of one kind, so a response can become
    three panels and two responses can merge into one. There is often no *gap* between panels to put
    a boundary in, so the marker goes in the margin beside where the request began.

    Several where two requests both start inside one panel, which is what a model answering twice in
    prose produces: the runs merge and both boundaries fall in the same run.
    """

    @property
    def anchor(self) -> str:
        return f"panel-{self.turn}-{self.at}"

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
class Spent:
    """
    What a turn was charged for, as the rule above it reports.

    Tokens and money are both here because they answer different questions and neither substitutes
    for the other: the counts say how much of the window a conversation is using, which is what
    decides when it stops fitting, and the cost says what that came to.

    `cost` is `None` where *any* response in the turn went unpriced, rather than the sum of the ones
    that were. A partial total reads as the whole of what a turn cost and understates it silently,
    which is the one way to be wrong about money that nobody looking at the page can catch.
    """

    asked: int
    answered: int
    cost: Decimal | None


def spent_on(responses: Sequence[ModelResponse]) -> Spent:
    """
    What a turn's model requests came to, summed over however many of them it took.

    A turn is one exchange to a reader and several requests to a provider, one before each batch of
    tool calls, so the figure worth showing is the turn's own. The same sum serves both readings of
    a turn, because a response carries its usage whether it was read back from `turn:{n}:messages`
    or from the `turn:{n}:model:{i}` step that recorded it.
    """
    charged = [response.usage.cost for response in responses]
    settled = [one for one in charged if one is not None]
    return Spent(
        asked=sum(response.usage.input_tokens for response in responses),
        answered=sum(response.usage.output_tokens for response in responses),
        cost=sum(settled, Decimal(0)) if settled and len(settled) == len(charged) else None,
    )


def altogether(spent: Iterable[Spent]) -> Spent:
    """
    Every turn's spend as the session's, under the rule one turn's already follows.

    Unknown anywhere is unknown for the whole, so a session with one unpriced turn reports no total
    rather than the sum of the rest: what a person reads off a total is what the session has cost
    them, and a figure quietly missing a turn is worse than no figure.
    """
    counted = tuple(spent)
    charged = [one.cost for one in counted]
    settled = [one for one in charged if one is not None]
    return Spent(
        asked=sum(one.asked for one in counted),
        answered=sum(one.answered for one in counted),
        cost=sum(settled, Decimal(0)) if settled and len(settled) == len(charged) else None,
    )


@dataclass(frozen=True, slots=True)
class Request:
    """
    One round trip to the model, as the tag beside it reports.

    A request is the unit three recorded things are actually about - the tree taken before it, the
    response it came back with, and what that response cost - and none of them is about a panel. That
    is the whole reason the tag exists: hung on panels, each had to be attributed to a chosen one.
    """

    at: int
    tree: str | None
    spent: Spent


def requests_in(recorded: Mapping[str, object], turn: int, responses: Sequence[ModelResponse]) -> tuple[Request, ...]:
    """
    What each of a turn's model requests is worth saying, in the order they were made.

    The tree comes from `turn:{n}:tree:{i}` and the spend from the response's own usage, which are
    the two halves of one request written by opposite ends: the snapshot is taken before the ask and
    the usage comes back with the answer. Reading them together here is what lets one tag say both.
    """
    return tuple(
        Request(at=at, tree=parse_tree(recorded.get(tree_key(turn, at))), spent=spent_on([response]))
        for at, response in enumerate(responses)
    )


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

    spent: Mapping[int, Spent] = field(default_factory=dict)
    """
    What each turn that has produced a response cost, by turn.

    A mapping rather than a field on `Panel`, because what it describes is the turn and a turn is
    several panels: hung on one of them it would have to be hung on a chosen one, and every reader
    would have to know which. A turn is absent until it has recorded something, which is why the
    rule above a turn nobody has started yet reports nothing rather than zero.
    """

    answering: int | None = None
    """
    The turn actually being answered, or nothing where none is.

    The *first* unanswered turn and not the last, which is the distinction a steer turns on: a person
    can type again while a reply is coming, so the turns behind the one in flight are queued rather
    than running, and `turns - 1` would name one of those. A message steered into a queued turn would
    reach a model that has not been asked anything yet.
    """

    requests: Mapping[int, tuple[Request, ...]] = field(default_factory=dict)
    """
    Each turn's model requests, which is what the tags in the margin are drawn from.

    Keyed by turn and indexed within it, so a panel's `opens` is a lookup rather than a search. The
    same reasoning as `spent` one level down: a request is not a panel, so what is true of one is not
    stored on the other.
    """

    @property
    def total(self) -> Spent:
        """What the whole conversation has cost, which is every turn's spend under one rule."""
        return altogether(self.spent.values())

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
        case Steering():
            return "steering"
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


type Sourced = tuple[Block, int | None]
"""
One block, and which model request of the turn produced it, or `None` where a person did.

The request index is what the page hangs a tag on, so a reader can see where one round trip ended
and the next began - which panels cannot show, because a panel is a run of one *kind* and a request
is a round trip, and the two cross-cut in both directions.
"""


def blocks_in(response: ModelResponse, returned: Mapping[str, Returned]) -> Iterator[Block]:
    """
    One response's parts as blocks.

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
    for part in response.parts:
        match part:
            case TextPart(content=said) if said.strip():
                yield Prose(text=said)
            case ThinkingPart(content=thought) if thought.strip():
                yield Reasoning(text=thought)
            case ToolCallPart(tool_name=tool, tool_call_id=call):
                yield ToolUse(tool=tool, arguments=part.args_as_json_str(), returned=returned.get(call))
            case _:
                continue


def steering_blocks(message: ModelRequest) -> Iterator[Block]:
    """
    Anything a person said inside a request the *agent* made, which is a steer and nothing else.

    A turn's own opening message arrives as a `UserPromptPart` too, and is skipped by `parted` rather
    than here: what tells them apart is position, since the opening message is the first thing in a
    turn and a steer never is. Everything else in a mid-turn request is a tool result, which is read
    as part of the call it answers rather than on its own.
    """
    for part in message.parts:
        if isinstance(part, UserPromptPart) and isinstance(part.content, str) and part.content.strip():
            yield Steering(text=part.content)


def parted(messages: Sequence[ModelMessage]) -> tuple[Sourced, ...]:
    """
    What a turn's messages are worth reading as, each with the request that produced it.

    Both directions of the walk, because a turn is no longer only what the model said: a steer
    arrives as a `UserPromptPart` in one of the agent's own requests, and reading only the responses
    would drop a message the person can see themselves having sent.

    The **first** message is skipped whatever it holds, because that is the turn's own prompt and
    `said_by` already draws it from `turn:{n}:prompt`. Positional rather than a check on the content,
    since a steer and an opening message are the same shape and only their place tells them apart.

    A steer carries the index of the request it was *sent with* rather than one of its own, which is
    what puts its panel below the tool results it travelled beside instead of above them.
    """
    returned = returns_in(messages)
    blocks: list[Sourced] = []
    at = -1
    for index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            at += 1
            blocks.extend((block, at) for block in blocks_in(message, returned))
        elif index > 0:
            blocks.extend((block, None) for block in steering_blocks(message))
    return tuple(blocks)


def blocks_of(messages: Sequence[ModelMessage]) -> tuple[Block, ...]:
    """What a turn's messages are worth reading as, in the order they were produced."""
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


def steers_in(recorded: Mapping[str, object], turn: int) -> tuple[str, ...]:
    """
    Everything the person has said into this turn while it ran, in the order they said it.

    Consecutive from zero, so the scan stops at the first number nobody has written, exactly as
    `reached` walks turns and `responded` walks requests. `Service.steer` never leaves a gap, which
    is what its clash check costs it.
    """
    said: list[str] = []
    while (typed := recorded.get(steer_key(turn, len(said)))) is not None:
        said.append(parse_prompt(typed))
    return tuple(said)


def responded(recorded: Mapping[str, object], turn: int) -> tuple[ModelResponse, ...]:
    """
    Every model response the turn being answered has recorded, in the order they were made.

    Consecutive from zero, so the scan stops at the first response not yet made rather than
    searching for the highest key, exactly as `reached` walks turns. A pass numbers its requests
    from zero within the turn, so this cannot be fooled by how many turns came before.

    Its own function because two readings of a running turn want the same walk: what it has said so
    far, and what it has spent so far. Done twice, the second would eventually disagree with the
    first about how much of a turn there is.
    """
    responses: list[ModelResponse] = []
    while (answered := recorded.get(model_key(turn, len(responses)))) is not None:
        responses.append(parse_model_response(answered))
    return tuple(responses)


def responses_in(messages: Sequence[ModelMessage]) -> tuple[ModelResponse, ...]:
    """The model's own turns within a settled turn, which is what carries what the turn cost."""
    return tuple(message for message in messages if isinstance(message, ModelResponse))


def so_far(recorded: Mapping[str, object], turn: int) -> tuple[Block, ...]:
    """
    What the turn being answered has produced up to now, read from its steps rather than its
    messages.

    The second of the two readings of a turn, and the reason a reader watches one happen instead of
    waiting for the whole of it: `turn:{n}:messages` is written when the turn *ends*, where the
    responses and the results behind it are written as they arrive. Nothing here is a second copy of
    anything - these are the records the durability capability already keeps so that a resumed pass
    does not pay for the same request twice.

    What comes out is a *prefix* of what `blocks_of` will produce once the turn is answered: the
    same responses, in the same order, cut by the same rule, with the results that have not arrived
    yet still out. That is what lets the page morph one into the other without a panel ever moving.
    """
    return tuple(block for block, _ in blocks_from(recorded, turn, responded(recorded, turn)))


def blocks_from(recorded: Mapping[str, object], turn: int, responses: Sequence[ModelResponse]) -> tuple[Sourced, ...]:
    """
    One running turn's recorded responses as blocks, each with the request that produced it.

    No steers here, and that is not an omission: a steer reaches the model *inside* the agent's own
    request, which is not something the step records - `turn:{n}:model:{i}` is the answer, not the
    question. It appears when the turn's messages land, which is the one place the question is
    written down. Until then the turn shows what it has said and not what it was told mid-way.
    """
    returned = {
        part.tool_call_id: returned_step(held)
        for response in responses
        for part in response.parts
        if isinstance(part, ToolCallPart)
        and (held := recorded.get(tool_key(turn, part.tool_call_id), NOTHING)) is not NOTHING
    }
    return tuple((block, at) for at, response in enumerate(responses) for block in blocks_in(response, returned))


def runs[T](items: Sequence[T], kind: Callable[[T], Kind]) -> Iterator[tuple[int, Kind, tuple[T, ...]]]:
    """
    One turn's items cut into runs of a single kind, each with the position that names it.

    Consecutive rather than gathered, so the page shows the order the model worked in: a reply
    that reasoned, called a tool, and then answered is three runs in that sequence, not a
    reasoning run and a tool run hoisted above the answer.

    Generic over the item because the same cut is taken of blocks and of blocks paired with the
    request that produced them: `panelled` needs both, and a second implementation would eventually
    disagree with this one about where a panel begins.
    """
    for at, (of_kind, run) in enumerate(groupby(items, key=kind)):
        yield at + 1, of_kind, tuple(run)


def panelled(turn: int, sourced: Sequence[Sourced]) -> Iterator[Panel]:
    """
    One turn's blocks cut into panels, a panel per run of blocks of the same kind.

    `opens` falls out of the same cut rather than being worked out again: a request opens in whatever
    panel its *first* block landed in, so the walk that groups the blocks is the one that knows. Done
    separately, the two would disagree about a panel's bounds the first time a response's parts
    straddled one.
    """
    seen: set[int] = set()
    for at, kind, run in runs(sourced, lambda held: kind_of(held[0])):
        opening = tuple(sorted({request for _, request in run if request is not None and request not in seen}))
        seen.update(opening)
        yield Panel(turn=turn, at=at, kind=kind, blocks=tuple(block for block, _ in run), opens=opening)


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
    spent: dict[int, Spent] = {}
    asking: dict[int, tuple[Request, ...]] = {}
    turn = 0
    while (asked := recorded.get(prompt_key(turn))) is not None:
        answered = recorded.get(messages_key(turn))
        if answered is None:
            break
        said = parse_messages(answered)
        answering = responses_in(said)
        panels.append(said_by(turn, parse_prompt(asked), parse_tree(recorded.get(opening_tree_key(turn)))))
        panels.extend(panelled(turn, parted(said)))
        spent[turn] = spent_on(answering)
        asking[turn] = requests_in(recorded, turn, answering)
        turn += 1
    # Several, because a person can type again while a reply is still coming. Those messages are
    # recorded in the turns after the one in flight and are answered in order, so a transcript
    # showing only the first would be hiding a message somebody had already sent.
    awaiting = False
    running = turn if recorded.get(prompt_key(turn)) is not None else None
    while (waiting := recorded.get(prompt_key(turn))) is not None:
        panels.append(said_by(turn, parse_prompt(waiting), parse_tree(recorded.get(opening_tree_key(turn)))))
        if not awaiting:
            # One walk of the turn's recorded responses, read twice: what it has said, and what it
            # has spent saying it. A turn in flight has a cost at all because the step that records
            # each response prices it on the way past, so this is the same reading the settled half
            # above does rather than a second, poorer one.
            answering = responded(recorded, turn)
            panels.extend(panelled(turn, blocks_from(recorded, turn, answering)))
            if answering:
                spent[turn] = spent_on(answering)
                asking[turn] = requests_in(recorded, turn, answering)
        awaiting = True
        turn += 1
    return Transcript(
        panels=tuple(panels),
        awaiting=awaiting,
        turns=turn,
        spent=spent,
        answering=running,
        requests=asking,
    )


def requested_at(recorded: Mapping[str, object], turn: int, at: int) -> object | None:
    """
    The `at`-th model response of a turn, exactly as the checkpoint holds it.

    One key and one value, which is what replaced showing a *panel's* record. A panel is a run of
    blocks of one kind and a request is a round trip, and the two cross-cut, so a panel's record was
    a slice of a stored value reached by indices one walk had to hand to another. A request is a
    thing the record actually has a key for, so this is a lookup.

    Read straight from `turn:{n}:model:{at}` rather than out of `turn:{n}:messages`, and the two are
    not the same claim: the step is what the provider answered, where the messages are what the agent
    concluded the turn was. The step is the earlier and more literal of the two, and it is there
    while the turn is still running.
    """
    return recorded.get(model_key(turn, at))


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


async def planting(workspaces: Workspaces | None, run: Run, chosen: Choice, turn: int) -> Worktree | None:
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


def working_in(workspaces: Workspaces | None, session: str, chosen: Choice) -> Worktree | None:
    """
    Where this session's files are, as a value, without asking whether they are there yet.

    A path rather than a planted worktree, because it is needed *before* the pass reaches the turn
    that plants one: the agent is built once per pass and its file tools are bound to this root,
    and the snapshot scope needs the same root for the same reason. Naming a directory cannot
    fail, and nothing here touches it until a tool is called, which is after `planting` has run.
    """
    if workspaces is None or chosen.repository is None:
        return None
    return workspaces.worktree(session)


def conversing(
    endpoints: Wires,
    instructions: str,
    workspaces: Workspaces | None = None,
    bwrap: str | None = None,
    prices: Prices | None = None,
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
        worktree = working_in(workspaces, run.workflow, chosen)
        # Only where there is a worktree to sit beside: a session with no repository has nothing
        # the scratch would be scratch *for*, and gets no tool that could reach it either.
        scratch = None if workspaces is None or worktree is None else workspaces.scratch_at(run.workflow)
        agent = agent_for(endpoints, chosen, instructions, worktree=worktree, scratch=scratch, bwrap=bwrap)
        # Built once per pass beside the agent and for the same reason: what it needs from the
        # session is the choice, and a choice cannot change. What it reads *through* is two holders,
        # so a rate that moves under a long-running pass still reaches the turn being priced.
        # Without one a turn records no cost, which is what a console with no reference configured
        # has always shown - a card with no numbers on it.
        pricer = None if prices is None else prices.pricer(chosen)
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
            # The snapshots are inside this rather than taken here, and that is what the worktree
            # is handed over for. One per model request is the only cadence that holds once tools
            # can write: the first is taken before the model is asked anything, which is the state
            # a rewind to this turn puts back, and each later one records what the previous batch
            # of calls left behind.
            # Loaded from the store on each request rather than read out of `run.recorded`, which is
            # the snapshot this pass started from. What makes a steer a steer is that it arrives
            # *after* the turn began, and it is often written by another process entirely, so the
            # pass's own copy is the one place it can never appear.
            async def waiting(already: int, turn: int = at.turn) -> Sequence[str]:
                return steers_in(await run.checkpointer.load(run.workflow), turn)[already:]

            with stepping(run, turn_prefix(at.turn), worktree, pricer, waiting):
                answered = await agent.run(prompt, message_history=list(at.history))
            said = await run.step(messages_key(at.turn), recording(answered), parse_messages)
            at = Reached(turn=at.turn + 1, history=(*at.history, *said))

    return converse
