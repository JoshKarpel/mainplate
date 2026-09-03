from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic_ai.usage import RequestUsage

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import Offering
from mainplate.config import ModelReference
from mainplate.reference import Cost
from mainplate.reference import Facts
from mainplate.reference import NotAReference
from mainplate.reference import Prices
from mainplate.reference import Reference
from mainplate.reference import References
from mainplate.reference import describe
from mainplate.reference import is_url
from mainplate.reference import load_reference
from mainplate.reference import parse_reference
from mainplate.reference import priced
from mainplate.reference import refreshed
from mainplate.reference import said

# A document shaped exactly as `models.dev` publishes one, cut to the cases that decide something.
# Written out rather than trimmed from a real 4MB download, so what each entry is here to exercise
# is readable beside it and a schema change is a diff in one place.
DOCUMENT: dict[str, object] = {
    "anthropic": {
        "models": {
            # Everything filled in, which is what most frontier records look like.
            "claude-opus-5": {
                "name": "Claude Opus 5",
                "description": "The careful one.",
                "reasoning": True,
                "tool_call": True,
                "structured_output": True,
                "release_date": "2026-07-24",
                "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                "limit": {"context": 1_000_000, "output": 128_000},
                "cost": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25},
            },
            # Prices nothing, which 6% of the database does. Everything else still reads.
            "claude-free-1": {
                "name": "Claude Free",
                "reasoning": False,
                "tool_call": True,
                "limit": {"context": 200_000, "output": 8_000},
            },
        }
    },
    "fireworks-ai": {
        "models": {
            # A resold model, filed under the name its own service uses. This is the key an
            # exe.dev-style gateway echoes back, and the only way most of a gateway is found.
            "accounts/fireworks/models/kimi-k3": {
                "name": "Kimi K3",
                "tool_call": True,
                "reasoning": True,
                "limit": {"context": 262_144},
                "cost": {"input": 0.6, "output": 2.5},
            },
            # The same upstream name a second provider also claims; see the collision test.
            "accounts/fireworks/models/contested": {
                "name": "Contested",
                "limit": {"context": 1_000},
                "cost": {"input": 1, "output": 2},
            },
        }
    },
    "reseller": {
        "models": {
            # An aggregator republishing somebody else's model under its own key, at its own price.
            # This is what makes a flat index dangerous rather than merely untidy.
            "accounts/fireworks/models/contested": {
                "name": "Contested, resold",
                "limit": {"context": 1_000},
                "cost": {"input": 9, "output": 9},
            },
            "own-model": {"name": "Own", "limit": {"context": 5_000}, "cost": {"input": 1, "output": 1}},
        }
    },
}


def a_document(body: object = DOCUMENT) -> bytes:
    return json.dumps(body).encode()


def listing(model_id: str, upstream: str | None = None) -> Listed:
    return Listed(id=model_id, label=model_id, provider=model_id.partition("/")[0], upstream=upstream)


class TestReadingADocument:
    def test_a_model_is_found_under_its_provider_and_its_own_id(self) -> None:
        found = parse_reference(a_document(), "models.dev")
        assert found.qualified["anthropic/claude-opus-5"].context == 1_000_000

    def test_every_field_a_card_draws_is_taken_off_the_record(self) -> None:
        found = parse_reference(a_document(), "models.dev")
        assert found.qualified["anthropic/claude-opus-5"] == Facts(
            cost=Cost(input=5, output=25, cache_read=0.5, cache_write=6.25),
            context=1_000_000,
            output=128_000,
            released=date(2026, 7, 24),
            about="The careful one.",
            traits=(("thinking", True), ("tools", True), ("structured", True), ("vision", True), ("pdf", True)),
        )

    def test_a_record_that_prices_nothing_still_carries_everything_else(self) -> None:
        """Six per cent of the database has no cost, and a card must show the rest rather than nothing."""
        facts = parse_reference(a_document(), "models.dev").qualified["anthropic/claude-free-1"]
        assert facts.cost is None
        assert facts.context == 200_000

    def test_half_a_price_is_no_price(self) -> None:
        """
        What a turn costs is reading and writing together, so one figure alone would rank a list wrongly.

        A card showing only an input rate reads as though that is the price, which is the kind of
        wrong number somebody makes a decision on.
        """
        half = {"p": {"models": {"m": {"cost": {"input": 3}, "limit": {"context": 1}}}}}
        assert parse_reference(a_document(half), "models.dev").qualified["p/m"].cost is None

    def test_a_trait_the_record_denies_is_an_answer_and_not_a_gap(self) -> None:
        """Three-valued, so "said no" and "never mentioned" stay distinguishable all the way to the card."""
        facts = parse_reference(a_document(), "models.dev").qualified["anthropic/claude-free-1"]
        assert said(facts.traits, "thinking") is False
        assert said(facts.traits, "tools") is True
        assert said(facts.traits, "structured") is None, "this record never mentions it"

    def test_modalities_become_the_traits_a_card_can_show(self) -> None:
        facts = parse_reference(a_document(), "models.dev").qualified["anthropic/claude-opus-5"]
        assert said(facts.traits, "vision") is True
        assert said(facts.traits, "pdf") is True

    @pytest.mark.parametrize(
        "body",
        [b"not json at all", b"[]", b'"a string"', b"null"],
        ids=["unparseable", "a list", "a string", "null"],
    )
    def test_a_document_that_is_not_a_reference_database_says_so(self, body: bytes) -> None:
        """The configured source answering with something else is worth naming in a log."""
        with pytest.raises(NotAReference):
            parse_reference(body, "models.dev")

    def test_a_document_declaring_no_models_is_refused(self) -> None:
        """An empty index and an unreachable database would otherwise be indistinguishable."""
        with pytest.raises(NotAReference, match="no models"):
            parse_reference(a_document({"format": {"models": {}}}), "models.dev")

    def test_one_unreadable_record_does_not_cost_the_others_their_prices(self) -> None:
        """Seven thousand community-maintained records, so a single bad entry must not be fatal."""
        mixed = {"p": {"models": {"broken": "not a record", "fine": {"cost": {"input": 1, "output": 2}}}}}
        found = parse_reference(a_document(mixed), "models.dev")
        assert "p/broken" not in found.qualified
        assert found.qualified["p/fine"].cost == Cost(input=1, output=2)


