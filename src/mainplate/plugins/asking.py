# Asking a session's plugins something, and doing what they answer.
#
# **A plugin answers with what it wants done, and never does it.** The console performs the effects,
# which is what keeps a plugin out of the queue it would otherwise be racing: putting a message in an
# inbox *queues* a session, so a plugin writing its own would be writing to the queue from inside the
# pass still holding the claim on it.
#
# Where an effect is performed differs by where the event was fired, and the split is the durability
# layer's rather than a preference. A `tool` answer is performed inside the step that records the
# call, so a resumed pass replays the return and writes no second entry. An `after_turn` answer has
# no step to sit in, so its deliveries come back as a value the pass returns and the composition root
# performs - which is the split `Crossed` already made, generalised. A `compose` or an `action` is
# fired from a request handler, which is where this console already writes.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets import ToolsetTool
from pydantic_ai.toolsets.external import TOOL_SCHEMA_VALIDATOR

from mainplate import records
from mainplate.plugins.installed import ON
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.installed import dropping_collisions
from mainplate.plugins.protocol import Acting
from mainplate.plugins.protocol import Answered
from mainplate.plugins.protocol import Calling
from mainplate.plugins.protocol import Composing
from mainplate.plugins.protocol import Declared
from mainplate.plugins.protocol import Delivery
from mainplate.plugins.protocol import Ending
from mainplate.plugins.protocol import Event
from mainplate.plugins.protocol import Gating
from mainplate.plugins.protocol import Opening
from mainplate.plugins.protocol import Payload
from mainplate.plugins.protocol import Requesting
from mainplate.plugins.protocol import Setting
from mainplate.plugins.protocol import SettingUp
from mainplate.plugins.protocol import parse_answer
from mainplate.plugins.protocol import parse_described
from mainplate.plugins.protocol import refusing
from mainplate.plugins.protocol import settings_of
from mainplate.plugins.protocol import state_of
from mainplate.plugins.running import Speaking
from mainplate.snapshots import Worktree
from mainplate.tending import TENDED
from mainplate.tending import Tending

logger = logging.getLogger(__name__)

RETRIES = 3
"""
How many times a plugin's tool may be corrected before the turn gives up on it.

Above Pydantic AI's default of one for the reason every other tool here is: what a plugin turns down
is correctable from the message, so a model that gets the arguments wrong twice should still be
allowed to get them right.
"""


type Delivering = Callable[[records.Note], Awaitable[None]]
"""
Where a note a plugin asked for goes, which is this session's own inbox.

Bound to the session already, so nothing below has to carry one: a `Live` is one session's plugins,
and a delivery made through it can only reach that session.
"""


type Storing = Callable[[str, Mapping[str, object]], Awaitable[None]]
"""
What a plugin's `set` writes, by qualified name, bound to the session the same way.

`object` rather than `Setting`, because one write covers both of the things that column holds: a
name the card declares is a control's value and is a switch or a number, and a name it does not is
the plugin's own state and is whatever JSON it likes. The split is made when the value is read back,
not when it is written.
"""


async def nowhere(note: records.Note) -> None:
    """
    Where a delivery goes when nobody said, which is nowhere at all.

    The same reading `Handoffs` absent already took: a capability nobody supplied is the feature
    absent rather than a failure, and it is what keeps a bare `Live` inert in a test that is not
    about deliveries.
    """


async def unstored(plugin: str, values: Mapping[str, object]) -> None:
    """The same, one effect along: a console given no way to write settings writes none."""


