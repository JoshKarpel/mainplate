# CLAUDE.md

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows.
[`README.md`](README.md) is what it does and why; this is what to know before changing it.

## Commands

```console
$ just setup            # uv sync + install pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_console.py::TestTheConsole  # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve            # foreground, on port 8101 so it never fights the installed service
$ just demo             # the same, against Pydantic AI's canned model: no provider, no spend
$ just install          # this checkout as a user systemd unit, on the default port 8100
$ just logs             # journalctl --user -u mainplate -f
$ just uninstall        # removes the unit, keeps the environment file and the database
```

`just serve` and `just install` are on different ports on purpose, so a foreground run for a quick
look never takes down the service. `just install` runs `uv sync` first, and that is not a
convenience: the unit names this checkout's interpreter, so an install from a stale environment
points systemd at a venv missing whatever was just added.

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

One key for the session and three per turn, written by four different places and read by three:

```text
choice               the profile and model; written once by `Service.start`, before the prompt
turn:{n}:prompt      the person's message; written from outside a pass, by `Service.say`
turn:{n}:model:{i}   the i-th model response of that turn; written by `StepwiseDurability`
turn:{n}:messages    what the agent run produced; written by the conversation body
```

`choice` goes in before the first prompt and never again. The order is load-bearing: the prompt is
what *queues* a session, so writing it first would let a worker take the session and find no
profile to answer on. Never again, because a session that changed endpoint halfway would replay
recorded answers from one and continue on another.

They all live in `conversation.py` so that the code writing them and the three functions reading
them (`choice_of` and `reached` for the body, `transcript` for the page) cannot drift apart. The
tests in `test_conversation.py` assert the shape against literal recorded values rather than
round-tripping through the writer, so a change to the scheme has to be made in both places.

## Profiles and per-session auth

A **profile** is an endpoint and a credential; the model is separate, because a gateway serves many
models behind one hostname. A session records both and is bound to them for life.

`profiles.py` parses `config.toml` into `Config`, once, at startup. Two things there are easy to
undo by accident:

- **Credentials are `SecretStr` and come from the file, not the environment.** A key handed to
  `AnthropicProvider(api_key=...)` never becomes an environment variable. `Profile.key_for` is the
  one place that decides between a configured key, the `KEYLESS` placeholder for a gateway that
  authenticates at its edge, and `None`, which is what leaves the SDK reading `ANTHROPIC_API_KEY`
  for itself. Do not "simplify" that `None` away.
- **`build_agents` is eager**, so a profile that cannot be built fails at startup naming itself
  rather than on whichever session first chose it. That is also why the service refuses to start on
  an unusable `config.toml` instead of running and failing on the first message.

A pair the configuration no longer offers is not a retry: `Agents.for_choice` raises
`UnknownChoice`, and the console renders the session with a sentence naming the profile and no
poll, because a spinner that will never resolve is the one state a person cannot diagnose.

`exe.py` is the exe.dev half. A VM with the built-in LLM integration reaches the provider with no
credential at all, so `mainplate install` asks the reflection integration what is attached and
writes a keyless profile. Every failure there returns `()` rather than raising: "you are not on
exe.dev" must not be a failed install. Discovery is passed *into* `converge` rather than done
inside it, so the suite does not behave differently depending on which machine it runs on.

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

There is no `Protect*`/`ReadWritePaths` hardening, deliberately: this project's whole direction is
an agent that edits repositories, so a sandbox written for today's no-tools console would be wrong
at the first tool, and one loose enough to survive that protects nothing.

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
