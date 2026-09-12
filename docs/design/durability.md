# Durability

How a turn is recorded step by step so a crash resumes rather than restarts, and what one pass of a
session actually does.

## The capability

`StepwiseDurability` is a Pydantic AI capability on the *public* extension surface,
`AbstractCapability` plus `WrapperModel`. `pydantic_ai.durable_exec._base` and `_utils` are what the
bundled Temporal/DBOS/Prefect capabilities share, and their module docstring reserves them; almost
everything in them is about crossing a serialization boundary that does not exist here, since
`without-durability` runs the body in this process. Do not reach for them.

The capability finds its checkpoint through a `ContextVar` set by `stepping(run, prefix,
workspace)`, because no Pydantic AI hook carries one. Outside such a block it is transparent, which
is what keeps a durable-capable agent usable in a script or a test.

It wraps two things, `wrap_model_request` and `wrap_tool_execute`, and the second is required rather
than an optimisation. A tool that *reads* answers differently every time it is asked, so a pass that
re-ran one would resume the conversation against a file that moved since the model was told what it
said. A tool that *writes* has already written, and re-running it here fails against anchors its own
first run invalidated, which is a refusal for an edit that actually succeeded.

Two rules the mechanism asks for, both easy to break silently:

- **Effects live in steps; the code around them is pure.** A pass re-runs the body from the top, so
  anything between steps runs again. Nothing enforces this.
