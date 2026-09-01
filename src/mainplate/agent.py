# What a declared endpoint becomes once it is something you can run: a wire per endpoint, and the agent
# built over one for a session that chose it.
#
# The wire is what is built once and held for the process, because it carries an SDK provider which
# carries an HTTP client with a connection pool. The agent is not: every model over one endpoint
# shares that endpoint's client, and an agent costs tens of microseconds against a model call that
# costs seconds, so there is nothing to gain by keeping a mapping of them and something to lose,
# which is that the set of models is discovered and changes while this process runs.
#
# So the split is along what varies. An endpoint is written down and fixed, and building its wire
# eagerly at startup is what makes a malformed `url` or a refused credential a failure that names
# itself before anything takes traffic. Which models it serves is discovered, lives in
# `catalogue.py`, and is nobody's business here.
#
# **Three words that are easy to run together, kept apart here on purpose.** An *endpoint* is what
# `config.yaml` declares: a URL, a format, a credential. A *wire* is the built thing that talks one
# API format, and is what this module is mostly about. A *provider* is whoever made a model
# (`anthropic`, `fireworks`, `xai`), which is discovered rather than configured and is a property of
# a model rather than of either of the other two - the same Fireworks model is reachable over both
# of exe.dev's wires under one id. `AnthropicProvider` and `OpenAIProvider` below are Pydantic AI's
# own names for SDK clients and are the one place the word means something else.
#
# Each format gets one wire class holding the two things that format decides: how to name a model
# over it, and how to ask it what it serves. They are together because they are the same knowledge,
# and because a third format should be one class rather than an edit in three files.
#
# The file tools hang off the agent as a toolset, and they hang off it *per session* rather than
# once for the process, because what makes a path safe is the worktree it is resolved inside and
# every session has its own. A session with no repository is built with no toolset at all: a console
# used to talk rather than to edit is what this was before there were repositories, and four tools
# that can only fail are worse than none. `StepwiseDurability` is what records the calls, on the
# same capability that already records the model requests.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from typing import Protocol
from typing import assert_never

from anthropic import AsyncAnthropic
from anthropic.types import ModelInfo
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.settings import ThinkingLevel

from mainplate.config import Config
from mainplate.config import Endpoint
from mainplate.durability import StepwiseDurability
from mainplate.snapshots import Workspace
from mainplate.tools import Files
from mainplate.tools import file_tools


@dataclass(frozen=True, slots=True)
class Choice:
    """
    Which endpoint, which model, and how hard to think: recorded at creation and fixed for life.

    Fixed because a conversation that changed model halfway would replay its recorded responses
    from one and continue on another, so what the transcript shows and what the next turn is
    reasoning from would have different authors. Forking is how you change your mind: it starts a
    session from a point in this one's history, so the answer before the branch has one author and
    the answer after it has another, and both remain readable.

    That is also why `thinking` is here rather than beside a turn. It is not a property of a
    question, it is a property of the thing answering, and a session whose effort moved mid-way
    would replay recorded answers reasoned at one budget and continue at a different one.

    The endpoint is what carries the API format, which is why it is recorded rather than looked up:
    the same model id genuinely does appear behind two formats - every Fireworks model on exe.dev's
    gateway is listed by both - and the two serialize a conversation differently, so a session that
    resolved its own format from a list discovered later could resume in the other one.
    """

    endpoint: str
    model: str

    repository: str | None = None
    """
    Which repository this session works in, as the id a forge gave it, or nothing for none.

    Fixed for life like the rest, and for a plainer reason than the model is: a conversation is
    *about* the files it is looking at, so one that changed repository halfway would have a
    transcript whose earlier half discusses code the later half cannot see.

    A fork may **attach** one to a session that had none, and may not **swap** one for another. The
    two look alike and are not: swapping re-asks a turn against different files, which is a
    different question wearing the same words, where attaching carries on with files where there
    were none - and the turns being inherited were not asked against other files, they were asked
    against no files at all.

    An id rather than a path or a URL, because how to reach a repository is a discovery-time fact
    and which repository it is is not. Absent means a session with no files at all, which is what a
    console being used to talk rather than to edit has and what every session had before this.
    """

    thinking: ThinkingLevel | None = None
    """
    How hard to think, or nothing at all to leave the setting off the request.

    Absent is the meaning rather than an omission, exactly as `Endpoint.url` is: a request with
    no thinking setting is answered however the model behaves by default, which for a reasoning
    model is to reason and for the rest is not to. Defaulted here because most constructions are
    ours; `parse_choice` supplies it explicitly, so a checkpoint written before this existed reads
    back as the default rather than as a failure.
    """

    @property
    def settings(self) -> ModelSettings | None:
        """
        What this choice asks of a model, which today is the thinking level and nothing else.

        `None` rather than an empty mapping, so a choice that asks for nothing builds an agent
        indistinguishable from one built before this field existed.
        """
        return None if self.thinking is None else ModelSettings(thinking=self.thinking)


