# Plugins

How somebody adds to this console without editing it: the protocol a plugin speaks, the events it is
sent, the effects it may ask for, and what has to be true before a repository's own plugin runs.

## Handoff is the specification

The protocol was not designed and then tried against something. It was read off [the handoff](
composer.md#handoff), which is the most demanding thing this console does that a plugin should be
able to do, and which turns out to need almost everything at once.

`hand_off` alone produced three separate effects from one call: a correctable refusal where the
document was too short to be one, a message delivered into the inbox carrying a boundary, and a value
back to the model.

And the tool was the smaller half. The whole of it, and what each part became:

| What handoff did in the console | What the protocol therefore has |
|---|---|
| `hand_off`, its description, `document: str` | a **tool** contribution: name, description, JSON Schema |
| refused a document under `LEAST` characters | a **retry** effect, correctable rather than a fault |
| wrote a message with a boundary to the inbox | a **deliver** effect, with a boundary and attribution |
| returned `Recorded. …` to the model | a **return** effect |
| asked once the reserve was crossed | an **after_turn** event carrying the numbers to decide on |
| never fired twice running | that payload saying what the turn *opened on*, and who asked for it |
| `hands_off` and `reserve` as columns | **settings**, declared once by the card that draws them |
| its card in the rail | a **card** contribution, whose controls are those settings |
| `/handoff`, empty box allowed, note appended | an **answer** contribution and a **compose** event |
| its own panel kind | a **label**, **title** and **tone** on the delivery |
| a mark on every turn rule | **nothing. This one did not port.** |

**The last row is stated rather than quietly dropped, and it is the one thing this port actually
removed.** The console drew a bar across every rule at the fraction the reserve opened at, read
off two columns the session index no longer has. A reserve is a plugin's own setting now, and the
console has no vocabulary for a plugin drawing in the transcript region; stretching the card language
that far to reach it would be inventing an axis in order to have a cross-product. So the gauge stays
the console's, the *fill* is unaffected since how much of the window a request used is the console's
own arithmetic, and a handoff shipped as a plugin does without the mark.

One thing got *better* under the protocol, which is worth noting because it is the opposite of what a
port usually does. The console needed a shared composition so that the auto path and the `/handoff`
button could not come to say different things about what a handoff is. Both are now events into the
same script, so that sharing is internal to the plugin rather than a function two callers have to
remember to reach for.

## A plugin is one script, spoken to in JSON

**A plugin is a single executable.** The console runs it with a JSON payload naming an event and
reads a JSON answer naming effects. That is the whole contract.

```console
$ echo '{"event":"tool","tool":"hand_off","args":{"document":"…"},"session":"a1b2",
         "plugin":"bundled:handoff"}' | src/mainplate/plugins/bundled/handoff
{"deliver": [{"said": "…", "forget": true, "label": "handoff", "tone": "strong", …}],
 "return": "Recorded. This conversation's context starts again from that document, …"}
```

One entrypoint rather than a file per hook, because **a plugin is a package and not a pile of
scripts**. Handoff is a tool *and* a condition *and* a card *and* a composer answer, and those share
one string, one setting, and one idea; split across five files wired up separately they would be
five things somebody has to install in agreement.

Four properties follow, and they are why this shape rather than in-process Python:

- **Any language.** The console cares about two file descriptors.
- **Its own dependencies**, because it is launched rather than imported.
  [PEP 723](https://peps.python.org/pep-0723/) under a `uv run --script` shebang is exactly the
  mechanism it was written for, and none of it is this console's problem.
- **Testable by hand**, with the `echo` above. No harness, no fixture, no console.
- **Reaches nothing it was not handed.** No `sys.modules`, no credentials, no store, no other
  session. That is what makes a repository's plugin possible at all.

**The protocol is transport-independent, and that is deliberate**, because one question here is not
yet answerable. Today it is a process per event, which is plainly right for a turn boundary and a
control press, and is a real cost on a tool call: the model calls tools several times a turn while
the pass holds its lease, and a cold `uv run` is not cheap. If measurement says so, the fix is a
persistent process spoken to over framed stdio, which changes the transport and not one word of the
payloads below. So the decision is deferred as a value rather than as a second code path, and it is
deferred knowingly rather than overlooked.

## Declaring is not setting up

**A plugin is a program this console executes, so nothing runs one until somebody has said which.**
That splits what used to be a single moment in two, and the split is the whole reason the settings
step exists:

- **Declaring** is reading files. A directory listing for the bundled set, a mapping in the
  operator's `config.yaml`, and a mapping in the repository's own `.mainplate/mainplate.yaml`. It
  yields a name, a tier and a path apiece, and it spawns nothing.
- **Setting up** is running each declared plugin's `setup` to get it ready and find out what it
  contributes. It is what the pass after the settings step does, to exactly the plugins that step
  left switched on.

A session's first pass declares; the press asks for a second pass, and that one sets up. So a session
somebody creates and never confirms has executed nothing at all, and a plugin somebody switched off
was not merely contributing nothing, it was never launched.

**The switches are therefore drawn from the declaration**, which is why they say a name and a path
rather than what each plugin calls itself: a heading comes off a card, a card comes back from
`setup`, and asking is running. What a reader is deciding about is whose program this is, and the
path is the whole of what there is to go on before it has been asked anything.

## Setup

**The first call to a plugin sets it up and asks what it is**, and it happens on the pass that
follows the settings step being answered. Everything it contributes comes back from one `setup`,
including which events it wants:

```json
→ {"event": "setup", "session": "a1b2", "plugin": "bundled:handoff", "worktree": "/…/a1b2",
   "scratch": "/…/plugins/a1b2/bundled/handoff"}
← {"events": ["after_turn", "compose"],
   "tools": [{"name": "hand_off", "description": "…", "schema": {…}}],
   "answers": [{"leader": "handoff", "saying": "hand off and clear the context", "demands": false}],
   "card": {"heading": "handoff", "rows": [
     {"switch": {"name": "hands_off", "label": "auto at reserve", "default": true}},
     {"number": {"name": "reserve", "label": "keep back", "unit": "K", "default": 40}}]}}
```

A plugin may also return **`instructions`**, which are composed into what the session is answered
under. Those are a `setup` contribution rather than an event for the reason tools are: they sit
in front of the cached prefix, so they have to be settled for the session or every request under
them is re-priced. It is what lets [the guidance
system](#both-are-ported-and-that-is-the-test) be a plugin at all.

### One event, and it is named after the stage

`setup` does two jobs: a plugin gets itself ready, and it says what it contributes. It was written as
two events, a `describe` with a setup of its own beside it, and collapsed back into one.

**What collapsed it is that a plugin's first run is already its install.** A `uv run --script`
shebang resolves an interpreter and a dependency tree the moment the console executes the file,
whether or not anybody named an event for it; a plugin that wants a program in the worktree runs
whatever fetches it. So the second event was buying a *scheduling* distinction - fast and pure here,
slow and effectful there - which is a decision the console can make on its own, at the price of one
more moment every plugin author has to learn about.

**And it is named after the stage rather than after the answer**, because a plugin is not the only
thing being set up. Starting a session on a repository means getting that repository ready, and this
is the stage where that happens. There is deliberately **no repo-setup mechanism of the console's
own** - no `setup:` field in `.mainplate/mainplate.yaml` running `uv sync` - because a plugin that
answers this event is already one. A field would be a convenience over what exists, not a capability,
and it would be a second thing to keep working for ever.

The cost, stated: the name says less about what comes *back* than `describe` did, and a plugin author
reading only the event name would not guess that the answer is the whole registration.

### It runs in a pass, and that is the second thing that moved

**The press records the switches and asks for a pass; the pass is what runs anything.** Setting up
fetches and builds - a hook environment per entry in a `.pre-commit-config.yaml`, an interpreter, a
model - and that is minutes on a cold cache. A request somebody is waiting on is the wrong place for
minutes, which is the argument this console [already made about the
clone](workspace.md#where-a-repository-comes-from): a pass is where slow work lives, under a lease,
with a worker slot and a page that says it is working.

Three things follow, and each is worth stating because each is a change from the draft where the
press did the work:

- **The failure has to be recorded.** Nobody is waiting on a response any more, so a setup that will
  not finish has nowhere to put its sentence, and a session would sit under a spinner for ever.
- **Recorded per attempt, under `plugins:setup:{n}:refused`.** The store keeps the value a key was
  first given, so an unnumbered refusal would be the sentence every later press showed - and pressing
  again after turning a plugin off is the whole recovery path.
- **The confirmation carries no list of its own.** `plugins:setup:{n}` records only *that* somebody
  pressed; which plugins are on is the `enabled` column's answer, read by the pass. A write-once list
  would make the second press run exactly what the first one ran, which is the same failure one field
  along.

**A session waiting on its setup is a fourth state of the settings step**, drawn with a spinner and
the line that says what is happening. The step now has four: the worktree being planted, a
declaration that would not parse, a setup out working, and the switches themselves.

**`events` is what stops this being wasteful.** Without it every event goes to every plugin and a
console with six of them spawns six processes per turn to be told nothing five times. With it, a
plugin that only wants `after_turn` is never launched for anything else.

**What comes back is settled for a session's life, and the cached prefix is what decides that.**
Tool definitions sit above the system prompt in the cached prefix, so introducing one
mid-conversation invalidates the whole prefix beneath it. So a session's contributions are recorded
on the pass that sets them up, the way everything else settled about a session is, and a plugin
edited underneath a running session reaches it on no turn at all.

**Which makes `setup` idempotent by requirement rather than by convention.** Nothing is recorded
until every plugin has answered, so one that fails leaves the whole registration unwritten and the
next attempt sets all of them up again. Recording each separately would still leave a session half
set up with no way to correct it, and being run twice is what an install is already built to
survive.

**Under two session-level keys rather than one, and the fork is what decides it.** `plugins:console`
holds what the bundled set and the operator's own contributed; `plugins:repository` holds what the
repository's did. They are not turn-prefixed, for the reason `instructions:{n}` is not: `before`
copies turn-shaped keys by shape, so one name would carry both halves of a parent's registration into
a branch, and a fork must inherit exactly one of them. Both are written even where nothing was
set up, which is what makes an empty registration mean *this session was set up* rather than *nobody
has pressed anything*.

**And two more beside them, `plugins:declared:console` and `plugins:declared:repository`**, holding
what was read out of files before any of it ran. Four keys rather than two because the two questions
have different answers at different moments: between them a session is on its settings step, with a
declaration to draw switches from and no registration at all. They split by tier on the same rule and
for the same reason, so a fork inherits the repository's declaration as well as its registration - a
fork of a session that never got past its step has only the first, and re-reading the file to draw a
switch would be the same read out of the same model-written tree, one step earlier.

**Forking is how a conversation picks up an edited plugin**, and that is the existing answer rather
than a new one: a fork is a session, so it sets the operator's plugins up afresh, and the way to
carry work across a plugin change is the control that already carries it across a change of model or
a change of mind. A repository's plugin is [the exception and cannot be reloaded that
way](#read-once-and-never-from-a-tree-this-console-wrote); a fresh session is what picks one of
those up.

There is deliberately no reload that reaches a *running* session. A set of tools that changed under
a conversation would invalidate the prefix beneath it and leave the turns already recorded having
been answered by a harness that session no longer has.

**A plugin does not declare its own name, and that is a correction rather than an omission.** It was
declared here in an earlier draft and had to match the key in the configuration, which was one fact
in two places bought for a mismatch check. It is not worth it, and it makes a plugin somebody else
wrote harder to install: two people's plugins can both call themselves `guidance`, and a rule that
the declared name must match the key means you cannot enable both without editing one of their
files, which their next release undoes.

**So a plugin is named by whoever installs it.** The key in the configuration is the whole of the
name, and a plugin needs none of its own: the console supplies the qualified name for the settings
blob and the tool prefix, and what a reader sees is the card's `heading` and a panel's `label`, both
of which the plugin already says separately.

**The qualified name is nevertheless in every payload**, under `plugin`, because one thing genuinely
needs it and cannot work it out: a plugin answering a turn boundary has to be able to tell that the
turn opened on *its own* delivery, or it fires again for as long as the condition that fired it stays
true. `opened_on.plugin` and `plugin` are compared, and that is the whole of what stops a handoff
recursing.

## Starting a session takes four steps

**A repository's plugin cannot even be named until its worktree is planted**, which is the first
pass, because [the worker clones and a request handler never does](
workspace.md#where-a-repository-comes-from). And no plugin in any tier may be *run* until somebody
has seen the list and said so, because running one is executing a program somebody may not want
executed.

The old shape cannot absorb either. Creating a session used to record the choice and queue the first
message in one POST, so the first turn would be answered before anything had been read out of the
repository. So creation splits:

1. **The choices.** Endpoint, model, repository, isolation, and whether the repository's own code may
   run at all. This records the `Choice`, enrols the session, calls `make_ready`, and redirects to
   the session's own page. Its button says `Create session`, because what it makes is a session and
   not yet a conversation.
2. **The declaring pass.** Plants the worktree, reads what each tier declares out of files, records
   both halves. Then it reaches `opening_turn`, finds an empty inbox, and comes back `Blocked`.
   **It runs nothing**: at this point the console knows a name, a tier and a path per plugin, and
   nothing else.
3. **The settings step.** A switch per declared plugin, under a heading per tier, and a button that
   says `Load plugins`. Pressing it records the switches, records that somebody pressed, and asks for
   another pass. **It runs nothing either**, and the redirect lands on a page that says the setup is
   working.
4. **The setup pass.** Runs `setup` on everything left on, concurrently and with a network, records
   what they said, and reaches `opening_turn` again - where the inbox is still empty, so it blocks.
   The page it fills in is the conversation.

**Nothing about step 2 is a new mechanism.** `opening_turn` already suspends a pass on the inbox,
and its own note says an empty take raises and the pass comes back `Blocked` until something is
delivered. So "set up, then stop and wait" is `planting` moving above `opening_turn` in the loop,
plus a session that is queued by `make_ready` rather than by a delivery. A session nobody types into
holds no lease and no worker slot, because a blocked pass has released its claim.

**Step 4 is a pass rather than the press, and this is the one that changed.** An earlier build ran
the whole thing in the request handler, on the argument that describing was a handful of short-lived
processes and the answer decided which page somebody got. That argument does not survive a plugin
that *installs*: a repository whose plugin fetches a toolchain would hold the press open for minutes
with nothing on screen. So the work moved to where slow work already lives, and the answer moved with
it - what a reader gets back is the same page either way, filled in by [the live
connection](console.md) when the pass finishes.

**You do not reach the conversation until the setup has finished.** The step is the last thing on
screen until every plugin left on has answered, so nothing is ever half set up behind a message box,
and the rail cannot draw a plugin's card on the screen that exists to decide whether to run that
plugin.

**Every plugin is set up there, the user's included**, rather than the user's at process startup and
the repository's later. One moment for all of them is worth more than the earlier read: it removes a
tier's worth of asymmetry, it puts every launch behind one confirmation, and a plugin edited on disk
reaches the next new session without the console being restarted, which matters most while somebody
is writing one.

**Step 3 is always drawn, and there is always something in it.** Skipping it when nothing is declared
would make the number of steps depend on what a repository happens to carry, so the flow could not be
described, learned or tested as one thing.

The empty version of that step is not a case worth designing around anyway, because **handoff ships
as a plugin and is on in every session**: there is no console with no plugins, so the step always
has at least a heading and a switch in it. The cost this looked like it had, a click through a blank
page, is not one that can arise.

**It is drawn in three groups, one per tier, each with a line saying what that tier is.** Grouping by
where a plugin came from rather than by what it does is the whole point: the three are not equally
trusted, and a reader deciding what to leave on is deciding about provenance. [What the repository's
line says](#what-the-trust-switch-is-actually-guarding) is the exposure in plain terms rather than a
warning that something may be unsafe. Every tier is drawn, empty ones included, so the step is the
same shape on every session.

**Each plugin can be turned on or off there, and that is settled for the session.** **Every declared
plugin comes on**, in every tier: installing one is the decision, and this console does not
second-guess it by turning something else off out of view. A plugin that is off contributes nothing:
no tool in the prefix, no card, no answer in the composer, and no events.

**A tier's heading carries a switch of its own, and it is not a third kind of answer**: it sets every
switch under it. What a session records is a switch per plugin, so turning a tier off is turning each
of its plugins off, and a tier that recorded an answer of its own would be a second place the same
question is answered. It works only with the script present, which is the standing bargain every
scripted control here takes - without `mainplate.js` each plugin's own switch still works.

The switches are live at step 3 because nothing has been run yet, and gone afterwards because a
tool definition leaving the prefix invalidates everything under it exactly as one arriving late
does. So the rail draws each running plugin's *card* and never these, and [forking](forking.md) is
how a conversation changes its mind, as it is for the model and the repository.

**A plugin's settings stay live where its being run at all does not**, and the two are different
questions rather than an inconsistency. A setting is a value the plugin reads when it runs; being
set up decides what is in the cached prefix. That is what keeps the auto-handoff switch behaving
exactly as it does today, taking effect on the press, while the plugin behind it is fixed for the
session.

**It is a state of the session page and not a route of its own.** The session id exists from step 1,
so the URL is stable and bookmarkable while the clone runs, and the console already renders a
session whose worktree is not yet planted and already has the live connection that fills it in when
the declaration lands. A second address would be a page somebody can be sitting on when the thing it
is waiting for arrives somewhere else.

**It stands alone on that page rather than above the conversation.** A session being set up has no
transcript to read, nothing to type into and nothing to navigate, so a message box and a rail drawn
beside it are controls pointed at a conversation that does not exist yet - and the rail draws a card
per *running plugin*, which would be a plugin's own surface standing on the screen that exists to
decide whether to run it. Which shape the page takes is one predicate, `settling`, and [the live
connection](console.md) reads the same one to decide which regions to send, so the page and the
connection driving it cannot disagree about what is on screen.

### A plugin that will not set up comes back to the step

**A setup either records both halves or records nothing.** Every plugin left on is set up at once,
before anything is written, so a tier that failed does not leave the other one recorded: the store
keeps the value a key was first given, and a half-written registration could never be corrected.

**The failure is recorded against the attempt, and the page draws the step again with the reason
above the switches.** That is where a reader can act on it, because the thing to do about a plugin
that will not set up is turn it off, and the switch is a line down.

**Recorded is the change, and the numbering is what makes it survive a write-once store.** While the
press did the work the sentence rode back on the response and was written nowhere, on the argument
that the first failure would otherwise be what every later attempt showed. In a pass there is no
response to ride on, so it goes under `plugins:setup:{n}:refused` and a second press opens attempt
`n+1` - which has no refusal under it, so what the page draws is the spinner rather than the sentence
somebody just acted on.

The step's own `Try again` is a different button for a different failure. It asks for another
*declaring* pass, which is what a worktree that would not plant or a `.mainplate/mainplate.yaml` that
would not parse needs; that failure is recorded under `plugins:refused`, unnumbered, because nothing
about it is per attempt - the files are the same files.

**One broken plugin stopping the setup is the right answer in every tier**, by the question
[refusing at startup](../philosophy.md#refusing-at-startup-or-promising-not-to-raise) asks: a
repository plugin is one you kept trusted and a user plugin is one you configured, so either failing
silently leaves somebody holding a choice they cannot use. A session that quietly ran without it
would be answering under a setup nobody asked for.

**Which puts the bundled plugins on that same path, and handoff is on in every session**, so a
handoff that will not set up is a console whose sessions cannot start. That reads alarming and
is the correct severity: a console whose handoff is broken *is* broken, and it is broken in this
repository rather than in somebody's configuration. It is also the one tier where the failure is not
a realistic runtime mode, because a bundled plugin that cannot set up fails this repository's own
suite long before it reaches anybody. What stays realistic is the user's own plugin and the
repository's, which is where the sentence naming the plugin and the switch beside it are aimed.

### What this costs, and the title

**Creation stops being fire-and-forget.** Time to a first answer is unchanged, since the clone
happens either way, but you now create, wait, confirm, and come back to type, where before you could
type and walk away. That is the real cost and it is bigger than an extra click. It is taken
deliberately: a boundary in front of executing somebody else's program is worth more here than the
convenience, and the convenience is recoverable later in a way a session that ran a plugin nobody
looked at is not.

**And the wait after the press is now unbounded by anything this console controls.** Setting up is
concurrent across every plugin, so it is one plugin's slowest `setup` rather than the sum of them,
but that one is somebody else's program fetching whatever it needs: a repository plugin building a
hook environment per entry in its config is a minute, and a cold interpreter download is another.
`SETTING_UP` caps it at twenty minutes and reports which plugin ran out, which is a bound on the
damage rather than on the wait.

That is the price of a plugin that can install things, and it is paid once per session rather than
once per turn. The alternative was letting the conversation open while the setup ran behind it, which
trades the wait for a page whose rail and composer fill in some moments later - a worse deal, since
what is filling in is the thing you were just asked to approve.

**A session is `UNTITLED` until its first message lands.** The name still comes from that message
and is still written once, so [the claim the session index rests
on](../philosophy.md#the-session-index-is-one-row-and-it-reaches-rather-than-copies) survives with
one word moved: written when the first message arrives rather than at creation. A title typed on
step 1 always wins over the generated one, which is `Choice.branching`'s existing rule.

Requiring a title instead was tempting and is worse: it adds a question to step 1 that nobody wants
to answer, and the whole appeal of naming from the first message is not having to name things.
Defaulting to `owner/repo/branch` is worse again, since every session on one repository would then
draw the same sidebar row.

**`enrol`'s ordering argument gets simpler.** It enrols before queueing today because a session in
the list with nothing in it is the visible failure and a session being answered that no list shows
is the invisible one. An empty session waiting for a message is now the *intended* state, so the
thing that ordering protected against has stopped being a failure at all.

**The step and the rail draw different things, and that is the split rather than an inconsistency.**
The step draws one switch per *declared* plugin, from a name and a path, because nothing has been run
and there is no card to draw. The rail draws a *card* per running plugin, from what that plugin said
when it was run. Neither is a rendering of the other, and a change to one is not a change to both.

## The events

Each carries its own payload and takes its own effects. They are a short list this console owns, for
[the reason below](#what-the-durability-layer-allows), and not a seam per point in the agent's loop.

| Event | Fired | Payload beyond `session` and settings | Effects it may ask for |
|---|---|---|---|
| `setup` | once per session, before its first turn | nothing | the contributions above |
| `tool` | the model called one of its tools | `tool`, `args` | `return`, `retry`, `deliver`, `set` |
| `before_request` | a model request is about to be sent | `messages` | `inject` |
| `after_turn` | a turn was recorded | `turn`, `opened_on`, `context`, `window` | `deliver`, `set` |
| `compose` | its answer was submitted | `said` | `deliver`, `set` |
| `action` | a control on its card was pressed | `control`, `value` | `set`, `deliver` |

Every payload also carries `worktree` and, for a confined plugin, `scratch`: where this session's
files are and where this plugin alone may write. `worktree` is on all of them rather than only the
two that act inside a turn, because a plugin composing instructions out of the repository's own files
reads them at `setup` or not at all.

**`before_request` is the one inside a turn, and it exists because [guidance is being ported onto
this protocol](#both-are-ported-and-that-is-the-test).** Its answer is
`inject`, a system-voice message appended to the request about to go out, which costs the cached
prefix nothing where an edited instruction re-prices everything under it. What it answers is
recorded, so a resumed pass replays the injection rather than recomputing it from a plugin that may
not be pure.

**`after_turn`'s payload is the one that has to be rich, and it is where the constrained vocabulary
is paid for.** A plugin can only decide on what it is handed, so unless that payload carries the
recorded context size, the model's window and the plugin's own settings, nothing can implement
`crossed` and the specification above is not met. `opened_on` is there for the same reason: handoff
must never fire on a turn that itself opened on a handoff, and the only way a plugin can know that
is to be told what opened the turn and which plugin, if any, delivered it.

**`after_turn` is asked only where a turn actually ended.** A pass that spent its allowance or hit a
refusal returns before it, so a plugin is never asked about a turn that stopped part-way, which is a
turn left unfinished for reasons that are the console's rather than the conversation's.

**Its deliveries come back as a value the pass returns**, rather than being written from inside the
loop, which is the split `Crossed` made and `Noting` now makes for every plugin: putting a message in
an inbox *queues* the session, and that is a fact about the queue in front of a pass rather than
about answering one. A `tool` answer is the exception and is written where it is asked, because
`wrap_tool_execute` wraps the whole call in a step - so a resumed pass replays the recorded return
and writes no second entry.

## The effects

**A plugin answers with what it wants done, and never does it.** The console performs the effects,
which is what keeps a plugin out of the queue it would otherwise be racing: putting a message in an
inbox *queues* a session, so a plugin writing its own would be writing to the queue from inside the
pass still holding the claim on it. It is the split
[`Crossed`](composer.md#handing-off-without-being-asked) made, generalised into `Noting`.

| Effect | Means | Where |
|---|---|---|
| `return` | hand this value back to the model | `tool` only |
| `retry` | tell the model to try again, correctably | `tool` only, becomes a `ModelRetry` |
| `deliver` | put this message in the session's inbox | anywhere |
| `set` | write these values into my own store | anywhere |

`deliver` carries `said`, `forget`, and [how its panel is drawn](#how-a-plugins-panel-is-drawn), and
is attributed to the plugin that asked for it. Several plugins answering one event is **not** a
conflict needing a tiebreak: each `deliver` is one inbox entry, and the inbox is already a queue
that orders them and opens a turn per message.

**The vocabulary is closed and the console owns it.** A fifth effect is something somebody adds
deliberately, which is what keeps every plugin speaking one language and keeps the set of things a
plugin can do to a conversation readable in one table. An answer asking for an effect the event it
answers has no room for is **refused**, naming the plugin, rather than quietly doing nothing.

### Settings are already a place, so state is a setting with nothing in front of it

A plugin needs to remember things between events - the tree it last looked at, what it has already
said - and that is one mechanism rather than a second: `set` writes into the session's own column,
and what separates a **setting** from **state** is whether the card declared the name.

A name the card declares has a control in front of it, a person can answer it, and it comes back
under `settings` on every later payload. A name the card does not declare is the plugin's own: nothing
draws it, nobody else writes it, and it comes back under `state`. So a plugin with no card gets pure
state and has no second mechanism to learn, and a plugin with a card cannot confuse a control's value
with its own bookkeeping, because the split is made by the console rather than by a naming convention
somebody has to remember.

A setting's value is a switch's or a number's, because that is what a control can hold; state is
whatever JSON the plugin likes. Both are settled per session, so a fork starts with none and two
sessions running one plugin never see each other's.

### What a delivered message becomes

A `records.Note`: an arm of `Delivered` beside `Prompt` and `Steer`, carrying `said`, `forget`, the
plugin that asked for it, and how its panel is drawn. **Its own arm because nobody typed it**: a
reader has to be able to tell at a glance that a message in their own conversation is neither theirs
nor the model's, and the tag is what the panel's kind is read off.

**`deliver` writes a `Note` and nothing else, handoff included.** Letting a plugin name which record
arm to write would hand out the one vocabulary this console has to own, and special-casing the
bundled plugin so it kept an arm of its own would break the uniformity the whole design is for: the
built-in would be running on a path no third-party plugin could reach, which is exactly the
second-class tier this replaced.

**`records.Handoff` goes**, rather than surviving as an arm that is read and never written. Keeping
it would mean two renderings of one idea told apart by when the session ran, and a bundled plugin
whose panel does not look like anybody else's. The cost is stated and accepted: a tag no arm answers
to is a hard failure, so a checkpoint holding one no longer loads, and sessions recorded before this
do not survive it.

### How a plugin's panel is drawn

A message that opens a turn is a panel a person reads, and a plugin that cannot say anything about
how it looks would have every note in the console drawn identically: a pre-commit failure and a
handoff document are different things to meet halfway down a transcript. So `deliver` carries three
presentational fields beside `said` and `forget`, all optional:

| Field | What it sets | Absent means |
|---|---|---|
| `label` | the word on the panel's role | the plugin's own name |
| `title` | the hover text saying what a reader is looking at | nothing, as most kinds have none |
| `tone` | which of the console's inks the panel takes | the plain one |

**A plugin names a tone; it does not pass a colour.** That is the card vocabulary's argument in the
one place it matters most, and it is three arguments rather than taste:

- **The page is theme-aware.** A hex that reads well in the light theme is the one that disappears
  in the dark one, and the plugin cannot see which the reader is in. Naming a tone leaves the two
  values where the rest of the palette's are.
- **A restyle has to reach it.** A colour written by a plugin is a colour nothing here can move, so
  the day the palette shifts, plugin panels are the ones left behind.
- **[The palette runs on one axis](composer.md#forget)**, which is who produced the text. A plugin's
  message is on the person's side by construction, since the console's own machinery composed it and
  the model is the party about to be told. So a tone is *weight within* that side rather than a hue
  competing with it, which is the same answer the forget rule reached in taking a stronger ink
  rather than a colour of its own.

**A tone is durable the moment a plugin writes one**, because it goes in the record and the record
is the conversation. That is [choose the word before the value is
durable](../philosophy.md#the-words) applying to a vocabulary rather than a field name, and it means
the set wants settling before the first plugin ships rather than growing one name at a time.

**An unknown tone draws the plain one rather than refusing**, which is the opposite of what an
unknown record `kind` does, and deliberately so. A kind nothing answers to is a checkpoint this
console cannot read; a tone nothing answers to is a panel in the wrong ink, and a session that will
not render is far worse than that. It is the [valid-but-optional is not
malformed](../philosophy.md#refusing-at-startup-or-promising-not-to-raise) reading, one layer down.

## What the durability layer allows

**A pass replays.** `Run.step` records an effect's result and returns the recorded one ever after,
so anything not recorded as a step runs again on resume, possibly answering differently the second
time. That is a constraint on the console rather than a hazard to warn plugin authors about:

> **A plugin may only be asked where the console can record its answer as a step, and apply that
> answer through a mechanism it already has.**

**Tool calls already satisfy this, which is the permissive half and the one that is easy to get
wrong.** `StepwiseDurability.wrap_tool_execute` wraps every tool call in a step and writes a
`records.Returned`, so a plugin-provided tool's answer is recorded and a resumed pass replays it
without running the script again. Tools are the *safest* thing a plugin can contribute, not a
forbidden one.

**So the line is not "outside the agent's loop", which is where this was first drawn and is too
tight.** What decides is whether the answer is a *value the console can write down*, and two points
inside a turn qualify:

- **A tool call**, recorded as `records.Returned`.
- **Whatever is added to a request before it is sent.** `guiding` already does exactly this: it
  appends system-voice guidance in `before_model_request`, cheaply, because an appended message
  costs the cached prefix nothing where an edited instruction re-prices every request under it.
  Today that is safe by being a pure function of the history it is handed. A plugin cannot be
  trusted to be pure, so the same point offered to a plugin records what was injected and replays
  it.

What stays out is what has no value to record: **wrapping** a model request, where the influence is
control flow rather than a result, and anything that changes what was asked while leaving nothing
saying it did. Not offering those points is the fix; documenting them as a caveat is not.

**And it is a second reason for scripts.** A script reaches the loop only where the console hands it
a payload and takes an answer back, so the boundary is a property of the mechanism rather than a
rule an author has to know and observe.

## A card is declared, not rendered

**A plugin declares the shape of its card once, at `setup`; the console draws it.** A render
executes nothing, and only an `action` runs the script.

That is what lets a card come from a repository, and it is the answer to the instinct that cards
need to be in-process. They do not; what they need is for rendering not to be a call. Rendering is
constant and an action is rare, so the one place a spawn is affordable is exactly where it lands. It
is also what the design wanted anyway: a card the console draws stays in step with the console's own
controls, where a card a plugin draws drifts the first time anything is restyled, which is the whole
subject of [the controls rules](../philosophy.md#controls).

**The declared card is also the settings schema, and that is one thing rather than three.** A switch
is a boolean setting and a number box is a number setting, so one declaration is what the rail
draws, what the settings blob holds, and what arrives in every payload that plugin receives. There
is no second schema language and no way for the card and the settings to disagree.

**The cost, stated: a plugin cannot draw a control the vocabulary has no word for.** The reserve mark
is the worked example of that cost being real.

## Where a plugin comes from

**One mechanism, two places it may be declared**, differing only in who wrote it and whether it may
run at all.

| | Where | Trusted |
|---|---|---|
| User | `$XDG_CONFIG_HOME/mainplate/config.yaml` | you wrote it |
| Repository | `.mainplate/mainplate.yaml` at the session's tree | unless the session says otherwise |

Either way it is a mapping, and **the key is the plugin's name**:

```yaml
plugins:
  handoff: ~/.config/mainplate/plugins/handoff        # replaces the bundled one
  alice-guidance: ~/.config/mainplate/plugins/alice   # runs beside it
```

A repository's file declares plugins and nothing else. Not "does not today": the type has no field
for an endpoint, a credential, an isolation level or a reference database, so the escalation is
unrepresentable rather than checked.

**The three tiers are a union and never a merge.** Each plugin is declared in exactly one place and
carries all of its settings from there, so nothing anywhere resolves one declaration against
another. That is what namespacing buys beyond removing the shadowing: with a merge there would have
to be a rule for whose value of a shared field wins, and any such rule lets a repository reach one
field of something you enabled.

**A plugin is identified by its tier and the key it was installed under, and nothing shadows
anything.** A bundled `guidance`, an operator's `guidance` and a repository's `guidance` are three
plugins, and all three can be on at once.

**Within a tier a collision cannot arise**, which is worth saying because the obvious case is two
plugins written by two different people who both called theirs `guidance`. They are installed under
keys in one mapping, and a mapping's keys are unique, so the operator picks `alice-guidance` and
`bob-guidance` and neither file is touched. That is [why a plugin declares no name of its
own](#setup): a rule tying the key to a declared name would make that pair uninstallable
together. Two repositories each carrying a `review` were never a collision either, since no session
holds both.

**Nothing is a stack, and a claimed name settles nothing.** An operator's `guidance` and the bundled
`guidance` are two plugins, both on, both listed on [the settings
step](#starting-a-session-takes-four-steps) with a switch apiece. Installing one is the decision; this
console does not read a second meaning into the name you happened to give it.

Two designs were tried here and both are worse, in the same way and by degrees. **Shadowing** - one
plugin quietly stepping aside for another of the same name - is behaviour with no control in front of
it. **A claimed name setting a default off** is that failure made visible, which is better and still
wrong: it is a rule somebody has to learn in order to predict what their own console will do, bought
to save one press in a case nobody has met yet. What it was reaching for is genuinely wanted and is
cheaper elsewhere: if you install your own `guidance` and want ours off, the switch is right there,
under a heading, on the step you pass through anyway.

So a repository's plugin comes on and turns nothing else off, exactly as every other tier's does.
Where what it contributes is nameless, instructions and injections, it simply sits alongside ours and
both run, which is the coherent reading of two guidance loaders in one session; where it is named, it
is [prefixed](#two-namespaces-stay-shared-and-they-need-an-answer), so its `handoff` never claims
`/handoff` or the `hand_off` tool name.

The **settings blob is keyed by the qualified name**, so three plugins called `guidance` hold three
sets of settings rather than one they take turns overwriting.

### Two namespaces stay shared, and they need an answer

Namespacing settles identity. It cannot settle the two places a contribution lands in a space
somebody else is already using:

- **Tool names**, because the model sees one list and cannot be asked which `hand_off` was meant.
- **Composer leaders**, because `/guidance` is a word somebody types and can only mean one thing.

**A repository's tools and leaders are always prefixed with its qualified name.** That is not
tidiness. Unprefixed, one could collide deliberately, and if a collision refused then a repository
could stop your session starting, which is the thing every other rule here is built to prevent.
Prefixing costs description tokens and a clumsier name in the prefix, and buys a tier that cannot
interfere with anything.

**Between the operator's own and a bundled one, a collision is asked of what is actually *on***, so
it is a question about a set somebody can see and change: two plugins that both want `hand_off` are
both listed on the settings step with a switch apiece, and turning either off is what fixes it. Such
a pair is **refused, naming both**.

Refusing is safe here precisely because both are yours and the refusal has somewhere to say so: it
holds up the first message and not the settings step, so the screen still draws, still lists the two
plugins, and still has the switch that fixes it. A collision that stopped the page rendering would
be one nothing could act on.

The cost, stated: **two tiers, two rules.** The uniform alternative, prefixing every plugin's tools,
was not taken because it renames `hand_off` to something worse in every session's prefix to solve a
collision the operator can already see and fix.

## Trusting a repository's plugin

A repository's plugin is a program this console executes. What makes that safe to offer at all is
that it is a separate process run behind the confinement [`bash` already uses](sandbox.md): it
reaches the worktree it was handed and nothing else.

**Its confinement is fixed and narrow, and is never the session's own.** A session on
`Filesystem.EVERYTHING` gets `OverEverything`, so its `bash` reaches `/`; a repository's plugin in
that session still gets `InAWorktree`, whatever the session chose. The isolation a session picked is
a decision about what the **model** may reach, and a plugin is not the model. Inheriting it would
mean the one control that widens a session quietly widens somebody else's code along with it.

**A console with no sandbox runs none of them at all.** That is a refusal rather than a fallback: a
repository's plugin is safe to run because the process is confined, so a console that cannot confine
one has nothing to offer in its place.

### Before the conversation, connected; during it, never

**`setup` is the one event with a network, and every event after it has none.** That is not inherited
from the session either: a session with the network shut is a statement about the model, and a
repository plugin's own network answer turns on *which event* it is answering.

Without it the whole stage is one that can only fail. A plugin that needs a program has to fetch one,
and the two things a plugin most obviously wants - its own dependencies and the tooling a repository
runs its checks with - are both downloads.

**What makes it safe to offer is when it happens rather than a check on what is fetched.** `setup`
runs before the first message: the worktree holds the commit the repository supplied and nothing
else, no credential of this console's is inside the namespace, and nothing the model has written
exists yet. So what a connected run there can carry out is the repository's own code, to its own
author, which is not an exfiltration in any useful sense. Every later event is shut again, and that
is the half that matters - a turn boundary is where a plugin has read whatever the model has been
writing.

The cost, stated: **a repository plugin can reach the network once per session, and a session that
chose no network still gives it that.** The trust switch in the picker is what says no, and it says
no to the whole plugin rather than to the fetch.

### A scratch of its own, which is not the session's

Every confined plugin is handed a directory nobody else can write: `<workspaces>/plugins/<session>/
<tier>/<name>`, named on the payload as `scratch` and made `$HOME` inside the namespace.

**Its own rather than the session's, and that is a correction.** The session's scratch is a place the
model writes, so a plugin that kept an executable there would be running whatever the model last put
at that path - unattended, at every turn boundary, and reported into the conversation as though this
console had run it. Confinement does not help: both are confined the same way, so what it buys the
model is not privilege but *laundering*, a way to write into the transcript in another party's voice.

**`$HOME` and not just a bound path**, because that is where tools keep what they fetch. On the tmpfs
a command gets, a `uv run --script` shebang resolves an interpreter and a package tree at `setup` and
finds neither at the next event, with the network shut and no way to fetch them again. Pointing
`$HOME` at the scratch makes the ordinary case work with no environment variable in any plugin.

Two costs, both real:

- **It is per session and never shared.** A repository plugin that pulls its own interpreter pays for
  it once per session. Measured on `.mainplate/pre-commit`, which fetches CPython 3.14, `pre-commit`
  and a hook environment per entry in this repository's config, that is about 120MB and twenty
  seconds against a warm `uv` cache. A shared cache would be quicker and would have to be keyed by
  repository as well as by name, or two repositories declaring a plugin under one name would share a
  directory - which is exactly the channel the split above closes. Not worth reopening for the
  seconds.
- **Nothing removes it.** Neither this nor a session's worktree is collected today, so the disk a
  session takes is the disk it keeps. This is a new line item on a bill that already exists rather
  than a new bill, and whatever eventually answers for worktrees answers for these.

### The control is the refusal, not the permission

**`Choice.trusted` is on by default**, and the switch in the picker is how you say *no*. An earlier
draft had it the other way round - a grant defaulting off, recorded per repository - and both halves
of that were wrong.

**Defaulting off makes the common case a click nobody reads**, which is the failure the whole control
exists to avoid. And the honest reading of what picking a repository already means is that its code
runs: a session with a shell runs its build, its tests, its `pre-commit` and whatever those shell out
to, every one of them unread. A plugin is one more caller of that, not the escalation.

**Recording it per repository was worse still, because repositories change.** An answer given against
`owner/repo` covers a branch somebody pushed this morning as readily as the one you reviewed last
year, and no finer unit fixes it. Per directory is the same answer wearing a path. Per content hash
of the entrypoint looks rigorous and is not: a script that `exec`s a sibling, resolves a dependency,
or shells out to `make` has a stable hash and changing behaviour, so to make hashing sound you would
have to hash the transitive closure of everything it can reach, and **that closure is the tree**. Per
tree is the only sound content unit and is unusable as a prompt, since it re-asks on every commit,
which trains somebody to answer without reading.

So it is recorded **per session, on the `Choice`**, settled before the first message and fixed for
its life like everything else there - and changing your mind is [`fork`](forking.md), which is the
answer this console gives to every other question about a session's terms. A fork inherits it, since
a fork inherits the repository half of what its parent registered rather than reading any file again.

**Asked blind, in the picker.** [The worker clones and a request handler never
does](workspace.md#where-a-repository-comes-from), so when somebody picks a repository there is no
clone and no worktree, and no way to read a file out of it without a network fetch on a POST somebody
is waiting on. A repository carrying no plugins makes the answer inert, and nothing had to be fetched
to find that out.

### What the trust switch is actually guarding

Worth stating plainly, because "runs code" is both true and too vague to decide anything with.
Behind the confinement a repository's plugin can read and write the worktree and run what is in it,
which is what that session's `bash` could already do. The two things it adds are the ones the switch
is for:

- **It runs unattended**, at every turn boundary, rather than because a model asked for it and a
  reader can see the call in the transcript.
- **It puts text into the conversation**, which the model reads as a message. That is a delivery
  channel for prompt injection with a guaranteed slot on every turn, and it is the real escalation
  over a shell the model was already offered.

The line under the repository group on the settings step says that, in those terms, rather than
warning that something may be unsafe. A reader's next question is always *what happens*, and "they
run unattended at every turn boundary, and what they write is said to the model" answers it.

**What the switch is genuinely for is the session where the reading above does not hold**, and there
are two: a repository somebody is *reading* rather than working in - a stranger's pull request, a
dependency being triaged - and a session on `Filesystem.NOTHING`, which picks a repository and hands
the model no shell at all. That second one is why it is drawn in the picker rather than inferred from
the isolation: the two are near enough to look like one question and are not.

**The exposure is made visible rather than the answer made precise.** That is what the [settings
step](#starting-a-session-takes-four-steps) is for: it runs *before* `setup` and before the first
message, so every session shows what it is about to run, in the terms above, before any of it has
been executed and before anything has been said.

The switch in the picker and the switches on that step are therefore two decisions rather than one
asked twice. The picker's decides whether the repository's file is even *read*, which has to be asked
blind because there is no clone yet; the step's decides which of what was read is *run*, by which
point there is a list to look at. Answering `no` in the picker leaves the step with an empty
repository tier and nothing was fetched to find that out.

**Which plugins a session runs, and what they registered, is settled when the step is answered.**
That is `turn:0:tree:0`'s own shape and the only other thing here like it: a fact about one session
that could only be learned by doing the work, recorded once and replayed after. It is also what
[registration once per session](#setup) needs in order to be true across a restart.

### Read once, and never from a tree this console wrote

A session's model has `edit` over its worktree, so the file declaring a repository's plugins is a
file the model can write. Two rules keep that from being a way to run code of the model's choosing.

**It is read once, on the session's first pass, and recorded.** Every pass after replays the record
and reads no file. Without this, a model writes a plugin on turn 4 and the console runs it on turn 5.

**It is read from the commit the repository supplied, and never from a tree this console
snapshotted.** This is the one that is easy to miss, and "read it out of the recorded tree" sounds
like it covers the case when it does not. A snapshot is a tree a model wrote: it is captured with
`git add -A`, so a `.mainplate/` file the model created on turn 4 is *in* the tree recorded for turn
5. [A fork plants at a recorded tree](workspace.md#what-a-forks-worktree-is), so a fork that re-read
its own worktree would run a plugin the parent's model authored, one fork away from any session with
files.

**So a fork inherits the repository half of what its parent recorded, and sets it up afresh for
nothing.** What it inherits is the whole of that half, the tools and the cards and not merely the
names, because re-running a repository's plugin would be launching a script out of a tree the
parent's model had been editing. Both keys come across, the declaration as well as the registration:
a fork of a session that never got past its settings step has only the first, and re-reading the file
to draw a switch for it is the same read out of the same tree, one step earlier. Only a session
planted at a commit the *repository* provided ever reads that file or runs what is in it.

**The user half is a different question and a fork sets it up again**, because those scripts are the
operator's own and sit outside every worktree, so nothing a model wrote can reach them. That is the
one place the two tiers are still told apart, and the asymmetry is the security one rather than a
timing one: it turns on who wrote the file, which is the question the trust switch already asks.

**Where a session says it does not trust the repository nothing is read**, and the recorded set is
empty for that session's life. Trusting afterwards reaches sessions started after it and none before,
which is the answer `Choice` gives to every other question; [forking](forking.md) is how a session
changes its mind.

The cost, stated: **editing a repository's plugin has no effect on a session already running.**

## Where settings live

Three things get called settings here and they stay apart: **process configuration** is `Settings`
and `config.yaml`, read once at startup; **a session's settled choice** is `Choice`, fixed for life;
and **a session's mutable state** is what a plugin's `set` writes.

`hands_off` and `reserve` were columns, and the columns cannot grow: a plugin cannot run `ALTER
TABLE`, and `ADDED` was five migrations long already. So they became a `settings TEXT` column holding
a mapping keyed by qualified plugin name.

**And an `enabled TEXT` beside it, because the two have two owners.** `settings` is each plugin's own
- what its card declares and what it remembers - and `enabled` is the console's decision about which
plugins a session runs at all. Held together they would need a reserved field name no plugin could
use, which is a rule somebody has to know rather than a shape that cannot be got wrong.

Two costs, both real:

- **A whole-blob write clobbers.** A blob written whole means two plugins saving at once lose one of
  the saves. A `set` is therefore a `json_patch` against that plugin's own sub-object in a single
  `UPDATE`, so each keeps its save.
- **`STRICT` stops covering it.** The column is unchecked text, and the invariant moves to a parse
  at the boundary against the schema the card already declares: a control's own default answers for
  a field nobody has set, and a stored value of the wrong shape reads as untouched rather than
  refusing - which is what a hand-edited row deserves, and a better answer than a session that will
  not run.

## Which parts of the harness become plugins

One question sorts them: **can you describe mainplate with this absent and still have mainplate?**

- **`StepwiseDurability`: no.** It is the checkpoint, and the checkpoint is the conversation.
- **The file tools' isolation table: no.** [Which tools a session gets is a pure function of its
  recorded `Choice.isolation`](tools.md#which-tools-a-session-gets), and a registry would make it a
  function of the choice *and* the configuration at the moment of the pass.
- **Handoff: yes, and it is the proof**, because the protocol was derived from it. The gauge is the
  only thing it loses: its panel keeps the word and the hover text it has today, since those are a
  `label` and a `title` on the delivery rather than a kind of its own.
- **[What a session is told](guidance.md): yes**, both halves. The operator's guidance, the
  repository's `AGENTS.md` and the directory index are instructions contributed at `setup`; the
  nested handover is an injection into a request. It is the half handoff does not exercise.

### Both are ported, and that is the test

Not afterwards, and not as a demonstration. Between them **handoff and guidance exercise nearly every
event, every effect and every contribution**, which is what makes the pair a test rather than a pair
of examples:

- **Events**: `setup`, `tool`, `before_request`, `after_turn`, `compose`.
- **Effects**: `return`, `retry`, `deliver`, `inject`.
- **Contributions**: tools, instructions, an answer in the composer, a card, and a panel's `label`,
  `title` and `tone`.

Handoff reaches all of it but `before_request`, `inject` and `instructions`; guidance is exactly
those three. A protocol that cannot carry the two is wrong, and finding that out while porting them
was far cheaper than hearing it from the first plugin somebody else writes - it is what turned up
that `compose` and `action` were being fired at plugins that never asked for them.

Neither of them installs anything, which is the ordinary case and worth saying: a plugin whose
`setup` is one `return` of a constant is a plugin that had nothing to fetch, not one that skipped a
step.

**And a third tier is exercised by a fixture rather than by a bundled plugin.** `tests/plugins/git-status`
is a **bash** script a test repository carries, ported from a `SessionStart` hook: it is declared by
`.mainplate/mainplate.yaml`, it runs behind the sandbox, and it contributes the worktree's git status
as `instructions`. It is there because it is none of Python, none of ours and none of the console's
own tiers, so it proves three claims that would otherwise only be asserted in prose - any language,
reaches nothing it was not handed, and a repository's own script can contribute to what a session is
told.

### And this repository carries one, which is the rest of the proof

`.mainplate/pre-commit` runs this project's own hooks over what a session has changed, at every turn
boundary, and tells the model what is still failing. It is a port of a Claude Code `Stop` hook, and
**it is what turned the two unexercised corners of the protocol into used ones**: `action` and `set`
were covered by test plugins written for the purpose, and they are now covered by a plugin somebody
actually wants.

It is also the only thing here that exercises what a repository plugin is *for*, end to end and on
real work:

- **It installs at `setup`**, both halves. Its shebang is `uv run --script`, so the console executing
  it is what resolves the interpreter and `pre-commit` itself; what its `setup` then does is build a
  hook environment per entry in the config, which is the part `uv` knows nothing about.
- **It runs confined for the rest of the session**, with the network shut, out of a scratch nothing
  else can write. That is why it contributes a `tool`: it holds the only `pre-commit` a session can
  reach, so the model cannot run one from `bash` and has to ask.
- **It bounds itself.** Each delivery opens a turn and a turn is a model request, so a hook the model
  cannot satisfy would bill for itself until somebody noticed. `most` is a number on its card saying
  how many turns in a row it will chase one failure, and any message from a person resets it. The
  hook it came from needed no such thing, because the thing it interrupted was a person.

Three things it does *not* do, each because the console already answers them: it never stages, since
`--files` needs no index and the clone is read-only anyway; it never announces a run that only fixed
things, since `edit` is anchored on what was read and a stale anchor is refused; and it says nothing
about which turn it is on, since `opened_on` carries that.

**Bundled means default, not fixed.** Somebody who writes their own `guidance` installs it beside
ours and turns ours off with one switch on the settings step. The two are separate plugins with
separate settings, and nothing is replaced out of view.

That is a stronger claim than an earlier draft made, and it is the one worth making: if ours cannot
be turned off in favour of somebody else's, the interface is a description of what we happened to
build rather than something anybody can build against.

What that does **not** extend to is the row above it. Durability and the file tools are not plugins
to switch off, so the registry says nothing about them, and a console with every plugin disabled is
still this console.

## What fits, and what does not

The useful question is not whether this is extensible but **in which direction**, so here is the
honest map.

### What the protocol already holds

A gate at a turn boundary (a linter, a test run, `pre-commit`). A tool. Conditional context
injection, of which handoff is one instance. A policy with a card and a switch behind it. A composer
command under its own leader. And a **notifier**, which is worth naming because the rule reads as
though it forbids one: *a plugin answers rather than acts* governs the **console's** state, the
queue and the store. A plugin doing its own I/O is ordinary, and a plugin that posts to a chat and
answers with no effects at all is using this exactly as intended.

### What wants an event that is not written yet

Two things that looked speculative when this was first written are not, because [porting
guidance](#both-are-ported-and-that-is-the-test) commits to them:
`instructions` as a `setup` contribution, and `before_request` with its `inject`. Both are in the
tables above.

What is left genuinely open fits the shape and needs a payload and an answer defined, not a new
mechanism:

- **A gate before a console tool runs**, refusing a command or a path. Recordable, since the refusal
  becomes the return, so this is a payload away rather than a question.
- **Starting a session**, for anything wanting to delegate. The first one that is genuinely hard,
  because it reaches the fork machinery rather than adding a field.

### What is structurally out, for three separate reasons

Keeping the reasons apart matters, because only one of them is negotiable:

- **Durability.** Wrapping a model request; changing what was asked while leaving nothing that
  records it. There is no value to write down, so there is no safe version of it.
- **The process boundary.** No live state across events. No progress while a plugin works, so a long
  tool call is a black box, which is the limitation [a command already
  has](composer.md#run). A process per event, until measurement says otherwise, at which point the
  transport changes and the protocol does not.
- **No client surface.** A plugin ships no JavaScript. Cards are declared and drawn by the console,
  so a plugin wanting rich interaction in the browser has nowhere to put it. This is the sharpest
  limit against what an in-process plugin in a harness that owns its own frontend can do, and it is
  the price of a plugin being a script.

Also out: drawing in the transcript region, of which the lost reserve mark is the cost made concrete; a
colour of a plugin's own, since a panel takes a `tone` this console named; and annotating a turn
after the fact, because the checkpoint is written once.

### The file tools are excluded by a rule rather than by difficulty

`read`, `edit`, `list` and `bash` are not awkward to port. They are **forbidden by this design's own
constraint**: [which tools a session gets is a pure function of its recorded
`Choice.isolation`](tools.md#which-tools-a-session-gets), and routing that through a registry would
make it a function of the choice *and* whatever configuration held at the moment of the pass, so a
replayed pass would build a different agent than the one whose answers are recorded. They stay the
console's.

### The real ceiling is the payload

**A plugin decides on what it is handed and nothing else**, so most new capabilities are this
repository adding a field to an event rather than a plugin author reaching for something. That is
the closed vocabulary working, and its cost is exactly that: somebody who needs a fact the payload
does not carry waits for it rather than fetching it.

That is the trade taken twice, once for isolation and once for replay-safety. What it buys is that
no plugin can break a conversation's durability, reach another repository's session, or drift out of
step with the console's own controls. What it costs is the open-endedness that makes an in-process
plugin system feel powerful, and there is no version of this that has both.

If in-process Python ever earns its place, it is a **separate mechanism with a name of its own**,
for the operator's own machine, and not this one widened until it fits. Widening this one is how the
two tiers came back last time.
