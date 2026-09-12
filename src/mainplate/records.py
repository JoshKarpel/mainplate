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
# **Two checkpoint values are deliberately not records here, and both are cursors.**
# `turn:{n}:opened` and `turn:{n}:heard:{i}` hold the key of the last inbox entry
# a pass took, written by `Run.receive` and `Run.pending` rather than by anything in this console. The
# shape argument does not reach them: their value is the store's, under the store's own semantics, so
# there is no second field this console could ever want to put beside one. Wrapping them would mean
# not using `receive`, and `receive` is the only thing that can suspend a pass on an inbox.

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from typing import Literal
from typing import TypeIs

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictInt
from pydantic import TypeAdapter

type StepKind = Literal[
    "choice",
    "instructions",
    "prompt",
    "note",
    "steer",
    "command",
    "result",
    "opened",
    "tree",
    "heard",
    "model",
    "refused",
    "failed",
    "tool",
    "messages",
    "plugins",
    "plugin",
    "declared",
    "named",
    "confirmed",
    "injected",
    "end",
    "environment",
]
"""
What a record says it is, and what a turn's keys are named by.

One vocabulary for both, so `Stepping.key("tree")` and `tree_key(n, i)` cannot come to build
different strings from opposite ends, which is a hazard those two carried with nothing enforcing it.

The two halves no longer line up member for member, and that is the inbox rather than untidiness.
`prompt`, `steer` and `command` name *records* and no key at all: what a person says and what they
run are entries in the session's inbox, filed under a key the store mints. `opened` and `heard` are
the other way round: they name keys whose value is a **cursor**, which is the store's own value
rather than one of ours, so they have no record here. See the note on cursors below.

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


class Note(Record):
    """
    A message a plugin asked for: the one thing in a conversation that nobody in it typed.

    **An arm of its own rather than a flag on `Prompt`, because nobody typed it.** A reader has to
    be able to tell at a glance that a message in their own conversation is neither theirs nor the
    model's, and the tag is what the panel's kind is read off - the same argument that made `Steer`
    its own record rather than a `Prompt` with a boolean, one axis along. It behaves exactly as a
    `Prompt` does otherwise: it must open a turn of its own, a draining pass stops at it, and it may
    carry a boundary.

    **One arm for every plugin, the bundled ones included.** Letting a plugin name which record arm
    to write would hand out the one vocabulary this console has to own, and special-casing a bundled
    plugin so it kept an arm of its own would break the uniformity the whole design is for: the
    built-in would be running on a path no third-party plugin could reach.

    It replaced a `Handoff` arm, and the cost is stated: a tag no arm answers to is a hard failure,
    so a checkpoint holding one no longer loads and sessions recorded before this do not survive it.

    **Recorded rather than composed at render time**, which is the fork's bargain and not the
    catalogue's: what a note says is settled the moment it is delivered, and nothing will ever
    rewrite it. What the model was told and what the page shows are then one string.
    """

    kind: Literal["note"] = "note"
    said: str

    plugin: str
    """
    Which plugin asked for this, by qualified name, so a turn can be told who opened it.

    Required rather than defaulted, because there is no such thing as a note nobody asked for: the
    console composes none of these itself any more. It is read twice - by the page, which draws the
    plugin's own name on the panel's role where the plugin named none, and by `after_turn`, where a
    plugin has to be able to tell that a turn opened on its own delivery so it does not fire again.
    """

    forget: bool = False
    """
    Whether this message starts the model's history again.

    Read by the same `forgets` predicate a `Prompt` is, since where a boundary may sit is a fact
    about turns rather than about who wrote the message that carries one.
    """

    label: str | None = None
    """
    The word on the panel's role, or nothing at all to take the plugin's own name.

    A pre-commit failure and a handoff document are different things to meet halfway down a
    transcript, so a plugin that cannot say anything about how its note looks would have every note
    in the console drawn identically.
    """

    title: str | None = None
    """The hover text saying what a reader is looking at, or nothing, as most kinds have none."""

    tone: str | None = None
    """
    Which of the console's inks this panel takes, named rather than passed as a colour.

    Held as a bare string rather than as the closed set, and read through `protocol.toned`: an
    unknown tone draws the plain one rather than refusing, which is the opposite of what an unknown
    `kind` does and deliberately so. A kind nothing answers to is a checkpoint this console cannot
    read; a tone nothing answers to is a panel in the wrong ink.
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