@dataclass(frozen=True, slots=True)
class Live:
    """
    One session's plugins, as the pass and the request handlers both ask them things.

    Built per pass and per handler rather than held for the process, because everything in it is
    that session's: which plugins it enrolled, what they are set to, and where its files are.

    `enrolled` is what is actually **on**, which under the switches a session was loaded under is
    every plugin it loaded. A plugin that is off contributes nothing - no tool in the prefix, no card,
    no answer in the composer, and no events - so it is simply not here, exactly as one that was never
    loaded is never described.
    """

    session: str
    enrolled: tuple[Enrolled, ...] = ()
    tending: Tending = TENDED
    """
    What this session's plugins are set to and remember, **captured once at the top of the pass.**

    So a `set` lands in the column and reaches the next pass, and no event of this one: not another
    plugin's, and not the writer's own next event. A plugin that writes at `tool` and reads at
    `after_turn` in the same turn reads what the pass began with. The bundled plugins never notice,
    because every `set` they make rides beside a `deliver` and a delivery ends the pass; a plugin
    that writes without delivering is the one this matters to.

    Once rather than re-read per event, because a setting is a place two writers share: a switch
    flicked while a turn was in flight would have that turn answered under one value and judged
    under another. The cost is stated in the design note beside the mechanism.
    """
    speaking: Speaking | None = None
    worktree: Worktree | None = None
    """
    This session's tree as this console knows it, git directory and all, rather than as a path.

    The value and not `root`, because what runs a confined plugin builds a sandbox around it and a
    tree whose directory was never named is one git discovers from a pointer file the session can
    write. See `Speaking`.
    """

    delivering: Delivering = nowhere
    storing: Storing = unstored

    def wanting(self, event: Event) -> tuple[Enrolled, ...]:
        """
        The plugins that asked to hear about this, which is what stops the mechanism being wasteful.

        Without it every event goes to every plugin and a console with six of them spawns six
        processes per turn to be told nothing five times.
        """
        return tuple(each for each in self.enrolled if each.described.wants(event))

    def named(self, qualified: str) -> Enrolled | None:
        """One running plugin by qualified name, or nothing where it is not one of this session's."""
        return next((each for each in self.enrolled if each.qualified == qualified), None)

    def settings(self, plugin: Enrolled) -> dict[str, Setting]:
        """
        What one plugin reads as its settings, which is its card's defaults under whatever was stored.

        The parse the `STRICT` table stopped doing, against the schema the card already declares; see
        `protocol.settings_of`.
        """
        return settings_of(plugin.described, self.tending.of(plugin.qualified))

    def instructions(self) -> tuple[str, ...]:
        """
        What every running plugin contributes to what this session is answered under, in enrolment
        order.

        A `describe` contribution rather than an event, because instructions sit in front of the
        cached prefix: composed again mid-conversation, every remaining request under them is
        re-priced.
        """
        return tuple(each.described.instructions for each in self.enrolled if each.described.instructions is not None)

    def tools(self) -> tuple[tuple[str, Enrolled, Declared], ...]:
        """
        Every tool this session's plugins contribute, as the model's name for it and who to ask.

        Three values rather than a mapping, because two of them are the address: which plugin, and
        what that plugin declared. A repository's are prefixed, so what the model sees and what the
        plugin called it are not the same string.

        The whole declaration rather than its name, because the one caller wants the description and
        the schema too and finding them again would be two scans of this plugin's tools per tool.
        """
        return tuple((named, plugin, declared) for plugin in self.enrolled for named, declared in plugin.tools())

    def answers(self) -> tuple[tuple[str, Enrolled, str], ...]:
        """The same for the composer's leaders, under the word somebody actually types."""
        return tuple(
            (named, plugin, declared.leader) for plugin in self.enrolled for named, declared in plugin.answers()
        )

    def answering(self, leader: str) -> tuple[Enrolled, str] | None:
        """Which plugin owns one typed leader, and what that plugin calls it."""
        return next(((plugin, own) for named, plugin, own in self.answers() if named == leader), None)

    async def ask(self, plugin: Enrolled, payload: Payload) -> Answered:
        """
        Send one event to one plugin and read what it wants done, holding it to what the event allows.

        The vocabulary is closed and the console owns it, so an answer asking for an effect this
        event has no room for is refused here rather than quietly doing nothing.
        """
        if self.speaking is None:  # pragma: no cover - a `Live` with plugins always has one
            raise RuntimeError("this console was given no way to run a plugin")
        answered = parse_answer(plugin.qualified, await self.speaking(plugin.installed, payload, self.worktree))
        refusing(plugin.qualified, payload.event, answered)
        return answered

    def state(self, plugin: Enrolled) -> dict[str, object]:
        """
        Whatever this plugin has written that no control on its card draws.

        The complement of `settings`, over the same stored mapping, so the two are exactly what is in
        the column and nothing is in both.
        """
        return state_of(plugin.described, self.tending.of(plugin.qualified))

    def payload(self, plugin: Enrolled) -> dict[str, object]:
        """The envelope every event carries: this session, its files, and this plugin's own store."""
        return {
            "session": self.session,
            "plugin": plugin.qualified,
            "settings": self.settings(plugin),
            "state": self.state(plugin),
            "worktree": None if self.worktree is None else str(self.worktree.root),
        }

    async def perform(self, plugin: Enrolled, answered: Answered) -> tuple[records.Note, ...]:
        """
        Do what one answer asked for, and hand back the notes it wants delivered.

        `set` is written here because it is a column write rather than a queue write: nothing about
        it races the claim the pass is holding, and a patch against this plugin's own sub-object
        leaves every other plugin's save alone. The notes are handed back rather than delivered,
        because where a delivery may be made is the caller's question and not this one's - inside a
        step it is safe, and at a turn boundary it is a value the pass returns.
        """
        if answered.setting:
            await self.storing(plugin.qualified, answered.setting)
        return tuple(noted(plugin.qualified, each) for each in answered.deliver)


