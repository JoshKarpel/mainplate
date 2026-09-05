# Handoff: adopt `without` 0.0.7 in mainplate

Three stages. Stage 1 is mechanical and nearly done. Stage 2 restructures how much of a turn one
pass does, and its design is settled below along with the measurements behind it. Stage 3 is
independent of both.

## Already done

- `pyproject.toml`: the six `without-*` floors moved to `>=0.0.7`, with a comment on the
  durability pair saying why (the inbox, and the `seq` column `load` orders by).
- `uv sync --upgrade` run; `uv.lock` updated; 0.0.7 is installed.
- `mainplate.db*` and `mainplate-demo.db*` deleted. 0.0.7 changed the checkpoint schema and
  ships **no migration** (`migrate` is `CREATE TABLE IF NOT EXISTS`, so an old database
  keeps its shape and every `load` fails on the missing `seq`). Both were disposable: the
  first was empty and the second is rebuilt by `just seed`. **The demo database has not
  been re-seeded yet.**

## Stage 1 — finish the upgrade

`just test` fails with exactly two mypy errors, and nothing else is known broken:

```text
tests/test_conversation.py:34: Module "without_durability.stepwise" has no attribute "Waiting"
tests/test_fork.py:10:        Module "without_durability.stepwise" has no attribute "Waiting"
```

`Waiting` became `Blocked`, which reports **every** branch a pass stopped on rather than
one, in two sets:

- `waiting` are addresses, from `Run.awaiting`, answered with `arrive(workflow, key, value)`
- `listening` are read-step keys, from `Run.receive`, answered with `deliver(workflow, value)`

So `Waiting(key=prompt_key(0))` becomes a `Blocked` whose `waiting` holds that key and
whose `listening` is empty. Read the class in `.venv/.../without_durability/stepwise.py` for the
exact field types rather than guessing. Also update the return annotation at
`tests/test_conversation.py:114`.

Then:

1. `just seed` to rebuild the demo database.
2. `just test` to green.

Watch for one thing that has not been checked: 0.0.7 makes two `without-web` routes that
differ only in what they *name* a path parameter a build-time `duplicate route` error. If
mainplate has such a pair it will fail loudly at app construction, not silently.

Stage 1 is a complete, committable change on its own. Confirm green before starting stage 2.

## Stage 2 — one model request per pass

### The problem this solves

