# A chat session as one durable workflow, and the pure readings of its checkpoint.
#
# The workflow id *is* the session id, and the body below is the whole of what a session is: wait
# to be told what the person said, answer it, wait again. Nothing ends it, so a session's every
# pass comes back `Blocked`, which is the honest report: a conversation is never finished, only
# between turns.
#
# What that buys is the property the whole design turns on, that **the checkpoint is the
# conversation**. There is no messages table and no session state held in the server: what has
# been said is what has been recorded, so the page renders the checkpoint, a crash resumes from
# it, and a second process reading the same file sees exactly what the first one did.
#
# Two key spaces, and the store owns one of them. The whole scheme is here so that the code that
# writes them and the functions that read them cannot drift apart:
#
#     inbox:{n}            a message or a command, filed by the store in the order it arrived and
#                          appended from outside a pass, by `Service.say`, `send` and `run`
#     result:{entry}       what the command delivered under that entry exited with, said and took,
#                          written by `Commands` when it finishes
#     choice               the endpoint, model, repository, isolation and thinking level this
#                          session is on, written once at creation
#     turn:{n}:opened      the entry this turn took, recorded by `Run.receive` in the body below
#     turn:{n}:tree:{i}    the worktree as it stood before the i-th model request of that turn
#     turn:{n}:heard:{i}   how far down the inbox the turn had read when it made that request
#     turn:{n}:model:{i}   the i-th model response of that turn, written by `StepwiseDurability`
#     turn:{n}:tool:{id}   what one tool call returned and how long it took, named by the call's
#                          own id
#     turn:{n}:messages    the messages the agent run produced, which is the turn's own answer
#
# **Every one of those holds a record from `records.py` rather than a bare value**, which is what
# lets any of them grow a field without a migration. The two cursors are the exception, and their
# value is the store's rather than ours: `receive` and `pending` write them, and an inbox key has
# nowhere a second field could ever want to go. Each record carries its own `kind`, so a value
# written under the wrong key fails to parse rather than being read as whatever that key expects -
# and in the inbox the tag is not a second copy of anything, since the store names an entry and
# nothing else says whether what is in it may be told to a model.
#
# **Nothing allocates a number by trying, and no key is contended.** A message used to name the turn
# it was going into, so writing one meant deciding which turn that was against a checkpoint that had
# already moved. The store names an entry; *which turn takes one* is decided later, by the pass that
# reads it, which is the only party reading at the moment the answer is true.
#
# **`command` is recorded and not told**, which is the whole of what a command is here. Those two
# questions are separate and this console already keeps them apart everywhere else: `tree:{i}` and
# `heard:{i}` are both records the page draws and no model ever sees. So a command is
# in the checkpoint - it renders, it survives a reload, a fork carries it - and it is not in the
# message history, so it costs the conversation no context and reaches no provider. Telling the model
# is a message somebody writes, which is what the box above it is already for. A pass draining its
# inbox passes over one rather than reading it.
#
# What being an entry buys a command is a *place*: the store files everything in the order it
# arrived, so counting a turn's model records ahead of it says how far the reply had got when
# somebody typed it, and its panel stays there rather than sinking as later answers land above it.
#
# The indexed kinds are numbered by *position* within the turn and the tool key deliberately is
# not. A model request happens in a fixed order, so counting them gives a name that is the same on
# every pass; a batch of tool calls runs concurrently, so counting those would name them by whoever
# won a race. A call already carries an id, and that id is part of the model response this
# conversation recorded, so a replay is handed the same one for free.
#
# A model request needs no `took` field of its own: a `ModelResponse` carries `metadata`, so how long
# the round trip took rides into `model:{i}` and `messages` alike in the record that already exists,
# where a tool call's duration is a field on the record that holds what it returned.
#
# `choice` is in the checkpoint rather than beside the session's row for the reason everything else
# is: it has to be the same on every pass and after every restart, and the checkpoint is the thing
# that already promises that. It is also why it is written before the first message and never
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
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from functools import partial
from itertools import groupby
from itertools import pairwise
from itertools import takewhile
from typing import Final
from typing import Literal
from typing import assert_never
from typing import cast

from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ThinkingLevel
from pydantic_core import to_json
from without_durability.interfaces import INBOX
from without_durability.interfaces import Durable
from without_durability.interfaces import Entry
from without_durability.stepwise import Run
from without_durability.stepwise import StepKey

from mainplate import records
from mainplate.agent import Choice
from mainplate.agent import Wires
from mainplate.agent import agent_for
from mainplate.agent import reaching
from mainplate.durability import TOOK
from mainplate.durability import Allowance
from mainplate.durability import AllowanceSpent
from mainplate.durability import Draining
from mainplate.durability import RequestRefused
from mainplate.durability import parse_model_response
from mainplate.durability import parse_refused
from mainplate.durability import parse_returned
from mainplate.durability import parse_took
from mainplate.durability import parse_tree
from mainplate.durability import stepping
from mainplate.forge import Workspaces
from mainplate.guidance import approaching
from mainplate.guidance import guidance_under
from mainplate.guidance import indexing
from mainplate.guidance import instructing
from mainplate.guidance import repository_guidance
from mainplate.reference import Prices
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.snapshots import Worktree
from mainplate.snapshots import parse_branch
from mainplate.snapshots import parse_commitish
from mainplate.tending import Tending
from mainplate.tending import standing
from mainplate.thinking import BY_LEVEL
from mainplate.tools import ASKING
from mainplate.tools import Handing

CHOICE_KEY: StepKey = "choice"

# What the thinking level is called inside the recorded choice. Named once here because the writer
# and the reader are both in this file and must not drift, which is the same reason the keys are.
THINKING_FIELD: Final = "thinking"

REPOSITORY_FIELD: Final = "repository"

# Where in that repository the session's worktree starts, and what branch it starts there, inside the
# recorded choice and on the form that begins one. Named here beside the repository they depend on,
# for the reason the turn keys are: the code that writes them and the code that reads them are both
# in this file and must not drift.
BASE_FIELD: Final = "base"
BRANCH_FIELD: Final = "branch"

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
    """Into this conversation, at whichever moment it is actually in: a steer where a turn is being
    answered, and the next turn where none is.

    **The one disposition nobody decides**, and it has to be that way. A page is rendered from a
    checkpoint that has already moved by the time the form posts, so a reader choosing between
    "steer" and "queue" is choosing against a state that no longer holds; so is a handler that reads
    the record and picks between two writes, since a turn can end between the read and the write. The
    message goes in the queue and the pass takes it or does not, which is the only reading made at
    the moment the answer is true.

    `NEXT` is the override for the case nothing can settle, since wanting to be answered *after* the
    reply that is coming is an intent no record carries."""

    NEXT = "next"
    """`Service.say`, which delivers a message a draining pass stops at rather than folds in.

    Kept as an explicit answer rather than deleted: a message meant to be taken up once the current
    reply lands is a different question from the one being answered now, and nothing in the
    checkpoint can tell the two apart."""

    FORGET = "forget"
    """`Service.say` with the model's context cleared.

    The one answer here that changes what the *model* is handed rather than where the message goes.
    Nothing is deleted and nothing is hidden: every turn above it still renders, still counts toward
    what the session cost, and still comes across on a fork. What starts again is only the history,
    which is why the word is `forget` and not `clear` - a control saying `clear` beside a transcript
    that keeps all of it would be describing something this does not do.

    Never a steer, because a boundary between turns is the only place one can go: it is delivered as
    a message a draining pass stops at, so it always opens a turn of its own. It carries a message
    for the same reason - a marker with no turn under it would be a rule with nothing below it - and
    there is no reason to forget without going on to say something.

    Continuing the conversation it closed is `fork` at that turn, which the rule already offers: the
    branch carries every turn above the boundary and leaves the marker behind, since `before` copies
    what is below the branch point and the record rides on the message that opens the turn."""

    HANDOFF = "handoff"
    """`Service.hand_off`, which asks this session to write down where it has got to and start again
    from that document.

    **The one answer where the box may be empty**, and that is what makes it an answer here at all
    rather than a control of its own. What a handoff takes is an optional note saying what it should
    dwell on, appended to the standing ask rather than replacing it, and the box is exactly where such
    a note is written: `/handoff` on its own hands off, and `/handoff` with a paragraph hands off
    pointed at what the paragraph says.

    That the ordinary case types nothing is why the button carries `formnovalidate`. The box is
    `required`, which is right for every other answer and would refuse the common case here, so this
    is the browser's own way of saying that this submitter does not need it; the boundary allows an
    empty message for this disposition and no other.

    It is *not* a message going anywhere, which it shares with `RUN`: what gets sent is the console's
    own ask, and the text rides along as guidance. Being in this field all the same is the menu's own
    premise, that the question is what happens to what you typed."""

    FORK = "fork"
    """`Service.fork` at the end, carrying the whole conversation, with this message asked there.
    The turns are settled by definition, since the branch point is past all of them.

    The same word the rule above every turn uses, because it is the same call with a different `at`.
    A second name for it would be a synonym to keep in step, not a distinction."""

    ASIDE = "aside"
    """The same call, recorded as a step out that is meant to come back.

    Nothing mechanical differs, and saying so is better than inventing a difference: what it buys is
    that the sidebar can draw a digression as one, and that the session knows to offer a way back."""

    RUN = "run"
    """`Service.run`, which runs the text as a command in this session's own worktree.

    The one answer here that is not a message going somewhere. It is in this field all the same,
    because the question the menu asks is what happens to what you typed and this is one more answer
    to it - and because a control of its own would spend a slot in the row above the box, which is
    the row a phone has least of.

    **As the person and not as the agent.** A session's `isolation` bounds what a *model* asked for,
    and the clone is bound read-only inside that sandbox precisely so no tool can write history. That
    is what makes this the useful half: `git commit` and `git push` are the person's to run, and
    confining them is what would make this pointless. The authority is nothing new - a session on
    `Filesystem.EVERYTHING` already hands a model the store and `config.yaml` - but it does mean who
    can reach this console is the whole of what guards it.

    Recorded and not told, so nothing here reaches the model. See the key scheme."""

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


# Which endpoint the session is answered on, inside the recorded choice and on the form that starts
# one. Named once here for the reason the turn keys are: the code that writes it and the code that
# reads it are both in this file and must not drift.
ENDPOINT_FIELD: Final = "endpoint"


