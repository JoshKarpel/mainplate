from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import pytest
from conftest import CATALOGUE
from conftest import CONFIG
from conftest import OFFERED
from pydantic_ai.models.function import FunctionModel
from without_async import background_task

from mainplate.agent import EMBEDDING
from mainplate.agent import AnthropicEndpoint
from mainplate.agent import Choice
from mainplate.agent import Endpoints
from mainplate.agent import Listed
from mainplate.agent import OpenAIEndpoint
from mainplate.agent import UnknownChoice
from mainplate.agent import agent_for
from mainplate.agent import build_endpoint
from mainplate.agent import build_endpoints
from mainplate.agent import chat_models
from mainplate.agent import family_of
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import NothingOffered
from mainplate.catalogue import default_choice
from mainplate.catalogue import discover
from mainplate.catalogue import grouped
from mainplate.catalogue import refreshing
from mainplate.catalogue import summarise
from mainplate.profiles import Config
from mainplate.profiles import Profile


@dataclass(frozen=True, slots=True)
class Says:
    """An endpoint that answers with whatever it was built holding, or raises what it was given."""

    offers: tuple[Listed, ...] = ()
    refusing: Exception | None = None

    def model(self, name: str) -> FunctionModel:  # pragma: no cover - nothing here runs an agent
        raise NotImplementedError

    async def listed(self) -> tuple[Listed, ...]:
        if self.refusing is not None:
            raise self.refusing
        return self.offers


ONE = Listed(id="vendor/quick", label="Quick", family="vendor")
TWO = Listed(id="vendor/thorough", label="Thorough", family="vendor")


class TestReadingAWiresModelList:
    def test_a_prefixed_id_is_grouped_under_its_prefix(self) -> None:
        """A gateway fronting several vendors prefixes them, and that prefix is all there is to go on."""
        assert family_of("fireworks/kimi-k3", "anthropic") == "fireworks"

    def test_an_unprefixed_id_falls_to_the_wire_that_listed_it(self) -> None:
        """A provider's own endpoint fronts one vendor, so the wire's name is the honest heading."""
        assert family_of("claude-opus-5", "anthropic") == "anthropic"


class TestTakingTheChatHalfOfAnOpenAiList:
    """
    Against the shapes a live exe.dev gateway actually publishes, which is where these rules
    came from: every OpenAI model twice, embedding models mixed in with chat ones, and Fireworks
    and xAI reachable only here.
    """

    LIVE = (
        "openai/gpt-5.6-sol",
        "gpt-5.6-sol",
        "fireworks/kimi-k3",
        "xai/grok-4.6",
        "openai/text-embedding-3-large",
        "text-embedding-3-large",
        "fireworks/qwen3-embedding-8b",
    )

    def test_a_bare_id_republished_under_a_prefix_is_listed_once(self) -> None:
        assert [found.id for found in chat_models(self.LIVE)] == [
            "openai/gpt-5.6-sol",
            "fireworks/kimi-k3",
            "xai/grok-4.6",
        ]

    def test_an_embedding_model_is_not_something_a_chat_console_can_pick(self) -> None:
        """It is not a malformed entry, so it is passed over: a general catalogue read for one use."""
        assert not [found for found in chat_models(self.LIVE) if EMBEDDING in found.id]

    def test_a_bare_id_nothing_republished_survives(self) -> None:
        """The rule is "the same model twice", not "anything unprefixed", which would empty a direct list."""
        assert [found.id for found in chat_models(("gpt-5.5", "o3"))] == ["gpt-5.5", "o3"]

    def test_each_vendor_behind_the_gateway_becomes_its_own_family(self) -> None:
        assert [found.family for found in chat_models(self.LIVE)] == ["openai", "fireworks", "xai"]


