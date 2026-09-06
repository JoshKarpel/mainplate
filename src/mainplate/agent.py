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
# The tools hang off the agent as toolsets, and they hang off it *per session* rather than once for
# the process, because what makes a path safe is the root it is resolved inside and every session
# picks its own. **Which tools a session gets is decided by its `isolation`**, not by what this
# module happens to be handed: a worktree gets the file tools over it and its scratch, the whole
# machine gets them over `/`, and reaching nothing gets no toolset at all - a console used to talk
# rather than to edit is what this was before there were repositories, and tools that can only fail
# are worse than none. `StepwiseDurability` is what records the calls, on the same capability that
# already records the model requests.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Final
from typing import Protocol
from typing import assert_never

from anthropic import AsyncAnthropic
from anthropic.types import ModelInfo
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.settings import ThinkingLevel

from mainplate.config import Config
from mainplate.config import Endpoint
from mainplate.durability import StepwiseDurability
from mainplate.roots import environment_named
from mainplate.sandbox import Confinement
from mainplate.sandbox import Filesystem
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Isolation
from mainplate.sandbox import OverEverything
from mainplate.snapshots import Worktree
from mainplate.snapshots import branch_named
from mainplate.tools import Files
from mainplate.tools import GitTracked
from mainplate.tools import Handing
from mainplate.tools import Scratch
from mainplate.tools import System
from mainplate.tools import bash_tools
from mainplate.tools import file_tools
from mainplate.tools import handoff_tools
from mainplate.tools.files.tools import Root


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

    base: str | None = None
    """
    What the worktree is checked out at when this session's files are first planted, or nothing at
    all to begin where the repository is.

    A **commit-ish** and not a commit: a branch name, a tag, a hash, or anything else `git rev-parse`
    resolves. It is recorded as the words somebody typed rather than as what they resolved to, and
    that is deliberate - what a session says about itself is the answer it was given, and `main` is a
    truer record of that intent than the hash `main` happened to be at that minute. The hash it came
    to is in `turn:0:tree:0` for anybody who wants it.

    Fixed for the session's life like everything else here, and it stops mattering after the first
    pass: a worktree is planted once, so this is read by exactly one call and is thereafter a fact
    about where the session began. A fork does not carry it, because a fork plants at a recorded tree
    and a base beside that would be two claims about one checkout.
    """

    branch: str | None = None
    """
    A branch to start at `base` and leave the worktree on.

    Optional on the *form* and not in the record: `Choice.branching` fills it with a name from the
    session's id wherever a repository was picked, so every session working in one is on a branch.
    `None` here therefore means a session with no repository, or a checkpoint written before this
    existed. What it buys is somewhere for a commit to go, since a commit on a detached `HEAD` is
    reachable only through the reflog and `Run` is what made committing easy.

    **Naming a base does not put the worktree on that branch, and cannot.** Git refuses to check out
    a branch that another worktree already holds, so two sessions started at `main` would mean the
    second one failing to plant at all - and a session's whole shape here is that it gets a worktree
    of its own. So a base names *where to begin* and this names *what to begin*, which is why they
    are two fields rather than one that sometimes means both.

    Not carried by a fork, and that is a refusal rather than an oversight: `git worktree add -b`
    takes a branch name that is not already in use, so a fork inheriting one could not be planted at
    all. Two sessions on one branch would be two writers in one history besides, which is the thing a
    worktree apiece exists to prevent. A fork is given one of its **own** instead, by the same call.
    """

    isolation: Isolation = field(default_factory=Isolation)
    """
    How confined this session is: what its tools may reach, and whether they may dial out.

    Not free of `repository` on the filesystem axis: a session that picked one reaches its worktree
    and can reach nothing else, and a session that picked none cannot reach a worktree there is none
    of. `Isolation.settled` is what makes that true, applied by `Service.start` and `Service.fork`
    rather than trusted from a form - the same stance that stops a form with no repository field
    moving a branch out of its repository. So the pair can never be recorded contradicting itself and
    nothing downstream reconciles anything.

    The network is off by default, and off rather than allowlisted. An allowlist containing a code
    forge contains every gist on it, one containing a package registry contains a package anybody can
    publish, and a DNS query carries whatever you like out through any resolver that is permitted.
    What it would cost is a proxy in front of every command and a certificate authority inside the
    sandbox; what it would buy is a defence against a repository's own build script and very little
    against anything deliberate.

    Defaulted to reaching nothing with no network, because that is what every session had before
    this existed and what a checkpoint written then must keep reading back as.
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

    def branching(self, session: str) -> Choice:
        """
        The same choice with a branch of its own, where a repository was picked and nobody named one.

        Every session working in a repository gets one, because the alternative is a detached `HEAD`
        and committing is something somebody does here now: `Run` puts `git commit` in the box under
        the conversation, and a commit on a detached `HEAD` is reachable only through the reflog.

        Taken rather than derived, so a name somebody typed always wins. It is the session's id that
        makes the generated one usable at all - see `branch_named` - which is why this takes one and
        why it is not part of `settled`, whose whole subject is the choice on its own.
        """
        if self.repository is None or self.branch is not None:
            return self
        return replace(self, branch=branch_named(session))

    def settled(self, *, forked: bool = False) -> Choice:
        """
        The same choice with everything that depends on the repository made to agree with it.

        **One place that makes a posted choice self-consistent**, rather than a rule per field spread
        over the two callers. A form is not the only way in - a fork inherits its repository rather
        than posting one - so the alternative to settling here is every writer reconciling the same
        three fields and one of them eventually not.

        With no repository there is no worktree, so there is nothing to reach, nothing to check out
        and no branch to start: all three collapse together because they are answers to one question
        the picker asks once. The page draws no base and no branch until a repository is picked, so a
        form cannot express the contradiction in the first place; this is what says the same of every
        other way in, a fork and `scripts/seed.py` alike.

        `forked` drops the base and the branch whatever the repository is. A fork plants at the tree
        of the turn it re-asks, so a base would be a second answer to where its files come from, and
        a branch would be a name `git worktree add -b` refuses because the parent already holds it.
        """
        if forked or self.repository is None:
            return replace(
                self,
                base=None,
                branch=None,
                isolation=self.isolation.settled(self.repository),
            )
        return replace(self, isolation=self.isolation.settled(self.repository))

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
    One built SDK client, and the three questions only its API format can answer about it.

    A protocol rather than a base class because there is no shared implementation to inherit: the
    arms have a client each, of unrelated types, and everything they do is the part that differs.
    """

    def model(self, name: str) -> Model:
        """The named model over this wire's own client, so every model shares one pool."""
        ...

    async def listed(self) -> tuple[Listed, ...]:
        """Whatever the endpoint currently says it serves, in the order it said it."""
        ...

    def caching(self) -> ModelSettings:
        """
        What this format has to be told to reuse a conversation's prefix, which for one of them is nothing.

        The third format-specific thing, and it belongs here for the reason the other two do: whether
        caching is opt-in is a fact about an API rather than about a session, and a `Choice` has no
        way to know which wire will answer it.

        **It is opt-in on the Anthropic wire and automatic on the OpenAI one, and getting that wrong
        costs real money on every request.** A conversation is re-sent whole each turn, so a session
        with no breakpoint pays full input price for everything said so far, over and over: on a long
        turn that is most of the bill. Nothing about the request looks different, which is why this
        is a method somebody has to answer rather than a setting somebody might forget.
        """
        ...


