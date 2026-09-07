# What a plugin is told and what a plugin says, as values this console owns rather than as bare JSON.
#
# Two directions and two stances, and keeping them apart is the whole of this module.
#
# What goes *out* is composed here and lowered to JSON, so an event payload is a value a test can
# build and read with no subprocess anywhere near it. Every field is one this console already has:
# a plugin decides on what it is handed and nothing else, which is the closed vocabulary working and
# the reason most new capabilities are a field added here rather than something a plugin reaches for.
#
# What comes *back* crossed a trust boundary and is parsed here, strictly. `extra="forbid"` is the
# one place this console refuses a field it has no answer for, and it is deliberate: a plugin that
# declares `tool` where the word is `tools` has made a mistake, and a setup that fails naming the
# plugin is how somebody finds out. That is the opposite of `records.Record`, which ignores what it
# does not know, and the difference is who wrote the value - a record was written by an older build
# of this console, where a plugin's answer was written by somebody who can be told.
#
# **A tone is the exception and is deliberately unvalidated.** A tone nothing answers to draws the
# plain one, because a panel in the wrong ink is a session that renders and a refusal is a session
# that does not. See `toned`.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic import model_validator

type Event = Literal["setup", "tool", "before_request", "after_turn", "compose", "action"]
"""
Every moment a plugin can be asked about, which is a short list this console owns.

Not a seam per point in the agent's loop, and not a set a plugin can add to: what decides whether a
moment is here at all is whether the console can record the answer as a step and apply it through a
mechanism it already has. See `docs/design/plugins.md`.

**`setup` is one event and not two**, which is worth saying because the pair is what it looks like it
should be: a plugin says what it contributes *and* gets itself ready in the same call. It was written
as two, `describe` beside a setup of its own, and collapsed again: a plugin's first run is already
the moment it installs, since a `uv run --script` shebang resolves its dependencies there whether or
not anybody named an event for it. The second event bought a scheduling distinction the console can
make on its own, at the price of a moment every plugin author has to learn about.

**And it is named after the stage rather than after the answer**, because it is one stage of starting
a session and a plugin is not the only thing in it: the repository is set up in the same stage, and a
name meaning *tell me what you are* would have said so of only half.
"""

SETUP: Final[Event] = "setup"
"""
The one event that happens before the conversation does, named where two modules have to agree on it.

`running.py` reads it to decide the network and how long a plugin has to answer, and `asking.py`
sends it. Written once here for the reason `roots.py` exists: two spellings of one word is the kind
of disagreement nothing reports.
"""

EVENTS: Final[tuple[Event, ...]] = ("setup", "tool", "before_request", "after_turn", "compose", "action")
"""
The same list as a value, for the places that enumerate it rather than match on it.

Written out rather than derived from the type, because `typing.get_args` over a `type` alias is a
runtime reading of a static thing and this is six words. `test_plugins.py` is what holds the two
against each other.
"""

type Setting = bool | int
"""
What one control on a plugin's card holds, which is a switch's answer or a number's.

Two kinds because the card declares two, and the card *is* the settings schema: there is no second
vocabulary here and no way for a control and the value behind it to disagree about a type.

**Narrower than what `set` may write**, and deliberately: a control is a thing a person answers, so
it is a switch or a number and nothing else, where what a plugin remembers between events is
whatever JSON it likes. See `state_of`.
"""

type Tone = Literal["plain", "quiet", "strong"]
"""
Which of the console's inks a delivered note takes, named rather than passed as a colour.

**Weight within a side rather than a hue.** The palette runs on one axis, which is who produced the
text, and a plugin's note is on the person's side by construction: the console's own machinery
composed it and the model is the party about to be told. So what a plugin gets to say is how heavy
it is, which is the same answer the forget rule reached in taking a stronger ink rather than a
colour of its own.

Three, and settled before the first plugin ships rather than grown one name at a time, because a
tone goes in the record and the record is the conversation.
"""

TONES: Final[frozenset[str]] = frozenset(("plain", "quiet", "strong"))

PLAIN: Final[Tone] = "plain"


def toned(named: str | None) -> Tone:
    """
    One name as the tone it means, with anything else drawing the plain one.

    **The opposite of what an unknown record `kind` does, and deliberately so.** A kind nothing
    answers to is a checkpoint this console cannot read; a tone nothing answers to is a panel in the
    wrong ink, and a session that will not render is far worse than that. It is the
    valid-but-optional reading one layer down: a plugin naming a tone a newer console knows is not
    malformed, it is saying something this one has no answer for.
    """
    match named:
        case "quiet" | "strong" as known:
            return known
        case _:
            return PLAIN


