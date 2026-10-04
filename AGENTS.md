# AGENTS.md

mainplate is a chat console over its own durable model-and-tool loop, using Pydantic AI for provider
requests and normalized messages. [`README.md`](README.md) is what it does and why; this is the map
for changing it.

`CLAUDE.md` beside it is one line importing this file, so Claude Code reads the same words every
other harness does. `AGENTS.md` is the one that holds them, because it is the name the ecosystem
converged on and the name this console's own guidance loader reaches for first. Parts of the source
carry their own pair of the same shape, listed below.

## Start here

[`PHILOSOPHY.md`](PHILOSOPHY.md) is the standard new work here is measured against, and it argues
each of the rules below at length. They are stated here as well because a change that breaks one is
the change that gets sent back, whether or not its author opened the file:

- **The checkpoint is the conversation.** There is no messages table, no session state in the
  server, and no cache; a page renders `checkpointer.load(session)`, a crash resumes from the same
  rows, and two tabs agree because they are reading the same thing. **Anything that would keep a
  second copy of what was said is the change to push back on**, and the rule is the narrow one: a
  copy that has to be kept in step with something that *changes*. The session index, the model
  catalogue, `localStorage`, a fork, a recorded cost, and an artifact all look like exceptions and
  are not; a new one has to argue the way theirs do, in `PHILOSOPHY.md`, before it is built.
- **The person drives and the model carries.** Nothing in the console decides anything: a recorded
  number is crossed and a message is delivered. Name the trigger, never an actor.
- **One name per thing, and the same name in the code and on the page.** A second word for one
  operation is a synonym to keep in step for ever. Say what it is: a tool `refuses`, a session is
  `stalled`, never "unavailable".
- **A page is a pure function of already-answered questions.** Rendering asks nothing, the clock
  included, which is what lets `scripts/gallery.py` draw every page from fixtures.
- **A component either refuses at startup or promises not to raise**, and which is decided by
  whether a failure could leave somebody holding a choice they cannot use.
- **Configuration that changes under a reader is read before ready, refreshed off the request path,
  and swapped whole**, and a failed refresh keeps the last good value.
- **One fact that has to be written in two places is named where both halves are, and tested** so a
  drift fails. A third place is never the answer.
- **Controls:** two controls kept in step are one question; a control that toggles may not move; a
  control says what it does and never remembers what it did last; everything `mainplate.js` does is
  an enhancement.
- **Say what a choice costs, in the sentence that makes the choice.**

## How a change is made here

This repository writes down more than most, and the writing is part of the design. **Where your
standing guidance says otherwise, such as to write few comments or keep docstrings short, this
section wins for work in this repository.**

- **Reasoning beside the code is load-bearing.** A docstring or comment here says why, and what the
  choice costs. Never delete one for being long. Where a change makes one false, rewrite it into
  reasoning that holds, in the same voice; a one-line assertion where an argument was is a deletion.
  A function written beside documented ones gets a docstring of the same kind.
- **When a change contradicts something written down, stop and say so before rewriting it.** A
  sentence saying "nothing ever renames it", or a design note saying a thing is not a copy, is
  usually the design, and whether the design moves is the person's to decide. Once it does, the
  prose is rewritten to read as if it were always so, with the argument whole.
- **Reuse before adding.** Find the control, helper, fixture or test already doing the same kind of
  job and build on it; a second one of a kind is a second thing to keep in step. For the page, the
  kinds of control are listed in [`src/mainplate/AGENTS.md`](src/mainplate/AGENTS.md).
- **A feature is looked at, not argued about:** a demo in the seed (below), `just shots`, and a
  section of the design note covering it, written in that note's voice.

## Commands