# An id whose last segment says it is an embedding model. The OpenAI list carries no capability to
# ask - an entry there is four fields, none of them about what the model does - so this is a rule
# over names rather than a fact, and it is worth having anyway: an embedding model in a chat picker
# is an option that can only ever fail. The Anthropic list needs none of this, since it does not
# carry them at all.
EMBEDDING: Final = "embedding"

RETENTION: Final = timedelta(hours=1)
"""
How long a cached prefix is kept where the format lets this console ask.

An hour rather than the five minutes that is the default, and the trade is stated because it is a
real one: an hour's retention is written at 2x base input against 1.25x, so it pays only where a
conversation is picked up again after a pause. That is what a chat console is - somebody reads an
answer, thinks, and replies - and five minutes barely outlasts one long turn, let alone the walk to
the kettle.

A duration rather than the string the wire takes, because two things read it: the parameter below,
and the page saying whether the next request will pay full price. It is **the longest retention this
console asks for on any wire**, which is what makes it usable as one threshold everywhere - past it
a prefix is cold whatever answered the conversation, where under it nothing can be asserted at all.
"""

CACHE_FOR: Final = "1h"
"""
The same duration as the parameter the Anthropic wire takes, in the vocabulary that wire accepts.

Written out rather than rendered from `RETENTION`, because the SDK types this field as
`Literal['5m', '1h'] | bool` and a derived string is a `str`: deriving it would trade a checked value
for an unchecked one to save a line. So the two are one fact in two places with nothing enforcing the
agreement, which is the bargain `tree_key` and `Stepping.key` already take, and
`test_the_retention_and_the_wire_parameter_are_one_duration` is what turns a drift into a failure.
"""


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

    def caching(self) -> ModelSettings:
        """
        A top-level `cache_control`, which is the one that moves its breakpoint forward as a turn grows.

        The alternative Pydantic AI offers is per-block breakpoints on the instructions, the tool
        definitions and the last message. Those are for a gateway that takes the Anthropic message
        format without the automatic parameter; asked for here they would pin breakpoints this
        console would then have to move itself, which is the server's job and it does it better.

        `CACHE_FOR` rather than the default five minutes, and that is a bet worth stating: an hour's
        retention is written at 2x base input against 1.25x, so it pays only where a conversation is
        picked up again after a pause. That is what a chat console *is* - somebody reads an answer,
        thinks, and replies - where five minutes barely outlasts a single long turn.
        """
        return AnthropicModelSettings(anthropic_cache=CACHE_FOR)


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

    def caching(self) -> ModelSettings:
        """
        Nothing, because this format caches a repeated prefix without being asked and cannot be told to.

        An empty answer rather than an absent method: what has to be true is that every wire answers
        the question, so that a format added later is a `caching` somebody had to write rather than a
        session quietly paying full price on every request. The retention is the provider's and is
        neither documented nor controllable, which is why the console's own reading of whether a
        prefix is still warm can only ever be one-sided here.
        """
        return ModelSettings()


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


