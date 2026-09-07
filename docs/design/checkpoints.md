# Checkpoints

The checkpoint is the conversation, so this is the page every other one reads or writes something
described on. It covers the two key spaces, what is recorded under each key, and what a value under
one is allowed to be.

## The key scheme

**Two key spaces, and the store owns one of them.** What a person puts into a session goes into its
**inbox**, under a key the store mints; what a pass records about a turn goes under a key this
console names.

The store's space is one key:

| Key | Holds | Written by |
|---|---|---|
| `inbox:{n}` | A message or a command, filed in the order it arrived | `Service.say`, `Service.send`, `Service.run` and a plugin's `deliver` from outside a pass, and a plugin's tool from inside one |

This console's is the rest:

| Key | Holds | Written by |
|---|---|---|
| `choice` | The endpoint, model, repository, base, branch, isolation and thinking level | `Service.start` and `Service.fork`, before the first message |
| `result:{entry}` | What the command delivered under `{entry}` exited with, said and took | `Commands`, when it finishes |
| `instructions:{n}` | What the stretch of context beginning at turn `n` is answered under, exactly as the model is sent it | The first pass to reach it, before its first request, and replayed by every later one |
| `turn:{n}:opened` | The entry this turn took | `Run.receive`, in the conversation body |
| `turn:{n}:tree:{i}` | The worktree before the i-th model request | `StepwiseDurability` |
| `turn:{n}:heard:{i}` | How far down the inbox the turn had read when it made that request | `Run.pending`, through `StepwiseDurability` |
| `turn:{n}:model:{i}` | The i-th model response of that turn | `StepwiseDurability` |
| `turn:{n}:refused:{i}` | Why the i-th request will never be accepted, where one never was. Exclusive with `model:{i}` | `StepwiseDurability` |
| `turn:{n}:tool:{id}` | What one tool call returned and how long it ran | `StepwiseDurability` |
| `turn:{n}:messages` | What the agent run produced | The conversation body |

**Nothing allocates a number by trying any more, and no key is contended.** A message used to name
the turn it was going into, so writing one meant deciding which turn that was against a checkpoint
that had already moved, and a steer meant claiming a numbered slot the pass was competing for. The
store names an entry, so three writers posting at once are three entries, and *which turn takes one*
is decided later by the pass that reads it, which is the only party reading at the moment the answer
is true.

**An entry says nothing about which turn it belongs to, and that is the price.** What decides is
`turn:{n}:opened`: a turn owns everything from its own entry up to the next turn's. Every reader
goes through `held_in` for that, `before` needs a second rule for [the fork](forking.md), and
`Service.run` no longer has to work out which turn a command is in.

**What it buys is a command drawn where it was run.** The store files everything in the order it
arrived, so counting a turn's model records ahead of a command's entry says how far the reply had
got when somebody typed it. Nothing had to be written at the time and nothing raced the pass for a
position in its sequence; `ran_in` reads it, and `alongside` puts the panel back there. Collected at
the end of the turn, as they were, a command sank down the page as each later answer landed above
it.

**`command` is recorded and not told**, which is the whole of what a command is here, and the split
it rests on is one this console already makes everywhere: whether something is *in the checkpoint*
and whether it is *in the message history* are two questions, and `tree:{i}` and `heard:{i}` are
both records the page draws and no model ever sees. So a command renders, survives a reload and
comes across on a fork, and costs the conversation no context and reaches no provider. Telling the
model what you ran is a message somebody writes, which is what the box above it is for. A pass
draining its inbox passes over one rather than reading it.

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
form and `Stepping.identified` is the other, and both take a `StepKind` rather than a bare string,
so the word a key is built from is the word the record under it tags itself with.

**`turn:{n}:late:{k}` is the key the inbox deleted**, and it went because the thing it answered
cannot happen any more. It recorded what was found at the boundary where a run would otherwise have
ended, so a message arriving during the last response could redirect the run into one more request
rather than reaching nobody. A pass reads the snapshot it loaded on the way in, so nothing arrives
*during* one: the drain before the first request already sees everything this pass ever will, and a
message delivered afterwards is read by the next pass, which opens the next turn on it. What used to
cost the ending turn a round trip nobody asked for now opens the turn after it.

