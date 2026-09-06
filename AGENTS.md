# AGENTS.md

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows.
[`README.md`](README.md) is what it does and why; this is what to know before changing it.

`CLAUDE.md` beside it is one line importing this file, so Claude Code reads the same words every
other harness does. `AGENTS.md` is the one that holds them, because it is the name the ecosystem
converged on and the name this console's own guidance loader reaches for first.

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
that it reached no files, because settling lives in `Service.start` and nothing here goes through it.
So a rule about what a recorded `choice` may hold is a rule this file has to apply too, and the demo
database is where that is noticed - rebuild it (`rm mainplate-demo.db*` then `just seed`) after any
change to what a *record* holds, or it keeps serving the old shape. That is the whole checkpoint and
not only the choice: the fixtures in `scripts/gallery.py` write every kind directly, so a shape change
lands here as a database full of values nothing can parse.

It calls `Choice.settled()` rather than restating what a repository decides, which is what keeps that
list in one place: a field added to what a repository settles is settled here without an edit. That
is the whole reason `settled` is a method on `Choice` rather than a line in each of its three
callers.

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

`working` is the same fixture with workspaces behind it, which the command box needs and nothing else
there does: a session must have a worktree before `Run` is offered at all. Two fixtures rather than
workspaces on the one, so a test that only drives a conversation does not get a clone and a worktree
it never looks at. The real repository they are both built on lives in `conftest.py`, since two
suites want one now.

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
only what a *reader* decided and would want to find again: the theme, which kinds are muted, and
what they have written and not sent. Never a word of the conversation - an unsent draft is not one
until it is sent - so a browser with it wiped renders exactly what one without it does. The theme is
the reader's across every session; the other two are facts about one conversation, so they are keyed
by session id, and that scoping is load-bearing rather than tidy: every session shares one origin, so
an unscoped key would be one conversation's decisions imposed on all of them.

**`muted` is what the key does, and the word matters because `aside` is taken.** A muted kind is
still drawn, at two fifths opacity, so it is quieted rather than hidden or removed; an *aside* is a
side conversation, which is a different thing entirely and the one that keeps the name.

Two things the script holds are deliberately *not* stored, and the line between them is worth
keeping. What a reader has folded, and whether they are following the end, are modes
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

## The words

**One name per thing, and the same name in the code and on the page.** A reader who learns a word
from a control should find that word in the identifier behind it, and somebody reading the source
should not have to work out that two names are one operation. This console keeps being tempted the
other way, because a second word always feels like it is adding a distinction; usually it is adding a
synonym, and a synonym is a thing to keep in step for ever.

**The test is whether a second *thing* exists, not whether a second word reads well.** Two controls
that call the same function with different arguments are one thing with two labels: the composer's
fork and a rule's fork are both `Service.fork`, differing in `at`, so they are both called `fork`.
Where the distinction is real the words stay apart, and `endpoint`, `wire` and `provider` are the
worked example - one is a line in `config.yaml`, one is a built object that speaks an API format, and
one is whoever made a model. Three things, three words, none of them interchangeable.

**Say what it is, not what is comfortable.** A tool that will not do something `refuses`; a session
nobody can answer is `stalled`; a model with no price says `no reference record`. None of those are
softened into "unavailable", "issue" or "not supported", because the reader's next question is always
*what happened*, and a euphemism makes them ask it. The same goes for what a control does: `keep`
takes text off the page, `drop` deletes it, and neither is called "manage".

**The reader knows how an agent harness works, so reach for the plain technical word.** They know
what a context, a tool call, a token, a checkpoint and a system prompt are, and naming one is the
shortest true thing this console can say: `context cleared` over "the model was told nothing above
this line", which describes a state where the reader wants the act and leaves them working out what
was acted on. Two failures, and the second is the one that keeps happening here:

- **Explaining what they already know.** A gloss on what a context window is, on why a tool call has
  an id, on what forking a conversation means. The term carries all of it, which is what a term is
  for.
- **Reaching around the term.** Naming the mechanism reads blunt, so a softer phrase gets written
  instead, and the reader is now inferring which mechanism was meant. That is the euphemism above,
  arriving through vocabulary rather than through tone, and it costs more here because the reader
  could have been told outright.

Neither is an argument for jargon this console made up, and it does not license the second word the
rule above refuses. It is the plain name of a thing that already has one.

**Name the trigger, never an actor.** Nothing in this console decides anything, and a word implying
otherwise is wrong about the mechanism as well as grandiose: what happens is that a recorded number is
crossed and a message is delivered. So the auto-handoff switch says `auto at reserve` and not `by
itself`, which reads as the console choosing or - worse, since it is the party that *does* choose
things here - as the model having decided to wrap up. The trigger is also the more useful half, since
a reader who knows what fires it knows what to change.

**Prose may inflect where a control may not.** English makes a noun of an act, so a fork produces a
branch and a session forked at turn three has a branch point. That is ordinary writing and not a
second term. What must not vary is the label on a button, the name of an identifier, and the word a
doc reaches for when it means the operation.

**Choose the word before the value is durable.** A term that has only ever been in a label costs a
rename; one that has been written into a checkpoint costs a migration, because the *value* is what is
in the store - see `Filesystem.WORKTREE`, which was `WORKSPACE`, and took every session written until
then with it. So a `Disposition` is free to be renamed and a `Choice` field is not, which is worth
knowing at the moment the word is picked rather than afterwards.

## The key scheme

**Two key spaces, and the store owns one of them.** What a person puts into a session goes into its
**inbox**, under a key the store mints; what a pass records about a turn goes under a key this
console names.

```text
inbox:{n}            a message or a command, filed in the order it arrived; appended from outside a
                     pass, by `Service.say`, `Service.send`, `Service.run` and `Service.hand_off`,
                     and from inside one by the `hand_off` tool

result:{entry}       what the command delivered under `{entry}` exited with, said and took; written
                     by `Commands` when it finishes
choice               the endpoint, model, repository, base, branch, isolation and thinking level;
                     written by `Service.start` and by `Service.fork`, before the first message
instructions:{n}     what the stretch of context beginning at turn `n` is answered under, exactly as
                     the model is sent it; composed and recorded by the first pass to reach it,
                     before its first request, and replayed by every later one
turn:{n}:opened      the entry this turn took, recorded by `Run.receive` in the conversation body
turn:{n}:tree:{i}    the worktree before the i-th model request; written by `StepwiseDurability`
turn:{n}:heard:{i}   how far down the inbox the turn had read when it made that request, recorded by
                     `Run.pending` through `StepwiseDurability`
turn:{n}:model:{i}   the i-th model response of that turn; written by `StepwiseDurability`
turn:{n}:refused:{i} why the i-th request will never be accepted, where one never was; written by
                     `StepwiseDurability`, and exclusive with `model:{i}`
turn:{n}:tool:{id}   what one tool call returned and how long it ran; written by
                     `StepwiseDurability`
turn:{n}:messages    what the agent run produced; written by the conversation body
```

**Nothing allocates a number by trying any more, and no key is contended.** A message used to name
the turn it was going into, so writing one meant deciding which turn that was against a checkpoint
that had already moved, and a steer meant claiming a numbered slot the pass was competing for. The
store names an entry, so three writers posting at once are three entries, and *which turn takes one*
is decided later by the pass that reads it - which is the only party reading at the moment the answer
is true.

**An entry says nothing about which turn it belongs to, and that is the price.** What decides is
`turn:{n}:opened`: a turn owns everything from its own entry up to the next turn's. Every reader goes
through `held_in` for that, `before` needs a second rule for the fork (see the fork section), and
`Service.run` no longer has to work out which turn a command is in.

**What it buys is a command drawn where it was run.** The store files everything in the order it
arrived, so counting a turn's model records ahead of a command's entry says how far the reply had got
when somebody typed it. Nothing had to be written at the time and nothing raced the pass for a
position in its sequence; `ran_in` reads it, and `alongside` puts the panel back there. Collected at
the end of the turn, as they were, a command sank down the page as each later answer landed above it.

**`command` is recorded and not told**, which is the whole of what a command is here, and the split
it rests on is one this console already makes everywhere: whether something is *in the checkpoint*
and whether it is *in the message history* are two questions, and `tree:{i}` and `heard:{i}` are both
records the page draws and no model ever sees. So a command renders, survives a reload and comes
across on a fork, and costs the conversation no context and reaches no provider. Telling the model
what you ran is a message somebody writes, which is what the box above it is for. A pass draining its
inbox passes over one rather than reading it.

**`result` and `tool` each carry their own duration**, and that used to be the one asymmetry in this
scheme: a tool return was stored bare, so a duration beside it would have been indistinguishable from
a tool returning a field of that name, and it needed a `turn:{n}:took:{id}` of its own. With every
value in an envelope the foreign value sits under a name this console owns, so the objection is gone
and so is the key.

**A result is named after the entry rather than after a turn**, because which turn a command belongs
to is decided by where its entry landed: a key naming one would be a second answer to that question,
written by a handler reading a page that may have moved on.

**The indexed kinds are numbered by position and the tool key deliberately is not.** Model requests
happen in a fixed order, so counting them names a step the same way on every pass, and the tree
captured before each one and the cursor recorded for it ride the same counter, so `tree:{i}`,
`heard:{i}` and `model:{i}` are three parts of one request. A *batch* of tool calls runs
concurrently, so counting those would name a record by whichever won a race and hand a later pass
somebody else's result. A call already carries an id, and that id is part of the model response the
conversation recorded, so a replay is handed the same one for free. `Stepping.key` is the positional
form and `Stepping.identified` is the other, and both take a `StepKind` rather than a bare string, so
the word a key is built from is the word the record under it tags itself with.

**`turn:{n}:late:{k}` is the key the inbox deleted**, and it went because the thing it answered
cannot happen any more. It recorded what was found at the boundary where a run would otherwise have
ended, so a message arriving during the last response could redirect the run into one more request
rather than reaching nobody. A pass reads the snapshot it loaded on the way in, so nothing arrives
*during* one: the drain before the first request already sees everything this pass ever will, and a
message delivered afterwards is read by the next pass, which opens the next turn on it. What used to
cost the ending turn a round trip nobody asked for now opens the turn after it.

**A model request's duration is still not a field**, because a `ModelResponse` has a `metadata` dict
Pydantic AI keeps for the application and does not send to the model: `Stepping.stamp` writes it there
and it rides into `turn:{n}:model:{i}` and `turn:{n}:messages` alike, which is what keeps the two
readings of a turn agreeing without either being taught where to look. A tool call has no such slot in
somebody else's value, so its duration is a field on the record around it. One word, `TOOK`, in both
places; two places because the values are two different kinds of thing rather than for symmetry's
sake.

`opening_tree_key(n)` is `turn:{n}:tree:0`, and it is what two things mean by "this turn's tree": a
fork plants its worktree at it, and the rule opening the turn shows it. Both want the state before the turn
did anything.

`instructions:{n}` is the one key here that is neither turn-prefixed nor named after an entry, and
both halves of that are decided. Not turn-prefixed, because `before` copies those by shape and a fork
that attached a repository its parent never had would inherit instructions with no guidance in them.
Not session-level, because a forget ends a stretch of context and composing again there is free: the
prefix it would have invalidated has just been thrown away.

`choice` goes in before the first message and never again *within a session*. The order is
load-bearing: the message is what *queues* a session, so writing it first would let a worker take the
session and find no endpoint to answer on. Never again, because a session that changed endpoint
halfway would replay recorded answers from one and continue on another. Forking is how the choice
changes, and it changes it by making a different session rather than by rewriting this one.

`turn_of` is the inverse of `turn_prefix`, and it answers about the key's *shape* rather than
against a list of known kinds. That is what lets `before` carry a whole prefix of a conversation
into a fork without being taught each new kind of step: a `turn:3:approval:0` nobody has written yet
is turn 3 already, and `turn:3:tool:toolu_017` was too before anything read tool keys. `entry_of` is
the same move over the other key space, answering which entry a key is *about* - an entry is about
itself and a result is about the command it answers - so a fork carries what hangs off an entry
without being taught that either.