def working_note(scratch: bool) -> str:
    """
    What the agent is told about the places its tools reach, by name and never by path.

    How to *use* the tools is on the tools, because that is where it stays true: a description of
    the anchor scheme written here would be a second copy of what each tool's own description
    already says, kept in step by hand. What cannot live there is which places this session got,
    since a toolset is built per session and its own description is not.

    **No absolute path appears here, and both halves of that are decided.** It is what `roots.py`
    exists for: a worktree sits under 32 hex characters of session id, and a model reproducing those
    from memory eventually reproduces them wrong, so printing the path invites exactly the failure
    the root names were built to prevent - and a relative path already lands in the worktree, so
    there was never anything to do with it.

    The second half is the cache. Instructions are a per-request parameter Pydantic AI renders in
    front of the whole cached prefix, so a sentence naming one session's directories makes that
    session's prefix unlike every other's. With the paths out, this is a pure function of the
    isolation: two sessions of the same shape compose the same string, and a fork's first request
    reads its parent's prefix from cache instead of paying for the whole conversation again.
    """
    said = (
        "You are working in a git worktree, which is called `worktree`. The file tools take paths "
        "relative to it and reach nothing outside it. Changes you make there are snapshotted "
        "automatically; you never need to commit, and you should not run git commands to record "
        "your work."
    )
    if not scratch:
        return said
    # The names and the policy both, because both are this session's rather than the tool's. A
    # `bash` description cannot carry either: one toolset is built per session and its tools'
    # descriptions are not, so what varies between sessions has to be said here.
    #
    # Where a command *starts* is said for a different reason: the tool's own description says a
    # `cd` does not survive to the next call, which on its own reads as an instruction to put one at
    # the front of every command. `--chdir` has already done it.
    return (
        f"{said} You also have a scratch directory called `scratch`, outside the worktree and "
        f"outside every snapshot, which is where anything that is not the repository's belongs. "
        f'Reach it by passing `root: "scratch"` to `read`, `edit` or `create`; in a command it is '
        f"`${environment_named('scratch')}`, and the worktree is `${environment_named('worktree')}`. "
        f"Commands you run start in the worktree, so a relative path means the same thing there as "
        f"it does to the file tools and you never need to `cd` into it. They reach those two "
        f"directories and a read-only system, and nothing else: no home directory, no other "
        f"session's files, and no configuration of the console itself. Git can be read but not "
        f"written there, so `status`, `diff`, `log` and `blame` answer while `add`, `commit` and "
        f"`stash` fail."
    )