RESULTS: Final = "result:"
"""
What a command's result is filed under, ahead of the entry the command itself arrived as.

Its own key space rather than a turn's, because a command belongs to whichever turn its entry landed
in and nothing outside a pass can know that yet; see `result_key`.
"""


@dataclass(frozen=True, slots=True)
class Posted:
    """
    One thing the person put into a session's inbox, and the key the store filed it under.

    Both halves, because the key is what everything else is said in terms of: a turn records the key
    it opened on, a request records how far down it had read, and a result names the command it
    answers. The key is also the order, since the store mints keys that sort.
    """

    key: str
    what: records.Delivered


def posted_in(recorded: Mapping[str, object]) -> tuple[Posted, ...]:
    """
    Everything delivered to this session, in the order the store filed it.

    The one walk of the inbox, so nothing else has to know how an entry is spotted. `recorded` comes
    back from `load` in record order and the keys sort, so this is both the arrival order and the
    order every cursor is compared in.
    """
    return tuple(
        Posted(key=key, what=parse_delivered(value)) for key, value in recorded.items() if key.startswith(INBOX)
    )


def openings(recorded: Mapping[str, object]) -> tuple[str, ...]:
    """
    The entry each turn opened on, one per turn that has started, consecutive from zero.

    The thing the whole transcript is cut by: a turn holds everything from its own entry up to the
    next turn's, which is the only thing that says where an entry belongs. The walk stops at the
    first turn nobody has opened rather than searching for the highest key, exactly as `reached`
    walks turns, because a pass opens them in order and cannot leave a gap.
    """
    opened: list[str] = []
    while (at := recorded.get(opened_key(len(opened)))) is not None:
        opened.append(parse_cursor(at))
    return tuple(opened)


def parse_cursor(recorded: object) -> str:
    """
    One recorded cursor, which is an inbox key and nothing else.

    Loud rather than lenient, like every other parser here, and hand-written because this is the one
    kind of value the store writes for itself: there is no record to validate, so what can be checked
    is that it is a string in the key space `receive` mints.

    `None` is one of the two things `Run.pending` writes, and it means a drain that found nothing
    with nothing behind it either. That reads as having read no further than the start of the inbox,
    which sorts below every key in it.
    """
    if recorded is None:
        return ""
    if not isinstance(recorded, str) or not recorded.startswith(INBOX):
        raise TypeError(f"a cursor names an inbox entry, and {recorded!r} is not one")
    return recorded


def held_in(inbox: Sequence[Posted], opened: Sequence[str], turn: int) -> tuple[Posted, ...]:
    """
    Everything that arrived while `turn` was the turn in hand, its own opening message included.

    Which is to say the entries from this turn's own up to the next turn's, or to the end of the
    inbox where no later turn has opened. That span is the whole of what makes an entry belong
    somewhere: nothing records a turn on an entry, and nothing should, since a message becoming a
    steer or a turn of its own is decided by where it lands.
    """
    if turn >= len(opened):
        return ()
    stop = opened[turn + 1] if turn + 1 < len(opened) else None
    return tuple(at for at in inbox if at.key >= opened[turn] and (stop is None or at.key < stop))


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

    Two rules rather than one, because there are two key spaces. The turn-prefixed keys come across
    by *shape*, so the whole of a shared past arrives whether it is the messages a turn produced or a
    step some later version records: a `turn:3:approval:0` nobody has written yet is turn 3 already.
    What is left behind there is `choice`, which the fork is about to answer differently.

    The store's own keys need the second rule, and this is the price the inbox charges: an entry says
    nothing about which turn it belongs to, so what decides is where it sits against the entry the
    branch point opened on. Everything below that is the shared past, and a command's result travels
    with the command it answers.

    **The keys come across unchanged**, which is what makes the cursors in the copied turns resolve:
    a fork appends nothing, it supplies the parent's own entry keys, so `turn:2:heard:0` still names
    an entry the branch holds. The store mints keys that only ever rise, so a message delivered to
    the branch afterwards still sorts after everything copied.
    """
    at = branch_at(recorded, turn)
    return {
        key: value
        for key, value in recorded.items()
        if ((held := turn_of(key)) is not None and held < turn)
        or ((posted := entry_of(key)) is not None and (at is None or posted < at))
    }


def branch_at(recorded: Mapping[str, object], turn: int) -> str | None:
    """
    The entry a fork at `turn` cuts the inbox at, or nothing where it cuts nothing off the end.

    Against the turns a *page* counts rather than the ones a pass has opened, because that is what a
    fork's `at` is: a reader forking at turn 3 of a conversation whose third message is still queued
    means the message, and the branch carries everything above it. So the boundaries are the entries
    every turn opened on, followed by the messages waiting for turns of their own, and `at` indexes
    into the two together exactly as `Transcript.turns` counts them.

    Nothing at all where the fork cuts nothing off the end, which is forking a conversation whole.
    """
    inbox = posted_in(recorded)
    opened = openings(recorded)
    queued = queued_in(recorded, inbox, opened, listening=False)
    opening = (*opened, *(at.key for at in queued if not isinstance(at.what, records.Command)))
    return opening[turn] if turn < len(opening) else None


def entry_of(key: StepKey) -> str | None:
    """
    Which inbox entry a key is about, for the two key spaces that are not turn-prefixed.

    An entry is about itself; a result is about the command it answers. Both answer by *shape*, like
    `turn_of` and for the same reason: a fork copies a prefix of a conversation without being taught
    each kind of thing that might hang off an entry.
    """
    if key.startswith(INBOX):
        return key
    if key.startswith(RESULTS):
        return key.removeprefix(RESULTS)
    return None


def opened_key(turn: int) -> StepKey:
    """
    The inbox entry this turn opened on, which is the cursor `Run.receive` recorded when it took one.

    What `turn:{n}:prompt` used to be, one indirection along: the message itself is an entry the
    store filed, and this says which one. That is also what makes a turn's *extent* readable, since
    everything from here to the next turn's own entry arrived while this turn was the one in hand.

    It holds a bare cursor rather than a record, which is the one exception to the record policy and
    is stated in `records.py`: the value is the store's, written by `receive` itself.
    """
    return f"{turn_prefix(turn)}:opened"


def result_key(entry: str) -> StepKey:
    """
    What the command delivered under `entry` exited with, said, and took.

    Named after the entry rather than after a turn and a slot, and that is not tidying: which turn a
    command belongs to is decided by where its entry sits, so a key naming a turn would be a second
    answer to that question, written by a handler reading a page that may have moved on. Keyed by the
    entry there is nothing to disagree with.

    A second key rather than a field on the command, because it lands later: a `pytest` is minutes
    and somebody is watching, so the command's panel is drawn the instant it is posted and this
    landing is what fills in the result. Absent is "still running", which is exactly how
    `ToolUse.returned` reads and needs no flag beside it.
    """
    return f"{RESULTS}{entry}"


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


def heard_key(turn: int, at: int) -> StepKey:
    """
    How far down the inbox this turn had read when it made its `at`-th model request.

    A cursor, and read as well as written: it is what tells a page where a steer has already gone.
    Until the turn records its messages there is nothing else that says so, since an entry is what
    somebody typed and says nothing about whether a model has seen it.

    Built here and by `Stepping.key("heard")` there, with the same standing hazard `tree_key` carries.
    """
    return f"{turn_prefix(turn)}:heard:{at}"


# `turn:{n}:late:{k}` used to be here, and the inbox is what deleted it. It recorded what was found
# at the boundary where a run would otherwise have ended, so that a message arriving during the last
# response could redirect the run into one more request to carry it. A pass reads its own snapshot,
# which is fixed the moment the pass starts, so nothing can arrive *during* one: the drain before the
# first request already takes everything the pass can see, and a message delivered after that is read
# by the next pass. Where it once forced an extra round trip onto the turn that was ending, it now
# opens the turn after it, which is both simpler and one fewer request nobody asked for.


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


def refused_key(turn: int, at: int) -> StepKey:
    """
    Why the `at`-th model request of this turn will never be accepted, where one never was.

    Named after the *request* rather than after the turn, so a pass that reached further than the one
    before it records against the request that actually failed. It shares its index with
    `model_key(turn, at)` on purpose: the two are the question and the reason there is no answer, and
    exactly one of them exists for any given request.

    Absent is the ordinary case, and reading it is how the page tells a session that stopped from one
    that is still being answered. The counterpart of `Stepping.identified("refused", str(at))`, with
    the same drift hazard as `model_key`.
    """
    return f"{turn_prefix(turn)}:refused:{at}"


def tool_key(turn: int, call: str) -> StepKey:
    """
    What one call of this turn came back with and how long it took, named by the call's own id.

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


def recorded_prompt(said: str, forget: bool = False) -> dict[str, object]:
    """
    What opens a turn, as the JSON-native value the store's codec will take.

    `forget` is what makes this turn the start of the model's history; see `records.Prompt.forget`
    for why it rides here rather than in a key of its own.
    """
    return records.Prompt(said=said, forget=forget).recorded()


def recorded_steer(said: str) -> dict[str, object]:
    """
    A message that may join the turn already running, as the value the store's codec will take.

    Its own function beside `recorded_prompt` rather than one taking a kind, because the two are
    read differently by the pass that takes them and a caller that could pass the wrong word would
    be a way to have a message answered on its own that somebody meant as a steer.
    """
    return records.Steer(said=said).recorded()


def recorded_ask(guiding: str | None = None) -> dict[str, object]:
    """
    The message that opens a handoff turn, as the value the store's codec will take.

    Composed here rather than at either of the two places that deliver it, and that is the point: a
    person pressing the button in the rail and a pass finding its reserve crossed are asking for the
    same thing, so the words have to be one string. Two writers, one composition; see `Service.hand_off`
    and `readying`.

    `guiding` is whatever a person wants this handoff pointed at, **appended** to the standing ask
    rather than replacing it, because the two say different things: the base is what a handoff *is*
    and has to be there whether or not anybody adds to it, where a note like "dwell on the parser" on
    its own is an instruction to summarise a summary. Nothing at all is the automatic case, which is
    also the ordinary one.

    No boundary on it, which is the asymmetry that makes a handoff work: the context has to survive
    long enough to be summarised, so it is the *document* that clears it - see `handing_through`.
    """
    said = ASKING if not (steer := (guiding or "").strip()) else f"{ASKING}\n\n{steer}"
    return records.Handoff(said=said).recorded()