class Speech(BaseModel):
    """
    What every value crossing this boundary is, and the two settings that make it one.

    `frozen` because these are values: a payload is handed to whatever runs the plugin and an answer
    is read by several effects, and neither should be able to edit the other's copy.

    `extra="forbid"` is the strictness described at the top of this module, and it applies in both
    directions for one reason: a payload composed here with a field this console has since renamed
    should fail its own tests rather than reach a plugin that silently ignores it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    def spoken(self) -> dict[str, object]:
        """
        The value as the JSON-native object the pipe will take.

        `by_alias`, because two of the words this protocol uses are Python keywords: a plugin writes
        `return` and `set`, and the fields behind them cannot be called that. Named here rather than
        at each call site so that every value crosses the boundary spelled the same way.
        """
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class Switch(Speech):
    """One control holding a yes or a no, which is a boolean setting and its label."""

    name: str
    label: str
    default: bool = False


class Number(Speech):
    """
    One control holding a count, which is an integer setting and how to draw it.

    `unit` is the suffix beside the box rather than part of the label, which is what lets a reserve
    of forty thousand tokens be two digits and a `K` instead of six digits to count the zeroes of.
    The stored value is whatever the plugin says it is; the unit is presentation and the console
    does no arithmetic with it.

    `least` and `most` are the browser's own refusal, said before the post rather than only after
    it. Optional because most numbers have no floor worth stating, and re-checked at the boundary
    regardless: a `min` on an input is a suggestion a browser was given.
    """

    name: str
    label: str
    default: int = 0

    unit: str | None = None
    least: int | None = None
    most: int | None = None


class Row(Speech):
    """
    One line of a card, which is exactly one control.

    A pair of optionals with a rule rather than a tagged union, because that is the shape the wire
    already has: a plugin writes `{"switch": {...}}`, and the key it used is the tag. Held to exactly
    one so a row that names both is a setup that fails rather than a card that quietly draws the
    first.
    """

    switch: Switch | None = None
    number: Number | None = None

    @model_validator(mode="after")
    def holds_one_control(self) -> Row:
        """
        The rule the shape cannot express, checked where the row is parsed rather than where it is
        drawn.

        A row naming both controls or neither is a plugin that cannot be drawn, and finding that out
        at `setup` is a session that says which plugin and why; found at render time it would be a
        page that will not draw.
        """
        if (self.switch is None) == (self.number is None):
            raise ValueError("a row holds exactly one control, a switch or a number")
        return self

    @property
    def control(self) -> Switch | Number:
        """Whichever control this row holds, for the renderers that do not care which it is."""
        if self.switch is not None:
            return self.switch
        if self.number is None:
            raise ValueError("a row holds exactly one control, and this one holds none")
        return self.number


class Card(Speech):
    """
    A plugin's own card, declared once and drawn by the console.

    **The declared card is also the settings schema**, which is one thing rather than three: a switch
    is a boolean setting and a number box is an integer setting, so one declaration is what the rail
    draws, what the settings blob holds, and what arrives in every payload that plugin receives.

    The cost, stated: a plugin cannot draw a control this vocabulary has no word for.
    """

    heading: str
    rows: tuple[Row, ...] = ()


class Declared(Speech):
    """
    One tool a plugin contributes: what the model sees, and nothing about how it is run.

    `schema` is JSON Schema and is passed to the provider as written. It is not validated against a
    meta-schema here, because what would refuse it is the provider, whose answer is the
    authoritative one and arrives on the turn - the same split `Choice.model` already takes against
    the catalogue.
    """

    name: str
    description: str
    schema_: Mapping[str, object] = Field(default_factory=dict, alias="schema")


class Answering(Speech):
    """
    One answer a plugin contributes to the composer, which is a leader somebody types.

    The same three facts `pages.Answer` holds, minus what it posts: where the answer goes is the
    console's to decide, since a plugin answer posts a value naming the plugin and this leader.

    `demands` is whether the box has to have something in it. False is the handoff's case, where the
    text is an optional note rather than the message, and it is the one thing a plugin has to be able
    to say about a control it does not draw.
    """

    leader: str
    saying: str
    demands: bool = True


class Described(Speech):
    """
    Everything a plugin contributes, which comes back from one call.

    One `setup` rather than a manifest beside the script, because a plugin is a package and not a
    pile of files: a handoff is a tool *and* a condition *and* a card *and* a composer answer, and
    those share one string, one setting and one idea.

    **Everything here is optional and a plugin contributing none of it is legal.** A notifier that
    answers `after_turn` and does its own I/O declares one event and nothing else, and that is using
    this exactly as intended.

    `events` is what stops the mechanism being wasteful: without it every event goes to every plugin,
    and a console with six of them spawns six processes per turn to be told nothing five times.
    """

    events: tuple[Event, ...] = ()
    tools: tuple[Declared, ...] = ()
    answers: tuple[Answering, ...] = ()
    card: Card | None = None

    instructions: str | None = None
    """
    What the session is told, composed into what it is answered under.

    A `setup` contribution rather than an event for the reason tools are: instructions sit in
    front of the cached prefix, so they have to be settled for the session or every request under
    them is re-priced.
    """

    @property
    def settings(self) -> dict[str, Setting]:
        """
        What this plugin's settings are where nobody has said otherwise, read off its own card.

        The card is the schema, so the defaults are the controls' own and there is no second place
        for them to be written down.
        """
        if self.card is None:
            return {}
        return {row.control.name: row.control.default for row in self.card.rows}

    def wants(self, event: Event) -> bool:
        return event in self.events


class Delivery(Speech):
    """
    One message a plugin asks to be put in the session's inbox, and how its panel is drawn.

    What it becomes is a `records.Note` and never anything else, handoff included: letting a plugin
    name which record arm to write would hand out the one vocabulary this console has to own.

    The three presentational fields are all optional, and absent means the console's own answer: the
    plugin's name on the panel's role, no hover text, and the plain ink.
    """

    said: str
    forget: bool = False

    label: str | None = None
    title: str | None = None
    tone: str | None = None


class Answered(Speech):
    """
    What a plugin wants done, which the console performs and the plugin never does.

    **The split is what keeps a plugin out of the queue it would otherwise be racing.** Putting a
    message in an inbox *queues* a session, so a plugin writing its own would be writing to the
    queue from inside the pass still holding the claim on it.

    Where each effect is allowed is not decided here. `tool` alone may `return` or `retry`, only
    `before_request` may `inject`, and `setup` may ask for nothing but `set`; the caller that sent
    the event is the one that knows which it sent, so it is the one that refuses. See `refusing`.
    """

    returned: object | None = Field(default=None, alias="return")
    retry: str | None = None
    deliver: tuple[Delivery, ...] = ()
    inject: tuple[str, ...] = ()

    setting: Mapping[str, object] = Field(default_factory=dict, alias="set")
    """
    Values to write into this plugin's own store, which is one place holding two kinds of thing.

    A name the card declares is a **setting**: it has a control in front of it, a person can answer
    it, and it comes back in `settings` on every later payload. A name the card does not declare is
    **state**: nothing draws it, nobody but this plugin ever writes it, and it comes back in `state`.

    One effect and one column rather than two of each, because there is one write here and one
    lifetime: what separates them is whether a card declared the name, which is a question the
    declaration already answers. So a plugin with no card gets pure state, a plugin with a card gets
    both, and neither has a second mechanism to learn.

    A setting's value is a switch's or a number's, because that is what a control can hold; state is
    whatever JSON the plugin likes. Nothing is refused here either way - a value of the wrong shape
    under a declared name reads back as the control's default, which is what `settings_of` does with
    a hand-edited row and is the same answer to the same mistake.
    """


NOTHING: Final = Answered()
"""
An answer asking for nothing, which is what a plugin that only did its own I/O comes back with.