def network_note(reachable: bool) -> str:
    """
    Whether commands can dial out, which is this session's setting rather than the tool's.

    Said either way rather than only when it is off. "There is no network" stops a model wasting
    a turn on a fetch that cannot work; "there is a network" stops one refusing to try.
    """
    if reachable:
        return "Commands you run can reach the network."
    return (
        "Commands you run cannot reach the network: no fetching, no installing, no cloning. "
        "Something that needs one fails rather than hanging."
    )


def whole_machine_note() -> str:
    """
    What a session reaching everything is told, which is the shape of what it has rather than a path.

    No root to name, because the root is `/` and saying so tells a model nothing it cannot see. What
    it cannot see is that this was *chosen*, and that nothing here is snapshotted: a session on this
    arm has no worktree, so the record of what it did is the conversation and nothing else.
    """
    return (
        "You are working on this machine directly, with no repository and no worktree. Paths are "
        "absolute and reach the whole filesystem. Nothing you change is snapshotted, so there is no "
        "going back to before a change through this console; say what you are about to do to "
        "anything you cannot undo."
    )


@dataclass(frozen=True, slots=True)
class Reach:
    """
    What a session's isolation comes to: the places it reaches, and what it is told about them.

    One value because it is one decision read by two callers. `agent_for` builds the toolsets from
    the roots and the confinement; `conversing` composes the note into the instructions it records.
    Decided in two `match` statements those would be two places to keep in step over one answer, and
    the failure would be quiet - a session told it has a scratch directory whose tools cannot reach
    one, or told there is no network by a command that can dial out.

    Empty on every arm that affords nothing, so a caller asks what it has rather than which arm it
    landed on: no roots is no toolset, no confinement is no `bash`, and no note is nothing to say.
    """

    roots: tuple[Root, ...] = ()
    confinement: Confinement | None = None
    note: str = ""