def parse_delivered(recorded: object) -> records.Delivered:
    """
    One inbox entry, as whichever of the three things a person can put in a session it holds.

    The one value here that crossed a trust boundary: a step's result was produced by code in this
    process, where this was appended by an HTTP handler on behalf of whoever posted the form. That is
    also why the tag is load-bearing rather than a second copy of the key: the store names an entry,
    so nothing but the record says whether it is to be told to a model.
    """
    return records.DELIVERED.validate_python(recorded)


def recorded_messages(said: Sequence[ModelMessage]) -> dict[str, object]:
    """What a turn's agent run produced, as the value the store's codec will take."""
    return records.Messages(messages=ModelMessagesTypeAdapter.dump_python(list(said), mode="json")).recorded()


def parse_messages(recorded: object) -> tuple[ModelMessage, ...]:
    return tuple(ModelMessagesTypeAdapter.validate_python(records.Messages.model_validate(recorded).messages))


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


def text_at(recorded: Mapping[str, object], field_name: str) -> str:
    """
    One optional string out of a record, with anything that is not one reading as absent.

    For the fields where "not there" and "not usable" are the same answer, which is what the parser
    below says about a base and a branch: both mean the session names none, and a checkpoint holding
    a number under one of those keys was never written by this console anyway.
    """
    held = recorded.get(field_name)
    return held if isinstance(held, str) else ""


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
        # Re-parsed on the way out rather than trusted because it was checked on the way in. What
        # these become is a `git` argument, and the record is the one thing between the form that
        # checked them and the pass that uses them - a checkpoint edited by hand, or written by
        # `scripts/seed.py`, has been through no boundary at all. Absent and unusable read the same,
        # which is right: both mean this session names no base and no branch, and every session
        # written before these existed is in exactly that state.
        base=parse_commitish(text_at(recorded, BASE_FIELD)),
        branch=parse_branch(text_at(recorded, BRANCH_FIELD)),
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
        # The tag every record carries, so a bag of records still says what this one is. The choice
        # is not a `records` model, and deliberately: it is already a record this console owns and has
        # grown fields twice with no migration, and its parser encodes things a schema cannot say.
        # See `records.Step`.
        "kind": "choice",
        ENDPOINT_FIELD: chosen.endpoint,
        "model": chosen.model,
        REPOSITORY_FIELD: chosen.repository,
        BASE_FIELD: chosen.base,
        BRANCH_FIELD: chosen.branch,
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


def opening(recorded: Mapping[str, object], turn: int) -> records.Delivered | None:
    """
    The message a turn opened on, or nothing at all for a turn nobody has opened yet.

    One lookup through two keys, because that is what the inbox costs: the turn records which entry
    it took and the entry holds what was said. Every reader wanting a turn's own message comes
    through here rather than doing the pair itself.
    """
    at = recorded.get(opened_key(turn))
    return None if at is None else parse_delivered(recorded[parse_cursor(at)])


def forgets(recorded: Mapping[str, object], turn: int) -> bool:
    """
    Whether this turn opens on a clean history, which is what `/forget` records.

    A turn nobody has opened forgets nothing, so an absent one is `False` rather than a failure:
    `reached` asks this about the turn it is about to return, which is often one no pass has reached.

    Only a message a turn opens on can carry the flag, which is not a special case but the same fact
    twice: forget is never a steer, so what asks for it is delivered as a message that must open a
    turn. Which records those are is `records.forgets`'s to say, so a session's own `/forget` and the
    boundary a handoff carries are one question here rather than two.
    """
    said = opening(recorded, turn)
    return said is not None and records.forgets(said)


def instructions_key(began: int) -> StepKey:
    """
    Where this stretch of context records what it is answered under, named by the turn it starts at.

    **Not `turn:{n}:instructions`**, deliberately, and the fork is what decides it: `before` copies
    turn-prefixed keys by shape, so a turn-shaped name would carry a parent's instructions into a
    branch that may have attached a repository the parent never had. Named this way a fork composes
    its own, which is what a session that can differ in its choice should do.
    """
    return f"instructions:{began}"


def history_began(recorded: Mapping[str, object], turn: int) -> int:
    """
    The turn the model's history currently starts at: the last forget at or before `turn`, else 0.

    **What a session is answered under is settled per stretch of context rather than per session**,
    and a forget is what ends one. That is not a weaker promise than "once, for life": instructions
    sit in front of the cached prefix, so what recomposing costs is every request that would have
    read that prefix from cache - and a forget has just thrown the whole prefix away. Recomposing
    exactly there is therefore free, and it is also the one moment a reader might reasonably expect
    a repository's edited guidance to be picked up.

    The same `forgets` predicate `reached` clears history on, over the same turns, so the two agree
    about where a context begins by asking one question rather than two.
    """
    return max((each for each in range(turn + 1) if forgets(recorded, each)), default=0)


def instructed_in(recorded: Mapping[str, object], turns: int) -> dict[int, str | None]:
    """
    What each stretch of context in this conversation is answered under, by the turn it begins at.

    One per stretch rather than one per session, because a forget composes again: a single system
    prompt at the top of the page would be the newest one standing over turns that were answered
    under an older one. Keyed by the turn a stretch begins at, which is where the page draws it -
    under that turn's own rule, which for a forget is the rule saying the context was cleared there.

    **`None` is a stretch whose instructions have not been composed yet**, which is a real state and
    not a missing record: composing reads the repository's guidance out of a worktree the pass is the
    one to plant, so a session's first turn is queued before there is anything to compose. The page
    draws that as a system prompt panel with nothing in it yet, so what is coming is visible from the
    moment the message is.

    A stretch that has *answered* under instructions this console never recorded is absent
    altogether, which is every session written before it recorded them. Absent rather than pending,
    because a turn that has landed will never compose anything now, and a panel waiting forever on a
    record nobody will write is the one state a reader cannot diagnose.
    """
    told: dict[int, str | None] = {}
    for turn in range(turns):
        if turn and not forgets(recorded, turn):
            continue
        said = recorded.get(instructions_key(turn))
        if said is not None:
            told[turn] = parse_instructions(said)
        elif recorded.get(messages_key(turn)) is None:
            told[turn] = None
    return told


def reached(recorded: Mapping[str, object]) -> Reached:
    """
    The first turn with no answer, and every message the model is to be told before it.

    Consecutive by construction: turn *n* is only reached once turn *n-1* recorded its messages,
    so the scan stops at the first gap rather than searching for the highest key. A checkpoint
    with a hole in it is not a state this body can produce.

    **A turn that forgets empties the history rather than truncating the walk**, so what is recorded
    above it is still read and still rendered - the checkpoint is still the whole conversation - and
    only what the model is handed starts again here. That is the same split `command` makes, applied
    to turns rather than to one kind of record.

    The **last** turn is asked too, outside the loop, and that is the half this can be quietly wrong
    about: the walk is conditioned on a turn having *answered*, so a forget on the turn about to run
    has not been seen by it. Missed, the first pass would answer that turn on the whole conversation
    and a resumed pass would answer it on nothing, which is the one disagreement between two passes
    this whole mechanism exists not to have.
    """
    history: list[ModelMessage] = []
    turn = 0
    while (answered := recorded.get(messages_key(turn))) is not None:
        if forgets(recorded, turn):
            history.clear()
        history.extend(parse_messages(answered))
        turn += 1
    return Reached(turn=turn, history=() if forgets(recorded, turn) else tuple(history))


# What became of a call, as Pydantic AI's own `ToolReturnPart` states it. Carried rather than
# reduced to a boolean, because the three ways a call can fail to succeed are different things to
# read: a tool that raised, one a person refused, and one that was cut off partway.
type Outcome = Literal["success", "failed", "denied", "interrupted"]