class TestFindingAModel:
    def test_a_routed_id_is_split_into_the_provider_and_the_model(self) -> None:
        found = parse_reference(a_document(), "models.dev")
        assert found.look_up(listing("anthropic/claude-opus-5")) == found.qualified["anthropic/claude-opus-5"]

    def test_a_resold_model_is_found_by_the_name_its_own_service_uses(self) -> None:
        """
        Most of a gateway's catalogue, and the half a provider-and-model split cannot reach.

        The gateway routes `fireworks/kimi-k3`, and no provider in the database is called
        `fireworks`; the record is filed under the name Fireworks itself uses.
        """
        found = parse_reference(a_document(), "models.dev")
        resold = listing("fireworks/kimi-k3", upstream="accounts/fireworks/models/kimi-k3")
        assert found.look_up(resold) is not None
        assert found.look_up(resold) == Facts(
            cost=Cost(input=0.6, output=2.5),
            context=262_144,
            traits=(("thinking", True), ("tools", True)),
        )

    def test_a_name_two_providers_claim_resolves_to_neither(self) -> None:
        """
        An ambiguous price is worse than no price, because a card cannot say which one it showed.

        An aggregator republishes other people's models under its own provider key at its own
        marked-up rate, so a flat index over every id collides in the hundreds and resolves to
        whichever provider happened to be walked last.
        """
        found = parse_reference(a_document(), "models.dev")
        contested = listing("fireworks/contested", upstream="accounts/fireworks/models/contested")
        assert found.look_up(contested) is None

    def test_a_contested_name_is_still_reachable_by_its_provider(self) -> None:
        """Dropping it from one index is not dropping it: the exact key is never ambiguous."""
        found = parse_reference(a_document(), "models.dev")
        assert found.qualified["fireworks-ai/accounts/fireworks/models/contested"].cost == Cost(input=1, output=2)

    def test_the_routed_id_wins_over_the_upstream_name(self) -> None:
        """
        A gateway that has taken a model over under its own key is described by that key's record.

        Its terms are what the session will actually be billed and limited by, so the original's
        record is the less true of the two answers.
        """
        found = parse_reference(a_document(), "models.dev")
        taken = listing("reseller/own-model", upstream="accounts/fireworks/models/kimi-k3")
        assert found.look_up(taken) == found.qualified["reseller/own-model"]

    def test_a_model_nothing_in_the_database_names_is_simply_not_found(self) -> None:
        found = parse_reference(a_document(), "models.dev")
        assert found.look_up(listing("nobody/nothing")) is None

    def test_a_bare_id_is_not_looked_up_by_name(self) -> None:
        """A name with no provider means nothing on its own, and indexing one invites the collisions above."""
        found = parse_reference(a_document(), "models.dev")
        assert found.look_up(listing("own-model")) is None