def reaching(
    isolation: Isolation,
    worktree: Worktree | None = None,
    scratch: Path | None = None,
    bwrap: str | None = None,
) -> Reach:
    """
    What one session's isolation affords, given the worktree and the sandbox this machine has.

    **Decided by the session's own isolation**, not by what a caller happens to be handed.
    `Filesystem.NOTHING` reaches nothing rather than reaching a place its tools would refuse: a
    console being used to talk rather than to edit is what this was before there were repositories,
    and offering a model tools that cannot work is worse than offering none, since it spends the
    description on every request and invites a call that can only fail.

    `bwrap` is passed in rather than looked up, because where the sandbox binary is is a fact about
    the machine. Without it a `WORKTREE` session keeps its file tools and is offered no `bash`, which
    is what this console was before there was one, and an `EVERYTHING` session reaches nothing at
    all: what that arm *is* is a sandbox with `/` in it, so without one there is nothing left that
    anybody chose.
    """
    match isolation.filesystem:
        case Filesystem.NOTHING:
            return Reach()
        case Filesystem.WORKTREE if worktree is not None:
            # The scratch is reachable by the file tools only where a command can make it exist,
            # which is the same condition `bash` is offered under. Offered without one, `read` would
            # name a directory nothing ever creates. The worktree is first, so a relative path still
            # means the repository however many roots a session ends up with.
            if scratch is None or bwrap is None:
                return Reach(roots=(GitTracked(path=worktree.root),), note=working_note(scratch=False))
            return Reach(
                roots=(GitTracked(path=worktree.root), Scratch(path=scratch)),
                confinement=InAWorktree(worktree=worktree, scratch=scratch),
                note=f"{working_note(scratch=True)}\n\n{network_note(isolation.network)}",
            )
        case Filesystem.WORKTREE:
            # A worktree was chosen and none was supplied, which is the instant before a session's
            # first pass has planted one. Nothing rather than tools rooted nowhere.
            return Reach()
        case Filesystem.EVERYTHING if bwrap is not None:
            return Reach(
                roots=(System(path=Path("/")),),
                confinement=OverEverything(),
                note=f"{whole_machine_note()}\n\n{network_note(isolation.network)}",
            )
        case Filesystem.EVERYTHING:
            return Reach()
        case _ as unreachable:
            assert_never(unreachable)


def agent_for(
    wires: Wires,
    chosen: Choice,
    instructions: str,
    worktree: Worktree | None = None,
    scratch: Path | None = None,
    bwrap: str | None = None,
    handing: Handing | None = None,
) -> Agent[None, str]:
    """
    The agent one session is answered by, built for the pass that is about to run it.

    Built rather than looked up, because the models an endpoint offers are discovered and change
    while this process runs, so a mapping built at startup would be a snapshot going stale. It
    costs a few tens of microseconds against a turn that costs seconds, and the connection pool -
    the part that is genuinely expensive to build - belongs to the endpoint and is not rebuilt.

    The settings come off the choice rather than being passed in, because they are recorded with it
    and are as fixed as it is: a pass that resumed a session at a different effort would continue a
    conversation whose earlier answers were reasoned at another.

    **The instructions are spoken exactly as they arrive**, and nothing is composed on top of them
    here. What a session is answered under is recorded before its first request, so a note added here
    would be a sentence the model was sent and the record does not hold - which is both a page
    reporting less than was said, and instructions that change under a conversation whose cached
    prefix they sit in front of. `reaching` is where the note comes from, and `conversing` composes
    it into what it records.

    **The handoff tool is not conditioned on the isolation**, unlike the two below it, because what
    it reaches is the conversation rather than the machine: every session has one of those. It is
    absent only where nowhere has been given to put a handoff, which is a bare agent in a script or a
    test and never a pass. That it is in every real session's prefix is the point rather than a
    detail - a tool added later invalidates the whole cached conversation beneath it, since tool
    definitions sit above the system prompt.
    """
    wire = wires.for_endpoint(chosen.endpoint)
    reach = reaching(chosen.isolation, worktree, scratch, bwrap)
    tools = []
    if handing is not None:
        tools.append(handoff_tools(handing))
    if reach.roots:
        tools.append(file_tools(Files(roots=reach.roots)))
    if reach.confinement is not None and bwrap is not None:
        tools.append(bash_tools(reach.confinement, bwrap, chosen.isolation.venue))
    return Agent(
        wire.model(chosen.model),
        name="mainplate",
        instructions=instructions,
        # The session's own settings over the wire's, so a recorded choice always wins: what the two
        # carry does not overlap today, and if it ever does, the thing somebody picked should be the
        # thing that happens.
        model_settings=ModelSettings(**wire.caching(), **(chosen.settings or ModelSettings())),
        capabilities=[StepwiseDurability()],
        toolsets=tools,
    )