class TestBuildingEndpoints:
    @pytest.mark.parametrize(
        ("wire", "built"),
        [("anthropic", AnthropicEndpoint), ("openai", OpenAIEndpoint)],
    )
    def test_the_declared_wire_decides_what_talks_to_the_endpoint(self, wire: str, built: type) -> None:
        profile = Profile.model_validate({"provider": wire, "base_url": "https://gateway.example.invalid/v1"})
        assert isinstance(build_endpoint(profile), built)

    def test_every_profile_gets_one_before_anything_takes_traffic(self) -> None:
        """Eager, so a credential an SDK refuses names its own profile instead of a later session."""
        assert sorted(build_endpoints(CONFIG).by_profile) == ["gateway", "here"]

    def test_a_profile_nothing_declared_is_a_question_and_not_a_construction(self) -> None:
        endpoints = build_endpoints(CONFIG)
        with pytest.raises(UnknownChoice, match="elsewhere"):
            agent_for(endpoints, Choice(profile="elsewhere", model="anything"), "be terse")


class TestDiscovering:
    async def test_every_profile_is_asked_and_what_it_says_is_what_is_offered(self) -> None:
        endpoints = Endpoints(by_profile={"here": Says(offers=(ONE, TWO)), "gateway": Says(offers=(TWO,))})
        found = await discover(endpoints, CONFIG)
        assert found.offered == {"here": (ONE, TWO), "gateway": (TWO,)}

    async def test_an_endpoint_that_cannot_be_asked_is_a_startup_failure_naming_it(self) -> None:
        """There is nothing to fall back on at the first read, so a hole is louder than an empty list."""
        endpoints = Endpoints(
            by_profile={"here": Says(offers=(ONE,)), "gateway": Says(refusing=OSError("no route to host"))}
        )
        with pytest.raises(NothingOffered, match="gateway"):
            await discover(endpoints, CONFIG)

    async def test_an_endpoint_serving_nothing_is_refused_rather_than_offered_empty(self) -> None:
        """A profile you can select and then cannot use is the state hardest to diagnose from a page."""
        endpoints = Endpoints(by_profile={"here": Says(offers=(ONE,)), "gateway": Says(offers=())})
        with pytest.raises(NothingOffered, match="serves no models"):
            await discover(endpoints, CONFIG)


@dataclass(slots=True)
class Rounds:
    """
    An endpoint that answers the same way every round and announces when each round begins.

    The announcement is what makes a test of the refresher deterministic without waiting on a
    clock: a round is known to have been *applied* once the round after it has started, because
    the refresher applies its result and only then goes back to sleep. Every round answering
    identically is what makes that enough, since a round the test did not wait for can only
    reach the same conclusion as the one it did.
    """

    starting: dict[int, asyncio.Event]
    answering: tuple[Listed, ...] | Exception
    rounds: int = 0

    def model(self, name: str) -> FunctionModel:  # pragma: no cover - nothing here runs an agent
        raise NotImplementedError

    async def listed(self) -> tuple[Listed, ...]:
        self.rounds += 1
        self.starting.setdefault(self.rounds, asyncio.Event()).set()
        if isinstance(self.answering, Exception):
            raise self.answering
        return self.answering


# Short enough that the test never waits on it, since every wait below is on a round beginning
# rather than on the clock.
BRIEF = timedelta(seconds=0.001)

# Long enough that a round firing inside one is the refresher not sleeping first, rather than a
# loaded machine.
PATIENT = timedelta(minutes=30)


async def until_round(endpoint: Rounds, number: int) -> None:
    """Wait for a given round to begin, which is the signal that the one before it was applied."""
    await endpoint.starting.setdefault(number, asyncio.Event()).wait()


