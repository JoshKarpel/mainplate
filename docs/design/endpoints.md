# Endpoints and models

Where an answer comes from: what a session records about who answers it, how the set of models is
discovered, and where a model's price and context window are looked up.

## Endpoints, discovery, and per-session auth

An **endpoint** is a URL, an API format, and a credential. The models are separate and are not in
the file at all: `catalogue.py` asks each endpoint's own model-list API what it serves. A session
records an endpoint, a model and a thinking level, and is bound to all three for life.

**Three words, kept apart deliberately.** An *endpoint* is what `config.yaml` declares. A *wire*
(`agent.py`) is the built thing that speaks one API format. A *provider* is whoever made a model,
which is discovered and is a facet rather than a level: the same provider appears under more than
one endpoint, because every Fireworks model on exe.dev's gateway is listed by both formats under one
id. `grouped` therefore groups *within* an endpoint's list and never across.

The thinking level lives in `thinking.py` rather than beside `Choice`, and the reason is a cycle:
`config.py` has to validate a configured name and `agent.py` already imports `config.py`. What is
left is a small shared vocabulary three layers read. Its effort names are recovered from Pydantic
AI's `ThinkingEffort` rather than restated, so a level that library adds reaches the picker without
an edit. The three that are not efforts are spelled out because they are not gradations of one
thing: `None` leaves the setting off the request, `False` asks for thinking off, and `True` asks for
it at the provider's own budget, which on the Anthropic wire is no parameter, an omitted block, and
ten thousand tokens.

`format` names the API shape rather than the vendor, because one hostname often answers both and
each reaches models the other does not. It also decides what `url` means: the Anthropic SDK appends
`/v1/messages`, so it wants the host; the OpenAI SDK appends `/chat/completions`, so it wants the
host and `/v1`. On exe.dev that is why `install` writes two endpoints for one gateway.

`agent.py` holds one `Wire` class per format, and it holds *all three* format-specific things: how
to name a model over it, how to ask it what it serves, and what it has to be told to reuse a
conversation's prefix. A third format is one class, not an edit in three files.

**`caching` is the third, and it exists because getting it wrong is invisible and expensive.** A
conversation is re-sent whole on every turn, so a session with no cache breakpoint pays full input
price for everything said so far, over and over: on a long turn that is most of the bill, and
nothing about the request looks any different. It is opt-in on the Anthropic wire
(`anthropic_cache`, a top-level `cache_control` whose breakpoint the server moves forward as the
conversation grows) and automatic and uncontrollable on the OpenAI one, which answers with an empty
`ModelSettings`. Empty rather than absent, because what has to be true is that every wire *answers*:
a format added later is then a `caching` somebody had to write rather than a session quietly paying
full price.

`CACHE_FOR` is `1h` rather than the default five minutes, and the trade is stated because it is
real: an hour's retention is written at 2x base input against 1.25x, so it pays only where a
conversation is picked up again after a pause. That is what a chat console *is*, somebody reads an
answer, thinks, and replies, where five minutes barely outlasts one long turn.

`agent_for` merges the wire's answer under the session's own, so a recorded choice always wins. The
two do not overlap today; if they ever do, the thing somebody picked should be the thing that
happens. `test_what_a_wire_asks_for_reaches_the_request` is what fails when the merge goes, because
a setting built and never passed on looks exactly like one that was.

`chat_models` is the pure half of the OpenAI side and is where its two exclusions live: exe.dev
publishes every OpenAI model twice (bare and prefixed) and mixes embedding models in with chat ones.
The embedding rule is a rule over names because that list carries no capability to ask;
`test_catalogue.py` pins both against the shapes a live gateway actually returns.

## Configuration

`config.py` parses `config.yaml` into `Config`, once, at startup. Two things there are easy to undo
by accident:

- **Credentials are `SecretStr` and come from the file, not the environment.** A key handed to
  `AnthropicProvider(api_key=...)` never becomes an environment variable. `Endpoint.key` is the one
  place that decides between a configured key, the `KEYLESS` placeholder for a gateway that
  authenticates at its edge, and `None`, which is what leaves the SDK reading its own environment
  variable for itself. Do not "simplify" that `None` away.
