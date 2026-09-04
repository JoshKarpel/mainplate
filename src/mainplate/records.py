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
    "prompt",
    "steer",
    "closed",
    "command",
    "result",
    "tree",
    "heard",
    "late",
    "model",
    "tool",
    "messages",
]
"""
What a record says it is, and what a turn's keys are named by.

One vocabulary for both, so `Stepping.key("tree")` and `tree_key(n, i)` cannot come to build
different strings from opposite ends, which is a hazard those two carried with nothing enforcing it.

Two members are not key segments and belong here all the same. `choice` names a key of its own rather
than a segment of a turn's, and `closed` names no key at all: it is what a pass writes into a *steer*
slot to say it has stopped listening, so the slot holds one of two kinds and the tag is what tells
them apart. See `Closed`.

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
    What the person said to open a turn.

    Written from *outside* a pass, because a message is what queues a session and somebody typing
    cannot be a step of the run that answers them. The one record here that crossed a trust boundary:
    everything else was produced by code in this process, where this arrived from an HTTP handler on
    behalf of whoever posted the form.
    """

    kind: Literal["prompt"] = "prompt"
    said: str

    forget: bool = False
    """
    Whether this turn opens on a clean history, telling the model nothing that came before it.

    **A field on the turn's opening record rather than a key beside it**, and that is what makes the
    boundary impossible to get wrong. Two keys would need an order, and only one order is sound: the
    worker parks on `prompt_key(n)`, so a marker written *after* the prompt could be missed by a pass
    that had already started the turn, and a resumed pass reading it would then build a different
    history and pair it with an answer the first pass gave to a different question. One record is one
    write, so a turn whose history policy has not landed yet cannot exist.

    A boolean and not the turn its history starts at, which would be this turn's number said twice
    and able to disagree with the key it sits under.

    Nothing is deleted and nothing is hidden: the transcript still draws every turn above the
    boundary, because the checkpoint is still the conversation. What changes is only what `reached`
    hands the model, which is the same split `command` already makes between being *in* the
    checkpoint and being *in* the message history.
    """


class Steer(Record):
    """
    Something the person said *into* a turn that was already being answered.

    Shares its slot with `Closed`, which is the only place in this scheme where one key holds two
    kinds of record. That is not untidiness: the slot is contended between whoever is typing and the
    pass shutting the door, and the tag is how a reader tells which of them won it.
    """

    kind: Literal["steer"] = "steer"
    said: str


class Closed(Record):
    """
    What a pass writes into the next free steer slot when it is about to stop listening.

    **This is the whole of how a message cannot be lost at the end of a turn**, and it works because
    `supply` is a compare-and-set: it keeps the value a key was first given and hands the loser the
    winner's. So the pass and whoever is typing contend for one key and exactly one of them wins.

    - The pass wins: nothing can be written at that slot afterwards, so a message arriving from now
      on is told the turn is closed and becomes a turn of its own instead.
    - The person wins: the pass is handed their text rather than its own marker, and redirects the
      run into one more request to carry it.

    Without it the two decisions are separate reads and there is a window at the end of every turn
    where the store still says a turn is being answered and the pass has already stopped reading. A
    message sent into that window goes to a key nothing will ever look at again.

    A record with no fields but its own tag, which is what a marker is once every value is a record.
    Nothing anybody types can produce one, because what a person writes is a `Steer` and their text
    is a field *inside* it. It was `None` before there were records, which said the same thing by
    shape and cost one ambiguity this does not have: `recorded.get(key)` answers `None` for a slot
    nobody has written as well as for a marker, where these are told apart.
    """

    kind: Literal["closed"] = "closed"


class Command(Record):
    """
    Something the person ran themselves, beside the conversation rather than inside it.

    Recorded and never told: no model sees one of these. It is in the checkpoint because that is the
    only place this console keeps anything, so a command renders, survives a reload and comes across
    on a fork; and it is out of the message history because telling a model what you ran is a message
    somebody writes.
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


class Heard(Record):
    """
    Which steers were appended to one model request of a turn.

    A record even when it names none, because a request that was told nothing is still a request that
    ran, and a reader walking these consecutively would otherwise stop at the first quiet one.
    """

    kind: Literal["heard"] = "heard"
    said: tuple[str, ...] = ()


class Late(Record):
    """
    Which steers were found at the boundary where a run would otherwise have ended.

    Its own kind rather than another `Heard`, because it is written where there is no request: the
    two share a counter only if you let them, and sharing it would drift `heard:{i}` off the
    `tree:{i}` and `model:{i}` it is supposed to name one request alongside.
    """

    kind: Literal["late"] = "late"
    said: tuple[str, ...] = ()


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


type Said = Annotated[Steer | Closed, Field(discriminator="kind")]
"""
What a steer slot holds: either what somebody said into a running turn, or the marker saying nobody
may.

The one place in this scheme where a key holds two kinds, and stating it as a type is what the tag
buys. Told apart by shape before there were records - a steer was text and the marker was `None` -
which worked and left the slot's contract recoverable only by reading the walk that recovered it.
"""

SAID: TypeAdapter[Said] = TypeAdapter(Said)


type Step = Annotated[
    Prompt | Steer | Closed | Command | Result | Tree | Heard | Late | Response | Messages | Returned,
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