# Which pigment a panel is drawn in, and the axis the palette runs on: `prompt` and `steer` are what
# reached the model and the rest is what it produced. A new kind takes its side from that rather than
# a colour chosen for it.
#
# **Each kind is named after the thing it holds, in the word the page prints.** A reader who learns
# `steer` from a panel finds `records.Steer` behind it, where `you (steering)` sent them looking for
# a word the code does not use. What the three on the person's side gave up by no longer all reading
# `you` is a non-colour cue for the side, which `data-side` and the hue still carry.
#
# `prompt` overlaps `records.Prompt`, which is narrower: the record is a message that *must* open a
# turn, where this is whatever message a turn opened on, and a `Steer` arriving at an idle session is
# both. That is the collision this vocabulary already has with `StepKind` on `command` and `tool`, and
# it is safe for the same reason - they never mix, and mypy refuses the crossing.
#
# `steer` is its own kind rather than a `prompt` panel with a flag, because the key filters by kind
# and the two are worth filtering apart: reading a long turn back, what somebody said *into* it is a
# different thing from the question that opened it. It takes the person's hue all the same, since the
# axis is about who produced the text and that is the same person.
#
# `command` is on the person's side for the same reason `steer` is. It is the one kind on that side
# no model ever saw, which its label used to say by being `you (ran)` and which the `title` on its
# role says now: a hue is for who produced the text, not for who was told.
#
# `system-prompt` is the one kind no `Block` produces, because what it draws is not part of any
# turn's exchange: it is what every request carried, which belongs to the session. It is a kind all
# the same so that the key governs it like the rest - a standing block of guidance at the top of a
# transcript is exactly the thing a reader who has read it once wants quieted - and it takes the
# person's hue, since what is in it was written by the operator and by whoever wrote the
# repository's `AGENTS.md`.
#
# `guidance` is the file the console handed over mid-turn because the model reached into a part of
# the repository carrying its own, and it is *not* `system-prompt` even though the two hold the same
# sort of text. What tells them apart is mechanical rather than editorial: a system prompt is
# Pydantic AI `instructions`, a per-request parameter re-rendered on every request in front of the
# cached prefix, where this is a `SystemPromptPart` appended into the message history at a position.
# Two mechanisms, two places in the request, two things a reader may want to quiet separately - so
# two words, by the same rule that keeps `steer` apart from `prompt`. How each one reaches the model
# differs too, and by more than the wire: see the guidance section in `AGENTS.md`. It takes the
# person's hue for the reason `system-prompt` does.
#
# `handoff` is the one kind nobody wrote: the console asks for a handoff in a message of its own, and
# what comes back opens the next turn on a cleared context. A reader has to be able to tell that at a
# glance, which is the whole of why it is a kind rather than a `prompt` with a flag - the same
# argument that keeps `steer` apart. It is on the person's side because the axis is who produced the
# text, and what a handoff holds was produced by this session rather than by the model about to be
# handed it; the `title` on its role is what says the console composed it, exactly as `command`'s says
# no model was told.
type Kind = Literal[
    "prompt", "handoff", "steer", "command", "assistant", "thinking", "tool", "system-prompt", "guidance"
]


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
class Guidance:
    """
    What the console handed the model about a part of the repository it was reaching into.

    Its own type rather than a `Prose` for the reason `Steering` is one: `panelled` reads a panel's
    kind off its blocks, and this drawn as prose would read as the model saying it. Nobody in the
    conversation said it - the console did, out of a file somebody committed.

    Its own `Kind` too, rather than the standing system prompt's, and the line between them is
    mechanical: that one is `instructions`, a per-request parameter in front of the cached prefix,
    where this is a `SystemPromptPart` at a position in the history. See the note above `Kind`.
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

    took: timedelta | None = None
    """
    How long the call ran, once it has come back and where a pass was there to time it.

    On the call rather than on its `Returned`, because it is a fact about the running rather than
    about the answer: a call that failed took just as long as one that worked. Absent while a call
    is still out, and absent for a call whose duration was never recorded, which are two states a
    reader reads the same way and neither of which is a duration of zero.
    """


@dataclass(frozen=True, slots=True)
class Result:
    """
    What a command exited with, said, and took: everything a run is worth knowing once it is over.

    One value rather than three fields on `Command`, so "it has finished" is a single thing to test
    and cannot be half true. That is `ToolUse.returned`'s shape, arrived at from the same place.

    Its own type rather than `Returned`, which is the tool's. They look alike and are not: a tool's
    outcome is one of the four words Pydantic AI's `ToolReturnPart` uses, where a command's is an
    exit status a program chose, and collapsing the two would mean inventing a mapping between them.

    `status` is the process's own exit code, kept as the number rather than reduced to a boolean:
    `git diff --quiet` exits 1 to mean *there are changes*, so a console that only said "failed"
    would be lying about a command doing exactly what it was asked.

    `output` is stdout and stderr together, in the order they were written, which is what a terminal
    shows and what somebody reading a run actually wants. Kept apart they interleave wrongly or not
    at all, and no reader has ever wanted a build's errors in a second column.
    """

    status: int
    output: str

    took: timedelta | None = None
    """
    How long it ran, and nothing where the console stopped before it could be timed.

    Inside this record rather than beside it, unlike a tool call's; see `result_key`.
    """


@dataclass(frozen=True, slots=True)
class Command:
    """
    Something the person ran themselves, and what came of it once it has run.

    `result is None` is the whole of "still running", exactly as `ToolUse.returned is None` is the
    whole of "still out", and for the same reason: a flag beside a result is a second thing to keep
    in step with it.

    No model ever sees one of these. It is in the checkpoint because that is the only place this
    console keeps anything, and it is out of the message history because telling a model what you ran
    is a message somebody writes. See the key scheme.

    `entry` is the inbox key the command arrived under, and it is on the block because the page needs
    a name for the fold that does not move. A panel's own position does move - a response landing
    above pushes it down - where an entry is what it is for ever.
    """

    entry: str
    text: str
    result: Result | None = None


type Block = Prose | Steering | Guidance | Command | Reasoning | ToolUse


@dataclass(frozen=True, slots=True)
class Panel:
    """
    A run of blocks of one kind, which is the unit the page draws an edge down.

    `at` is the panel's position within its turn, so a panel's identity is `turn` and `at` and
    nothing else. That is what a permalink can be built on: a turn's panels only ever grow at the
    end, where a position in the whole transcript would shift under a reader whenever an earlier
    turn they had typed past was answered.

    The command panel is the one exception, and it is worth knowing before building on the address.
    A turn's commands are drawn *after* its model panels, so a response arriving renumbers the panel
    they sit in while everything before it stays put. Anything that has to survive a running turn is
    named from the turn and the record's own slot instead; see `command_block` in `pages.py`.
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

    forget: bool = False
    """
    Whether the turn this panel opens was asked with the model's context cleared.

    Carried on the turn's first panel beside `tree`, and for the same reason: it is a fact about the
    turn rather than about the message, and the rule that opens the turn is what draws it. Where a
    turn begins is decided once, by the panels, rather than by a second list beside them.

    `False` on every other panel, which is not a claim about them: only the panel that opens a turn
    is ever asked.
    """

    asked: int | None = None
    """
    Which model request of the turn produced these blocks, and nothing where a person did.

    One request and never several, because a panel is cut at a request boundary as well as at a
    change of kind: two responses that both answer in prose are two panels rather than one merged
    run. That is what gives a rule somewhere to sit, and it is what lets `transcript_region` draw a
    rule per round trip by watching this change down a turn.

    `None` for the person's own panel and for a steer, which is the same thing said twice: neither
    came out of a response, so neither opens a request.
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

    `took` is the same figure in time and follows the same rule for the same reason. What it adds up
    is the round trips to the provider and nothing else, so it is what a turn spent *waiting on the
    model*: the calls it made in between ran here and are timed on their own panels, and summing
    those into this would double-count a batch that ran at once.

    `context` and `cached` are the one pair here that is **not** a sum, and that is what separates a
    level from a total. Every request of a turn carries the whole conversation, so adding their input
    counts up says what the provider charged for and says it several times over about the same
    tokens: a turn of four round trips reports four contexts and would draw a window four times as
    full as it is. What is true of the window is the *last* request's input, which is where the
    conversation had got to when the turn ended.
    """

    asked: int
    answered: int
    cost: Decimal | None
    took: timedelta | None = None

    context: int = 0
    """
    How much the last request of this turn carried, which is how much of the window is spoken for.

    Zero where nothing has been asked yet, which is the same answer as "nothing to say about the
    window" and is drawn as no figure at all. See `spent_on` for why this is a level and not a sum.
    """

    cached: int = 0
    """
    How much of that context the provider read out of its cache rather than being sent afresh.

    Part of `context` rather than beside it: Pydantic AI normalises every wire so `input_tokens`
    already includes the cache reads, which is the same nesting `priced` subtracts against.
    """


def response_took(answered: ModelResponse) -> timedelta | None:
    """
    How long the round trip that produced this response took, as the pass that made it recorded.

    Read off `metadata` rather than out of a key of its own, which is what makes it the one figure
    here that needs no walk: the response carries it whether it came back from `turn:{n}:model:{i}`
    or out of `turn:{n}:messages`. See `Stepping.stamp`, which is the only thing that writes it.

    Nothing for every response recorded before this console timed anything, and nothing for a
    response some other writer put there, which is the same answer to the same question.
    """
    return parse_took((answered.metadata or {}).get(TOOK))


def spent_on(responses: Sequence[ModelResponse]) -> Spent:
    """
    What a turn's model requests came to, summed over however many of them it took.

    A turn is one exchange to a reader and several requests to a provider, one before each batch of
    tool calls, so the figure worth showing is the turn's own. The same sum serves both readings of
    a turn, because a response carries its usage whether it was read back from `turn:{n}:messages`
    or from the `turn:{n}:model:{i}` step that recorded it.

    The window is the exception, and it is read off the **last** response rather than summed. Each
    request of a turn carries the whole conversation again, so what the sum answers is what the
    provider charged for, where what a reader wants to know is how much of the window is gone - and
    those are the same number only for a turn that took one round trip. The last request is the
    furthest the conversation got, which is what the turn leaves behind it.
    """
    charged = [response.usage.cost for response in responses]
    settled = [one for one in charged if one is not None]
    last = responses[-1].usage if responses else None
    return Spent(
        asked=sum(response.usage.input_tokens for response in responses),
        answered=sum(response.usage.output_tokens for response in responses),
        cost=sum(settled, Decimal(0)) if settled and len(settled) == len(charged) else None,
        took=whole(response_took(response) for response in responses),
        context=last.input_tokens if last is not None else 0,
        cached=last.cache_read_tokens if last is not None else 0,
    )


def whole(taken: Iterable[timedelta | None]) -> timedelta | None:
    """
    Several durations as one, or nothing at all where any of them is nothing.

    The rule `cost` follows, for the reason `cost` follows it: a total quietly missing one of its
    parts reads as the whole and understates it, and a reader has no way to tell that from a turn
    that really was that quick. Recorded before durations existed, every response in a turn is
    unstamped, so what its rule reports is no figure rather than a suspiciously small one.
    """
    total = timedelta()
    timed = False
    for one in taken:
        if one is None:
            return None
        total += one
        timed = True
    return total if timed else None


def altogether(spent: Iterable[Spent]) -> Spent:
    """
    Every turn's spend as the session's, under the rule one turn's already follows.

    Unknown anywhere is unknown for the whole, so a session with one unpriced turn reports no total
    rather than the sum of the rest: what a person reads off a total is what the session has cost
    them, and a figure quietly missing a turn is worse than no figure.

    The window carries forward from the last turn that said anything about it, by `spent_on`'s own
    rule one scale up: a session's context is where its most recent request left it, and a turn that
    made no request at all knows nothing about the window rather than knowing it is empty. That is
    also what makes a forget read correctly here, since the turn after one starts a smaller context
    and the session's figure follows it down.
    """
    counted = tuple(spent)
    charged = [one.cost for one in counted]
    settled = [one for one in charged if one is not None]
    reached = [one for one in counted if one.context]
    return Spent(
        asked=sum(one.asked for one in counted),
        answered=sum(one.answered for one in counted),
        cost=sum(settled, Decimal(0)) if settled and len(settled) == len(charged) else None,
        took=whole(one.took for one in counted),
        context=reached[-1].context if reached else 0,
        cached=reached[-1].cached if reached else 0,
    )