class Refused(Record):
    """
    A model request the provider will not accept, whatever this console does about it.

    **Recorded because the alternative is a session that stops with nothing saying so.** A pass that
    raises is left unanswered by the worker, redelivered when its lease elapses, and tried again for
    as long as it keeps failing - which for a deterministic refusal is for ever, once per lease, at
    no cost anybody can see. So the refusal is written down, the pass reports `Stalled`, and the page
    says what happened.

    **A settled value rather than a state, which is what lets it live in a write-once store.** What
    is refused is a request, and a request's input is the recorded history and the recorded message,
    neither of which will ever change: a turn refused at request `i` is refused at request `i` on
    every later pass. So the key it goes under is the request's own position, and the record can
    never be contradicted by a retry.

    `status` is the provider's own, `None` where the failure carried none. It is not flattened into a
    reason, for the same reason `Result.status` is not flattened into a boolean: the number is what
    somebody looks up.
    """

    kind: Literal["refused"] = "refused"

    why: str
    status: int | None = None


class Failed(Record):
    """
    Why the last pass at this session raised, where one did, and how far it had got.

    **`Refused`'s opposite, which is why it is a second record and not a second reading of that
    one.** A refusal is settled: the provider will not take the request and no pass ever will, so it
    is recorded once, the pass reports `Stalled`, and nothing wakes the session again. This is a pass
    that fell over, on a plugin that exited non-zero, a tool that raised, a store that was briefly
    unreachable, or a bug - and every one of those is something that can be *fixed*, after which the
    redelivery the worker was already going to make resumes the session from where it stopped. So the
    reason is written down and the failure is still re-raised, which is what keeps the retry.

    **`at` is how many records the session held when the pass fell over**, counting every key but
    these, and it is what makes one of these sound in a write-once store. A pass that fails at the
    same point writes the same key and the store keeps what is there, so a session failing for ever
    accumulates one record rather than one per lease. A pass that gets further and then fails has a
    different count and so writes a new one. And it is what the page asks to tell a current failure
    from a spent one: this is why the session is stopped exactly while `at` is still what the session
    holds, because anything recorded since is a pass that got past it.

    Named for what happened rather than for what it is about, because there is nothing it is about:
    the failure is of the pass, and where the pass was is `at`.
    """

    kind: Literal["failed"] = "failed"

    why: str
    at: int = 0


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


class Named(Record):
    """
    One plugin as a declaring file named it: where it came from, what it is called, and what to run.

    **Everything here is read out of a file, and nothing here was run.** That is the whole difference
    from `Enrolled` below, which is this plus what the plugin said when it was asked. A session
    records these before its settings step and the other after it, because running a plugin is
    executing a program and the step is where somebody says which ones to execute.
    """

    kind: Literal["named"] = "named"
    name: str
    tier: str
    path: str


class Declared(Record):
    """
    Which plugins a session *could* run, settled on its first pass and drawn on its settings step.

    Two of these per session, under two keys, for the reason there are two registrations: the tiers
    are read from different places and the repository's half can fail on its own, so a repository
    that will not be read leaves the operator's declaration recorded rather than taking it down too.
    A fork carries neither and reads both again.

    Both are written even where there is nothing declared, which is what makes an empty declaration
    mean *this session has looked* rather than *nobody has looked yet* - the same claim the
    registration's own emptiness makes, one moment earlier.
    """

    kind: Literal["declared"] = "declared"
    plugins: tuple[Named, ...] = ()

    setup: bool = False
    """
    Whether the repository carries a `.mainplate/setup` script, read on the same pass as its plugins.

    On the repository's declaration only, and beside the plugins rather than as a plugin: it is a
    program the console runs itself, in the session's own namespace with a network, so it is drawn on
    the settings step with a switch of its own and is registered nowhere. Recorded here so that what
    the step offers and what the pass may run are one reading of the tree.
    """


class Enrolled(Record):
    """
    One plugin as a session recorded it: where it came from, what to run, and everything it declared.

    **`described` is opaque here for the reason `Response.response` is.** What validates it is
    `plugins.protocol.Described`, and this module imports nothing; `parse_described` is the other
    half. Held whole rather than field by field, so a contribution added to that vocabulary is a
    field there and no migration here.

    `path` is recorded beside the name because a later pass has to run the same script without
    reading any declaring file again: a repository's declaration is read once, on the session's first
    pass, and every pass after replays this.
    """

    kind: Literal["plugin"] = "plugin"
    name: str
    tier: str
    path: str
    described: object


class Registered(Record):
    """
    Which plugins a session ran and what they contributed, settled when its settings step was answered.

    **Recorded rather than re-read**, which is `turn:0:tree:0`'s own shape: a fact about one session
    that could only be learned by doing the work, written once and replayed after. Tool definitions
    sit above the system prompt in the cached prefix, so a set that changed under a conversation
    would invalidate the whole prefix beneath it and leave the turns already recorded having been
    answered by a harness that session no longer has.

    Two of these per session, under two keys, and that is the tiers rather than untidiness: they run
    behind different isolation, so the set is split where it is already being split to be launched.
    Both halves answer before either is written, so a tier that failed leaves neither recorded and the
    whole step is retried; what the two keys buy is a resumed pass re-launching only the half it never
    got to. A fork carries neither and sets both up again.
    """

    kind: Literal["plugins"] = "plugins"
    plugins: tuple[Enrolled, ...] = ()