class UnknownChoice(LookupError):
    """
    A session names an endpoint the configuration no longer declares.

    Its own type because the answer is a person's rather than a retry's: the endpoint was there when
    the session started, so the file was edited, and the fix is to put it back or to start a new
    session. The console says so on the session's own page rather than leaving it to a worker log.

    Only ever about the endpoint. A *model* an endpoint has stopped listing is not this, because an
    endpoint routes more ids than it advertises: its own refusal is the authoritative answer about
    one, and it arrives on the turn rather than here.
    """


@dataclass(frozen=True, slots=True)
class Listed:
    """
    One model an endpoint says it serves: what a request names it, and what a person reads.

    `provider` is whoever made the model, and what the picker groups by. It is the part of the id
    before the slash where there is one, because a gateway fronting several vendors prefixes them
    and that prefix is the only thing that distinguishes a Claude from a Kimi in a list of seventy.
    Where there is no slash the endpoint fronts one vendor and the format's own name is the honest
    answer.

    It is deliberately *not* a level of any hierarchy. The same provider appears behind more than
    one endpoint - every Fireworks model on exe.dev's gateway is listed by both its formats, under
    one id - so this is a facet of a model rather than a parent of it, and the tree really is
    `endpoint -> model` with this as a heading.

    This is **identity and nothing else**: which model, under which two names, in which group. What
    a card says *about* a model - what it costs, how much it reads, what it can do - is not here and
    is not read off a listing at all. It comes from `reference.py`, from one database, for every
    model alike.

    That is a deliberate refusal rather than an omission, and the reason is what these listings look
    like. The Anthropic wire describes Claude in detail, forwards a different vendor's record
    verbatim for the models it resells, and says nothing whatever about GPT; the OpenAI wire answers
    four fields; neither publishes a price at any point. Reading each of those and filling the gaps
    from a database would put three shapes of card on one page, where the facts shown depended on
    which wire happened to answer and two models could not honestly be compared. One source is worth
    more here than the extra coverage a merge would buy.
    """

    id: str
    label: str
    provider: str

    upstream: str | None = None
    """
    What the service actually serving this model calls it, where the endpoint says.

    Identity rather than description, which is why this one field *is* read off the listing. A
    gateway reselling somebody else's model echoes the original's canonical name beside its own
    routed id: `fireworks/kimi-k3` is routed here and is `accounts/fireworks/models/kimi-k3` there.
    It is the second of the two names the reference is looked up under, and the one that finds a
    model a gateway has renamed into its own namespace - which is most of what a gateway serves.
    """


class Wire(Protocol):
    """
    One built SDK client, and the two questions only its API format can answer about it.

    A protocol rather than a base class because there is no shared implementation to inherit: the
    arms have a client each, of unrelated types, and everything they do is the part that differs.
    """

    def model(self, name: str) -> Model:
        """The named model over this wire's own client, so every model shares one pool."""
        ...

    async def listed(self) -> tuple[Listed, ...]:
        """Whatever the endpoint currently says it serves, in the order it said it."""
        ...


# An id whose last segment says it is an embedding model. The OpenAI list carries no capability to
# ask - an entry there is four fields, none of them about what the model does - so this is a rule
# over names rather than a fact, and it is worth having anyway: an embedding model in a chat picker
# is an option that can only ever fail. The Anthropic list needs none of this, since it does not
# carry them at all.
EMBEDDING: Final = "embedding"


def provider_of(model_id: str, format_name: str) -> str:
    """Whoever made this model, taken from the prefix a gateway puts on it, or the format's own name."""
    prefix, slash, _ = model_id.partition("/")
    return prefix if slash else format_name