def noted(plugin: str, delivery: Delivery) -> records.Note:
    """
    One asked-for delivery as the record it becomes, attributed to whoever asked.

    Field by field rather than through a splat, because these are two types that happen to agree
    today: one is what a plugin said and one is what the store holds, and a field added to either
    should fail here rather than arrive silently.
    """
    return records.Note(
        said=delivery.said,
        plugin=plugin,
        forget=delivery.forget,
        label=delivery.label,
        title=delivery.title,
        tone=delivery.tone,
    )


def opening_of(said: records.Delivered) -> Opening:
    """
    What a turn opened on, as the two things a plugin has to be able to tell apart.

    A plugin cannot see the inbox, so this is the whole of "did I cause this turn": the record's own
    tag, and the qualified name of whoever asked for it where a plugin did. It is what stops a plugin
    that answers a turn boundary firing again on its own delivery, for ever, while the condition that
    fired it stays true.
    """
    return Opening(kind=said.kind, plugin=said.plugin if isinstance(said, records.Note) else None)


async def setting_up(
    installed: Sequence[Installed], speaking: Speaking, session: str, worktree: Worktree | None
) -> tuple[Enrolled, ...]:
    """
    Set every declared plugin up, all at once, and fail naming whichever one will not answer.

    **The first call to a plugin sets it up and asks what it is**, and everything it contributes
    comes back from that one call. Concurrent because the plugins are independent and each is a cold
    process doing the slowest thing it will ever do, and in the order they were declared because the
    record is what a settings step is drawn from twice.

    **One broken plugin stopping the whole session is the right answer in every tier.** A repository
    plugin is one you granted and a user plugin is one you configured, so either failing silently
    leaves somebody holding a choice they cannot use, and a session that quietly ran without it would
    be answering under a setup nobody asked for.

    **A setup has to be idempotent, and this is where that constraint comes from.** What it produces
    is recorded once every plugin has answered, so one that fails leaves nothing recorded and the
    next attempt sets all of them up again. Recording each separately would still leave a session
    half set up, and being run twice is what an install is already built to survive.
    """
    where = None if worktree is None else str(worktree.root)
    said = await asyncio.gather(
        *(
            speaking(each, SettingUp(session=session, plugin=each.qualified, worktree=where), worktree)
            for each in installed
        )
    )
    return tuple(
        Enrolled(installed=each, described=parse_described(each.qualified, answered))
        for each, answered in zip(installed, said, strict=True)
    )


def recorded_declaration(installed: Sequence[Installed], setup: bool = False) -> dict[str, object]:
    """
    What one tier's declaring file named, as the JSON-native value the store's codec will take.

    The counterpart of `recorded_registration` one moment earlier, and the pair is the trust boundary
    made structural: this is written by reading files and that is written by running programs, so a
    session sitting on its settings step has one and not the other.

    `setup` is the repository's half saying it carries a `.mainplate/setup`, which is not a plugin
    and is read on the same pass for the same reason: the step draws a switch for it, and nothing
    that draws a switch may have run anything.
    """
    return records.Declared(
        plugins=tuple(records.Named(name=each.name, tier=each.tier.value, path=str(each.path)) for each in installed),
        setup=setup,
    ).recorded()


def parse_declaration(recorded: object) -> tuple[Installed, ...]:
    """What a session recorded about the plugins it may run, back as the values a load runs them from."""
    held = records.Declared.model_validate(recorded)
    return tuple(Installed(tier=Tier(each.tier), name=each.name, path=Path(each.path)) for each in held.plugins)


def recorded_registration(enrolled: Sequence[Enrolled]) -> dict[str, object]:
    """What one tier's worth of plugins is, as the JSON-native value the store's codec will take."""
    return records.Registered(
        plugins=tuple(
            records.Enrolled(
                name=each.installed.name,
                tier=each.installed.tier.value,
                path=str(each.installed.path),
                described=each.described.spoken(),
            )
            for each in enrolled
        )
    ).recorded()