Named because three callers want the same one and because an empty answer is a real answer rather
than a failure: a plugin that posts to a chat and asks for no effects is using this as intended.
"""


class Payload(Speech):
    """
    What every event carries, before the fields the event itself adds.

    See the field notes below for what each one is and why it is here.
    """

    event: Event
    session: str
    plugin: str
    settings: Mapping[str, Setting] = Field(default_factory=dict)
    worktree: str | None = None
    scratch: str | None = None

    """
    What every event carries, before the fields the event itself adds.

    `plugin` is this plugin's own qualified name, which it cannot work out for itself: a plugin
    declares no name, so what it is called is the key somebody installed it under and the tier that
    key is in. It is here because one thing genuinely needs it - a plugin answering a turn boundary
    has to be able to tell that the turn opened on *its own* delivery, or it fires again for as long
    as the condition that fired it stays true.

    `settings` is this plugin's own, filled from its card's defaults, so a plugin reads a value
    rather than deciding what absent means. `worktree` is where this session's files are, or nothing
    for a session with none.

    **`worktree` is on every payload rather than only the two events that act inside a turn**, which
    is a departure worth stating: `setup` needs it because a plugin composing instructions out of
    the repository's own files reads them at setup time or not at all. A session with no
    repository is handed nothing, which is what it has.

    `scratch` is a directory of this plugin's own, which is where anything it installs at `setup`
    lives and where every later event finds it again. **Its own rather than the session's**, because
    the session's scratch is a place the model writes: a plugin that kept an executable there would
    be running, unattended and at every turn boundary, whatever the model last put at that path.
    Nothing for a plugin that runs unconfined, which has the operator's own environment and a `$HOME`
    and needs nothing from this console to find somewhere to write.
    """

    state: Mapping[str, object] = Field(default_factory=dict)
    """
    Whatever this plugin has written that no control on its card draws, carried back verbatim.

    **Settings are already a place, so state is a setting with nothing in front of it.** One column,
    one `set`, one lifetime; what separates the two is whether the card declared the name, which is a
    question the declaration already answers. So a plugin with no card gets pure state and needs no
    second mechanism, and a plugin with a card cannot confuse a control's value with its own
    bookkeeping, because the split is made here rather than by a naming convention somebody has to
    remember.

    It is settled per session for the same reason settings are: it lives in the session's own row, so
    a fork starts with none and two sessions running one plugin never see each other's.
    """


class SettingUp(Payload):
    """
    The first call, which asks a plugin to get ready and say what it is.

    It carries nothing the envelope does not, because everything it needs is already there: the
    worktree it is being set up against, and a scratch directory of its own to install into.

    **Both halves in one call, and the answer is what the session records.** A plugin with
    dependencies resolves them by being run at all; a plugin that wants a program in the worktree
    fetches it here, because this is the one event with a network. What it returns is `Described`,
    which is settled for the session's life: tool definitions sit above the system prompt in the
    cached prefix, so a set that changed mid-conversation would invalidate everything under it.
    """

    event: Literal["setup"] = "setup"


class Calling(Payload):
    """One of this plugin's tools, called by the model, with the arguments it was called with."""

    event: Literal["tool"] = "tool"
    tool: str
    args: Mapping[str, object] = Field(default_factory=dict)


