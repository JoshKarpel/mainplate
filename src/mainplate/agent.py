# What a profile becomes once it is something you can run: an endpoint per profile, and the agent
# built over one for a session that chose it.
#
# The endpoint is what is built once and held for the process, because it carries a provider which
# carries an HTTP client with a connection pool. The agent is not: every model over one endpoint
# shares that endpoint's client, and an agent costs tens of microseconds against a model call that
# costs seconds, so there is nothing to gain by keeping a mapping of them and something to lose,
# which is that the set of models is discovered and changes while this process runs.
#
# So the split is along what varies. A profile is written down and fixed, and building its endpoint
# eagerly at startup is what makes a malformed `base_url` or a refused credential a failure that
# names itself before anything takes traffic. Which models it serves is discovered, lives in
# `catalogue.py`, and is nobody's business here.
#
# Each wire gets one class holding the two things that wire decides: how to name a model over it,
# and how to ask it what it serves. They are together because they are the same knowledge, and
# because a third wire should be one class rather than an edit in three files.
#
# No tools yet, deliberately: the thing worth getting right first is that a conversation survives
# the process running it, and a tool call is another effect to record rather than a different kind
# of one. When tools arrive they are a toolset on these agents, and `StepwiseDurability` is where
# the recording of their calls will go.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from typing import Protocol
from typing import assert_never

from anthropic import AsyncAnthropic
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.settings import ThinkingLevel

from mainplate.durability import StepwiseDurability
from mainplate.profiles import Config
from mainplate.profiles import Profile


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

    The profile is what carries the wire format, which is why it is recorded rather than looked up:
    a model id can appear behind two wires, and the two serialize a conversation differently, so a
    session that resolved its own wire from a list discovered later could resume in the other one.
    """

    profile: str
    model: str

    repository: str | None = None
    """
    Which repository this session works in, as the id a forge gave it, or nothing for none.

    Fixed for life like the rest, and for a plainer reason than the model is: a conversation is
    *about* the files it is looking at, so one that changed repository halfway would have a
    transcript whose earlier half discusses code the later half cannot see. Forking inherits it
    rather than offering to change it, since re-asking a turn against a different repository is a
    different question wearing the same words.

    An id rather than a path or a URL, because how to reach a repository is a discovery-time fact
    and which repository it is is not. Absent means a session with no files at all, which is what a
    console being used to talk rather than to edit has and what every session had before this.
    """

    thinking: ThinkingLevel | None = None
    """
    How hard to think, or nothing at all to leave the setting off the request.

    Absent is the meaning rather than an omission, exactly as `Profile.base_url` is: a request with
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
    A session names a profile the configuration no longer declares.

    Its own type because the answer is a person's rather than a retry's: the profile was there when
    the session started, so the file was edited, and the fix is to put it back or to start a new
    session. The console says so on the session's own page rather than leaving it to a worker log.

    Only ever about the profile. A *model* an endpoint has stopped listing is not this, because an
    endpoint routes more ids than it advertises: its own refusal is the authoritative answer about
    one, and it arrives on the turn rather than here.
    """


@dataclass(frozen=True, slots=True)
class Listed:
    """
    One model an endpoint says it serves: what a request names it, and what a person reads.

    `family` is what the picker groups by. It is the part of the id before the slash where there is
    one, because a gateway fronting several vendors prefixes them and that prefix is the only thing
    that distinguishes a Claude from a Kimi in a list of seventy. Where there is no slash the
    endpoint fronts one vendor and the wire's own name is the honest answer.
    """

    id: str
    label: str
    family: str


class Endpoint(Protocol):
    """
    One built provider, and the two questions only the wire can answer about it.

    A protocol rather than a base class because there is no shared implementation to inherit: the
    arms have a client each, of unrelated types, and everything they do is the part that differs.
    """

    def model(self, name: str) -> Model:
        """The named model over this endpoint's own client, so every model shares one pool."""
        ...

    async def listed(self) -> tuple[Listed, ...]:
        """Whatever this endpoint currently says it serves, in the order it said it."""
        ...


# An id whose last segment says it is an embedding model. The OpenAI list carries no capability to
# ask - an entry there is four fields, none of them about what the model does - so this is a rule
# over names rather than a fact, and it is worth having anyway: an embedding model in a chat picker
# is an option that can only ever fail. The Anthropic list needs none of this, since it does not
# carry them at all.
EMBEDDING: Final = "embedding"


def family_of(model_id: str, wire: str) -> str:
    prefix, slash, _ = model_id.partition("/")
    return prefix if slash else wire


def listing_client(provider: AnthropicProvider) -> AsyncAnthropic:
    """
    A provider's client, narrowed to the one kind that has a model list to ask for.

    `AnthropicProvider.client` is a union because the same class also fronts Bedrock, Vertex, and
    Foundry, none of which serve `/v1/models`. Only `build_endpoint` constructs one here and it
    constructs the direct client every time, so this narrows once at the boundary rather than
    leaving every use to ask again.
    """
    if not isinstance(provider.client, AsyncAnthropic):
        raise TypeError(f"listing models needs a direct Anthropic client, not {type(provider.client).__name__}")
    return provider.client