```console
$ just setup            # dependencies, browser, and pre-commit as a git hook
$ just vendor           # every script and face somebody else wrote, fetched and checked against scripts/vendored.toml
$ just test             # mypy, then pytest
$ just test -n0 tests/test_console.py::TestTheConsole  # extra args go straight to pytest; -n0 below the whole suite
$ just check            # pre-commit over all files, then mypy
$ just serve            # foreground, on port 8101 so it never fights the installed service
$ just demo             # the same, on a database of its own, for poking without touching real sessions
$ just seed             # the gallery's fixtures into that database, replacing any seeded before, so there is something to click
$ just gallery          # render every page to build/gallery, as files a browser can open
$ just shots            # render every page and screenshot it, wide and phone, into build/shots
$ just replay           # measure what replaying a turn costs, over a stand-in provider
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

### Running less of the suite while working

**Pass `-n0` for anything narrower than the whole suite**: `just test -n0 tests/test_console.py`.
Each `xdist` worker imports the console, whose provider SDKs are over a second of imports, and
collects every test before running any, so starting two of them costs three to four seconds that a
single test, a class, or even a whole file never earns back on two cores. Only the full suite and
`test_browser.py` are faster with workers. That is why `addopts` keeps `-n auto`: it is what a bare
`just test` wants, and `-n0` on the command line overrides it.

- `--lf` reruns what failed last time, and `--ff` runs it first and then everything else.
- `-k` picks tests by name, and it matches a test's name, never a subtest's label: a browser test
  that checks several claims as subtests runs whole or not at all, so a failing subtest is reached
  by the test holding it.
- `-p no:randomly` fixes the order, for bisecting; `--randomly-seed=last` replays the order of the
  run that failed, which is the one to reach for when a test passes alone and fails in the suite.
- Anything outside `test_browser.py` runs without launching Chromium, since the browser is a
  fixture only that file asks for.

Finish with a bare `just test`, since a narrow run is not the suite and `randomly` is only shuffling
whatever it was handed.

## A real turn costs real money

**Do not spend one to see something a fixture already shows.** `just seed` plants the gallery's
checkpoints into the demo database, which is a whole conversation to read, fold, search, fork and
screenshot without a provider ever being asked anything. That is the right tool for a rendering, a
stylesheet, a control, or anything downstream of a checkpoint, which is most of what changes here.

**A new or changed feature gets a demo in the seed wherever one is reasonable to write.** Something
a page draws is shown by putting what it draws into a fixture's checkpoint in `scripts/gallery.py`,
so `just seed` plants it and the demo console has it to click, and the gallery and the shots draw it
too. It is how the feature gets looked at without a real turn, both now and by whoever changes it
next. The exception is a state only a worker produces on the way to settled, which is a variation in
`pages()` rather than a fixture; see [`scripts/AGENTS.md`](scripts/AGENTS.md).

When a change genuinely needs a live pass, the wires, the catalogue, durability, the worker, start
the session on the cheapest model the endpoint lists and say the shortest thing that exercises it.
Reach for a frontier model only when the change is about what a frontier model does differently, and
say so.

## A checkout is a session's to write, so the parent must not run git against it

The console process holds the credential, the store and the service user's whole filesystem; the
sandbox holds a session's checkout, bound read-write because a session has to work in it. **Anything
the parent runs against that checkout is running with one side's authority over the other side's
input**, and the mistake is never a missing check, it is a program that goes and *finds* something
rather than being handed it.

Git is the standing example and the reason this has a section. A session's checkout is a complete
repository with a `.git` of its own, so its configuration and hooks are the session's to write, and
several configuration keys name programs git runs (`core.fsmonitor` fires inside `git add`, and the
list is open-ended). A failing monitor makes git scan normally, so a capture run in the parent would
succeed and nothing would report that a program ran. So the parent runs no git against a checkout at
all: `Checkout.git` runs it behind `bwrap`, and what comes back is a listing or a bundle.

What must hold when adding to the parent:

- **Never run git against a session's checkout in the parent.** Not `git -C <checkout>`, not
  `--git-dir <checkout>/.git`, not in `snapshots.py`, a tool, a page, a plugin or a script. Reach a
  checkout through `Checkout.git`, which confines it.
- **Git in the parent is for the `Store` and nothing else.** `Store.git` names the bare clone with
  `--git-dir`; `git_at` is for the directory clones are made under and for a checkout still being
  built, before any session has written to it.
- **What crosses from a sandbox is data.** A bundle is fetched by the store, refused if it is a link
  or not a regular file, and the tree it carries is read back out of the store rather than taken on
  the checkout's word. Never take a path out of a checkout and act on it in the parent.
- **Never carry a session's checkout as a path and rebuild a `Checkout` from it** somewhere that then
  runs git with it the ordinary way. Pass the value, which knows its store and its sandbox.
- **A console-tier plugin runs unconfined**, and is handed the checkout's path. The bundled guidance
  plugin reads the index through `GIT_INDEX_FILE` against an empty repository of its own; anything
  else there that runs git has to do the same.
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
  handoff, steering, running a command, and pushing.
- [`docs/design/workspace.md`](docs/design/workspace.md): forges, clones, a session's checkout,
  where in it and on what branch, snapshots, where everything a session keeps on disk is and what it
  takes, archiving, which takes it away while keeping the conversation, deleting, which takes the
  conversation too, and the daily vacuum that gives the database file back what deleting freed.
- [`docs/design/tools.md`](docs/design/tools.md): which tools a session gets, and the
  content-addressed anchoring scheme behind `read` and `edit`.
- [`docs/design/artifacts.md`](docs/design/artifacts.md): the store of versioned HTML documents
  beside the checkpoint, the tools that keep and export one, and how a version is served sandboxed.
- [`docs/design/sandbox.md`](docs/design/sandbox.md): the mount namespace `bash` runs behind, and
  the two isolation axes a session picks.
- [`docs/design/jobs.md`](docs/design/jobs.md): jobs, the one thing a session runs that outlives
  the call, how the checkpoint says which should be running, why a job must be idempotent, and how a
  port is handed into a sandbox with no network rather than found in one.
- [`docs/design/security.md`](docs/design/security.md): the boundary between the parent and the
  sandbox, what is untrusted, and what is deliberately left undefended. **Read it before adding
  anything to the parent that runs a program against a session's checkout.**
- [`docs/design/durability.md`](docs/design/durability.md): the model-and-tool loop, the steps it
  records, what one pass does, what a session does when a provider says to come back later, and what
  replaying a turn costs, which `just replay` measures rather than asserts.
- [`docs/design/plugins.md`](docs/design/plugins.md): the protocol a plugin speaks, the events it is
  sent, the effects it may ask for, and the settings step in front of running any of them. **Nothing
  executes a plugin before somebody presses the button on that step**, which is a trust boundary and
  not a loading order. **Handoff and what a session is told are both plugins**, so a change to either
  is a change to a script in `src/mainplate/plugins/bundled/` rather than to the console. **Getting a
  repository ready to work in is a plugin too**, which is where the two grants a repository's plugin
  has at `setup` and at no other event are written down. **This repository carries that plugin in
  `.mainplate/`**, described below.
- [`docs/design/console.md`](docs/design/console.md): the live connection, panels and rules, which
  clock a moment is printed against, the picker, and the message box.
- [`docs/design/assets.md`](docs/design/assets.md): the three shapes, the one value that scales the
  page, and the vendored monospace face box drawing depends on.
- [`docs/design/deployment.md`](docs/design/deployment.md): the systemd unit `mainplate install`
  renders, and why the service is not itself confined.

The toolchain around the source rather than any part of the console is
[`docs/maintaining.md`](docs/maintaining.md): the dependency choices, the checks, the documentation
site, and where the prose in this repository goes.

Seven directories carry an `AGENTS.md` of their own, which you are handed on reaching into one rather
than having to go and find: `src/mainplate/` for the stylesheet, the script and everything else
under `assets/`, which cannot carry one of its own because every file there is served;
`src/mainplate/pages/` for where markup is written and what a page may ask;
`src/mainplate/tools/` and `src/mainplate/tools/files/` for what a change to a tool must not break,
`src/mainplate/plugins/` for what a change to the protocol or a bundled plugin must not break,
`tests/` for how the suite is driven and what has to be a browser, and `scripts/` for the gallery
and the seeder.

**Those say what must hold; the design notes say why.** A page argues for four lowercase letters and
the file beside `anchors.py` says do not make it three, so write a new constraint beside the code
and its reasoning on the page, rather than either in both.

## This repository runs a plugin of its own

`.mainplate/mainplate.yaml` declares a repository-tier plugin like anybody else's: it runs behind
the sandbox, with a network only at `setup`, out of a scratch directory nothing else can write.

### `setup` fetches the toolchain and asks for the demo

`.mainplate/setup` is what a mainplate session runs, once, to be able to run `just test` here: it
installs mise into the session's scratch, `mise install`s the tools `mise.toml` pins, and runs `just
setup` under them. It is the plugin that uses [the two grants a `setup`
has](docs/design/plugins.md#getting-the-repository-ready-is-a-plugin-too): the session's own scratch
bound read-write, so what it installs is where the session's commands look for it, and
`$MAINPLATE_ENV`, whose `KEY=value` lines are what those commands then run under. It declares no
events, so nothing asks it anything again.

**Its answer is one [job](docs/design/jobs.md), `.mainplate/demo`**: the demo console on the seeded
fixtures, under `watchfiles`, so a session changing the console has one of its own to look at. It
starts against `.mainplate/standin`, an endpoint on the sandbox's own loopback that lists one model
and answers nothing, because the session may have no network and cannot see the operator's
configuration. Like every job it must be safe to run twice, since a console restart runs it again.

Four things follow for anybody changing it:

- **`just setup` installs the git hook in the session's checkout**, as it does in a fresh clone.
  A session that enables this repository's setup plugin gets pre-commit checks on its commits;
  that costs a hook even in sessions that never commit.
- **The `PATH` it writes is the session's whole `PATH`.** Leave the system directories on the end,
  or the session's commands lose `sh`.
- **It installs into `$MAINPLATE_SCRATCH` and never `$HOME`.** `$HOME` inside it is the plugin's own
  directory, which the session's commands cannot see; the two names are different on purpose and
  [the table](docs/design/plugins.md#what-is-in-the-environment) is which is which.
- **Its answer goes to the stdout saved as `3`**, since everything else it runs is sent to stderr;
  a line of progress on stdout would be an answer the console cannot read.

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