@dataclass(frozen=True, slots=True)
class Request:
    """
    One round trip to the model, as the rule at its boundary reports.

    A request is the unit three recorded things are actually about - the tree taken before it, the
    response it came back with, and what that response cost - and none of them is about a panel. That
    is the whole reason a rule stands here: hung on panels, each had to be attributed to a chosen one.
    """

    at: int
    tree: str | None
    spent: Spent


def requests_in(recorded: Mapping[str, object], turn: int, responses: Sequence[ModelResponse]) -> tuple[Request, ...]:
    """
    What each of a turn's model requests is worth saying, in the order they were made.

    The tree comes from `turn:{n}:tree:{i}` and the spend from the response's own usage, which are
    the two halves of one request written by opposite ends: the snapshot is taken before the ask and
    the usage comes back with the answer. Reading them together here is what lets one rule say both.
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

    system_prompts: Mapping[int, str | None] = field(default_factory=dict)
    """
    What each stretch of context is answered under, by the turn it begins at, with `None` for one
    whose instructions have not been composed yet.

    On the transcript rather than on a panel, because it belongs to a stretch rather than to any one
    exchange: it is carried by every request in that stretch and is not a thing anybody said at a
    position. It is here at all because a console that shows what a model answered and hides what it
    was told is showing half of how a turn happened.

    Read from `instructions:{n}`, which a pass writes before it makes the stretch's first request, so
    the panel is drawn while a turn is still being answered rather than only once it has landed. See
    `instructed_in` for what an absent one and a pending one each mean.
    """

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
    Each turn's model requests, which is what the rules within a turn are drawn from.

    Keyed by turn and indexed within it, so a panel's `asked` is a lookup rather than a search. The
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
            if panel.turn == turn and panel.kind == "prompt":
                return "\n".join(block.text for block in panel.blocks if isinstance(block, Prose))
        return None


def kind_of(block: Block) -> Kind:
    match block:
        case Prose():
            return "assistant"
        case Steering():
            return "steer"
        case Guidance():
            return "guidance"
        case Command():
            return "command"
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

Half of what a panel is cut by, the kind being the other half. Carried on the block rather than
worked out afterwards, because the walk that reads a response into blocks is the only one that knows
which response it was reading.
"""


def blocks_in(
    response: ModelResponse, returned: Mapping[str, Returned], took: Mapping[str, timedelta]
) -> Iterator[Block]:
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
                yield ToolUse(
                    tool=tool, arguments=part.args_as_json_str(), returned=returned.get(call), took=took.get(call)
                )
            case _:
                continue


def interjected(message: ModelRequest) -> Iterator[Block]:
    """
    Anything put into a request the *agent* made, which is a person steering or the console guiding.

    Two part types and therefore two blocks, told apart by what carried them rather than by anything
    written beside them: a person's message is a `UserPromptPart` and the console's own is a
    `SystemPromptPart`, which is exactly why the delivery uses one. In part order, so a turn where
    both arrived draws them as they arrived.

    A turn's own opening message arrives as a `UserPromptPart` too, and is skipped by `parted` rather
    than here: what tells them apart is position, since the opening message is the first thing in a
    turn and a steer never is. Everything else in a mid-turn request is a tool result, which is read
    as part of the call it answers rather than on its own.
    """
    for part in message.parts:
        if isinstance(part, UserPromptPart) and isinstance(part.content, str) and part.content.strip():
            yield Steering(text=part.content)
        elif isinstance(part, SystemPromptPart) and part.content.strip():
            yield Guidance(text=part.content)


def parted(messages: Sequence[ModelMessage], took: Mapping[str, timedelta]) -> tuple[Sourced, ...]:
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

    How long each call took is the one thing a settled turn cannot say for itself, so it arrives from
    the caller: a result is a `ToolReturnPart` in the message list, where a duration is recorded under
    `turn:{n}:tool:{id}` beside what the call returned. Asked for rather than defaulted, because a
    reading that quietly dropped it would differ from the running turn's reading of the same call,
    which is the one difference this pair of walks exists not to have.
    """
    returned = returns_in(messages)
    blocks: list[Sourced] = []
    at = -1
    for index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            at += 1
            blocks.extend((block, at) for block in blocks_in(message, returned, took))
        elif index > 0:
            blocks.extend((block, None) for block in interjected(message))
    return tuple(blocks)


def blocks_of(messages: Sequence[ModelMessage], took: Mapping[str, timedelta]) -> tuple[Block, ...]:
    """What a turn's messages are worth reading as, in the order they were produced."""
    return tuple(block for block, _ in parted(messages, took))


def recorded_instructions(said: str) -> dict[str, object]:
    """What this session is answered under, as the JSON-native value the store's codec will take."""
    return records.Instructions(said=said).recorded()


def parse_instructions(recorded: object) -> str:
    """What a session records itself as being answered under, or a loud failure if it is not that."""
    return records.Instructions.model_validate(recorded).said


def system_prompt_in(messages: Sequence[ModelMessage]) -> str | None:
    """
    What the model was told about itself in these messages, which is the system prompt it carried.

    **What the page draws is `instructions:{n}`, not this**, because that record exists before the
    first request where these messages exist only after the turn. This is the other end of the same
    fact, and holding the two against each other is what pins the claim that record makes: the
    recorded string is spoken verbatim, so what a stretch records and what its requests carried have
    to be one string rather than two that drift.

    The **last** one rather than the first, because a turn's requests all carry the same instructions
    unless something under them moved. Nothing where a turn recorded none, which is a turn answered
    before this console said anything at all.
    """
    told: str | None = None
    for message in messages:
        if isinstance(message, ModelRequest) and message.instructions is not None:
            told = message.instructions
    return told


def returned_step(held: records.Returned) -> Returned:
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
    if held.returned is None:
        return Returned(outcome="success", content="")
    said = held.returned if isinstance(held.returned, str) else to_json(held.returned).decode()
    return Returned(outcome="success", content=said)


def recorded_command(said: str) -> dict[str, object]:
    """What the person ran, as the JSON-native value the store's codec will take."""
    return records.Command(said=said).recorded()


def recorded_result(result: Result) -> dict[str, object]:
    """
    What a finished command is as the JSON-native value the store's codec will take.

    Field by field rather than through a splat, because these are two types that happen to agree
    today: one is what a reader sees and one is what the store holds, and a field added to either
    should fail here rather than arrive silently.
    """
    return records.Result(status=result.status, output=result.output, took=result.took).recorded()


def parse_result(recorded: object) -> Result:
    """
    What a command came back with, or a loud failure if the checkpoint holds something else.

    Strict about the pair that must be there and lenient about the duration, which is the same split
    `parse_choice` makes: a status and an output are what this record *is*, where a run nothing timed
    is an ordinary state a reader already has a rendering for.
    """
    held = records.Result.model_validate(recorded)
    return Result(status=held.status, output=held.output, took=held.took)


def ran_in(recorded: Mapping[str, object], held: Sequence[Posted], turn: int) -> tuple[tuple[int, Command], ...]:
    """
    Every command run while this turn was in hand, each with how many of its requests had answered.

    **That number is where the command goes on the page**, and it is read rather than recorded: the
    store files everything in the order it arrived, so counting this turn's model records ahead of a
    command's entry says how far the reply had got when somebody typed it. Nothing had to be written
    at the time, which is what makes it safe - a writer racing the pass for a position in its
    sequence is exactly what a steer costs and a command has no reason to pay.

    A command with no result beside it is one still running, which is what a reader watching one sees
    and what a command a killed console leaves behind. The second is the honest record: the process
    that would have written the result is gone, and inventing one would be a claim about something
    nobody observed.
    """
    answering = f"{turn_prefix(turn)}:model:"
    said = {at.key: at.what for at in held if isinstance(at.what, records.Command)}
    ran: list[tuple[int, Command]] = []
    made = 0
    for key in recorded:
        if key.startswith(answering):
            made += 1
        elif (was := said.get(key)) is not None:
            ran.append((made, Command(entry=key, text=was.said, result=result_in(recorded, key))))
    return tuple(ran)


def drains_in(recorded: Mapping[str, object], turn: int) -> tuple[str, ...]:
    """
    How far this turn had read at each point it read, in the order the cursors were recorded.

    The turn's own opening entry leads them, because that is where its reading of the inbox begins:
    the first request's steers are the entries between the message that opened the turn and the
    cursor that request recorded.

    One walk of the checkpoint in *record* order rather than two lookups by name, so nothing here
    depends on the drains being numbered consecutively - which they are, but the order is the
    property being used and the store already guarantees it.
    """
    heard = f"{turn_prefix(turn)}:heard:"
    return tuple(
        parse_cursor(value) for key, value in recorded.items() if key == opened_key(turn) or key.startswith(heard)
    )


def steered(held: Sequence[Posted], since: str, upto: str) -> tuple[str, ...]:
    """What somebody said into the turn between two cursors, which is one drain's worth of steers."""
    return tuple(at.what.said for at in held if isinstance(at.what, records.Steer) and since < at.key <= upto)


def told_in(recorded: Mapping[str, object], held: Sequence[Posted], turn: int) -> tuple[tuple[str, ...], ...]:
    """
    What each of this turn's requests was told, in the order the requests were made.

    Recovered from the cursors rather than from a list of texts, which is what the inbox replaced: a
    request's steers are the entries between the cursor before it and the one it recorded.
    """
    return tuple(steered(held, since, upto) for since, upto in pairwise(drains_in(recorded, turn)))


def since_last(recorded: Mapping[str, object], turn: int) -> str:
    """
    How far this turn has read, which is the cursor its last drain left.

    An empty string for a turn with no drains at all, which sorts below every inbox key: a turn
    nobody has opened has read nothing, so everything is still ahead of it.
    """
    drains = drains_in(recorded, turn)
    return drains[-1] if drains else ""


