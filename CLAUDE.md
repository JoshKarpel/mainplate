# CLAUDE.md

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows.
[`README.md`](README.md) is what it does and why; this is what to know before changing it.

## Commands

```console
$ just setup            # uv sync, the browsers, and pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_console.py::TestTheConsole  # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve            # foreground, on port 8101 so it never fights the installed service
$ just demo             # the same, on a database of its own, for poking without touching real sessions
$ just seed             # the gallery's fixtures into that database, so there is something to click
$ just shots            # render every page and screenshot it, wide and phone, into build/shots
$ just install          # this checkout as a user systemd unit, on the default port 8100
$ just logs             # journalctl --user -u mainplate -f
$ just uninstall        # removes the unit, keeps the environment file and the database
```

`just serve` and `just demo` run under `watchfiles` and restart on any change under `src/mainplate`,
which is what makes a styling change watchable: the assets are inventoried once at startup, so an
edited stylesheet only reaches a *new* process. They restart the server and do not reload the
browser, which is one keystroke against needing a dev-only script injected into a page that ships.

`just serve` and `just install` are on different ports on purpose, so a foreground run for a quick
look never takes down the service. `just install` runs `uv sync` first, and that is not a
convenience: the unit names this checkout's interpreter, so an install from a stale environment
points systemd at a venv missing whatever was just added.

**A real turn costs real money, so do not spend one to see something a fixture already shows.**
`just seed` plants the gallery's checkpoints into the demo database, which is a whole conversation
to read, fold, search, fork and screenshot without a provider ever being asked anything. That is
the right tool for a rendering, a stylesheet, a control, or anything downstream of a checkpoint,
which is most of what changes here. When a change genuinely needs a live pass - the wires, the
catalogue, durability, the worker - start the session on the cheapest model the endpoint lists and
say the shortest thing that exercises it. Reach for a frontier model only when the change is about
what a frontier model does differently, and say so.

**`scripts/seed.py` is the one writer that is not `Service`, so an invariant the service enforces
does not hold there unless it is repeated.** It supplies checkpoint keys directly, which is what lets
it plant a finished conversation nobody paid for, and is also what makes it the one place a
contradictory record can be written: it once seeded every fixture naming a repository while recording
that it reached no files, because `Isolation.settled` lives in `Service.start` and nothing here goes
through it. So a rule about what a recorded `choice` may hold is a rule this file has to apply too,
and the demo database is where that is noticed - rebuild it (`rm mainplate-demo.db*` then `just
seed`) after any change to what a choice records, or it keeps serving the old shape.

**The stylesheet is a deliverable, and no string assertion checks one.** `just shots` renders every
page from fixture checkpoints and drives a real Chromium over them, so a styling change can be
looked at rather than argued about. It needs no server, no database, no provider and no
`config.yaml`, because a page is a pure function of already-answered questions: `scripts/gallery.py`
answers them with fixtures and the assets are copied beside the output, so a static server renders
what the console renders. Every shot also prints whether the document scrolls sideways, which is how
the `:target` rule that widened a panel past its container was found.

`tests/test_browser.py` is the other half and asks a different kind of question, mostly over the
same gallery. What a still cannot show is that *two* panels are drawn as where the reader is, or
that a form posts controls that sit outside it, so behaviour gets a real Chromium and its own
assertions. It is in the suite rather than in a recipe of its own because a check nobody runs is a
check that catches nothing: both of the bugs it first pinned were live while an equivalent script
sat beside it unrun. A browser that is not installed fails loudly rather than skipping, for the same
reason.

**Sideways scroll is asserted there rather than in the shots, and that is the same lesson again.**
`shoot.mjs` prints it beside the screenshot it is measuring and fails nothing, which is a diagnostic
for somebody already looking; `TestTheShapeOfANarrowWindow` fails a build. It asks two things,
because overflow can be right for the wrong reason: whether any page pushes the document sideways on
a phone, and whether a page carrying a rail is still *one* grid track there. The second is the
direct guard on the breakpoint ordering below, and it reports the sidebar track coming back rather
than one of the ways that shows.

Its `console` fixture is the one thing there that leaves the gallery, and it has to. The gallery
proves how a conversation *renders*; what the live connection has to prove is that the page changes
when the checkpoint does, which is a second render arriving at a page nobody reloaded. So that
fixture serves the real app on a real port and hands the test the `Service` behind it, and the test
writes the steps a pass would write while the browser is looking - which is also the only way to
hold a turn half-finished long enough to assert on it.

It drives Playwright's **async** binding, which is not a preference: `sync_playwright` runs an event
loop on the calling thread, and this suite is already running one, so the sync API leaves every
browser test passing and every *other* async test failing its teardown. One session-scoped loop
(`pytestmark = pytest.mark.asyncio(loop_scope="session")`) so the browser can be session-scoped too.

The console's own script is served from `assets/` and depends on nothing. `package.json` exists only
for `scripts/shoot.mjs`, so a checkout currently pins two Chromiums: Playwright's Python and Node
bindings each fetch their own.

Run `just test` or `just check` before saying anything is done. CI runs the same pre-commit
configuration, so there is one definition of what the checks are.

`pytest` runs under `xdist` (`-n auto`), `pytest-randomly`, and a 10-second per-test timeout, all
from `addopts`. A test that needs longer raises it with `@pytest.mark.timeout(...)` rather than
changing the global; Playwright's retrying `expect` is capped well under it so a failed assertion
reports as itself rather than as a hang. `pythonpath = ["."]` is what puts `scripts/` on the path,
so the browser tests render the gallery with the same code `just gallery` runs.

## The one idea

**The checkpoint is the conversation.** There is no messages table, no session state in the
server, and no cache. A page renders `checkpointer.load(session)`, a crash resumes from the same
rows, and two tabs agree because they are reading the same thing.

Anything that would keep a second copy of what was said is the change to push back on. The
session index (`sessions.py`) is the one row per session that exists, because
`without-durability` cannot enumerate workflows; it holds a title as well as an id, and that is
*not* a copy of changing state, because a session is named after its first message and nothing
ever renames it.

What the index does *not* hold, it **reaches for rather than copies**. A checkpoint is a row per
key rather than one value, and both tables are in the one file, so `SELECTION` reads a session's
repository straight out of its `choice` with a `LEFT JOIN` and `json_extract`: one small row per
session, and no word of any conversation. That is the shape any further "what is this session on"
question should take. A column here would be the second copy the whole console is built to avoid,
and unlike the title it would be a copy of something recorded elsewhere and already authoritative.

The model catalogue (`catalogue.py`) is the one piece of process state that genuinely changes under
a reader, and it is not an exception either: nothing in it is anything anybody said. It is
configuration that happens to live at the far end of a request rather than on disk, so it is
handled the way reloadable configuration is, and a page render reads it out of memory rather than
asking a gateway. The test for a change here is the same one: does it keep a second copy of what
was *said*?

The console's `localStorage` is the last thing that looks like an exception and is not. It holds
only what a *reader* decided and would want to find again: the theme, and which kinds are set
aside. Never a word of the conversation, so a browser with it wiped renders exactly what one
without it does. The theme is the reader's across every session; what is set aside is a fact about
one conversation, so it is keyed by session id, and that scoping is load-bearing rather than tidy:
every session shares one origin, so an unscoped key would be one conversation's decisions imposed
on all of them.

Two things the script holds are deliberately *not* stored, and the line between them is worth
keeping. Which calls a reader has unfolded, and whether they are following the end, are modes
within a visit rather than decisions about a conversation: unfolding a call is how you read one
answer, and following is a mode you fall out of by scrolling up and back into by scrolling down.
Carried across a reload either would be a page that opens somewhere the reader has to notice and
undo. Both survive every swap, which is what they actually have to do.

A **fork** is the one thing here that copies what was said, and it is not the exception it looks
like. The rule is against a copy that has to be kept in step with something that changes; a fork
copies turns that are already settled and that nothing will ever rewrite, so the two sessions are
two values that happened to be equal rather than two views of one thing. It is also what keeps a
fork readable on its own, since each session's checkpoint stays the whole of its own conversation
with nothing to dereference.

## The key scheme

One key for the session and five per turn, written by five different places and read by five:

```text
choice               the endpoint, model, repository, isolation and thinking level; written by
                     `Service.start`
                     and by `Service.fork`, before the prompt
turn:{n}:prompt      the person's message; written from outside a pass, by `Service.say`
turn:{n}:tree:{i}    the worktree before the i-th model request; written by `StepwiseDurability`
turn:{n}:model:{i}   the i-th model response of that turn; written by `StepwiseDurability`
turn:{n}:tool:{id}   what one tool call returned; written by `StepwiseDurability`
turn:{n}:messages    what the agent run produced; written by the conversation body
```

