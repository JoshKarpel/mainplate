# What an endpoint does not publish about the models it serves, looked up in a database of record.
#
# A gateway's model list says what it *routes*, and almost nothing about what routing one costs or
# how much it will read. exe.dev's Anthropic wire carries a context window and a capability block
# for Claude and nothing at all for GPT or Grok; its OpenAI wire carries four fields. Nowhere on
# either is there a price. So the facts a person actually chooses a model on are not on the wire,
# and the only honest way to have them is to look them up somewhere that keeps them.
#
# `models.dev` is that somewhere: one unauthenticated JSON document, a provider per key and a model
# per key under it, carrying cost, context and output limits, modalities, and a release date. This
# module is the whole of reading it.
#
# It is **configuration that arrives over the network**, exactly as `catalogue.py` is, and it is
# handled the same way: read once at startup, refreshed on a timer by a task that answers no
# requests, and swapped in whole so a reader holding one never sees half of the next.
#
# What it is *not* is `catalogue.py`'s startup contract, and the difference is the point.
# `catalogue.discover` refuses to return a catalogue with a hole, because an endpoint that lists no
# models is one you can select and then cannot use. Nothing here can put the console in that state:
# a reference that will not load costs a card some numbers. So every failure in this module is an
# ordinary answer of "nothing", in the way `forge.offers` promises, and never a console that will
# not start. A machine with no outbound access is a supported machine.
#
# It is off unless asked for. `Config.model_reference` is absent by default, and absent means this
# process calls nobody but the gateways its own endpoints name.

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final
from typing import Literal
from typing import assert_never
from urllib.parse import urlsplit

import h11
from pydantic_ai.usage import RequestUsage
from without_async import timeout
from without_http import ConnectionPool
from without_http import follow_redirects
from without_http import request
from without_http import stack

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.config import ModelReference
from mainplate.config import ReferenceFormat
from mainplate.durability import Pricer

logger = logging.getLogger(__name__)

# Where the community database lives, offered as the value to put in `config.yaml` rather than
# used as a default: nothing is fetched unless the file names something.
MODELS_DEV: Final = "https://models.dev/api.json"

# Long enough for a four-megabyte document over a slow link, and short enough that a refresh which
# will never finish does not sit in the background holding a connection until the next one starts.
PATIENCE: Final = timedelta(seconds=30)

# The unit every rate in the database is written in, and the one a person compares in. Kept as the
# divisor rather than folded into each rate so that `priced` multiplies whole token counts by whole
# published figures and divides once, which is exact where a per-token rate is a repeating decimal.
MILLION: Final = Decimal(1_000_000)


type Trait = Literal["thinking", "tools", "vision", "pdf", "structured"]

# Every trait a card can show, in the order it shows them. A closed set, because each one has to be
# a thing both a wire's capability block and this database can be read for: a trait only one source
# could ever answer would be a badge that means "and this model came from over there".
TRAITS: Final[tuple[Trait, ...]] = ("thinking", "tools", "vision", "pdf", "structured")


class NotAReference(ValueError):
    """
    The document at the configured source is not a reference database.

    Loud where the bytes are parsed, and caught at the one boundary that promised not to fail. That
    split is deliberate: a source somebody configured and that answers with something else is a
    mistake worth naming in a log, and it is still not a reason this console cannot start.
    """


@dataclass(frozen=True, slots=True)
class Cost:
    """
    What a million tokens costs, in US dollars, as the database states it.

    Per million rather than per token because that is the unit the database is written in and the
    unit a person compares in; converting to a per-token float here would produce numbers no page
    could render without converting them back.

    `cache_read` and `cache_write` are absent for a model whose provider prices no cache, which is
    most of them outside the frontier labs.
    """

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True, slots=True)
class Facts:
    """
    Everything the database says about one model, as the page reads it.

    Every field is optional because the database's own coverage is: cost is absent for the models
    nobody publishes a price for, and a record with no `limit` is one nobody has filled in yet. A
    missing field renders as a card that does not mention it, never as a zero.
    """

    cost: Cost | None = None
    context: int | None = None
    output: int | None = None
    released: date | None = None
    about: str | None = None
    traits: tuple[tuple[Trait, bool], ...] = ()