def unread_in(
    recorded: Mapping[str, object], held: Sequence[Posted], turn: int, *, listening: bool
) -> tuple[tuple[str, ...], tuple[Posted, ...]]:
    """
    What nobody has read, split into what this turn could still take and what will open its own turn.

    **A message that must open a turn is the boundary and a steer is not**, which is the difference
    between the records said one more way: a steer is a message the running turn may fold in, so it
    is drawn where it would go if it did; a `Prompt` or a `Handoff` cannot be folded in by anybody, so
    one of those and everything behind it are messages waiting for turns of their own. `records.opens`
    is what decides which, here as in the drain, so the page and the pass cannot come to disagree
    about where a turn's reading stops.

    A steer behind one is drawn as waiting for a turn too, even though the pass will fold it into the
    turn that boundary opens. That is the honest reading rather than a shortcoming: what a page can
    say is that neither has been read and that the message between them is where this turn's reading
    stops.

    `listening` is whether this turn is still being answered, and with nothing listening every unread
    message is one waiting for a turn. Without it a message that arrived just after a turn ended
    would be drawn as a steer of that turn, which is a panel the settled reading does not draw at
    all: it reads a finished turn from its own messages, where a steer nobody was told is not.

    What comes back second is everything from that boundary on, commands included, because it is
    where this turn's own entries stop rather than a list of messages: a command run after a message
    nobody has opened a turn on belongs beside that message, not back up in the turn before it.
    """
    unread = tuple(at for at in held if at.key > since_last(recorded, turn))
    # A turn still being answered reaches everything up to the first message it may not fold in; one
    # that has finished reaches no message at all, and still owns the commands run beside it.
    reaching = (
        (lambda at: not records.opens(at.what)) if listening else (lambda at: isinstance(at.what, records.Command))
    )
    reachable = tuple(takewhile(reaching, unread))
    return tuple(at.what.said for at in reachable if isinstance(at.what, records.Steer)), unread[len(reachable) :]


def queued_in(
    recorded: Mapping[str, object], inbox: Sequence[Posted], opened: Sequence[str], *, listening: bool
) -> tuple[Posted, ...]:
    """
    Everything past the last turn, which is what a page draws after the conversation so far.

    Every entry of a session nobody has answered yet, since no turn has opened to own any of them;
    otherwise what the last turn does not own. Commands are in it, so one run beside a message
    waiting for a turn is drawn beside that message rather than back in the turn before it.
    """
    if not opened:
        return tuple(inbox)
    _, queued = unread_in(recorded, held_in(inbox, opened, len(opened) - 1), len(opened) - 1, listening=listening)
    return queued


def result_in(recorded: Mapping[str, object], entry: str) -> Result | None:
    """What the command delivered under `entry` came to, or nothing where it is still running."""
    came = recorded.get(result_key(entry))
    return None if came is None else parse_result(came)


def owned_in(
    recorded: Mapping[str, object], inbox: Sequence[Posted], opened: Sequence[str], turn: int, *, listening: bool
) -> tuple[Posted, ...]:
    """
    The entries this turn owns: its span, up to where the messages waiting for a turn of their own
    begin.

    The last opened turn's span runs to the end of the inbox, because nothing later has opened to
    stop it. That is the right span for reading its cursors and wrong for drawing it, since what is
    queued behind it has not happened *in* it. This is the drawing answer.
    """
    held = held_in(inbox, opened, turn)
    _, rest = unread_in(recorded, held, turn, listening=listening)
    return held if not rest else tuple(at for at in held if at.key < rest[0].key)


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


def refusal_in(recorded: Mapping[str, object]) -> records.Refused | None:
    """
    Why this conversation stopped and will not start again on its own, or nothing where it has not.

    Asked of the turn being answered and of no other. A refusal recorded against a turn that later
    answered is history and the transcript is where history goes; only one on the turn nothing has
    got past means the session has stopped.

    Neither index is searched for. The turn is the first with no messages, which is what a pass would
    open next, and the request is the one after the last that answered, which is what `responded`
    already counts. The turn walk is `reached`'s without the history: this needs the number and not
    the messages, and parsing every turn's messages is the expensive half of that function, on a path
    a page takes on every render.
    """
    turn = 0
    while messages_key(turn) in recorded:
        turn += 1
    said = recorded.get(refused_key(turn, len(responded(recorded, turn))))
    return None if said is None else parse_refused(said)


def called_in(responses: Sequence[ModelResponse]) -> Iterator[str]:
    """Every call id a turn's responses asked for, which is what names both records a call has."""
    for response in responses:
        for part in response.parts:
            if isinstance(part, ToolCallPart):
                yield part.tool_call_id


def calls_in(
    recorded: Mapping[str, object], turn: int, responses: Sequence[ModelResponse]
) -> dict[str, records.Returned]:
    """
    Every recorded call of a turn, by the call id that names which one it is about.

    Asked of the ids the responses already carry, the way `tool_key` is: the writer and the reader
    are handed the same id, so nothing scans the checkpoint for keys of a shape. A call still out is
    simply absent, which needs no sentinel now that a recorded return is a record: a tool that
    returns nothing records `returned` of `None` inside one, where it used to be a bare `None`
    indistinguishable from a key nobody had written.
    """
    return {
        call: parse_returned(held)
        for call in called_in(responses)
        if (held := recorded.get(tool_key(turn, call))) is not None
    }


def tooks_in(recorded: Mapping[str, object], turn: int, responses: Sequence[ModelResponse]) -> dict[str, timedelta]:
    """
    How long each of a turn's calls took, by the call id that names which one it is about.

    A call nothing timed is simply absent, which `ToolUse.took` reads as no figure rather than as
    none of it.

    The same mapping serves both readings of a turn - the one built from `turn:{n}:messages` and the
    one built from the model steps - because a duration is recorded in neither of them and beside
    both, in the record that holds what the call came back with.
    """
    return {call: held.took for call, held in calls_in(recorded, turn, responses).items() if held.took is not None}


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
    held = held_in(posted_in(recorded), openings(recorded), turn)
    blocks = blocks_from(recorded, held, turn, responded(recorded, turn))
    return tuple(block for block, _ in alongside(blocks, ran_in(recorded, held, turn)))


def blocks_from(
    recorded: Mapping[str, object], held: Sequence[Posted], turn: int, responses: Sequence[ModelResponse]
) -> tuple[Sourced, ...]:
    """
    One running turn's recorded steps as blocks, each with the request that produced it.

    Steers included, and they have to be: with Send deciding for itself whether a message steers, a
    turn that drew only what the *model* had said would take somebody's message and show nothing at
    all until the turn ended. The cursors are what make that possible - they say how far down the
    inbox each request had read, so a steer goes above the response it shaped, exactly where the
    settled reading will put it.

    Anything no request has carried goes at the end, which covers the steer nobody has read yet and
    the one a redirect took at the end of a run. That is its right place while it is pending, since
    nothing has been said since; a redirected one moves above its answer when that answer lands,
    which is the one reorder this reading performs.
    """
    # One walk of the recorded calls, read twice: what each came back with and how long it took. Two
    # walks would eventually disagree about which calls a turn has heard from.
    called = calls_in(recorded, turn, responses)
    returned = {call: returned_step(said) for call, said in called.items()}
    took = {call: said.took for call, said in called.items() if said.took is not None}
    told = told_in(recorded, held, turn)
    taking, _ = unread_in(recorded, held, turn, listening=True)
    blocks: list[Sourced] = []
    for at, response in enumerate(responses):
        blocks.extend((Steering(text=text), None) for text in (told[at] if at < len(told) else ()))
        blocks.extend((block, at) for block in blocks_in(response, returned, took))
    blocks.extend((Steering(text=text), None) for text in taking)
    return tuple(blocks)


def alongside(sourced: Sequence[Sourced], ran: Sequence[tuple[int, Command]]) -> tuple[Sourced, ...]:
    """
    A turn's blocks with the commands run during it put back where they were run.

    **Merged rather than appended**, which is the whole of what filing a command as an inbox entry
    buys: a command sits after the requests that had answered when somebody typed it, so its panel
    stays where it happened instead of sinking down the turn as each later answer lands above it.
    Collected at the end, a reader watching a turn saw the command they had just run move.

    `asked` is `None` on a command, as it is on a steer and on the person's own message, because no
    model request produced it. That is what keeps it from opening a rule: a rule stands at a request
    boundary, and a command is not one.

    Both readings of a turn come through here with the same positions, which is what keeps the
    running one a prefix of the settled one: nothing moves when `turn:{n}:messages` lands.
    """
    waiting = list(ran)
    blocks: list[Sourced] = []
    for block, asked in sourced:
        while waiting and asked is not None and waiting[0][0] <= asked:
            blocks.append((waiting.pop(0)[1], None))
        blocks.append((block, asked))
    blocks.extend((command, None) for _, command in waiting)
    return tuple(blocks)


def runs[T, K](items: Sequence[T], key: Callable[[T], K]) -> Iterator[tuple[int, K, tuple[T, ...]]]:
    """
    One turn's items cut into runs sharing a key, each with the position that names it.

    Consecutive rather than gathered, so the page shows the order the model worked in: a reply
    that reasoned, called a tool, and then answered is three runs in that sequence, not a
    reasoning run and a tool run hoisted above the answer.

    Generic over the key as well as the item, because what a panel is cut by is a pair: the request
    that produced a block and the kind of thing it is. A second implementation of the same cut would
    eventually disagree with this one about where a panel begins.
    """
    for at, (shared, run) in enumerate(groupby(items, key=key)):
        yield at + 1, shared, tuple(run)


def panelled(turn: int, sourced: Sequence[Sourced]) -> Iterator[Panel]:
    """
    One turn's blocks cut into panels, a panel per run of one kind within one model request.

    Cut by the request as well as by the kind, so a panel never spans two round trips. That costs
    the merge a model answering twice in prose used to get - two responses of prose are now two
    panels - and buys the thing that merge made impossible: every boundary between requests is a gap
    between panels, so a rule can stand in it. A marker attached to a panel instead had to attribute
    a round trip to whichever fraction of itself came first.
    """
    for at, (asked, kind), run in runs(sourced, lambda held: (held[1], kind_of(held[0]))):
        yield Panel(turn=turn, at=at, kind=kind, blocks=tuple(block for block, _ in run), asked=asked)


