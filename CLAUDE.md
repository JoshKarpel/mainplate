# CLAUDE.md

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows.
[`README.md`](README.md) is what it does and why; this is what to know before changing it.

## Commands

```console
$ just setup            # uv sync + install pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_console.py::TestTheConsole  # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve            # foreground, on port 8101 so it never fights the installed service
$ just demo             # the same, on a database of its own, for poking without touching real sessions
$ just seed             # the gallery's fixtures into that database, so there is something to click
$ just shots            # render every page and screenshot it, wide and phone, into build/shots
$ just browse           # drive the console in a real Chromium and assert on what it does
$ just install          # this checkout as a user systemd unit, on the default port 8100
$ just logs             # journalctl --user -u mainplate -f
$ just uninstall        # removes the unit, keeps the environment file and the database
```

`just serve` and `just install` are on different ports on purpose, so a foreground run for a quick
look never takes down the service. `just install` runs `uv sync` first, and that is not a
convenience: the unit names this checkout's interpreter, so an install from a stale environment
points systemd at a venv missing whatever was just added.

**The stylesheet is a deliverable, and no string assertion checks one.** `just shots` renders every
page from fixture checkpoints and drives a real Chromium over them, so a styling change can be
looked at rather than argued about. It needs no server, no database, no provider and no
`config.toml`, because a page is a pure function of already-answered questions: `scripts/gallery.py`
answers them with fixtures and the assets are copied beside the output, so a static server renders
what the console renders. Every shot asserts the document never scrolls sideways, which is how the
`:target` rule that widened a panel past its container was found.

`just browse` is the other half and asks a different kind of question. What a still cannot show is
that *two* panels are drawn as where the reader is, so behaviour gets its own checks in
`scripts/check-landing.mjs`. Both are dev tools: the console's own script is served from
`assets/` and depends on nothing, and `package.json` exists only for Playwright.

Run `just test` or `just check` before saying anything is done. CI runs the same pre-commit
configuration, so there is one definition of what the checks are.

`pytest` runs under `xdist` (`-n auto`), `pytest-randomly`, and a 10-second per-test timeout, all
from `addopts`. A test that needs longer raises it with `@pytest.mark.timeout(...)` rather than
changing the global.

## The one idea

**The checkpoint is the conversation.** There is no messages table, no session state in the
server, and no cache. A page renders `checkpointer.load(session)`, a crash resumes from the same
rows, and two tabs agree because they are reading the same thing.

Anything that would keep a second copy of what was said is the change to push back on. The
session index (`sessions.py`) is the one row per session that exists, because
`without-durability` cannot enumerate workflows; it holds a title as well as an id, and that is
*not* a copy of changing state, because a session is named after its first message and nothing
ever renames it.

The model catalogue (`catalogue.py`) is the one piece of process state that genuinely changes under
a reader, and it is not an exception either: nothing in it is anything anybody said. It is
configuration that happens to live at the far end of a request rather than on disk, so it is
handled the way reloadable configuration is, and a page render reads it out of memory rather than
asking a gateway. The test for a change here is the same one: does it keep a second copy of what
was *said*?

The console's `localStorage` is the last thing that looks like an exception and is not. It holds
only what a *reader* decided (the theme, which kinds are set aside, which calls are unfolded,
whether they are following the end), never a word of the conversation, so a browser with it wiped
renders exactly what one without it does. It is keyed by session id, and that scoping is
load-bearing rather than tidy: every session shares one origin, so an unscoped key would be one
conversation's folds imposed on all of them.

A **fork** is the one thing here that copies what was said, and it is not the exception it looks
like. The rule is against a copy that has to be kept in step with something that changes; a fork
copies turns that are already settled and that nothing will ever rewrite, so the two sessions are
two values that happened to be equal rather than two views of one thing. It is also what keeps a
fork readable on its own, since each session's checkpoint stays the whole of its own conversation
with nothing to dereference.

## The key scheme

One key for the session and four per turn, written by five different places and read by four:

```text
choice               the profile, model and thinking level; written by `Service.start` and by
                     `Service.fork`, before the prompt
turn:{n}:prompt      the person's message; written from outside a pass, by `Service.say`
turn:{n}:tree        the worktree that turn started on; written by the conversation body
turn:{n}:model:{i}   the i-th model response of that turn; written by `StepwiseDurability`
turn:{n}:messages    what the agent run produced; written by the conversation body
```

`choice` goes in before the first prompt and never again *within a session*. The order is
load-bearing: the prompt is what *queues* a session, so writing it first would let a worker take the
session and find no profile to answer on. Never again, because a session that changed endpoint
halfway would replay recorded answers from one and continue on another. Forking is how the choice
changes, and it changes it by making a different session rather than by rewriting this one.