**A model request's duration is not a field**, because a `ModelResponse` has a `metadata` dict
Pydantic AI keeps for the application and does not send to the model: `Stepping.stamp` writes it
there and it rides into `turn:{n}:model:{i}` and `turn:{n}:messages` alike, which is what keeps the
two readings of a turn agreeing without either being taught where to look. A tool call has no such
slot in somebody else's value, so its duration is a field on the record around it. One word, `TOOK`,
in both places; two places because the values are two different kinds of thing rather than for
symmetry's sake.

`opening_tree_key(n)` is `turn:{n}:tree:0`, and it is what two things mean by "this turn's tree": a
fork plants its worktree at it, and the rule opening the turn shows it. Both want the state before
the turn did anything.

`instructions:{n}` is the one key here that is neither turn-prefixed nor named after an entry, and
both halves of that are decided. Not turn-prefixed, because `before` copies those by shape and a
fork that attached a repository its parent never had would inherit instructions with no guidance in
them. Not session-level, because a forget ends a stretch of context and composing again there is
free: the prefix it would have invalidated has just been thrown away.

The four `plugins:…` keys are session-level for the same reason `instructions:{n}` is not
turn-prefixed, and they come in two pairs one moment apart. `plugins:declared:console` and
`plugins:declared:repository` hold what each tier's files *name*, written by the session's first pass
and read by [the settings step](plugins.md#starting-a-session-takes-three-steps);
`plugins:console` and `plugins:repository` hold what those plugins said when they were *run*, written
by the request that answers that step. The console half of each pair is re-read by a fork and the
repository half is inherited, because a fork's tree is one a model has been editing.

`choice` goes in before the first message and never again *within a session*. The order is
load-bearing: the message is what *queues* a session, so writing it first would let a worker take
the session and find no endpoint to answer on. Never again, because a session that changed endpoint
halfway would replay recorded answers from one and continue on another. Forking is how the choice
changes, and it changes it by making a different session rather than by rewriting this one.

`turn_of` is the inverse of `turn_prefix`, and it answers about the key's *shape* rather than
against a list of known kinds. That is what lets `before` carry a whole prefix of a conversation
into a fork without being taught each new kind of step: a `turn:3:approval:0` nobody has written yet
is turn 3 already, and `turn:3:tool:toolu_017` was too before anything read tool keys. `entry_of` is
the same move over the other key space, answering which entry a key is *about*: an entry is about
itself and a result is about the command it answers, so a fork carries what hangs off an entry
without being taught that either.

**The names are built in two places and have to agree.** `conversation.py` names them for the
readers (`opened_key`, `tree_key`, `opening_tree_key`, `messages_key`, `model_key`, `tool_key`, read
by `choice_of` and `reached` for the body, `transcript`, `so_far` and `responded` for the page,
`before` for a fork, `planting` for a fork's worktree). `Stepping` in `durability.py` builds them
for the writers, from a turn prefix and a kind, which is what lets one capability name a step
without importing the conversation. `tree_key(n, i)` and `Stepping.key("tree")` therefore produce
the same string from opposite ends, and nothing enforces that: change one and change the other. That
is [one fact in two places](../philosophy.md#one-fact-in-two-places), paid the usual way, and the
tests in `test_conversation.py` assert the shape against literal recorded values rather than
round-tripping through the writer, which is what turns a drift into a failure rather than a silently
unfindable record. What *is* shared is the word, since both take a `StepKind`, so a kind nobody has
declared is a type error rather than a key nothing reads.

**The two indexed kinds have a reader, and that is what draws a turn as it happens.** `responded`
walks `model:{i}` from zero and `so_far` looks each call's result up under `tool:{id}`, so the turn
being answered renders from the steps behind it rather than waiting for its `messages`. It is not a
second copy of anything: those records exist so that a resumed pass does not pay for the same
request twice, and this reads them.

The walk is its own function because a running turn is read *twice*, for what it has said and for
what it has spent, and `transcript` calls `responded` once and hands the result to both. Walked
separately the two would eventually disagree about how much of a turn there is, which on a page that
draws a turn as it fills in is a rule reporting one number against a conversation showing another.

## What a checkpoint value is

**Never a bare string, a bare number, a bare list, or a value this console does not own.** Every one
goes into a record from `records.py`, and the rule is about *shape* rather than about validation: a
bare value has nowhere to put a second field, so the day one needs one is a migration. A message
held a bare string until a turn needed to say what history it opens on, and paying for that once is
the argument for paying for it nowhere else.

**Three keys hold a cursor and are the exception, because their value is the store's.**
`turn:{n}:opened` and `turn:{n}:heard:{i}` are written by `Run.receive` and `Run.pending` rather
than by anything here, and what they hold is an inbox key: the shape argument does not reach them,
since there is no second field this console could ever want beside one. Wrapping them would mean not
using `receive`, and `receive` is the only thing that can suspend a pass on an inbox.

It covers the foreign values too. A `ModelResponse` and a tool's return are wrapped rather than
stored raw, and the envelope is honest about what it buys: it cannot protect against Pydantic AI
renaming a field *inside* a response, because nothing here could. What it is is the place a
`version` would go the day one is needed, so adding one then costs an optional field rather than a
shape change.

**`turn:{n}:took:{id}` is the key that stopped existing because of it**, which is the clearest case
for the whole policy: a duration could not sit beside a bare tool return without being
indistinguishable from a tool that returned a field of that name, so it needed a key, and the key
brought a window where a return was recorded and its duration was not. One record, one write, no
window, and `tooks_in` and `blocks_from` now read one mapping rather than being handed two.

**Every record carries its own `kind`**, which is a second copy of what its key already says, and
the copy is the point: parsed by key alone, a record written under the wrong one is silently
reinterpreted as whatever that key expects, where a tag makes it fail. It is safe from being a third
place to keep in step because `StepKind` is one vocabulary the key builders and the discriminators
both take.

**In the inbox it is not a second copy at all, and that is where it earns most.** The store names an
entry, so nothing in the key says whether what is in it is a message that must open a turn, one a
running turn may fold in, a message the console wrote itself, or a command no model will ever see.
`records.Delivered` is the union of the four and the tag is the whole of what tells them apart,
which is why a pass draining its queue can stop at a prompt and pass over a command.

**Which questions a reader asks of that tag are `records.opens` and `records.forgets`**, and they
are functions rather than an `isinstance` chain repeated at five call sites. `opens` is whether a
draining pass must stop here, which a `Prompt` and a `Handoff` answer alike; `forgets` is that and
the field together, since only a message a turn opens on can carry a boundary. `opens` is a `TypeIs`
so the union it names is written once and every caller that goes on to read `forget` is narrowed by
asking rather than by repeating it.

**Not to be confused with the panel `Kind`.** Both are called `kind` because it is a generic word
and each is unambiguous where it is used; they overlap on `command` and `tool` meaning different
things, they never mix, and mypy refuses the crossing since they are distinct unions. The *types*
take the prefix where both are in scope, which is `conversation.py`: `StepKind` beside the `StepKey`
that already existed, against the panel `Kind` that keeps the bare word.

**Unknown fields are ignored; an unknown kind is not.** The first is Pydantic's default said out
loud, and it is what lets a newer build's record survive being read by an older one after a
rollback. It is safe here for a reason specific to this store: nothing round-trips a record back
into it, since the checkpoint keeps the value a key was first given and `before` copies raw values
without parsing them, so a fork taken under the older build carries the newer record across intact.
An unknown *tag* is a hard parse failure, which is why readers parse by key, where the caller
already knows what it asked for, and `records.Step` is only for the places that take a bag: a dump,
an export, a migration. The HTTP boundary is the opposite case and stays that way, since an
unrecognised disposition is a refusal.

**Three shapes are deliberate exceptions.** `choice` is not a `records` model: it is already a
record this console owns and has grown fields twice with no migration, and its parser encodes things
a schema cannot say, defaulting an absent isolation from whether a repository was picked and
re-parsing a base and a branch that become `git` arguments. It carries the tag all the same.
`Result.status` is a `StrictInt`, because Pydantic reads `True` as `1` where it is not and `git diff
--quiet` exits 1 to mean there *are* changes. And `parse_tree` reads an absent key and a recorded
tree holding nothing as the same answer, since every caller reaches it through `recorded.get` and
both draw as no tree.

**A parser raises `ValidationError` rather than `TypeError`.** That is Pydantic's own error
surfacing rather than a hand-written one, and it is still loud, which is the property those parsers
were written for.

What holds the two readings together is that **`so_far` produces a prefix of what `blocks_of` will
produce once the turn lands**: the same responses, in the same order, cut by `blocks_in`, with the
results that have not arrived still out. That is why a panel never moves as a turn fills in, and why
the morph when `messages` finally lands touches nothing. `test_conversation.py` asserts the two
readings of a finished turn are equal, which is also what catches the subtle half of it: a tool
result's text has to be what `ToolReturnPart.model_response_str` produces, so `returned_step` uses
`pydantic_core.to_json` and not `json.dumps`, whose spacing differs on every structured return.