class Confirmed(Record):
    """
    That somebody answered this session's settings step, which is what lets a pass run a plugin.

    **The trust boundary written down.** Everything before it is files being read; this is the record
    that a person looked at what was declared and pressed the button, and the pass that follows sets
    up exactly what the switches left on.

    **It holds nothing, and that is the decision rather than an omission.** Which plugins are on is
    the `enabled` column's answer, and a copy here could never be corrected: what somebody does about
    a plugin that will not set up is turn it off and press again, and a write-once list would have
    the second press run exactly what the first one ran.
    """

    kind: Literal["confirmed"] = "confirmed"


class Injected(Record):
    """
    What a plugin asked to be appended to one model request, recorded so a replay says it again.

    A step, because a plugin cannot be trusted to be pure: `guiding` was safe unrecorded by being a
    pure function of the history it was handed, and a script is not that. What is recorded is what
    was injected, so a resumed pass replays the injection rather than recomputing it.

    One record per request holding every plugin's contribution in order, rather than one per plugin:
    what the request carried is one list, and a second key per plugin would be a numbering that has
    to stay in step with which plugins answered.
    """

    kind: Literal["injected"] = "injected"
    said: tuple[str, ...] = ()


class End(Record):
    """
    One end of a turn: what the session's plugins said when it tried to end, recorded so a replay
    says it again.

    A turn that was sent back has several ends and only the last is real, so there is one of these
    per attempt, `turn:{n}:end:{j}`, written whether or not anything was said: an empty `said` is the
    record of the plugins letting the turn go, and a resumed pass reads that rather than asking
    scripts that may answer differently the second time. It is `Injected`'s shape for `Injected`'s
    reason - what was put to the model is one list in enrolment order - under its own kind, because
    the word a key is built from is the word the record under it carries.

    `at` is how many model responses the turn had made when it was asked, which is where the page
    draws what was said: above the response it shaped, exactly as a steer is, and at the end while
    that response is still out.
    """

    kind: Literal["end"] = "end"
    said: tuple[str, ...] = ()
    at: int = 0


class Environment(Record):
    """
    What a repository's `.mainplate/setup` asked to have set for the session's own commands.

    Recorded on the pass that ran the script, beside the registrations, and fixed for the session's
    life like them: what a session's commands run under is part of its terms, and a fork sets up
    again and records its own. Empty where the script was never run, whether because the repository
    carries none, the switch was off, or the session has no worktree, which are three ways of saying
    the same thing to a command.
    """

    kind: Literal["environment"] = "environment"
    values: dict[str, str] = {}


type Delivered = Annotated[Prompt | Note | Steer | Command, Field(discriminator="kind")]
"""
What one inbox entry holds: a message that must open a turn, one a plugin asked for, one that
may join the running one, or something the person ran.

The one place a *key* says nothing about what is under it, which is what the store minting the key
buys and what makes the tag load-bearing rather than a second copy: three kinds share one key space,
so nothing but the tag tells a reader whether an entry is to be told to a model, and a pass draining
the queue answers a different question for each.
"""

DELIVERED: TypeAdapter[Delivered] = TypeAdapter(Delivered)


def opens(what: Delivered) -> TypeIs[Prompt | Note]:
    """
    Whether a pass draining its inbox must stop at this entry rather than carrying past it.

    A `TypeIs` rather than a `bool`, so the two arms it names are written once in this module and
    every caller that goes on to read `forget` is narrowed by asking the question rather than by
    repeating the union.

    The question every reader of the queue asks, written once here rather than as an `isinstance`
    chain repeated at each of them. A `Steer` may be folded into the turn already being answered and
    a `Command` reaches no model at all, so both are carried past; a `Prompt` and a `Note` are
    messages that must be answered on their own, whoever wrote them.
    """
    return isinstance(what, Prompt | Note)


def forgets(what: Delivered) -> bool:
    """
    Whether the turn this message opens starts the model's history again.

    Only a message a turn opens on can carry a boundary, because a boundary between turns is the only
    place one can be, so this is `opens` and the field together rather than the field alone.
    """
    return opens(what) and what.forget


type Step = Annotated[
    Prompt
    | Note
    | Steer
    | Command
    | Result
    | Tree
    | Response
    | Refused
    | Messages
    | Returned
    | Instructions
    | Declared
    | Registered
    | Confirmed
    | Injected
    | End,
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