**The two indexed kinds are numbered by position and the tool key deliberately is not.** Model
requests happen in a fixed order, so counting them names a step the same way on every pass, and the
tree captured before each one rides the same counter so `tree:{i}` and `model:{i}` are the two
halves of one request. A *batch* of tool calls runs concurrently, so counting those would name a
record by whichever won a race and hand a later pass somebody else's result. A call already carries
an id, and that id is part of the model response the conversation recorded, so a replay is handed
the same one for free. `Stepping.key` is the positional form and `Stepping.identified` is the other.

`opening_tree_key(n)` is `turn:{n}:tree:0`, and it is what two things mean by "this turn's tree": a
fork plants its worktree at it, and the rule opening the turn shows it. Both want the state before the turn
did anything.

`choice` goes in before the first prompt and never again *within a session*. The order is
load-bearing: the prompt is what *queues* a session, so writing it first would let a worker take the
session and find no endpoint to answer on. Never again, because a session that changed endpoint
halfway would replay recorded answers from one and continue on another. Forking is how the choice
changes, and it changes it by making a different session rather than by rewriting this one.

`turn_of` is the inverse of `turn_prefix`, and it answers about the key's *shape* rather than
against a list of known kinds. That is what lets `before` carry a whole prefix of a conversation
into a fork without being taught each new kind of step: a `turn:3:approval:0` nobody has written yet
is turn 3 already, and `turn:3:tool:toolu_017` was too before anything read tool keys.

