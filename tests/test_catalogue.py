from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest
from anthropic.types import ModelInfo
from conftest import CATALOGUE
from conftest import CONFIG
from conftest import OFFERED
from conftest import Stand
from conftest import Watching
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import DeltaToolCall
from pydantic_ai.models.function import DeltaToolCalls
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.settings import ModelSettings
from without_async import background_task

from mainplate.agent import ANTHROPIC_RETENTION
from mainplate.agent import CACHE_FOR
from mainplate.agent import EMBEDDING
from mainplate.agent import OPENAI_RETENTION
from mainplate.agent import AnthropicWire
from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.agent import OpenAIWire
from mainplate.agent import Streamed
from mainplate.agent import UnknownChoice
from mainplate.agent import Wires
from mainplate.agent import agent_for
from mainplate.agent import anthropic_listed
from mainplate.agent import build_wire
from mainplate.agent import build_wires
from mainplate.agent import chat_models
from mainplate.agent import provider_of
from mainplate.agent import retention_of
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import NothingOffered
from mainplate.catalogue import Offering
from mainplate.catalogue import default_choice
from mainplate.catalogue import discover
from mainplate.catalogue import grouped
from mainplate.catalogue import refreshing
from mainplate.catalogue import retention_for
from mainplate.catalogue import summarise
from mainplate.config import Config
from mainplate.config import Endpoint


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

    def caching(self) -> ModelSettings:  # pragma: no cover - nothing here builds an agent
        return ModelSettings()


ONE = Listed(id="vendor/quick", label="Quick", provider="vendor")
TWO = Listed(id="vendor/thorough", label="Thorough", provider="vendor")


def serving(endpoint: str, *models: Listed) -> Offering:
    """
    One endpoint offering these models, on an endpoint no test in this file asserts about.

    A helper because what nearly every test here is about is the *models*, and spelling an endpoint
    beside each one would put four fields of noise in front of the one that matters.
    """
    return Offering(endpoint=endpoint, format="anthropic", url="https://gateway.invalid", models=models)


class TestReadingAWiresModelList:
    def test_a_prefixed_id_is_grouped_under_its_prefix(self) -> None:
        """A gateway fronting several vendors prefixes them, and that prefix is all there is to go on."""
        assert provider_of("fireworks/kimi-k3", "anthropic") == "fireworks"

    def test_an_unprefixed_id_falls_to_the_wire_that_listed_it(self) -> None:
        """A provider's own endpoint fronts one vendor, so the wire's name is the honest heading."""
        assert provider_of("claude-opus-5", "anthropic") == "anthropic"