class TestKeepingTheCatalogueCurrent:
    async def test_a_round_that_answers_replaces_what_the_holder_carries(self) -> None:
        """A model that appears at the gateway reaches the picker without anybody restarting."""
        endpoint = Rounds(starting={}, answering=(ONE, TWO))
        holder = Catalogues(current=Catalogue(offered={"here": (ONE,)}, default=Choice(profile="here", model=ONE.id)))
        async with background_task(refreshing(holder, Endpoints({"here": endpoint}), CONFIG, BRIEF)):
            await until_round(endpoint, 2)
            assert holder.current.offered == {"here": (ONE, TWO)}

    async def test_a_round_that_fails_keeps_the_models_discovered_earlier(self) -> None:
        """
        The previous catalogue still answers the question that was asked, only less recently.

        There is no staleness bound past which it gives up, deliberately: emptying the picker
        because a gateway was unreachable is worse than the staleness that would prevent.
        """
        was = Catalogue(offered={"here": (ONE,)}, default=Choice(profile="here", model=ONE.id))
        endpoint = Rounds(starting={}, answering=OSError("no route to host"))
        holder = Catalogues(current=was)
        async with background_task(refreshing(holder, Endpoints({"here": endpoint}), CONFIG, BRIEF)):
            await until_round(endpoint, 2)
            assert holder.current is was

    async def test_nothing_is_replaced_before_a_round_has_finished(self) -> None:
        """
        Whoever filled the holder keeps answering until a round has something better to say.

        Deterministic because the endpoint sets its event *before* it answers: reaching this
        assertion means round 1 is still in flight, so anything the holder carries now was put
        there by the caller.
        """
        endpoint = Rounds(starting={}, answering=(ONE, TWO))
        holder = Catalogues(current=CATALOGUE)
        async with background_task(refreshing(holder, Endpoints({"here": endpoint}), CONFIG, BRIEF)):
            await until_round(endpoint, 1)
            assert holder.current is CATALOGUE


class TestTheDefaultChoice:
    def test_a_named_model_is_used_when_the_endpoint_still_has_it(self) -> None:
        named = Config(default="here", default_model=TWO.id, profiles=CONFIG.profiles)
        assert default_choice({"here": (ONE, TWO)}, named) == Choice(profile="here", model=TWO.id)

    def test_a_name_the_endpoint_dropped_falls_to_the_first_it_listed(self) -> None:
        """A model retired overnight must not leave a picker whose selected option does not exist."""
        stale = Config(default="here", default_model="vendor/withdrawn", profiles=CONFIG.profiles)
        assert default_choice({"here": (ONE, TWO)}, stale) == Choice(profile="here", model=ONE.id)

    def test_naming_none_takes_whatever_came_first(self) -> None:
        assert default_choice({"here": (TWO, ONE)}, CONFIG) == Choice(profile="here", model=TWO.id)


class TestWhatACatalogueOffers:
    def test_a_pair_that_was_discovered_is_offered(self) -> None:
        assert CATALOGUE.offers("here", OFFERED["here"][1].id) is True

    @pytest.mark.parametrize(
        ("profile", "model"),
        [("gateway", "ripe/careful"), ("here", "vendor/quick"), ("gone", "ripe/fast")],
    )
    def test_a_pair_from_another_profile_or_no_profile_is_not(self, profile: str, model: str) -> None:
        """A session records a pair, so what is checked later is the pair and never the halves."""
        assert CATALOGUE.offers(profile, model) is False

    def test_a_profile_nothing_offers_has_no_models_rather_than_none(self) -> None:
        assert CATALOGUE.models_of("gone") is None


class TestGroupingForAPicker:
    def test_models_of_one_family_stay_in_the_order_the_endpoint_gave(self) -> None:
        """A gateway lists its newest first, and no ordering imposed here would know that."""
        assert grouped((TWO, ONE)) == (("vendor", (TWO, ONE)),)

    def test_families_appear_in_the_order_each_was_first_seen(self) -> None:
        other = Listed(id="second/plain", label="Plain", family="second")
        assert [family for family, _ in grouped((ONE, other, TWO))] == ["vendor", "second"]


class TestSummarising:
    def test_one_line_names_each_profile_and_how_much_it_offers(self) -> None:
        """What a log is read for the morning after somebody asks where a model went."""
        one = Catalogue(offered={"here": (ONE, TWO)}, default=Choice(profile="here", model=ONE.id))
        assert summarise(one) == "here offers 2"