def parse_registration(recorded: object) -> tuple[Enrolled, ...]:
    """
    What a session recorded about its plugins, back as the values a pass runs them from.

    The declaration is read once and every pass after replays this, which is what makes registration
    once per session true across a restart - and what stops a plugin edited underneath a running
    session reaching it on any turn at all.
    """
    held = records.Registered.model_validate(recorded)
    return tuple(
        Enrolled(
            installed=Installed(tier=Tier(each.tier), name=each.name, path=Path(each.path)),
            described=parse_described(f"{each.tier}:{each.name}", each.described),
        )
        for each in held.plugins
    )


def running(enrolled: Sequence[Enrolled], tending: Tending) -> tuple[Enrolled, ...]:
    """
    Which of a session's loaded plugins it runs, which under the switches it was loaded under is all
    of them, less any repository contribution that collides with one of yours.

    The switches are applied rather than assumed, because the two facts are recorded in two places
    and only one of them is write-once: the registration is settled for the session's life, and the
    switches are a column somebody could still have edited by hand. The switch is the session's own
    answer where it has one and `ON` where it has not.

    **The drop is here rather than at each reader**, because this is the only thing that produces a
    running set and a reader that forgot got a row in the composer menu, and a card in the rail, for
    something no event will ever reach. See `dropping_collisions` for why a repository's is dropped
    rather than refused.

    **Refusing a collision between two of yours is not here**, and that is the half that stays at the
    caller: what a pass is about to do with the set is open a turn, so a set it cannot settle is a
    turn it must not open, where a handler is about to draw a menu and a refusal there is a page that
    will not render over a session whose settings step is the thing that fixes it. See
    `without_collisions`.
    """
    return dropping_collisions(tuple(each for each in enrolled if tending.on(each.qualified, ON)))


type Asking = Callable[[str, str, Mapping[str, object]], Awaitable[object]]
"""
How one plugin-contributed tool is actually called: which plugin, which of its tools, and the
arguments.

A function rather than the `Live` itself, so the toolset below knows nothing about payloads, effects
or inboxes. What it hands back is what the model is told, and a correctable refusal reaches it as a
`ModelRetry` raised out of the call.
"""


@dataclass(frozen=True, slots=True)
class Contributed:
    """One plugin's tool as the agent sees it: what to send the model, and who to ask when it calls."""

    named: str
    plugin: str
    declared: str
    definition: ToolDefinition


class PluginTools(AbstractToolset[Any]):
    """
    Every tool this session's plugins contribute, as one toolset the agent knows nothing else about.

    **Tools are the safest thing a plugin can contribute, not a forbidden one.**
    `StepwiseDurability.wrap_tool_execute` wraps every call in a step and writes a `records.Returned`,
    so a plugin-provided tool's answer is recorded and a resumed pass replays it without running the
    script again. That is what puts a tool call inside the line the durability layer draws.

    The arguments are passed to the plugin as the model produced them, validated against nothing
    here: the schema is the plugin's and what would refuse a bad call is the plugin, whose refusal
    comes back as a `retry` and reaches the model as a correction. Validating a stranger's JSON
    Schema in this process would be a second implementation of the provider's own check.
    """

    def __init__(self, contributed: Sequence[Contributed], asking: Asking) -> None:
        self.contributed = tuple(contributed)
        self.asking = asking

    @property
    def id(self) -> str | None:
        return "plugins"

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        return {
            each.named: ToolsetTool(
                toolset=self,
                tool_def=each.definition,
                max_retries=RETRIES,
                args_validator=TOOL_SCHEMA_VALIDATOR,
            )
            for each in self.contributed
        }

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> object:
        found = next((each for each in self.contributed if each.named == name), None)
        if found is None:  # pragma: no cover - the agent only calls what `get_tools` offered
            raise ModelRetry(f"there is no tool called {name!r}")
        return await self.asking(found.plugin, found.declared, tool_args)


def contributions(live: Live) -> tuple[Contributed, ...]:
    """
    Every plugin tool of this session as a definition the model is sent.

    The description is the plugin's own and the schema is passed through as written. A repository's
    name is prefixed here rather than at the plugin, so what a plugin declares is what it is called
    everywhere it is *its* name, and the prefix exists only where the model would otherwise see two
    tools it cannot be asked to choose between.
    """
    return tuple(
        Contributed(
            named=named,
            plugin=plugin.qualified,
            declared=declared.name,
            definition=ToolDefinition(
                name=named,
                description=declared.description,
                parameters_json_schema=object_schema(declared.schema_),
            ),
        )
        for named, plugin, declared in live.tools()
    )


