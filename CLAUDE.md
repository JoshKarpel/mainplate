# CLAUDE.md

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows.
[`README.md`](README.md) is what it does and why; this is what to know before changing it.

## Commands

```console
$ just setup            # uv sync + install pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_console.py::TestTheConsole  # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve            # foreground, on port 8101 so it never fights the default
$ just demo             # the same, against Pydantic AI's canned model: no provider, no spend
```

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

## The key scheme

Three keys per turn, written by three different places and read by two:

```text
turn:{n}:prompt      the person's message; written from outside a pass, by `Service.say`
turn:{n}:model:{i}   the i-th model response of that turn; written by `StepwiseDurability`
turn:{n}:messages    what the agent run produced; written by the conversation body
```

They all live in `conversation.py` so that the code writing them and the two functions reading
them (`reached` for the body, `transcript` for the page) cannot drift apart. Changing one means
changing all four sites in that file, and the tests in `test_conversation.py` assert the shape
against literal recorded values rather than round-tripping through the writer.

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

## Dependencies

Built on [`without`](https://without.help), a workspace of small sans-IO libraries, and the
sibling checkout at `../without` is where its source is. Read the installed packages in `.venv`
rather than guessing at an API from memory; the same goes for `pydantic_ai`, which moves fast.

`uv` resolution has a 7-day cooldown (`exclude-newer`), with the `without-*` packages exempted in
`[tool.uv.exclude-newer-package]`. Adding a `without` package means adding it to that exemption
list too, or the whole graph gets held back to a release predating it. The cooldown is also why
`pydantic-ai-slim` is floored a release or two behind its latest.

`pydantic-ai-slim[anthropic]` rather than `pydantic-ai`, which pulls every provider SDK, a CLI, an
MCP server, and an evals framework. Another provider is an extra in that list, not a code change.

## The console

htmx **4**, vendored at `assets/htmx.min.js`, which reads very differently from htmx 2: explicit
`:inherited`, lowercase colon-separated event names (`hx-on:htmx:after:swap`), `hx-status:` in
place of `responseHandling`, and every status swapping except `204` and `304`. That last one is
why the poll carries `hx-status:4xx="swap:none"`: a refusal swapped into the region would replace
the conversation *and* take away the trigger that would have recovered it.

Pages are `without-html` node trees, pure functions of already-answered questions. A page and the
fragment inside it are the same function called at two depths, which is what stops the two
renderings from disagreeing.

Tests drive the app through `without-http`'s in-memory loopback client (`tests/calling.py`), so
nothing binds a port and the suite parallelizes. The `app` fixture deliberately runs the console
over a store with **no worker**, so a test asserting on a pending turn cannot race one; what the
worker does is tested in `test_conversation.py`, a pass at a time.
