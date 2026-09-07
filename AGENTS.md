# AGENTS.md

mainplate is a chat console over a Pydantic AI agent whose sessions are durable workflows.
[`README.md`](README.md) is what it does and why; this is the map for changing it.

`CLAUDE.md` beside it is one line importing this file, so Claude Code reads the same words every
other harness does. `AGENTS.md` is the one that holds them, because it is the name the ecosystem
converged on and the name this console's own guidance loader reaches for first. Parts of the source
carry their own pair of the same shape, listed below.

## Start here

Read [`PHILOSOPHY.md`](PHILOSOPHY.md) before changing anything. It rests on one idea: **the
checkpoint is the conversation.** There is no messages table, no session state in the server, and no
cache; a page renders `checkpointer.load(session)`, a crash resumes from the same rows, and two tabs
agree because they are reading the same thing. **Anything that would keep a second copy of what was
said is the change to push back on**, and the rule is the narrow one: a copy that has to be kept in
step with something that *changes*. The session index, the model catalogue, `localStorage`, a fork,
and a recorded cost all look like exceptions and are not, and the doc says why each one is not,
because a sixth will be proposed and its argument has to look like one of theirs.

It also carries the vocabulary this console names things with, and the cross-cutting rules the
design notes cite rather than restate: when a component refuses at startup against when it promises
not to raise, how configuration that changes under a reader is handled, what "a page is a pure
function of already-answered questions" rules out, and what to do about one fact that has to be
written in two places.

## Commands

```console
$ just setup            # uv sync, the browsers, and pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_console.py::TestTheConsole  # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve            # foreground, on port 8101 so it never fights the installed service
$ just demo             # the same, on a database of its own, for poking without touching real sessions
$ just seed             # the gallery's fixtures into that database, so there is something to click
$ just gallery          # render every page to build/gallery, as files a browser can open
$ just shots            # render every page and screenshot it, wide and phone, into build/shots
$ just docs             # serve the documentation site with live reload
$ just docs-build       # build it into ./site, strictly
$ just install          # this checkout as a user systemd unit, on the default port 8100
$ just logs             # journalctl --user -u mainplate -f
$ just uninstall        # removes the unit, keeps the environment file and the database
```

Run `just test` or `just check` before saying anything is done. CI runs the same pre-commit
configuration, so there is one definition of what the checks are.

`just serve` and `just demo` run under `watchfiles` and restart on any change under `src/mainplate`,
which is what makes a styling change watchable: the assets are inventoried once at startup, so an
edited stylesheet only reaches a *new* process. They restart the server and do not reload the
browser, which is one keystroke against needing a dev-only script injected into a page that ships.

`just serve` and `just install` are on different ports on purpose, so a foreground run for a quick
look never takes down the service. `just install` runs `uv sync` first, and that is not a
convenience: the unit names this checkout's interpreter, so an install from a stale environment
points systemd at a venv missing whatever was just added.

## A real turn costs real money

**Do not spend one to see something a fixture already shows.** `just seed` plants the gallery's
checkpoints into the demo database, which is a whole conversation to read, fold, search, fork and
screenshot without a provider ever being asked anything. That is the right tool for a rendering, a
stylesheet, a control, or anything downstream of a checkpoint, which is most of what changes here.

When a change genuinely needs a live pass, the wires, the catalogue, durability, the worker, start
the session on the cheapest model the endpoint lists and say the shortest thing that exercises it.
Reach for a frontier model only when the change is about what a frontier model does differently, and
say so.

## Where the rest of it is written

The design notes are [a documentation site](https://joshkarpel.github.io/mainplate/) built from
`docs/`, one page per part of the console. Each says what the mechanism is, what it costs, and which
alternatives were tried and are not worth trying again. Read the one covering what you are about to
change:

- [`docs/design/checkpoints.md`](docs/design/checkpoints.md): the two key spaces, what is recorded
  under each key and by whom, and what a checkpoint value may be. Start here, because every other
  page reads or writes something described on it.
- [`docs/design/endpoints.md`](docs/design/endpoints.md): endpoints, wires and providers, the
  discovered model catalogue, and the reference database a model's price and window come from.
- [`docs/design/cost.md`](docs/design/cost.md): pricing a response, timing a request, and the line
  above the message box that says what re-sending the conversation costs.
- [`docs/design/forking.md`](docs/design/forking.md): how a session changes its mind, and why there
  is no rewind.
- [`docs/design/composer.md`](docs/design/composer.md): dispositions, leaders, the shelf, `forget`,
  handoff, steering, and running a command.
- [`docs/design/workspace.md`](docs/design/workspace.md): forges, clones, a session's worktree,
  where in it and on what branch, and snapshots.
- [`docs/design/guidance.md`](docs/design/guidance.md): what a session is told, and the nested
  guidance handed over as the model reaches into a directory.
- [`docs/design/tools.md`](docs/design/tools.md): which tools a session gets, and the
  content-addressed anchoring scheme behind `read` and `edit`.
- [`docs/design/sandbox.md`](docs/design/sandbox.md): the mount namespace `bash` runs behind, and
  the two isolation axes a session picks.
- [`docs/design/durability.md`](docs/design/durability.md): the stepwise capability, and what one
  pass does.
- [`docs/design/plugins.md`](docs/design/plugins.md): the protocol a plugin speaks, the events it is
  sent, the effects it may ask for, and the trust gate in front of a repository's own plugin. The
  one page here describing something **not built yet**, so read it as the design being written to
  rather than as the console as it stands.
- [`docs/design/console.md`](docs/design/console.md): the live connection, panels and rules, the
  picker, and the message box.
- [`docs/design/assets.md`](docs/design/assets.md): the three shapes, the one value that scales the
  page, and the vendored monospace face box drawing depends on.
- [`docs/design/deployment.md`](docs/design/deployment.md): the systemd unit `mainplate install`
  renders, and why the service is not itself confined.

The toolchain around the source rather than any part of the console is
[`docs/maintaining.md`](docs/maintaining.md): the dependency choices, the checks, the documentation
site, and where the prose in this repository goes.

Five directories carry an `AGENTS.md` of their own, which you are handed on reaching into one rather
than having to go and find: `src/mainplate/tools/` and `src/mainplate/tools/files/` for what a
change to a tool must not break, `src/mainplate/assets/` for what a change to the stylesheet, the
script or the vendored font must not break, `tests/` for how the suite is driven and what has to be
a browser, and `scripts/` for the gallery and the seeder.

**Those say what must hold; the design notes say why.** A page argues for four lowercase letters and
the file beside `anchors.py` says do not make it three, so write a new constraint beside the code
and its reasoning on the page, rather than either in both.

## Dependencies

Built on [`without`](https://without.help), a workspace of small sans-IO libraries, and the sibling
checkout at `../without` is where its source is. **Read the installed packages in `.venv` rather
than guessing at an API from memory**; the same goes for `pydantic_ai`, which moves fast. What the
version pins and the resolution cooldown are for is in
[`docs/maintaining.md`](docs/maintaining.md).

## Writing here

The design notes and this file are written to one standard, and it is worth knowing before adding to
them:

- **Say what a choice costs, in the sentence that makes the choice.** "The cost, stated" is
  throughout the notes and is not a tic: if you cannot name what an option costs, you have not made
  a decision.
- **Record a rejected design only while it is still tempting.** The reason a note says what was
  tried is that the alternative reads better than it works. When it stops being tempting, cut it.
- **Name the thing, in the word the page prints.** See the words section of `PHILOSOPHY.md`.