def listing_client(provider: AnthropicProvider) -> AsyncAnthropic:
    """
    A provider's client, narrowed to the one kind that has a model list to ask for.

    `AnthropicProvider.client` is a union because the same class also fronts Bedrock, Vertex, and
    Foundry, none of which serve `/v1/models`. Only `build_wire` constructs one here and it
    constructs the direct client every time, so this narrows once at the boundary rather than
    leaving every use to ask again.
    """
    if not isinstance(provider.client, AsyncAnthropic):
        raise TypeError(f"listing models needs a direct Anthropic client, not {type(provider.client).__name__}")
    return provider.client


@dataclass(frozen=True, slots=True)
class AnthropicWire:
    """
    An endpoint spoken to over `/v1/messages`.

    Its list is the narrower and the cleaner of the two: every entry is a chat model, every entry
    carries a name written for a person, and there are no aliases to collapse. On exe.dev it is
    also not only Claude, because the gateway translates - Fireworks models answer here too.
    """

    sdk: AnthropicProvider

    def model(self, name: str) -> Model:
        return AnthropicModel(name, provider=self.sdk)

    async def listed(self) -> tuple[Listed, ...]:
        # `limit` is the page size, and one page is asked for rather than paginated: a gateway
        # listing more than a thousand chat models is a different problem than this one.
        page = await listing_client(self.sdk).models.list(limit=1000)
        return tuple(anthropic_listed(found) for found in page.data)


def anthropic_listed(found: ModelInfo) -> Listed:
    """One entry of an Anthropic-wire listing, as the two names and the group it belongs to."""
    passed = passed_through(found.model_extra or {})
    return Listed(
        id=found.id,
        label=found.display_name or passed.label or found.id,
        provider=provider_of(found.id, "anthropic"),
        upstream=passed.upstream,
    )


@dataclass(frozen=True, slots=True)
class PassedThrough:
    """
    How a gateway names a model it forwarded from the service actually serving it.

    Two fields, and both are identity: what that service calls the model, and what it calls it in
    front of a person. Everything else in a forwarded record - the context length, the description,
    the capability flags - is deliberately left unread, because those are the facts `reference.py`
    answers for every model alike and reading some of them here is what would make one card
    disagree with the next.

    A value of its own because these keys are neither wire's: they belong to the upstream record, so
    they arrive in the extras of whichever wire happened to ask and are read the same way on both.
    Anything absent or of an unexpected type is simply not here, since no SDK validated any of it.
    """

    upstream: str | None = None
    label: str | None = None


def passed_through(extra: Mapping[str, object]) -> PassedThrough:
    """The two upstream names, taken out of a forwarded record and type-checked."""
    upstream = extra.get("name")
    label = extra.get("displayName")
    return PassedThrough(
        upstream=upstream if isinstance(upstream, str) and upstream else None,
        label=label.strip() if isinstance(label, str) and label.strip() else None,
    )


@dataclass(frozen=True, slots=True)
class OpenAIWire:
    """
    An endpoint spoken to over `/v1/chat/completions`.

    Its list needs two things thrown out before it is a picker. exe.dev publishes every OpenAI
    model twice, once bare and once prefixed, so a bare id that some prefixed id ends with is the
    same model named again; and it publishes embedding models, which this console can do nothing
    with. Neither is a malformed entry, so neither is refused: what is happening is a general
    catalogue being read by something that only wants the chat half of it.
    """

    sdk: OpenAIProvider

    def model(self, name: str) -> Model:
        return OpenAIChatModel(name, provider=self.sdk)

    async def listed(self) -> tuple[Listed, ...]:
        page = await self.sdk.client.models.list()
        return chat_models(tuple((found.id, found.model_extra or {}) for found in page.data))


def chat_models(every: Sequence[tuple[str, Mapping[str, object]]]) -> tuple[Listed, ...]:
    """
    An OpenAI-compatible list as the models a chat console can actually pick from.

    Pure, because it is the half of that wire with a decision in it and the half worth testing
    against a real list: what a client hands back is a page of entries, and everything interesting
    happens after.

    An id with no slash that some prefixed id ends with is the same model published twice, which
    exe.dev does for every OpenAI model. `EMBEDDING` is the other exclusion and the softer one.

    This format declares four fields and none of them describe a model, so a card's facts come from
    the reference database rather than from here. For a model of the format's own vendor the listing
    carries nothing at all, which is the honest state: an id, a provider heading, and whatever the
    database knows. For a reselling entry it also carries the upstream names.

    No `display_name` on this format either, so the id is the label unless the forwarded record
    carries a real name. Grouping by provider is what makes the bare case readable: `gpt-5.5` under
    an "openai" heading needs no more than its id.
    """
    ids = tuple(found for found, _ in every)
    prefixed = {found for found in ids if "/" in found}
    aliased = {found for found in ids if any(other.endswith(f"/{found}") for other in prefixed)}
    return tuple(
        openai_listed(found, passed_through(extra))
        for found, extra in every
        if found not in aliased and EMBEDDING not in found.rpartition("/")[2]
    )


