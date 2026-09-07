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

**A PEP 723 block does not settle this, and that was checked rather than assumed:** ruff 0.16 ignores
`requires-python` in inline script metadata when it picks a target version. What the block *does*
settle is which interpreter actually runs the script, which is why `.mainplate/pre-commit` needs no
entry in that table - it says `uv run --script` and gets the Python this project pins. These two say
`python3` because they must run before `uv` is known to be anywhere on the machine.

They are also *executables*: `pre-commit` identifies them as Python by shebang, where a path-based
tool sees no `.py` and skips them. That is why `just check` catches this and `ruff check src/` does
not, and why anything reaching for them wants the explicit paths rather than a glob.

## The bundled plugins are not privileged

`bundled/handoff` and `bundled/guidance` are executables this console speaks to over a pipe, exactly
as it speaks to anybody else's. **Nothing in them imports `mainplate`**, and that is the point rather
than an inconvenience: what ships is a worked example rather than a path no third-party plugin can
reach.

So a change to what a handoff says, what it costs, or when it fires is a change to a *script*, and
the console learns about it through `setup` like any other. The price is stated: `bundled/guidance`
carries a `description:` line reader rather than a YAML parser, because a plugin with no dependencies
is worth more here than the general case of a field nothing else reads.

Both are run for real in `tests/test_plugins.py`, over a real pipe, and both are exercised end to end
by `tests/test_app.py`, which is the one place `open_console` installs them.

## Two things not to undo

- **Nothing runs a plugin before the settings step.** A session's first pass *declares*, which is
  reading files; the request that answers the step records the switches and asks for a pass; and that
  pass runs `setup` over exactly the plugins left switched on. Anything that would spawn one earlier,
  or spawn one somebody switched off, is the change to push back on: this is a trust boundary and not
  a loading order.
- **`setup` is once per session and its answer is recorded.** Tool definitions sit above the
  system prompt in the cached prefix, so a set that changed under a conversation would invalidate
  everything beneath it. That is why `instructions` is a `setup` contribution rather than an
  event, and it is why a forget no longer picks up a repository's edited guidance - forking does.
- **`setup` is the only event with a network, and it must stay the only one.** What makes a connected
  run safe there is *when* it happens: before the first message, over a worktree holding the commit
  the repository supplied and nothing the model wrote. An event during the conversation with a
  network would be a plugin that has read whatever the model has been writing and can send it
  anywhere.
- **A plugin's scratch is its own and never the session's.** The model writes the session's, so a
  plugin keeping an executable there would run whatever the model last left at that path, unattended,
  and report the result into the conversation in this console's voice. It is also `$HOME` inside the
  namespace, which is what makes a `uv run --script` plugin work at all - undo that and inline
  dependencies resolve at `setup` and are gone by the next event. Nothing here can check it: `Spawned`
  is handed a root and `Workspaces` is handed a root, and `app.py` is the only place both are named,
  so that is where the two must stay apart and `test_app.py` is what fails when they do not. It is
  `$MAINPLATE_PLUGIN_SCRATCH` inside and never `$MAINPLATE_SCRATCH`, which is the name a model's own
  `bash` finds the *session's* directory under.
- **A plugin outside a worktree is handed no scratch, and that is a decision.** What a scratch answers
  is having nowhere to write, which only the namespace creates; such a plugin has the operator's
  `$HOME`, their caches, their `/tmp` and their other scripts, and may need all four. Giving it one
  would be two empty directories per session that nothing removes. Something to remember per session
  is the payload's `state`.
- **A setup must be idempotent, and this is a constraint on plugin authors as well as on us.**
  Nothing is recorded until every plugin has answered, so one that fails means all of them run again.
- **A fork carries nothing about its parent's plugins**, so it declares, draws the settings step over
  the turns it holds, and runs `setup` again on its own press. That is what lets a session iterate on
  `.mainplate/` by forking, and it is why the setup that installs a session's toolchain can live in a
  plugin at all: a branch plants a fresh worktree, and an ignored directory does not come across in a
  recorded tree. A snapshot is `git add -A`, so the tree a branch is planted at *is* a tree the model
  wrote, and what makes running it legitimate is the press in the branch rather than the parent's.
  Anything that would hand a fork a registration, a declaration or a press is the change to push back
  on.