`turn_of` is the inverse of `turn_prefix`, and it answers about the key's *shape* rather than
against a list of known kinds. That is what lets `before` carry a whole prefix of a conversation
into a fork without being taught each new kind of step: a `turn:3:tool:0` nobody has written yet is
turn 3 already.

They all live in `conversation.py` so that the code writing them and the functions reading them
(`choice_of` and `reached` for the body, `transcript` for the page, `before` for a fork) cannot
drift apart. The tests in `test_conversation.py` assert the shape against literal recorded values
rather than round-tripping through the writer, so a change to the scheme has to be made in both
places.

## Profiles, discovery, and per-session auth

A **profile** is an endpoint, a wire format, and a credential. The models are separate and are not
in the file at all: `catalogue.py` asks each endpoint's own model-list API what it serves. A session
records a profile, a model and a thinking level, and is bound to all three for life.

The thinking level lives in `thinking.py` rather than beside `Choice`, and the reason is a cycle:
`profiles.py` has to validate a configured name and `agent.py` already imports `profiles.py`. What
is left is a small shared vocabulary three layers read. Its effort names are recovered from Pydantic
AI's `ThinkingEffort` rather than restated, so a level that library adds reaches the picker without
an edit. The three that are not efforts are spelled out because they are not gradations of one
thing: `None` leaves the setting off the request, `False` asks for thinking off, and `True` asks for
it at the provider's own budget, which on the Anthropic wire is no parameter, an omitted block, and
ten thousand tokens.

`provider` names the wire rather than the vendor, because one hostname often answers both and each
reaches models the other does not. It also decides what `base_url` means: the Anthropic SDK appends
`/v1/messages`, so it wants the host; the OpenAI SDK appends `/chat/completions`, so it wants the
host and `/v1`. On exe.dev that is why `install` writes two profiles for one gateway.

`agent.py` holds one class per wire, and it holds *both* wire-specific things: how to name a model
over it and how to ask it what it serves. A third wire is one class, not an edit in three files.
`chat_models` is the pure half of the OpenAI side and is where its two exclusions live: exe.dev
publishes every OpenAI model twice (bare and prefixed) and mixes embedding models in with chat
ones. The embedding rule is a rule over names because that list carries no capability to ask;
`test_catalogue.py` pins both against the shapes a live gateway actually returns.

`profiles.py` parses `config.toml` into `Config`, once, at startup. Two things there are easy to
undo by accident:

- **Credentials are `SecretStr` and come from the file, not the environment.** A key handed to
  `AnthropicProvider(api_key=...)` never becomes an environment variable. `Profile.key` is the
  one place that decides between a configured key, the `KEYLESS` placeholder for a gateway that
  authenticates at its edge, and `None`, which is what leaves the SDK reading its own environment
  variable for itself. Do not "simplify" that `None` away.
- **`build_endpoints` is eager**, so a profile that cannot be built fails at startup naming itself
  rather than on whichever session first chose it. It builds the *provider* and not a model per
  name, which loses nothing: an SDK validates neither, so the eager build was only ever buying
  endpoint validation. That is also why the service refuses to start on an unusable `config.toml`,
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

- **Starting** a session asks `Catalogue.offers(profile, model)`. That is form validation: a select
  is a suggestion the page made, not a constraint on what can be posted, so a new session may only
  be created on a pair the picker actually offered.
- **Answering** one asks only whether the *profile* exists, in the worker (`agent_for` raising
  `UnknownChoice`) and in `Conversation.answerable` (`models_of(...) is not None`), which are
  deliberately the same question so the page and the worker cannot disagree. The model is not
  checked: the provider's own refusal is the authoritative answer about a model and it arrives on
  the turn, where gating here would strand a conversation nobody broke.

A session whose profile is gone renders with a sentence naming it and no poll, because a spinner
that will never resolve is the one state a person cannot diagnose. `test_console.py` pins both
halves, including that a session on an unlisted-but-routable model keeps its poll.