def said(traits: Sequence[tuple[Trait, bool]], trait: Trait) -> bool | None:
    """
    Whether a source said this model does the thing, does not, or said nothing about it.

    Three answers and not two, which is what keeps a card honest. A wire that publishes
    `image_input: false` has told us something; a wire that publishes no capability block at all has
    not, and collapsing the two would let a database's guess overrule an endpoint's own statement,
    or paint every model from a quiet endpoint as incapable.
    """
    for named, does in traits:
        if named == trait:
            return does
    return None


@dataclass(frozen=True, slots=True)
class Described:
    """
    One model as a card draws it: the endpoint's identity for it, and the database's record of it.

    Every fact comes from the record and none from the listing, which is the whole reason a page of
    these reads as one table rather than three. A gateway describes the models of its own vendor
    richly, forwards somebody else's record verbatim for the ones it resells, and says nothing at
    all about the rest; filling the gaps from a database would leave the facts on a card depending
    on which wire answered, so two models on one page could not honestly be compared.

    `consulted` is whether a reference was configured at all, and it is the whole of what decides
    whether a card with no record says so. With the setting absent there is nothing missing - the
    console was never asked to look anything up - so a marker there would report the absence of a
    feature nobody turned on.
    """

    listed: Listed
    facts: Facts | None
    consulted: bool

    @property
    def unreferenced(self) -> bool:
        """Whether the card should say the database has no record of this model."""
        return self.consulted and self.facts is None

    @property
    def context(self) -> int | None:
        return self.facts.context if self.facts else None

    @property
    def output(self) -> int | None:
        return self.facts.output if self.facts else None

    @property
    def released(self) -> date | None:
        return self.facts.released if self.facts else None

    @property
    def about(self) -> str | None:
        return self.facts.about if self.facts else None

    @property
    def cost(self) -> Cost | None:
        return self.facts.cost if self.facts else None

    @property
    def traits(self) -> tuple[Trait, ...]:
        """
        What this model does, as positive badges only.

        Positive because that is all a badge can honestly mean, and because the database answers
        `false` for most traits on most models: drawing those would put a row of struck-out words on
        every card. A trait the record denies or never mentions is simply not drawn, which reads as
        "not stated" rather than as a claim either way.
        """
        answered = self.facts.traits if self.facts else ()
        return tuple(trait for trait in TRAITS if said(answered, trait) is True)


@dataclass(frozen=True, slots=True)
class Reference:
    """
    A database of record, indexed by the two names a model can be looked up under.

    Two indexes rather than one, and the split is what keeps a price from being wrong rather than
    merely missing. `qualified` is `provider/model`, taken apart from the id an endpoint routes;
    it is exact and cannot be ambiguous. `upstream` is the model's canonical name at whichever
    service actually serves it (`accounts/fireworks/models/kimi-k3`), which is how a gateway's own
    listing identifies a model it is reselling.

    The upstream index holds only names that appear **once** in the whole database. That is the
    load-bearing part: an aggregator publishes other people's models under its own provider key, so
    a flat index over every id collides hundreds of times, and the colliding records carry the
    aggregator's marked-up price. A name that two providers claim therefore resolves to nothing, and
    a card shows no price rather than somebody else's.
    """

    qualified: Mapping[str, Facts]
    upstream: Mapping[str, Facts]

    def look_up(self, listed: Listed) -> Facts | None:
        """
        What this database says about a model, by its routed id and then by its upstream name.

        In that order because the routed id is what the session will actually name, so a gateway
        that has taken a model over under its own provider key is described by that key's record
        rather than by the original's.
        """
        provider, slash, model = listed.id.partition("/")
        if slash and (found := self.qualified.get(f"{provider}/{model}")) is not None:
            return found
        if listed.upstream is not None:
            return self.upstream.get(listed.upstream)
        return None


def parse_reference(raw: bytes, notation: ReferenceFormat) -> Reference:
    """
    A document as the two indexes a card is drawn from, read according to what it is.

    One arm today, and the `assert_never` is the point of writing it as a match at all: a second
    format is a member added to `ReferenceFormat` and an arm added here, and until the arm exists
    the type checker names this function as the place that has to answer for it.
    """
    match notation:
        case "models.dev":
            return parse_models_dev(raw)
        case _ as unreachable:
            assert_never(unreachable)