@dataclass(frozen=True, slots=True)
class AnthropicEndpoint:
    """
    An endpoint spoken to over `/v1/messages`.

    Its list is the narrower and the cleaner of the two: every entry is a chat model, every entry
    carries a name written for a person, and there are no aliases to collapse. On exe.dev it is
    also not only Claude, because the gateway translates - Fireworks models answer here too.
    """

    provider: AnthropicProvider

    def model(self, name: str) -> Model:
        return AnthropicModel(name, provider=self.provider)

    async def listed(self) -> tuple[Listed, ...]:
        # `limit` is the page size, and one page is asked for rather than paginated: a gateway
        # listing more than a thousand chat models is a different problem than this one.
        page = await listing_client(self.provider).models.list(limit=1000)
        return tuple(
            Listed(id=found.id, label=found.display_name or found.id, family=family_of(found.id, "anthropic"))
            for found in page.data
        )


@dataclass(frozen=True, slots=True)
class OpenAIEndpoint:
    """
    An endpoint spoken to over `/v1/chat/completions`.

    Its list needs two things thrown out before it is a picker. exe.dev publishes every OpenAI
    model twice, once bare and once prefixed, so a bare id that some prefixed id ends with is the
    same model named again; and it publishes embedding models, which this console can do nothing
    with. Neither is a malformed entry, so neither is refused: what is happening is a general
    catalogue being read by something that only wants the chat half of it.
    """

    provider: OpenAIProvider

    def model(self, name: str) -> Model:
        return OpenAIChatModel(name, provider=self.provider)

    async def listed(self) -> tuple[Listed, ...]:
        page = await self.provider.client.models.list()
        return chat_models(tuple(found.id for found in page.data))


def chat_models(every: Sequence[str]) -> tuple[Listed, ...]:
    """
    An OpenAI-compatible list as the models a chat console can actually pick from.

    Pure, because it is the half of that wire with a decision in it and the half worth testing
    against a real list: what a client hands back is a page of ids, and everything interesting
    happens after.

    An id with no slash that some prefixed id ends with is the same model published twice, which
    exe.dev does for every OpenAI model. `EMBEDDING` is the other exclusion and the softer one.

    No `display_name` on this wire, so the id is the label. Grouping by family is what makes that
    readable: `gpt-5.5` under an "openai" heading needs no more than its id.
    """
    prefixed = {found for found in every if "/" in found}
    aliased = {found for found in every if any(other.endswith(f"/{found}") for other in prefixed)}
    return tuple(
        Listed(id=found, label=found, family=family_of(found, "openai"))
        for found in every
        if found not in aliased and EMBEDDING not in found.rpartition("/")[2]
    )


def build_endpoint(profile: Profile) -> Endpoint:
    """
    One profile as the thing that talks to it.

    `api_key=None` is deliberately passed through rather than dropped, because that is the value
    that leaves an SDK reading its own environment variable: a profile naming neither a key nor an
    endpoint then behaves exactly as a plain `Agent('anthropic:...')` does.
    """
    match profile.provider:
        case "anthropic":
            return AnthropicEndpoint(AnthropicProvider(api_key=profile.key, base_url=profile.base_url))
        case "openai":
            return OpenAIEndpoint(OpenAIProvider(api_key=profile.key, base_url=profile.base_url))
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class Endpoints:
    """
    Every endpoint this process can reach, keyed by the profile that named it.

    A mapping rather than a factory, so what exists is decided once, at startup, from a
    configuration that has already been parsed. A profile that is not in it is a question about
    configuration and never a construction to attempt.
    """

    by_profile: Mapping[str, Endpoint]

    def for_profile(self, profile: str) -> Endpoint:
        try:
            return self.by_profile[profile]
        except KeyError:
            raise UnknownChoice(f"no profile {profile!r} is configured") from None


def build_endpoints(config: Config) -> Endpoints:
    """
    An endpoint per profile, constructed before anything takes traffic.

    Eager rather than on demand, so a profile that cannot be built at all - a malformed endpoint, a
    credential an SDK refuses - fails at startup naming itself, rather than on whichever session
    first happened to choose it. It is also what makes the first discovery a request over a client
    that already exists.
    """
    return Endpoints(by_profile={name: build_endpoint(profile) for name, profile in config.profiles.items()})


def agent_for(endpoints: Endpoints, chosen: Choice, instructions: str) -> Agent[None, str]:
    """
    The agent one session is answered by, built for the pass that is about to run it.

    Built rather than looked up, because the models an endpoint offers are discovered and change
    while this process runs, so a mapping built at startup would be a snapshot going stale. It
    costs a few tens of microseconds against a turn that costs seconds, and the connection pool -
    the part that is genuinely expensive to build - belongs to the endpoint and is not rebuilt.

    The settings come off the choice rather than being passed in, because they are recorded with it
    and are as fixed as it is: a pass that resumed a session at a different effort would continue a
    conversation whose earlier answers were reasoned at another.
    """
    return Agent(
        endpoints.for_profile(chosen.profile).model(chosen.model),
        name="mainplate",
        instructions=instructions,
        model_settings=chosen.settings,
        capabilities=[StepwiseDurability()],
    )
