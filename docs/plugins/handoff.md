# Handoff

**A forget whose message the session wrote itself.** The plugin asks a session to write down where it
has got to; the model does that with its own tools, calls `hand_off` with the document, and the
document is delivered back as a message carrying a boundary. What the next model is told is the
document and nothing above it.

It is `src/mainplate/plugins/bundled/handoff`, and it is [the plugin the protocol was read
off](../design/plugins.md#handoff-is-the-specification): a tool with a schema, a correctable refusal,
a delivery carrying a boundary and a tone, a value back to the model, a turn-boundary condition
decided on numbers the payload carries, a card whose two controls are its two settings, and an answer
in the composer under its own leader. So what follows describes a script this console speaks to over
a pipe, and everything below is that script's rather than the console's unless it says otherwise.

**Which means every word of it can be replaced.** Install your own `handoff` beside the bundled one
and turn ours off with one switch on the settings step; the two are separate plugins with separate
settings, and nothing is replaced out of view.

## It happens in the session, not in an aside

That was the first design and was worse in four ways at once:

- **The worktree.** A fork plants a fresh one at a recorded tree, and an end-fork has no recorded
  tree at all, so it falls through to the repository's default branch. The agent asked to describe
  the work would have been looking at a directory with none of it in it, and "check rather than
  recall" is the whole reason for letting it use tools.
- **The cost.** `altogether` sums a session's own turns, so a handoff's spend would have landed on a
  different total, and the parent's running figures would have been quietly missing it.
- **The recursion.** An aside inherits its parent's settings, and it carries the parent's whole
  window, so it starts near any reserve by construction. Turning auto-handoff off on the copy is one
  line and exactly the kind that gets forgotten until it recurses in production.
- **The cache.** Instructions are the per-request parameter Pydantic AI renders in front of the
  whole cached prefix, so a fork's first request pays full price for the window unless its composed
  instructions come out byte-identical. In-session there is no second prefix: the handoff turn is
  the next turn on the one already cached.

## The tool

**It is in every session's prefix**, and that is arithmetic rather than convenience. Tool
definitions sit above the system prompt in the cached prefix, so adding one invalidates the whole
conversation beneath it: introduced at handoff time it would cost a full uncached read of the
window, where a permanent one costs its own description at cache-read prices on every request. Four
orders of magnitude. What a plugin contributes is therefore settled at `setup`, once per session,
and a plugin's tools are not conditioned on the isolation the way the file tools are, because what
one reaches is decided by its own tier rather than by what the *model* may touch.

**A tool rather than the turn's prose, because models leak the framing.** Asked for a handoff in
words, a model writes "Here is the handoff document: ... What would you like next?", and the framing
is then durably part of what the next model is told. An argument splits the document from the chat
around it, and `hand_off` refuses anything under `LEAST` characters, which is what catches the model
that acknowledges the ask instead of answering it. That refusal is a `retry`, since what it turns
down is correctable from the message.

**Neither the ask nor the tool prescribes a shape**, and that is a decision rather than an omission.
What somebody picking up a refactor needs handed over and what somebody picking up an investigation
needs are different documents, so a fixed set of headings would have every session filling in the
ones it has nothing to say under. `ASKING` says what a handoff is about, where the work got to, what
was decided and why, what to do next, and stops; the tool's description carries the parts that are
*mechanical* rather than editorial (the document becomes the whole context, pass it alone, check
rather than recall) and says outright that the shape is the model's.

## Asking for one

**`/handoff` in the composer**, which is an answer in the sending menu like every other. The menu's
premise is that its rows are decisions about the text somebody typed, and this one is: a handoff
takes an optional note saying what it should dwell on, and the box is exactly where such a note is
written. `/handoff` on its own hands off, and `/handoff` with a paragraph hands off pointed at what
the paragraph says.

**What is typed is appended to the standing ask rather than replacing it**: "dwell on the parser
work" on its own is an instruction to summarise a summary. The plugin composes both, so a handoff
nobody asked for and one somebody typed a paragraph into cannot come to say different things about
what a handoff *is* - and that sharing is internal to the plugin rather than a function two callers
have to remember to reach for, which is one thing the port made strictly better.

**The box may be empty for this answer and no other**, which is what `demands: false` on the
declared answer buys. The box is `required`, which is right for a message and would refuse the
ordinary handoff, so the row's button says it does not need the form's required fields and the
boundary allows an empty message for this disposition alone. [The composer](
../design/composer.md#leaders) is where that mechanism is argued.

It sits beside `Forget` in the menu because they are the same family: both end a stretch of context
where they stand, and what separates them is who writes what the next one opens on.

## What it delivers

**The ask and the document are both notes, and what tells them apart is the boundary.** The ask
carries none, because the context has to survive long enough to be summarised; the document carries
one, because a handoff that did not clear the context would be a summary of the conversation appended
to the conversation, which is the one shape that costs tokens and buys nothing. Neither is a record
arm of its own: [`deliver` writes a `Note` and nothing else](
../design/plugins.md#what-a-delivered-message-becomes), handoff included.

What the plugin names is the `label` on the panel's role, the `title` saying what a reader is looking
at, and the `tone`, which is weight within the person's side rather than a hue competing with it: the
ask is `quiet` and the document is `strong`.

## Handing off without being asked

The same script, fired by a number rather than by a person: both are events into the same plugin,
so the words a handoff is asked for in are in one place and cannot come apart. What the plugin's card
in the rail holds is the two settings that decide when.

**Headroom in tokens, never a percentage.** What has to be true is that the handoff run has room to
do its work: the ask, a few tool calls, the returns they bring back, and the document. That is an
absolute quantity and the same one on every model, where a fifth of the window is 40k on a 200k
model and 200k on a 1M one, the same setting re-tuned per model, by somebody who would have to know
the absolute number anyway in order to pick the fraction. The control is denominated in thousands
because a reserve is only ever chosen in round ones, and the plugin does that arithmetic: the `unit`
on a declared number is presentation, and the console does none of its own with it.

**A window and not a threshold.** `opens` is where a handoff becomes worth asking for and `shuts` is
where there is no longer room to write one. Two bounds because a single turn can cross the first and
overshoot the second, which one large tool return is enough to do. The second is a constant in the
plugin rather than a setting, because it is not a preference: below it a handoff is a request nobody
should pay for.

**Past the close the plugin stops asking, and reaches for nothing smaller.** A cheaper non-agentic
summariser would be a second path that only ever runs when the first is already failing, so nothing
would exercise it and its bugs would surface during the one moment a conversation is least able to
absorb them. What is left is the person's, `forget` or `fork`, and both cost nothing. There is
deliberately no second stall mechanism either: a request that no longer fits is refused by the
provider, and `turn:{n}:refused:{i}` already says so in the same sentence-instead-of-a-spinner shape.

**Silent where the model has no reference record.** The `after_turn` payload carries `window` of
`null` for a model the database has never heard of, so there is no fraction and no way to know a
reserve was crossed.

**Where a conversation stands is the fill on every rule, and there is no mark on it.** The gauge says
how much of the window this request used, which is the console's own arithmetic and unaffected by any
of this. What is *gone* is the short bar at the fraction the reserve opened at: a reserve is a
plugin's own setting now, and [the console has no vocabulary for a plugin drawing in the transcript
region](../design/plugins.md#what-is-structurally-out-for-three-separate-reasons). That is the one
thing the port removed, and it is stated rather than quietly dropped.

**Fired at the boundary that crosses the reserve, not at the start of the next turn**, because the
conversation's prefix is warm right then and may not be when somebody comes back and types. The same
argument that makes a handoff cheap in-session makes it cheap here. The pass decides and the
composition root writes, which is [`Noting`](../design/plugins.md#the-effects): delivering a message
*queues* the session, so it is a fact about the queue in front of a pass rather than about answering
one.

**A turn the plugin opened itself never triggers another**, and that is the whole of what stops this
recursing. The reserve stays crossed for as long as the context is large, so without it the ask turn,
whose own context is the conversation it is summarising, would cross it again the instant it ended,
and so would every turn after that. The payload says what opened the turn and which plugin asked for
it, and the plugin's own qualified name is in the payload beside it, so the two are compared rather
than guessed at - which is also why another plugin's note does not stop it. A model that answers the
ask in prose instead of calling the tool is therefore not asked again until a person says something,
which is a retry per human action rather than one per turn: the rule a refusal already follows.

**The settings are read once at the top of a pass**, which is [the console's rule for every
plugin's](../design/plugins.md#settings-are-already-a-place-so-state-is-a-setting-with-nothing-in-front-of-it):
a switch flicked while a turn was in flight would otherwise have that turn answered under one answer
and judged under another. What it costs is that a change takes effect on the next pass, which is
the next turn.

**The window is asked for at the boundary rather than at the top of the pass**, because the
reference under it is reloadable configuration exactly as the rates are. `Prices.facts` is that one
lookup, and `pricer` reads it too: what a turn is priced by and how big its window is are the same
record read for two fields, which is `facts_of`'s own argument said one layer in.

**A fork starts on the declared defaults rather than inheriting what its parent was set to.** A
reserve is a decision about how much room one conversation's context has left, and a branch's context
is not that conversation's, so carrying the number across by *default* would be carrying an answer to
a question the branch has not been asked.

**Safe to default on, and only here.** Every other harness defaults its compaction on as a bet that
the summary is good enough, because what the summary replaces is gone. A handoff replaces nothing:
it is an append, the whole conversation stays in the transcript, it still counts toward what the
session cost, it still comes across on a fork, and forking above the boundary carries the entire
backlog into a session whose context holds all of it. The worst a wrong default costs is one turn
nobody asked for.