def parse_models_dev(raw: bytes) -> Reference:
    """
    A `models.dev` document as the two indexes a card is drawn from.

    Pure, so the whole of what a document means is testable without a network or a file, and so the
    one place that decides what a usable record is is the one place that reads one.

    Loud only about the shape of the *document*. An individual record that is malformed is skipped
    rather than fatal, because this is a community database of seven thousand models and one bad
    entry must not cost the other six thousand nine hundred their prices. A document that is not a
    mapping of providers at all is a different thing: that is the configured source answering with
    something that is not a reference database, and it is worth a line in a log naming it.
    """
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as broken:
        raise NotAReference(f"this is not valid JSON: {broken}") from broken
    if not isinstance(document, dict):
        raise NotAReference(f"a reference database is a mapping of providers, not {type(document).__name__}")

    qualified: dict[str, Facts] = {}
    # Counted rather than collected, so a name claimed twice is dropped from the index instead of
    # resolving to whichever provider happened to be walked last.
    upstream: dict[str, Facts] = {}
    claimed: set[str] = set()
    for provider, offered in document.items():
        if not isinstance(offered, dict):
            continue
        models = offered.get("models")
        if not isinstance(models, dict):
            continue
        for model, record in models.items():
            if not isinstance(record, dict):
                continue
            facts = parse_facts(record)
            qualified[f"{provider}/{model}"] = facts
            # Only the slash-bearing ids, which are the ones a reselling gateway echoes back as a
            # model's canonical name. A bare id is a name in its own provider's namespace and means
            # nothing on its own, so indexing it would be inviting exactly the collisions the
            # uniqueness rule exists to refuse.
            if "/" not in model:
                continue
            if model in claimed:
                upstream.pop(model, None)
            else:
                claimed.add(model)
                upstream[model] = facts
    if not qualified:
        raise NotAReference("this document declares no models")
    return Reference(qualified=qualified, upstream=upstream)


def parse_facts(record: Mapping[str, object]) -> Facts:
    """One model's record, taking the fields a card draws and ignoring everything else."""
    limit = record.get("limit")
    limits = limit if isinstance(limit, dict) else {}
    return Facts(
        cost=parse_cost(record.get("cost")),
        context=whole(limits.get("context")),
        output=whole(limits.get("output")),
        released=released_on(record.get("release_date")),
        about=prose(record.get("description")),
        traits=parse_traits(record),
    )


def parse_cost(costs: object) -> Cost | None:
    """
    What the record says a million tokens costs, or nothing where it prices nothing.

    Input and output are both required, because a card showing one of them is a price nobody can
    use: what a turn costs is the two together. The cache figures are genuinely optional and are
    taken where present.

    The richer keys the database also carries - tiered pricing, a separate rate past 200k of
    context, audio - are deliberately not read. Each is a different thing to explain on a card, and
    a single figure that silently means "under 200k" would be a number that is wrong exactly when
    somebody is doing the thing that makes it wrong.
    """
    if not isinstance(costs, dict):
        return None
    asked = money(costs.get("input"))
    answered = money(costs.get("output"))
    if asked is None or answered is None:
        return None
    return Cost(
        input=asked,
        output=answered,
        cache_read=money(costs.get("cache_read")),
        cache_write=money(costs.get("cache_write")),
    )


def parse_traits(record: Mapping[str, object]) -> tuple[tuple[Trait, bool], ...]:
    """
    What this record says the model does, as answers rather than as a set of what it can do.

    Read as three-valued for the reason `said` explains: the database answers `reasoning` and
    `tool_call` for every model it holds, so `false` there is a statement and not a gap, and a card
    must be able to tell that from a record that never mentioned the thing.
    """
    modalities = record.get("modalities")
    accepts = modalities.get("input") if isinstance(modalities, dict) else None
    taking = tuple(each for each in accepts if isinstance(each, str)) if isinstance(accepts, list) else None
    answers: tuple[tuple[Trait, object], ...] = (
        ("thinking", record.get("reasoning")),
        ("tools", record.get("tool_call")),
        ("structured", record.get("structured_output")),
        ("vision", "image" in taking if taking is not None else None),
        ("pdf", "pdf" in taking if taking is not None else None),
    )
    return tuple((trait, answer) for trait, answer in answers if isinstance(answer, bool))