class Requesting(Payload):
    """
    A model request about to be sent, with the history it will carry.

    `messages` is Pydantic AI's own dump of the history, which is the shape a plugin can read
    tool calls and system parts out of. It is passed whole rather than summarised, because what a
    guidance plugin needs from it - which paths were reached for, and what has already been handed
    over - is not something this console can decide to summarise on somebody else's behalf.
    """

    event: Literal["before_request"] = "before_request"
    messages: tuple[object, ...] = ()


class Opening(Speech):
    """
    What a turn opened on, as the two things a plugin has to be able to tell apart.

    `kind` is the record's own tag, so a plugin reads the vocabulary this console already prints;
    `plugin` is the qualified name of whoever delivered it, and is absent for a message a person
    typed. Together they are the whole of "did I cause this turn", which is what stops a plugin
    that answers a turn boundary recursing on its own delivery.
    """

    kind: str
    plugin: str | None = None


class Ending(Payload):
    """
    A turn that has just been recorded, with the numbers a plugin decides on.

    **The payload that has to be rich, and where the constrained vocabulary is paid for.** A plugin
    can only decide on what it is handed, so unless this carries the recorded context size and the
    model's window, nothing can implement the reserve and the protocol does not meet its own
    specification.

    `opened_on` is there for the same reason: a handoff must never fire on a turn that itself opened
    on a handoff, and the only way a plugin can know that is to be told what opened the turn and
    which plugin, if any, delivered it.
    """

    event: Literal["after_turn"] = "after_turn"
    turn: int
    opened_on: Opening
    context: int = 0
    window: int | None = None


class Composing(Payload):
    """One of this plugin's answers in the composer was submitted, with whatever was in the box."""

    event: Literal["compose"] = "compose"
    leader: str
    said: str = ""