- **`build_wires` is eager**, so an endpoint that cannot be built fails at startup naming itself
  rather than on whichever session first chose it. It builds the *provider* and not a model per
  name, which loses nothing: an SDK validates neither, so the eager build was only ever buying
  endpoint validation. That is [the refusal](../philosophy.md#refusing-at-startup-or-promising-not-to-raise),
  and it is also why `discover` refuses an endpoint that lists nothing.

The agent itself is built **per turn** rather than held in a startup mapping, because the model set
is discovered and changes while the process runs, and because what a session is told includes the
repository's own guidance and the worktree holding it is planted inside the loop. That costs tens of
microseconds against a turn that costs seconds, and the connection pool, the expensive part, belongs
to the endpoint and is shared by every model over it.

The endpoint is still asked for *before* the loop, and that split is the point rather than a
leftover: `endpoints.for_endpoint` raising `UnknownChoice` there is what keeps a missing endpoint a
failure the console can explain rather than one discovered mid-turn. Built any earlier than the
loop, a session's first turn would be answered having been told nothing the project says about
itself, since the clone and the worktree do not exist until `planting` has run.

## Advertised is narrower than routable

**A discovered catalogue says what an endpoint advertises, which is narrower than what it will
route**, and conflating the two is the mistake to avoid. exe.dev's gateway answers
`claude-sonnet-4-6` while listing it as `anthropic/claude-sonnet-4-6`, so every session recorded
before that prefix appeared names a model discovery will never return. So the two questions are kept
apart:

- **Starting** a session asks `Catalogue.offers(endpoint, model)`. That is form validation: what the
  picker drew is a suggestion the page made, not a constraint on what can be posted, so a new
  session may only be created on a pair the picker actually offered.
- **Answering** one asks only whether the *endpoint* exists, in the worker (`agent_for` raising
  `UnknownChoice`) and in `Conversation.answerable` (`models_of(...) is not None`), which are
  deliberately the same question so the page and the worker cannot disagree. The model is not
  checked: the provider's own refusal is the authoritative answer about a model and it arrives on
  the turn, where gating here would strand a conversation nobody broke.

A session whose endpoint is gone renders with a sentence naming it and no spinner, because a spinner
that will never resolve is the one state a person cannot diagnose. `test_console.py` pins both
halves, including that a session on an unlisted-but-routable model keeps its spinner. The connection
stays open either way, which is a different question: it is the page's rather than the turn's, so
what a stalled session must not do is claim something is coming.

`exe.py` is the exe.dev half, and it answers reflection twice over: which LLM gateways are attached
(so `mainplate install` writes keyless endpoints) and which GitHub repositories are (so a session
has somewhere to work). A VM reaches both with no credential at all. Every failure there returns
`()` rather than raising: "you are not on exe.dev" must not be a failed install or a console that
will not start. Gateway discovery is passed *into* `converge` rather than done inside it, so the
suite does not behave differently depending on which machine it runs on.

## Keeping the catalogue current

`catalogue.py` is [configuration that changes under a
reader](../philosophy.md#configuration-that-changes-under-a-reader) and takes the whole of that
stance: `open_console` calls `discover` before the store is opened, a background task re-asks on
`Settings.refresh`, `Catalogues.current` is rebound rather than edited, and a failed refresh keeps
the last good value with no staleness bound.

It does not contradict the checkpoint being the conversation: nothing in it is anything anybody
said.

## What a model card says, and where it comes from

`reference.py` is a second piece of reloadable configuration beside the catalogue, and the split
between them is the thing to keep straight. The **catalogue** says which models exist and is asked
of the endpoints. The **reference** says what they cost and what they do, and is asked of one
database, because no endpoint reached so far answers that question at all.

**`Listed` is identity and nothing else**: id, label, family, and `upstream`. That is a refusal
rather than an omission. A gateway's list holds three shapes at once: a Claude arrives fully typed
with a capability block and token limits, a resold model arrives with all of that empty and the
upstream service's record forwarded in the extras, and GPT and Grok arrive as four fields saying
nothing. Reading each of those and filling the gaps from a database would put three kinds of card on
one page, where the facts shown depended on which wire answered. One source is worth more than the
coverage a merge would buy, so `Described` reads facts only from the reference.

`upstream` is the exception and is identity too: it is what the service actually serving a model
calls it (`accounts/fireworks/models/kimi-k3`), and it is the second of the two keys a record is
found under. It is not optional in practice, since most of what a gateway serves is resold and the
provider-and-model split alone finds none of it.

Three rules there are load-bearing:

- **The routed id wins over the upstream name.** A gateway that has taken a model over under its own
  key sets the terms the session is actually billed and limited by, so its record is the truer of
  the two.
- **A name two providers claim resolves to neither.** An aggregator republishes other people's
  models under its own key at its own markup, so a flat index over every id collides in the
  hundreds. Demanding uniqueness turns a wrong price into no price, which is the only safe way to be
  wrong here. `test_reference.py` pins this against a fixture where the collision costs 15x.
- **It can never stop the console starting.** This is [the
  promise](../philosophy.md#refusing-at-startup-or-promising-not-to-raise) and not the refusal,
  because nothing here can leave somebody holding a choice they cannot use. A reference that will
  not load costs a card its numbers.

`facts_of` is the same lookup asked from the other end: a card starts with a listing, and a session
starts with a recorded choice, so the endpoint's own listing has to be found first. One function
rather than two, because what prices a turn and how big that model's window is are the same record
read for two fields, and two lookups could come to disagree about which record that is.
`Prices.pricer` reads it for the cost, and `Service.read` reads it for `Conversation.window`, which
is what every rule's gauge is drawn against.

`Described.consulted` is what decides whether a card with no record says so. With
`[model_reference]` absent nothing was looked up, so nothing is missing, and a marker there would
report the absence of a feature nobody turned on.

`format` in the config table exists so a second database is one more `ReferenceFormat` member and
one more arm in `parse_reference`, which `assert_never` makes the type checker demand. It stays a
value rather than becoming a plugin point.