def said_by(turn: int, said: records.Delivered, tree: str | None = None) -> Panel:
    """
    A turn's opening panel, which is whatever message it opened on and is always its first.

    Any kind of message can open one, and that is the inbox rather than a looseness: a `Steer` that
    arrived with nothing running opens the next turn, which is what `Send` means when a session is
    idle. Only a message that *must* open one can carry a boundary, so only one of those draws one.

    **A `Handoff` is drawn as its own kind**, because nobody typed it and a reader has to be able to
    tell that at a glance. It stays on the person's side of the palette all the same: the axis is who
    produced the text, and what a handoff holds was produced by this session rather than by the model
    about to be handed it.
    """
    return Panel(
        turn=turn,
        at=0,
        kind="handoff" if isinstance(said, records.Handoff) else "prompt",
        blocks=(Prose(text=said.said),),
        tree=tree,
        forget=records.forgets(said),
    )


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
    which is the only place its progress exists until it ends. At most one turn is in that state,
    because a pass answers the turn it opened before opening another; what a person types meanwhile
    is an inbox entry with no turn yet, and those are drawn last, in the order they arrived.
    """
    inbox = posted_in(recorded)
    opened = openings(recorded)
    panels: list[Panel] = []
    spent: dict[int, Spent] = {}
    asking: dict[int, tuple[Request, ...]] = {}
    turn = 0
    while turn < len(opened) and (answered := recorded.get(messages_key(turn))) is not None:
        held = owned_in(recorded, inbox, opened, turn, listening=False)
        said = parse_messages(answered)
        answering = responses_in(said)
        panels.append(said_by(turn, held[0].what, parse_tree(recorded.get(opening_tree_key(turn)))))
        blocks = parted(said, tooks_in(recorded, turn, answering))
        panels.extend(panelled(turn, alongside(blocks, ran_in(recorded, held, turn))))
        spent[turn] = spent_on(answering)
        asking[turn] = requests_in(recorded, turn, answering)
        turn += 1
    running = turn if turn < len(opened) else None
    if running is not None:
        held = owned_in(recorded, inbox, opened, turn, listening=True)
        # One walk of the turn's recorded responses, read twice: what it has said, and what it has
        # spent saying it. A turn in flight has a cost at all because the step that records each
        # response prices it on the way past, so this is the same reading the settled half above does
        # rather than a second, poorer one.
        answering = responded(recorded, turn)
        panels.append(said_by(turn, held[0].what, parse_tree(recorded.get(opening_tree_key(turn)))))
        blocks = blocks_from(recorded, held, turn, answering)
        panels.extend(panelled(turn, alongside(blocks, ran_in(recorded, held, turn))))
        if answering:
            spent[turn] = spent_on(answering)
            asking[turn] = requests_in(recorded, turn, answering)
        turn += 1
    # A person can type again while a reply is still coming, and what they type has no turn of its own
    # until a pass opens one. Drawn all the same, and in order, because a transcript showing only what
    # a pass had got to would be hiding a message somebody had already sent. A command run out here is
    # drawn beside the message it followed, for the same reason it is drawn where it was run inside a
    # turn: that is where it happened.
    for waiting in queued_in(recorded, inbox, opened, listening=running is not None):
        if isinstance(waiting.what, records.Command):
            at = max(turn - 1, 0)
            ran = Command(entry=waiting.key, text=waiting.what.said, result=result_in(recorded, waiting.key))
            panels.append(
                Panel(turn=at, at=sum(1 for panel in panels if panel.turn == at), kind="command", blocks=(ran,))
            )
            continue
        panels.append(said_by(turn, waiting.what))
        turn += 1
    return Transcript(
        panels=tuple(panels),
        awaiting=running is not None or turn > len(opened),
        turns=turn,
        spent=spent,
        answering=running,
        requests=asking,
        system_prompts=instructed_in(recorded, turn),
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
        return recorded_messages(answered.new_messages())

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
    the same files. Every other turn plants at whatever the session was started at, which is a base
    somebody named or the repository's own head, and which only happens once because the worktree is
    then already there.

    Both are handed over on every pass and only one can be true of a session at a time: `settled`
    clears the base and the branch on a fork, so a run that finds a recorded tree finds no base
    beside it and `Worktrees.plant`'s ranking never has to choose between two answers somebody gave.
    """
    if workspaces is None or chosen.repository is None:
        return None
    at = parse_tree(run.recorded.get(opening_tree_key(turn)))
    planted = await workspaces.plant(run.workflow, chosen.repository, tree=at, base=chosen.base, branch=chosen.branch)
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


def taken(entries: Sequence[Entry]) -> tuple[records.Delivered, ...]:
    """What a take from the inbox holds, parsed, in the order the store handed it back."""
    return tuple(parse_delivered(entry.value) for entry in entries)


async def opening_turn(run: Run, turn: int) -> records.Delivered:
    """
    The message this turn opens on, suspending on the inbox until there is one.

    `receive` rather than a key named in advance, which is the whole of what the inbox changes here:
    nothing allocates a turn number before the fact, so a message is delivered and the pass decides
    where it lands. That is also what deletes the race at the end of a turn - there is no slot for a
    pass and a person to contend for, only a queue and a cursor.

    It takes up to and including the *first message*, so a command run while the session was idle is
    passed over rather than opening a turn nobody asked for. What is behind that message is left for
    the drain, which is what lets one turn answer several things said in a row.

    Nothing to take is a limit of zero and that is how this waits: `receive` never returns empty, so
    an empty take raises and the pass comes back `Blocked` on this key until something is delivered.
    The limit is computed from what this pass can see, and a replay reads a fuller inbox; that is
    sound because entries only ever arrive *behind* what is there, so the first message after the
    cursor is the same one on every pass, and the recorded cursor governs the take regardless.
    """
    after = since_last(run.recorded, turn - 1) if turn else ""
    available = run.delivered(after or None, None)
    said = [at for at, what in enumerate(taken(available)) if not isinstance(what, records.Command)]
    took = await run.receive(opened_key(turn), after=after or None, limit=said[0] + 1 if said else 0)
    return taken(took)[-1]


type Handoffs = Callable[[str], Handing]
"""
Where a session's handoffs go, as a function from the session to the tool's own hook.

Injected into `conversing` rather than built inside it, symmetric with `Pricer`, `Draining` and
`Guiding` and for a plainer reason than a cycle: writing a handoff means *queueing* the session, and
a pass holds a checkpoint rather than the scheduler in front of it.
"""


def handing_through(durable: Durable, session: str) -> Handing:
    """
    Where a handoff written inside a pass goes, which is this session's own inbox.

    **Delivered and not appended**, which is the whole of what the queue is for here. An entry
    appended mid-pass is invisible to the pass that appended it - `receive` reads the snapshot loaded
    at the top, which is what makes a drain replayable - and an append queues nothing, so a handoff
    written that way leaves the session `Blocked` on a message already sitting in its inbox, with
    nothing that will ever wake it. Delivering makes the session ready again, and the pass that takes
    it reads a fresh snapshot with the document in it.

    The cost, stated: a handoff always crosses a pass boundary. That is the same bargain a steer
    already takes, one direction along, and it costs a claim rather than a round trip.

    The write is an *effect inside a step*, since `wrap_tool_execute` records what the tool returned:
    a resumed pass replays the return and does not write a second entry. That is what stops a crash
    between the delivery and the record leaving a conversation with two handoffs in it.

    `forget=True` is why this is worth a mechanism at all. A handoff that did not clear the context
    would be a summary of the conversation appended to the conversation, which is the one shape that
    costs tokens and buys nothing.
    """

    async def hand(document: str) -> None:
        await durable.deliver(session, records.Handoff(said=document, forget=True).recorded())

    return hand


type Tendings = Callable[[str], Awaitable[Tending]]
"""
What this console is doing for a session unasked, as a function from the session to its settings.

Injected rather than reached for, symmetric with `Handoffs`, `Pricer`, `Draining` and `Guiding`, and
for the plainest reason of the five: a pass holds a checkpoint, and these live on the session index,
which is a table it has no business knowing the shape of.

`None` is a console that was never given a way to read them, and such a console tends nothing. That
is the same reading `prices` and `handoffs` already take - a capability absent is the feature absent -
and it is what keeps the arithmetic inert in every test that does not ask for it, by construction
rather than by the accident of some other value being missing.
"""


def crossed(asked: records.Delivered, said: Sequence[ModelMessage], window: int | None, tended: Tending) -> bool:
    """
    Whether the turn that just ended is the one that should be followed by a handoff.

    **Read at the boundary that crosses the reserve, rather than at the start of the next turn**, and
    that is about the cache rather than about promptness: the conversation's prefix is warm right now,
    where by the time somebody comes back and types it may not be, and a handoff run is several
    requests over the whole window. The same argument that makes a handoff cheap in-session makes it
    cheap *here*.

    **A turn that opened on a handoff never triggers another**, which is the whole of what stops this
    recursing. The reserve is crossed for as long as the context stays large, so without it the ask
    turn - whose own context is the conversation it is summarising - would cross it again the instant
    it ended, and so would the one after that. Asking about the message the turn opened on is enough
    for both cases: the ask carries no boundary and the document carries one, and neither should be
    followed by a second ask. A model that answered the ask in prose rather than by calling the tool
    is therefore not asked again until a person says something, which is a retry per human action
    rather than one per turn - the rule a refusal already follows.

    The context is this turn's *last* request rather than a sum, by `Spent.context`'s own rule: every
    request carries the whole conversation, so what says how much of the window is gone is where the
    turn left it.
    """
    if isinstance(asked, records.Handoff):
        return False
    context = spent_on(responses_in(said)).context
    return standing(context, window, tended.reserve) == "due"


def draining_inbox(run: Run, turn: int) -> Draining:
    """
    What to put to the model now, recorded as how far down the inbox this turn has read.

    One function for both boundaries a steer can arrive at, where there were two: reading the queue
    and shutting it are the same act now, because nothing has to be shut. A message that arrives
    after the last drain is not lost to a closed turn, it is simply still in the queue, and the next
    turn opens on it.

    **It stops at a message that must open a turn**, which is the one thing this has to get right: a
    `Prompt` is one somebody asked to be answered on its own and a `Handoff` is one this console
    wrote to end a stretch of context, so folding either into the turn already running would be
    answering a question nobody put. Which records those are is `records.opens`'s to say rather than
    this function's, so a fifth kind of entry is answered in one place. Everything up to that point is
    taken, including the commands in between, which are passed over rather than told.

    The cursor comes out of the pass's own snapshot, which is current within the pass because a step
    writes back into it: two drains in one turn read where the one before them stopped without asking
    the store again.
    """

    async def drain(key: StepKey) -> Sequence[str]:
        since = since_last(run.recorded, turn)
        available = taken(run.delivered(since or None, None))
        wanted = len(tuple(takewhile(lambda what: not records.opens(what), available)))
        took = await run.pending(key, after=since or None, limit=wanted)
        return tuple(what.said for what in taken(took) if isinstance(what, records.Steer))

    return drain