class TestDescribingAModelForACard:
    REFERENCE = Reference(qualified={"p/known": Facts(context=100, cost=Cost(input=1, output=2))}, upstream={})

    def test_a_model_with_a_record_carries_its_facts(self) -> None:
        described = describe(listing("p/known"), self.REFERENCE)
        assert described.context == 100
        assert described.cost == Cost(input=1, output=2)
        assert not described.unreferenced

    def test_a_model_the_configured_database_does_not_hold_says_so(self) -> None:
        """The reference was asked and had nothing, which is a fact about the lookup worth showing."""
        described = describe(listing("p/absent"), self.REFERENCE)
        assert described.facts is None
        assert described.unreferenced

    def test_with_no_reference_configured_nothing_is_reported_as_missing(self) -> None:
        """
        Nothing was looked up, so nothing is missing.

        This is the default, and a marker here would report the absence of a feature nobody turned
        on: every card on every console without the setting would carry a note about a database it
        was never told to consult.
        """
        described = describe(listing("p/absent"), None)
        assert described.facts is None
        assert not described.unreferenced

    def test_only_what_a_record_affirms_becomes_a_badge(self) -> None:
        """
        Positive only, because that is all a badge can mean and because most answers are `false`.

        Drawing the denials would put a row of struck-out words on every card; drawing the silences
        would claim things no source said.
        """
        facts = Facts(traits=(("thinking", True), ("tools", False)))
        described = describe(listing("p/x"), Reference(qualified={"p/x": facts}, upstream={}))
        assert described.traits == ("thinking",)


class TestWhereTheDatabaseComesFrom:
    @pytest.mark.parametrize(
        ("source", "fetched"),
        [
            ("https://models.dev/api.json", True),
            ("http://localhost:8080/api.json", True),
            ("/etc/mainplate/models.json", False),
            ("models.json", False),
            ("./relative/models.json", False),
        ],
    )
    def test_the_scheme_decides_whether_it_is_fetched_or_read(self, source: str, fetched: bool) -> None:
        """
        One setting for both, so a machine with no outbound access names a file and changes nothing else.

        Nothing downstream asks which it got: the bytes are parsed the same way either way.
        """
        assert is_url(source) is fetched

    async def test_a_file_on_disk_is_read_and_parsed(self, tmp_path: Path) -> None:
        at = tmp_path / "models.json"
        at.write_bytes(a_document())
        found = await load_reference(ModelReference(source=str(at)))
        assert found.qualified["anthropic/claude-opus-5"].context == 1_000_000

    async def test_a_source_that_cannot_be_read_leaves_the_console_without_one(self, tmp_path: Path) -> None:
        """
        Never a startup failure, which is the whole of this module's contract with the rest of the
        process: unlike an endpoint that cannot list its models, a reference that will not load costs
        a card some numbers and can cost it nothing else.
        """
        holder = References()
        await refreshed(holder, ModelReference(source=str(tmp_path / "absent.json")))
        assert holder.current is None

    async def test_a_failed_reread_keeps_the_database_already_read(self, tmp_path: Path) -> None:
        """
        The previous answer is still a good answer to the question that was asked; it is only older.

        There is deliberately no staleness bound past which it is discarded: the bound would have to
        be invented, and emptying every card because a host was unreachable for an hour is worse
        than the staleness it would prevent.
        """
        at = tmp_path / "models.json"
        at.write_bytes(a_document())
        holder = References()
        await refreshed(holder, ModelReference(source=str(at)))
        at.unlink()
        await refreshed(holder, ModelReference(source=str(at)))
        assert holder.current is not None
        assert holder.current.qualified["anthropic/claude-opus-5"].context == 1_000_000

    async def test_a_source_answering_with_something_else_is_not_a_failure_either(self, tmp_path: Path) -> None:
        at = tmp_path / "models.json"
        at.write_text("<html>a login page</html>")
        holder = References()
        await refreshed(holder, ModelReference(source=str(at)))
        assert holder.current is None


