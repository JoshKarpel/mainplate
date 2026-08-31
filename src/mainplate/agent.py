# The agents, one per profile-and-model a configuration offers.
#
# Built once at startup and held for the process, because an agent carries a provider which
# carries an HTTP client with a connection pool: building one per session would open a pool per
# conversation and throw away every warm connection between turns.
#
# No tools yet, deliberately: the thing worth getting right first is that a conversation survives
# the process running it, and a tool call is another effect to record rather than a different kind
# of one. When tools arrive they are a toolset on these agents, and `StepwiseDurability` is where
# the recording of their calls will go.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from mainplate.durability import StepwiseDurability
from mainplate.profiles import Config
from mainplate.profiles import Profile


@dataclass(frozen=True, slots=True)
class Choice:
    """
    Which endpoint and which model a session is on, recorded when it is created and fixed for life.

    Fixed because a conversation that changed model halfway would replay its recorded responses
    from one and continue on another, so what the transcript shows and what the next turn is
    reasoning from would have different authors. Starting a second session is how you change your
    mind, which also keeps the first one readable.
    """

    profile: str
    model: str


class UnknownChoice(LookupError):
    """
    A session names a profile or a model the configuration no longer offers.

    Its own type because the answer is a person's rather than a retry's: the pair was configured
    when the session started, so something was edited or removed out from under it, and the fix is
    to put it back or to start a new session. The console says so on the session's own page rather
    than leaving it to a worker log.
    """


@dataclass(frozen=True, slots=True)
class Agents:
    """
    Every agent this process can answer with, keyed by the choice that selects one.

    A mapping rather than a factory, so what exists is decided once, at startup, from a
    configuration that has already been parsed. A choice that is not in it is a question about
    configuration and never a construction to attempt.
    """

    by_choice: Mapping[Choice, Agent[None, str]]

    def for_choice(self, chosen: Choice) -> Agent[None, str]:
        try:
            return self.by_choice[chosen]
        except KeyError:
            raise UnknownChoice(f"no profile {chosen.profile!r} offering {chosen.model!r} is configured") from None


def build_model(profile: Profile, model: str) -> AnthropicModel:
    """
    One model over one profile's endpoint and credential.

    `api_key=None` is deliberately passed through rather than dropped, because that is the value
    that leaves the SDK reading `ANTHROPIC_API_KEY` for itself: a profile naming neither a key nor
    an endpoint then behaves exactly as a plain `Agent('anthropic:...')` does.
    """
    return AnthropicModel(model, provider=AnthropicProvider(api_key=profile.key, base_url=profile.base_url))


def build_agents(config: Config, instructions: str) -> Agents:
    """
    An agent per pair the configuration offers, constructed before anything takes traffic.

    Eager rather than on demand, so a profile that cannot be built at all (a malformed endpoint, a
    model name the SDK refuses) fails at startup naming itself, rather than on whichever session
    first happened to choose it.
    """
    return Agents(
        by_choice={
            Choice(profile=name, model=model): Agent(
                build_model(profile, model),
                name="mainplate",
                instructions=instructions,
                capabilities=[StepwiseDurability()],
            )
            for name, profile in config.profiles.items()
            for model in profile.models
        }
    )