def whole(found: object) -> int | None:
    """A token count, which is a positive whole number or nothing. `bool` is not one, though it is an `int`."""
    return found if isinstance(found, int) and not isinstance(found, bool) and found > 0 else None


def money(found: object) -> float | None:
    """A rate in dollars, which may be written as a whole number and may legitimately be zero."""
    return float(found) if isinstance(found, int | float) and not isinstance(found, bool) and found >= 0 else None


def released_on(found: object) -> date | None:
    try:
        return date.fromisoformat(found) if isinstance(found, str) else None
    except ValueError:
        return None


def prose(found: object) -> str | None:
    return stripped if isinstance(found, str) and (stripped := found.strip()) else None


def is_url(source: str) -> bool:
    """
    Whether the configured source is something to fetch rather than something to read.

    The scheme decides, so one setting serves both: a machine with no outbound access names a file
    it has, and one that would rather be current names the database itself. Nothing else about the
    two differs, so nothing else in this module knows which it got.
    """
    return urlsplit(source).scheme in {"http", "https"}


async def fetch(url: str) -> bytes:
    async with (
        ConnectionPool() as pool,
        timeout(PATIENCE),
        # The URL is somebody's setting rather than a constant, so it may well be a mirror, a
        # shortened link, or a raw-file host that redirects. A hop nobody follows would be an empty
        # card for a reason no log would explain.
        request(stack(follow_redirects())(pool), "GET", url) as (head, body),
    ):
        if head.status != 200:
            raise NotAReference(f"{url} answered {head.status}")
        return await body.read()


async def load_reference(configured: ModelReference) -> Reference:
    """
    The database the configuration names, whether that is a URL to fetch or a file to read.

    Raises, and is called only from places that treat a failure as "no reference": this says what
    went wrong, and `refreshed` decides that the console carries on anyway.
    """
    if is_url(configured.source):
        return parse_reference(await fetch(configured.source), configured.format)
    # Off the event loop, because a four-megabyte read is long enough to be worth not blocking a
    # console that is answering requests while the refresher runs.
    raw = await asyncio.to_thread(Path(configured.source).read_bytes)
    return parse_reference(raw, configured.format)


@dataclass(slots=True)
class References:
    """
    The current reference, held so its readers can outlive any one of them. See `Catalogues`.

    `current` is `None` where the setting names no source, and that is a different state from an
    empty one: nothing was asked, so nothing is missing, and a card says nothing about a record it
    was never told to look for.
    """

    current: Reference | None = None


async def refreshed(holder: References, configured: ModelReference) -> None:
    """
    Re-read the database into the holder, keeping the last good value if this read fails.

    Never raises, which is the whole of this module's contract with the rest of the process: a
    reference is a nicety over a console that already works, so an unreachable database costs a
    card its numbers and nothing else. It is `forge.offers`'s promise rather than
    `catalogue.discover`'s refusal, and the difference is that nothing here can leave somebody
    holding a choice they cannot use.
    """
    try:
        holder.current = await load_reference(configured)
    except (NotAReference, OSError, TimeoutError, h11.RemoteProtocolError) as unread:
        kept = "keeping the reference read earlier" if holder.current else "carrying on without one"
        logger.warning(f"could not read the model reference at {configured.source}: {unread!r}, {kept}")
    else:
        logger.info(f"model reference read from {configured.source}: {len(holder.current.qualified)} models")


async def refreshing(holder: References, configured: ModelReference, every: timedelta) -> None:
    """
    Re-read on a timer, for as long as this is running.

    Sleeps first, because the caller has already done the read that filled the holder. A reference
    database changes when somebody ships a model or moves a price, so this is measured in hours
    where the catalogue's is measured in minutes.
    """
    while True:
        await asyncio.sleep(every.total_seconds())
        await refreshed(holder, configured)


def describe(listed: Listed, reference: Reference | None) -> Described:
    """One model as a card draws it, which is the endpoint's answer and the database's together."""
    return Described(
        listed=listed,
        facts=reference.look_up(listed) if reference is not None else None,
        consulted=reference is not None,
    )