class TestPricingOneRequest:
    """
    The arithmetic a recorded cost is made of, against a rate table and four integers.

    Pure, so none of this needs a gateway, a database or a conversation, which is the point of
    `priced` being separable from everything that finds a rate for it.
    """

    RATES = Cost(input=3, output=15, cache_read=0.3, cache_write=3.75)

    def test_input_and_output_are_charged_at_their_own_rates(self) -> None:
        spent = priced(self.RATES, RequestUsage(input_tokens=1_000, output_tokens=500))
        # 1000 * $3/M + 500 * $15/M.
        assert spent == Decimal("0.0105")

    def test_cached_tokens_are_taken_out_of_the_input_rather_than_added_to_it(self) -> None:
        """
        The counts nest: `input_tokens` already includes both cache figures.

        This is the sign error worth a test of its own, because adding them charges the cached
        tokens twice at the full rate and a long conversation is mostly cached tokens. Here the
        wrong reading gives $0.0111 against the right one's $0.0084, and both look plausible.
        """
        spent = priced(
            self.RATES,
            RequestUsage(input_tokens=1_000, output_tokens=500, cache_read_tokens=800, cache_write_tokens=100),
        )
        # 100 fresh at $3/M, 800 read at $0.30/M, 100 written at $3.75/M, 500 out at $15/M.
        assert spent == Decimal("0.008415")

    def test_a_record_pricing_no_cache_charges_cached_tokens_as_input(self) -> None:
        """
        The conservative reading, and deliberately not a discount nobody published.

        Calling the request unpriceable instead would blank exactly the models that cache the most,
        which is the population a person most wants a number for.
        """
        plain = Cost(input=3, output=15)
        spent = priced(plain, RequestUsage(input_tokens=1_000, output_tokens=500, cache_read_tokens=800))
        assert spent == priced(plain, RequestUsage(input_tokens=1_000, output_tokens=500))

    def test_counts_that_cannot_be_true_of_one_request_are_priced_at_nothing(self) -> None:
        """
        More cached tokens than input is a wire contradicting itself, so there is no honest figure.

        `None` rather than a clamp, because a clamped total reads as a real one; and never an
        exception, because this runs inside the model request and must not be able to fail a turn.
        """
        assert priced(self.RATES, RequestUsage(input_tokens=10, cache_read_tokens=50)) is None

    def test_a_request_that_cost_nothing_is_priced_at_nothing_rather_than_left_unpriced(self) -> None:
        """Zero and unknown are different answers, and only one of them may be drawn as `free`."""
        assert priced(Cost(input=0, output=0), RequestUsage(input_tokens=99, output_tokens=99)) == Decimal(0)

    def test_a_published_rate_carries_no_binary_noise_into_a_recorded_figure(self) -> None:
        """
        `Decimal(0.3)` is `0.29999999999999998889776975374843...`, and this figure is *recorded*.

        A float rate would put that tail into the checkpoint of every turn priced at it, where it
        would stay: the point of recording a cost is that nothing later re-derives it.
        """
        spent = priced(Cost(input=0.3, output=0), RequestUsage(input_tokens=1_000_000))
        assert spent == Decimal("0.3")


class TestFindingTheRateForASession:
    """
    Which rate a session's requests are priced at, and the three ways there is no answer.

    Every one of them is an ordinary `None`, because a card shows the same blank for the same
    reasons: a turn goes unpriced exactly where the model it ran on does.
    """

    CHOICE = Choice(endpoint="gateway", model="p/known")

    def catalogue(self, *models: Listed) -> Catalogues:
        offering = Offering(endpoint="gateway", format="anthropic", url=None, models=models)
        return Catalogues(current=Catalogue(offered={"gateway": offering}, default=self.CHOICE))

    def test_a_listed_model_with_a_record_is_priced_by_it(self) -> None:
        prices = Prices(
            catalogues=self.catalogue(listing("p/known")),
            references=References(
                current=Reference(qualified={"p/known": Facts(cost=Cost(input=3, output=15))}, upstream={})
            ),
        )
        assert prices.pricer(self.CHOICE)(RequestUsage(input_tokens=1_000)) == Decimal("0.003")

    def test_a_resold_model_is_priced_through_the_upstream_name_its_listing_carries(self) -> None:
        """
        The name the serving service uses is the second key a record is found under, and for most of
        what a gateway routes it is the only one that finds anything.
        """
        prices = Prices(
            catalogues=self.catalogue(listing("gw/rebadged", upstream="accounts/fireworks/models/real")),
            references=References(
                current=Reference(
                    qualified={},
                    upstream={"accounts/fireworks/models/real": Facts(cost=Cost(input=1, output=2))},
                )
            ),
        )
        priced_at = prices.pricer(Choice(endpoint="gateway", model="gw/rebadged"))
        assert priced_at(RequestUsage(input_tokens=1_000_000)) == Decimal(1)

    def test_with_no_database_configured_nothing_is_priced(self) -> None:
        prices = Prices(catalogues=self.catalogue(listing("p/known")), references=References())
        assert prices.pricer(self.CHOICE)(RequestUsage(input_tokens=1_000)) is None

    def test_a_model_the_endpoint_no_longer_lists_is_not_priced(self) -> None:
        """
        A gateway routes more ids than it advertises, so a session can outlive its own listing.

        Unpriced and emphatically not unanswerable: this is the same distinction `Catalogue.offers`
        keeps, and getting it wrong here would be a session that cannot be continued because nobody
        could say what it cost.
        """
        prices = Prices(
            catalogues=self.catalogue(listing("p/something-else")),
            references=References(
                current=Reference(qualified={"p/known": Facts(cost=Cost(input=3, output=15))}, upstream={})
            ),
        )
        assert prices.pricer(self.CHOICE)(RequestUsage(input_tokens=1_000)) is None

    def test_a_record_that_prices_nothing_leaves_the_turn_unpriced(self) -> None:
        prices = Prices(
            catalogues=self.catalogue(listing("p/known")),
            references=References(current=Reference(qualified={"p/known": Facts(context=100)}, upstream={})),
        )
        assert prices.pricer(self.CHOICE)(RequestUsage(input_tokens=1_000)) is None
