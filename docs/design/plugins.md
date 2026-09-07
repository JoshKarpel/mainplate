# Plugins

How somebody adds to this console without editing it: the protocol a plugin speaks, the events it is
sent, the effects it may ask for, and what has to be true before a repository's own plugin runs.

**None of this is built yet.** Every other page here is the authority on the code as it stands; this
one is the design the code is being written to, and the line above goes when it is.

## Handoff is the specification

The protocol was not designed and then tried against something. It was read off [the handoff](
composer.md#handoff), which is the most demanding thing this console does that a plugin should be
able to do, and which turns out to need almost everything at once.

`hand_off` alone produces three separate effects from one call:

```python
raise ModelRetry(...)   # a correctable refusal, where the document is too short to be one
await handing(written)  # a message delivered into the inbox, carrying forget=True
return "Recorded. …"    # and a value back to the model
```

And the tool is the smaller half. The whole of it:

| What handoff does | What the protocol therefore has |
|---|---|
| `hand_off`, its description, `document: str` | a **tool** contribution: name, description, JSON Schema |
| refuses a document under `LEAST` characters | a **retry** effect, correctable rather than a fault |
| writes `records.Handoff(forget=True)` to the inbox | a **deliver** effect, with a boundary and attribution |
| returns `Recorded. …` to the model | a **return** effect |
| `ASKING` delivered once the reserve is crossed | an **after_turn** event carrying the numbers to decide on |
| never fires twice running | that payload saying what the turn *opened on* |
| `hands_off` and `reserve` | **settings**, declared once |
| its card in the rail | a **card** contribution, whose controls are those settings |
| `/handoff`, empty box allowed, note appended to `ASKING` | an **answer** contribution and a **compose** event |
| `records.Handoff` drawn as its own panel kind | a **label**, **title** and **tone** on the delivery |
| `reserve_mark` on every turn rule | **nothing. This one does not port.** |

**The last row is stated rather than quietly dropped.** `reserve_mark` draws a bar across every rule
at the fraction the reserve opens at, which is a mark on a control the console owns, in a region
plugins have no vocabulary for. Stretching the card language into the transcript to reach it would
be inventing an axis in order to have a cross-product. So the gauge stays the console's, and a
handoff shipped as a plugin does without it.

One thing gets *better* under the protocol, which is worth noting because it is the opposite of what
a port usually does. `recorded_ask()` exists today so that the auto path and the `/handoff` button
cannot come to say different things about what a handoff is. Under the protocol both are events into
the same script, so that sharing is internal to the plugin rather than a function two callers have
to remember to reach for.

## A plugin is one script, spoken to in JSON

**A plugin is a single executable.** The console runs it with a JSON payload naming an event and
reads a JSON answer naming effects. That is the whole contract.

```console
$ echo '{"event":"tool","tool":"hand_off","args":{"document":"…"},"session":"a1b2"}' | ./handoff
{"return": "Recorded. …", "deliver": [{"said": "…", "forget": true}]}
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

## Registration

**The first call to a plugin asks it what it is.** Everything it contributes comes back from one
`describe`, including which events it wants:

```json
→ {"event": "describe"}
← {"events": ["after_turn", "compose"],
   "tools": [{"name": "hand_off", "description": "…", "schema": {…}}],
   "answers": [{"leader": "handoff", "saying": "hand off and clear the context", "demands": false}],
   "card": {"heading": "handoff", "rows": [
     {"switch": {"name": "hands_off", "label": "auto at reserve", "default": true}},
     {"number": {"name": "reserve", "label": "keep back", "unit": "K", "default": 40}}]}}
```

A plugin may also return **`instructions`**, which are composed into what the session is answered
under. Those are a `describe` contribution rather than an event for the reason tools are: they sit
in front of the cached prefix, so they have to be settled for the session or every request under
them is re-priced. It is what lets [the guidance
system](#both-are-ported-as-part-of-this-work-and-that-is-the-test) be a plugin at all.

**`events` is what stops this being wasteful.** Without it every event goes to every plugin and a
console with six of them spawns six processes per turn to be told nothing five times. With it, a
plugin that only wants `after_turn` is never launched for anything else.

**What comes back is settled for a session's life, and the cached prefix is what decides that.**
Tool definitions sit above the system prompt in the cached prefix, so introducing one
mid-conversation invalidates the whole prefix beneath it: the same arithmetic that keeps [`hand_off`
unconditional](composer.md#handoff). So a session's contributions are recorded at turn 0, the way
everything else settled about a session is, and a plugin edited underneath a running session reaches
it on no turn at all. *When* the call is made is a different question, and it is once per session on
that session's own first pass, for both tiers alike: see
[below](#starting-a-session-takes-two-steps).

**Forking is how a conversation picks up an edited plugin**, and that is the existing answer rather
than a new one: a fork is a session, so it describes the operator's plugins afresh, and the way to
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

## Starting a session takes two steps

A plugin's settings are the controls on its card, and its card comes back from `describe`. So
nothing knows a plugin's settings exist until it has been run, and **a repository's plugin cannot be
run until its worktree is planted**, which is the first pass, because [the worker clones and a
request handler never does](workspace.md#where-a-repository-comes-from).

The old shape cannot absorb that. Creating a session used to record the choice and queue the first
message in one POST, so the first turn would be answered before anything had been read out of the
repository. So creation splits:

1. **The choices.** Endpoint, model, repository, isolation, and the grant. This records the
   `Choice`, enrols the session, calls `make_ready`, and redirects to the session's own page.
2. **The setup pass.** Plants the worktree, `describe`s every plugin, and records what they declared
   at turn 0. Then it reaches `opening_turn`, finds an empty inbox, and comes back `Blocked`.
3. **The settings.** The page swaps in a step showing every plugin's card, or what went wrong. The
   first message leaves it.

**Nothing about step 2 is a new mechanism.** `opening_turn` already suspends a pass on the inbox,
and its own note says an empty take raises and the pass comes back `Blocked` until something is
delivered. So "set up, then stop and wait" is `planting` moving above `opening_turn` in the loop,
plus a session that is queued by `make_ready` rather than by a delivery. A session nobody types into
holds no lease and no worker slot, because a blocked pass has released its claim.

**Every plugin is `describe`d there, the user's included**, rather than the user's at process
startup and the repository's later. One moment for both is worth more than the earlier read: it
removes a tier's worth of asymmetry, and a plugin edited on disk reaches the next new session
without the console being restarted, which matters most while somebody is writing one.

**Step 3 is always drawn, and there is always something in it.** Skipping it when no plugin declares
settings would make the number of steps depend on what a repository happens to carry, so the flow
could not be described, learned or tested as one thing.

The empty version of that step is not a case worth designing around anyway, because **handoff ships
as a plugin and is on in every session**: there is no console with no plugins, so the step always
has at least a heading, a switch and a number in it. The cost this looked like it had, a click
through a blank page, is not one that can arise.

**It is drawn in two groups, the operator's and the repository's, with the banner under the
second.** Grouping by where a plugin came from rather than by what it does is the whole point: those
two are not equally trusted, and a reader deciding what to leave on is deciding about provenance.
[What that banner says](#what-the-grant-is-actually-guarding) is the exposure in plain terms rather
than a warning that something may be unsafe.

**Each plugin can be turned on or off there, and that is settled for the session.** Some arrive
already off, because [a name claimed by a more specific tier defaults the one under it
off](#where-a-plugin-comes-from); the step draws that state rather than hiding it. A plugin that is
off contributes nothing: no tool in the prefix, no card, no answer in the composer, and no events.
The switch is live at step 3 because nothing has been asked yet, and frozen afterwards because a
tool definition leaving the prefix invalidates everything under it exactly as one arriving late
does. So the rail draws the state and does not change it, and [forking](forking.md) is how a
conversation changes its mind, as it is for the model and the repository.

**A plugin's settings stay live where its being loaded does not**, and the two are different
questions rather than an inconsistency. A setting is a value the plugin reads when it runs; being
loaded decides what is in the cached prefix. That is what keeps the auto-handoff switch behaving
exactly as it does today, taking effect on the press, while the plugin behind it is fixed for the
session.

**It is a state of the session page and not a route of its own.** The session id exists from step 1,
so the URL is stable and bookmarkable while the clone runs, and the console already renders a
session whose worktree is not yet planted and already has the live connection that fills it in when
turn 0 lands. A second address would be a page somebody can be sitting on when the thing it is
waiting for arrives somewhere else.

### A plugin that will not load stops the session

**Turn 0 is recorded only once every declared plugin has described.** A failure ends the pass with
nothing written, the page says which plugin and why, and **retrying is another pass**: `make_ready`
is the whole of the mechanism, and `describe` runs again from scratch with nothing recorded to
conflict with.

That is forced rather than chosen. The store keeps the value a key was first given, so a turn-0
record written with one plugin missing could never be corrected, and a reload control that rewrote
it would be writing into a slot that ignores it.

**One broken plugin stopping the whole session is the right answer in every tier**, by the question
[refusing at startup](../philosophy.md#refusing-at-startup-or-promising-not-to-raise) asks: a
repository plugin is one you granted and a user plugin is one you configured, so either failing
silently leaves somebody holding a choice they cannot use. A session that quietly ran without it
would be answering under a setup nobody asked for.

**Which puts the bundled plugins on that same path, and handoff is on in every session**, so a
handoff that will not describe is a console that starts no sessions at all. That reads alarming and
is the correct severity: a console whose handoff is broken *is* broken, and it is broken in this
repository rather than in somebody's configuration. It is also the one tier where the failure is not
a realistic runtime mode, because a bundled plugin that cannot describe fails this repository's own
suite long before it reaches anybody. What stays realistic is the user's own plugin and the
repository's, which is where the retry and the message naming the plugin are aimed.

### What this costs, and the title

**Creation stops being fire-and-forget.** Time to a first answer is unchanged, since the clone
happens either way, but you now create, wait, and come back to type, where before you could type and
walk away. That is the real cost and it is bigger than an extra click. It is taken deliberately:
consistency and a setup that cannot half-happen are worth more here than the convenience, and the
convenience is recoverable later in a way a half-configured session is not.

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

**The setup step and the rail both draw a plugin's controls, from one declaration.** That is
`tending_fields`'s existing shape, where the picker, the fork page and the rail render one pair of
fields from one call, and the trap here is rendering them twice rather than reaching for it.

## The events

Each carries its own payload and takes its own effects. They are a short list this console owns, for
[the reason below](#what-the-durability-layer-allows), and not a seam per point in the agent's loop.

| Event | Fired | Payload beyond `session` and settings | Effects it may ask for |
|---|---|---|---|
| `describe` | once per session | nothing | the contributions above |
| `tool` | the model called one of its tools | `tool`, `args` | `return`, `retry`, `deliver`, `set` |
| `before_request` | a model request is about to be sent | `messages`, `worktree` | `inject` |
| `after_turn` | a turn was recorded | `turn`, `opened_on`, `context`, `window`, `worktree` | `deliver`, `set` |
| `compose` | its answer was submitted | `said` | `deliver`, `set` |
| `action` | a control on its card was pressed | `control`, `value` | `set`, `deliver` |

**`before_request` is the one inside a turn, and it exists because [guidance is being ported onto
this protocol](#both-are-ported-as-part-of-this-work-and-that-is-the-test).** Its answer is
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

**`after_turn` is asked only where a turn actually ended.** A pass that spent its allowance, hit a
refusal, or crossed the reserve returns before it, so a plugin is never asked about a turn that
stopped part-way, which is a turn left unfinished for reasons that are the console's rather than the
conversation's.

## The effects

**A plugin answers with what it wants done, and never does it.** The console performs the effects,
which is what keeps a plugin out of the queue it would otherwise be racing: putting a message in an
inbox *queues* a session, so a plugin writing its own would be writing to the queue from inside the
pass still holding the claim on it. It is the split
[`Crossed`](composer.md#handing-off-without-being-asked) already makes, generalised.

| Effect | Means | Where |
|---|---|---|
| `return` | hand this value back to the model | `tool` only |
| `retry` | tell the model to try again, correctably | `tool` only, becomes a `ModelRetry` |
| `deliver` | put this message in the session's inbox | anywhere |
| `set` | write these values into my settings | anywhere |

`deliver` carries `said`, `forget`, and [how its panel is drawn](#how-a-plugins-panel-is-drawn), and
is attributed to the plugin that asked for it. Several plugins answering one event is **not** a
conflict needing a tiebreak: each `deliver` is one inbox entry, and the inbox is already a queue
that orders them and opens a turn per message.

**The vocabulary is closed and the console owns it.** A fifth effect is something somebody adds
deliberately, which is what keeps every plugin speaking one language and keeps the set of things a
plugin can do to a conversation readable in one table.

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

**A plugin declares the shape of its card once, in `describe`; the console draws it.** A render
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

**The cost, stated: a plugin cannot draw a control the vocabulary has no word for.** `reserve_mark`
is the worked example of that cost being real.

## Where a plugin comes from

**One mechanism, two places it may be declared**, differing only in who wrote it and whether it may
run at all.

| | Where | Trusted |
|---|---|---|
| User | `$XDG_CONFIG_HOME/mainplate/config.yaml` | you wrote it |
| Repository | `.mainplate/mainplate.yaml` at the session's tree | only once granted |

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
own](#registration): a rule tying the key to a declared name would make that pair uninstallable
together. Two repositories each carrying a `review` were never a collision either, since no session
holds both.

**Which makes replacing a bundled plugin something the operator opts into by choosing the name.**
Install yours as `guidance` and the stack below applies; install it as `alice-guidance` and both
run. The naming *is* the decision, rather than a separate switch meaning the same thing.

Shadowing was the first design and is worse in the way silent things are worse: one plugin quietly
stepping aside for another of the same name is behaviour with no control in front of it.

**A claimed name still means something, though: it sets a default rather than replacing anything.**
An operator's plugin called `guidance` comes on and the bundled `guidance` comes **off by default**,
both drawn on [the settings step](#starting-a-session-takes-two-steps) with their switches in that
state. Bringing your own then costs no presses in the ordinary case, and running both, or keeping
ours instead, costs one. Nothing is hidden and nothing is settled out of view, which was the whole
complaint against shadowing.

**The stack is the operator's over the bundled, and a repository is not in it.** That is a departure
from the usual most-specific-wins layering, taken on two grounds:

- **A repository would be reaching into the operator's setup.** Defaulting a bundled plugin off by
  shipping a file named `handoff` is a cloned repository deciding which of *your* plugins are live.
  Visible and one toggle away is better than silent, and it is still the thing every other rule on
  this page refuses.
- **It could not deliver on it anyway.** A repository's tools and leaders are
  [prefixed](#two-namespaces-stay-shared-and-they-need-an-answer), so its `handoff` cannot claim
  `/handoff` or the `hand_off` tool name. Defaulting the bundled one off would leave the session
  with neither, chosen by the repository.

So a repository's plugin comes on by default and turns nothing else off. Where what it contributes
is nameless, instructions and injections, it simply sits alongside ours and both run, which is the
coherent reading of two guidance loaders in one session.

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

**Between the operator's own and a bundled one, a collision is asked of what is actually *on*, after
[the name stack](#where-a-plugin-comes-from) has set the defaults.** So the ordinary case never
arises: an operator's `guidance` has already defaulted the bundled `guidance` off, and one of the
two claimants is not running. What is left is two differently-named plugins that both want
`hand_off`, or same-named ones somebody deliberately switched both on, and those are **refused,
naming both**.

Refusing is safe here precisely because both are yours and the refusal has somewhere to say so: it
holds up the first message and not the settings step, so the screen still draws, still lists the two
plugins, and still has the switch that fixes it. A collision that stopped the page rendering would
be one nothing could act on.

The cost, stated: **two tiers, two rules.** The uniform alternative, prefixing every plugin's tools,
was not taken because it renames `hand_off` to something worse in every session's prefix to solve a
collision the operator can already see and fix.

## Trusting a repository's plugin

A repository's plugin is a program this console executes. The grant in front of that is a real
boundary rather than a disclosure, because it is a separate process run behind the confinement
[`bash` already uses](sandbox.md): it reaches the worktree it was handed and nothing else.

**Its confinement is fixed and narrow, and is never the session's own.** A session on
`Filesystem.EVERYTHING` gets `OverEverything`, so its `bash` reaches `/`; a repository's plugin in
that session still gets `InAWorktree`. The isolation a session picked is a decision about what the
**model** may reach, and a plugin is not the model. Inheriting it would mean the one control that
widens a session quietly widens somebody else's code along with it.

### What the grant is actually guarding

Worth stating plainly, because "runs code" is both true and too vague to decide anything with.
Behind the confinement a repository's plugin can read and write the worktree and run what is in it,
which is what that session's `bash` could already do. The two things it adds are the ones the grant
is for:

- **It runs unattended**, at every turn boundary, rather than because a model asked for it and a
  reader can see the call in the transcript.
- **It puts text into the conversation**, which the model reads as a message. That is a delivery
  channel for prompt injection with a guaranteed slot on every turn, and it is the real escalation
  over a shell the model was already offered.

The banner under the repository group says that, in those terms, rather than warning that something
may be unsafe. A reader's next question is always *what happens*, and "this repository's plugins run
on every turn without being asked, and what they write is said to the model" answers it.

### When the set is known

[The worker clones and a request handler never does](workspace.md#where-a-repository-comes-from), so
when somebody picks a repository there is no clone and no worktree, and no way to read a file out of
it without a network fetch on a POST somebody is waiting on.

So **the grant is asked blind, in the picker, defaulting to off**: may plugins carried by this
repository run. A repository carrying none makes the grant inert, and nothing had to be fetched to
find that out. It is recorded per repository, keyed by `Repository.id`, in a table beside
`sessions`: per repository rather than per session, because a question re-asked every session is one
answered yes without reading it.

**Which plugins a session runs, and what they registered, is settled on its first pass and recorded
at turn 0.** That is `turn:0:tree:0`'s own shape and the only other thing here like it: a fact about
one session that could only be learned by doing the work, recorded once and replayed after. It is
also what [registration once per session](#registration) needs in order to be true across a restart.

### Why the grant is coarse, and what pays for it

A grant per repository is plainly imprecise: it covers a branch somebody pushed this morning as
readily as the one you reviewed last year. The finer units are worth walking, because two of them
look better than they are.

- **Per directory**, which is what most harnesses do, is the same grant wearing a path.
- **Per content hash of the entrypoint** is the one that looks rigorous and is not. A hook that
  `exec`s a sibling script, or resolves a dependency, or shells out to `make`, has a stable hash and
  changing behaviour. To make hashing sound you would have to hash the transitive closure of
  everything the script can reach, and **that closure is the tree**.
- **Per tree** is therefore the only content unit that is actually sound. Git has already computed
  it, and this console already records it. It is also unusable as a prompt: it re-asks on every
  commit, which trains somebody to grant without reading, which is the failure the whole control
  exists to avoid.

So the grant stays coarse and **the exposure is made visible instead of the grant precise.** That is
what the [settings step](#starting-a-session-takes-two-steps) is for: it runs after `describe` and
before the first message, so every session shows what it actually loaded, in the terms above, before
anything has been said to it. The review moment is per session even though the grant is not, which
is the part somebody can actually act on.

### Read once, and never from a tree this console wrote

A session's model has `edit` over its worktree, so the file declaring a repository's plugins is a
file the model can write. Two rules keep that from being a way to run code of the model's choosing.

**It is read once, at turn 0, and recorded.** Every pass after replays the record and reads no file.
Without this, a model writes a plugin on turn 4 and the console runs it on turn 5.

**It is read from the commit the repository supplied, and never from a tree this console
snapshotted.** This is the one that is easy to miss, and "read it out of the recorded tree" sounds
like it covers the case when it does not. A snapshot is a tree a model wrote: it is captured with
`git add -A`, so a `.mainplate/` file the model created on turn 4 is *in* the tree recorded for turn
5. [A fork plants at a recorded tree](workspace.md#what-a-forks-worktree-is), so a fork that re-read
its own worktree would run a plugin the parent's model authored, one fork away from any session with
files.

**So a fork inherits the repository half of what its parent recorded, and describes it afresh for
nothing.** What it inherits is the whole of that half of turn 0's record, the tools and the cards
and not merely the names, because re-running a repository's plugin would be launching a script out
of a tree the parent's model had been editing. Only a session planted at a commit the *repository*
provided ever reads that file or runs what is in it.

**The user half is a different question and a fork re-describes it**, because those scripts are the
operator's own and sit outside every worktree, so nothing a model wrote can reach them. That is the
one place the two tiers are still told apart, and the asymmetry is the security one rather than a
timing one: it turns on who wrote the file, which is the question the grant already asks.

**Without a grant nothing is read**, and the recorded set is empty for that session's life. Granting
trust afterwards reaches sessions started after it and none before, which is the answer `Choice`
gives to every other question; [forking](forking.md) is how a session changes its mind.

The cost, stated: **editing a repository's plugin has no effect on a session already running.**

## Where settings live

Three things get called settings here and they stay apart: **process configuration** is `Settings`
and `config.yaml`, read once at startup; **a session's settled choice** is `Choice`, fixed for life;
and **a session's mutable state** is what a plugin's `set` writes.

`hands_off` and `reserve` are columns today, and the columns cannot grow: a plugin cannot run `ALTER
TABLE`, and `ADDED` is five migrations long already. So they become one `settings TEXT` column
holding a mapping keyed by qualified plugin name, and `SELECTION` reaches into it with
`json_extract` exactly as it already reaches into a session's `choice`.

Two costs, both real:

- **A whole-blob write clobbers.** `tend` writes both columns in one statement so the pair cannot be
  half applied; a blob written whole means two plugins saving at once lose one of the saves. A `set`
  is therefore `json_set` against one sub-field in a single `UPDATE`.
- **`STRICT` stops covering it.** The column is unchecked text, and the invariant moves to a parse
  at the boundary against the schema the card already declares.

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
  repository's `AGENTS.md` and the directory index are instructions contributed at `describe`; the
  nested handover is an injection into a request. It is the half handoff does not exercise.

### Both are ported as part of this work, and that is the test

Not afterwards, and not as a demonstration. Between them **handoff and guidance exercise every
event, every effect and every contribution**, which is what makes the pair a test rather than a pair
of examples:

- **Events**: `describe`, `tool`, `before_request`, `after_turn`, `compose`, `action`.
- **Effects**: `return`, `retry`, `deliver`, `set`, `inject`.
- **Contributions**: tools, instructions, an answer in the composer, a card, and a panel's `label`,
  `title` and `tone`.

Handoff reaches all of it but `before_request`, `inject` and `instructions`; guidance is exactly
those three. A protocol that cannot carry the two is wrong, and finding that out while porting them
is far cheaper than hearing it from the first plugin somebody else writes.

**Bundled means default, not fixed.** Somebody who writes their own `guidance` gets it on and ours
off without touching a control, because [a claimed name sets the
default](#where-a-plugin-comes-from); somebody who wants both, or wants ours after all, presses one
switch on the settings step. Either way the two are separate plugins with separate settings, and
nothing was replaced out of view.

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
guidance](#both-are-ported-as-part-of-this-work-and-that-is-the-test) commits to them:
`instructions` as a `describe` contribution, and `before_request` with its `inject`. Both are in the
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

Also out: drawing in the transcript region, of which `reserve_mark` is the cost made concrete; a
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