**The names are built in two places and have to agree.** `conversation.py` names them for the
readers (`prompt_key`, `tree_key`, `opening_tree_key`, `messages_key`, `model_key`, `tool_key`, read
by `choice_of` and `reached` for the body, `transcript`, `so_far` and `responded` for the page,
`before` for a fork, `planting` for a fork's worktree). `Stepping` in `durability.py` builds them for the writers,
from a turn prefix and a kind, which is what lets one capability name a step without importing the
conversation. `tree_key(n, i)` and `Stepping.key("tree")` therefore produce the same string from
opposite ends, and nothing enforces that: change one and change the other. The tests in
`test_conversation.py` assert the shape against literal recorded values rather than round-tripping
through the writer, which is what turns a drift into a failure rather than a silently unfindable
record.

**The two indexed kinds have a reader now, and that is what draws a turn as it happens.** `responded`
walks `model:{i}` from zero and `so_far` looks each call's result up under `tool:{id}`, so the turn
being answered renders from the steps behind it rather than waiting for its `messages`. It is not a
second copy of anything: those records exist so that a resumed pass does not pay for the same request
twice, and this reads them.

The walk is its own function because a running turn is read *twice*, for what it has said and for
what it has spent, and `transcript` calls `responded` once and hands the result to both. Walked
separately the two would eventually disagree about how much of a turn there is, which on a page that
draws a turn as it fills in is a rule reporting one number against a conversation showing another.

What holds the two readings together is that **`so_far` produces a prefix of what `blocks_of` will
produce once the turn lands**: the same responses, in the same order, cut by `blocks_in`, with the
results that have not arrived still out. That is why a panel never moves as a turn fills in, and why
the morph when `messages` finally lands touches nothing. `test_conversation.py` asserts the two
readings of a finished turn are equal, which is also what catches the subtle half of it - a tool
result's text has to be what `ToolReturnPart.model_response_str` produces, so `returned_step` uses
`pydantic_core.to_json` and not `json.dumps`, whose spacing differs on every structured return.

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
`config.py` has to validate a configured name and `agent.py` already imports `config.py`. What
is left is a small shared vocabulary three layers read. Its effort names are recovered from Pydantic
AI's `ThinkingEffort` rather than restated, so a level that library adds reaches the picker without
an edit. The three that are not efforts are spelled out because they are not gradations of one
thing: `None` leaves the setting off the request, `False` asks for thinking off, and `True` asks for
it at the provider's own budget, which on the Anthropic wire is no parameter, an omitted block, and
ten thousand tokens.

`format` names the API shape rather than the vendor, because one hostname often answers both and
each reaches models the other does not. It also decides what `url` means: the Anthropic SDK appends
`/v1/messages`, so it wants the host; the OpenAI SDK appends `/chat/completions`, so it wants the
host and `/v1`. On exe.dev that is why `install` writes two endpoints for one gateway.

`agent.py` holds one `Wire` class per format, and it holds *both* format-specific things: how to
name a model over it and how to ask it what it serves. A third format is one class, not an edit in
three files.
`chat_models` is the pure half of the OpenAI side and is where its two exclusions live: exe.dev
publishes every OpenAI model twice (bare and prefixed) and mixes embedding models in with chat
ones. The embedding rule is a rule over names because that list carries no capability to ask;
`test_catalogue.py` pins both against the shapes a live gateway actually returns.

`config.py` parses `config.yaml` into `Config`, once, at startup. Two things there are easy to
undo by accident:

- **Credentials are `SecretStr` and come from the file, not the environment.** A key handed to
  `AnthropicProvider(api_key=...)` never becomes an environment variable. `Endpoint.key` is the
  one place that decides between a configured key, the `KEYLESS` placeholder for a gateway that
  authenticates at its edge, and `None`, which is what leaves the SDK reading its own environment
  variable for itself. Do not "simplify" that `None` away.
- **`build_wires` is eager**, so an endpoint that cannot be built fails at startup naming itself
  rather than on whichever session first chose it. It builds the *provider* and not a model per
  name, which loses nothing: an SDK validates neither, so the eager build was only ever buying
  endpoint validation. That is also why the service refuses to start on an unusable `config.yaml`,
  and why `discover` refuses an endpoint that lists nothing.

The agent itself is built per pass rather than held in a startup mapping, because the model set is
now discovered and changes while the process runs. That costs tens of microseconds against a turn
that costs seconds, and the connection pool - the expensive part - belongs to the endpoint and is
shared by every model over it.

**A discovered catalogue says what an endpoint advertises, which is narrower than what it will
route**, and conflating the two is the mistake to avoid. exe.dev's gateway answers
`claude-sonnet-4-6` while listing it as `anthropic/claude-sonnet-4-6`, so every session recorded
before that prefix appeared names a model discovery will never return. So the two questions are
kept apart:

- **Starting** a session asks `Catalogue.offers(endpoint, model)`. That is form validation: what the
  picker drew is a suggestion the page made, not a constraint on what can be posted, so a new
  session may only be created on a pair the picker actually offered.
- **Answering** one asks only whether the *endpoint* exists, in the worker (`agent_for` raising
  `UnknownChoice`) and in `Conversation.answerable` (`models_of(...) is not None`), which are
  deliberately the same question so the page and the worker cannot disagree. The model is not
  checked: the provider's own refusal is the authoritative answer about a model and it arrives on
  the turn, where gating here would strand a conversation nobody broke.

A session whose endpoint is gone renders with a sentence naming it and no spinner, because a
spinner that will never resolve is the one state a person cannot diagnose. `test_console.py` pins
both halves, including that a session on an unlisted-but-routable model keeps its spinner. The
connection stays open either way, which is a different question: it is the page's rather than the
turn's, so what a stalled session must not do is claim something is coming.

`exe.py` is the exe.dev half, and it answers reflection twice over: which LLM gateways are attached
(so `mainplate install` writes keyless endpoints) and which GitHub repositories are (so a session
has somewhere to work). A VM reaches both with no credential at all. Every failure there returns
`()` rather than raising: "you are not on exe.dev" must not be a failed install or a console that
will not start. Gateway discovery is passed *into* `converge` rather than done inside it, so the
suite does not behave differently depending on which machine it runs on.

## Where a repository comes from

`forge.py` is the seam, and it exists because there will be more than one answer. A **forge**
answers one question - what repositories can this console reach? - and everything below it takes a
git directory without asking how it got there. `ExeDevGitHub` is the one that exists because it is
the one we are on; a plain `GitHub` through an App is a different thing to configure and to trust,
so it will be a second class rather than a flag on this one.

Three things there are decided rather than incidental:

- **`offers()` promises not to raise.** A forge describes an environment this process merely
  happens to be in, so "not on exe.dev" and "nothing attached" are ordinary answers. `discover`
  logs a forge that breaks that promise and carries on with the others, because one forge's mistake
  is not a reason the console cannot start. This is deliberately *unlike* `catalogue.discover`,
  which refuses an endpoint listing no models: that is an endpoint you can select and then cannot use,
  where a machine with no repositories attached simply has none.
- **A repository is one *way of reaching* a repository, not one repository.** `key` is the forge's
  identifier for the attachment and `name` is what a person recognises, because the same repository
  can be attached twice with different rights - one acting as your GitHub user, one as the app, one
  read-only - and those are different things to start a session on. `Reachable.labelled` puts the
  attachment name on a row only where two rows would otherwise read identically.
- **The clone URL is rebuilt on the integration's own hostname**, never the aggregate
  `github.int.exe.xyz` that an integration's `help` prints. The aggregate resolves to whichever
  attachment happens to serve that repository; `<integration>.int.exe.xyz` resolves to exactly one.
  That is what tells two attachments apart, and it is narrower besides: an integration's own host
  answers "Repository not found" for any repository but its own.

`Clones` keeps one **bare** clone per repository, so there is no "main" checkout to confuse with a
session's and every worktree is a linked one off a shared object store. `Workspaces` is the four of
them as one value - clones, the worktree root, the scratch root, and what the forges reach - because
they only mean anything as a set: a worktree is of a clone, a clone is of something a forge reached,
and a scratch is what sits beside a worktree. It is the one place the word "workspaces" still means
anything, naming the storage area rather than a collection of `Worktree`, which is what
`MAINPLATE_WORKSPACES` and `Settings.workspace_root` have always called it.

**The worker clones, never a request handler.** `Service.start` records the choice and returns; the
session's first pass clones the repository and plants the worktree. A clone is a network fetch that
can take minutes and creating a session is a POST somebody is waiting on, so putting it there is
exactly the coupling the control-plane rule argues against. Both halves are idempotent, so every
later pass reaches the same call and does nothing. It is an effect *outside* a step deliberately:
what it does is make a directory exist, which is the same on every pass, so there is no result to
record and nothing for a replay to disagree with.

`FORGES` in `app.py` is the list, declared rather than configured. A forge that needs configuring
will carry its own settings; one that does not needs no switch, because `ExeDevGitHub` reaching
nothing off exe.dev is the correct behaviour there rather than something to turn off.

## Keeping the catalogue current

`catalogue.py` is the one piece of process state that changes under a reader, and it does not
contradict the checkpoint being the conversation: nothing in it is anything anybody said. It is
configuration that lives at the far end of a request rather than on disk, handled the way any
reloadable configuration is.

- **Read before ready, refreshed off the request path.** `open_console` calls `discover` before the
  store is opened, so an endpoint that cannot say what it serves is a lifespan that raises and a
  service that never takes traffic. After that a background task re-asks on `Settings.refresh`. A
  page render reads `catalogues.current` out of memory and never causes a request to a gateway.
- **Swapped whole, never edited.** `Catalogues.current` is rebound to a new `Catalogue`, so a
  reader that grabbed one holds a consistent answer even if a newer one lands mid-render.
- **A failed refresh keeps the last good value and there is no staleness bound.** That is
  deliberate rather than an omission: the bound would have to be invented, and emptying the picker
  because a gateway was unreachable for an hour is worse than the staleness it would prevent.

## What a model card says, and where it comes from

`reference.py` is a second piece of reloadable configuration beside the catalogue, and the split
between them is the thing to keep straight. The **catalogue** says which models exist and is asked
of the endpoints. The **reference** says what they cost and what they do, and is asked of one
database, because no endpoint reached so far answers that question at all.

**`Listed` is identity and nothing else** - id, label, family, and `upstream`. That is a refusal
rather than an omission. A gateway's list holds three shapes at once: a Claude arrives fully typed
with a capability block and token limits, a resold model arrives with all of that empty and the
upstream service's record forwarded in the extras, and GPT and Grok arrive as four fields saying
nothing. Reading each of those and filling the gaps from a database would put three kinds of card
on one page, where the facts shown depended on which wire answered. One source is worth more than
the coverage a merge would buy, so `Described` reads facts only from the reference.

`upstream` is the exception and is identity too: it is what the service actually serving a model
calls it (`accounts/fireworks/models/kimi-k3`), and it is the second of the two keys a record is
found under. It is not optional in practice - most of what a gateway serves is resold, and the
provider-and-model split alone finds none of it.

Three rules there are load-bearing:

- **The routed id wins over the upstream name.** A gateway that has taken a model over under its
  own key sets the terms the session is actually billed and limited by, so its record is the truer
  of the two.
- **A name two providers claim resolves to neither.** An aggregator republishes other people's
  models under its own key at its own markup, so a flat index over every id collides in the
  hundreds. Demanding uniqueness turns a wrong price into no price, which is the only safe way to
  be wrong here. `test_reference.py` pins this against a fixture where the collision costs 15x.
- **It can never stop the console starting.** This is `forge.offers`'s promise, not
  `catalogue.discover`'s refusal, and the difference is that nothing here can leave somebody
  holding a choice they cannot use. A reference that will not load costs a card its numbers.

`Described.consulted` is what decides whether a card with no record says so. With
`[model_reference]` absent nothing was looked up, so nothing is missing, and a marker there would
report the absence of a feature nobody turned on.

`format` in the config table exists so a second database is one more `ReferenceFormat` member and
one more arm in `parse_reference`, which `assert_never` makes the type checker demand. It stays a
value rather than becoming a plugin point.

## What a turn cost, and why it is recorded rather than looked up

**A response is priced before the step records it**, in `Stepping.price`, called from
`CheckpointedModel.request`. That is not where Pydantic AI does it: `fill_response_cost` runs in the
agent graph, which is *outside* the step, so left to it the cost reaches `turn:{n}:messages` and
never `turn:{n}:model:{i}`, and a turn being watched has no cost until the instant it ends. Priced
here it is in both, and `so_far` stays the prefix of `blocks_of` that the console depends on.

**Recorded rather than re-derived, and that does not contradict the one idea.** The rule is against
a copy kept in step with something that changes; what a turn cost is settled the moment the request
is answered and nothing will ever rewrite it. It is the fork's bargain rather than the catalogue's.
Re-priced on each render from a reference that has since moved, the same turn would show a different
number next month and two sessions would stop being comparable.

What it is **not** is authoritative. No wire reports what it actually charged, so this is an
estimate made immutable rather than a bill, and the page says so in a title attribute. `Stepping.price`
never overwrites an existing cost, which is how Pydantic AI's own filling is written too: the day a
provider reports what it took, its answer wins over any estimate of it.

**The reference and not `genai-prices`, and the coverage gap is why.** Pydantic AI's pricing knows
`claude-sonnet-4-6` and returns nothing for `accounts/fireworks/models/glm-5p3`, which is exactly
the resold population `Reference.upstream` exists to price. Live, that gap is a blank that fixes
itself when the database improves; *recorded*, it would be a permanent null. So recording is what
makes the coverage worth the threading.

`Prices` holds both holders rather than either's current value, because a pass lasts as long as a
session is being answered and both are reloadable configuration underneath it. `Prices.pricer(chosen)`
closes over the choice, since the only thing that varies request to request is the usage.

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

## Forking, and where a session may change its mind

A session's choice is fixed for life, so **forking is how it changes**. `Service.fork` copies every
recorded key belonging to a turn before the branch point into a new session, writes a new `choice`,
and records an `Origin` on the row. The tree in the sidebar is emergent from those origins; there is
no tree inside any checkpoint, and a session stays a flat run of turns.

Three things there are load-bearing:

- **A turn boundary is the only place a fork can happen**, and it is not a simplification. What a
  fork hands the next model is a conversation with no half-finished exchange in it, and that is only
  true between turns: inside one there is a call awaiting its result, or reasoning signed by the
  model that produced it, and neither survives being handed to another. So only a person's panel
  offers the link.
- **The branch point is *before* the forked turn's message.** That message comes across into an
  editable box and is asked again on the new model, because the usual reason to fork a turn is to
  see it answered differently and a fork that made you retype the question first would be answering
  a different one. Forking the *end* of a conversation has nothing to re-ask and waits instead.
- **The picker starts on the parent's own choice, not the configured default.** A fork is the one
  moment a choice *may* differ and deliberately not the moment it must.

The confirm page is a page rather than a control in the transcript, because the transcript is
re-rendered every time a turn in flight records anything: a picker per person panel would be
rebuilt under the reader's hand, and there would be one per turn.

## The workspace

A session that picked a repository gets **a git worktree of its own**, under `MAINPLATE_WORKSPACES`
(beside the database by default, and never inside any repository, which would put every session's
files in every other session's snapshots). A worktree apiece rather than one shared tree, because
two writers in one directory make a snapshot unattributable and the person is always one of the two.

`Settings.workspace_root` **resolves that path**, and it is not tidying. Everything under it runs
`git` with a `cwd` of its own: a clone is made from the clones root, a worktree is added from the
repository. Hand either a relative destination and git resolves it against *that* directory, so the
clone lands at `workspaces/clones/workspaces/clones/…` and the worktree lands inside the repository.
The idempotence checks then look at the path that was asked for, never find it, and let every pass
try again, which is a `SnapshotFailed` on a session's second turn. The default database is
`mainplate.db` in the working directory and `just serve`/`just demo` name one there too, so a
relative root is the common case rather than the odd one. Resolved at the setting because that is
where a configured path enters the process, and one absolute value cannot be got wrong by the next
consumer; `Clones` and `Worktrees` take an absolute root as a precondition. `test_snapshots.py` goes
the whole way from a `Settings` with a relative database, because the other fixtures there hand both
an absolute root and so would never notice.

There is deliberately **no setting naming a repository**. What a session works in is picked when it
is created and recorded on the session, so a process-wide answer would be a second answer to a
question each session already answers, exactly as a process-wide model would be. A session may
choose *no* repository, which is what this console was before there were any: a place to talk, with
no files.

`snapshots.py` captures a tree through a *shadow index*, so nothing a reader can see moves: not
their staged changes, not `HEAD`, not a branch, not `git log`. Four things there are easy to undo:

- **The index path is asked for, never assumed to be `.git`.** In a linked worktree `.git` is a
  file holding a pointer, and almost every workspace here is a linked one, so `staging` resolves it
  with `rev-parse --absolute-git-dir`.
- **A fresh index per operation, not one per workspace.** Two concurrent captures over one path
  write over each other, and the loser's `write-tree` then describes a tree that never existed - in
  practice the *empty* tree.
- **Trees are chained into commits under `refs/mainplate/snapshots`.** An unreferenced tree is
  unreachable and `gc` prunes it, so a bare `write-tree` would be a hash that stops resolving later.
- **Capture only where the agent is quiescent**, which means at a model-request boundary and not
  after each tool call. A model can issue several calls in one response and they run at once; while
  they do, `git add -A` walks a tree somebody is still writing to and records a mixture that never
  existed. Between one model request and the next, every tool of the previous batch has returned by
  construction. That is why `Stepping.snapshot` is called from `CheckpointedModel.request` and
  nowhere else: it is the one place in the process that stands at that boundary. A replayed request
  replays its snapshot too, so a later pass runs no git at all and the pair cannot drift.

Snapshots are **gitignore-aware**, deliberately. A rewind then restores what is version-controlled
and leaves the environment alone, which is what makes the motivating case work: a tool fails for
want of something installed, you install it, you go back to before the call, and the install is
still there. The cost is that an ignored path the agent itself wrote goes stale while the source
around it moves back, and it is the contract git already offers so nobody has to learn a second one.

**A fork's worktree is checked out at the tree the forked turn originally saw**, so a branch
re-asks its question against the files that question was asked about. Planting at the repository's
head instead would ask the new model to redo turn 3 against whatever the disk holds now, which is a
different question wearing the same words and invisible in the transcript.

The mechanism is one extra key rather than a checkout in a request handler: `Service.fork` copies
`turn:{at}:tree:0` across on its own, even though that turn's prompt and messages are *not*
inherited, and the fork's first pass plants at whatever tree is already recorded for the turn it is
about to run. The `:0` is the point: a turn now records a tree per model request, and what a fork
wants is the one before the turn did anything.

**A fork may attach a repository and may not swap one.** The two look alike and are not. Swapping
re-asks a turn against different files, which is a different question wearing the same words and
invisible in the transcript; attaching carries on with files where there were none, and the turns
being inherited were not asked against *other* files, they were asked against none. So a session in
a repository inherits it and its fork page renders no control, and a session in none is offered the
picker. `Service.fork` decides that rather than trusting what the form posted, which is what stops
a form with no repository field quietly moving a branch out of its repository - the bug that shape
of trust actually produced.

`Worktree.restore` is written and tested but nothing calls it yet: today a snapshot is a record of
what disk looked like, not something to go back to.

## How a model names a line

Every tool lives under `tools/`, one package per tool, as `tools/{name}/{module}.py`. Only the
constructor reaches the harness: `tools/__init__.py` exports `Files`, `file_tools` and `bash_tools`
and nothing else, so `agent.py` asks for the tools a workspace affords without knowing that editing
is anchored, that a worktree root has to be resolved against, or how a command is confined. A third
tool is a new package beside `files/` and `bash/` and one more name in that list, rather than an
edit to anything that already imports them.

Within the files one, `tools/files/anchors.py` is pure and `tools/files/tools.py` is the shell
around it, which is the split that lets the interesting half be tested with a list of strings. A
**Which tools a session gets is decided by its `isolation`, not by whether it picked a
repository.** A session on `WORKTREE` gets `list`, `read`, `edit` and `create` over its worktree
and its scratch; one on `EVERYTHING` gets the same four over `/`, where `list` refuses because
nothing there is in git; one on `NOTHING` gets **no toolset at all**, because tools that can only
fail are worse than none and cost a description on every request. `bash` is added to the first two
wherever there is a sandbox to run it in.

**`list` asks git rather than walking**, so a `.gitignore` is obeyed and a `.venv` or a
`node_modules` never reaches a context window. `git ls-files --cached --others --exclude-standard`
is the exact call, and each flag earns its place: `--cached` is what is committed, `--others` is
what the agent itself just wrote, and `--exclude-standard` is the ignoring. What comes back is
*flat*, one path per entry, because git records files and never directories; `catalogue` builds the
tree from those paths, which is also why an empty directory does not appear at all. `depth` bounds
the answer rather than hinting at it: a directory at that depth is summarised with a count instead
of opened, and `MAX_ROWS` is the backstop on a large depth over a large repository.

**`list` survives `bash` rather than being replaced by it**, because what it does is not listing. A
`git ls-files` in a shell returns every path in the repository, flat, into a context window; `list`
builds a tree from those paths, opens it only to `depth`, summarises a directory past that with a
count, and caps the whole answer at `MAX_ROWS`. That is context economy, and it is the difference
between orienting in a large repository for a few hundred tokens and doing it for tens of thousands.
The sandbox gives it a second reason to exist: `list` is a narrow tool whose argv this console
writes, so the question a model asks most often stays off the unbounded path.

What `bash` does *not* get a defence for is the at-least-once window. Anchored `edit` has one, since
a re-run edit fails loudly on anchors its own first run invalidated; an arbitrary shell command
re-runs silently. That is the cost of `step` rather than `transact` and it is unchanged by the
sandbox, which bounds where a command reaches and says nothing about how many times it runs.

**A line is addressed by a hash of its own content.** A line number is the one address that cannot
fail, so a stale one silently edits the wrong place; a content hash either resolves to exactly one
line or does not resolve, which turns that into a loud refusal. It also means the model never
retypes what it is replacing, which is the expensive half of a search-and-replace edit and the half
that lands in *output* tokens.

Four things about the scheme were measured against this repository rather than chosen, and the
numbers are the reason not to "simplify" any of them:

- **Four lowercase letters.** 26^4 expects about one collision per thousand distinct lines. Three
  base62 characters, which the published implementations of this idea use, collide 88% of the time
  over a thousand lines, which is why they need probe-based tie-breaking and then a persistent store
  to keep the probe order stable. One more character deletes that whole tower.
- **Letters, not digits.** OpenAI's tokenizer packs digit runs three to a token, so digits look
  ideal there; Qwen and StarCoder2 spend one token per digit, where a six-digit anchor costs three
  times as much. Four lowercase letters cost 2.4 to 3.1 extra tokens per line on every tokenizer
  tested.
- **A box-drawing `GUTTER` divides the anchor from the line, and it is worth the token per line it
  costs over a space.** A space is what a line of code is already full of, so `xhkm # mainplate`
  says nothing about where the name stops and the file starts. A model that guesses wrong writes the
  anchor back as content on its next edit, and every read after that shows a *fresh* anchor in front
  of the stale one, which confirms the guess and puts recovery out of reach. That is observed, not
  hypothetical: a session burned fifteen model requests on a one-line README that way. No ASCII
  character is safe here, since a plain `|` can legitimately open a line and costs the same 1.0
  token per line on `o200k_base` (0.9 on `cl100k_base`) as the box character does.
- **Blank lines get no anchor.** They are 17% of the lines here and *none* is unique on its own
  content, so they were the largest single source of both overhead and instability. Leaving them out
  takes the share of lines unique on their own content from 62.5% to 75.6% and cuts the lines
  needing three or more lines of context by 41%. They are still *rendered* with the gutter and the
  `UNADDRESSABLE` marker, so the column never breaks and no line of a read is parsed by a different
  rule than the one above it, which costs 2 tokens per blank line. Dashes rather than spaces there,
  at identical token cost: a run of spaces before the bar is invisible, so a deliberate "no anchor"
  would read the same as an anchor that went missing.
- **A duplicate line and a hash collision are the same problem**, so one rule answers both: where
  two lines share an anchor, extend each with the line before it and hash again. About 24% of
  anchorable lines need one line of context and 5% need two, capped at `MAX_DEPTH`; past that a line
  is inside a run nothing tells apart and gets no anchor, which the model routes around.

The cost of that last rule is the thing to know before changing it: an extended anchor depends on
its neighbours, so an edit just above one invalidates it. Measured here, a single-line edit
invalidates 0.59 anchors and 0.13 of those are more than five lines away. **Both are answered by
what the reply says rather than by making anchors survive changes they should not survive**: the
changed regions come back with fresh anchors, and any anchor that moved elsewhere comes back as an
explicit remapping. `written` has both tables in hand, so the remapping costs a comparison rather
than any state kept between calls. There is deliberately no store of anything.

`edit` takes a **list** of operations, resolved against one reading of the file and applied
together. That is the thing content addressing buys that search-and-replace cannot: overlap is
*decidable* before anything is written, so a contradictory batch is refused entire instead of
resolved in an order nobody chose.

Which lines a span covers is said by the **field name**, so there is no inclusive-or-exclusive flag
to get backwards: `from`/`to` are inside the span and `after`/`before` are outside it. One bound
alone inserts there, and only the exclusive forms may do that, since an inclusive bound with no
partner does not describe a span. The exclusive forms are also how a span reaches a blank line,
which has no anchor: deleting a function and the blanks after it is `from` its first line `before`
the next code line.

**Two calls at one file are serialised, because a batch of tool calls runs concurrently.** `Files`
holds a lock per resolved path and every operation takes the one for the path it touches. Without it
two `edit`s aimed at one file interleave: each reads, each computes against what it read, each
writes, and the loser's work vanishes while *both* calls report success to the model. Two `create`s
race the same way, both seeing a path that is not there yet, so `create`'s promise never to
overwrite quietly fails.

The lock is held around the whole read-modify-write rather than around the write, and that is what
makes it work: serialised that way the second call reads the first one's result, so anchors do the
job they were chosen for. An edit whose anchors the first one invalidated fails loudly; one whose
anchors still resolve lands. `create`'s existence check is inside the lock for the same reason.

It does not reach `bash`, whose paths are not knowable before the command runs, so a shell command
rewriting a file under an `edit` is outside what this can see. That is the same boundary snapshots
already draw when they capture only at model-request boundaries, where every tool of the previous
batch has returned by construction.

**Every tool that writes hands back anchors**, so a write is never followed by a read to find out
where anything now is. `create` renders the whole new file; `edit` renders the changed regions and
names any anchor that moved elsewhere. That is one property rather than two conveniences, and it is
what lets a run of edits happen with no re-read between them.

Two things are refusals rather than omissions. **There is no `write`**: a tool that overwrites a
whole file is the escape hatch that makes anchored editing pointless, since the first refused edit
becomes a full rewrite that discards whatever was not read. `create` is that same whole-file write
restricted to the one case where the objection does not apply - a path that does not exist yet has
nothing to discard - so `here.exists()` is the entire difference between the tool that is here and
the tool that is refused. It is also why there is no `delete` now that there is a `bash`: deletion
addresses nothing and returns nothing, so it never joins the anchoring scheme, and `rm` does it
exactly as well.
And **the formatter is not wired into `edit`**, which was tried and dropped: exclusive bounds fix
the addressing gap that made blank-line hygiene awkward, where a formatter would only have tidied
the symptom, at the price of a per-repository configuration decision on every write.

Everything a tool turns down reaches the model as a `ModelRetry`, because all of it is correctable
from the message: a stale anchor, a `find` occurring twice, a batch that overlaps. `RETRIES` is
above Pydantic AI's default of one for that reason, and the reason is observed rather than
theoretical - a smaller model got an operation's shape wrong once and the default turned a
correctable mistake into a failed turn.

`Text` carries the two things `splitlines` throws away, the line endings and the final newline, and
`tools/files/tools.py` reads and writes with `newline=""` so universal-newline translation does not
quietly normalise a CRLF file. Without both halves an edit to one line is a diff on every line,
attributed to an edit that touched one. It splits on `\n` and not with `splitlines`, which also
breaks on form feed - a page break some source files genuinely use.

## Where a command runs

`sandbox.py` is the boundary `bash` runs behind, and it is a **mount namespace** rather than a list
of commands that are allowed. A denylist over commands loses on contact with reality: `git stash`
reads as safe and reverts every tracked edit in the worktree, `git config` can set `core.hooksPath`,
and a release next year adds something nobody has classified. A mount says what a process can
*reach*, so it is already right about commands nobody has thought of, including whatever a
repository's own build script runs. `test_sandbox.py` pins that with `git stash` specifically.

**Per call, never a long-lived executor**, and the reason is replay rather than cost. A pass re-runs
the conversation body from the top and `wrap_tool_execute` replays recorded results instead of
re-running them, so a sandbox holding state between calls would offer that state on a first pass and
withhold it on a resumed one, with nothing to tell the agent which it is in. State that survives
*sometimes* is worse than state that never survives, because it invites reliance and then breaks
only under crash-resume. A fresh namespace costs a couple of milliseconds against a call that costs
hundreds, and leaves no process to supervise, reap, or reconstruct. The tool's own description says
nothing persists, and `test_sandbox.py` asserts it.

Five things about the policy are decided rather than incidental:

- **The clone is bound read-only, and that is the load-bearing half.** Every read still works -
  `ls-files`, `status`, `diff`, `log`, `blame` - while `add`, `commit`, `stash` and `checkout` fail
  on a read-only `index.lock`. What that buys is not tidiness: a git write from in there would be a
  second history that no panel shows, no fork inherits and no rewind restores, which is the second
  copy of state this whole console exists to refuse. Snapshots keep working because they run in the
  parent, where the clone is writable, so the agent physically cannot rewrite the history
  `refs/mainplate/snapshots` is chained onto. The invariant `snapshots.py` used to hold by being
  careful is now one no tool can break, including tools that do not exist yet.
- **The *common* directory is what is bound, not the worktree's own.** A linked worktree's `.git` is
  a file holding an absolute pointer into the clone, and the per-worktree directory sits inside the
  clone with a `commondir` pointing back out at it for objects and refs. So `--git-common-dir`
  reaches both and `--absolute-git-dir` reaches neither: bind the wrong one and there is no git in
  the sandbox at all, which silently takes `list` with it.
- **Both are bound at their own absolute paths**, never remapped to a tidy `/workspace`. That is
  forced by the same pointer being absolute. The alternative is a `GIT_COMMON_DIR` that every
  consumer has to carry and any subprocess is free to unset, bought for a shorter path.
- **The session binds come after `--tmpfs /tmp`.** bwrap applies arguments in order, so a workspace
  root that happens to live under `/tmp` is covered by the tmpfs and disappears if the binds come
  first, leaving a command that cannot change directory into its own worktree. That is not
  hypothetical: it is where every test in the suite puts a worktree.
- **`--unshare-pid` is teardown as much as isolation.** Killing the namespace's init reaps whatever
  the command left running, which is what makes the timeout and a cancelled turn leave no orphan
  build behind.

**A session gets a scratch directory, and nothing captures it on purpose.** `workspaces/scratch/
<session>` is bound read-write beside the worktree, so a build cache, a downloaded artifact or a
note to itself survives from one call to the next and from one turn to the next. That it is *not*
snapshotted is the same decision as snapshots honouring a `.gitignore`, arrived at one level out:
going back to before a call should not uninstall what was installed since. The cost is the one an
ignored path already carries, that what is in there goes stale while the source around it moves
back.

Outside the worktree rather than under it, and that is not tidiness. `list` passes `--others`, so a
directory inside the worktree is in every listing and every `git status` until something excludes
it, and the only place to write that exclusion is a git directory read-only wherever a command can
see it. `test_sandbox.py` pins this by asserting that nothing in the scratch reaches either.

It is made on the first command rather than when the session is planted, because bwrap will not bind
a source that does not exist and the tool is the one thing that knows a command is about to run. A
fork gets its own, empty: copying it would be copying mutable state, and sharing it would be two
sessions writing one directory. That matches the worktree, which a fork plants fresh at a recorded
tree and therefore without any ignored file either.

**`read`, `edit` and `create` reach it; `list` does not.** The point of extending them at all is a
plan or a notes file kept across turns, which is the one thing in a scratch directory that wants a
line editor; a build cache never does.

`Files` holds `roots`, a tuple of *typed* places rather than one path and a list of extras. The type
is what decides: a `GitTracked` is files a conversation is about and is the only kind git can be asked
about, so it owns `entries` and answers `list`; a `Scratch` answers no question git answers, which
is why it exists, so it carries no way to enumerate itself and `listing` refuses it in its own arm
of a `match` that `assert_never` closes. Adding a kind is one arm, and adding a *second worktree* is
one more element, where a `root` plus an `also` would have hardcoded exactly one.

`resolved` returns a `Located`, which is the resolved path **and** the root it landed in. Both
halves, because the caller needs both and working the second one out twice is how they come to
disagree: a tool holding one of these has proof the path is reachable and proof of what kind of
place it is, so nothing downstream re-asks either question. That is what turned `list`'s restriction
from a condition inside the tool into a property of the root.

The **first** root is where a relative path lands, and that stays well defined however many roots a
session ends up with. So a bare `notes.md` is about the repository, because that is what a
conversation is about, and anywhere else is reached by naming the absolute path the instructions
carry. Refusing `list` in the tool rather than leaving it to `entries` is the usual reason: "not a
repository" arrives from git as a `ListingFailed` fault and ends the turn, where a `Refused` tells
the model to reach for `bash` instead.

The file tools reach the scratch only where `bash` is offered, since without a command to make the
directory exist `read` would name a path nothing ever creates.

**Binding a port works; reaching it does not.** `--unshare-net` gives a namespace with loopback up,
so a command can start a server and curl it within one call, which covers integration tests. What it
cannot do is make that port visible to a person, and that is deliberate: a dev server somebody
watches is the harness's to run, outside the sandbox, not something an agent tool call should leave
behind. `Venue.CONNECTED` is the arm for that and nothing uses it yet.

Network is **off**, and off rather than allowlisted. An allowlist containing github.com contains
gists, one containing a package registry contains a package anybody can publish, and a DNS query to
`<secret>.attacker.example` leaves through any resolver that is allowed. It would buy a MITM proxy,
a CA inside the sandbox, and every tool that pins certificates breaking, for a defence against the
malicious-repository case and almost none against a determined injection. Landlock cannot help here
either: its network rules key on a *port*, never an address, and do not cover UDP at all.

`--clearenv` is what keeps the parent's environment out, and the credential is out of the sandbox
structurally rather than carefully: the agent loop that holds it stays in the parent and only the
command crosses.

**A missing sandbox is reported, not refused.** `open_console` resolves `bwrap` once and logs what
it found; without it a session keeps every file tool and is offered no `bash`, which is exactly what
this console was before there was one. That is `forge.offers`'s promise rather than
`catalogue.discover`'s refusal, and the difference is the usual one: nothing here leaves somebody
holding a choice they cannot use. It is logged because a shell tool that quietly is not there is the
state nobody can diagnose.

## Isolation, as two axes a session picks

`Choice.isolation` is an `Isolation`, holding a `filesystem` and a `network`, recorded once before
the first prompt and fixed for the session's life like the rest of the choice. Forking is how it
changes. One value rather than two fields spread across `Choice`, because they are answered
together, recorded together and read together by the one thing that builds a session's tools - and a
third axis, what a command may *spend* in a cgroup, lands as a member rather than as a parameter
threaded through four signatures.

**The two axes are independent, and `EVERYTHING` still being a sandbox is what keeps them so.** The
network switch is `--unshare-net` on the same namespace, the credential is kept out by `--clearenv`
and teardown is `--unshare-pid`, so an arm that dropped the sandbox would silently take all three
with it and make "the whole machine with no network" unrepresentable. `Sandbox.everywhere` binds `/`
read-write instead of a worktree and changes nothing else, which is why there is one `argv` rather
than two.

**The repository and the filesystem level are one question, asked once.** `workspace_cards` is the
group: every repository a forge reaches, plus `no files` and `this whole machine`. Picking one
settles `Choice.repository` and `isolation.filesystem` together, so they cannot disagree at the
source. `posted_workspace` is where one posted value becomes the two recorded ones, told apart
without a prefix because a repository's id is `forge:key` and so always holds a colon.

That is a correction rather than the first design, and the reason is worth keeping. They were two
groups, with the worktree level drawn greyed until a repository was picked. Keeping the two in step
then wanted a swap to refresh the greying, a fix so the narrowing box would not check a disabled
card, and a card in the completion list that could not be chosen - three pieces of machinery for one
answer stored in two places, which is the thing this console refuses everywhere else. **If a control
here starts needing to be kept in step with another control, that is the signal the two are one
question.**

**An enum whose members are recorded cannot be renamed freely, because the *value* is what is in
the store.** `Filesystem.WORKTREE` was `WORKSPACE`, and renaming the member changed the recorded
string too, so every session written until then became a page that answered 500. Nothing had been
released, so those records were only ever in a development database and the rename stands as it is.

**Where that is not true, the answer is a migration at startup, not a branch on the read path.**
`parse_isolation` describes the shape this console writes *now*; a retired value bridged inline never
goes away, and a file accreting them stops saying what the record is. Defaulting an *absent* field is
a different thing and stays: that is ordinary parsing of an optional, the way `thinking` is read, and
it is what lets every session written before `isolation` existed read back as what it already had.

`Isolation.settled` survives the merge and is still applied by `Service.start` and `Service.fork`,
because a fork's repository is *inherited* rather than posted and a form is not the only way in. What
it no longer has to do is correct the start page, which can no longer express a contradiction.
`parse_isolation` still takes a record as it stands and only *defaults* it, from `repository`, for
the checkpoints written before the field existed.

Whether the workspace can be chosen at all is the caller's answer, given to `picker` as `None` rather
than as an empty `Reachable`. The difference is load-bearing now that the group holds more than
repositories: empty means no forge reaches anything, which still leaves two answers worth offering,
where `None` means a fork already works somewhere and a control would be a lie about what the page
does.

The network sits under the workspace and above the endpoint, following the picker's order of
breadth: what a session's files are decides what it can touch, whether it can dial out decides what
it can do with them, and the endpoint and model only decide who answers.

**A session on `EVERYTHING` can read `config.yaml` and the store**, which is to say the credentials
and every other conversation. That is what choosing it means rather than an oversight, and the card
says so.

One thing is deliberately still to come. `GitTracked.entries` runs `git ls-files` in the parent
rather than through the sandbox, which is a narrower problem than arbitrary shell (its argv is ours;
the exposure is a malicious repository's git configuration) and a good next step.

## Durability

`StepwiseDurability` is a Pydantic AI capability on the *public* extension surface,
`AbstractCapability` plus `WrapperModel`. `pydantic_ai.durable_exec._base` and `_utils` are what
the bundled Temporal/DBOS/Prefect capabilities share, and their module docstring reserves them;
almost everything in them is about crossing a serialization boundary that does not exist here,
since `without-durability` runs the body in this process. Do not reach for them.

The capability finds its checkpoint through a `ContextVar` set by `stepping(run, prefix, workspace)`,
because no Pydantic AI hook carries one. Outside such a block it is transparent, which is what keeps
a durable-capable agent usable in a script or a test.

It wraps two things, `wrap_model_request` and `wrap_tool_execute`, and the second is required rather
than an optimisation. A tool that *reads* answers differently every time it is asked, so a pass that
re-ran one would resume the conversation against a file that moved since the model was told what it
said. A tool that *writes* has already written, and re-running it here fails against anchors its own
first run invalidated, which is a refusal for an edit that actually succeeded.

Two rules the mechanism asks for, both easy to break silently:

- **Effects live in steps; the code around them is pure.** A pass re-runs the body from the top,
  so anything between steps runs again. Nothing enforces this.
- **A step's key must be stable across passes.** `Stepping.key` numbers positionally within a turn,
  so a pass that issues its model requests in a different order finds the wrong records. Anything
  whose order is *not* fixed must use `Stepping.identified` instead; see the key scheme.

`Run.step` records what the *codec* takes, which is stdlib `json`: a value has to be JSON-native
going in, and comes back as an `object` needing a `Parse` on the way out. That is why every step
here pairs a `dump_python(..., mode="json")` with a matching parser, on the pass that ran it as
much as on the one that resumed. A tool return goes through `to_jsonable_python` and comes back
unnarrowed, because a toolset is unrelated functions with unrelated return types and there is no one
type to validate against; both passes see the round trip, so they agree.

This is `step` and not `transact`, so a tool is **at-least-once**: a crash between the tool
returning and the record landing re-runs it next pass. That window is one store round trip, and
anchored editing is what makes the failure mild rather than corrupting, since an edit whose anchors
no longer resolve is refused rather than applied somewhere wrong.

## The systemd unit

`install.py` renders and converges it. Three things there are load-bearing and easy to undo by
accident:

- **The unit names `sys.executable`, never `uv run`.** A `uv run` process holds a shared lock on
  the uv cache for its whole lifetime, so a service started that way blocks `uv cache prune` on
  the machine for as long as it is up, and `--no-cache` is not an escape for a package with
  dependencies. `sys.executable` is not resolved either: a venv's `bin/python` is a symlink to a
  base interpreter that has none of the venv's packages.
- **The restart is unconditional, the `daemon-reload` is not.** An upgrade in place renders
  identical unit text, so gating the restart on a difference would report success while leaving
  the old code serving.
- **The environment file is written once and never overwritten.** It holds the credential, so an
  install that rewrote it would delete the key on every upgrade.

There is no `Protect*`/`ReadWritePaths` hardening, deliberately, and the sandbox is the reason
rather than an exception to it. The agent edits repositories, so the paths it legitimately writes
are the worktree root and everything under it, which is exactly what a `ReadWritePaths` would have
to name: a unit sandbox loose enough to permit the worktree protects nothing. The boundaries that
actually hold are both *inside* the process and per session rather than per service, which is what a
unit setting can never be: `Files.resolved` for the file tools, and a mount namespace for `bash`.
That is also why the service is not itself confined - it holds the credential, the store, and every
session's worktree, all of which it needs, so the useful boundary is the one around what a model
asked for and not the one around the console.

## Dependencies

Built on [`without`](https://without.help), a workspace of small sans-IO libraries, and the
sibling checkout at `../without` is where its source is. Read the installed packages in `.venv`
rather than guessing at an API from memory; the same goes for `pydantic_ai`, which moves fast.

`uv` resolution has a 7-day cooldown (`exclude-newer`), with the `without-*` packages exempted in
`[tool.uv.exclude-newer-package]`. Adding a `without` package means adding it to that exemption
list too, or the whole graph gets held back to a release predating it. The cooldown is also why
`pydantic-ai-slim` is floored a release or two behind its latest.

`pydantic-ai-slim[anthropic,openai]` rather than `pydantic-ai`, which pulls every provider SDK, a
CLI, an MCP server, and an evals framework. Two extras and not more: between them the Anthropic and
OpenAI wires reach almost every gateway, and each further extra is a whole SDK. The `openai` one is
not free - it brings `openai`, `tiktoken`, `requests`, `urllib3`, `regex`, and `certifi` - which is
the price of the OpenAI-compatible half of a gateway being reachable at all.

## The console

htmx **4**, vendored at `assets/htmax.min.js`, which reads very differently from htmx 2: explicit
`:inherited`, lowercase colon-separated event names (`hx-on:htmx:after:swap`), `hx-status:` in
place of `responseHandling`, and every status swapping except `204` and `304`.

**`htmax` and not `htmx`, and the extensions are gated by a meta tag.** `htmax` is core plus every
bundled extension in one file, taken because one file cannot drift from itself: core and an
extension vendored separately are two files that have to be kept on one version, and the failure
when they are not is a swap that silently misbehaves rather than an error anybody sees. The price
is ten extensions this console does not want, several of which would change how a page behaves
just by being included - `history-cache` puts back the history store htmx 4 deliberately removed,
and `hx-live` and `alpine-compat` are reactive scripting. `<meta name="htmx-config" content=
"extensions: ...">` is the allowlist, read before any of them register, so a name absent from
`EXTENSIONS` in `pages.py` is never installed rather than installed and unused.

Pages are `without-html` node trees, pure functions of already-answered questions. A page and the
fragment inside it are the same function called at two depths, which is what stops the two
renderings from disagreeing.

**A page holds one connection and the transcript carries no `hx-` attribute of its own.** The
region neither asks for itself nor decides when to: `streaming.py` sends it down the page's event
stream whenever the session records anything. That deletes a whole class of bug rather than moving
it - a trigger on a region that is itself replaced had to be `every` and never `load`, because
morphing keeps the element and a `load` poll fires exactly once and then waits forever on an answer
that already arrived, invisibly to any markup assertion. A region with no trigger has nothing to
get wrong.

Three things about that connection are decided rather than incidental:

- **It lives outside everything that swaps**, directly under `body`. Held by the transcript it
  would be a connection its own traffic kept tearing down. Its target is itself with `innerHTML`,
  so it is an inert sink: every message is `<hx-partial>` elements naming their own targets, which
  htmx applies while leaving the connecting element alone, and anything else lands somewhere
  harmless rather than over the conversation.
- **Every message is a whole current render, never a delta.** So a reconnect needs no replay and no
  cursor, a dropped frame costs nothing, and a duplicate morphs to a no-op. It is also why the
  first thing a stream sends is the current state: what a page that has just connected needs and
  what one connected for an hour needs are the same thing.
- **The server notices by polling a change token**, not by being told. `Service.token` counts a
  session's recorded steps, which is sound because a checkpoint is append-only and cheap because it
  decodes none of them. The two halves of the process stay joined only by the store, exactly as
  they would be if the worker were elsewhere.

The transcript still swaps with **`outerMorph`**, and that is what lets a turn be watched: a turn
records several times while it runs, so a replacement would shut a call the reader opened to watch,
over and over, precisely while they were reading it. `test_browser.py` pins that against a real
Chromium and a real server, because it is a second render reaching a page nobody reloaded and
neither a still nor a markup assertion can see one.

**A panel that arrives or changes is marked for a beat** (`data-fresh`, a colour fade in the
panel's own kind hue), because a re-render that lands silently leaves the reader to spot what
moved. Two things there are decided:

- **The change is worked out here, not taken from the swap.** Morphing reports nothing a listener
  can hear: `htmx:before:morph:node` is an extension hook rather than a DOM event, and it fires
  before htmx has decided whether the node differs. So `mainplate.js` keeps a signature per panel.
- **The signature is the *text* of a panel's blocks.** A reader unfolding a call, a search mark laid
  over a word, a kind switched off in the key: all change a panel's markup and none is news. It is
  blocks rather than the whole panel because the `recorded` disclosure is `hx-preserve`d, so what a
  reader fetched into it survives every swap and would otherwise read as the panel having changed.
  The first render marks nothing, since every panel is new to the script then and a conversation
  flashing top to bottom points at everything.

**Following the end is being at the end**, decided in both directions by where the reader has
scrolled, and re-entered by sending a message. `land` therefore has to route its scroll through
`scrolling()` like `toEnd` does: without it, landing on the *last* panel puts the reader at the
bottom and the scroll listener switches following back on at the very moment they asked to be
somewhere in particular.

The rail (search, key, dock, theme) lives **outside** the region that swaps, so no control is
rebuilt under a reader's finger. What it projects back *onto* the transcript — search marks, the
panel landed on, which kinds are set aside, which calls are unfolded — cannot live in the markup
either, so `assets/mainplate.js` holds it as values and reapplies it after every swap. That
projection is one idempotent `repaint()` serving the first render, every swap, and every press.
Everything it drives is an enhancement: with the file absent the page still renders, posts, and
folds.

**The console has three shapes and one place that decides between them.** Over 78rem the shell is
three columns and the rail stands beside the conversation; between 48rem and 78rem it is two, with
the rail lying over the page and drawn shut behind its clasp; under 48rem it is one, and the session
list becomes a strip of chips across the top. Every phone rule is in one block at the *end* of
`mainplate.css`, and that is not tidiness: the queries overlap, so the narrow one wins only by
coming later. Split up, the rail's own `max-width: 78rem` block sat below the narrow one and put the
17rem sidebar column back on every phone, leaving the conversation about a hundred pixels to render
in - a bug invisible in either rule and obvious with both in one list. So a rule that changes shape
on a phone goes in that block; a rule that applies at two widths (the rail's overlay) stays with the
thing it is about.

A strip rather than a shorter band, and the axis is what decides it: a band is a second *vertical*
scroller stacked on the transcript's own, and two of those on one axis is what feels broken under a
thumb. What a chip gives up is the date, the repository and the tree's indentation, which are for
telling sessions apart where a strip is for getting back to one; the fork marker stays.
`toCurrentSession` brings the session being read into that list and is deliberately shape-blind:
`nearest` scrolls the list on whichever axis it actually scrolls on, so one call serves the strip
and the full-height column both.

Two more things change on a phone, and both follow from it having one column of room. **Nested
same-axis scrollers go away**: the wide picker has the models scrolling inside a block that scrolls
inside the page, which is what keeps the endpoints and the settings put beside a seventy-model list,
and on a phone it bought a list thirty pixels tall. So the models stop scrolling *and* nothing in
the picker shrinks - the `flex: none` is the half that is easy to miss, since every `min-height: 0`
above exists to let a part give way, and with nothing left to scroll that permission just squashes
the list and draws the rest of it over what follows. **And nothing on the transcript is revealed by
hover any more**, which is the same lesson taken one step further than a `@media (hover: none)`
override: the branch link used to appear on a person's panel under the pointer, so on a touch
screen forking did not exist until a media query put it back. On the rule it is simply always
drawn, and there is no pointer question left to answer.

**The picker is ordered widest-first: workspace, network, endpoint, model, thinking**, and then the
name and the message box, which are the composer's rather than the picker's. What files a session
has is the broadest thing about it and is one question rather than two, so it leads; the network
follows because it is the other thing deciding what the agent can do at all, where the endpoint and
the model only decide who answers; the endpoint and the model are adjacent because they are a pair,
the list being whatever the endpoint above it offers; the thinking level is a setting *on* the
model, so it sits under it.

**Every question the picker asks is one component.** `choosing` in `pages.py` takes a legend, a
toggle id, the names on offer and a body of cards, and gives back a group that folds to what is
picked, says how many options it has, and can be narrowed by typing. All five questions - the
workspace, the network, the endpoint, the model and the thinking level - are built from it, and that
is why none of them is a `<select>`: a select renders its options as text in every browser, so it
could carry neither the forge a repository came from, nor the sentence under a level that is not
one, nor the fold. Having two kinds of control answering versions of one question was the thing to
remove.

A sixth question is a `choosing` call and nothing else. The script names none of the card classes
- it finds a card structurally, as a `<label>` with a radio in it, or by the `data-name` the card
declares - so a new kind of card needs no edit there. The one selector it does name is
`.models__provider`, which is not a card but the heading over a run of them. The CSS is the one
place a new kind is listed by name, because the shared card rules are a grouped selector, so a card
added without being added there is drawn unstyled and, worse, does not fold.

Four things there are decided:

- **The fold is a checkbox and the folding is `:has()`, so nothing in the script decides it.** That
  is what keeps a shut group honest: what it draws is the card whose radio is actually checked, read
  off the radio, so there is no second copy of the choice to go stale. A summary line naming the
  model was the obvious alternative and is exactly that copy - with scripting off it names the wrong
  model from the first pick onwards. Everything the script does to a group - shutting it on a pick,
  narrowing it, checking a card the reader named - *sets* state and never reads the choice back out
  to decide anything.
- **The count of what is on offer is on the control** (`27 options`), because a shut group is one
  card and a card on its own reads as a fact about the session rather than a choice. It has to be
  rendered *inside* what the endpoint swap replaces, which is why `model_cards` returns the whole
  group, head and fold included, rather than just the list: outside it, the count keeps saying 27
  after the list under it became 46.
- **A swap resetting the fold is the right state to arrive in.** Picking an endpoint replaces the
  model group, so it comes back shut on that endpoint's default model, which is a real choice
  already made and worth seeing rather than a list to close.
- **One argument is the count, the completions and the filter**, so the three cannot disagree.
  `names` is one entry per card: passing a model's label *and* its routed id was two entries each
  and a group announcing 54 options over 27 cards. Searching by id still works, because the filter
  matches a card's whole text and the id is printed on it - the `<datalist>` is the readable half
  and a list of names interleaved with `anthropic/claude-sonnet-4-6` is not that.

The narrowing box is the one control on this page that does nothing without the script, and it is
drawn that way deliberately: the `<datalist>` beside it is the browser's own completion over the
same names, so with the file absent typing still helps and every card is still there to be picked.

**Naming one exactly is choosing it.** Taking an entry from the completion menu puts the whole name
in the box, and that is the reader having decided, so the card is checked and the group shuts rather
than leaving them to reach for the one card still showing. The match is on the whole name and never
a prefix, which is what stops the keystrokes spelling `xhigh` from stopping at `high`; every card
carries the name it answers to as `data-name`, the same string that went into the completion list.
Checking a radio from script fires nothing on its own, so the pick dispatches a real bubbling
`change` - which is what shuts the fold *and* what lets an endpoint's own `hx-get` swap the models.

**A sticky heading is measured from the scroll container's *content* edge, so that container gets no
block padding.** `.setup` carried `padding-block` and `.picker` now does, which looks like moving a
value between two elements that fill each other and is not: on a phone `.setup` is the box that
scrolls, so padding on it parked the sticky provider heading that far down the box and left a strip
above it with model cards sliding through. Inside, the padding scrolls away with the content, which
is what it was always for.

The picker's controls are **associated with their form by name, not by nesting**, and that is
load-bearing on the start page. There the choosing fills `main`'s growing row and the box is pinned
under it, so every radio in every group is a *sibling* of the form that posts them;
`form="choosing"` (`CHOOSING_ID` in `pages.py`) is the whole of what makes them submit, and without it
the console refuses its own page with a 422 saying a message needs an endpoint and a model. The fork
page nests its picker inside a form of the same name, so `model_cards` can carry one attribute and
serve both the pages and the `/fragments/models` swap. The fold's own checkbox is the one control
that deliberately carries *neither* a `name` nor a `form`: it is how a group is looked at, not part
of what a session is decided by. A markup assertion cannot see any of this, which is why
`TestWhatAFormPosts` asks a browser what `form.elements` holds and `TestFoldingAGroupOfCards` asks
what a shut group still posts.

**Shift-Enter sends and plain Enter breaks the line**, which is that way round because a message here
is prose that wants paragraphs and fenced blocks: a box where the obvious key sends is a box you
cannot write one in. `wireSend` calls **`requestSubmit`** and not `submit`, and that is the whole of
why one delegated listener serves every page: `submit()` posts *without* dispatching a `submit`
event, so htmx would never see a send on a session page and the browser would navigate away from the
conversation instead. It also runs the form's own validation, so an empty box refuses from the
keyboard exactly as it refuses from the button. The Send button names the key, because a shortcut
nothing on the page mentions is one nobody uses.

A message is **rendered Markdown, then sanitised**, in `markup.py`. Both halves are required.
Python-Markdown passes raw HTML through untouched and never looks at URL schemes, so
`<script>alert(1)</script>` and `[x](javascript:alert(1))` reach the page from a plain `convert`;
`nh3` is what stops them. Do not drop it because the text "comes from the model": a reply is shaped
by whatever was pasted into the box, and this project's direction is an agent that reads
repositories.

Fenced code is highlighted, and the sanitiser is where that gets interesting. `codehilite` emits
classes, `nh3` strips `class` by default, and the fix is **`allowed_classes` with Pygments' own
`STANDARD_TYPES` vocabulary** rather than allowing the attribute. The difference is the whole point:
this text is shaped by whatever reaches the box, so an open `class` would let a reply paint itself
as the rail, the composer, or a panel of somebody else's kind. Taking the vocabulary from Pygments
rather than listing it also means a token a new release emits is allowed the day it appears, where a
hand-written list would strip it silently and that run of code would render unhighlighted with
nothing saying why. `guess_lang` is off: a wrong guess colours text by a grammar it is not written
in, which reads worse than no colour. The palette is the console's own hues in `mainplate.css`, not
an imported Pygments theme with its own opinion about light and dark.

A turn is read out of the checkpoint as **panels of blocks**, not as a question-and-answer pair.
A block is prose, reasoning, or a call with its result; a panel is a run of blocks of one kind, and
it is what the page draws a coloured edge down. The palette runs on one axis and every kind takes
its side from it: cool is what reached the model (the person), warm is what the model produced (its
answer, its reasoning drawn back toward the ink, a call in ochre). A kind added later has its hue
decided by that rather than chosen for it. A part kind `parted` has no rendering for is passed
over rather than refused, because the provider and Pydantic AI are both free to add one.

**A panel says what is in it; a rule says what is true of the turn around it.** Which turn it is,
where the session may be forked from, the worktree the turn started on, and what it spent all belong
to the exchange rather than to any one run of blocks, and hung on a panel they had to be hung on a
chosen one. Usage settles it: it belongs to a `ModelResponse`, and one response becomes as many
panels as it has kinds of part while two responses can merge into one panel, so there is no
attribution rule from a response to a panel that is not invented. `transcript_region` groups the
panels by `turn` and draws the rule from that, so nothing keeps a second list of where turns begin,
and the tree comes off the turn's first panel, which is always the person's.

**A rule names its turn, and that is legibility rather than decoration.** It sits directly under the
last panel of the turn before it, so a bare row of figures there reads as a footer summarising what
is *above* it, which is the opposite of what it says. The `#1` against the `#1.0` on the panels below
settles the direction, and doubles as the permalink to the boundary the fork acts on.

The dock's left column steps rules rather than the person's panels, and that is a removal. There is
exactly one message per turn, so a "previous message of yours" column and a "previous turn" column
visit the same positions and differ only in where they stop: two controls answering one question,
which is the thing this console removes wherever it finds it. Every arrow now declares what it steps
over (`data-stop`) rather than being told apart by what it lacks, because a button identified as
"the one with no side" stops being identifiable the instant a second kind of stop exists.

Every **settled** panel carries a `recorded` disclosure showing the JSON the checkpoint holds behind
it, and four things there are decided rather than incidental:

- **Nothing is stored per panel**, so the panel and its record have to come out of one walk.
  `parted` is that walk and hands out `Source` indices as it goes; `runs` is the one grouping rule
  `panelled` and `sourced_at` both use. Recovered by a second pass the indices would be a guess at
  what the first did, and the skipping in `parted` is exactly what makes that guess wrong from the
  first unrenderable part onwards - every panel after it would show somebody else's record.
- **Indices into the stored value, never the parsed part dumped again.** A round trip states what
  today's Pydantic AI would write, which agrees with the record right up until a release renames a
  field and then disagrees silently. A person's panel is the exception and is one whole key,
  `turn:{n}:prompt`.
- **Fetched on demand and `hx-preserve`d.** A running turn re-renders the transcript repeatedly, so
  the raw record of every panel is not something to carry in it; and because the server renders the
  disclosure closed, a morph takes the `open` attribute back off unless the element is preserved.
  htmx reads `hx-preserve` off the *incoming* markup, so taking it off the live node proves nothing.
- **`Panel.settled` is what decides a panel offers one at all**, and `once` is what makes it cheap.
  A panel of the turn in flight is read from that turn's *steps*, where `sourced_at` answers out of
  its messages, which are not written until the turn ends: offering the disclosure there would
  fetch nothing, once, and keep the nothing. It appears when the turn lands.

Tests drive the app through `without-http`'s in-memory loopback client (`tests/calling.py`), so
nothing binds a port and the suite parallelizes; `Caller.watching` consumes a real event stream
through the same encoder and decoder a socket would, which is how the console tests see the
transcript with no document around it now that no endpoint serves one. The `app` fixture
deliberately runs the console over a store with **no worker**, so a test asserting on a pending turn
cannot race one; what the worker does is tested in `test_conversation.py`, a pass at a time.

`test_browser.py`'s `console` fixture is the exception that binds a port, and it has to: what a
live connection must prove is that a *second* render reaches a page nobody reloaded, which needs a
real server, a real Chromium, and a test writing steps into the checkpoint while the browser
watches. It has no worker either, for the same reason and one more: writing the steps by hand is
the only way to hold a turn half-finished long enough to assert on it.