def openai_listed(model_id: str, passed: PassedThrough) -> Listed:
    return Listed(
        id=model_id,
        label=passed.label or model_id,
        provider=provider_of(model_id, "openai"),
        upstream=passed.upstream,
    )


def build_wire(endpoint: Endpoint) -> Wire:
    """
    One declared endpoint as the thing that talks to it.

    `api_key=None` is deliberately passed through rather than dropped, because that is the value
    that leaves an SDK reading its own environment variable: an endpoint naming neither a key nor a
    URL then behaves exactly as a plain `Agent('anthropic:...')` does.
    """
    match endpoint.format:
        case "anthropic":
            return AnthropicWire(AnthropicProvider(api_key=endpoint.key, base_url=endpoint.url))
        case "openai":
            return OpenAIWire(OpenAIProvider(api_key=endpoint.key, base_url=endpoint.url))
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class Wires:
    """
    Every endpoint this process can reach, keyed by the name that declared it.

    A mapping rather than a factory, so what exists is decided once, at startup, from a
    configuration that has already been parsed. An endpoint that is not in it is a question about
    configuration and never a construction to attempt.
    """

    by_endpoint: Mapping[str, Wire]

    def for_endpoint(self, endpoint: str) -> Wire:
        try:
            return self.by_endpoint[endpoint]
        except KeyError:
            raise UnknownChoice(f"no endpoint {endpoint!r} is configured") from None


def build_wires(config: Config) -> Wires:
    """
    A wire per declared endpoint, constructed before anything takes traffic.

    Eager rather than on demand, so an endpoint that cannot be built at all - a malformed URL, a
    credential an SDK refuses - fails at startup naming itself, rather than on whichever session
    first happened to choose it. It is also what makes the first discovery a request over a client
    that already exists.
    """
    return Wires(by_endpoint={name: build_wire(endpoint) for name, endpoint in config.endpoints.items()})


def working_note(workspace: Workspace) -> str:
    """
    What the agent is told about the directory its tools reach, which is where it is and nothing more.

    How to *use* the tools is on the tools, because that is where it stays true: a description of
    the anchor scheme written here would be a second copy of what each tool's own description
    already says, kept in step by hand. What cannot live there is which directory this session got,
    since a toolset is built per session and its own description is not.
    """
    return (
        f"You are working in a git worktree at {workspace.root}. The file tools take paths relative "
        f"to it and reach nothing outside it. Changes you make there are snapshotted automatically; "
        f"you never need to commit, and you should not run git commands to record your work."
    )


def agent_for(wires: Wires, chosen: Choice, instructions: str, workspace: Workspace | None = None) -> Agent[None, str]:
    """
    The agent one session is answered by, built for the pass that is about to run it.

    Built rather than looked up, because the models an endpoint offers are discovered and change
    while this process runs, so a mapping built at startup would be a snapshot going stale. It
    costs a few tens of microseconds against a turn that costs seconds, and the connection pool -
    the part that is genuinely expensive to build - belongs to the endpoint and is not rebuilt.

    The settings come off the choice rather than being passed in, because they are recorded with it
    and are as fixed as it is: a pass that resumed a session at a different effort would continue a
    conversation whose earlier answers were reasoned at another.

    **A session with no workspace gets no file tools at all**, rather than tools that refuse every
    call. A console being used to talk rather than to edit is what this was before there were any
    repositories, and offering a model four tools that cannot work is worse than offering none:
    it spends the description on every request and invites a call that can only fail.
    """
    tools = [] if workspace is None else [file_tools(Files(root=workspace.root))]
    spoken = instructions if workspace is None else f"{instructions}\n\n{working_note(workspace)}"
    return Agent(
        wires.for_endpoint(chosen.endpoint).model(chosen.model),
        name="mainplate",
        instructions=spoken,
        model_settings=chosen.settings,
        capabilities=[StepwiseDurability()],
        toolsets=tools,
    )