A pass is currently a whole turn, which couples the lease to how long a model takes to answer
a conversation rather than a request. `Settings.lease` is ten minutes for exactly that reason,
and that number is a bet on the longest turn anybody will ever ask for: a turn with enough
round trips to cross it is `Fenced` on its next write, redelivered, replayed, and does it
again. `app.py` already names the symptom as a cost ("a model call that hangs holds a pass for
the whole lease").

It is also what makes the inbox unusable. `Run.receive` and `Run.pending` read the pass's
snapshot, loaded once at the top, and the docstring's "the append made the workflow ready, so
the next pass sees it" is only wrong because a pass here lasts minutes. Today
`steers_waiting` works around that with a **live** `checkpointer.load` on every model request.

### The design

Keep the effect inside the pass, and cut the pass finer: one live model request per pass,
plus the tool batch that follows it.

- `wrap_model_request` counts the live (non-replayed) requests this pass has made. On reaching
  the allowance it raises a **mainplate-private** exception, which unwinds `agent.run`.
- `converse` catches that outside `agent.run` and returns, so its type becomes `Progress`
  rather than `Never`: `Progressed` when more is owed on this turn, and the driver re-readies
  the session at once. Waiting on a person is still `Blocked` on `prompt_key(n)`, unchanged.
- The exception is deliberately **not** a `Suspended`. Nothing is owed by the outside world, so
  there is no key to report and nothing for `arrive` to answer. It also restores `resume`'s
  `Swallowed` check as a live guard, which a `-> Never` body can never trigger: a body that
  returns is one whose caught-and-carried-on suspension can be refused.
- `Settings.lease` drops to cover one request and its tool batch rather than a conversation.
  Two minutes is the starting figure; a request that hangs longer than that is the HTTP
  client's timeout to own, not the lease's.

**The allowance is one setting with a live value, not a second code path.** `allowance=1` is
the default and an unbounded allowance is exactly today's behaviour, so the whole tradeoff is a
number. What it trades is replay CPU and lease duration against steer latency: within one pass
`before_model_request` reads one snapshot, so an allowance of *n* means a steer can wait up to
*n* requests. That coupling is the reason to keep the default at 1, and the reason
`steers_waiting`'s live read can go at 1 and not above it.

### Why the request stays inside the pass

The alternative considered and rejected was dispatching each request to a server-held pool and
suspending the pass on `Run.awaiting`, answered by `Durable.arrive`. It works (`without`
supports it natively and it was probed), and it is worse, because the lease is what recovers
interrupted work: a request outside the pass is a request outside the lease. A pass that
dispatched and reported `Blocked` has had its delivery acknowledged, and `InputNeeded`
schedules no wakeup, so a process that dies with work in flight leaves a session waiting
forever on an `arrive` that will never come.

Recovering that needs a reconciler over in-flight state, idempotent dispatch so a replaying
pass cannot pay a provider twice, and a durable leased in-flight marker the moment the worker
is not in the same process as the pool, which is a second queue. Keeping the request under the
claim deletes all of it: a dead process is an expired claim, `reclaim` redelivers, and the pass
replays. That is today's recovery mechanism, untouched.

One trap worth recording, in case the pool is ever revisited: **never write a placeholder
record for a model request.** `Commands` writes `UNFINISHED` from `aclose` and that precedent
does not transfer. A command result is terminal so a placeholder is honest; `supply` is
first-writer-wins, so an `UNFINISHED` under `turn:{n}:model:{i}` is permanent and the turn can
never be retried.

### Stage 2a: restructure the pass

Land this on its own, before the inbox.

- The private unwind exception, the allowance, and `converse` returning `Progress`.
- The driver re-readying on `Progressed`.
- `Settings.lease` down to the one-request figure.
- `steers_waiting`'s live `checkpointer.load` goes: at `allowance=1` every pass has a fresh
  snapshot, so reading `run.recorded` is correct.

### Stage 2b: adopt the inbox

- `Service.say`, `Service.steer` and `Service.run` stop allocating keys and become
  `Durable.deliver` appends. The two `for n in count():` claim-by-trying loops go.
- `converse`'s `run.awaiting(prompt_key(n))` becomes `run.receive(...)`, with `after`
  carrying the cursor (the caller threads it; it is not hidden state on the `Run`).
- `CLOSED`, `records.Closed` and `records.Said` are deleted. A queue cannot be permanently
  claimed, so the end-of-turn compare-and-set has nothing left to do: whether a message is a
  steer or a turn of its own is decided by which inbox cursor was recorded, not by a race
  against a marker.
- `heard:{i}` and `late:{k}` collapse into cursor records. Check whether both are still
  needed; they were split because two hand-rolled counters could not share one.
- The command panel ordering falls out. A command's place comes from where its entry sits
  among the model records in the ordered `load`, which is the bug that started this whole
  line of work (`ran_by` currently collapses a turn's commands into one panel at the end,
  so a command sinks as each response lands above it).

### Expect this to be mostly documentation

`CLAUDE.md` documents the key scheme, the steer mechanism, the record policy and the
disposition design at length, and stage 2 invalidates large parts of all four. Rewrite
them to describe what is, rather than appending notes about what changed, per the durable-docs
rule. `README.md` needs a pass too.

`scripts/seed.py` and `scripts/gallery.py` write checkpoint records directly, bypassing
`Service`, so both need updating for any record or key change, and the demo database
must be rebuilt (`rm mainplate-demo.db*` then `just seed`) or it serves the old shape.

## What replay costs, and why the allowance exists

Each pass re-runs `converse` from the top, so a turn of *n* requests replays O(n²) steps. This
was measured rather than reasoned about, and the cost is **not** in the store or the codec:

- Record parsing is 1.4% of a 40-round turn. 820 `ModelResponse` validations cost 48 ms.
- `load` is linear, one per pass, and 0.8% to 2.4% of the turn against real SQLite at 200-byte
  to 20 KB tool returns. It only reaches 10% at 200 KB per return.
- The dominant term is Pydantic AI rebuilding the frozen 39-field `RunContext` once per
  capability per hook (`_replace_capability_context` → `dataclasses.replace`). About 44
  rebuilds per model request with *no* capabilities attached, and a ~25% ceiling on a plain
  run's CPU.

Absolute figures, one request per pass over SQLite, 40-round turn: 2.17s at 200-byte returns,
2.62s at 20 KB, spread across a turn that costs minutes of provider time. Roughly 65 ms per
pass. Affordable at `allowance=1`, and the allowance is the dial if it ever is not:

```text
allowance= 1  passes= 40  1.54s
allowance= 2  passes= 20  0.83s
allowance= 4  passes= 10  0.44s
allowance= 8  passes=  5  0.24s
```

**Do not build a record cache or a fetch-only-what-is-missing store for this.** Both were
considered and the numbers rule them out: loads are already linear and parsing is 1.4%, so the
quadratic is somewhere a store-level cache cannot reach, and raising the allowance cuts the
pass count, the graph replay and the re-loading together. Revisit only if very large `read`
returns become common, and re-run the SQLite table with a 200 KB payload to decide.

The Pydantic AI overhead is a separate, upstream track: an issue write-up and a standalone
repro exist and should be filed rather than worked around here.

## Verified before committing to any of this

Probed against a `FunctionModel` so no provider was paid:

- Unwinding `agent.run` mid-run and replaying reproduces the checkpoint **exactly**. Same key
  set, same output, and a leaf-by-leaf diff finds only `ModelResponse.timestamp`, which differs
  between any two runs.
- Exactly one live model request per pass, with the tool batch riding inside the pass.
- Concurrent tool batches unwind and replay cleanly, four calls to a pass.

## Stage 3 — replace the CLI with `without-cli`

Independent of stages 1 and 2; do it separately.

`without-cli` is new in 0.0.7 and is meant to replace the `typer` dependency
(`pyproject.toml`, and `src/mainplate/main.py` plus whatever it imports). Read its
changelog entry: it is long and states the design directly, with tokens as values, `parse_argv`
as a total pure function returning `Bound | Answered | Rejected`, `Streams` injected so
output is asserted by passing `Streams.captured()` rather than by capturing a process, and
`run` as the only place `-h`/`--help`/`--version` are named.

Two things that are easy to miss:

- `without-cli` must be added to **both** the dependency list and
  `[tool.uv.exclude-newer-package]`. CLAUDE.md is explicit: a `without` package left out of
  the exemption list holds the whole graph back to a release predating it.
- `typer` comes out of `dependencies` in the same change, or the swap has bought nothing.

## Ground rules

- `just test` (mypy then pytest) or `just check` before calling anything done; CI runs the
  same pre-commit configuration.
- A real turn costs real money. Use `just seed` fixtures for anything downstream of a
  checkpoint, which is most of this.
- Do not create commits.
