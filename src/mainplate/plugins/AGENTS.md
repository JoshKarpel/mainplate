---
description: "What a change to the plugin protocol, the tiers or a bundled plugin must not break."
---

# The plugins

Why any of this is shaped the way it is is [Plugins](https://joshkarpel.github.io/mainplate/design/plugins/).
What follows is what must hold while editing here.

## The three modules and what each owns

- **`protocol.py`** is the vocabulary and nothing else: it imports nothing from `mainplate`, so a
  test can build a payload and read an answer with no subprocess, no session and no store anywhere
  near it. Keep it that way.
- **`installed.py`** is where a plugin comes from and what it is called. Tiers, qualified names,
  prefixing, collisions.
- **`running.py`** is the transport, and the only thing that knows a plugin is a process.
- **`asking.py`** is the runtime: what a session's plugins are, what they are asked, and what the
  console does with the answers.

`asking.py` must not import `agent.py`, because `agent.py` reaches this package for the toolset. That
is why `Declaring.runs` takes a repository and a boolean rather than a `Choice`.

## What must not change without deciding to

- **The vocabulary is closed and the console owns it.** A fifth effect, a seventh event or a third
  kind of control is something somebody adds deliberately, in `protocol.py`, with a line in the
  design note saying what it costs. It is not something a plugin can add.
- **`extra="forbid"` on everything crossing the boundary.** A plugin that wrote `tool` where the word
  is `tools` is told so, naming the plugin. That is the opposite of `records.Record`, and the
  difference is who wrote the value.
- **A tone is the one exception and is deliberately unvalidated.** An unknown tone draws the plain
  one, because a panel in the wrong ink is a session that renders and a refusal is a session that
  does not.
- **A repository's contributions are prefixed and no other tier's are.** Unprefixed, a repository
  could collide with one of yours deliberately, and a collision that refused would let a repository
  stop your session starting.
- **Every event is gated on what the plugin declared.** `wanting(event)` before asking, everywhere -
  including `compose` and `action`, which are fired from request handlers rather than from a pass.
  Without it a console with six plugins spawns six processes to tell five of them nothing.

## A bundled plugin runs under an interpreter this project does not pin

`#!/usr/bin/env python3` is whatever the machine has, **not** the interpreter `requires-python`
names. So a bundled plugin's syntax has to be older than everything else here, and
`[tool.ruff.per-file-target-version]` pins `bundled/*` to `py39`. That pin is doing two jobs at
once: it holds the *formatter* to that grammar, and it makes `ruff check` report anything newer as a
syntax error. Without it `except (OSError, UnicodeDecodeError)` is rewritten into PEP 758's
unparenthesized form, which is a `SyntaxError` on anything before 3.14 and therefore a plugin that
will not run at all.

**The linter is the guard, and a test cannot be.** This suite runs on the interpreter this project
pins, so a plugin using newer syntax imports and runs perfectly here; `compile(...,
_feature_version=...)` does not reject it either, since that flag gates only a handful of
constructs. What actually catches it is `ruff check` under the pin, so raising the floor is one edit
in `pyproject.toml` and this paragraph.

They are also *executables*: `pre-commit` identifies them as Python by shebang, where a path-based
tool sees no `.py` and skips them. That is why `just check` catches this and `ruff check src/` does
not, and why anything reaching for them wants the explicit paths rather than a glob.

## The bundled plugins are not privileged

`bundled/handoff` and `bundled/guidance` are executables this console speaks to over a pipe, exactly
as it speaks to anybody else's. **Nothing in them imports `mainplate`**, and that is the point rather
than an inconvenience: what ships is a worked example rather than a path no third-party plugin can
reach.

So a change to what a handoff says, what it costs, or when it fires is a change to a *script*, and
the console learns about it through `describe` like any other. The price is stated: `bundled/guidance`
carries a `description:` line reader rather than a YAML parser, because a plugin with no dependencies
is worth more here than the general case of a field nothing else reads.

Both are run for real in `tests/test_plugins.py`, over a real pipe, and both are exercised end to end
by `tests/test_app.py`, which is the one place `open_console` installs them.

## Two things not to undo

- **`describe` is once per session and its answer is recorded.** Tool definitions sit above the
  system prompt in the cached prefix, so a set that changed under a conversation would invalidate
  everything beneath it. That is why `instructions` is a `describe` contribution rather than an
  event, and it is why a forget no longer picks up a repository's edited guidance - forking does.
- **A repository's declaration is read from the commit the repository supplied, never from a
  snapshotted tree.** A snapshot is `git add -A`, so a `.mainplate/` file the model wrote on turn 4
  is *in* the tree recorded for turn 5, and a fork plants at a recorded tree. A fork therefore
  inherits `plugins:repository` whole and re-reads nothing.