def object_schema(declared: Mapping[str, object]) -> Any:
    """
    A declared schema as the object schema a tool definition takes, or an empty one where it is not.

    A tool's parameters are an object on every wire this console speaks to, so a plugin that declared
    a bare `{"type": "string"}` has declared something no provider will take. Answered with the empty
    object rather than refused, because what a wrong schema costs is a tool the model calls with no
    arguments, where a refusal costs the whole session - and the provider's own answer about a schema
    is the authoritative one either way.
    """
    if declared.get("type") != "object":
        return {"type": "object", "properties": {}}
    return dict(declared)


def asking_through(live: Live) -> Asking:
    """
    Calling a plugin's tool, with its answer applied and its refusal raised as a correction.

    **Three effects out of one call**, which is what the protocol was read off: a `retry` is a
    correctable refusal, a `deliver` puts a message in the inbox carrying its own boundary, and a
    `return` is the value the model is handed. The order is the one the tool intends - a refusal
    happens instead of the other two, and a delivery happens before the model is told it was
    recorded.

    The delivery is made from **inside** the call rather than handed back, and that is sound rather
    than an exception to the rule at the top of this module: `wrap_tool_execute` wraps this whole
    call in a step, so a resumed pass replays the recorded return and writes no second entry.
    """

    async def call(qualified: str, declared: str, arguments: Mapping[str, object]) -> object:
        plugin = live.named(qualified)
        if plugin is None:  # pragma: no cover - built from the same list the agent was given
            raise ModelRetry(f"{qualified} is not one of this session's plugins")
        answered = await live.ask(plugin, Calling(tool=declared, args=dict(arguments), **live.payload(plugin)))
        if answered.retry is not None:
            raise ModelRetry(answered.retry)
        for note in await live.perform(plugin, answered):
            await live.delivering(note)
        return answered.returned

    return call


def declined(refusals: Sequence[tuple[str, str]]) -> str:
    """
    What the model is told in a refused call's place: who refused it, and why, one line apiece.

    Composed by the console rather than by whichever plugin spoke first, because several may refuse
    one call and the model should hear all of them. Each names its plugin, so a model told twice can
    tell two policies from one said twice.
    """
    return "\n\n".join(f"Refused by {plugin}: {reason}" for plugin, reason in refusals)


async def gating(live: Live, tool: str, args: Mapping[str, object]) -> str | None:
    """
    Ask every plugin that wants to know that a tool is about to run, and say whether it may.

    **Any one refusal is enough**, and every refusal is told: the answer is what the model is handed
    in the call's place, or nothing where every plugin let it through. The call is not run either
    way until this has returned, which is what makes a refusal a refusal rather than a comment.

    Asked and performed inside the step that records the call, the way a `tool` answer is, so a
    delivery asked for here is made where it is asked and a resumed pass replays the recorded answer
    without asking again. Concurrent across plugins and performed in enrolment order, for
    `injections`' reason.
    """
    wanting = live.wanting("before_tool")
    answers = await asyncio.gather(
        *(live.ask(plugin, Gating(tool=tool, args=dict(args), **live.payload(plugin))) for plugin in wanting)
    )
    refusals: list[tuple[str, str]] = []
    for plugin, answered in zip(wanting, answers, strict=True):
        for note in await live.perform(plugin, answered):
            await live.delivering(note)
        if answered.refuse is not None:
            refusals.append((plugin.qualified, answered.refuse))
    return declined(refusals) if refusals else None


async def injections(live: Live, messages: Sequence[object]) -> tuple[str, ...]:
    """
    What every plugin wants appended to the request about to go out, in enrolment order.

    A system-voice message appended to the request, which costs the cached prefix nothing where an
    edited instruction re-prices everything under it. What it answers is recorded by the caller, so a
    resumed pass replays the injection rather than recomputing it from a plugin that may not be pure.

    **Asked concurrently and performed in order**, which is `setting_up`'s bargain on a smaller
    scale: each ask is a process spawned and waited on, so asking one at a time makes a request stall
    for the sum of their round trips where nothing orders them. What a plugin is handed is the
    `Tending` this pass captured once at the top, so a `set` is invisible for the rest of the pass -
    to the other plugins and to the one that wrote it. See `Live.tending`. The effects and the order
    they are appended in are sequential all the same, because enrolment order is what the record is
    read back in.
    """
    wanting = live.wanting("before_request")
    answers = await asyncio.gather(
        *(live.ask(plugin, Requesting(messages=tuple(messages), **live.payload(plugin))) for plugin in wanting)
    )
    said: list[str] = []
    for plugin, answered in zip(wanting, answers, strict=True):
        await live.perform(plugin, answered)
        said.extend(answered.inject)
    return tuple(said)