class TestTakingTheChatHalfOfAnOpenAiList:
    """
    Against the shapes a live exe.dev gateway actually publishes, which is where these rules
    came from: every OpenAI model twice, embedding models mixed in with chat ones, and Fireworks
    and xAI reachable only here.
    """

    # A model of the wire's own vendor carries nothing describing it; one the gateway resells
    # carries the upstream service's whole record. Both shapes are here because the second is where
    # the two names a model can be looked up under come from.
    LIVE: tuple[tuple[str, dict[str, object]], ...] = (
        ("openai/gpt-5.6-sol", {}),
        ("gpt-5.6-sol", {}),
        (
            "fireworks/kimi-k3",
            {
                "name": "accounts/fireworks/models/kimi-k3",
                "displayName": "Kimi K3",
                "contextLength": 262144,
                "supportsTools": True,
            },
        ),
        ("xai/grok-4.6", {}),
        ("openai/text-embedding-3-large", {}),
        ("text-embedding-3-large", {}),
        ("fireworks/qwen3-embedding-8b", {}),
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
        assert [found.id for found in chat_models((("gpt-5.5", {}), ("o3", {})))] == ["gpt-5.5", "o3"]

    def test_each_vendor_behind_the_gateway_becomes_its_own_family(self) -> None:
        assert [found.provider for found in chat_models(self.LIVE)] == ["openai", "fireworks", "xai"]

    def test_a_resold_models_upstream_name_is_kept_so_it_can_be_looked_up(self) -> None:
        """
        The second of the two names a reference is searched by, and the one that finds most models.

        A gateway routes `fireworks/kimi-k3` and the database of record files it under the name
        Fireworks itself uses, so without this the whole resold half of a catalogue has no record.
        """
        resold = next(found for found in chat_models(self.LIVE) if found.id == "fireworks/kimi-k3")
        assert resold.upstream == "accounts/fireworks/models/kimi-k3"

    def test_a_resold_model_is_named_as_its_own_service_names_it(self) -> None:
        """The OpenAI wire publishes no display name, so a forwarded one beats showing the raw id."""
        resold = next(found for found in chat_models(self.LIVE) if found.id == "fireworks/kimi-k3")
        assert resold.label == "Kimi K3"

    def test_a_model_of_the_wires_own_vendor_is_labelled_by_its_id(self) -> None:
        """Nothing forwarded and no display name on this wire, so the id is the only name there is."""
        own = next(found for found in chat_models(self.LIVE) if found.id == "openai/gpt-5.6-sol")
        assert own.label == "openai/gpt-5.6-sol"
        assert own.upstream is None


class TestBuildingEndpoints:
    @pytest.mark.parametrize(
        ("wire", "built"),
        [("anthropic", AnthropicWire), ("openai", OpenAIWire)],
    )
    def test_the_declared_wire_decides_what_talks_to_the_endpoint(self, wire: str, built: type) -> None:
        endpoint = Endpoint.model_validate({"format": wire, "url": "https://gateway.example.invalid/v1"})
        assert isinstance(build_wire(endpoint), built)

    def test_the_openai_wire_keeps_nothing_at_the_provider_and_asks_what_it_thought(self) -> None:
        """
        The checkpoint is the conversation, said to the one API that offers to hold it instead.

        Pinned on the built model rather than on a request, because the request's shape is Pydantic
        AI's: what this console decides is which API, and that the exchange is not stored, which is
        what server-side chaining would depend on. The two chaining settings are pinned absent, since
        either would have the provider reconstruct the history and this console send only what is new.

        The summary is the other half, and it is here because unasked this API reasons in private: a
        reasoning item comes back as encrypted content and nothing else, which Pydantic AI reads into
        a `ThinkingPart` carrying no text and `blocks_in` passes over, so a session on this wire
        showed none of the thinking a session on the Anthropic one showed throughout.

        The whole set rather than a key apiece, so a third thing said to this wire is a line somebody
        wrote here rather than a setting that arrived with nothing to explain it.
        """
        wire = build_wire(Endpoint.model_validate({"format": "openai", "url": "https://gw.invalid/v1"}))
        built = wire.model("openai/gpt-5.6-sol")

        assert isinstance(built, Streamed)
        assert isinstance(built.wrapped, OpenAIResponsesModel)
        assert built.settings == {"openai_store": False, "openai_reasoning_summary": "auto"}
        assert "openai_previous_response_id" not in (built.settings or {})
        assert "openai_conversation_id" not in (built.settings or {})

    @pytest.mark.parametrize("format_name", ["anthropic", "openai"])
    def test_every_format_asks_for_its_answer_as_a_stream(self, format_name: str) -> None:
        """Pinned per format, because the wrapping is each wire's own line to forget."""
        wire = build_wire(Endpoint.model_validate({"format": format_name, "url": "https://gw.invalid/v1"}))

        assert isinstance(wire.model("whichever"), Streamed)

    def test_every_profile_gets_one_before_anything_takes_traffic(self) -> None:
        """Eager, so a credential an SDK refuses names its own endpoint instead of a later session."""
        assert sorted(build_wires(CONFIG).by_endpoint) == ["gateway", "here"]

    def test_a_profile_nothing_declared_is_a_question_and_not_a_construction(self) -> None:
        endpoints = build_wires(CONFIG)
        with pytest.raises(UnknownChoice, match="elsewhere"):
            agent_for(endpoints, Choice(endpoint="elsewhere", model="anything"), "be terse")

    def test_the_anthropic_wire_asks_for_caching_and_the_openai_one_has_nothing_to_ask(self) -> None:
        """
        Caching is opt-in on one of these formats and automatic on the other, and the difference costs money.

        A conversation is re-sent whole every turn, so a session with no breakpoint pays full input
        price for everything said so far, over and over. Nothing about the request *looks* different
        when it goes missing, which is why the wire answers a question rather than a setting being
        left somewhere it might not be noticed.
        """
        anthropic = build_wire(Endpoint.model_validate({"format": "anthropic", "url": "https://gw.invalid"}))
        openai = build_wire(Endpoint.model_validate({"format": "openai", "url": "https://gw.invalid/v1"}))

        assert anthropic.caching() == {"anthropic_cache": CACHE_FOR}
        assert openai.caching() == {}, "this format caches a repeated prefix without being asked"

    def test_the_retention_and_the_wire_parameter_are_one_duration(self) -> None:
        """
        Two representations of one fact with nothing enforcing the agreement, which is what this is.

        The parameter has to be a literal, because the SDK types the field as `Literal['5m', '1h']`
        and a string rendered from a `timedelta` is a `str`; the duration has to be a `timedelta`,
        because what reads it is the composer deciding whether the next request pays full price. So
        they are written twice, and this is what turns a drift into a failure rather than a console
        confidently calling a dead prefix warm for as long as somebody left the two disagreeing.
        """
        assert f"{ANTHROPIC_RETENTION // timedelta(hours=1)}h" == CACHE_FOR

    def test_each_format_is_believed_for_exactly_as_long_as_it_holds_a_prefix(self) -> None:
        """
        The two durations apart, because one constant for both is what drew an evicted prefix as warm.

        The Anthropic figure is what this console *asks* for and the OpenAI one is what that provider
        publishes and offers no way to change, so they are different kinds of fact that happen to be
        read at the same place. What must not drift is which is which.
        """
        assert retention_of("anthropic") == ANTHROPIC_RETENTION
        assert retention_of("openai") == OPENAI_RETENTION
        assert OPENAI_RETENTION < ANTHROPIC_RETENTION, "the reason one threshold could not serve both"

    def test_the_retention_a_session_is_read_against_comes_off_its_endpoint(self) -> None:
        """
        The format and never the model: every model reached over one wire is cached on that wire's terms.

        `wide/steady` is offered by both fixture endpoints, so a lookup that keyed off the model would
        pass this by accident. Asking for the same id twice, once per endpoint, is what pins that the
        answer follows the endpoint.
        """
        assert retention_for(CATALOGUE, Choice(endpoint="here", model="wide/steady")) == ANTHROPIC_RETENTION
        assert retention_for(CATALOGUE, Choice(endpoint="gateway", model="wide/steady")) == OPENAI_RETENTION

    @pytest.mark.parametrize(
        ("chosen", "why"),
        [
            (None, "a session with no choice recorded has no wire to be read against"),
            (Choice(endpoint="retired", model="wide/steady"), "a recorded choice outlives the configuration"),
        ],
    )
    def test_nothing_is_known_about_a_wire_the_catalogue_cannot_name(self, chosen: Choice | None, why: str) -> None:
        """Both holes answer `None`, which the page draws as a write time it declines to call cold."""
        assert retention_for(CATALOGUE, chosen) is None, why

    async def test_what_a_wire_asks_for_reaches_the_request(self) -> None:
        """
        The other end of it: a setting built and never passed on would look exactly like this one does.

        Beside the choice's own rather than instead of it, because the two answer different questions
        and a session asks both: what the person picked, and what the format needs to be told.
        """
        watcher = Watching()
        caching = Stand(offers=OFFERED["here"], responding=watcher, asking=ModelSettings(temperature=0.5))
        endpoints = Wires(by_endpoint={"here": caching})

        await agent_for(endpoints, Choice(endpoint="here", model="ripe/careful", thinking="high"), "be terse").run("hi")

        assert watcher.seen == [{"temperature": 0.5, "thinking": "high"}]

    async def test_the_output_limit_reaches_the_request_as_the_models_whole_maximum(self) -> None:
        """
        The number sent is the model's own ceiling and not a budget: the model is never told it, so a
        smaller one buys nothing but an answer cut off with its tokens already paid for.
        """
        watcher = Watching()
        endpoints = Wires(by_endpoint={"here": Stand(offers=OFFERED["here"], responding=watcher)})

        await agent_for(endpoints, Choice(endpoint="here", model="ripe/careful"), "be terse", output_cap=128_000).run(
            "hi"
        )

        assert watcher.seen == [{"max_tokens": 128_000}]

    async def test_a_number_somebody_typed_beats_the_one_the_console_looked_up(self) -> None:
        """
        The override's precedence is the ordering that already says a recorded choice wins, and this
        is what pins that the looked-up cap was slotted *under* the choice rather than over it.
        """
        watcher = Watching()
        endpoints = Wires(by_endpoint={"here": Stand(offers=OFFERED["here"], responding=watcher)})
        chosen = Choice(endpoint="here", model="ripe/careful", output_override=20_000)

        await agent_for(endpoints, chosen, "be terse", output_cap=128_000).run("hi")

        assert watcher.seen == [{"max_tokens": 20_000}]

    async def test_with_no_limit_known_nothing_is_sent_rather_than_a_guess(self) -> None:
        """A number guessed too high is refused outright, so the adapter's own default is the honest answer."""
        watcher = Watching()
        endpoints = Wires(by_endpoint={"here": Stand(offers=OFFERED["here"], responding=watcher)})

        await agent_for(endpoints, Choice(endpoint="here", model="ripe/careful"), "be terse").run("hi")

        # Empty settings reach the model as `None`, which is Pydantic AI's normalisation and not
        # this console's, so what is pinned is the absence of the key rather than the shape.
        assert len(watcher.seen) == 1
        assert "max_tokens" not in (watcher.seen[0] or {})


class TestCollectingAStreamedAnswer:
    """
    What `Streamed` hands back, which is a whole response over a request that was a stream.

    Over `FunctionModel`'s streaming arm rather than a provider, because the stand-ins the rest of
    the suite runs on answer whole responses and never reach this.
    """

    def parameters(self) -> ModelRequestParameters:
        return ModelRequestParameters()

    async def test_the_text_a_stream_arrived_in_pieces_is_one_part(self) -> None:
        async def saying(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            yield "the answer "
            yield "in three "
            yield "pieces"

        answered = await Streamed(FunctionModel(stream_function=saying)).request(
            [ModelRequest(parts=[UserPromptPart(content="go")])], None, self.parameters()
        )

        assert answered.parts == [TextPart(content="the answer in three pieces")]

    async def test_a_tool_call_split_across_chunks_arrives_whole(self) -> None:
        """The case a half-drained stream would ruin quietly: arguments are assembled from deltas."""

        async def calling(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
            yield {0: DeltaToolCall(name="read", json_args='{"path":')}
            yield {0: DeltaToolCall(json_args=' "README.md"}')}

        answered = await Streamed(FunctionModel(stream_function=calling)).request(
            [ModelRequest(parts=[UserPromptPart(content="go")])], None, self.parameters()
        )

        assert [(part.tool_name, part.args) for part in answered.parts if isinstance(part, ToolCallPart)] == [
            ("read", '{"path": "README.md"}')
        ]

    async def test_a_stream_drained_to_its_end_is_a_complete_response(self) -> None:
        """A stream broken off part-way answers `incomplete`, which would record a response the console cut short."""

        async def saying(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            yield "done"

        answered = await Streamed(FunctionModel(stream_function=saying)).request(
            [ModelRequest(parts=[UserPromptPart(content="go")])], None, self.parameters()
        )

        assert answered.state == "complete"


class TestReadingAnAnthropicListing:
    def found(self, model_id: str, max_tokens: int | None) -> ModelInfo:
        return ModelInfo(
            id=model_id,
            display_name=model_id,
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
            type="model",
            max_tokens=max_tokens,
        )

    def test_the_output_limit_the_endpoint_states_is_read_off_the_listing(self) -> None:
        """
        The one number beside identity that is, because it is what the request sends and only the
        endpoint is guaranteed to agree with itself about what it accepts.
        """
        assert anthropic_listed(self.found("anthropic/claude-opus-5", 128_000)).output == 128_000

    def test_a_resold_model_the_endpoint_states_nothing_for_carries_nothing(self) -> None:
        """The gateway forwards the serving service's record and leaves the SDK's own field empty."""
        assert anthropic_listed(self.found("fireworks/kimi-k3", None)).output is None


class TestDiscovering:
    async def test_every_profile_is_asked_and_what_it_says_is_what_is_offered(self) -> None:
        endpoints = Wires(by_endpoint={"here": Says(offers=(ONE, TWO)), "gateway": Says(offers=(TWO,))})
        found = await discover(endpoints, CONFIG)
        assert {name: offering.models for name, offering in found.offered.items()} == {
            "here": (ONE, TWO),
            "gateway": (TWO,),
        }

    async def test_a_profile_carries_the_endpoint_it_was_declared_on(self) -> None:
        """
        What a picker needs to tell two endpoints apart, since one gateway often answers both wires.

        Copied off the parsed `Config` rather than the `Endpoint` being carried, so what reaches a
        page is a value with no credential anywhere in it.
        """
        endpoints = Wires(by_endpoint={"here": Says(offers=(ONE,)), "gateway": Says(offers=(TWO,))})
        found = await discover(endpoints, CONFIG)
        assert found.offered["gateway"].format == "openai"
        assert found.offered["gateway"].url == "https://llm.example.invalid/v1"
        assert found.offered["here"].url is None

    async def test_an_endpoint_naming_no_url_says_whose_endpoint_it_is_on(self) -> None:
        """Words rather than a hostname, because which host that is belongs to the SDK, not to us."""
        endpoints = Wires(by_endpoint={"here": Says(offers=(ONE,)), "gateway": Says(offers=(TWO,))})
        found = await discover(endpoints, CONFIG)
        assert found.offered["here"].where == "the anthropic SDK's own endpoint"
        assert found.offered["gateway"].where == "https://llm.example.invalid/v1"

    async def test_an_endpoint_that_cannot_be_asked_is_a_startup_failure_naming_it(self) -> None:
        """There is nothing to fall back on at the first read, so a hole is louder than an empty list."""
        endpoints = Wires(
            by_endpoint={"here": Says(offers=(ONE,)), "gateway": Says(refusing=OSError("no route to host"))}
        )
        with pytest.raises(NothingOffered, match="gateway"):
            await discover(endpoints, CONFIG)

    async def test_an_endpoint_serving_nothing_is_refused_rather_than_offered_empty(self) -> None:
        """An endpoint you can select and then cannot use is the state hardest to diagnose from a page."""
        endpoints = Wires(by_endpoint={"here": Says(offers=(ONE,)), "gateway": Says(offers=())})
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

    def caching(self) -> ModelSettings:  # pragma: no cover - nothing here builds an agent
        return ModelSettings()


# Short enough that the test never waits on it, since every wait below is on a round beginning
# rather than on the clock.
BRIEF = timedelta(seconds=0.001)


async def until_round(endpoint: Rounds, number: int) -> None:
    """Wait for a given round to begin, which is the signal that the one before it was applied."""
    await endpoint.starting.setdefault(number, asyncio.Event()).wait()


class TestKeepingTheCatalogueCurrent:
    async def test_a_round_that_answers_replaces_what_the_holder_carries(self) -> None:
        """A model that appears at the gateway reaches the picker without anybody restarting."""
        endpoint = Rounds(starting={}, answering=(ONE, TWO))
        holder = Catalogues(
            current=Catalogue(offered={"here": serving("here", ONE)}, default=Choice(endpoint="here", model=ONE.id))
        )
        async with background_task(refreshing(holder, Wires({"here": endpoint}), CONFIG, BRIEF)):
            await until_round(endpoint, 2)
            assert holder.current.offered["here"].models == (ONE, TWO)

    async def test_a_round_that_fails_keeps_the_models_discovered_earlier(self) -> None:
        """
        The previous catalogue still answers the question that was asked, only less recently.

        There is no staleness bound past which it gives up, deliberately: emptying the picker
        because a gateway was unreachable is worse than the staleness that would prevent.
        """
        was = Catalogue(offered={"here": serving("here", ONE)}, default=Choice(endpoint="here", model=ONE.id))
        endpoint = Rounds(starting={}, answering=OSError("no route to host"))
        holder = Catalogues(current=was)
        async with background_task(refreshing(holder, Wires({"here": endpoint}), CONFIG, BRIEF)):
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
        async with background_task(refreshing(holder, Wires({"here": endpoint}), CONFIG, BRIEF)):
            await until_round(endpoint, 1)
            assert holder.current is CATALOGUE


class TestTheDefaultChoice:
    def test_a_named_model_is_used_when_the_endpoint_still_has_it(self) -> None:
        named = Config(default="here", default_model=TWO.id, endpoints=CONFIG.endpoints)
        assert default_choice({"here": serving("here", ONE, TWO)}, named) == Choice(endpoint="here", model=TWO.id)

    def test_a_name_the_endpoint_dropped_falls_to_the_first_it_listed(self) -> None:
        """A model retired overnight must not leave a picker whose selected option does not exist."""
        stale = Config(default="here", default_model="vendor/withdrawn", endpoints=CONFIG.endpoints)
        assert default_choice({"here": serving("here", ONE, TWO)}, stale) == Choice(endpoint="here", model=ONE.id)

    def test_naming_none_takes_whatever_came_first(self) -> None:
        assert default_choice({"here": serving("here", TWO, ONE)}, CONFIG) == Choice(endpoint="here", model=TWO.id)


class TestWhatACatalogueOffers:
    """
    `offers` answers "did the picker put this in front of somebody", which is what a posted form
    is checked against. Whether an *existing* session can be answered is the other question, asked
    of the endpoint alone; `test_console.py` is where that one is pinned.
    """

    def test_a_pair_that_was_discovered_is_offered(self) -> None:
        assert CATALOGUE.offers("here", OFFERED["here"][1].id) is True

    @pytest.mark.parametrize(
        ("endpoint", "model"),
        [("gateway", "ripe/careful"), ("here", "vendor/quick"), ("gone", "ripe/fast")],
    )
    def test_a_pair_the_picker_never_showed_together_is_not(self, endpoint: str, model: str) -> None:
        """
        The picker offers models *per endpoint*, so a form naming one endpoint's model under
        another names a pair no page ever rendered.
        """
        assert CATALOGUE.offers(endpoint, model) is False

    def test_a_profile_nothing_declared_has_no_models_rather_than_none(self) -> None:
        """The question the worker and the page both ask, since an endpoint is what an agent needs."""
        assert CATALOGUE.models_of("gone") is None


class TestGroupingForAPicker:
    def test_models_of_one_family_stay_in_the_order_the_endpoint_gave(self) -> None:
        """A gateway lists its newest first, and no ordering imposed here would know that."""
        assert grouped((TWO, ONE)) == (("vendor", (TWO, ONE)),)

    def test_families_appear_in_the_order_each_was_first_seen(self) -> None:
        other = Listed(id="second/plain", label="Plain", provider="second")
        assert [family for family, _ in grouped((ONE, other, TWO))] == ["vendor", "second"]


class TestSummarising:
    def test_one_line_names_each_profile_and_how_much_it_offers(self) -> None:
        """What a log is read for the morning after somebody asks where a model went."""
        one = Catalogue(offered={"here": serving("here", ONE, TWO)}, default=Choice(endpoint="here", model=ONE.id))
        assert summarise(one) == "here offers 2"