@dataclass(frozen=True, slots=True)
class Progressed:
    """
    What a pass that ended mid-turn comes back with: the session is owed another one at once.

    The only way this body returns, and the reason it is a value rather than `None`. A pass that
    returns is `Completed` as far as the mechanism is concerned, and a conversation is never
    completed - it is only ever between turns - so the value is what stops that reading being the
    obvious one. Waiting on a person is still a suspension and comes back `Blocked`.

    Nothing is owed by the outside world, so there is nothing for a driver to wait on and nothing to
    schedule: the answer is to make the session ready again, which `answering` in `app.py` does.
    """


@dataclass(frozen=True, slots=True)
class Stalled:
    """
    What a pass that hit an unanswerable request comes back with: the session is owed nothing.

    The other way this body returns, and the opposite instruction to `Progressed`. A pass that
    refuses to go on must not be made ready again, because the next one would ask the identical
    question of the identical recorded history and be refused identically - which, left to the
    worker's own redelivery, is a session retried once per lease for ever with nothing saying so.

    The reason is already in the checkpoint by the time this is returned, under
    `refused_key(turn, at)`, so this carries none: what the page draws it reads for itself, and a
    value passed back through the worker would be a second copy of it that no restart survives.

    A person can still ask again, and that is the point rather than a gap: writing a message queues
    the session, so a refusal costs one attempt per human action instead of one per lease. What gets
    a conversation *past* a refused turn is `fork` at it, which drops the turn's own requests while
    keeping everything under them - see `before`.
    """


@dataclass(frozen=True, slots=True)
class Crossed:
    """
    What a pass whose session reached its reserve comes back with: it is owed a handoff, then a turn.

    The third instruction, and the one that is not about this pass at all. `Progressed` and `Stalled`
    both say what to do with a conversation that is where the pass left it; this says the conversation
    has run far enough into its model's window that the next thing it should be asked is to write down
    where it has got to.

    **A value rather than a delivery made from inside the loop**, which is the same split `Progressed`
    already makes and for a stronger reason. Asking for a handoff means putting a message in the
    session's inbox, and putting a message in an inbox *queues* the session: that is a fact about the
    queue in front of a pass rather than about answering one, so it belongs where `make_ready` already
    is. It is also what makes the decision testable as a value - a test drives one pass and reads what
    came back, with no store and no scheduler anywhere near the arithmetic.

    It carries nothing, because there is nothing here that the composition root does not already have.
    What the ask says is `recorded_ask`'s, so a person pressing the button in the rail and this cannot
    come to ask for two different things.
    """


type Ended = Progressed | Stalled | Crossed
"""
What one pass ends as, and the whole of what the worker owes each.

Arms rather than a boolean, so `readying` reads what happened rather than a flag saying what to do
about it, and so a fourth answer is a type error at the match rather than a session that quietly
stops being woken.

Named for the pass rather than `Outcome`, which in this module already means how one tool call went
and in `without-durability` already means what the mechanism made of a pass. Three things, three
words.
"""


def conversing(
    endpoints: Wires,
    instructions: str,
    workspaces: Workspaces | None = None,
    bwrap: str | None = None,
    prices: Prices | None = None,
    allowance: int | None = None,
    handoffs: Handoffs | None = None,
    tendings: Tendings | None = None,
) -> Callable[[Run], Awaitable[Ended]]:
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

    `allowance` is how many live model requests one pass may make before it hands the rest of the
    turn back, and `None` is unbounded, which is a pass answering a whole turn however many round
    trips that takes. It is one number rather than two code paths, which is what keeps the choice a
    thing to turn rather than a thing to maintain; see `Settings.allowance` for what it trades.

    `tendings` is where a session's own settings are read, once at the top of a pass. Once, rather
    than at each boundary, because a setting read twice inside one pass is a place rather than a
    value: a switch turned while a turn was being answered would have that turn answered under one
    answer and judged under another, which is precisely the escaping mutation a value is for. What it
    costs is that a change takes effect on the next pass, which is the next turn.
    """

    async def converse(run: Run) -> Ended:
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
        # The endpoint is asked for here and the agent is built per turn below, which is the same
        # check split in two. Without one there is no endpoint to answer on at all, and finding that
        # out before the first `awaiting` is what makes it a failure the console can explain rather
        # than one discovered mid-turn; the agent itself cannot be built this early any more, because
        # what it is told includes the repository's own guidance and the worktree holding it is
        # planted inside the loop.
        endpoints.for_endpoint(chosen.endpoint)
        # What each stretch of context in this pass is answered under, by the turn it began at.
        composed: dict[int, str] = {}
        # Built once per pass beside the agent and for the same reason: what it needs from the
        # session is the choice, and a choice cannot change. What it reads *through* is two holders,
        # so a rate that moves under a long-running pass still reaches the turn being priced.
        # Without one a turn records no cost, which is what a console with no reference configured
        # has always shown - a card with no numbers on it.
        pricer = None if prices is None else prices.pricer(chosen)
        # One per pass and shared by every turn in it, because what it bounds is how long this pass
        # runs. A pass that finds two prompts waiting answers two turns, and a fresh count per turn
        # would let it make one live request for each of them under a lease sized for one.
        spending = Allowance(limit=allowance)
        # Once, at the top, and held as a value for the rest of the pass. A setting re-read at each
        # turn boundary would be a place two writers share, so a switch flicked while a turn was in
        # flight would have that turn answered under one answer and judged under another. `None` is a
        # console that was given no way to read these at all, and such a console tends nothing.
        tended = None if tendings is None else await tendings(run.workflow)
        at = reached(run.recorded)
        while True:
            asked = await opening_turn(run, at.turn)
            # Read off the message this pass just parked on rather than by asking the store again,
            # which is the whole reason the boundary rides on the message itself: a pass carries its
            # history forward between turns, so a marker delivered beside the message while it was
            # waiting here would be invisible to it and seen by the pass that resumed.
            if records.forgets(asked):
                at = Reached(turn=at.turn, history=())
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

            # Built here rather than once per pass, because what a session is answered under includes
            # the repository's own `AGENTS.md` and the worktree holding it is planted directly above:
            # read any earlier, a session's first turn would be answered having been told nothing the
            # project says about itself. It costs one `Agent` per turn, which is tens of microseconds
            # against a turn that costs seconds, and the connection pool it reaches through belongs to
            # the endpoint and is not rebuilt.
            #
            # **A step, so it is composed once for the session's life and replayed after that.**
            # Instructions sit in front of the cached prefix, so composing them again on a later turn
            # would re-price every remaining request the moment the repository's own `AGENTS.md`
            # moved - and a session working on a repository's guidance moves it constantly. Reading
            # it again buys nothing against that, because the thing most likely to have edited the
            # file is the model, which knows what it wrote.
            #
            # **It holds exactly what the model is sent**, which is what lets the page draw the
            # system prompt from the moment a turn opens rather than only once one has landed.
            # `agent_for` speaks this string verbatim, so the note about this session's worktree and
            # network is composed in here beside the guidance instead of being appended out there:
            # appended, it would be a sentence the model carried that no record held, recomposed on
            # every turn in front of a cached prefix it is supposed to sit still behind.
            #
            # The repository's own guidance, and an index of what the rest of it carries. The index
            # is one line per file rather than their contents, which is what makes it affordable on
            # every request in a repository with fifty of them: that a directory *has* rules is what
            # a model needs before it reaches in, and what they are is a `read` away.
            async def composing() -> object:
                elsewhere = () if worktree is None else await guidance_under(worktree)
                return recorded_instructions(
                    instructing(
                        instructions,
                        None if worktree is None else repository_guidance(worktree.root),
                        None if worktree is None else indexing(worktree.root, elsewhere),
                        reaching(chosen.isolation, worktree, scratch, bwrap).note,
                    )
                )

            # Memoised for the pass as well as recorded, because `Run.step` refuses a key it has
            # already used: a pass answering two turns of one context would otherwise claim the same
            # name twice. The dictionary is keyed by where the context began, so a pass that crosses
            # a forget composes a second time, which is exactly when it should.
            began = history_began(run.recorded, at.turn)
            if began not in composed:
                composed[began] = await run.step(instructions_key(began), composing, parse_instructions)
            spoken = composed[began]
            agent = agent_for(
                endpoints,
                chosen,
                spoken,
                worktree=worktree,
                scratch=scratch,
                bwrap=bwrap,
                handing=None if handoffs is None else handoffs(run.workflow),
            )

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
            draining = draining_inbox(run, at.turn)
            # What the parts of the repository this turn reaches into say about themselves, handed
            # over on the request after it reaches. Closed over the worktree rather than given the
            # index above, so a directory whose guidance the model has only just written is covered
            # by the same walk as one that was there all along.
            guiding = None if worktree is None else partial(approaching, worktree.root)
            with stepping(run, turn_prefix(at.turn), worktree, pricer, draining, spending, guiding):
                try:
                    answered = await agent.run(asked.said, message_history=list(at.history))
                except AllowanceSpent:
                    # Caught out here rather than anywhere inside the agent, because what it ends is
                    # the pass and not the request: every step this turn has taken is recorded, so
                    # the pass that follows replays them and reaches the request this one refused.
                    return Progressed()
                except RequestRefused:
                    # The same shape one answer along, and the answer is the opposite one. The
                    # provider will not take this request on any pass, so asking for another is
                    # asking to be refused again; the reason is already recorded, so the page can
                    # say what happened without this carrying anything back.
                    return Stalled()
            said = await run.step(messages_key(at.turn), recording(answered), parse_messages)
            at = Reached(turn=at.turn + 1, history=(*at.history, *said))
            # After the turn is recorded rather than before, so the context this is read against is
            # the one the turn actually left behind, and so a crash between the two loses nothing: the
            # pass that resumes replays the step, reaches here, and asks the same question of the same
            # numbers. The window is asked for now rather than at the top of the pass because the
            # reference under it is reloadable configuration, exactly as the rates are.
            if tended is not None and tended.hands_off:
                facts = None if prices is None else prices.facts(chosen)
                if crossed(asked, said, facts.context if facts is not None else None, tended):
                    return Crossed()

    return converse