async def ending(
    live: Live, turn: int, opened_on: Opening, context: int, window: int | None
) -> tuple[records.Note, ...]:
    """
    Tell every plugin that a turn was recorded, and collect whatever they want delivered.

    **Asked only where a turn actually ended.** A pass that spent its allowance, hit a refusal, or is
    already carrying deliveries returns before this, so a plugin is never asked about a turn that
    stopped part-way, which is a turn left unfinished for reasons that are the console's rather than
    the conversation's.

    Several plugins answering one event is not a conflict needing a tiebreak: each delivery is one
    inbox entry, and the inbox is already a queue that orders them and opens a turn per message.

    **Asked concurrently and performed in order**, for `injections`' reason: the plugins are
    independent processes and a turn boundary should cost the slowest of them rather than all of
    them added up, while the notes still reach the inbox in enrolment order.
    """
    wanting = live.wanting("after_turn")
    answers = await asyncio.gather(
        *(
            live.ask(
                plugin,
                Ending(turn=turn, opened_on=opened_on, context=context, window=window, **live.payload(plugin)),
            )
            for plugin in wanting
        )
    )
    notes: list[records.Note] = []
    for plugin, answered in zip(wanting, answers, strict=True):
        notes.extend(await live.perform(plugin, answered))
    return tuple(notes)


async def composed(live: Live, plugin: Enrolled, leader: str, said: str) -> tuple[records.Note, ...]:
    """
    One plugin's own answer in the composer was submitted, with whatever was in the box.

    Asked only of a plugin that wanted `compose`, which is `events` doing its job here as everywhere
    else: a plugin that declared an answer and not the event has declared a control it will never be
    told about, and spawning a process to tell it nothing is the waste `events` exists to prevent.
    """
    if not plugin.described.wants("compose"):
        return ()
    answered = await live.ask(plugin, Composing(leader=leader, said=said, **live.payload(plugin)))
    return await live.perform(plugin, answered)


async def acted(live: Live, plugin: Enrolled, control: str, value: Setting) -> tuple[records.Note, ...]:
    """
    Tell one plugin that a control on its card moved, and what it now holds.

    The value is already written by the time this runs, because that is what a control *is*: a card
    that told the plugin and left the column alone would draw one answer and hold another. What the
    plugin does with being told is its own, and most do nothing - a setting is read when the plugin
    next runs, so `action` is for the ones that want to act at the moment of the press.

    One control at a time rather than the whole card, because that is what moved: a card that
    announced every control on every press would make a plugin reconcile what changed against what
    did not, which the console has already done.

    Asked only of a plugin that wanted `action`, which most do not: a setting is read when the plugin
    next runs, so being told at the moment of the press is for the few that want to act on it. The
    value is written either way, because that is what the control *is*.
    """
    if not plugin.described.wants("action"):
        return ()
    answered = await live.ask(plugin, Acting(control=control, value=value, **live.payload(plugin)))
    return await live.perform(plugin, answered)


@dataclass(frozen=True, slots=True)
class Declaring:
    """
    Where a console's plugins come from, held once for the process and read per session.

    The bundled set and the operator's are both fixed at startup, because both are files outside
    every worktree and neither can change under a running console without one being restarted.
    A repository's are read per session, from the worktree, once.

    Injected into the pass and into the service rather than reached for, symmetric with `Pricer` and
    `Draining`: what runs a plugin spawns processes and knows about sandboxes, and a console given
    none of this simply has no plugins - which is what every test that is not about them wants.
    """

    console: tuple[Installed, ...] = ()
    speaking: Speaking | None = None
    confining: bool = False

    def runs(self, repository: str | None, trusted: bool) -> bool:
        """
        Whether this session may run the plugins its repository carries.

        Both facts come off the session's own recorded `Choice`, so this is a read of something
        already settled rather than a question asked again. Taken apart rather than as the `Choice`
        itself, because `agent.py` reaches this module for the toolset and a `Choice` here would
        close the ring.

        Off wherever this console has no sandbox, whatever the session recorded, and that is a
        refusal rather than a fallback: a repository's plugin is safe to run because the process is
        confined, so a console that cannot confine one has nothing to offer in its place.
        """
        return trusted and repository is not None and self.confining