**The names are built in two places and have to agree.** `conversation.py` names them for the
readers (`opened_key`, `tree_key`, `opening_tree_key`, `messages_key`, `model_key`, `tool_key`, read
by `choice_of` and `reached` for the body, `transcript`, `so_far` and `responded` for the page,
`before` for a fork, `planting` for a fork's worktree). `Stepping` in `durability.py` builds them for the writers,
from a turn prefix and a kind, which is what lets one capability name a step without importing the
conversation. `tree_key(n, i)` and `Stepping.key("tree")` therefore produce the same string from
opposite ends, and nothing enforces that: change one and change the other. What is now shared is the
*word*, since both take a `StepKind`, so a kind nobody has declared is a type error rather than a key
nothing reads. The tests in `test_conversation.py` assert the shape against literal recorded values
rather than round-tripping through the writer, which is what turns a drift into a failure rather than
a silently unfindable record.

**The two indexed kinds have a reader now, and that is what draws a turn as it happens.** `responded`
walks `model:{i}` from zero and `so_far` looks each call's result up under `tool:{id}`, so the turn
being answered renders from the steps behind it rather than waiting for its `messages`. It is not a
second copy of anything: those records exist so that a resumed pass does not pay for the same request
twice, and this reads them.

The walk is its own function because a running turn is read *twice*, for what it has said and for
what it has spent, and `transcript` calls `responded` once and hands the result to both. Walked
separately the two would eventually disagree about how much of a turn there is, which on a page that
draws a turn as it fills in is a rule reporting one number against a conversation showing another.

## What a checkpoint value is

**Never a bare string, a bare number, a bare list, or a value this console does not own.** Every one
goes into a record from `records.py`, and the rule is about *shape* rather than about validation: a
bare value has nowhere to put a second field, so the day one needs one is a migration. A message held
a bare string until a turn needed to say what history it opens on, and paying for that once is the
argument for paying for it nowhere else.

**Three keys hold a cursor and are the exception, because their value is the store's.**
`turn:{n}:opened` and `turn:{n}:heard:{i}` are written by `Run.receive` and `Run.pending` rather than
by anything here, and what they hold is an inbox key: the shape argument does not reach them, since
there is no second field this console could ever want beside one. Wrapping them would mean not using
`receive`, and `receive` is the only thing that can suspend a pass on an inbox.

It covers the foreign values too. A `ModelResponse` and a tool's return are wrapped rather than
stored raw, and the envelope is honest about what it buys: it cannot protect against Pydantic AI
renaming a field *inside* a response, because nothing here could. What it is is the place a `version`
would go the day one is needed, so adding one then costs an optional field rather than a shape change.

**`turn:{n}:took:{id}` is the key that stopped existing because of it**, which is the clearest case
for the whole policy: a duration could not sit beside a bare tool return without being
indistinguishable from a tool that returned a field of that name, so it needed a key, and the key
brought a window where a return was recorded and its duration was not. One record, one write, no
window, and `tooks_in` and `blocks_from` now read one mapping rather than being handed two.

**Every record carries its own `kind`**, which is a second copy of what its key already says, and the
copy is the point: parsed by key alone, a record written under the wrong one is silently reinterpreted
as whatever that key expects, where a tag makes it fail. It is safe from being a third place to keep in
step because `StepKind` is one vocabulary the key builders and the discriminators both take.

**In the inbox it is not a second copy at all, and that is where it earns most.** The store names an
entry, so nothing in the key says whether what is in it is a message that must open a turn, one a
running turn may fold in, a message the console wrote itself, or a command no model will ever see.
`records.Delivered` is the union of the four and the tag is the whole of what tells them apart, which
is why a pass draining its queue can stop at a prompt and pass over a command.

**Which questions a reader asks of that tag are `records.opens` and `records.forgets`**, and they are
functions rather than an `isinstance` chain repeated at five call sites. `opens` is whether a
draining pass must stop here, which a `Prompt` and a `Handoff` answer alike; `forgets` is that and
the field together, since only a message a turn opens on can carry a boundary. `opens` is a `TypeIs`
so the union it names is written once and every caller that goes on to read `forget` is narrowed by
asking rather than by repeating it.

**Not to be confused with the panel `Kind`.** Both are called `kind` because it is a generic word and
each is unambiguous where it is used; they overlap on `command` and `tool` meaning different things,
they never mix, and mypy refuses the crossing since they are distinct unions. The *types* take the
prefix where both are in scope, which is `conversation.py`: `StepKind` beside the `StepKey` that
already existed, against the panel `Kind` that keeps the bare word.

**Unknown fields are ignored; an unknown kind is not.** The first is Pydantic's default said out
loud, and it is what lets a newer build's record survive being read by an older one after a rollback.
It is safe here for a reason specific to this store: nothing round-trips a record back into it, since
the checkpoint keeps the value a key was first given and `before` copies raw values without parsing
them, so a fork taken under the older build carries the newer record across intact. An unknown *tag*
is a hard parse failure, which is why readers parse by key, where the caller already knows what it
asked for, and `records.Step` is only for the places that take a bag: a dump, an export, a migration.
The HTTP boundary is the opposite case and stays that way, since an unrecognised disposition is a
refusal.

**Three shapes are deliberate exceptions.** `choice` is not a `records` model: it is already a record
this console owns and has grown fields twice with no migration, and its parser encodes things a schema
cannot say, defaulting an absent isolation from whether a repository was picked and re-parsing a base
and a branch that become `git` arguments. It carries the tag all the same. `Result.status` is a
`StrictInt`, because Pydantic reads `True` as `1` where it is not and `git diff --quiet` exits 1 to
mean there *are* changes. And `parse_tree` reads an absent key and a recorded tree holding nothing as
the same answer, since every caller reaches it through `recorded.get` and both draw as no tree.

**A parser now raises `ValidationError` rather than `TypeError`.** That is Pydantic's own error
surfacing rather than a hand-written one, and it is still loud, which is the property those parsers
were written for.

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

`agent.py` holds one `Wire` class per format, and it holds *all three* format-specific things: how to
name a model over it, how to ask it what it serves, and what it has to be told to reuse a
conversation's prefix. A third format is one class, not an edit in three files.

**`caching` is the third, and it exists because getting it wrong is invisible and expensive.** A
conversation is re-sent whole on every turn, so a session with no cache breakpoint pays full input
price for everything said so far, over and over - on a long turn that is most of the bill, and
nothing about the request looks any different. It is opt-in on the Anthropic wire
(`anthropic_cache`, a top-level `cache_control` whose breakpoint the server moves forward as the
conversation grows) and automatic and uncontrollable on the OpenAI one, which answers with an empty
`ModelSettings`. Empty rather than absent, because what has to be true is that every wire *answers*:
a format added later is then a `caching` somebody had to write rather than a session quietly paying
full price.

`CACHE_FOR` is `1h` rather than the default five minutes, and the trade is stated because it is real:
an hour's retention is written at 2x base input against 1.25x, so it pays only where a conversation
is picked up again after a pause. That is what a chat console *is* - somebody reads an answer, thinks,
and replies - where five minutes barely outlasts one long turn.

`agent_for` merges the wire's answer under the session's own, so a recorded choice always wins. The
two do not overlap today; if they ever do, the thing somebody picked should be the thing that
happens. `test_what_a_wire_asks_for_reaches_the_request` is what fails when the merge goes, because a
setting built and never passed on looks exactly like one that was.
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

The agent itself is built **per turn** rather than held in a startup mapping, because the model set
is now discovered and changes while the process runs, and because what a session is told includes
the repository's own guidance and the worktree holding it is planted inside the loop. That costs
tens of microseconds against a turn that costs seconds, and the connection pool - the expensive part
- belongs to the endpoint and is shared by every model over it.

The endpoint is still asked for *before* the loop, and that split is the point rather than a
leftover: `endpoints.for_endpoint` raising `UnknownChoice` there is what keeps a missing endpoint a
failure the console can explain rather than one discovered mid-turn. Built any earlier than the
loop, a session's first turn would be answered having been told nothing the project says about
itself, since the clone and the worktree do not exist until `planting` has run.

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

## How long it took

The same argument as the cost, one field along, and it is recorded for the same reason: what a
request took is settled the moment it is answered, and a resumed pass did not make it. `Stepping.stamp`
writes it beside `Stepping.price` in `CheckpointedModel.request`, timing the wrapped model's own call
and nothing around it, so the figure is the round trip to the provider and not the snapshot before it
or the store write after. Neither is ever overwritten, so the day a wire reports what a request
actually took, its answer wins.

**Where each one is recorded is decided by the value, not by symmetry.** A `ModelResponse` has
`metadata`, so the request's duration needs no field of its own and reaches both readings of a turn
for free; a tool return is somebody else's value with nowhere to put a fact about the call, so its
duration is a field on the record wrapped around it. See the key scheme. A tool that *raised* is timed
no more than it is recorded, since the `ModelRetry` propagates out of the step and there is nothing to
write - which is the honest record, and is why a still-out call and an untimed one read the same.

**A tool's duration is threaded into both readings rather than found in either.** It is not in
`turn:{n}:messages`, so `parted` and `blocks_from` are both handed the mapping `tooks_in` builds out
of `turn:{n}:tool:{id}`. Handed to one and not the other, a call's time would appear or disappear at
the moment a turn landed, which is exactly the drift the two readings exist not to have. Both readings
now take it from one walk, `calls_in`, rather than from two: what a call returned and how long it took
are one record, so nothing can find one without the other.

**A turn's time is its round trips and not its wall clock.** `Spent.took` sums the responses, so what
a rule reports is what the turn spent waiting on the provider; the calls it made in between are timed
on their own panels, and adding those in would double-count a batch that ran at once. Unknown
anywhere is unknown for the whole, exactly as with the cost, so a turn recorded before this console
timed anything shows no figure rather than a suspiciously small one.

## Whether the cache is still warm, and what that is worth

A conversation is re-sent whole on every turn, so a session picked up after lunch pays full input
price for everything said in it and nothing about the request looks any different. `cache_note` is the
line above the message box that says so, and it earns its row only because `Wire.caching` asks for a
cache at all: with none there would be nothing to have gone cold and nothing worth saying.

**A figure and not a warning, in the family of the gauge and the `▣` count.** It never tells anybody
to `forget`: at low utilization the right move is to carry on, and picking which figure matters is the
reader's. It says what is true and stops.

**One-sided, always.** Past the retention a prefix is cold and this says so; under it nothing can be
asserted, because eviction is unobservable from here. What it says instead is `warm as of 12m`, which
is a claim about when the prefix was last *written* - a response landing is exactly that moment - and
is true on any wire whatever that wire's own TTL. `RETENTION` works as the one threshold for the same
reason: it is the longest this console asks for anywhere, so past it the prefix is gone everywhere and
no format has to be threaded to the page to know it.

**The server renders an absolute time and the script renders the relative one.** Nothing here
re-renders on the clock - the stream sends when the session *records* something, and the interval that
decides the answer is exactly the one where nothing is recorded - so a server-rendered `warm` would
sit there while the retention rolled past it. `cached at 15:09` is a fact that cannot rot, which is
what a reader with `mainplate.js` absent gets, and `warm as of 12m` is the script's reading of it.
Cold is the one state the server *can* assert, since it was already true when the page was rendered
and nothing makes a cold prefix warm again.

**That split is also what keeps one elapsed formatter rather than two.** The server never renders a
duration here, so `ago` exists only in the script; a server that rendered `12m` too would be the same
three-branch format written in two languages with nothing holding them together.

**What the script adds is a duration to a duration, never one clock to another.** `data-since` is how
long ago the server measured the last response to be, and the rest is measured in the browser from the
moment it first saw that element, so a reader whose machine disagrees with the console's is still
right. A swap replaces the element, which gets a fresh `data-since` and a fresh stamp - which is
exactly what should happen.

**`Conversation.since` is the one place this console subtracts two clocks**, and the caveat lives on
the field. `Transcript.answered_at` is stamped by whichever process ran the pass and `since` is taken
in a request handler, so the difference is sound exactly as long as those are one machine, which today
they are. Split across machines it becomes as good as the two clocks' agreement, which for a threshold
in hours is fine and for anything finer would not be. It is measured in `Service.read` rather than on
the page, because a page is a pure function of already-answered questions and `now()` is not one.

**`ModelResponse.timestamp` rather than the store's own write time**, which `Checkpointer.history`
would give. Pydantic AI already stamps it, it survives the checkpoint round trip, and it needs no
second read; the store's clock has the identical split-deployment caveat, so the general shape buys
nothing here. What it means is "when the response was received locally", which is as close as this
console gets to when the provider last touched the prefix.

**The money is a floor and says so.** What it prices is the input of the next turn's *first* request -
re-sending what has already been said - and not the answer, the tools that turn runs, or the further
requests it makes, any of which can dwarf it. A bare figure would read as what the next turn costs and
understate it by however much work that turn turns out to be, so it carries a `+` and the title spells
out what sits on top. It is the one thing about a turn nobody has started that can be stated exactly
rather than guessed at.

**Both ends are drawn, and the gap between them is the point.** `▣$0.0289 / $0.2889+` is what
re-sending costs with the whole prefix cached against none of it, which is what makes the cost of
*waiting* legible: on a long conversation that is a tenfold jump and nothing about the request would
have looked any different. Neither figure claims to be the one that will be charged - how much of a
prefix the provider still holds is unobservable, which is the same reason `warm` is never asserted in
words - so the pair is stated and the state beside it says which end the session is nearer.

`▣` for the cached end rather than the word `warm`, because the state is already one of those two
words and the line would carry each of them twice. It is the mark the rule already uses for the part
of an input a provider read from its cache, so it means the same thing in both places.

**No warm figure where the record prices no cache.** `priced` falls back to the input rate there, so a
warm end would be the cold one printed twice - which reads as a bug rather than as a database that
does not say. `Resending.warm` is `None` for exactly that, and the line draws the one end it knows.

**One value rather than two fields**, so the pair can never be computed from two different contexts or
two different records, which is `facts_of`'s own argument one scale down.

**It is a second partial on the page's own connection**, which is the shape `streaming.py` was built
for and the first thing to use it. The note lives in the composer, so the transcript's swap does not
reach it, and what it says goes stale on every turn: the context it prices grows and the moment it
measures moves. `outerHTML` rather than the transcript's morph, since it is one short line with
nothing in it worth preserving.

**`RETENTION` and `CACHE_FOR` are one fact in two places.** The parameter has to be a literal, because
the SDK types the field as `Literal['5m', '1h']` and a string rendered from a `timedelta` is a `str`;
the duration has to be a `timedelta`, because that is what the comparison takes. So they are written
twice with nothing enforcing the agreement, which is the bargain `tree_key` and `Stepping.key` already
take, and `test_the_retention_and_the_wire_parameter_are_one_duration` is what turns a drift into a
failure rather than a console confidently calling a dead prefix warm.

**Every response fixture now carries a timestamp**, in `conftest.recorded_turn` and in
`scripts/gallery.py`. `ModelResponse.timestamp` defaults to the moment it was constructed, so a
fixture without one is the moment the test or the render ran: an assertion over a whole `Transcript`
becomes a comparison against the wall clock, and two `just gallery` runs produce two different pages.
A screenshot that differs run to run is one nobody can compare against the last.

## Forking, and where a session may change its mind

A session's choice is fixed for life, so **forking is how it changes**. `Service.fork` copies every
recorded key belonging to a turn before the branch point into a new session, writes a new `choice`,
and records an `Origin` on the row. The tree in the sidebar is emergent from those origins; there is
no tree inside any checkpoint, and a session stays a flat run of turns.

**`before` needs two rules rather than one, and that is what the inbox costs.** The turn-prefixed
keys come across by *shape*, so a `turn:3:approval:0` nobody has written yet is turn 3 already; an
entry says nothing about which turn it is in, so what decides is where it sits against the entry the
branch point opened on. `branch_at` is that boundary, and it counts against the turns a *page* counts
rather than the ones a pass has opened: a reader forking at turn 3 of a conversation whose third
message is still queued means the message.

**The keys come across unchanged**, which is what makes the copied cursors resolve: a fork appends
nothing, it supplies the parent's own entry keys, so `turn:2:heard:0` still names an entry the branch
holds. The store mints keys that only ever rise, so a message delivered to the branch afterwards
still sorts after everything copied.

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

## Where a message goes, and what has not been sent yet

The composer grows more than one way to act on what is in the box, and **those ways are not variants
of one thing**. They are told apart by what each one writes, which is also the order of how much
they can break:

- **The shelf** writes nothing recorded at all. It is unsent text.
- **A disposition** decides which session's inbox the message goes into, and which of the two kinds
  of message it is. Nothing new is written that was not already written by `say`, `send` or `fork`.
- **A steer** is not a disposition and not a thing anybody asks for: it is what happens to an
  ordinary message that a pass finds while it is working. Nothing about the write differs.
- **A command** is the one that is not a message at all. It goes in the same queue, is read out of it
  by nobody, runs a process outside the sandbox everything else here runs behind, and is never told
  to a model.

Sorting them this way is what keeps the cheap ones cheap. Three of the four need no new mechanism.

### The disposition

**One field on the composer's form, not one button per endpoint**, because every disposition takes
the same input and differs only in where it goes. Parsed at the boundary into an enum, the way
`posted_workspace` turns one posted value into the two it records.

- `here` is `Service.send`, and it is **the one nobody decides**: it delivers a `Steer`, which is a
  message the pass may fold into the turn it is working on and will otherwise open the next turn
  with. Neither the page nor the server can settle that, and neither tries. A page is rendered from a
  checkpoint that has moved by the time somebody has typed a paragraph into it, so a `Steer` button
  beside `Send` asked a reader to choose between two moments against a state that no longer held; and
  so did the server, when it read the record and chose between two calls, because a turn can end
  between the read and the write. The pass is the only party reading at the moment the answer is
  true, so the answer is its.
- `next` is `Service.say`, which delivers a `Prompt`: a message a draining pass stops at rather than
  folds in. It is kept as an explicit answer because wanting to be taken up *after* the reply that is
  coming is an intent no record carries and so nothing can decide it for you. It is offered only
  while something is being answered, since otherwise it is what Send already does.
- `forget` is `Service.say` with the boundary set, so it shares an arm with `next` the way `fork`
  shares one with `aside`. It is the only answer that changes what the *model* is handed rather than
  where the message goes, and being a `Prompt` is what makes it possible: a boundary between turns is
  the only place one can be, so it must never be folded into a turn already running. See the forget
  section below.
- `handoff` is `Service.hand_off`, and it is **the one answer whose box may be empty**. What it does
  with the text is point the handoff at something rather than send it anywhere, and the ordinary
  handoff has nothing typed into it, so the button carries `formnovalidate` and the boundary allows an
  empty message for this disposition alone. It shares the family `forget` is in - both end a stretch
  of context where they stand - and differs in who writes what the next one opens on. See the handoff
  section.
- `fork` is `Service.fork(at=turns, said=...)`, which is pi's `/clone` and needed a control rather
  than a mechanism: the fork route already accepts `at == said.turns`, so forking the end has always
  been reachable by URL and offered by nothing. It is called `fork` and not `branch` because it is
  the same call the rule above every turn makes, with a different `at`; see the words.
- `aside` is the same call with `Origin.aside` set. **Nothing mechanical differs** - the copy, the
  worktree and the choice are identical - so what it records is what somebody *meant*, which nothing
  else could recover and which the sidebar cannot draw otherwise. Saying that plainly is better than
  inventing a difference to justify the flag.
- `parent` sends into the session this one was forked from, which is how an aside comes back. It is
  offered from **any** fork rather than only an aside, because what it needs is `Origin.session` and
  every fork has one; gating it on the flag would be a restriction invented to make the flag look
  load-bearing. The destination is read off the row and never posted, so a form cannot put a message
  in a conversation nobody was looking at.
- `run` is `Service.run`, and it is the one answer here that is not a message going somewhere. It is
  in the same field all the same, because the question the menu asks is what happens to what you
  typed; a control of its own would spend a slot in the row above the box. It is offered only where
  the session has a worktree to run a command in, and posting it to one that has none is a `422`
  rather than a silence, since a command that vanished is indistinguishable from one that did
  nothing. See the command section below.
- A **steer** is below. It is not one of these and never was a choice a form makes: it is what
  becomes of a `here` message that a pass finds while it is working.

**`Origin.aside` is the one column added for presentation**, and it earns that only because the
sidebar draws the two marks differently: a fork gets `→2` in the mark ink and an aside gets `↩2` in
the faint one, because what a reader scanning a tree wants to pick out is where the conversation
actually went. It arrives through `ADDED` like the two columns before it, and `parse_origin`
*defaults* it where the pair beside it is demanded: every fork written before asides existed has
`NULL` there and was a plain fork, so reading it as one is ordinary parsing of an optional rather
than a guess.

**Send and everywhere else are one split control**, because a destination per button spends a slot in
the row above the message box, which is the row a phone has least of. `sending_control` is Send plus
a caret opening a `<details>` whose items are submit buttons, so the whole thing needs no script:
the fold is how everything else here folds, and a named button has always posted its own pair.

**Every answer is one `Answer` value, rendered three times**: as a row in that menu, as the button
the box shows once a leader has put it in that answer's mode, and as the sentence above the box
saying what will happen. `sending_answers` is the list and the three renderings are functions of it,
so what is on offer, what it is called and what it posts cannot come apart between them. That is the
same bargain the branch field takes in rendering one `branches` argument as a `<datalist>` and as the
list the script narrows.

**One word per answer, and `Answer.named` is `leader.capitalize()` rather than a second field.** The
word is the menu row's name, the leader typed after `/`, and the value in `data-leading`; where it
names a disposition it *is* `Disposition.value`, so the word on the page, the word on the keyboard
and the word in the store are one string. That is what took `Wait for the next turn` back to `Next`
and `Back to where this came from` back to `Parent`: a sentence cannot be typed, so a leader would
have needed a second name, and a second name is a synonym to keep in step for ever. What each one
*does* is the `saying` under it, which is where an explanation belongs anyway.

It deliberately does **not** switch what the primary button does *by remembering*, which is where
GitHub's version of this control goes further. Remembering a choice means a button labelled `Send`
that forks, and that is the one failure a control like this can have that nobody notices until after
it has happened. A mode is different because it is only ever entered by asking for it by name, and
the button then says `Fork` rather than `Send`. What closing the menu on an outside click and on
Escape adds is an enhancement over a control that already opens, chooses and submits with the file
absent.

**Send not saying whether it steers is not an exception to that.** What a button says is still what
it does: `Send` means "into this conversation, now", and steering is *how* that is carried out when a
turn is running rather than a second thing the button might secretly be. The failure the rule guards
against is a control whose meaning depends on something the reader cannot see; here the reader could
not see it either way, which is the whole reason the decision moved off the page.

**`Keep` is an answer in that menu and not a button beside it**, because "put this on the shelf" is
one more answer to what happens to what you typed, and answering one question in two places is the
thing this console removes wherever it finds it. It sits under a rule in the menu, since it is the
only answer that sends the text nowhere. The shelf's *list* stays in the rail, where nothing rebuilds
it mid-turn.

That row is the one thing in the menu that needs the script, because the shelf is `localStorage`;
everything that *sends* works without it. That is the shelf's standing bargain rather than a new
exception - its card in the rail shows nothing without the script either.

**It is posted as the submit button's own `name`/`value`**, which is the browser's mechanism rather
than anything scripted, so it works with `mainplate.js` absent and htmx appends the submitter's pair
like any other field. Shift-Enter reaches whichever button the *mode* leaves standing and no other:
`requestSubmit()` with no submitter posts no disposition at all, which parses as `HERE`, so the
keyboard shortcut means the one thing the button beside the box says rather than whichever row was
pressed last.

### Leaders

**`/fork ` in an empty box is a shortcut to a row of that menu, never a second way of saying it.**
Typed, it puts the composer into that answer's mode: the button beside the box says `Fork`, a
sentence above it says what will happen, and what is then written and sent goes there. `! ` is the
same thing for `/run`, which earns a key of its own by being the mode reached oftenest.

**The space is what commits it, and that is what makes a leader something a reader *finishes*.**
Until it is pressed the word is ordinary text sitting in the box with the menu open beside it, so
`!` is a character, `/fo` is two, and nothing has happened on a keystroke somebody was in the middle
of. It commits only where the box names an answer *in full*, because a prefix is somebody still
typing and taking the row the keyboard happens to be on would put the box in a mode they were
spelling their way towards; unnamed, the space types itself, which breaks the pattern and puts the
menu away. That is also what lets `!` be a leader rather than a key that does something: it is `run`
written in one character, so it narrows, commits, and is undone by a backspace exactly as `/run` is.

Six things there are decided rather than incidental:

- **It is entered in the page, visibly, and never parsed off the message.** If the server stripped a
  leading `/fork` out of what was posted, a paragraph that legitimately opens with one would silently
  be a fork, and it would have happened by the time anybody noticed. So the leader is consumed by the
  script, the mode is drawn, and both buttons are rendered by the server with their own labels and
  their own posted values - the script toggles one attribute on the form and hands `requestSubmit`
  whichever button that leaves standing, so it holds no label, no field name and no disposition. A
  button whose text and `name` the script rewrote would be the failure this shape exists to avoid.
- **Only at the start of an empty box, and only in the default mode.** Mid-message a `/` is an
  ordinary character; in a command box it is the front of half the paths anybody types, so a palette
  opening over one would be in the way of every command. A word no answer answers to is ordinary text
  too, so `/etc/hosts is where it lives` is a message.
- **The sending menu *is* the palette**, which is what keeps one list: the rows already say what each
  answer does and already carry its word, so a second list beside them would be a copy to keep in
  step. It opens narrowed to what still fits, by prefix rather than anywhere in the word - the
  opposite of the branch field, because a leader is a short word typed from the front. Enter takes
  the row the keyboard is on, which is how a word nobody finished typing is finished; plain Enter is
  safe to swallow there where it is nowhere else in this box, because what it would otherwise do is
  break a line in the middle of `/fo`. Two keys and not two mechanisms: the space says the word is
  done and Enter says the *row* is, which are different things to have decided.
- **A row pressed while a leader is being typed chooses the mode rather than sending.** The rows are
  submit buttons, so without the capture-phase intercept a press with `/fo` in the box would post
  `/fo` as the message: both a message nobody wrote and a session nobody asked for.
- **`Keep` is a mode like the rest and is the one that cannot be a submitter.** It posts nothing at
  all, so it is a `type=button` the shelf listens for, and the keyboard reaches it by pressing it
  rather than through `requestSubmit`. It is also the one mode nothing dispatches a `submit` from, so
  leaving it is said outright in the shelf's own listener rather than reached through the path every
  other answer takes; what decides is still the button's own `data-staying`.
- **Whether a mode outlives what was sent from it is the answer's own decision**, carried on the
  button the server drew for it as `data-staying` and read there rather than kept in a list in the
  script. `Run` stays, because a command is rarely the only one; everything else comes back to `Send`,
  because it is a thing somebody meant once, and a `Fork` or an `Aside` has navigated away by then
  anyway. The script leaves the mode a turn of the event loop after the `submit`, because what leaving
  it does is hide the very button the send is attributed to.

**Which modes exist is read off the buttons the server drew**, not kept in a list in the script. A
session with no files is offered no `Run`, so there is no `/run` and no `!`, and the two cannot drift
because there is only the one thing that decides it. What CSS lists by name is which
`data-leading` shows which button and sentence, the same bargain the card kinds take, and it is the
one place the answers *can* drift: CSS cannot ask whether a descendant's attribute matches an
ancestor's, so an answer added without a line there enters a mode that hides `Send` and reveals
nothing, leaving a composer with no primary button and no sentence.
`TestNamingAModeFromTheKeyboard` asks it of every button the server drew rather than of a chosen
one, which is what turns that into a failure rather than a mode nobody can use.

**A mode is left by Escape, and by a send where the answer is not one that stays.** What makes
staying safe is what makes the mode safe at all: the button says `Run`, not `Send`.

**The sentence saying what the mode does sits *above* the box.** The composer is the bottom of the
page, so a row appearing anywhere in its column pushes everything above that row upward: under the
box it moved the box itself out from under the cursor at the moment somebody entered the mode, where
above it what grows is the composer's top edge. `TestNamingAModeFromTheKeyboard` measures the box
across the press, because both layouts are correct markup and each screenshot is right on its own.

An **absent** field is `HERE` and an unrecognised one is a **refusal**, which is the one place a
default would be wrong: guessing puts a message in a conversation nobody addressed it to, and it is
sent by the time anybody could notice.

**A branch answers `HX-Redirect`, never a `303`.** htmx follows a redirect itself and swaps what
comes back into the target, so a `303` would put the branch's transcript inside the parent's page and
leave the address bar naming the parent. `elsewhere` in `console.py` is that, and
`TestWhereTheComposerSendsTo` pins it in a real Chromium, because both halves - that htmx sends the
submitter's value, and that it navigates on this header - are htmx's behaviour rather than ours and
look identical in markup either way.

### The shelf

Named slots of unsent text, **per session, and copied when the session forks**. That copy does not
offend the rule against a second copy of what changes, for the reason a fork does not: once copied
the two are independent values that happened to be equal, and editing one never has to reach the
other.

Accumulative rather than save-and-replace, and the case that decides it is a review: reading a diff
and building one comment up across several turns is the shape this is for, where a single
overwriting draft would only ever hold the last thing typed.

**It cannot live in the checkpoint while it is editable, and that is structural rather than a
preference.** `without-durability-sqlite` writes steps with `ON CONFLICT (workflow, step) DO UPDATE
SET value = workflow_checkpoint.value`, which is a no-op update: a key keeps the value it was first
given, so a draft saved twice would keep its first text for ever. And `Service.token` counts rows, so
even a rewrite that did land would not move the change token and no other tab would learn of it.

So it starts in `localStorage`, keyed by session id exactly as the set-aside kinds are, and the fork
copy is the script's rather than the server's. `document` puts `data-forked-from` on the body for
exactly that: the server has never seen a draft so it cannot carry one the way `Service.fork` carries
a turn, but it can say which conversation this one came from and let the page holding both stores do
the rest.

**An empty shelf and a missing one are different, and the inheritance turns on it.** The adoption
runs only where `held(SHELF) === null`, because a reader who cleared theirs has a stored `[]` and
must not be handed the parent's back on every load with no way to refuse it. `test_browser.py` pins
that, and it is the one rule here a plausible simplification silently breaks.

**Keeping clears the box and taking appends to it**, never the other way round. Clearing is what
makes it "keep that" rather than "copy that", since the reason to shelve a paragraph is almost
always to write a different one next; appending is what can never lose something already typed, and
what assembles several kept notes into one message, which is the case an aside's merge and a diff
review are both instances of.

Moving it onto the session is the goal and not a regret, because a draft that does not reach another
browser is a draft you have to be at one machine to finish. **What that move costs, decided before it is made rather than during it**: a table beside
`sessions` rather than a checkpoint key, since the checkpoint cannot hold a mutable value; a second
change signal, since `token` counts checkpoint rows and a drafts table is not one; and a story for
two tabs editing one slot, which is a genuine conflict where everything else here is append-only and
therefore has none.

### Forget

**The one answer that changes what the *model* is handed rather than where the message goes.** It
records `forget` on the turn's own opening record, and `reached` starts the history there instead of
at turn 0.

**Nothing is deleted and nothing is hidden**, which is the whole reason the word is `forget` and not
`clear`. Every turn above the boundary still renders, still counts toward what the session cost, and
still comes across on a fork; the checkpoint is still the conversation. What starts again is only the
message history, which is the split `command` already makes between being *in* the checkpoint and
being *in* what a model is told, applied to turns rather than to one kind of record. A control saying
`clear` beside a transcript that keeps all of it would be describing something this does not do.

**The rule says `context cleared`, and the object is what keeps that from being the `clear` the
control is refused.** A bare `clear` names nothing, so beside a transcript that keeps every word it
reads as a claim about the transcript; naming the *context* says the one thing that was cleared and
leaves the phrase true. That is the words section's plain technical word, at the one place a reader
meets this mechanism, and it is two words rather than a sentence because a rule now carries six
figures beside it: what a reader needs there is the noun and the verb, and the fork link under the
same finger already says what to do about it.

**It sits in the middle of the rule, between two of the gaps that hold the line apart.** A rule has
the turn's own controls at one end and its figures at the other, and a boundary belongs to neither:
drawn against the left group it read as one more fact about the turn rather than as the thing the
rule is saying. On a phone the three parts stack instead, each on its own row - the same two gaps,
given a whole line's basis so that a flex item takes a line on its own, which is one mechanism at
both widths rather than a wrapper element that exists for one of them.

**It rides on the message rather than in a record beside it**, and that is what makes the boundary
impossible to get wrong rather than a saving. Two entries need an order, and a turn can open between
them: a marker delivered *after* the message can be missed by a pass that has already taken it, and a
resumed pass reading it would then build a shorter history and pair it with an answer the first pass
gave to a different question - which is exactly what `heard:{i}` exists to stop a steer doing. One
record is one append, so a message whose history policy has not landed cannot exist. It also settles
two smaller things for free: there is no dangling marker at the end of a session, and no live pass
that can miss one, since the message a pass just took carries the answer.

**A boolean and not the turn the history starts at**, which would be a number recoverable from where
the entry sits and able to disagree with it.

**Never a steer, and always with a message.** A boundary between turns is the only place one can go,
so it is delivered as a `Prompt` rather than a `Steer`: a draining pass stops at one of those, which
is the whole of what "never folded into the turn already running" means. That is why it shares an arm
with `NEXT` the way `FORK` shares one with `ASIDE`. It carries a message because there is no reason to
forget without going on to say something, and because a marker with no turn under it would be a rule
with nothing below it.

**`reached` asks about the turn it is about to return, not only the ones behind it.** The walk is
conditioned on a turn having *answered*, so a forget on the turn about to run is never reached by it.
Missed, the first pass answers that turn on the whole conversation and the pass that resumes it
answers on nothing. That is the one place this can be quietly half-implemented, and
`test_conversation.py` pins both routes to the same answer.

**Continuing the conversation a forget closed is `fork` at that turn**, and the affordance already
exists: the fork link lives on the turn rule, which is exactly where the boundary lands. `before`
copies the turns *below* the branch point and the marker lives on the turn that opens, so the branch
carries the whole backlog and no boundary, with nothing taught about the field. Forking *after* one
inherits it in the prefix and starts its history in the same place. A control of its own would be a
second name for one call, so what the rule carries instead is a `title` saying what forking there
means.

**The rule is the one this transcript draws that describes what is *above* it**, which is what lets
the phrase be short: every other rule looks forward at the request or the turn it opens. It is drawn
in a stronger ink rather than a hue of its own, because the palette runs on one axis and a boundary
belongs to neither side of it, and the phrase takes the full ink and the bold face where everything
else on a rule is faint - set in the same weight as a tree hash it reads as chrome to skip. The panels
above are left exactly as they were: what changed is who was told, not what is worth reading, and
fading them would say the second thing while colliding with `muted`, which is the reader's own
decision and already drawn that way.

**The rule wraps and the controls on it do not.** It is the only rule carrying a phrase as well as
its figures, so it is the only one that wraps unprompted, and what goes onto the second line is the
tail of the figures rather than the `#N` and the fork link somebody is about to press. That is "a
control that toggles may not move", one rule along.

**The dock gains a leftmost column**, widest-first the way the picker is ordered: it steps the points
where the model's history starts again. It is drawn in every session and steps nothing in most of
them, which is right rather than a gap - the rail lives outside the region that swaps, so a column
that appeared with the first forget would not appear until a reload. Its stops are found in the live
transcript the way every other column's are, by the `data-stop` a rule declares about itself, so one
recorded mid-session is reachable at once; its upper terminus is the top of the transcript, which is
what "before any forget" means.

### Handoff

**A forget whose message the session wrote itself.** The console asks a session to write down where
it has got to; the model does that with its own tools, calls `hand_off` with the document, and the
document is delivered back as a message carrying a boundary. What the next model is told is the
document and nothing above it.

**It happens in the session, not in an aside**, which was the first design and was worse in four ways
at once:

- **The worktree.** A fork plants a fresh one at a recorded tree, and an end-fork has no recorded
  tree at all, so it falls through to the repository's default branch. The agent asked to describe
  the work would have been looking at a directory with none of it in it - and "check rather than
  recall" is the whole reason for letting it use tools.
- **The cost.** `altogether` sums a session's own turns, so a handoff's spend would have landed on a
  different total, and the parent's running figures would have been quietly missing it.
- **The recursion.** An aside inherits its parent's settings, and it carries the parent's whole
  window, so it starts near any reserve by construction. Turning auto-handoff off on the copy is one
  line and exactly the kind that gets forgotten until it recurses in production.
- **The cache.** Instructions are the per-request parameter Pydantic AI renders in front of the whole
  cached prefix, so a fork's first request pays full price for the window unless its composed
  instructions come out byte-identical. In-session there is no second prefix: the handoff turn is the
  next turn on the one already cached.

**The tool is in every session's prefix**, and that is arithmetic rather than convenience. Tool
definitions sit above the system prompt in the cached prefix, so adding one invalidates the whole
conversation beneath it: introduced at handoff time it would cost a full uncached read of the window,
where a permanent one costs its own description at cache-read prices on every request. Four orders of
magnitude. `agent_for` therefore adds it unconditionally, and unlike the file tools it is not
conditioned on the isolation, because what it reaches is the conversation rather than the machine.

**A tool rather than the turn's prose, because models leak the framing.** Asked for a handoff in
words, a model writes "Here is the handoff document: ... What would you like next?", and the framing
is then durably part of what the next model is told. An argument splits the document from the chat
around it, and `hand_off` refuses anything under `LEAST` characters, which is what catches the model
that acknowledges the ask instead of answering it.

**Neither the ask nor the tool prescribes a shape**, and that is a decision rather than an omission.
What somebody picking up a refactor needs handed over and what somebody picking up an investigation
needs are different documents, so a fixed set of headings would have every session filling in the
ones it has nothing to say under. `ASKING` says what a handoff is about - where the work got to, what
was decided and why, what to do next - and stops; the tool's description carries the parts that are
*mechanical* rather than editorial (the document becomes the whole context, pass it alone, check
rather than recall) and says outright that the shape is the model's.

**A person who wants it pointed somewhere types it in the box**, and it is appended to the standing
ask rather than replacing it: "dwell on the parser work" on its own is an instruction to summarise a
summary. Both writers compose through `recorded_ask`, which takes the note where there is one and the
bare ask where there is not, so a handoff nobody asked for and one somebody typed a paragraph into
cannot come to say different things about what a handoff *is*.

**It is delivered and not appended, and that costs a pass boundary.** An entry appended mid-pass is
invisible to the pass that appended it, since `receive` reads the snapshot loaded at the top - which
is what makes a drain replayable - and an append queues nothing. A handoff written that way leaves
the session `Blocked` on a message already sitting in its own inbox with nothing that will ever wake
it. `handing_through` therefore takes the whole `Durable` rather than the checkpointer a pass holds.

**`records.Handoff` is an arm of `Delivered` rather than a flag on `Prompt`.** Every other message in
a conversation was typed by somebody, so a reader has to be able to tell at a glance that this one
was not; the tag is what the panel's kind is read off, which is the same argument that made `Steer`
its own record. It behaves exactly as a `Prompt` otherwise, and `records.opens` and `records.forgets`
are where that "exactly as" is written once rather than as an `isinstance` chain at each of the five
readers. Both uses are `handoff` because both are the handoff: the ask carries no boundary and the
document carries one.

The panel takes the person's hue, by `command`'s rule: the axis is who produced the text, and what a
handoff holds was produced by this session rather than by the model about to be handed it. Its
`TITLES` entry is what says the console composed it, which is the one thing the label leaves out.

**Asking for one is `/handoff` in the composer**, which is an answer in the sending menu like every
other. The menu's premise is that its rows are decisions about the text somebody typed, and this one
is: a handoff takes an optional note saying what it should dwell on, and the box is exactly where such
a note is written. `/handoff` on its own hands off, and `/handoff` with a paragraph hands off pointed
at what the paragraph says.

**The box may be empty for this answer and no other**, which is what the row's `formnovalidate` buys.
The box is `required`, which is right for a message and would refuse the ordinary handoff, so the
button says it does not need the form's required fields and the boundary allows an empty message for
this disposition alone. That is the browser's own mechanism rather than the script toggling an
attribute under a reader, which is the same reason every mode's button is drawn by the server.

`Answer.demands` is where an answer says so, and it is read by *both* renderings: a menu row is a
submit button exactly as a mode's own button is, so an exception on one of them would be a control
that refuses from the menu and works from the keyboard. `TestWhereTheComposerSendsTo` drives it in a
real Chromium, because a form refused before any request leaves and a control that silently does
nothing look identical in the markup.

It sits beside `Forget` in the menu because they are the same family: both end a stretch of context
where they stand, and what separates them is who writes what the next one opens on.

### Handing off without being asked

The same call as `/handoff`, fired by a number rather than by a person: both go through
`recorded_ask`, so the words a handoff is asked for in are in one place and cannot come apart. What
the rail's card holds is the two settings that decide when.

**`Tending` is the one thing about a session that changes, and it has nowhere else to live.** A
session's `Choice` is recorded before the first message and fixed for life; this is what is being done
*to* a running session, so it has to be changeable or it is not a setting. Neither place this console
otherwise keeps things will take it: `without-durability-sqlite` writes steps with `ON CONFLICT ... DO
UPDATE SET value = workflow_checkpoint.value`, so a key keeps the value it was first given and a
setting saved twice would keep its first answer for ever; and `localStorage` is in a browser where the
worker that acts on this may be another process. So it is two columns on the `sessions` row, arriving
through `ADDED` the way `forked_aside` did, and `tend` is the only thing that writes them.

That is not the second copy the index otherwise refuses. The rule is against copying something already
recorded elsewhere, and this is recorded nowhere else.

**`NULL` reads as a module constant rather than a `Settings` field.** A process-wide answer would be a
second place a session's question is answered, exactly as a process-wide model would be, and nobody
has asked to set these per console. Moving a constant therefore moves every session nobody has told
anything, and a session somebody *has* told stops following it, which is the point of having said
something. `parse_tending` defaults each column on its own, unlike `parse_origin`, which demands its
pair: half an origin is a row nothing here could have written, where a session told one setting and
not the other is ordinary.

**A fork starts on the defaults rather than inheriting.** A reserve is a decision about how much room
one conversation's context has left, and a fork's context is not that conversation's, so carrying the
number across would be carrying an answer to a question the branch has not been asked yet. The default
is on, so what a fork inherits is being looked after.

**Headroom in tokens, never a percentage.** What has to be true is that the handoff run has room to do
its work: the ask, a few tool calls, the returns they bring back, and the document. That is an absolute
quantity and the same one on every model, where a fifth of the window is 40k on a 200k model and 200k
on a 1M one - the same setting re-tuned per model, by somebody who would have to know the absolute
number anyway in order to pick the fraction.

**A window and not a threshold**, which is `Reserve` in `tending.py`: `opens` is where a handoff
becomes worth asking for and `shuts` is where there is no longer room to write one. Two bounds because
a single turn can cross the first and overshoot the second, which one large tool return is enough to
do. `LEAST_ROOM` is the second bound and is a constant rather than a setting, because it is not a
preference: below it a handoff is a request nobody should pay for.

**Past the close the console stops offering, and reaches for nothing smaller.** A cheaper non-agentic
summariser would be a second path that only ever runs when the first is already failing, so nothing
would exercise it and its bugs would surface during the one moment a conversation is least able to
absorb them. What is left is the person's - `forget`, or `fork` - and both cost nothing. There is
deliberately no second stall mechanism either: a request that no longer fits is refused by the
provider, and `turn:{n}:refused:{i}` already says so in the same sentence-instead-of-a-spinner shape.

**`standing` is asked of the reserve rather than of a whole `Tending`**, because where a conversation
is and whether the console will act on it are two questions, and the gauge answers only the first.

**Off where the model has no reference record.** `Conversation.window` is `None` for a model the
database has never heard of, so there is no fraction, no way to know a reserve was crossed, and no
gauge on any rule either - which is what a session showed before there was one.

**Where it says so is the gauge on every rule, not a line in the card.** `reserve_mark` puts a short
bar across the rule at the fraction the reserve opens at, on the same scale `--filled` is drawn
against, so watching the line lengthen toward the mark is watching the handoff approach. That costs no
row and no words, on a control a reader is already reading, where a sentence in the rail said the same
thing once per page in a place nobody is looking. It is drawn only where the switch is on, because a
mark for something that will not happen is a line to explain.

It is an element rather than a second pseudo, and that is forced: the fill is unconditional and draws
nothing at 0%, where a mark has no position to fall back on. Keyed off the style attribute it would be
a selector matching on the text of one, and given a fallback offset it would be a bar parked somewhere
rather than absent.

**The pass decides and the composition root writes**, which is `Crossed`. Asking for a handoff means
putting a message in an inbox, and that *queues* the session, so it is a fact about the queue in front
of a pass rather than about answering one and it belongs where `make_ready` already is. It is also
what makes the decision testable as a value: a test drives one pass and reads what came back, with no
store and no scheduler anywhere near the arithmetic.

**Fired at the boundary that crosses the reserve, not at the start of the next turn**, because the
conversation's prefix is warm right then and may not be when somebody comes back and types. The same
argument that makes a handoff cheap in-session makes it cheap here.

**A turn that opened on a handoff never triggers another**, and that is the whole of what stops this
recursing. The reserve stays crossed for as long as the context is large, so without it the ask turn -
whose own context is the conversation it is summarising - would cross it again the instant it ended,
and so would every turn after that. Asking about the message the turn opened on covers both the ask
and the document. A model that answers the ask in prose instead of calling the tool is therefore not
asked again until a person says something, which is a retry per human action rather than one per turn:
the rule a refusal already follows.

**The settings are snapshotted once at the top of a pass**, injected as `Tendings` the way `Handoffs`,
`Pricer`, `Draining` and `Guiding` are. Once, because a setting re-read at each turn boundary is a
place two writers share, so a switch flicked while a turn was in flight would have that turn answered
under one answer and judged under another. What it costs is that a change takes effect on the next
pass, which is the next turn. `None` is a console that was never given a way to read them, and such a
console tends nothing - the same reading `prices` and `handoffs` already take, and what keeps the
arithmetic inert in every test that does not ask for it by construction rather than by the accident of
some other value being missing.

**The window is asked for at the boundary rather than at the top of the pass**, because the reference
under it is reloadable configuration exactly as the rates are. `Prices.facts` is that one lookup, and
`pricer` now reads it too: what a turn is priced by and how big its window is are the same record read
for two fields, which is `facts_of`'s own argument said one layer in.

**Safe to default on, and only here.** Every other harness defaults its compaction on as a bet that the
summary is good enough, because what the summary replaces is gone. A handoff replaces nothing: it is an
append, the whole conversation stays in the transcript, it still counts toward what the session cost, it
still comes across on a fork, and forking above the boundary carries the entire backlog into a session
whose context holds all of it. The worst a wrong default costs is one turn nobody asked for.

**The pair is asked in three places and rendered once**, which is `tending_fields`: the rail's card
changes a running session's, and the start page and the fork page decide a new one's before it exists.
What a card posts and what a picker posts then cannot come apart, because they are the same two names
from the same call. `form="choosing"` is the only difference, and it is what associates the picker's
copy with a form it is not nested inside, exactly as every other question there does it.

**In the picker it is the last question**, by that page's widest-first order taken to its end: the
workspace decides what a session can touch, the network what it can do with that, the endpoint and
model who answers, the thinking level how hard, and this how long the conversation gets before the
console writes it down. It is also the only one of the six measured against the model above it. Not a
`choosing` group, for `starting_at`'s reason - a number of tokens has no closed set of answers to draw
- so it takes the heading that group would have had, because `auto at reserve` alone says nothing
about what is being automated.

**A fork settles it afresh rather than inheriting it**, and the fork page starts the control on the
parent's so that wanting the same thing needs nothing touched. A reserve is a decision about how much
room one conversation's context has left, and a branch's context is not that conversation's, so
carrying the number across by *default* would be carrying an answer to a question the branch has not
been asked.

**Starting on the defaults writes no column at all.** The picker posts this pair on every session, so
recording it unconditionally would make every column explicit, leave a moved constant reaching
nothing, and make the defaulting branch a path only a database written before this existed can take -
which is a path nothing exercises. Somebody who sets exactly the defaults is indistinguishable from
somebody who left them, and that is the correct reading of both.

**The switch takes effect on the press and the number does not**, which is the difference between a
control you set and one you type into. A checkbox says the whole of what it means the moment it moves,
so waiting for `Set` leaves a console that looks switched off and is not; a number is half-written for
as long as somebody is writing it, so a `change` on that box would post whatever was in it when they
tabbed away. The form's `hx-trigger` is `submit, change from:.tending__switch` for exactly that, and
`submit` stays beside it because `Set` is what the number is sent with and what the form does with no
script at all. Either way the whole form posts, so a number typed and then a switch flicked saves
both rather than losing the typing.

**Which leaves `Set` to say there is something to press.** `data-dirty` is the mark, set by comparing
the box against its own `defaultValue` - which is exactly the `value` the server rendered, so nothing
is kept anywhere and a swap needs no repaint: the box that comes back is a new element carrying the
new default and no mark. Undoing a change unmarks it, because it is a comparison rather than a flag
the first keystroke sets. `--mark` is the gold every control here draws its focus ring in, and the
border alone rather than a fill, since a filled button reads as pressed.

**No spinner on the box, because no increment is right**: a step of 1 is a hundred presses to move a
reserve anywhere worth moving it, and any larger one is a number this console would have to invent.
The type stays `number` for the keypad it asks for on a phone and the `min` it validates against, and
the arrows go - they sit inside the box and take the room a fourth digit needs. Four digits is the
width, which is a bound rather than a guess: the reserve is in thousands and a context window past
1000K is not a thing to size a box for today.

**The card answers itself rather than the transcript.** Nothing about the conversation changed, so
swapping the transcript would replace the whole region in order to show what is already in the rail.
What comes back is the box holding the value as it was recorded, which is worth doing rather than
leaving the browser's own state alone precisely because the box is denominated in thousands.

**The box is in thousands and the record is in tokens.** A reserve is only ever chosen in round
thousands and six digits is a number to count the zeroes of, so the control holds `40` with a `K`
beside it while the value behind it stays in the unit every other figure on the page is in. `THOUSAND`
is the multiplier, named once, so the boundary and the card cannot disagree about which way it goes.

**An unchecked checkbox posts no field**, so an absent `hands_off` on a *form* means off where an absent
`hands_off` in the *column* means the default. Those answer two different questions - what this form
said, against what anybody has ever said - and nothing has to reconcile them, because the boundary
resolves a form to a whole `Tending` and `tend` writes both columns in one statement.

**An empty reserve box is the default and an unusable one is refused**, which is `posted_ref`'s split
exactly. A reserve below `LEAST_ROOM` is refused at the boundary rather than stored and left to
`reserving`, which would answer that the console cannot say where the session stands with nothing
saying the number was why. A *stored* one is never clamped, for the same reason: correcting a number
somebody typed into the shape this console prefers is how a setting stops meaning what it says.

**A session nobody can answer may still be tended**, unlike `/handoff`, which such a session refuses.
One nobody can answer is exactly one somebody might want the console to stop spending on, and that is
the only useful thing left to do with it.

**The card sits under the shelf, and the theme sits below it at the foot.** The shelf is the boundary
in that column: everything above it reads the conversation, and this is the first thing that changes
how the conversation is run. What `margin-top: auto` pins to the bottom is the theme, because it is
the one card there that is not about this conversation at all - it is the reader's, across every
session - so it is what somebody scanning the rail for something about *this* session can skip.

`tending.py` is a module of its own for `thinking.py`'s reason, which is a cycle: the columns live on
the session index and the decision is made inside a pass, so `sessions.py` and `conversation.py` both
read it, and `sessions.py` already reads `conversation.py` for the key scheme. `HANDS_OFF_FIELD` and
`RESERVE_FIELD` live there too, by `roots.py`'s rule: `pages.py` renders the controls and `console.py`
parses them, and the module that owns the vocabulary is the one both can read without closing a ring.

### Merging an aside is a disposition, not a merge

Splicing an aside's turns into its parent is the appealing reading and the wrong one. Those turns
were asked against the history at the branch point, so a parent that has advanced would end up
holding request parts whose context never existed - durably, and invisibly, because `messages`
records the request as well as the answer.

What comes back is a **message** whose text happens to have been written elsewhere. Nothing is
falsified, `Origin.session` already names where it goes, and there is no merge machinery to write.

### Steer

What becomes of an ordinary message that a pass finds while it is working. Nobody asks for one by
name, nothing is written that a message sent to an idle session does not write, and the only thing
that differs is where the entry lands.

**Two keys, and the difference between them is transport and delivery.** The message itself is an
inbox entry, appended from *outside* the pass, because the worker may be in another process and the
store is the only channel between them. `turn:{n}:heard:{i}` is a recorded step saying how far down
that queue the turn had read when it made request `i`, which is what says where a steer went.

**A message cannot be lost at the end of a turn, and nothing has to be claimed for that to hold.**
There is no slot: a message nobody took is still in the queue, and whichever turn opens next opens on
it. That deleted a whole mechanism - a marker the pass wrote into the next steer slot as it stopped
listening, a compare-and-set the store settled between the pass and whoever was typing, and a
`Service.steer` that could answer `None` and make its caller decide again. The window it existed to
close does not exist in a queue.

**Whether a message may be folded in at all is the record's**, not the moment's. `records.Steer` is
one a running turn may take and `records.Prompt` is one it must not, which is how `next` and `forget`
say what they mean; a pass draining its queue stops at the first prompt. See the key scheme.

**It is appended to `request_context.messages` in `before_model_request`, and emphatically not
`ctx.enqueue`, which was tried and delivered every steer one round trip late.** Pydantic AI's own
drain capability is ordered `outermost`, so it empties the queue in *its* `before_model_request`
before this one runs: a message enqueued there misses the request it was read for and lands in the
next one. That cost a round trip nobody asked for, drew the steer's panel below the answer it was
meant to shape, and made `heard:{i}` a claim about a request that never heard it.

Appending is sound for the two reasons the enqueue was reached for. `_agent_graph` builds the request
context with `messages=ctx.state.message_history[:]`, a *copy*, and what `before_model_request`
returns is adopted wholesale (`ctx.state.message_history[:] = messages`), so the steer lands in
`turn:{n}:messages` and the transcript draws it with nothing else taught about it. And a *new*
message is added rather than an existing one mutated, which is the thing the docs actually forbid.
Pydantic AI merges consecutive trailing requests for the wire with the tool parts first, so a steer
travelling beside a batch of results arrives after them in one request and is recorded as its own
message. It is emphatically not `CheckpointedModel.request` either, which an earlier draft of this
file said: anything added at the model reaches that one request and never the recorded history.

**What it reads is the pass's own snapshot rather than the store**, which is `Run.pending`'s own
shape: entries are ordinary records, so they are already in the snapshot the pass loaded on its way
in. A message delivered while the pass was setting up waits for the next one, which is a round trip
that has already been sent either way. That is the coupling the allowance costs and a reason to
leave it at one, since a pass making several requests off one snapshot makes a message wait behind as
many as it has left.

**It is also what deleted the second drain.** There used to be an `after_node_run` that read again
where a run would otherwise have ended, so that a message arriving during the last response could
redirect the run into one more request rather than reaching nobody. Nothing can arrive *during* a
pass: the drain before the first request already sees everything this pass ever will. What used to
cost the ending turn a round trip nobody asked for now opens the turn after it, which is both simpler
and one fewer request.

**The step is what makes the drain replayable, because the queue keeps filling between passes.** A
resumed pass reading live would ask a question the first pass never asked - and `turn:{n}:model:{i}`
is the *answer* to a question, so a replay that asked a different one would be pairing an answer with
a prompt nobody gave. The record is a **cursor**, which is why nothing is carried on the scope any
more: where the record was a list of texts and the next request needed a count of them, it is now a
place in a queue that the next drain simply reads. Reading the inbox is the capability reaching into
conversation state, so it arrives **injected** as `Draining`, symmetric with `Pricer` and for the
same cycle.

`Steering` is its own block type and `steer` its own `Kind`, because `panelled` reads a panel's
kind off its blocks: a steer arriving as `Prose` would be drawn as the model answering itself. It
takes the person's hue, since the axis is who produced the text.

**`heard:{i}` has a reader, and that is what stops a steered message vanishing.** A steer reaches
`turn:{n}:messages` only when the turn *ends*, so a running turn read from its model steps alone
would take somebody's message and show nothing at all until the reply finished - which is fine while
steering is a button somebody presses deliberately and not fine at all when every `Send` may become
one. So `blocks_from` walks the entries too: `told_in` turns the cursors into the steers each request
carried, and one is drawn above the response it shaped, exactly where the settled reading will put
it. What no cursor accounts for goes at the end, which is where a message nobody has read belongs
since nothing has been said since it.

**A prompt is where that stops, and `unread_in` is the rule.** A steer past the last cursor is drawn
as one the running turn may still take; a prompt cannot be folded in by anybody, so it and everything
behind it are drawn as messages waiting for turns of their own. With nothing being answered every
unread message is one of those, which is what keeps a message arriving just after a turn ended from
being drawn as a steer of a turn whose settled reading does not draw it at all.

### Run

The one answer in the menu that is not a message, and the only thing this console does that runs
outside the sandbox everything else runs behind. `! ` typed into an empty box is the shortcut to it.

**As the person and not as the agent, and that is the whole point rather than a gap.** A session's
`isolation` bounds what a *model* asked for, and `sandbox.py` binds the clone read-only precisely so
no tool can write a history no panel shows and no fork inherits. `git commit` and `git push` are the
person's to run, and confining them is what would make this pointless. What it adds to the blast
radius is nothing new: a session on `Filesystem.EVERYTHING` already hands a model the store, every
other conversation, and `config.yaml` with the credentials in it. What it does mean is that who can
reach this console is the whole of what guards it, which was already true and is now worth saying.

**Recorded and not told**, which is the split the key scheme rests on. See there for why that is not
an exception to the one idea: what a command exited with is settled the moment it exits, and nothing
will ever rewrite it.

**Two keys and a background task, because a `pytest` is minutes and somebody is waiting on the POST.**
`Service.run` appends the command to the inbox and returns; `Commands` runs the thing and writes
`result:{entry}` when it is over. The panel is drawn from the entry the instant it lands and
`Command.result is None` is the whole of "still running", exactly as `ToolUse.returned is None` is the
whole of "still out". That is the control-plane argument the worker already answers for cloning, one
step along. The cost, stated: **no live output.** The panel says running and then shows the whole
result, which is right for `git commit` and irritating for a watch; live output needs a channel
outside the checkpoint, which is a different feature.

**It goes where it was run, and that is read rather than recorded.** The store files an entry in the
order it arrived, so counting a turn's model records ahead of the command's entry says how far the
reply had got when somebody typed it, and `alongside` puts the panel back between those two answers.
Nothing had to be written at the time and nothing raced the pass for a position in its sequence,
which is the objection that used to send commands to the end of the turn: collected there, a panel
sank down the page as each later answer landed above it, under a reader who had just run it. Both
readings of a turn come through the same merge with the same positions, so nothing moves when
`turn:{n}:messages` lands.

Which turn a command is in is decided the same way, by where its entry sits between two turns'
opening ones, so nothing has to compute a turn number at the moment it is posted. `Service.run` used
to answer `turns - 1` off a page that could already have moved.

**`Commands` is the one place this console holds work in flight**, which the note at the top of
`service.py` says it does not. Stated rather than quietly excepted: a running command belongs to one
process and does not survive a restart. What keeps it from spreading is that the place holds no
answers - the command and its result are both in the checkpoint - so a page renders the same thing
whichever process is asked, and the task set exists only so a shutdown can reap what it started.

**A shutdown writes the record from `aclose`, not from the task**, and that is not belt and braces: a
task cancelled before it has had a turn on the loop never enters its body at all, so its own `except`
cannot run and nothing would say what became of it. `supply` keeping the first value is what lets the
run that *did* get to say something for itself, with the partial output it managed, keep its answer.
`UNFINISHED` is outside both the range a process can exit with and the negatives a signal produces, so
"the console never learned" is not mistakable for either.

**A status is drawn as the number, never as "failed".** `git diff --quiet` exits 1 to mean there
*are* changes and `grep` exits 1 to mean no match, so flattening it would have this console report a
command doing its job as one that broke.

**The output is drawn open where a tool call's is folded, and the axis is who asked.** A call is the
model reaching for context, so what it returned is something a reader opens to check the work; a
command is a line the person typed, and what it said is the whole of why they typed it. It is still a
`<details>` - it folds, the dock's fold controls reach it, a reader who has read one can put it away
- and it simply does not have to be opened to be read.

**A fold's frame shuts it, and not only its summary.** A summary is one row at the top of a box that
may be several screens of output, so putting a long one away meant scrolling back up to the single
place that would do it; the room around the output is at the *bottom* as well, which is where a reader
who has just read to the end already is. It is one complaint about three boxes: a command and a
document are drawn open so shutting is the press made oftenest there, and a call the reader opened to
check the work is the one whose return runs to hundreds of lines. Two boxes of the same shape
answering the same press differently would be the thing to explain.

**`FRAMES` names the bodies rather than the folds around them**, and that is what let one rule
survive the fold moving up to the panel: each frame shuts whichever `<details>` it is a body of, so a
document's shuts the panel it sits in and a call's shuts the call. Stated that way it also says the
thing a list of folds could not - a panel's own room is the whitespace between the blocks of a
conversation, which is in no frame here, so a press that missed a paragraph cannot fold the reply it
missed. That is the one place this rule must not reach, and it is why the panel joining `FOLDS` did
not put it in `FRAMES`.

What is *in* the box is exempt, and that exemption is the whole of what makes this safe: a press in
there is usually the start of lifting a line out, and a panel that folded under somebody selecting
from it would cost more than the scroll it saves. Two selectors, because the content takes two shapes
- a `pre` for a command's output and a tool's return, rendered prose for a document - and the
exemption is about the content rather than about either shape.
A press that ended a drag is out for the same reason, since
a browser reports one as a click on wherever the pointer came to rest. It shuts and never opens - a
shut panel is a summary and little else - so this is the way out of a tall box rather than the toggle
in a second place, and setting `open` dispatches `toggle`, so the decision is recorded exactly as a
press on the summary is. `TestShuttingAFoldFromItsFrame` pins both kinds and both halves in a real
Chromium, because where the frame stops and the output starts is a fact about the rendered layout that
no markup assertion can see.

**And a command that said nothing says so**, rather than drawing the empty pane that being open
exposed. Plenty of them do - `git diff --quiet` is the gallery's own example, and so is every command
whose whole answer is its exit status - and a blank rectangle under one reads as output that failed
to arrive. It is a stated absence for the same reason `no reference record` is. A command still
*running* gets no body at all, since "said nothing" is a claim about a finished one.

**That is what makes the fold a decision in two directions, and the script keeps both.** A call the
server renders shut can be opened and a command it renders open can be shut, so `mainplate.js` holds
what the reader decided about each fold rather than a set of the ones they unfolded, and a fold nobody
has touched is left to the server. And the id it keeps that under has to be one that does not move:
a command's is its own inbox entry, deliberately not the panel anchor a call's is built on, because a
panel's position moves as a turn is answered and a fold identified by it is a decision the script
loses on the next response. `test_browser.py` pins the two directions beside each other.

A panel's own fold is kept under its anchor, which has that same weakness and takes it knowingly: a
command landing mid-turn shifts every panel after it, so a decision about a panel can be applied to
its neighbour for as long as the turn runs. It is the exposure every permalink and `data-landed` on
this page already has, and the cost of getting it wrong is a fold in the wrong state rather than a
record in the wrong place.

**`! ` is `/run`'s own key and never a parse of the message**, which is the leader rule above applied
to the mode reached oftenest; see there for why a leader is entered in the page rather than stripped
off what was posted, and why the space is what commits it. A command box is also where "only in the
default mode" earns its keep, since `/` is the front of half the paths anybody types.

**And it is the one mode that stays once a command has gone**, which is what `data-staying` is for: a
session that reaches for `Run` reaches for it again a line later, where every other answer in that
menu is a thing somebody meant once.

`requestSubmit(submitter)` and not `requestSubmit()` is load-bearing here and nowhere else:
unattributed it posts no button's pair at all, so a command typed into a command box would arrive as
an ordinary message and be said to the model. `TestTurningTheBoxIntoACommandBox` pins it in a real
Chromium, because that is htmx's and the browser's behaviour rather than ours and looks identical in
the markup either way.

`Run` is the one mode that changes what you are *writing* rather than only where it goes, so the box
takes the terminal's monospace and a heavier edge on top of the button and the sentence every mode
gets.

The mode is entered from the box and left from the box, both by a key pressed while it has the focus,
and it is deliberately *not* stored: it is a mode within a visit, like following the end, rather than
a decision about a conversation.

### The record hangs off a request, not a panel

`Source`, `sourced_at` and the per-panel `recorded` disclosure are gone. A panel is a run of blocks
of one kind and a request is a round trip, so a panel's record was a *slice* of a stored value
reached by indices one walk had to hand another. A request has a key of its own, so `requested_at` is
a lookup, and it answers while the turn is still running - a step is written once and never
rewritten, where `turn:{n}:messages` does not exist until the turn ends.

**A rule per request, with the turn rule being the first one.** That adds no concept: every rule the
transcript has ever drawn already stood at a request boundary, because a turn opens with its first
request. A marker in the panel's own header row was wrong in a way this fixes - one request becomes
as many panels as it has kinds of part, so a marker on one of them attributed a round trip to a
fraction of itself.

It is what `Panel.asked` is for, and why `panelled` cuts by the request *and* the kind rather than by
the kind alone. The cost is a merge: two responses that both answer in prose used to be one panel and
are now two. That is the point rather than a regression, because a merged panel left no gap between
requests for a rule to stand in, and it is also the more accurate reading.

A turn's first rule carries the turn's own facts as well - which turn it is, where it may be forked
from, the tree it started on, and what the **whole turn** spent - and the later ones carry only their
own request's. The tree there comes off the turn's first panel rather than out of request 0, which is
the same key read a request earlier: `turn:{n}:tree:0` is written *before* the model is asked, so a
turn whose first answer has not landed yet still says what it started on. Where the figures on one
rule are a turn's and on the next a request's, the title says which; only `rule--turn` is what the
dock's turn arrows step, or a turn with four round trips in it would give that column four stops.

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

### Where in it, and on what branch

`Choice.base` is a commit-ish the worktree is checked out at and `Choice.branch` is one started
there; both are recorded with the rest of the choice. A blank base is the repository's default branch
as it stands now.

**A blank branch is not blank: every session working in a repository gets one.** `Choice.branching`
fills it with `mainplate/{session id}` where nobody named one, and that is a correction rather than
the first design. A detached `HEAD` was the default until `Run` put `git commit` in the box below the
conversation, and a commit on a detached `HEAD` is reachable only through the reflog - a way to lose
work that nobody should have to know about, offered by the one control that makes committing easy.
Reading, editing and every question `git` answers are all fine detached; committing is the one thing
that is not, and it is now the thing this console invites.

It is named from the **session id** because `git worktree add -b` refuses a name already in use, so
two sessions on one repository must not collide: a name from the session's *title* would collide the
moment two were opened with the same message, so the id would have to be in it anyway. A name
somebody typed always wins over the generated one.

The cost, stated: one local branch per session in the bare clone, accumulating, with nothing pruning
them - `Worktrees.uproot` exists and nothing calls it. `git branch --list 'mainplate/*'` is what
finds them, which is what the prefix is for.

It is filled by `Service.start` and `Service.fork` rather than by `Choice.settled`, and that split is
the same one `settled` already makes: `settled` is a rule about a choice on its own, where this needs
the session's id. A fork takes one of its **own** for the same reason it drops its parent's - the
parent's worktree still holds that name - and dropping without filling would land every fork on a
detached `HEAD`, which is exactly where somebody carries on working.

**They are two questions and not one, because a base cannot check its own branch out.** Git refuses a
branch another worktree already holds, so a session started at `main` and left *on* `main` would stop
the next such session planting at all - and a worktree apiece is the property everything here rests
on. So a base says where to begin and a branch says what to begin, and the words on both controls
have to say so: "leave the worktree detached" read as though the first field answered the second.

Five things there are decided rather than incidental:

- **`Choice.settled` is what stops the form expressing a contradiction**, and it is one call rather
  than a rule per field. A base and a branch are answers *about* a repository, so with none picked
  all three collapse together. That is the same stance `Isolation.settled` already took and it now
  lives in one place with it, applied by `Service.start`, by `Service.fork`, and by `scripts/seed.py`,
  which is the one writer that is not the service.
- **With no repository the two controls are not drawn at all**, so a page never asks a question the
  session does not have, and the record `settled` would drop is never posted in the first place. That
  is not the greying `workspace_cards` was written to undo, and the difference is that nothing is kept
  in step: which fields exist and which branches complete them are one answer, decided in one call
  from the same `repository`, delivered by the one swap picking a card already makes. The block stays
  as an empty anchor, since it is what the next pick targets. What it costs with `mainplate.js` and
  htmx absent is naming a base by hand: a card cannot then reveal the fields, and such a session
  starts on the repository's default branch under the name this console gives it. The completions were
  always the swap's to deliver, so that page was already the lesser half of this control.
- **A fork carries neither**, which is `settled(forked=True)`, and is then given a branch of its own.
  A fork plants at the tree of the turn it re-asks, so a base beside that is a second answer to where
  its files come from; and `git worktree add -b` refuses a branch already in use, so an inherited one
  is a worktree that cannot be planted at all. `Worktrees.plant` ranks its three answers - a tree
  wins, then a base, then the default branch - and `settled` is what makes sure it is never handed
  two.
- **Planting a worktree fetches, whether or not a base was named**, and that is where a person says
  when this console's copy of a repository catches up. Nothing else ever refreshes a clone: it is
  made once and would otherwise answer out of whatever the repository looked like the first time
  anybody used it, for as long as the machine lives. Starting a session is both the moment that is
  affordable and the moment somebody wants current code. `Clones.refresh` fetches into
  `refs/remotes/origin/` and never over `refs/heads/`, since a forcing refspec there would walk over
  a branch a session has been committing to.

  **The no-base arm is the one that is easy to get wrong**, and it was wrong first: a fetch writes
  `refs/remotes/origin/` and leaves the clone's own `HEAD` pointing at the stale `refs/heads/`, so
  planting at `rev-parse HEAD` refreshed the refs and then checked out the commit beside them - a
  round trip that changes nothing, which is worse than not making it. `Worktrees.default_branch`
  reads the *name* out of the clone's `HEAD` symref and `resolve` turns that into the current commit,
  so both arms go down one path. `test_snapshots.py` parametrises over naming a base and naming
  nothing for exactly that reason.

  Two cases skip the fetch and both would be round trips that cannot change an answer: a clone that
  has just been made is current by construction, and a fork plants at a recorded tree, which is an
  object this console wrote and already holds.
- **The branches are offered rather than enumerated, and asked of the remote.** Picking a workspace
  card swaps the block under the cards through `/fragments/branches`, which is the shape the model
  group already has under the endpoint cards and for the same reason: what a repository's branches
  are has a different answer per card, so a page that serialized one list would be completing the
  wrong repository's the moment somebody changed their mind. `Clones.branches` runs `git ls-remote`,
  which transfers no objects, so it needs no clone - which is the point, since the very first session
  on a repository is both the case with no clone and the case where saying where to start matters
  most. It promises not to raise, `forge.offers`-style, so an unreachable host costs a suggestion
  rather than an ability.
- **The field is a search over them, and is the only control in the picker that is not cards.** Every
  other question here is a `choosing` group because every other question has a closed set of answers;
  a starting point does not, since a tag, a hash or `main~3` is still typed. So the branches are
  narrowed under the box rather than drawn as cards beside it: cards *are* the answer everywhere else,
  where these only fill in the one answer, and a card posting `base` beside a field posting `base`
  would be two places one value could come from.

  It is rendered **twice** and that is not a copy to keep in step: a `<datalist>`, which is the whole
  of what the field offers with `mainplate.js` absent, and a list the script narrows. Both come from
  one `branches` argument in one call, and exactly one is ever live, because `paintBranches` removes
  the `list` attribute at the moment it takes over - two dropdowns over one box is one more than a
  reader can use. The narrowing matches anywhere in a name rather than at the front, because a branch
  is called `feature/the-thing` far more often than it is called for the word you remember.

  Two things there are decided. The list is `position: absolute`, so typing a letter does not push
  the endpoint and the model down the page. And Enter is swallowed **only** while the reader is
  actually on an entry, because this field's form is the one that starts the session: swallowing it
  whenever the list was open would make the obvious key do nothing on a page whose whole point is
  that form. `TestNarrowingTheBranches` drives all of it in a real Chromium, because the list is
  `hidden` in what the server sends and everything that makes it a search happens after that.
- **The values are refused at the form and re-parsed off the record.** Both become `git` arguments,
  so what they must not be is an *option*: `parse_commitish` and `parse_branch` in `snapshots.py` are
  anchored patterns that refuse a leading `-` along with everything nobody types on purpose. Refused
  rather than dropped at the boundary, because a blank box is somebody taking the default where
  `my branch` is somebody who meant something; and re-parsed on the way out, because a checkpoint
  written by `scripts/seed.py` or edited by hand has been through no boundary at all.

They are recorded as the words somebody typed rather than as what they resolved to, deliberately:
what a session says about itself is the answer it was given, and `main` is a truer record of that
intent than the hash `main` happened to be at that minute. The hash is in `turn:0:tree:0` for anybody
who wants it.

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

Snapshots are **gitignore-aware**, deliberately. A tree holds what is version-controlled and nothing
else, so what a fork checks out is the source as that turn saw it and never a `.venv`, a build
directory, or an untracked file holding a secret. It is the contract git already offers, so nobody
has to learn a second one.

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

**There is no rewind, and that is settled rather than pending.** `Worktree.restore` existed for one
and was deleted unused, because forking already delivers the whole of what a rewind was for: going
back to before turn 3 with the files as they were is `fork(at=3)`, which plants a clean worktree at
`turn:3:tree:0` and leaves the original readable beside it.

Putting a session back *in place* would cost two things this console is built on. `Service.token`
counts recorded rows and is sound only because the checkpoint is append-only - "the only way this
moves is a record that did not exist before" - and truncation makes the count fall, so rewinding ten
steps to five and then running five more returns it to ten and a live connection polling either side
of that window sends nothing and silently stops updating. And a fork records `Origin(session, turn)`,
so rewinding a parent past a turn some branch left from leaves the sidebar drawing a fork off a turn
that no longer exists. A fork is a copy of an immutable prefix precisely so two sessions can never
disagree; making the prefix mutable is what that rests on.

pi.dev reaches the same place from a different design: its sessions are trees inside one file
(`id`/`parentId`, the active leaf is the position), and even there `/tree` navigation branches rather
than destructively editing a path. Its three operations map onto ours - `/fork` is `fork(at=turn)`,
`/clone` is `fork(at=turns)`, and `/tree` is the sidebar, which already draws branches nested under
their parent labelled with the turn they left at.

## What a session is told

`guidance.py` reads the words a session is answered under, and there are two scopes. **Console
guidance** is the operator's own, every `.md` file under `<config home>/mainplate/guidance/`, sorted
by path and concatenated. **Repository guidance** is the project's own `AGENTS.md`, read out of the
worktree the session works in. Both go into the agent's `instructions`.

**The repository is last, so it wins.** That is not a claim about trust: a repository is right about
itself, which is the local-conventions rule one layer out, and it is why escapement puts the gains
in the repository rather than in the server. What the console holds is how to work; what the
repository holds is what this project is.

**`AGENTS.md`, and a name this console invented for nobody.** A `.mainplate/` directory would be
knowledge only mainplate can read, and the whole reason to write a line in the repository rather
than in a prompt is that it survives the tool being turned off. `CLAUDE.md` is the fallback where
there is no `AGENTS.md`, and only the fallback: a repository carrying both is carrying one set of
instructions twice, and pi reads the pair and warns about exactly that in its own docs.

**Instructions and not a message, which is a decision about caching before it is one about
placement.** Pydantic AI's `instructions` are a per-request parameter it re-renders on every request,
never a message at a position, so they sit in front of the cached prefix. What that buys is that
guidance is present on every request without being appended anywhere, and what it costs is that
changing it invalidates the cache from the system block onward. Anything that arrives *mid*
conversation therefore cannot go here: it would re-price the whole conversation, and it belongs
appended as a `SystemPromptPart`, which is one more entry at the end and leaves the cached prefix the
top-level `system` parameter sits in untouched. That is what the directory-scoped guidance below
does; see there for how differently that part reaches the model depending on which model it is, and
why nothing may depend on it.

**Composed once per stretch of context, and recorded**, under `instructions:{n}` where `n` is the
turn that stretch began at. Instructions sit in front of the cached prefix, so composing them again
on a later turn re-prices every remaining request the moment anything under them has moved, and a
session working on a repository's own `AGENTS.md` moves it constantly. Re-reading buys nothing
against that, because the thing most likely to have edited the file is the model, and it knows what
it wrote.

**A stretch of context and not a whole session, because a forget ends one.** That is a weaker-sounding
promise that costs nothing: what recomposing spends is the requests that would have read the prefix
from cache, and a forget has just thrown the whole prefix away. Composing again exactly there is
therefore free, and it is the one moment a reader might reasonably expect edited guidance to be
picked up. `history_began` asks the same `forgets` predicate `reached` clears history on, so the two
cannot disagree about where a context starts.

Four details there are decided:

- **It is a `Run.step`**, so the first pass composes and every later one replays. That also means it
  cannot be composed twice under one key in a pass, which is why a pass answering two turns of one
  stretch memoises what it composed and a pass crossing a forget composes a second time.
- **It records exactly what the model is sent**, the notes about this session's worktree and network
  included, and `agent_for` speaks it verbatim. Composed out there instead, those notes would be a
  sentence the model carried that no record held - so a page could report only what a *turn's*
  messages held, which is nothing until a turn has landed - and they would be recomposed on every
  turn from live state, in front of a cached prefix they are supposed to sit still behind. The reason
  they cannot simply be appended in `agent_for` on top of a record that already holds them is the
  replay: a second pass hands it back the recorded string and would get them twice.
- **What decides the note and what decides the toolset is one function.** They are read off one
  `Choice.isolation` and one worktree, and `agent.reaching` answers both at once, because in two
  `match` statements they would be two places to keep in step over one answer and the failure would
  be quiet - a session told it has a scratch directory whose tools cannot reach one.
- **The key is not turn-prefixed**, deliberately. `before` copies turn-prefixed keys by shape, so a
  turn-shaped name would carry a parent's instructions into a fork that may have attached a
  repository the parent never had. Named this way a fork composes its own.
- **`working_note` names a session's places and never paths them**, which is `roots.py`'s whole
  argument said one layer out and a fact about the cache besides. A worktree sits under 32 hex
  characters of session id, so printing the path invites the failure the root names were built to
  prevent; and instructions are the per-request parameter Pydantic AI renders in front of the entire
  cached prefix, so a sentence naming one session's directories makes that session's prefix unlike
  every other's. With the paths out, the note is a pure function of the isolation: two sessions of
  the same shape compose byte-identical instructions, and a fork's first request reads its parent's
  prefix from cache rather than paying full price for the whole conversation again. A relative path
  already lands in the worktree and `$MAINPLATE_WORKTREE` already names it in a command, so nothing
  was given up. `test_what_a_stretch_records_is_exactly_what_its_requests_carried` asserts the path
  is absent beside its control that the note is present, so an emptied note cannot pass it.

**Console guidance is read once at startup instead, and nothing watches it.** `just serve` watches
`src/mainplate`, and this lives under the config home, so an edit there wants the process restarted
by hand rather than arriving on the next save. That is the ordinary reloadable-configuration answer
of "restart to apply", said out loud rather than left to be discovered: the failure it prevents is
editing a rule, seeing the dev server reload for an unrelated reason, and believing the new rule is
in force.

**Frontmatter is taken off.** It is addressed to whatever loads the file rather than to the model,
so passing a `paths:` list on spends a context window on it and invites an answer about it. Only a
block that opens the file and closes counts, so a document whose first line is a rule of dashes is
left as written.

**The system prompt is drawn as a panel**, under the rule that opens the stretch it belongs to,
folded, as the Markdown it is. A console that shows what a model answered and hides what it was told
is showing half of how a turn happened. Five things there are decided:

- **It is read out of `instructions:{n}` and never out of a turn.** That record is written before the
  stretch's first request, where a turn's messages do not exist until it ends, so the panel is on the
  page while a turn is being answered rather than only afterwards. What makes the record worth
  trusting for this is that `agent_for` speaks it verbatim: nothing is composed on top of it, so what
  a stretch records and what its requests carried are one string.
  `test_what_a_stretch_records_is_exactly_what_its_requests_carried` holds the two ends against each
  other, with the worktree note as the control, since that is the part that used to be added after
  the record was written.
- **One per stretch, under its own rule.** A forget composes again, so a single panel above
  everything would stand the newest instructions over turns answered under an older one. Under the
  rule the reader gets the order it happened in: the boundary, then what the model is told from here,
  then the message.
- **It is not a `Panel`.** A panel's identity is its turn and its position, and this belongs to the
  first but not the second; giving it one would have taken `#N.0` off the person's opening message,
  which the fork link and every permalink already point at. `waiting_panel` is the same split, and
  `Transcript.system_prompts` is where it rides instead. It is `system-prompt` in `data-kind`, in the
  anchor and in the class, and `system prompt` on the page, because a reader who reaches for `#told`
  is reaching for a word this console prints nowhere.
- **A stretch nothing has composed for yet carries the working dots on the panel's own row**, in the
  opening line's place, with the panel drawn shut. Composing reads a repository the pass is the one
  to fetch: a session's first message is on the page before there is anything to put under it, and on
  a fresh clone that gap is minutes. Drawn rather than left out, so what is coming is visible from
  the moment the message is; on the row rather than inside an open panel, because a panel opened to
  show three dots is a row spent on three dots, and because a fold whose default *moves* is one the
  console cannot draw either way once a morph has recorded the state it delivered. `instructed_in` is
  what keeps it off a stretch nothing will ever compose for, which is every turn answered before this
  console recorded instructions at all. Those draw no panel, which is a loss taken knowingly against
  a spinner that would never resolve. `opening.html` in the gallery is that state to look at.
- **Rendered as the Markdown it is, and that costs nothing about what was sent.** What is in the
  panel is `.md` files concatenated - the operator's guidance and the repository's `AGENTS.md` - so
  its headings, lists and fences are the structure their authors wrote, and a wall of `##` is the one
  reading of it nobody meant. The claim that this is what was *sent* is kept by the source riding
  along in `data-markdown`, which is what the copy button hands back, and by the raw record on the
  rule one step further out. The block is deliberately uncapped and does not scroll: a reader who
  opened the panel asked for all of it, and a box that scrolls has no still corner for the copy
  button to pin to.

  **It is a `.block` and not bare prose**, which is what puts a copy button on the panel: the script
  seats one against a panel's blocks, and the whole prompt is what somebody reaches for.

  **It carries no fold of its own; the panel is its fold.** It is drawn as the same `block--document`
  the guidance a turn is handed mid-way is drawn as, because on the page the two are the same thing,
  and what separates them is where each sits in the request, which is what the *panel* around each
  says. The document's opening line is on the panel's row, which is what identifies it without opening
  it. See "Every panel folds, from its own row".

The panel takes the person's hue, by the same rule as `command`: the axis is who produced the text,
and what is in a system prompt was written by the operator and by whoever wrote the repository's
`AGENTS.md`. The console composed it; it did not write it.

**A message's newlines are the author's and a document's are its wrapping**, which is the whole
difference between `markup.py`'s two converters. A chat box promises that a newline is a newline,
because Markdown's own rule - a line break needs two trailing spaces - is a rule about *documents*
that nobody typing a message knows. A guidance file is a document, soft-wrapped at whatever width its
author's editor uses, so `nl2br` there draws one paragraph as a column of ragged lines saying nothing
about how it was written. `as_message` and `as_document` are the pair, and `written(text,
document=...)` is where a caller says which it has. Everything else about the two is one list, the
extensions and the sanitiser included, because the *safety* of this does not depend on where the text
came from: a guidance file is written by whoever wrote the repository, which is the same trust as
whatever reached the message box. `test_markup.py` asks every sanitiser question of both.

### Guidance elsewhere in the repository

A monorepo puts rules beside the part they are about, and loading all of them would swamp a context
window with instructions about apps this session will never touch. **Load-time scoping cannot help
here**, and that is worth knowing before reaching for it: "does this repository have a web app" is
always yes, where the question is whether *this session* is working on it, which nothing resolvable
at load time can answer. So nesting is not a scoping mechanism this console implements. It is a
placement convention repositories already have, and two things carry it.

**An index, in the instructions, on every request.** One row per nested guidance file: its path, and
a `description` from its own frontmatter where it has one. That `apps/web` has conventions is one
line and what they are is a page, so the line rides in the prefix and the page is read when it is
wanted. It also serves the goal path scoping never did, which is knowing a part of the repository
*has* rules before reaching in and breaking them. Asked of git rather than walked, for the reason
`list` asks: a walk descends a `.venv` looking for a file that is never in one.

**And the file itself, handed over on approach.** `approaching` reads which paths the model has named
to a file tool and hands over the guidance covering them, from the root down. Six things there are
decided:

- **It is delivered in `before_model_request`, not attached to a tool return.** A batch of calls and
  the reply to them are one exchange, so there is no earlier moment: a tool return and the next
  request arrive together. Delivered here it needs no change to `Files` at all, it is the console's
  own voice rather than text smuggled into a tool's output, and it is where a tree diff would go if
  the `bash` hole ever needs closing.
- **The history is the ledger.** `delivered_in` asks whether the block is already in what the model
  will be handed, and that one question answers every case: the console delivered it, the model read
  the file itself off the index, the model *wrote* the file, a fork carried it across in the copied
  prefix, or a `forget` dropped it and it is delivered again. A set kept on the session would be
  wrong rather than merely redundant, since it would survive a boundary and leave the model working
  without guidance it can no longer see. `test_guidance.py` pins the forget case with a control, so
  it cannot pass by comparing two empty answers.
- **A `SystemPromptPart` rather than a `UserPromptPart`.** Nobody typed it, so `interjected` tells the
  two apart by which part carried them and the transcript draws guidance as its own kind rather than
  as the person having said it. It is also what leaves the cached prefix alone, and *that* is the
  whole reason this is appended rather than added to the instructions: appended it is one more entry
  at the end, where an instruction re-prices every request from the system block onward.

  **How it reaches the model is the provider's business, and it varies more than is comfortable.**
  Pydantic AI's `prepare_messages` renders a non-leading system part as a real `{"role": "system"}`
  entry only where the profile sets `supports_inline_system_prompts`: always on the OpenAI wire, and
  on Anthropic only for the four models in `_INLINE_SYSTEM_PROMPT_MODEL_PREFIXES` (`claude-fable-5`,
  `claude-mythos-5`, `claude-opus-4-8`, `claude-opus-5`). Everywhere else - `claude-sonnet-4-6` and
  `claude-sonnet-5` included, which is most of what sessions here run on - it is rewritten as a
  `<system>`-tagged `UserPromptPart`. Nothing in this console may depend on which, and the caching
  argument above is the one that holds either way.
- **It is injected as `Guiding`**, symmetric with `Pricer` and `Draining` and for the same cycle:
  the capability stays ignorant of what a guidance file is and one instance still serves every
  session.
- **`bash` reaches nothing here, and that is stated rather than left to be found.** Its argv is the
  model's, so a path inside it is a string this console has no business parsing. That is the hole the
  index covers, and a diff between consecutive `turn:{n}:tree:{i}` would close it for writes: the
  snapshots are already taken, so it is available whenever it earns its keep.
- **The delivered block renders**, as a `guidance` panel at the position it was delivered. A console
  that shows what a model answered and hides what it was handed is showing half of how a turn
  happened, and this is the half that arrives mid-turn. Drawn as the Markdown it is, by the standing
  prompt's own argument one panel up: it is an `AGENTS.md` with a line of the console's own in front
  of it, drawn as the same `block--document`, in a panel drawn shut and named on its row by the line
  it opens with.

  **`guidance` and not `system-prompt`, and the line between them is mechanical rather than
  editorial.** A system prompt is `instructions`, a per-request parameter re-rendered on every
  request in front of the cached prefix; this is a `SystemPromptPart` appended into the history at a
  position. Two mechanisms, two places in the request, two things a reader may want to quiet apart in
  the key - so two words, by the rule that keeps `steer` apart from `prompt`. What they share is the
  shape on the page, which is why they share a block and not a kind.

  **The two are not weighted alike by the model either, and that is an argument for the split rather
  than against it - but it is the provider's answer and not ours.** Pydantic AI measured it and
  recorded the result in `_INLINE_SYSTEM_PROMPT_MODEL_PREFIXES`: on `claude-opus-5` an inline system
  entry carries enough authority to lift a restriction the top-level prompt set, every time, where
  `claude-sonnet-5` accepts the same entry with a 200 and ignores it - so Sonnet gets the
  `<system>`-tagged user text instead, on which a plain formatting instruction actually lands more
  often. So the same guidance is above the standing prompt on one model and in the user's voice on
  another. Write nothing here that turns on it; what this console owns is where each one goes, and
  the caching consequence of that is the same everywhere.

## How a model names a line

Every tool lives under `tools/`, one package per tool, as `tools/{name}/{module}.py`. Only the
constructor reaches the harness: `tools/__init__.py` exports the constructors and the values they
take and nothing else, so `agent.py` asks for the tools a workspace affords without knowing that
editing is anchored, that a worktree root has to be resolved against, or how a command is confined. A
further tool is a new package beside `files/`, `bash/` and `handoff/` and one more name in that list,
rather than an edit to anything that already imports them.

**`handoff/` is the one whose subject is the conversation rather than the machine**, which is why it
alone is not conditioned on the isolation: every session has a conversation. See the handoff section
for why it is in every session's cached prefix, and why it takes a document as an argument rather
than reading one out of the turn's prose.

Within the files one, `tools/files/anchors.py` is pure and `tools/files/tools.py` is the shell
around it, which is the split that lets the interesting half be tested with a list of strings. A
**Which tools a session gets over its *files* is decided by its `isolation`, not by whether it picked
a repository.** A session on `WORKTREE` gets `list`, `read`, `edit` and `create` over its worktree
and its scratch; one on `EVERYTHING` gets the same four over `/`, where `list` refuses because
nothing there is in git; one on `NOTHING` gets **none of them**, because tools that can only fail are
worse than none and cost a description on every request. `bash` is added to the first two wherever
there is a sandbox to run it in.

**`hand_off` is outside that entirely and is in every session**, `NOTHING` included, so a session
with no files still has exactly one toolset rather than none. It is not an exception to the rule
above but a different subject: what it reaches is the conversation, and every session has one. See
the handoff section for why it has to be in the prefix from the first request rather than added when
a handoff is wanted.

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
conversation is about. Refusing `list` in the tool rather than leaving it to `entries` is the usual
reason: "not a repository" arrives from git as a `ListingFailed` fault and ends the turn, where a
`Refused` tells the model to reach for `bash` instead.

**Anywhere else is reached by naming the root, not by writing its path out.** `read`, `edit` and
`create` take a `root`, which says which place a *relative* path joins and nothing else: an absolute
path still lands where it points, so naming one cannot redirect a path that already says where it
goes. A worktree sits under 32 hex characters of session id, and a model reproducing those from
memory eventually reproduces them wrong, which is a refusal it then has to recover from at the cost
of a round trip. Four things there are decided:

- **The names are `roots.py`, which both the tools and the sandbox read.** A model reaches the same
  directory two ways, by naming it to a tool and through `$MAINPLATE_SCRATCH` in a command, and those
  are two surfaces of one answer. Written separately they are two lists to keep in step and the
  failure is quiet. It is the `StepKind` move, and it lives in a module of its own for `thinking.py`'s
  reason: the file tools know nothing about sandboxes and the sandbox knows nothing about `Files`.
- **A root owns its own name**, as a property on each arm beside `GitTracked.entries`, so adding a
  kind of place brings its name with it rather than needing an entry somewhere else.
- **An unknown root is refused with the ones this session has.** That is what lets one tool
  description serve every session: what a session's places are called varies and a toolset's
  descriptions do not, so the vocabulary is taught at the one moment it is got wrong.
- **What comes back names the root only where it is not the first.** A bare `notes.md` in a return is
  two different files once a session has two roots; a root named on every line stops being read. Same
  rule as `Reachable.labelled`.

In the sandbox the name is a `Bind` field, so an environment variable is only ever a name for a path
that sandbox actually has. The clone gets none deliberately: it is bound so git works, not so anybody
addresses it, and a name would invite a write to the one place the read-only bind exists to refuse.
`/` gets none either, since a variable holding `/` names what every path already starts with.

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

**None of these axes bind a command the *person* runs**, which is the composer's `Run`: that runs
outside the sandbox entirely, as the service user, in the session's worktree. It is not a hole in
this, it is what this is for - the read-only clone bound here is what stops a *tool* writing a
history no panel shows, and `git commit` is not a tool. The authority it grants is what the paragraph
above already grants a model, so what actually guards it is who can reach the console. See the Run
section.

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
going in, and comes back as an `object` needing a `Parse` on the way out. That is what
`Record.recorded` is, so every step here pairs one with a matching parser, on the pass that ran it as
much as on the one that resumed. A tool return goes through `to_jsonable_python` and comes back
unnarrowed *inside* its record, because a toolset is unrelated functions with unrelated return types
and there is no one type to validate against; both passes see the round trip, so they agree.

This is `step` and not `transact`, so a tool is **at-least-once**: a crash between the tool
returning and the record landing re-runs it next pass. That window is one store round trip, and
anchored editing is what makes the failure mild rather than corrupting, since an edit whose anchors
no longer resolve is refused rather than applied somewhere wrong.

## What one pass does

**A pass is one live model request and the tool batch behind it**, not a whole turn, and the lease
is why. A pass that was a whole conversation had to fit inside `Settings.lease`, which made that
number a bet on the longest turn anybody would ever ask for: a turn with enough round trips to cross
it is fenced on its next write, redelivered, replayed, and runs into the same wall again. Cut per
request, what the lease has to cover is one round trip and the batch after it, which is a bound that
can be reasoned about rather than guessed at.

`Settings.allowance` is the whole of it: **one setting with a live value, never a second code
path.** `CheckpointedModel.request` spends one on each *live* request and `Allowance.take` refuses
the one that would go past it, which raises `AllowanceSpent` and unwinds `agent.run`; `conversing`
catches that outside the run and returns `Progressed`. An allowance of `None` is unbounded, which is
exactly what a pass was before there was a number here, so the tradeoff is a dial rather than a
branch.

Four things there are decided rather than incidental:

- **Only live requests count.** A replayed one pays nobody and takes no time worth bounding, so a
  resumed pass gets *further* than the last rather than stopping where it did. Whether a request is
  live is what its key says, which is why the key is taken before the check and the snapshot in
  front of it is taken after: a request that was never made leaves no tree recorded ahead of it.
- **The refusal happens before anything at all is recorded for the request**, which is earlier than
  the request itself. `before_model_request` runs first and records how far the turn has read, so a
  pass that drained and *then* refused would leave a cursor for a request nobody made; the next pass
  replays it, and a message delivered meanwhile waits for the request after the one it should have
  reached. So `Stepping.allow` is called there, before the drain, and again at the request, and it
  is idempotent per request key. Both call sites are load-bearing: the first for *when* a refusal
  happens, the second because that is the request. Found by driving a real worker, not by reading.
- **`AllowanceSpent` is deliberately not a `Suspended`.** Nothing is owed by the outside world, so
  there is no key to report and nothing for `arrive` to answer. Being an ordinary exception is also
  what keeps `resume`'s `Swallowed` check live: a body that returns having caught a real suspension
  is refused, and a `-> Never` body could never trigger that at all.
- **The request stays inside the pass.** Dispatching it to a pool and suspending on `Run.awaiting`
  works and is worse, because the lease is what recovers interrupted work: a request outside the
  pass is a request outside the lease, and a pass that dispatched and reported `Blocked` has had its
  delivery acknowledged with nothing scheduled, so a process that dies with work in flight leaves a
  session waiting for ever. Recovering that needs a reconciler, idempotent dispatch, and a durable
  leased in-flight marker, which is a second queue. Under the claim, a dead process is an expired
  claim and `reclaim` redelivers. **Never write a placeholder record for a model request** if that
  is ever revisited: `supply` keeps the first value, so an `UNFINISHED` under `turn:{n}:model:{i}` is
  permanent and the turn can never be retried. `Commands` writes one from `aclose` and that
  precedent does not transfer, because a command's result is terminal where a request's is not.
- **The allowance is the pass's, not the turn's.** A pass that finds two prompts already recorded
  answers two turns, and a fresh count per turn would let it make one live request for each under a
  lease sized for one. So `conversing` makes one `Allowance` per pass and hands the same one to
  every `stepping` scope in it.

**A pass that returns is `Completed`, which the worker answers by doing nothing**, so `readying` in
`app.py` is what carries the turn on: it asks the scheduler to make the session ready again. That is
in the composition root rather than in `conversation.py`, because the body is about answering a
session and this is about the queue in front of it. Asked for from inside the pass while the claim
is still held, which is the queue's documented shape rather than a race: `make_ready` is a plain
upsert onto a running pass's row and the pass's own `done` is conditional on the visibility it took,
so the row this writes survives. What it costs is the queue's 50ms poll per request, measured, which
is nothing against a round trip that takes seconds. `test_app.py` is what fails when it goes, and it
fails as a timeout, because the failure it guards is a session that stops mid-turn with nothing
anywhere saying so.

**A pass that cannot go on comes back `Stalled` instead, and that is a different instruction rather
than a shade of the same one.** `readying` matches on which it got: `Progressed` asks the scheduler
to make the session ready again, and `Stalled` asks for nothing, because the pass that followed would
put an identical question to the provider and get an identical answer. `Ended` is the union, named
for the pass rather than `Outcome`, which already means how a tool call went here and what the
mechanism made of a pass in `without-durability`.

**The bug it closes is invisible rather than loud.** `without-durability`'s worker leaves a delivery
unanswered when a pass raises, deliberately, since it cannot tell a workflow's own failure from a
store that was briefly unreachable, and its own docstring names the cost: a workflow that fails on
every pass is retried once per lease for as long as it keeps failing, and the count belongs in the
checkpoint. So a refusal is written to the checkpoint, once, and the loop stops there.

**Terminal is a 4xx and transient is everything else**, which `terminally` decides. A 4xx is the
provider saying the request itself is wrong - a prompt over the window, a model it will not route, a
body it will not parse - and no amount of asking again fixes any of those; the exceptions are the
4xx codes that describe the moment rather than the request (`408`, `409`, `425`, `429`), and a
redelivery is exactly what each asks for. **The default is transient**, which is the safe way round:
read as terminal, a transient error stalls a session that would have recovered on its own, where the
other way costs a redelivery per lease until somebody looks.

**`turn:{n}:refused:{i}` is named after the request rather than the turn**, and it is a settled value
in a write-once store for a reason worth keeping: what a request is made of is the recorded history
and the recorded message, neither of which will ever change, so a turn refused at request `i` is
refused at request `i` on every later pass. Sharing the index with `turn:{n}:model:{i}` is the point,
since the two are the question and the reason there is no answer and exactly one of them exists.
`CheckpointedModel.request` reads it *before* the allowance and the snapshot, so a pass spends
nothing on a question already answered and captures no tree in front of a request nobody makes.

**A person can still ask again, and that is the point rather than a gap.** Writing a message queues
the session, so a refusal costs one attempt per human action rather than one per lease - and that
attempt is free, since the recorded refusal answers it without reaching a provider. What gets a
conversation *past* a refused turn is `fork` at it, which drops the turn's own requests while keeping
everything under them, and the sentence on the page says so. `refusal_in` is what the page reads, of
the turn being answered and no other: a refusal on a turn that later answered is history, and the
transcript is where history goes.

**What replay costs was measured rather than reasoned about**, and it is not where it looks. Each
pass re-runs `converse` from the top, so a turn of *n* requests replays O(n²) steps; record parsing
is 1.4% of a 40-round turn and `load` is 0.8% to 2.4% against real SQLite. The dominant term is
Pydantic AI rebuilding its frozen `RunContext` once per capability per hook, which is upstream's.
Absolute figures: about 65ms per pass, 2.2s spread across a 40-round turn that costs minutes of
provider time. **Do not build a record cache or a fetch-only-what-is-missing store for this**: loads
are already linear and parsing is 1.4%, so the quadratic is somewhere a store-level cache cannot
reach, and raising the allowance cuts the pass count, the graph replay and the re-loading together.
Revisit only if very large `read` returns become common.

**The tests default to unbounded and the console ships one.** A test about a conversation drives a
whole turn in one pass and says nothing about how a pass is cut; `TestWhatOnePassDoes` is where the
two are pinned against each other, and what it asserts is that the allowance decides how much one
pass does and *nothing* about what the conversation comes to.

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

**`transcript_region` takes a whole `Conversation` rather than the things drawn out of one**, because
every caller had one in hand and was taking it apart the same way. What the region needs is the
session, what was said, whether it is stalled, the model's window and where its reserve falls, and
five arguments derived from one value are five chances for a caller to pair a transcript with another
session's window.

**One connection now drives two regions**, which is what `partial` was always for: the transcript, and
the cache note in the composer. See "Whether the cache is still warm" for why that one cannot simply
be rendered with the page.

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
  blocks rather than the whole panel because the header row - a role and an anchor - is the same on
  every render, so nothing there is ever news either.
  The first render marks nothing, since every panel is new to the script then and a conversation
  flashing top to bottom points at everything.

**A copy button sits on every panel and inside every block of code in one, and they are one control
in two places rather than two controls.** One look, one listener, one clipboard, one way of saying it
worked; what each copies is decided by where it sits, so the one in a fence hands over the fence and
the panel's own hands over what the panel says. Five things there are decided:

- **The script seats them, and the server draws none of them.** A fence is markup the Markdown
  renderer produced, so `pages.py` has no node to hang a button on inside one; rendering the panel's
  and seating the code's would be two mechanisms for one thing when the seating has to exist anyway.
  They come off before a swap and go back after it, exactly as the search marks do and for the same
  reason: a node the server never sent is a node a morph should not be reconciling.
- **What comes out is what was *written*, not what is drawn.** A message is rendered Markdown and
  the rendering is lossy in exactly the way somebody copying cares about, so a block that was
  Markdown carries its source in `data-markdown` and that is what the button hands over. It is not a
  second copy of anything: it is the same value the element was built from, put into the same render,
  and nothing else reads it. Only the kinds that *are* Markdown, since a tool's arguments and its
  return are already shown verbatim and a fence renders as the characters it was written with.
  Measured on the gallery's own conversation, carrying the sources costs the page 16%, most of that
  the system prompt, which is the longest Markdown on any page and the one a reader is least likely to
  be copying from. It is carried all the same: a fold nobody opened costs bytes, and a fold somebody
  did open with no way to lift the prompt out of it costs the control.
- **Where there is no source to carry it is `textContent`, never `innerText`.** `innerText` is what
  is *rendered*, so a folded call would copy as its summary alone and one button would answer two
  different things a click apart. The buttons are taken back out of the text first, since one seated
  inside a fence is inside the very text that fence hands over.
- **The confirmation is a value, projected.** A running turn morphs the transcript every time it
  records anything, which is exactly when somebody is lifting a result out of it, so `copied` names
  the button rather than marking it - the panel it is on and where in the panel it sits.
- **A block that scrolls has no still corner to pin to.** An absolutely positioned child of a scroll
  container travels with the content, so `.text pre` no longer scrolls and its `code` does, and the
  raw record on a rule - a bounded box that genuinely scrolls - gets no button at all.

The panel's own stands in the row of facts just left of the permalink, and the code's is inset inward
from its block's corner on both axes, which is where a reader looks for each. **The panel keeps the
spacing it has always had, and that is the point of putting the button in a row that already
exists.** Hung off the top edge of the text instead - which is where scriptorium puts it, riding the
border of a block that has one - it needs room, and the room costs every panel a strip of empty page
between its title and what it says: a change to the whole transcript's rhythm bought for one control.
The permalink gives up its own `margin-left: auto` only where the button is there to take it over, so
a page rendered with the script absent still has it flush right.

**Following the end is being at the end**, decided in both directions by where the reader has
scrolled, and re-entered by sending a message. `land` therefore has to route its scroll through
`scrolling()` like `toEnd` does: without it, landing on the *last* panel puts the reader at the
bottom and the scroll listener switches following back on at the very moment they asked to be
somewhere in particular.

The rail (search, key, dock, shelf, handoff, theme) lives **outside** the region that swaps, so no control is
rebuilt under a reader's finger. What it projects back *onto* the transcript — search marks, the
panel landed on, which kinds are muted, what is folded — cannot live in the markup
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

**The picker is ordered widest-first: workspace, network, endpoint, model, thinking, handoff**, and
then the name and the message box, which are the composer's rather than the picker's. What files a
session has is the broadest thing about it and is one question rather than two, so it leads; the
network follows because it is the other thing deciding what the agent can do at all, where the
endpoint and the model only decide who answers; the endpoint and the model are adjacent because they
are a pair, the list being whatever the endpoint above it offers; the thinking level is a setting *on*
the model, so it sits under it; and the handoff reserve is measured *against* the model, so it comes
after both.

**Every question the picker asks with a closed set of answers is one component.** `choosing` in
`pages.py` takes a legend, a toggle id, the names on offer and a body of cards, and gives back a group
that folds to what is picked, says how many options it has, and can be narrowed by typing. The
workspace, the network, the endpoint, the model and the thinking level are all built from it, and that
is why none of them is a `<select>`: a select renders its options as text in every browser, so it
could carry neither the forge a repository came from, nor the sentence under a level that is not
one, nor the fold. Having two kinds of control answering versions of one question was the thing to
remove.

**Two questions here are deliberately not cards, and both for the same reason**: `starting_at` asks
for a commit-ish and `tending_group` asks for a number of tokens, and neither has a set to draw.
Behind `choosing` they would be a card per ref a repository has, or a card that is really a text box.

Another question with a closed set of answers is a `choosing` call and nothing else. The script names none of the card classes
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

**And a scroller clips, so `.picker` carries inline padding too: the room a focus ring is drawn
into.** `overflow-y: auto` computes `overflow-x` to `auto` as well, so a control flush with the
scroller's edge has its gold cut off on that side - the base and the branch boxes, which fill their
grid columns, and every card while its group is open. Inside the scroller for the same reason the
block padding is. `TestTheFocusRingHasRoomToBeDrawn` is what fails when it goes, and it measures the
gap against the ring's own `outline-width` and `outline-offset` rather than against a number written
down twice. It sets a window narrower than the suite's own, because at 1400 the picker sits inside its
`max-width` with room to spare and the clipping - which is every narrower window, so the common case -
does not happen at all.

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
cannot write one in. `sendFrom` calls **`requestSubmit`** and not `submit`, and that is the whole of
why one delegated listener serves every page: `submit()` posts *without* dispatching a `submit`
event, so htmx would never see a send on a session page and the browser would navigate away from the
conversation instead. It also runs the form's own validation, so an empty box refuses from the
keyboard exactly as it refuses from the button. The Send button names the key, because a shortcut
nothing on the page mentions is one nobody uses.

Plain Enter is the one key a mode may take, and only while the leader palette is open: what it would
otherwise do there is break a line in the middle of `/fo`. See the leaders section.

**The box is one line at rest and grows a line at a time**, to fourteen lines or two fifths of the
window, whichever is smaller, and scrolls inside itself past that. `field-sizing: content` is the
whole of the mechanism, so there is no script and no height kept anywhere a swap could take it back
from;
`rows` stays in the markup as the floor for a browser without the property, which a browser that has
it ignores. The cap has a viewport term as well as a count of lines because the box sits under the
conversation it belongs to: bounded only by lines, a long message on a short window would leave the
transcript nothing. `TestTheBoxYouTypeIn` is what fails when this breaks, and it has to be a browser:
every height here is a correct rendering of *some* box, so what is asserted is how one box changes
across what is put in it, which no still and no markup assertion can see. A browser that ignored the
property would draw the `rows` floor and look entirely deliberate.

`resize: none` is *said* rather than left off, and that is not a style preference: a textarea's own
default is `resize: both`, so dropping the declaration puts the handle back. It goes because a
dragged height is an inline style that outranks the content, so the box would stop growing and stop
shrinking from the moment it was touched, and stay tall after the message had gone.

**The box is edged in the person's own hue** rather than in the neutral `--edge` every other input
takes, which is the palette's one axis applied to the thing a message is written in: cool is what
reached the model, so the box belongs on the same side of it as the panel a message becomes and as
the ground Send is painted in. The gold in a screenshot is the focus ring (`--mark`) over that
border, not the border.

**The Send control takes its own height rather than the box's**, and the row's `flex-end` puts it
level with the bottom of the box, which is where its menu hangs from anyway. Stretched to a box that
now reaches fourteen lines, it would be a slab of person-hue reading as a panel rather than a button.

**And the cursor goes back into the box once the message has gone**, whichever way it was sent: the
button takes the focus on a click, and `hx-disable` blurs the box itself while the post is in
flight, so without this the cursor is on nothing at all by the time the answer swaps in. *When*
matters as much as whether - htmx re-enables what it disabled just after dispatching
`htmx:finally:request`, so the focus is asked for a turn of the event loop later, and asked any
sooner it is asked of a box that is still disabled and takes nothing. Only where nothing else has
claimed the focus meanwhile, so a reader who went to the search box while the message was in flight
is left where they went. `TestWhereTheCursorIsAfterSending` drives both ways of sending, and it too
has to be a browser: the focus is a live property the server never renders, and the ordering it turns
on is htmx's rather than ours.

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

**One value scales the whole page, and it is `html { font-size }`.** Everything here except the
monospace grid is sized in `rem` - the text, the spacing steps, the reading measure, the sidebar and
the rail - so a single percentage moves all of it in the proportions it already has. It is 110%
because the console read small enough that the page was better at a browser zoom of 110%, which is
the same scaling asked for by hand on every visit, and a stylesheet that needs a zoom is a stylesheet
with a number in the wrong place. Deliberately *not* the breakpoints, which resolve `rem` against the
browser's own default rather than against this: the three shapes are about how much screen there is,
and a shape should change where the window runs out of room and not where the text got bigger.

**Monospace is a grid, and it is vendored because a grid cannot be borrowed.** A model answers in
tables and trees, and every `read` comes back as lines behind a `│` gutter, so most of what a panel
here shows is box drawing. Two rows of it join on two conditions, and missing either draws that
column as a dashed line rather than as a line. The row pitch must be no more than the glyph's own
ink, which is a fact about the font: `│` is drawn over about 1.7 times its size in JuliaMono, where
Fira Code, Cascadia Code, DejaVu Sans Mono, Liberation Mono and Courier New all cap out well under
that, so a stack of names makes the console's spacing depend on which of those the reader happens to
have. And the pitch must be a **whole number of pixels**, or every row lands on a different subpixel
phase and the joins falling between two device rows draw as two half-lit ones - a hairline on some
rows of a figure and not others, which is the failure that survives getting the first condition
right.

So `assets/JuliaMono-Regular.woff2` and its bold are upstream unmodified, under the OFL beside them,
and `--mono-size` and `--mono-line` are stated in pixels: at a size of 14 a run joins at a pitch of
23 and breaks at 24, so the pitch is 22, which is that ceiling less the pixel that keeps it off its
own boundary. **Measured rather than taken from the outline**, because a rendered glyph is hinted:
the outline says 1.70em and `measureText` says less again, and neither is the number to build on.
That also means the pair cannot be *scaled* when the rest of the page is, since the whole numbers on
either side of the answer are a pixel apart: `html { font-size }` moves everything in `rem` at once
and this is the one thing it does not reach, so moving it is a measurement rather than a
multiplication. The pixel held back is not symmetry: overlapping ink
still draws the line the figure means, and a gap draws a line the figure does not. Air beyond that
has to come from a larger `--mono-size`, since the span is a multiple of the size rather than a
constant, which is the one knob that moves the ceiling. Both the size and the pitch have to move together,
and both belong to every monospace block at once - `.text pre` for a fence and `.tool__body pre` for
a read, whose family is said again there rather than inherited, since a browser's own sheet sets
`pre` to `monospace` and a rule on the element beats a value inherited from an ancestor.

**A megabyte a weight is what completeness costs, and completeness is what is being bought.** A model
draws with far more than box drawing, and the gaps in an otherwise good font are exactly what a
fallback fills, one character at a time, on a cell of its own. Measured as the share of each block a
face carries: JuliaMono has all of box drawing, block elements, geometric shapes, arrows,
mathematical operators, miscellaneous symbols, dingbats and braille, every glyph on the cell, where
Fira Code has 2% of dingbats and no braille, Cascadia Code 8% of arrows, Iosevka 361 symbols at the
wrong advance, and Roboto Mono no box drawing at all. Two static weights rather than one variable
file, 400 and 700, so the 600 a panel role asks for resolves to the bold instead of being synthesised
by smearing the regular.

A *nerd font* is not the answer to a missing `✗` and the numbers say why: patching Cascadia Code adds
9,219 private-use icons and takes it from 598 KB to 3.35 MB while leaving dingbats at 8 of 192,
because what it fills is the private-use area a shell prompt draws from and not the block `✗` lives
in. The lighter alternative to a whole face, if the megabyte ever has to go, is a face cut to the
symbol blocks under a `unicode-range` so it is fetched only by a session that shows one, with
`size-adjust` to put its cell on this grid: JuliaMono cut to arrows through braille is 115 KB.

`TestTheGridMonospaceIsDrawnOn` is what fails when any of this breaks, and it has to be a browser
twice over: every one of these renderings is a correct picture of *some* grid, so what is wrong with
the broken one is a hairline no still and no markup assertion can see, and the join is asked by
drawing a run into a canvas and reading the pixels back rather than by comparing the pitch against a
number. Rows rather than a column, because the stroke is a pixel wide across two half-lit columns
and the inkiest single column reports joins as gaps. A canvas rasterises about a pixel longer than
the same text laid out in the document, so the gap check is the coarse half and `pitch < span` is
the exact one.

**Reasoning is set in italic and the code inside it is not.** A model reasons *about* code, so a
block in a reasoning panel is a quotation of something that exists, and slanting it makes the
quotation differ from the thing quoted. It costs alignment as well: no italic face is vendored, so an
oblique is synthesised by shearing every glyph, which leans a gutter and the sides of a box while
leaving the horizontals flat.

### Every panel folds, from its own row

**A panel is a `<details>`, its row of facts is the `<summary>`, and the mark sits immediately right
of the title.** That is one mechanism for what used to be three folds and five kinds that could not
fold at all. What a reader wants put away is theirs to decide, so the console says only where each
kind *starts* (`OPENS` in `pages.py`) and nothing more.

**It began as a complaint about vertical space, and the space was the symptom.** A stretch of
reasoning, the standing system prompt and a delivered guidance file each carried a `<details>` of
its own whose summary was the front of its own body - so a panel spent one row saying what it was and
a second row saying it again, and once open the second row held a lone marker and nothing else. Those
three had no fold worth keeping, because the thing their summary said is the thing the panel's row
already says. They are drawn plain now, and the panel is their fold.

**A call and a command keep theirs, and the difference is whether the summary is the block
restated.** A tool's name with its outcome and how long it ran, and a command's line with the status
a program chose, are facts about the block rather than a prefix of it - and a panel holds a whole
*batch* of either, so a reader wanting one read out of three needs a fold per call as well as one per
panel. Two levels, and each is named: a panel's fold is the reader's, a call's is the call's.

**The row is the summary and everything in it keeps working**, which is the browser's rule rather
than this markup's: a summary's activation behaviour skips a press whose target is interactive
content, so the permalink navigates and the copy button copies without folding the panel underneath.
`TestFoldingAPanel` asks Chromium both questions, because a page where that did not hold would look
identical in the markup and fold the panel a reader had just linked to.

**The mark is a `::after` on the role rather than the summary's own**, and that is forced: a
disclosure's marker always leads the row, and this one has to come *between* the title and the line
it stands for. On the role it takes the panel's kind colour for free, so there is no second list of
hues to keep in step.

**Shut, the transcript is its own outline**, one row per panel, which is what the dock's
fold-everything button now produces. That is a wider meaning than it had - it used to put the calls
away - and it is the one worth having, since what `muted` could never give a reader coming back to a
finished conversation is a conversation that takes less room.

**A third dock button puts every fold back where the console had it**, and it is not a midpoint
between the two beside it: those set every fold one way, and this hands out a different answer per
fold, a call shut and a reply open and a system prompt away. It is the way back from either of the
others, which without it are one-way presses over a whole conversation. What makes it possible is
`opens` in `pages.py`, which writes `data-opens` beside the `open` attribute: `open` is the state and
is what makes the page work with no script, `data-opens` is where the console *put* it, and the two
stop being the same thing the moment anything presses anything. Not a copy that can drift, then, but
the original beside the current.

**`mainplate.js` records every toggle as a decision, a morph's own included, and that is
deliberate.** A call still out is drawn open, so the morph that delivers one records it open, and a
reader watching it fill in keeps it open when the result lands rather than having it collapse under
them at the moment it became worth reading. Told apart - by comparing against the `data-opens` the
server just sent - a still-out call would shut itself on arrival and a reader could not ask
otherwise, because at the moment they would press, open is already what the console said, so the
press reads as agreeing rather than as deciding. The two are indistinguishable there, so nothing
tries. What it costs is that little stays undecided on a turn being watched, and the third button is
the way back. `TestWatchingATurnArrive` pins both halves against each other.

**A turn out on a tool call draws no waiting panel at all.** A call with no result is already drawn
working, on its own panel, and it is the model's call, so a second panel of dots under it says the
same thing twice - and says it in a shape nothing is writing, since an empty reply below a call reads
as a turn that has started answering where what is happening is a tool running. `out_on_a_call` asks
it of the turn being answered rather than of the last panel on the page, because a person can type
while a reply is coming and what is at the bottom may be their message. A *command* running is not
this: it runs outside the conversation and no model was told about it, so it says nothing about
whether one is answering.

**A panel whose default would otherwise move carries the working dots on its own row instead.** The
panel saying a reply is being written, and a stretch of context whose instructions no pass has
composed yet, are both drawn *shut* with the dots in the opening line's place. Two things fall out of
one decision: a panel opened to show three dots is a row spent on three dots, which is the thing this
row exists not to spend; and a fold whose default moves under a reader is one the console can no
longer draw either way once a morph has recorded the state it delivered.

### The line a shut panel stands for

**Every panel's row carries the front of what is in it, clipped by the browser at the panel's
width.** Folding prose is only worth offering if the shut state identifies it, so this is what makes
the fold above worth having on a message and a reply rather than only on reference material.

What that line *is* differs by what the blocks are, and `panel_opening` draws the split the old
per-block folds already drew. Prose stands for itself with its own opening. A call and a command have
no prose to take a front off, so the panel *names* what is in it: `read, read` and `git status
--short, git diff --quiet`. The first block for the prose kinds and every block for the two that are
named, which is not an inconsistency - an opening is a prefix, and a prefix of a run of paragraphs is
the front of the first, where a list of calls naming only its first would be hiding the rest.

There is deliberately no length in the markup: `text-overflow: ellipsis` measures a line against a
box, which is a measurement the server cannot make, and any character count it picked would cut in
the wrong place at every other width. `min-width: 0` is what lets the flex item shrink below its
content and so is the whole of what makes the ellipsis appear.

**The row carries the line and no figure beside it, a document's included.** The tempting one is a
character count on a system prompt or a delivered guidance file, since what is in either is paid for
on every request from here on and no rule speaks for that. It is refused because characters are not
the unit: every other number on this page is tokens or money, a window is measured in tokens, and a
count that cannot be compared against the gauge on the rule below it is a number a reader converts
rather than reads. What a request carried is a fact about the request, so it belongs on the rule with
the rest of them.

`pages.OPENING` is a bound on what is *carried* rather than on what is shown, and it exists because a
line holding the whole of a long block would put every word of it on the page twice, on a region
re-rendered whenever the turn in flight records anything. The number is what keeps the clipping
honest: clipped short of it the ellipsis says there is more, and clipped *at* it with no ellipsis it
would say there is not. So it has to exceed what the widest panel can show, which is a bounded
question because the transcript is capped at `--measure`, and
`TestTheLineAShutPanelStandsFor::test_more_is_carried_than_the_widest_panel_can_ever_show` measures
the worst case there is - the narrowest glyph the prose face draws, repeated - and fails if it fits.

Three consequences of the line being a prefix of the body under it:

- **It is hidden once open**, since a prefix standing directly above the body says nothing twice.
  Hidden and not removed: taken out of the flow the row's free space collapses and the permalink
  slides left across the panel, and a control that moves under the finger that pressed it cannot be
  pressed twice.
- **The search skips `.opening`.** Found in both, the dock would step through one sentence at two
  stops and the count would say there is twice as much of it as there is. It is the one entry in
  `markHits`'s skip list that is there for being a second copy rather than for not being
  conversation.
- **A copy button reads `data-markdown`**, as it does for every other Markdown block, so what comes
  out is the source and the opening is in it once.

**It is the source rather than the rendering**, which is what a message written to be markup makes
worth saying: the sanitiser never sees this line, so the escaping of a text child is the whole of
what keeps it inert, and `test_markup_in_a_message_is_still_text_in_the_line_its_panel_stands_for`
reads it back with a real HTML parser.

A turn is read out of the checkpoint as **panels of blocks**, not as a question-and-answer pair.
A block is prose, reasoning, a call with its result, or a command with its own; a panel is a run of
blocks of one kind within one model request, and it is what the page draws a coloured edge down. The
palette runs on one axis and every kind takes its side from it: cool is what the person produced
(their message, their steer, their command), warm is what the model produced (its answer, its
reasoning drawn back toward the ink, a call in ochre). A kind added later has its hue decided by that
rather than chosen for it. A part kind `parted` has no rendering for is passed over rather than
refused, because the provider and Pydantic AI are both free to add one.

**The axis is who wrote it and not who was told**, which a command is the case that settles: it is
the one kind on the person's side that no model ever saw, and what says so is the `title` on its role
and its chip in the key rather than a hue of its own. A colour for "the model does not know about
this" would be a second axis over one palette.

**Every kind is named after the thing it holds, in the word the page prints**, so `prompt`, `steer`
and `command` are the labels, the `data-kind` values and the words behind them in `records.py`. They
were `you`, `you (steering)` and `you (ran)`, which sent a reader who learned a word from a panel
looking for an identifier the code does not have. Two things that cost, both accepted:

- **`prompt` overlaps `records.Prompt`, which is narrower.** The record is a message that *must*
  open a turn; the panel is whatever message a turn opened on, and a `Steer` arriving at an idle
  session is drawn as one. That is the collision this vocabulary already has with `StepKind` on
  `command` and `tool`, safe for the same reason: they never mix and mypy refuses the crossing.
- **Three labels no longer share the word `you`**, which was the non-colour cue that they are one
  side. The hue and `data-side` still carry it, and `TITLES` is where `you (ran)` put its extra
  meaning down.

`muted` is keyed by these values in `localStorage`, so the rename left every stored decision naming a
kind nothing draws: a reader who had quieted one gets it back once and mutes it again. Said rather
than migrated, because it is one press and the alternative is code that reads a vocabulary this
console no longer has.

**A panel says what is in it; a rule says what is true of the request or the turn around it.** Which
turn it is, where the session may be forked from, the worktree a request was made against, and what
it spent all belong to the exchange rather than to any one run of blocks, and hung on a panel they
had to be hung on a chosen one. Usage settles it: it belongs to a `ModelResponse`, and one response
becomes as many panels as it has kinds of part, so there is no attribution rule from a response to a
panel that is not invented. `transcript_region` groups the panels by `turn` and then watches `asked`
change within one, so nothing keeps a second list of where a turn or a request begins.

**A rule names its turn, and that is legibility rather than decoration.** A turn rule sits directly
under the last panel of the turn before it, so a bare row of figures there reads as a footer
summarising what is *above* it, which is the opposite of what it says. The `#1` against the `#1.0` on
the panels below settles the direction, and doubles as the permalink to the boundary the fork acts
on. A rule inside a turn names its request the same way, as `r1.1`, which is also what opens the
record: the whole address rather than the index within the turn, for `Panel.label`'s reason one level
along, since a rule inside a turn draws no `#N` and a bare `r1` said which request without saying of
what. The `r` is what keeps it from being read as a panel, which numbers a different axis - `#3.1` is
turn 3's second *panel* and `r3.1` is its second *request*.

### What the figures on a rule say

Six of them, and none is picked out from the others: how long it took, how much context it carried
and how much of that came out of the cache, how full the model's window is, how much came back, what
it cost, and what the conversation has cost so far. The cost used to take a stronger ink, which read
as the figure to look at; which one somebody is reading changes with what they are doing, so picking
one is deciding that for them.

**The input figure is the context and not the sum**, which is `Spent.context`'s argument said on the
page. Every request of a turn carries the whole conversation again, so a summed input says what the
provider charged for, several times over about the same tokens; what a reader wants off a rule is
how much of the window is gone, which is where the turn's *last* request left it. The summed figure
is still true and still drawn, under the message box, as what the session has been charged for.

**A symbol per figure, and the words in the titles.** A rule is one line that must not wrap and it
now carries six figures where it carried three. `↑` and `↓` are a count of tokens going up to the
model and coming back, `▣` is how much of the first came out of the provider's cache instead, and
`Δ` against `Σ` is what this exchange added against the running total, which is that pair's own
notation and reads as a pair rather than as two prices to tell apart by size. Five cells against the
twenty or so the words would take, on the one line that cannot afford them.

`▣` is the one of the five that is a mark rather than notation, and it is a mark rather than the `↩`
tried before it for two reasons: `↩` is already the sidebar's aside, so it would be one glyph reading
two ways on one page, and beside `↑` it read as a third direction rather than as a fact about the
first. A square with something in it reads as a store, sits on the cell like every other symbol here,
and cannot be mistaken for an arrow.

The cached count sits inside the context figure as `↑96K (▣45K)` rather than beside it, because it is
a fact about that count and not a figure of its own, the way the wire's own numbers nest. The bracket
is what says so.

**The separator is interleaved rather than carried by each figure.** Every one of these is drawn only
where there is something to say, so a dot baked into a figure is a dot that appears with it: the time
had none and the count after it had one, and a turn nothing timed then opened with a dot standing for
nothing.

**The running total is inclusive of what the rule speaks for**, so a turn rule says what the
conversation had cost by the end of the turn it opens, exactly as it already says what that turn
spent: both figures on it summarise what is below rather than what is above. It follows
`altogether`'s rule one rule at a time, so the first unpriced turn takes it off every rule below,
and it is left off entirely where it *is* the figure beside it, since the first priced turn of a
session would otherwise print one number twice.

**And the line itself is a gauge.** It is filled from the left as far as the request's context
reaches into the model's window, shading from the rule's own colour toward `--vermilion`, which is
the palette's red already. A rule is a hairline drawn across the whole column at every request
boundary, so the one thing a long conversation most wants to know costs no row and no control: a
reader scrolling down watches the line lengthen and warm. Three things there are decided:

- **The scale is the whole width and the fill is clipped to it**, rather than the gradient being
  squeezed into the filled part. Squeezed, every conversation ends in red and the colour at a point
  means nothing; clipped, a point along the line means the same fraction on every rule of every
  session. It shades rather than steps because a threshold is a number somebody would have to invent
  and defend, where the whole point is that this gets worse gradually.
- **The server computes fractions and nothing else.** The colours, the geometry and the cap are
  decisions, so they live in the stylesheet; a fraction is a fact about one request, and there is
  nowhere else it could come from. They are the only inline styles this console writes.
- **`rule__reserve` is the second mark on that scale**, a short bar standing across the rule where
  the session's reserve opens, drawn only where auto-handoff is on. On the same scale as the fill, so
  the two are read against one another: the fill says how far this request got and the mark says
  where a handoff would be asked for. See the handoff section for why it is an element rather than a
  second pseudo.
- **The window comes from the reference and not from the checkpoint.** `Conversation.window` is
  `facts_of` asked about the session's own choice, which is the same lookup that prices a turn, so a
  database that learns a model's window shows it on every session already running on that model.
  Absent - no database, an endpoint that no longer lists the recorded id, a model with no record -
  the counts are drawn and the fraction and the gauge simply are not, which is what a rule was before
  there was one.

The dock's turn column steps `rule--turn` rather than the person's panels, and that is a removal.
There is exactly one message per turn, so a "previous message of yours" column and a "previous turn"
column visit the same positions and differ only in where they stop: two controls answering one
question, which is the thing this console removes wherever it finds it. Every arrow now declares what
it steps over (`data-stop`) rather than being told apart by what it lacks, because a button
identified as "the one with no side" stops being identifiable the instant a second kind of stop
exists. The modifier and not every rule, because a rule now stands at every model request and a turn
with four round trips in it would otherwise give that column four stops.

**Four columns, widest first**, which is the picker's own ordering one control along: where the
model's history starts again, then turns, then every panel in play, then one side of them. The forget
column is the newest and the only one that finds its stops by an attribute a rule declares about
itself rather than by a class; see the forget section for why it is drawn in every session.

**The leap to the start lands on the rule that opens the first turn**, which is the top of the
transcript rather than the first panel in it: a turn rule carries that turn's own facts and its fork
link, and where the stretch has instructions there is a system prompt panel between the rule and the
message, so landing on the panel put the reader below both with nothing saying so. The leap to the
end is still the last panel, since nothing is drawn under one.

The raw record hangs off a **model request** rather than a panel, on the rule at that request's own
boundary; see "The record hangs off a request, not a panel" above for why. Two things about how it is
fetched are decided rather than incidental:

- **On demand and `hx-preserve`d.** A running turn re-renders the transcript repeatedly, so the raw
  record of every request is not something to carry in it; and because the server renders the
  disclosure closed, a morph takes the `open` attribute back off unless the element is preserved.
  htmx reads `hx-preserve` off the *incoming* markup, so taking it off the live node proves nothing.
- **`once` is safe here in a way it never was under a panel.** A step's key is written once and never
  rewritten, so a request's record is settled the moment it exists - where a panel's record came out
  of `turn:{n}:messages`, which does not exist until the turn ends. So a tag can be opened mid-turn
  and the panel disclosure could not.

**Opened, it grows the rule downward rather than lying over the conversation.** A record read against
the reply it came from is worth more than a page that holds still, and an overlay is the one shape
where the two cannot be looked at together. It takes a line of the rule to itself - the rule wraps
and the record asks for the whole of one - which is what a phone decides: sharing the line leaves the
record a column six characters wide, and pinning the figures so it does not is a row that runs off
the side of the screen.

**A control that toggles may not move.** Whatever a disclosure opens, the thing that opened it stays
exactly where it was, and that holds for every fold this console draws. A control that moves under
the finger that pressed it cannot be pressed twice, and the page reads as having jumped rather than
as something having opened. The record tag is where that was learned: what wrapped onto the second
line was the whole `<details>`, so `r0` set off across the rule on the way to opening it.

The shape that gets this right is the general one, so reach for it before inventing another. What
wraps must be the *content* and never the summary above it, which means the summary and the content
have to be separate items of the row that wraps. `display: contents` on the `<details>` is what does that: the
tag makes no box of its own, so the summary stays an item in its own place and the content becomes
the item that takes a line. Both candidates for that item are told the same thing, because
`::details-content` is the box a browser wraps a disclosure's content in and the content itself is
the item where there is no such box, and the closed state has to hide *both* or an empty item leaves
every shut rule a row gap taller.

`TestOpeningTheRecordBehindARequest` is what fails when this breaks, and it has to be a browser: both
states are correct markup and each screenshot is right on its own, so what is measured is one
element's box across the press. Within its rule rather than within the window, because the page
follows the end and a record opening at the bottom scrolls the transcript under it - which is the
console doing what it is asked, and would otherwise report as the marker having moved.

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

`test_commands.py` runs real processes against a real worktree, and synchronises on the *record*
rather than on a clock: a command is run by a task nobody holds a handle to, so what a test waits for
is `result:{entry}` appearing. Any fixed sleep there is either racy or wasted, and the record is the
actual signal.

**A test that wants a turn writes the two records a pass would**, which is the cursor saying which
entry the turn took and the messages saying what came of it. The suites that run without a worker
have a helper apiece for that (`taken` and `answered` in `test_console.py`, `taking` in
`test_browser.py`, `said_at` in `conftest.py`), because a message that is only *delivered* is a
different state: it is queued, and the page draws it as a message waiting for a turn rather than as
one being answered.
