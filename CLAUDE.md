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

**The stylesheet is a deliverable, and no string assertion checks one.** `just shots` renders every
page from fixture checkpoints and drives a real Chromium over them, so a styling change can be
looked at rather than argued about. It needs no server, no database, no provider and no
`config.yaml`, because a page is a pure function of already-answered questions: `scripts/gallery.py`
answers them with fixtures and the assets are copied beside the output, so a static server renders
what the console renders. Every shot asserts the document never scrolls sideways, which is how the
`:target` rule that widened a panel past its container was found.

`tests/test_browser.py` is the other half and asks a different kind of question, over the same
gallery. What a still cannot show is that *two* panels are drawn as where the reader is, or that a
form posts controls that sit outside it, so behaviour gets a real Chromium and its own assertions.
It is in the suite rather than in a recipe of its own because a check nobody runs is a check that
catches nothing: both of the bugs it now pins were live while an equivalent script sat beside it
unrun. A browser that is not installed fails loudly rather than skipping, for the same reason.

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

One key for the session and five per turn, written by five different places and read by four:

```text
choice               the endpoint, model, repository and thinking level; written by `Service.start`
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
fork plants its worktree at it, and the person's panel shows it. Both want the state before the turn
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
readers (`prompt_key`, `tree_key`, `opening_tree_key`, `messages_key`, read by `choice_of` and
`reached` for the body, `transcript` for the page, `before` for a fork, `planting` for a fork's
worktree). `Stepping` in `durability.py` builds them for the writers, from a turn prefix and a kind,
which is what lets one capability name a step without importing the conversation. `tree_key(n, i)`
and `Stepping.key("tree")` therefore produce the same string from opposite ends, and nothing
enforces that: change one and change the other. The tests in `test_conversation.py` assert the shape
against literal recorded values rather than round-tripping through the writer, which is what turns a
drift into a failure rather than a silently unfindable record.

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

- **Starting** a session asks `Catalogue.offers(endpoint, model)`. That is form validation: a select
  is a suggestion the page made, not a constraint on what can be posted, so a new session may only
  be created on a pair the picker actually offered.
- **Answering** one asks only whether the *endpoint* exists, in the worker (`agent_for` raising
  `UnknownChoice`) and in `Conversation.answerable` (`models_of(...) is not None`), which are
  deliberately the same question so the page and the worker cannot disagree. The model is not
  checked: the provider's own refusal is the authoritative answer about a model and it arrives on
  the turn, where gating here would strand a conversation nobody broke.

A session whose endpoint is gone renders with a sentence naming it and no poll, because a spinner
that will never resolve is the one state a person cannot diagnose. `test_console.py` pins both
halves, including that a session on an unlisted-but-routable model keeps its poll.

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
session's and every worktree is a linked one off a shared object store. `Workspaces` is the three
of them as one value - clones, worktree root, and what the forges reach - because they only mean
anything as a set: a worktree is of a clone, and a clone is of something a forge reached.

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

`Workspace.restore` is written and tested but nothing calls it yet: today a snapshot is a record of
what disk looked like, not something to go back to.

## How a model names a line

Every tool lives under `tools/`, one package per tool, as `tools/{name}/{module}.py`. Only the
constructor reaches the harness: `tools/__init__.py` exports `Files` and `file_tools` and nothing
else, so `agent.py` asks for the tools a workspace affords without knowing that editing is anchored
or that a worktree root has to be resolved against. A second tool is a new package beside `files/`
and one more name in that list, rather than an edit to anything that already imports it.

Within the one that exists, `tools/files/anchors.py` is pure and `tools/files/tools.py` is the shell
around it, which is the split that lets the interesting half be tested with a list of strings. A
session with a repository gets `list`, `read`, `edit` and `create` bound to its own worktree; a
session with none gets **no toolset at all**, because four tools that can only fail are worse than
none and cost a description on every request.

**`list` asks git rather than walking**, so a `.gitignore` is obeyed and a `.venv` or a
`node_modules` never reaches a context window. `git ls-files --cached --others --exclude-standard`
is the exact call, and each flag earns its place: `--cached` is what is committed, `--others` is
what the agent itself just wrote, and `--exclude-standard` is the ignoring. What comes back is
*flat*, one path per entry, because git records files and never directories; `catalogue` builds the
tree from those paths, which is also why an empty directory does not appear at all. `depth` bounds
the answer rather than hinting at it: a directory at that depth is summarised with a count instead
of opened, and `MAX_ROWS` is the backstop on a large depth over a large repository.

`list` is the one tool here with no defence against a bash tool arriving later. Anchored `edit` has
one - within the at-least-once window a re-run edit fails loudly on anchors its own first run
invalidated, where an arbitrary shell command re-runs silently - but listing a directory is
something `git ls-files` in a shell does exactly as well. It exists because there is no bash tool
today and a session otherwise cannot discover a filename, and it is the first thing to delete when
there is one.

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

Two things are refusals rather than omissions. **There is no `write`**: a tool that overwrites a
whole file is the escape hatch that makes anchored editing pointless, since the first refused edit
becomes a full rewrite that discards whatever was not read. `create` refuses an existing path.
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

There is no `Protect*`/`ReadWritePaths` hardening, deliberately. The agent edits repositories, so
the paths it legitimately writes are the worktree root and everything under it, which is exactly
what a `ReadWritePaths` would have to name; the boundary that actually holds is `Files.resolved`,
which resolves every path and refuses anything that lands outside the session's own worktree. A unit
sandbox loose enough to permit the worktree protects nothing the tools do not already refuse.

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

The picker's controls are **associated with their form by name, not by nesting**, and that is
load-bearing on the start page. There the choosing fills `main`'s growing row and the box is pinned
under it, so every endpoint radio, model radio and select is a *sibling* of the form that posts them;
`form="choosing"` (`CHOOSING_ID` in `pages.py`) is the whole of what makes them submit, and without it
the console refuses its own page with a 422 saying a message needs an endpoint and a model. The fork
page nests its picker inside a form of the same name, so `model_cards` can carry one attribute and
serve both the pages and the `/fragments/models` swap. A markup assertion cannot see any of this,
which is why `TestWhatAFormPosts` asks a browser what `form.elements` holds.

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

Every panel carries a **`recorded` disclosure** showing the JSON the checkpoint holds behind it, and
three things there are decided rather than incidental:

- **Nothing is stored per panel**, so the panel and its record have to come out of one walk.
  `parted` is that walk and hands out `Source` indices as it goes; `runs` is the one grouping rule
  `panelled` and `sourced_at` both use. Recovered by a second pass the indices would be a guess at
  what the first did, and the skipping in `parted` is exactly what makes that guess wrong from the
  first unrenderable part onwards - every panel after it would show somebody else's record.
- **Indices into the stored value, never the parsed part dumped again.** A round trip states what
  today's Pydantic AI would write, which agrees with the record right up until a release renames a
  field and then disagrees silently. A person's panel is the exception and is one whole key,
  `turn:{n}:prompt`.
- **Fetched on demand and `hx-preserve`d.** The transcript swaps once a second, so the raw record of
  every panel is not something to carry in it; and because the server renders the disclosure closed,
  a morph takes the `open` attribute back off unless the element is preserved. htmx reads
  `hx-preserve` off the *incoming* markup, so taking it off the live node proves nothing. `once` is
  safe because a panel exists only once what is behind it has stopped changing.

Tests drive the app through `without-http`'s in-memory loopback client (`tests/calling.py`), so
nothing binds a port and the suite parallelizes. The `app` fixture deliberately runs the console
over a store with **no worker**, so a test asserting on a pending turn cannot race one; what the
worker does is tested in `test_conversation.py`, a pass at a time.