- **A step's key must be stable across passes.** `Stepping.key` numbers positionally within a turn,
  so a pass that issues its model requests in a different order finds the wrong records. Anything
  whose order is *not* fixed must use `Stepping.identified` instead; see [the key
  scheme](checkpoints.md#the-key-scheme).

`Run.step` records what the *codec* takes, which is stdlib `json`: a value has to be JSON-native
going in, and comes back as an `object` needing a `Parse` on the way out. That is what
`Record.recorded` is, so every step here pairs one with a matching parser, on the pass that ran it
as much as on the one that resumed. A tool return goes through `to_jsonable_python` and comes back
unnarrowed *inside* its record, because a toolset is unrelated functions with unrelated return types
and there is no one type to validate against; both passes see the round trip, so they agree.

This is `step` and not `transact`, so a tool is **at-least-once**: a crash between the tool returning
and the record landing re-runs it next pass. That window is one store round trip, and [anchored
editing](tools.md#a-line-is-addressed-by-a-hash-of-its-own-content) is what makes the failure mild
rather than corrupting, since an edit whose anchors no longer resolve is refused rather than applied
somewhere wrong. An arbitrary shell command has no such defence and re-runs silently.

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

Five things there are decided rather than incidental:

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
  is ever revisited: `supply` keeps the first value, so an `UNFINISHED` under `turn:{n}:model:{i}`
  is permanent and the turn can never be retried. `Commands` writes one from `aclose` and that
  precedent does not transfer, because a command's result is terminal where a request's is not.
- **The allowance is the pass's, not the turn's.** A pass that finds two prompts already recorded
  answers two turns, and a fresh count per turn would let it make one live request for each under a
  lease sized for one. So `conversing` makes one `Allowance` per pass and hands the same one to
  every `stepping` scope in it.

## Carrying the turn on, and stopping

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
to make the session ready again, and `Stalled` asks for nothing, because the pass that followed
would put an identical question to the provider and get an identical answer. `Ended` is the union,
named for the pass rather than `Outcome`, which already means how a tool call went here and what the
mechanism made of a pass in `without-durability`.

**`Archived` asks for the same nothing for a third reason**: the session was closed, so a message
queued before the press is passed over, and the pass records and plants nothing, since what it would
plant is what [the reconciler](workspace.md#archiving) is taking off the disk. Its own arm so the
log says so rather than reporting a stall on a session somebody deliberately ended.

**`Unconfirmed` asks for the same nothing and means the opposite**, which is why it is a fourth arm
and not a second reading of the third. A pass that reaches a session still on its
[settings step](plugins.md#setup) stops having recorded nothing at all, so there is no refusal to
read and nothing is wrong. Somebody has simply not pressed the button yet, and the press is what
queues the pass that goes on. Told apart because the worker logs what it is handed, and every fork's
first pass ends here, so folding the two together would report a fault at the one moment the console
is working as designed.

**The bug it closes is invisible rather than loud.** `without-durability`'s worker leaves a delivery
unanswered when a pass raises, deliberately, since it cannot tell a workflow's own failure from a
store that was briefly unreachable, and its own docstring names the cost: a workflow that fails on
every pass is retried once per lease for as long as it keeps failing, and the count belongs in the
checkpoint. So a refusal is written to the checkpoint, once, and the loop stops there.

**Terminal is a 4xx and transient is everything else**, which `terminally` decides. A 4xx is the
provider saying the request itself is wrong, a prompt over the window, a model it will not route, a
body it will not parse, and no amount of asking again fixes any of those; the exceptions are the 4xx
codes that describe the moment rather than the request (`408`, `409`, `425`, `429`), and a
redelivery is exactly what each asks for. **The default is transient**, which is the safe way round:
read as terminal, a transient error stalls a session that would have recovered on its own, where the
other way costs a redelivery per lease until somebody looks.

**`turn:{n}:refused:{i}` is named after the request rather than the turn**, and it is a settled
value in a write-once store for a reason worth keeping: what a request is made of is the recorded
history and the recorded message, neither of which will ever change, so a turn refused at request
`i` is refused at request `i` on every later pass. Sharing the index with `turn:{n}:model:{i}` is
the point, since the two are the question and the reason there is no answer and exactly one of them
exists. `CheckpointedModel.request` reads it *before* the allowance and the snapshot, so a pass
spends nothing on a question already answered and captures no tree in front of a request nobody
makes.

**A person can still ask again, and that is the point rather than a gap.** Writing a message queues
the session, so a refusal costs one attempt per human action rather than one per lease, and that
attempt is free, since the recorded refusal answers it without reaching a provider. What gets a
conversation *past* a refused turn is `fork` at it, which drops the turn's own requests while
keeping everything under them, and the sentence on the page says so. `refusal_in` is what the page
reads, of the turn being answered and no other: a refusal on a turn that later answered is history,
and the transcript is where history goes.

## A pass that falls over

**A refusal is settled and a failure is not, so the two are recorded differently and read
differently.** The provider turning a request down is permanent: the recorded history and the
recorded message are what they are, so the pass reports `Stalled` and nothing wakes the session
again. A pass that *raises* is a different animal - a plugin that exited non-zero, a tool that threw,
a store that blinked, a bug - and every one of those is something somebody can fix, after which the
redelivery the worker was already going to make resumes the session from the step it stopped at. So
`reporting` records the reason and **re-raises**, which changes no control flow at all and adds a
sentence the page can draw.

**What it closes is a session stuck with nothing saying so.** Before it, the whole account of a
broken pass was `logger.warning` inside the worker: the page drew the same three dots it draws for a
reply being written, and the two were indistinguishable for as long as the fault lasted. The bug that
produced this was exactly that - a bundled plugin raising on every `before_request`, a session
retried once per lease for two days, and nothing anywhere on screen.

It catches `Exception` and not `BaseException`, which is what `without-durability`'s own hierarchy
asks for: a suspension and a lost claim are `Interruption`s precisely so that a driver's `except
Exception` cannot absorb one, and neither is a failure. It sits **outside** `readying` rather than
inside `conversing`, so what it covers is everything a pass can raise rather than everything the
conversation can, the queue calls included. And a store that will not take the record leaves the
original exception the only one worth raising: the write is the diagnostic and the exception is the
fault.

**It does not promise the retry will work, and the page says so.** Most of what lands here is fixable
and the next pass carries on; some of it is not, because what a pass replays is *recorded*. A model
response the agent graph will not accept - a thinking-only response cut off by the output limit, say
- is recorded before the graph ever sees it, so every later pass replays the same record, raises the
same exception, and never reaches a provider at all. Nothing in `reporting` can tell those apart, so
the sentence says what the mechanism does and names the way out of the second, which is `fork` at
that turn.

## What the worker is doing about a session

**Live control-plane state, read on every render, and emphatically not in the checkpoint.** Whether a
pass holds a session and when the next delivery is due are true at the instant they are read and
change several times per pass; a checkpoint holds what was said and never changes. `Service.attended`
reads the claim and the queue beside the record count, and `attention_of` turns the three into one of
four: `Claimed`, `Queued`, `Delayed`, `Idle`.

The claim settles it first, because a live claim *is* a pass in flight and the queue row beside one
is only the delivery that pass is answering for. A claim outliving the process that took it reads as
held until its lease elapses, which is honest rather than wrong: for that interval the claim is what
stops another worker starting, and `reclaim` is what ends it.

**`Delayed` with a recorded failure is the signature of the whole problem.** The worker deliberately
leaves a failed pass's delivery unanswered, so the queue keeps the row it reserved and reclaims it
once the lease elapses. Read on its own that state is ordinary - the queue reserves a row a store
round trip before the claim lands, so every healthy pass passes through it - which is why the page
speaks on the *failure* and uses the delivery only to say when the next attempt is due. `Idle` with
something outstanding is the exception and needs no failure beside it: a message and the row that
queues it are written in one commit, and a pass asks for the next one from inside itself, so there is
no race that produces one.

**It is in `Service.token` as well, and that is what makes any of it visible.** A pass that falls over
records nothing, so a token made of the record count alone holds still while the page sits under a
spinner - the failure this exists to end. The claim and the delivery are what move when the worker
picks a session up, lets it go, and schedules the next attempt. Two consequences: the token is no
longer monotone, which is fine because the stream compares it for inequality and nothing reads it as
a position; and the two moments go in **as the store wrote them** and never as durations, since a
duration shrinks between two polls with nothing having happened and a token that differs from itself
is a page that re-renders for ever.

The cost, stated: this is the one place in the console that knows
`without-durability-sqlite`'s schema rather than its interface, and it is now three tables rather
than one. All three belong upstream - the count as a method on the checkpointer, the claim and the
delivery as a status read - and until they are there, renaming any of those tables is a change that
has to be made in `Service.attended` too. One query behind one method, so there is one place to
change.

## What replay costs

**Measured rather than reasoned about**, and it is not where it looks. Each pass re-runs `converse`
from the top, so a turn of *n* requests replays O(n²) steps; record parsing is 1.4% of a 40-round
turn and `load` is 0.8% to 2.4% against real SQLite. The dominant term is Pydantic AI rebuilding its
frozen `RunContext` once per capability per hook, which is upstream's. Absolute figures: about 65ms
per pass, 2.2s spread across a 40-round turn that costs minutes of provider time.

**Do not build a record cache or a fetch-only-what-is-missing store for this**: loads are already
linear and parsing is 1.4%, so the quadratic is somewhere a store-level cache cannot reach, and
raising the allowance cuts the pass count, the graph replay and the re-loading together. Revisit
only if very large `read` returns become common.

**The tests default to unbounded and the console ships one.** A test about a conversation drives a
whole turn in one pass and says nothing about how a pass is cut; `TestWhatOnePassDoes` is where the
two are pinned against each other, and what it asserts is that the allowance decides how much one
pass does and *nothing* about what the conversation comes to.