def facts_of(catalogue: Catalogue, reference: Reference | None, chosen: Choice) -> Facts | None:
    """
    What the database says about the model one session is bound to, or nothing where nothing does.

    The lookup a card makes, asked from the other end: a card starts with a listing and this starts
    with a recorded choice, so the endpoint's own listing has to be found first. Each of the three
    ways to know nothing is an ordinary `None` - no database was configured, the endpoint no longer
    lists the id this session was recorded on, or the database has never heard of it - and a page
    draws the same blank for all three.

    One function rather than two, because what a turn is priced by and how big its window is are the
    same record read for two fields, and two lookups could come to disagree about which record that
    is.
    """
    if reference is None:
        return None
    listed = catalogue.listed_as(chosen.endpoint, chosen.model)
    if listed is None:
        return None
    return reference.look_up(listed)


def rate(per_million: float) -> Decimal:
    """
    One published price as an exact decimal.

    Through `str` rather than `Decimal(float)`, which would carry the binary approximation of a
    figure written in decimal into a number this console then *records*: `$0.30` per million becomes
    `0.29999999999999998889776975374843...` and every turn priced at it says so forever.
    """
    return Decimal(str(per_million))


def priced(cost: Cost, usage: RequestUsage) -> Decimal | None:
    """
    What one model request came to in US dollars, at the rates a record currently publishes.

    Pure, and the whole of the arithmetic, so what a turn cost is testable against a `Cost` and four
    integers with no gateway, no database and no conversation anywhere near it.

    **The token counts nest rather than partition.** Pydantic AI normalises every wire so that
    `input_tokens` *includes* the cache reads and writes, which Anthropic's own numbers exclude, so
    the freshly-read input is what is left once both are taken back out. Adding them on instead
    charges the cached tokens twice at the full rate, which on a long conversation is most of the
    bill and in the direction that flatters nobody.

    A record that prices no cache has its cached tokens charged at its input rate. That is the
    conservative reading rather than a guess at a discount nobody published, and the alternative -
    calling the whole request unpriceable - would blank exactly the models that cache the most.

    `None` where the counts cannot be true of one request, which is a wire claiming more cached
    tokens than input. A number to say nothing about rather than one to clamp into looking sound,
    and never an exception: pricing runs inside the model request and must not be able to fail a turn.
    """
    fresh = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
    if fresh < 0:
        return None
    reading = cost.input if cost.cache_read is None else cost.cache_read
    writing = cost.input if cost.cache_write is None else cost.cache_write
    charged = (
        Decimal(fresh) * rate(cost.input)
        + Decimal(usage.cache_read_tokens) * rate(reading)
        + Decimal(usage.cache_write_tokens) * rate(writing)
        + Decimal(usage.output_tokens) * rate(cost.output)
    )
    return charged / MILLION


@dataclass(frozen=True, slots=True)
class Prices:
    """
    Where a session's model is priced: what its endpoint lists, and what the database says of it.

    Both holders rather than either's current value, because this is read at the moment a request is
    made and not when the pass around it started. A pass lasts as long as a session is being
    answered, and both of these are reloadable configuration underneath it, so a value taken at the
    top would price every turn of a long conversation at whatever happened to be true for its first.
    """

    catalogues: Catalogues
    references: References

    def facts(self, chosen: Choice) -> Facts | None:
        """
        What is known about this session's model as things stand, or nothing where nothing is.

        One record read for however many fields a caller wants, which is `facts_of`'s own argument
        said one layer in: what a turn is priced by and how big its window is come off the same
        lookup, so asking twice is how the two would come to disagree about which record that is.

        Read at the moment of the question rather than at the top of the pass, because both holders
        under it are reloadable configuration and a pass outlives a refresh of either.
        """
        return facts_of(self.catalogues.current, self.references.current, chosen)

    def pricer(self, chosen: Choice) -> Pricer:
        """
        What one session's requests are priced by, which is as fixed as the choice it is built from.

        A closure over the choice because the only thing that varies from one request to the next is
        the usage: a session records its endpoint and its model once and is bound to both for life,
        so every other half of the question is already answered here.

        Each of the three ways to know nothing is an ordinary `None`: no database was configured, the
        endpoint no longer lists the id this session was recorded on, or the record carries no price.
        A card shows the same blank for the same reasons, so a turn is unpriced exactly where the
        model it ran on was.
        """

        def price(usage: RequestUsage) -> Decimal | None:
            facts = self.facts(chosen)
            if facts is None or facts.cost is None:
                return None
            return priced(facts.cost, usage)

        return price