`exe.py` is the exe.dev half, and it answers reflection twice over: which LLM gateways are attached
(so `mainplate install` writes keyless profiles) and which GitHub repositories are (so a session
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
  which refuses a profile listing no models: that is a profile you can select and then cannot use,
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
session's and every worktree is a linked one off a shared object store.

None of this is wired into the app yet: nothing constructs a forge, and `MAINPLATE_REPOSITORY`
still names a local checkout. The picker, recording a repository on a session, and cloning from the
worker rather than from a request handler are the next steps.

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
replaced once a second while a turn is in flight: a picker per person panel would be rebuilt under
the reader's hand, and there would be one per turn.

## The workspace

With `MAINPLATE_REPOSITORY` set, **every session gets a git worktree of its own** under
`MAINPLATE_WORKTREES` (beside the database by default, and never inside the repository, which would
put every session's files in every other session's snapshots). A worktree apiece rather than one
shared tree, because two writers in one directory make a snapshot unattributable and the person is
always one of the two.

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
  construction.

Snapshots are **gitignore-aware**, deliberately. A rewind then restores what is version-controlled
and leaves the environment alone, which is what makes the motivating case work: a tool fails for
want of something installed, you install it, you go back to before the call, and the install is
still there. The cost is that an ignored path the agent itself wrote goes stale while the source
around it moves back, and it is the contract git already offers so nobody has to learn a second one.

**Forking checks the new worktree out at the tree the forked turn originally saw**, so a branch
re-asks its question against the files that question was asked about. Planting at the repository's
head instead would ask the new model to redo turn 3 against whatever the disk holds now, which is a
different question wearing the same words and invisible in the transcript.

`Workspace.restore` is written and tested but nothing calls it yet: today a snapshot is a record of
what disk looked like, not something to go back to.

## Durability

`StepwiseDurability` is a Pydantic AI capability on the *public* extension surface,
`AbstractCapability` plus `WrapperModel`. `pydantic_ai.durable_exec._base` and `_utils` are what
the bundled Temporal/DBOS/Prefect capabilities share, and their module docstring reserves them;
almost everything in them is about crossing a serialization boundary that does not exist here,
since `without-durability` runs the body in this process. Do not reach for them.

The capability finds its checkpoint through a `ContextVar` set by `stepping(run, prefix)`, because
no Pydantic AI hook carries one. Outside such a block it is transparent, which is what keeps a
durable-capable agent usable in a script or a test.

Two rules the mechanism asks for, both easy to break silently:

- **Effects live in steps; the code around them is pure.** A pass re-runs the body from the top,
  so anything between steps runs again. Nothing enforces this.
- **A step's key must be stable across passes.** `Stepping` numbers requests positionally within
  a turn, so a pass that issues its model requests in a different order finds the wrong records.

`Run.step` records what the *codec* takes, which is stdlib `json`: a value has to be JSON-native
going in, and comes back as an `object` needing a `Parse` on the way out. That is why every step
here pairs a `dump_python(..., mode="json")` with a matching parser, on the pass that ran it as
much as on the one that resumed.

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

There is no `Protect*`/`ReadWritePaths` hardening, deliberately: this project's whole direction is
an agent that edits repositories, so a sandbox written for today's no-tools console would be wrong
at the first tool, and one loose enough to survive that protects nothing.

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

htmx **4**, vendored at `assets/htmx.min.js`, which reads very differently from htmx 2: explicit
`:inherited`, lowercase colon-separated event names (`hx-on:htmx:after:swap`), `hx-status:` in
place of `responseHandling`, and every status swapping except `204` and `304`. That last one is
why the poll carries `hx-status:4xx="swap:none"`: a refusal swapped into the region would replace
the conversation *and* take away the trigger that would have recovered it.

Pages are `without-html` node trees, pure functions of already-answered questions. A page and the
fragment inside it are the same function called at two depths, which is what stops the two
renderings from disagreeing.

The transcript swaps with **`outerMorph`**, and two things follow from that:

- **The poll is `every 1s`, never `load`.** A `load` trigger fires once per element *load*, so it
  repeated only because each answer replaced the region. Morphing keeps the element, so a `load`
  poll fires exactly once and the conversation then waits forever on an answer that already
  arrived, with nothing on the page saying so. The failure is invisible to a markup assertion,
  because the markup is identical either way; `test_console.py` pins the trigger for that reason.
- **A reader's own changes survive an answer arriving.** Morphing merges rather than replaces, so
  an unfolded tool call, the caret, and a scroll position are not thrown away once a second. The
  server still renders the whole conversation from the checkpoint, which is the property worth
  keeping: the swap got cleverer, not the endpoint.

The rail (search, key, dock, theme) lives **outside** the region that swaps, so no control is
rebuilt under a reader's finger. What it projects back *onto* the transcript — search marks, the
panel landed on, which kinds are set aside, which calls are unfolded — cannot live in the markup
either, so `assets/mainplate.js` holds it as values and reapplies it after every swap. That
projection is one idempotent `repaint()` serving the first render, every swap, and every press.
Everything it drives is an enhancement: with the file absent the page still renders, posts, polls,
and folds.

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
decided by that rather than chosen for it. A part kind `blocks_of` has no rendering for is passed
over rather than refused, because the provider and Pydantic AI are both free to add one.

Tests drive the app through `without-http`'s in-memory loopback client (`tests/calling.py`), so
nothing binds a port and the suite parallelizes. The `app` fixture deliberately runs the console
over a store with **no worker**, so a test asserting on a pending turn cannot race one; what the
worker does is tested in `test_conversation.py`, a pass at a time.
