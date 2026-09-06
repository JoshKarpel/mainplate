# The philosophy of mainplate

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows. This is
the standard new work here is measured against: one idea about where a conversation lives, a
vocabulary for naming things, and the handful of rules that keep being the answer in a design
argument.

It is not the authority on what the code currently does. The [design
notes](https://joshkarpel.github.io/mainplate/design/) are, and where one of them says this console
does not yet meet something written here, believe the design note and say so.

## The one idea

**The checkpoint is the conversation.** There is no messages table, no session state in the server,
and no cache. A page renders `checkpointer.load(session)`, a crash resumes from the same rows, and
two tabs agree because they are reading the same thing.

Anything that would keep a second copy of what was said is the change to push back on. That is the
whole test, and it is worth applying in the narrow form rather than the broad one: the rule is
against a copy that has to be **kept in step with something that changes**. A copy of something
already settled is two values that happened to be equal, and those are cheap.

Five things look like exceptions and are not. Each is worth knowing, because a sixth will be
proposed and the argument for it has to look like one of these.

### The session index is one row, and it reaches rather than copies

`sessions.py` holds one row per session, because `without-durability` cannot enumerate workflows. It
holds a title as well as an id, and that is *not* a copy of changing state, because a session is
named after its first message and nothing ever renames it.

What the index does not hold, it **reaches for**. A checkpoint is a row per key rather than one
value, and both tables are in the one file, so `SELECTION` reads a session's repository straight out
of its `choice` with a `LEFT JOIN` and `json_extract`: one small row per session, and no word of any
conversation. That is the shape any further "what is this session on" question should take. A column
would be the second copy this console is built to avoid, and unlike the title it would be a copy of
something recorded elsewhere and already authoritative.

The two columns that *are* there for presentation earn it by being facts nothing else records:
`Origin.aside` is what somebody meant by a fork, and `Tending` is what is being done to a running
session. Neither is written anywhere else, so neither is a copy.

### The catalogue is configuration that lives at the far end of a request

`catalogue.py` is the one piece of process state that genuinely changes under a reader, and nothing
in it is anything anybody said. It is configuration that happens to live behind an HTTP request
rather than on disk, so it is handled the way reloadable configuration is. The test for a change
there is the same one: does it keep a second copy of what was *said*?

### `localStorage` holds what a reader decided

The theme, which kinds are muted, and what they have written and not sent. Never a word of the
conversation, since an unsent draft is not one until it is sent, so a browser with it wiped renders
exactly what one without it does.

The theme is the reader's across every session; the other two are facts about one conversation, so
they are keyed by session id. That scoping is load-bearing rather than tidy: every session shares
one origin, so an unscoped key would be one conversation's decisions imposed on all of them.

Two things the script holds are deliberately *not* stored, and the line between them is worth
keeping. What a reader has folded, and whether they are following the end, are modes within a visit
rather than decisions about a conversation: unfolding a call is how you read one answer, and
following is a mode you fall out of by scrolling up and back into by scrolling down. Carried across
a reload either would be a page that opens somewhere the reader has to notice and undo. Both survive
every swap, which is what they actually have to do.

### A fork copies what was said, and the turns it copies are settled

The rule is against a copy that has to be kept in step; a fork copies turns that are already settled
and that nothing will ever rewrite, so the two sessions are two values that happened to be equal
rather than two views of one thing. It is also what keeps a fork readable on its own, since each
session's checkpoint stays the whole of its own conversation with nothing to dereference.

### What a turn cost and how long it took are recorded, not looked up

The same bargain as the fork's, and the argument runs the same way: what a request cost and what it
took are settled the moment it is answered, and nothing will ever rewrite either. Re-derived on each
render from a reference database that has since moved, the same turn would show a different number
next month and two sessions would stop being comparable.

Neither is authoritative. No wire reports what it actually charged, so a recorded cost is an
estimate made immutable rather than a bill, and the page says so. Neither is ever overwritten, which
is how Pydantic AI's own filling is written too: the day a provider reports what it took, its answer
wins over any estimate of it.

## The words

**One name per thing, and the same name in the code and on the page.** A reader who learns a word
from a control should find that word in the identifier behind it, and somebody reading the source
should not have to work out that two names are one operation. This console keeps being tempted the
other way, because a second word always feels like it is adding a distinction; usually it is adding
a synonym, and a synonym is a thing to keep in step for ever.

**The test is whether a second *thing* exists, not whether a second word reads well.** Two controls
that call the same function with different arguments are one thing with two labels: the composer's
fork and a rule's fork are both `Service.fork`, differing in `at`, so they are both called `fork`.
Where the distinction is real the words stay apart, and `endpoint`, `wire` and `provider` are the
worked example: one is a line in `config.yaml`, one is a built object that speaks an API format, and
one is whoever made a model. Three things, three words, none of them interchangeable.

**Say what it is, not what is comfortable.** A tool that will not do something `refuses`; a session
nobody can answer is `stalled`; a model with no price says `no reference record`. None of those are
softened into "unavailable", "issue" or "not supported", because the reader's next question is
always *what happened*, and a euphemism makes them ask it. The same goes for what a control does:
`keep` takes text off the page, `drop` deletes it, and neither is called "manage".

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
otherwise is wrong about the mechanism as well as grandiose: what happens is that a recorded number
is crossed and a message is delivered. So the auto-handoff switch says `auto at reserve` and not `by
itself`, which reads as the console choosing or, worse, since it is the party that *does* choose
things here, as the model having decided to wrap up. The trigger is also the more useful half, since
a reader who knows what fires it knows what to change.

**Prose may inflect where a control may not.** English makes a noun of an act, so a fork produces a
branch and a session forked at turn three has a branch point. That is ordinary writing and not a
second term. What must not vary is the label on a button, the name of an identifier, and the word a
doc reaches for when it means the operation.

**Choose the word before the value is durable.** A term that has only ever been in a label costs a
rename; one that has been written into a checkpoint costs a migration, because the *value* is what
is in the store. See `Filesystem.WORKTREE`, which was `WORKSPACE`, and took every session written
until then with it. So a `Disposition` is free to be renamed and a `Choice` field is not, which is
worth knowing at the moment the word is picked rather than afterwards.

## Refusing at startup, or promising not to raise

Two opposite stances, and which one a component takes is decided by one question: **can a failure
here leave somebody holding a choice they cannot use?**

- **`catalogue.discover` refuses.** An endpoint that cannot say what it serves is a lifespan that
  raises and a service that never takes traffic, and `build_wires` is eager for the same reason. An
  endpoint you can select and then cannot answer on is worse than a console that will not start
  naming the endpoint that is wrong.
- **`forge.offers` promises not to raise.** A forge describes an environment this process merely
  happens to be in, so "not on exe.dev" and "nothing attached" are ordinary answers. One forge's
  mistake is logged and the others carry on, because a machine with no repositories attached simply
  has none.

Everything else picks the arm this question puts it in. The reference database takes `offers`'s
promise, because a reference that will not load costs a card its numbers and nothing else. A missing
`bwrap` takes it too: a session keeps every file tool and is offered no `bash`, which is exactly
what this console was before there was one. `Clones.branches` takes it, so an unreachable host costs
a suggestion rather than an ability.

**A promise not to raise is not a licence to be quiet.** A `bash` that is not there is logged at
startup, because a shell tool that silently does not exist is the state nobody can diagnose.

## Configuration that changes under a reader

The catalogue and the reference database are both read across the network and both change while the
process runs. Neither is an exception to the one idea, and both are handled the same way:

- **Read before ready, refreshed off the request path.** Loaded during the lifespan, before the
  store is opened, and re-asked afterwards by a background task on a timer. A page render reads the
  value out of memory and never causes a request to a gateway.
- **Swapped whole, never edited.** The holder is rebound to a new value, so a reader that grabbed
  one holds a consistent answer even if a newer one lands mid-render.
- **A failed refresh keeps the last good value, and there is no staleness bound.** That is
  deliberate rather than an omission: the bound would have to be invented, and emptying the picker
  because a gateway was unreachable for an hour is worse than the staleness it would prevent.

## A page is a pure function of already-answered questions

Rendering asks nothing. Every question a page needs answered is answered before the render begins,
which is what makes the whole gallery possible: `scripts/gallery.py` answers them with fixtures and
gets exactly what the console draws, with no server, no database, no provider and no `config.yaml`.

`Conversation.since` is the case that shows where the line is. What the composer needs is how long
ago the last response landed, and `now()` is not an already-answered question, so it is measured in
`Service.read` and handed to the page rather than taken during the render.

## One fact in two places

Sometimes a value genuinely has to be written twice in two forms that nothing can unify: `tree_key`
and `Stepping.key` build one string from opposite ends, and `RETENTION` and `CACHE_FOR` are one
duration as a `timedelta` and as the string literal an SDK's type demands.

That is a bargain rather than a mistake, and it is paid the same way each time. **Name it where both
halves are written, and give it a test that turns a drift into a failure** rather than into a
console confidently calling a dead prefix warm. What must not happen is the third place: where a
vocabulary can be shared, share it, which is what `StepKind` does for the key builders and the
record discriminators alike.

## Controls

**If a control here starts needing to be kept in step with another control, that is the signal the
two are one question.** The repository and the filesystem isolation level were two card groups, with
one drawn greyed until the other was picked, and keeping them in step wanted a swap to refresh the
greying, a fix so the narrowing box would not check a disabled card, and a card in the completion
list that could not be chosen: three pieces of machinery for one answer stored in two places. They
are one group now.

**A control that toggles may not move.** Whatever a disclosure opens, the thing that opened it stays
exactly where it was. A control that moves under the finger that pressed it cannot be pressed twice,
and the page reads as having jumped rather than as something having opened.

**A control says what it does, and never remembers what it did last.** A split button that
remembered its last choice would be a button labelled `Send` that forks, which is the one failure a
control like that can have that nobody notices until after it has happened.

**Everything the script does is an enhancement.** With `assets/mainplate.js` absent the page still
renders, still posts, and still folds. The controls that genuinely need the file say so, and the
shelf is the honest example: it lives in `localStorage`, so its card shows nothing without the
script, and that is its standing bargain rather than a new exception.

## Say what it costs

A design note here states what a choice is bad at, in the sentence that makes the choice. "The cost,
stated" appears throughout the design notes and is not a tic: if you cannot name what an option
costs, you have not made a decision, you have expressed a preference.

Two consequences worth writing down:

- **Name the limits in the main text.** What was tried and dropped, what is not covered, what is
  known to be wrong. `bash` reaching around the file tools' lock, the at-least-once window on a tool
  call, one accumulating branch per session with nothing pruning them: all of those are stated where
  the mechanism is described, not in a footnote.
- **A rejected design is worth recording only while it is still tempting.** The reason a note says
  what was tried is that the alternative reads better than it works, and the next person will reach
  for it. When it stops being tempting, the note goes.

## Where the rest of it lives

The [design notes](https://joshkarpel.github.io/mainplate/design/) carry how each part works and why
it is built that way, and they are the authority on the code as it stands. What the console does and
how to run it is the [README](https://github.com/JoshKarpel/mainplate/blob/main/README.md).
