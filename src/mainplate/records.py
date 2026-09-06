# What a checkpoint slot holds, as objects this console owns rather than as bare values.
#
# Every value written to the store goes through one of these. That is a rule about *shape* rather
# than about validation: a bare string, a bare number and a list have nowhere to put a second field,
# so the day one of them needs one is a migration, where a record with a field added is an optional
# somebody's parser defaults. `turn:{n}:prompt` held a bare string until a turn needed to say what
# history it opens on, and paying for that once is the argument for paying for it nowhere else.
#
# It applies to values this console does *not* own as much as to its own: a `ModelResponse` and a
# tool's return are wrapped rather than stored raw. The envelope does not protect against Pydantic AI
# renaming a field inside a response - nothing here could - but it is where a `version` would go the
# day one is needed, and adding it then costs an optional field rather than a shape change.
#
# **Every record carries its own `kind`**, which is a second copy of what its key already says, and
# the copy is the point: parsed by key alone a record written under the wrong one is silently
# reinterpreted as whatever that key expects, where a tag makes it fail. `StepKind` is the one
# vocabulary the key builders and the discriminators share, so the string is written once rather than
# at both ends of a name nothing enforces the agreement of.
#
# **Unknown fields are ignored, and that is for records only.** A newer build's record read by an
# older one after a rollback must not fail on a field the older one never heard of, and there is no
# read-modify-write here that could silently drop it: the store keeps the value a key was first
# given, and `before` copies raw values without parsing them, so a fork taken under the older build
# carries the newer record across intact. The HTTP boundary is the opposite case and stays that way -
# an unrecognised disposition is a refusal, because guessing puts a message in a conversation nobody
# addressed it to.
#
# An unknown *kind* is not covered by that and cannot be: a tag no arm answers to is a hard failure.
# So readers parse by key, where the caller already knows what it asked for, and `Step` is for the
# places that take a bag of records rather than one - a dump, an export, a migration.
#
# **Three checkpoint values are deliberately not records here, and all three are cursors.**
# `turn:{n}:opened`, `turn:{n}:heard:{i}` and `turn:{n}:late:{k}` hold the key of the last inbox entry
# a pass took, written by `Run.receive` and `Run.pending` rather than by anything in this console. The
# shape argument does not reach them: their value is the store's, under the store's own semantics, so
# there is no second field this console could ever want to put beside one. Wrapping them would mean
# not using `receive`, and `receive` is the only thing that can suspend a pass on an inbox.

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictInt
from pydantic import TypeAdapter

type StepKind = Literal[
    "choice",
    "instructions",
    "prompt",
    "steer",
    "command",
    "result",
    "opened",
    "tree",
    "heard",
    "model",
    "tool",
    "messages",
]
"""
What a record says it is, and what a turn's keys are named by.

One vocabulary for both, so `Stepping.key("tree")` and `tree_key(n, i)` cannot come to build
different strings from opposite ends, which is a hazard those two carried with nothing enforcing it.

The two halves no longer line up member for member, and that is the inbox rather than untidiness.
`prompt`, `steer` and `command` name *records* and no key at all: what a person says and what they
run are entries in the session's inbox, filed under a key the store mints. `opened`, `heard` and
`late` are the other way round: they name keys whose value is a **cursor**, which is the store's own
value rather than one of ours, so they have no record here. See the note on cursors below.

Not to be confused with the panel `Kind` in `conversation.py`, which is a rendering vocabulary. Both
are called `kind` because it is a generic word and each is unambiguous where it is used; they overlap
on `command` and `tool` meaning different things, they never mix, and mypy refuses the crossing since
they are distinct unions.
"""