class Acting(Payload):
    """
    A control on this plugin's card was pressed, with the value it now holds.

    One control at a time rather than the whole card, because that is what was pressed: a card that
    posted every control on every press would make a plugin reconcile what changed against what did
    not, which is the console's job and it has already done it.
    """

    event: Literal["action"] = "action"
    control: str
    value: Setting


class Refused(ValueError):
    """
    A plugin answered with something this console cannot act on, naming the plugin and the reason.

    One type for both halves of that: an answer that is not readable at all, and one asking for an
    effect the event it answers does not allow. Both are a mistake in somebody's plugin and both are
    reported the same way, which is a session that says which plugin and why.
    """


ANSWERED: Final[TypeAdapter[Answered]] = TypeAdapter(Answered)
DESCRIBED: Final[TypeAdapter[Described]] = TypeAdapter(Described)


def parse_answer(plugin: str, said: object) -> Answered:
    """One plugin's answer to an event, or a refusal naming the plugin and what was wrong with it."""
    try:
        return ANSWERED.validate_python(said)
    except ValidationError as broken:
        raise Refused(f"{plugin} answered with something this console cannot read: {broken}") from broken


def parse_described(plugin: str, said: object) -> Described:
    """What one plugin says it is, or a refusal naming the plugin and what was wrong with it."""
    try:
        return DESCRIBED.validate_python(said)
    except ValidationError as broken:
        raise Refused(f"{plugin} described itself in a way this console cannot read: {broken}") from broken


def refusing(plugin: str, event: Event, answered: Answered) -> None:
    """
    Refuse an answer asking for an effect the event it answers does not allow.

    Checked here rather than left to whichever caller happens to read a field, so that the table in
    the design note is enforced in one place: `return` and `retry` are a tool's alone, and `inject`
    belongs to the one event that has a request to append to. Anything else asking for one is a
    plugin that would otherwise be quietly doing nothing.
    """
    if event != "tool":
        if answered.returned is not None:
            raise Refused(f"{plugin} answered a {event} with a return, which only a tool call may ask for")
        if answered.retry is not None:
            raise Refused(f"{plugin} answered a {event} with a retry, which only a tool call may ask for")
    if event != "before_request" and answered.inject:
        raise Refused(f"{plugin} answered a {event} with an injection, which only before_request may ask for")


def settings_of(described: Described, held: Mapping[str, object]) -> dict[str, Setting]:
    """
    One plugin's settings as its card declares them, filled in from whatever the column holds.

    **This is the parse the `STRICT` table stopped doing.** The blob is unchecked text, so the
    invariant moves here, against the schema the card already declares: a control's own default
    answers for a field nobody has ever set, and a stored value of the wrong shape is passed over
    rather than handed to a plugin that declared a number and would be given a string.

    Passed over rather than refused, because what could put one there is a hand-edited row, and a
    session that will not run is a worse answer to that than a setting that reads as untouched.
    """
    settings: dict[str, Setting] = {}
    for name, default in described.settings.items():
        found = held.get(name)
        if isinstance(default, bool):
            settings[name] = found if isinstance(found, bool) else default
        elif isinstance(found, int) and not isinstance(found, bool):
            settings[name] = found
        else:
            settings[name] = default
    return settings


def state_of(described: Described, held: Mapping[str, object]) -> dict[str, object]:
    """
    Everything one plugin has stored that its card does not draw, which is the plugin's own state.

    The complement of `settings_of` over the same mapping, so the two together are exactly what is in
    the column and nothing is in both. Nothing is defaulted and nothing is type-checked, because
    nothing declared it: what a plugin wrote is what it gets back.
    """
    declared = set(described.settings)
    return {name: value for name, value in held.items() if name not in declared}


def setting_of(described: Described, control: str, held: object) -> Setting | None:
    """
    One posted control's value as the setting its card declares, or nothing where it declares none.

    What an `action` needs: a control name arrived from a browser, and what it is worth depends on
    which kind of control the plugin declared. A name the card does not carry is `None`, which the
    route reads as a request naming nothing.
    """
    if described.card is None:
        return None
    for row in described.card.rows:
        if row.control.name != control:
            continue
        if isinstance(row.control, Switch):
            return bool(held)
        if isinstance(held, bool) or not isinstance(held, int):
            return None
        return held
    return None


def carried(said: Sequence[object]) -> tuple[object, ...]:
    """A message history as the payload holds it, which is whatever the dump already produced."""
    return tuple(said)
