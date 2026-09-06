# What a turn cost

The money and the clock. Both are [recorded rather than
re-derived](../philosophy.md#what-a-turn-cost-and-how-long-it-took-are-recorded-not-looked-up), and
this page is where and how.

## Pricing a response

**A response is priced before the step records it**, in `Stepping.price`, called from
`CheckpointedModel.request`. That is not where Pydantic AI does it: `fill_response_cost` runs in the
agent graph, which is *outside* the step, so left to it the cost reaches `turn:{n}:messages` and
never `turn:{n}:model:{i}`, and a turn being watched has no cost until the instant it ends. Priced
here it is in both, and `so_far` stays the prefix of `blocks_of` that the console depends on.

**The reference and not `genai-prices`, and the coverage gap is why.** Pydantic AI's pricing knows
`claude-sonnet-4-6` and returns nothing for `accounts/fireworks/models/glm-5p3`, which is exactly
the resold population [`Reference.upstream`](endpoints.md#what-a-model-card-says-and-where-it-comes-from)
exists to price. Live, that gap is a blank that fixes itself when the database improves; *recorded*,
it would be a permanent null. So recording is what makes the coverage worth the threading.

`Prices` holds both holders rather than either's current value, because a pass lasts as long as a
session is being answered and both are reloadable configuration underneath it.
`Prices.pricer(chosen)` closes over the choice, since the only thing that varies request to request
is the usage.

**`Pricer` is a function because the alternative is an import cycle.** `reference.py` reads
`agent.py`, which builds the agent `durability.py`'s capability is attached to, so `durability.py`
cannot import what prices a model. Injecting the one question it has keeps the capability ignorant
of endpoints, catalogues and databases, which is the same ignorance that lets one instance serve
every session.

**`priced` is pure, and the token counts nest rather than partition.** Pydantic AI normalises every
wire so `input_tokens` *includes* the cache reads and writes, which Anthropic's own numbers exclude,
so the fresh input is what is left after taking both out. Adding them instead charges cached tokens
twice at the full rate, which on a long conversation is most of the bill; `test_reference.py` pins
that sign. A record pricing no cache charges cached tokens at its input rate, which is conservative
rather than a guess at an unpublished discount. Counts that cannot be true of one request price at
`None` rather than clamping, and nothing here raises: this runs inside the model request and must
not be able to fail a turn.

**Unknown is not free.** `None` means nothing could price it and is drawn as no figure at all; a
`Cost` of zero is drawn as `free`. `Spent.cost` is `None` where *any* response in a turn went
unpriced rather than the sum of the ones that were, and `altogether` applies the same rule to a
session, because a total quietly missing a turn is the one way to be wrong about money that a reader
cannot catch.

## How long it took

The same argument as the cost, one field along. `Stepping.stamp` writes it beside `Stepping.price`
in `CheckpointedModel.request`, timing the wrapped model's own call and nothing around it, so the
figure is the round trip to the provider and not the snapshot before it or the store write after.

**Where each one is recorded is decided by the value, not by symmetry.** A `ModelResponse` has
`metadata`, so the request's duration needs no field of its own and reaches both readings of a turn
for free; a tool return is somebody else's value with nowhere to put a fact about the call, so its
duration is a field on the record wrapped around it. See [the key
scheme](checkpoints.md#the-key-scheme). A tool that *raised* is timed no more than it is recorded,
since the `ModelRetry` propagates out of the step and there is nothing to write, which is the honest
record, and is why a still-out call and an untimed one read the same.

**A tool's duration is threaded into both readings rather than found in either.** It is not in
`turn:{n}:messages`, so `parted` and `blocks_from` are both handed the mapping `tooks_in` builds out
of `turn:{n}:tool:{id}`. Handed to one and not the other, a call's time would appear or disappear at
the moment a turn landed, which is exactly the drift the two readings exist not to have. Both
readings take it from one walk, `calls_in`, rather than from two: what a call returned and how long
it took are one record, so nothing can find one without the other.

**A turn's time is its round trips and not its wall clock.** `Spent.took` sums the responses, so
what a rule reports is what the turn spent waiting on the provider; the calls it made in between are
timed on their own panels, and adding those in would double-count a batch that ran at once. Unknown
anywhere is unknown for the whole, exactly as with the cost, so a turn recorded before this console
timed anything shows no figure rather than a suspiciously small one.

## Whether the cache is still warm, and what that is worth

A conversation is re-sent whole on every turn, so a session picked up after lunch pays full input
price for everything said in it and nothing about the request looks any different. `cache_note` is
the line above the message box that says so, and it earns its row only because `Wire.caching` asks
for a cache at all: with none there would be nothing to have gone cold and nothing worth saying.

**A figure and not a warning, in the family of the gauge and the `▣` count.** It never tells anybody
to `forget`: at low utilization the right move is to carry on, and picking which figure matters is
the reader's. It says what is true and stops.

**One-sided, always.** Past the retention a prefix is cold and this says so; under it nothing can be
asserted, because eviction is unobservable from here. What it says instead is `warm as of 12m`,
which is a claim about when the prefix was last *written*, a response landing being exactly that
moment, and is true on any wire whatever that wire's own TTL. `RETENTION` works as the one threshold
for the same reason: it is the longest this console asks for anywhere, so past it the prefix is gone
everywhere and no format has to be threaded to the page to know it.

**The server renders an absolute time and the script renders the relative one.** Nothing here
re-renders on the clock, since the stream sends when the session *records* something and the
interval that decides the answer is exactly the one where nothing is recorded, so a server-rendered
`warm` would sit there while the retention rolled past it. `cached at 15:09` is a fact that cannot
rot, which is what a reader with `mainplate.js` absent gets, and `warm as of 12m` is the script's
reading of it. Cold is the one state the server *can* assert, since it was already true when the
page was rendered and nothing makes a cold prefix warm again.

**That split is also what keeps one elapsed formatter rather than two.** The server never renders a
duration here, so `ago` exists only in the script; a server that rendered `12m` too would be the
same three-branch format written in two languages with nothing holding them together.

**What the script adds is a duration to a duration, never one clock to another.** `data-since` is
how long ago the server measured the last response to be, and the rest is measured in the browser
from the moment it first saw that element, so a reader whose machine disagrees with the console's is
still right. A swap replaces the element, which gets a fresh `data-since` and a fresh stamp, which
is exactly what should happen.

**`Conversation.since` is the one place this console subtracts two clocks**, and the caveat lives on
the field. `Transcript.answered_at` is stamped by whichever process ran the pass and `since` is
taken in a request handler, so the difference is sound exactly as long as those are one machine,
which today they are. Split across machines it becomes as good as the two clocks' agreement, which
for a threshold in hours is fine and for anything finer would not be. It is measured in
`Service.read` rather than on the page, because [a page is a pure function of already-answered
questions](../philosophy.md#a-page-is-a-pure-function-of-already-answered-questions) and `now()` is
not one.

**`ModelResponse.timestamp` rather than the store's own write time**, which `Checkpointer.history`
would give. Pydantic AI already stamps it, it survives the checkpoint round trip, and it needs no
second read; the store's clock has the identical split-deployment caveat, so the general shape buys
nothing here. What it means is "when the response was received locally", which is as close as this
console gets to when the provider last touched the prefix.

**The money is a floor and says so.** What it prices is the input of the next turn's *first*
request, re-sending what has already been said, and not the answer, the tools that turn runs, or the
further requests it makes, any of which can dwarf it. A bare figure would read as what the next turn
costs and understate it by however much work that turn turns out to be, so it carries a `+` and the
title spells out what sits on top. It is the one thing about a turn nobody has started that can be
stated exactly rather than guessed at.

**Both ends are drawn, and the gap between them is the point.** `▣$0.0289 / $0.2889+` is what
re-sending costs with the whole prefix cached against none of it, which is what makes the cost of
*waiting* legible: on a long conversation that is a tenfold jump and nothing about the request would
have looked any different. Neither figure claims to be the one that will be charged, since how much
of a prefix the provider still holds is unobservable, which is the same reason `warm` is never
asserted in words, so the pair is stated and the state beside it says which end the session is
nearer.

`▣` for the cached end rather than the word `warm`, because the state is already one of those two
words and the line would carry each of them twice. It is the mark the rule already uses for the part
of an input a provider read from its cache, so it means the same thing in both places.

**No warm figure where the record prices no cache.** `priced` falls back to the input rate there, so
a warm end would be the cold one printed twice, which reads as a bug rather than as a database that
does not say. `Resending.warm` is `None` for exactly that, and the line draws the one end it knows.

**One value rather than two fields**, so the pair can never be computed from two different contexts
or two different records, which is `facts_of`'s own argument one scale down.

**It is a second partial on the page's own connection**, which is the shape `streaming.py` was built
for and the first thing to use it. The note lives in the composer, so the transcript's swap does not
reach it, and what it says goes stale on every turn: the context it prices grows and the moment it
measures moves. `outerHTML` rather than the transcript's morph, since it is one short line with
nothing in it worth preserving.

**`RETENTION` and `CACHE_FOR` are [one fact in two
places](../philosophy.md#one-fact-in-two-places).** The parameter has to be a literal, because the
SDK types the field as `Literal['5m', '1h']` and a string rendered from a `timedelta` is a `str`;
the duration has to be a `timedelta`, because that is what the comparison takes. So they are written
twice with nothing enforcing the agreement, and
`test_the_retention_and_the_wire_parameter_are_one_duration` is what turns a drift into a failure
rather than a console confidently calling a dead prefix warm.

**Every response fixture carries a timestamp**, in `conftest.recorded_turn` and in
`scripts/gallery.py`. `ModelResponse.timestamp` defaults to the moment it was constructed, so a
fixture without one is the moment the test or the render ran: an assertion over a whole `Transcript`
becomes a comparison against the wall clock, and two `just gallery` runs produce two different
pages. A screenshot that differs run to run is one nobody can compare against the last.