class Record(BaseModel):
    """
    What every checkpoint value is, and the two settings that make it one.

    `frozen` because a record is a value: it is written once, never rewritten, and read by several
    places that must not be able to edit each other's copy.

    `extra="ignore"` is the forward compatibility, and it is Pydantic's default said out loud rather
    than a behaviour change: see the note at the top of this module for why it is safe here and why
    it stops at the HTTP boundary.

    `ser_json_timedelta="float"` keeps a duration in the store as seconds rather than as an ISO 8601
    string, so a record carries a real `timedelta` in the process and a number the codec takes on
    disk. The unit is stated once here instead of at every field that holds one.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", ser_json_timedelta="float")

    def recorded(self) -> dict[str, object]:
        """
        The record as the JSON-native value the store's codec will take.

        `Run.step` records what the codec takes, which is the standard library's `json`, so a value
        has to be lowered on the way in and parsed on the way out. Named here rather than written at
        each call site so that every writer lowers a record the same way.
        """
        return self.model_dump(mode="json")


class Prompt(Record):
    """
    A message that must open a turn of its own, whatever is running when it lands.

    An inbox entry, written from *outside* a pass, because a message is what queues a session and
    somebody typing cannot be a step of the run that answers them. The one record here that crossed a
    trust boundary: everything else was produced by code in this process, where this arrived from an
    HTTP handler on behalf of whoever posted the form.

    **The difference from a `Steer` is what the pass may do with it, and nothing else.** A pass
    draining what arrived while it was working stops at one of these, so it waits for the next turn
    rather than folding it into this one. That is what `Disposition.NEXT` asks for, and what
    `Disposition.FORGET` needs in order to mean anything.
    """

    kind: Literal["prompt"] = "prompt"
    said: str

    forget: bool = False
    """
    Whether this turn opens on a clean history, telling the model nothing that came before it.

    **A field on the message rather than a record beside it**, and that is what makes the boundary
    impossible to get wrong. Two entries would need an order and could be separated by a turn opening
    between them; one record is one append, so a message whose history policy has not landed cannot
    exist.

    A boolean and not the turn its history starts at, which would be a number recoverable from where
    the entry sits and able to disagree with it.

    Nothing is deleted and nothing is hidden: the transcript still draws every turn above the
    boundary, because the checkpoint is still the conversation. What changes is only what `reached`
    hands the model, which is the same split `command` already makes between being *in* the
    checkpoint and being *in* the message history.
    """


class Instructions(Record):
    """
    What one stretch of context is answered under, composed before its first answer and never again.

    **Recorded rather than re-derived**, which is the argument a turn's cost already makes one field
    along: what a turn was answered under is settled the moment it is answered, and composing it
    again later would give a different answer the instant anything under it moved. Instructions sit
    in front of the cached prefix, so that later answer would re-price every remaining request of the
    stretch - and a session working on a repository's own `AGENTS.md` moves it constantly.

    **A stretch of context rather than a whole session, because a forget ends one.** That is not a
    weaker promise: what recomposing costs is the requests that would have read the prefix from
    cache, and a forget has just thrown the whole prefix away, so composing again exactly there is
    free. It is also the one moment a reader might reasonably expect edited guidance to be picked up.

    **It holds exactly what the model is sent**, notes about this session's worktree and network
    included, because `agent_for` speaks it verbatim. That is what the page draws: a system prompt
    panel reads this rather than digging one out of a turn's recorded messages, so it is there from
    the moment a turn opens instead of only once one has landed.

    Written by a pass rather than by `Service.start`, because composing it reads a worktree the
    worker is the one to plant. That is the whole of the gap a reader sees: a session's very first
    turn draws the panel without a prompt in it until the clone and the worktree are there to be
    read, which on a fresh repository is the one slow moment in a session's life.
    """

    kind: Literal["instructions"] = "instructions"
    said: str


class Steer(Record):
    """
    A message that may join a turn already being answered, and opens one where none is.

    What `Send` delivers, which is to say the ordinary case. **Whether it steers is not decided
    here**: it is decided by where the entry lands, which is the whole of what the inbox settles. A
    pass working when it arrives drains it into the request it is about to make; a pass between turns
    takes it as the message that opens the next one. Nothing has to be claimed, refused, or re-tried,
    because there is no slot for two writers to contend for.
    """

    kind: Literal["steer"] = "steer"
    said: str


class Command(Record):
    """
    Something the person ran themselves, beside the conversation rather than inside it.

    An inbox entry like a message, and read out of the inbox by nobody: a pass passes over one on its
    way down the queue and no model ever sees it. It is in the checkpoint because that is the only
    place this console keeps anything, so a command renders, survives a reload and comes across on a
    fork; and it is out of the message history because telling a model what you ran is a message
    somebody writes.

    Being an entry is also what puts it in the right place on the page. Where it sits among a turn's
    model records is where it was run, because the store files everything in the order it arrived, so
    nothing has to record which request was in flight at the time.
    """

    kind: Literal["command"] = "command"
    said: str


class Result(Record):
    """
    What a command exited with, said, and took.

    A record of its own rather than fields on `Command`, because it lands later: a `pytest` is
    minutes and somebody is watching, so the command's panel is drawn the instant it is posted and
    this filling in is what ends it. Absent is "still running", which needs no flag beside it.

    `status` is the process's own exit code and stays a number, because `git diff --quiet` exits 1 to
    mean there *are* changes: flattened to a boolean this console would report a command doing its
    job as one that broke.

    `took` is in this record rather than in a key of its own, and that is now true of a tool call's
    duration as well; see `Returned`.
    """

    kind: Literal["result"] = "result"

    status: StrictInt
    """
    Strict, because Pydantic reads `True` as `1` where it is not, and a boolean in this slot is
    exactly the confusion the number exists to avoid: a console that turned somebody's `true` into an
    exit status of 1 would report `git diff --quiet` finding changes.
    """

    output: str
    took: timedelta | None = None


class Tree(Record):
    """
    What the worktree held before one model request of a turn.

    `tree` is `None` for a turn taken with no workspace configured, which is a different thing from a
    request nobody has reached: the record exists either way, so the absence is stated rather than
    inferred from a missing key.
    """

    kind: Literal["tree"] = "tree"
    tree: str | None = None


class Response(Record):
    """
    One model response, as the provider answered it.

    `response` is Pydantic AI's own shape and is held as an opaque value here, because the type that
    validates it is `ModelResponse` and this module is the one thing in the scheme that must import
    nothing. `parse_model_response` is the other half.

    Named `Response` and not `Model`, which means an LLM everywhere else in this console.
    """

    kind: Literal["model"] = "model"
    response: object


class Returned(Record):
    """
    What one tool call came back with, and how long it took.

    `returned` is deliberately unnarrowed: a toolset is a set of unrelated functions with unrelated
    return types, so there is no one type to validate against the way there is for a response. What a
    caller receives is the JSON round trip of what the tool returned, on the pass that ran it exactly
    as on the pass that replayed it, which is what makes the two passes agree.

    **`took` is in this record**, where it used to be a `turn:{n}:took:{id}` of its own. That key
    existed because a bare tool return is somebody else's value, so a duration beside it would have
    been indistinguishable from a tool that returned a field of that name - an objection the envelope
    removes, since the foreign value is nested under a name this console owns. Collapsing them also
    retires the window where a return was recorded and its duration was not, because there is now one
    write rather than two.

    Named `Returned` and not `Tool`, which means a member of a toolset.
    """

    kind: Literal["tool"] = "tool"
    returned: object
    took: timedelta | None = None


class Messages(Record):
    """
    What one turn's agent run produced, which is the turn's own answer.

    Pydantic AI's shapes again, held opaquely for the reason `Response` holds one: what validates
    these is `ModelMessagesTypeAdapter`, and this module imports nothing.
    """

    kind: Literal["messages"] = "messages"
    messages: list[object]


type Delivered = Annotated[Prompt | Steer | Command, Field(discriminator="kind")]
"""
What one inbox entry holds: a message that must open a turn, one that may join the running one, or
something the person ran.

The one place a *key* says nothing about what is under it, which is what the store minting the key
buys and what makes the tag load-bearing rather than a second copy: three kinds share one key space,
so nothing but the tag tells a reader whether an entry is to be told to a model, and a pass draining
the queue answers a different question for each.
"""

DELIVERED: TypeAdapter[Delivered] = TypeAdapter(Delivered)


type Step = Annotated[
    Prompt | Steer | Command | Result | Tree | Response | Messages | Returned | Instructions,
    Field(discriminator="kind"),
]
"""
Any one record, told apart by its own tag.

**For the places that take a bag of records rather than one**: a dump, an export, a migration.
Everywhere else parses by key, because the caller already knows what it asked for and a type demanded
is stronger than a type discovered - and because an unknown tag is a hard failure here, where an
unknown field is not.

`choice` is not an arm, and that is a decision rather than an oversight. It is already a record this
console owns and has grown fields twice without a migration, so the shape argument that put an
envelope round everything else does not apply to it; and its parser encodes things a schema cannot
say, defaulting an absent isolation from whether a repository was picked and re-parsing a base and a
branch that will become `git` arguments. It carries the `kind` tag all the same, so a bag holding one
still says what it is.
"""

STEP: TypeAdapter[Step] = TypeAdapter(Step)
