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
$ just dependencies     # the same without the hook, which is the half a session's `.mainplate/setup` runs
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

## A worktree is a session's to write, so the parent must not read anything out of it

The console process holds the credential, the store and the service user's whole filesystem; the
sandbox holds a session's worktree, bound read-write because a session has to work in it. **Anything
the parent runs against that worktree is running with one side's authority over the other side's
input**, and the mistake is never a missing check, it is a program that goes and *finds* something
rather than being handed it.

Git is the standing example and the reason this has a section. Its configuration names programs it
runs (`core.fsmonitor` fires inside `git add`, and the list is open-ended), a session's worktree is a
linked one whose `.git` is a pointer file the session can replace, and a failing monitor makes git
scan normally, so the capture succeeds and nothing reports it. So `Worktree.gitdir` names git's
directory and `Worktree.git` passes `--git-dir` with `--work-tree`, which reads the configuration out
of the read-only clone and consults the tree's not at all.

What must hold when adding to the parent:

- **Never run git in a session's worktree without naming its directory.** A bare `git -C <worktree>`
  in `snapshots.py`, in a tool, in a page or in a script is the whole vulnerability restored.
- **Never take a path out of a worktree and act on it in the parent.** Derive it, the way
  `Worktrees.gitdir` does, or receive it from the console's own state.
- **Never carry a session's worktree as a path and rebuild a `Worktree` from it.** The rebuilt one
  names no git directory, which is the discovery mode, and nothing about the call site changes to
  say so. Pass the value.
- **Prefer the sandbox** where the parent has no reason to be the one running it at all. That is the
  [plugins-as-scripts](docs/design/plugins.md) argument, and it is the same argument.

[`docs/design/security.md`](docs/design/security.md) is why, and names what is still open.

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
  where in it and on what branch, snapshots, and where everything a session keeps on disk is and
  what it takes.
- [`docs/design/tools.md`](docs/design/tools.md): which tools a session gets, and the
  content-addressed anchoring scheme behind `read` and `edit`.
- [`docs/design/sandbox.md`](docs/design/sandbox.md): the mount namespace `bash` runs behind, and
  the two isolation axes a session picks.
- [`docs/design/security.md`](docs/design/security.md): the boundary between the parent and the
  sandbox, what is untrusted, and what is deliberately left undefended. **Read it before adding
  anything to the parent that runs a program against a session's worktree.**
- [`docs/design/durability.md`](docs/design/durability.md): the stepwise capability, and what one
  pass does.
- [`docs/design/plugins.md`](docs/design/plugins.md): the protocol a plugin speaks, the events it is
  sent, the effects it may ask for, and the settings step in front of running any of them. **Nothing
  executes a plugin before somebody presses the button on that step**, which is a trust boundary and
  not a loading order. **Handoff and what a session is told are both plugins**, so a change to either
  is a change to a script in `src/mainplate/plugins/bundled/` rather than to the console. **Getting a
  repository ready to work in is a plugin too**, which is where the two grants a repository's plugin
  has at `setup` and at no other event are written down. **And this repository carries two of its
  own**, in `.mainplate/`, described below.
- [`docs/design/console.md`](docs/design/console.md): the live connection, panels and rules, the
  picker, and the message box.
- [`docs/design/assets.md`](docs/design/assets.md): the three shapes, the one value that scales the
  page, and the vendored monospace face box drawing depends on.
- [`docs/design/deployment.md`](docs/design/deployment.md): the systemd unit `mainplate install`
  renders, and why the service is not itself confined.

The toolchain around the source rather than any part of the console is
[`docs/maintaining.md`](docs/maintaining.md): the dependency choices, the checks, the documentation
site, and where the prose in this repository goes.

Six directories carry an `AGENTS.md` of their own, which you are handed on reaching into one rather
than having to go and find: `src/mainplate/tools/` and `src/mainplate/tools/files/` for what a
change to a tool must not break, `src/mainplate/plugins/` for what a change to the protocol or a
bundled plugin must not break, `src/mainplate/assets/` for what a change to the stylesheet, the
script or the vendored font must not break, `tests/` for how the suite is driven and what has to be
a browser, and `scripts/` for the gallery and the seeder.

**Those say what must hold; the design notes say why.** A page argues for four lowercase letters and
the file beside `anchors.py` says do not make it three, so write a new constraint beside the code
and its reasoning on the page, rather than either in both.

## This repository runs two plugins of its own

`.mainplate/mainplate.yaml` declares both, and they are repository-tier plugins like anybody else's:
they run behind the sandbox, with a network only at `setup`, out of a scratch directory nothing else
can write.

### `pre-commit`, which runs the checks

`.mainplate/pre-commit` means **a mainplate session working on mainplate runs this project's own
hooks whenever the model tries to stop** and is sent back with what is still failing, inside the same
turn, up to a number of times its card says.

Two things follow for anybody changing it:

- **It is the only `pre-commit` a session can reach.** The model's `bash` has no network to install
  one and no way into that scratch, so the plugin's `repo_pre-commit_run` tool is the whole of how a
  session checks itself. Removing the tool would leave a session unable to run the checks this
  repository asks for before saying anything is done.
- **Changing it changes nothing about a session already running.** A repository's plugin is read once
  and set up once, so an edit reaches the next *new* session and no turn of any existing one.

Try it by hand rather than by starting a session:
`echo '{"event":"setup","session":"x","plugin":"repository:pre-commit","worktree":"'$PWD'","scratch":"/tmp/x"}' | .mainplate/pre-commit`.

### `setup`, which fetches the toolchain

`.mainplate/setup` is what a mainplate session runs, once, to be able to run `just test` here: it
installs mise into the session's scratch, `mise install`s the tools `mise.toml` pins, and runs `just
dependencies` under them. It is the plugin that uses [the two grants a `setup`
has](docs/design/plugins.md#getting-the-repository-ready-is-a-plugin-too): the session's own scratch
bound read-write, so what it installs is where the session's commands look for it, and
`$MAINPLATE_ENV`, whose `KEY=value` lines are what those commands then run under. It declares no
events and **prints nothing**, so nothing asks it anything again.

Three things follow for anybody changing it:

- **`just dependencies` and never `just setup`**, because the other half of `setup` installs a git
  hook into the clone's common directory, which is shared by every worktree of it and bound read-only
  in a session. A step that has to write git belongs in the composer's `Run`, as the person.
- **The `PATH` it writes is the session's whole `PATH`.** Leave the system directories on the end,
  or the session's commands lose `sh`.
- **It installs into `$MAINPLATE_SCRATCH` and never `$HOME`.** `$HOME` inside it is the plugin's own
  directory, which the session's commands cannot see; the two names are different on purpose and
  [the table](docs/design/plugins.md#what-is-in-the-environment) is which is which.

It is not run by the suite, since what it does is fetch a toolchain.

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
