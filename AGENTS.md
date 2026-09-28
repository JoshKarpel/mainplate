# AGENTS.md

mainplate is a chat console over its own durable model-and-tool loop, using Pydantic AI for provider
requests and normalized messages. [`README.md`](README.md) is what it does and why; this is the map
for changing it.

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

It also carries who the console is for (a centaur: the person drives, the model carries), the
vocabulary this console names things with, and the cross-cutting rules the design notes cite rather
than restate: when a component refuses at startup against when it promises not to raise, how
configuration that changes under a reader is handled, what "a page is a pure function of
already-answered questions" rules out, and what to do about one fact that has to be written in two
places.

## Commands

```console
$ just setup            # uv sync, the browser, and pre-commit as a git hook
$ just dependencies     # the same without the hook, which is the half a session's `.mainplate/setup` runs
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

## A worktree is a session's to write, so the parent must not run git against it

The console process holds the credential, the store and the service user's whole filesystem; the
sandbox holds a session's worktree, bound read-write because a session has to work in it. **Anything
the parent runs against that worktree is running with one side's authority over the other side's
input**, and the mistake is never a missing check, it is a program that goes and *finds* something
rather than being handed it.

Git is the standing example and the reason this has a section. A session's worktree is a complete
checkout with a `.git` of its own, so its configuration and hooks are the session's to write, and
several configuration keys name programs git runs (`core.fsmonitor` fires inside `git add`, and the
list is open-ended). A failing monitor makes git scan normally, so a capture run in the parent would
succeed and nothing would report that a program ran. So the parent runs no git against a worktree at
all: `Worktree.git` runs it behind `bwrap`, and what comes back is a listing or a bundle.

What must hold when adding to the parent:

- **Never run git against a session's worktree in the parent.** Not `git -C <worktree>`, not
  `--git-dir <worktree>/.git`, not in `snapshots.py`, a tool, a page, a plugin or a script. Reach a
  worktree through `Worktree.git`, which confines it.
- **Git in the parent is for the `Store` and nothing else.** `Store.git` names the bare clone with
  `--git-dir`; `git_at` is for the directory clones are made under and for a checkout still being
  built, before any session has written to it.
- **What crosses from a sandbox is data.** A bundle is fetched by the store, refused if it is a link
  or not a regular file, and the tree it carries is read back out of the store rather than taken on
  the worktree's word. Never take a path out of a worktree and act on it in the parent.
- **Never carry a session's worktree as a path and rebuild a `Worktree` from it** somewhere that then
  runs git with it the ordinary way. Pass the value, which knows its store and its sandbox.
- **A console-tier plugin runs unconfined**, and is handed the worktree's path. The bundled guidance
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
- [`docs/design/workspace.md`](docs/design/workspace.md): forges, clones, a session's worktree,
  where in it and on what branch, snapshots, where everything a session keeps on disk is and what it
  takes, and archiving, which takes it away while keeping the conversation.
- [`docs/design/tools.md`](docs/design/tools.md): which tools a session gets, and the
  content-addressed anchoring scheme behind `read` and `edit`.
- [`docs/design/sandbox.md`](docs/design/sandbox.md): the mount namespace `bash` runs behind, and
  the two isolation axes a session picks.
- [`docs/design/security.md`](docs/design/security.md): the boundary between the parent and the
  sandbox, what is untrusted, and what is deliberately left undefended. **Read it before adding
  anything to the parent that runs a program against a session's worktree.**
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

Five directories carry an `AGENTS.md` of their own, which you are handed on reaching into one rather
than having to go and find: `src/mainplate/tools/` and `src/mainplate/tools/files/` for what a
change to a tool must not break, `src/mainplate/plugins/` for what a change to the protocol or a
bundled plugin must not break, `tests/` for how the suite is driven and what has to be a browser,
and `scripts/` for the gallery and the seeder.

**Those say what must hold; the design notes say why.** A page argues for four lowercase letters and
the file beside `anchors.py` says do not make it three, so write a new constraint beside the code
and its reasoning on the page, rather than either in both.

## The assets

`src/mainplate/assets/` is the one directory whose constraints are written here rather than beside
it, because every file in it is served: the inventory walks the whole tree, so a guidance file there
is a page anybody can fetch and a representation every process start compresses. **Put nothing in
`assets/` that a browser should not have.**

Why any of this is the way it is, including every number below, is [The stylesheet, the script, and
the grid](https://joshkarpel.github.io/mainplate/design/assets/). What follows is what must hold
while editing there.

An edit is only visible to a *new* process, since the assets are inventoried once at startup. `just
serve` restarts on any change under `src/mainplate`; `just shots` is how a styling change gets
looked at rather than argued about.

### A narrow rule goes in the block at the end of `mainplate.css`

There are two shapes and one width between them, 78rem, and every rule for the narrow shape is in
the one `max-width` block at the end of the file. **Do not add a second top-level width.** There
used to be a shape between, with the rail folded and the list not, and its query overlapped the
narrow one so that whichever came later won: a shape rule written beside the thing it is about,
above the narrow block, was silently overridden, which is how the 17rem sidebar column came back on
every phone and left the conversation a hundred pixels wide. One block has no order to get wrong.

**What a phone needs beyond the fold goes in the `48rem` query nested at the end of that block**:
room on a line, a pointer that hovers, a height a whole picker fits in, a thumb to press with. The
fold is about columns and fires on half a laptop; a rule that stacks a line or hides half of it
fires there too if it is written in the outer block, which is how a 1200px window came to draw its
rules the way a phone does. Nested, it comes after everything it refines and needs no ordering.

`TestTheShapeOfANarrowWindow` is what fails when this goes, and it also pins that the narrow shape
begins exactly where three columns stop fitting.

### `--mono-size` and `--mono-line` are measured, in pixels, and do not scale

The pitch must be no more than the `│` glyph's own ink **and** a whole number of pixels, or box
drawing draws as a dashed line. At size 14 a run joins at 23 and breaks at 24, so the pitch is 22.

- **Do not put them in `rem`.** `html { font-size }` scales everything else on the page; these are
  the one thing it must not reach, because the whole numbers either side of the answer are a pixel
  apart.
- **Do not move either alone.** They move together, and moving them is a *measurement* against a
  real rendering, not a multiplication.
- **Both belong to every monospace block**, `.text pre` and `.tool__body pre` alike. The latter
  restates the font family deliberately: a browser's own sheet sets `pre` to `monospace`, and a rule
  on the element beats a value inherited from an ancestor.

### The gutter in front of a line is `data-gutter`, painted by `::before`

A diff's line numbers are an attribute the stylesheet draws, not text, so that copying the block
copies the diff; `mainplate.js` reads `textContent` and never sees them. A read's anchors are not
drawn at all, and a line the tool wrote itself is `data-said`, which is the only thing that sets it
apart from the file's lines. **Do not move the gutter into the text, and do not draw the anchors**;
do not put a newline *between* `.line` spans either: each carries its own as its last character,
which is what lets a line be `display: block` and paint its whole row while the block's text still
reads as lines. `TestWhatAnOpenCallShows` and the read-copies-the-file browser test are what fail
when any of that goes. The Pygments token colours are on any `pre` rather than `.text pre`, because
a call's body is coloured by the same tokens.

`TestTheGridMonospaceIsDrawnOn` draws a run into a canvas and reads the pixels back, because the
failure is a hairline no screenshot shows.

### Anything somebody else wrote comes through `just vendor`

`htmax.min.js`, `mermaid.min.js`, the two faces and the licence beside each are rows in
`scripts/vendored.toml`, and the copy in `assets/` is the bytes that row's digest names. **Do not
edit one, and do not drop a script into `assets/` by hand**: `tests/test_vendored.py` hashes every
row's copy and refuses a `.js` or `.woff2` no row names. Bumping one is editing the version in its
`url`, running `just vendor`, and recording the digest it refuses on once the file has been looked
at. The pre-commit hooks are told to leave these files alone, which is why a vendored file may end
without a newline while nothing else there does, and `.gitattributes` tells git to store them
verbatim, so a licence published with CRLF is CRLF in every checkout and not only on the machine
that vendored it.

**The `.br`, `.zst` and `.gz` beside a vendored file are `just vendor`'s too**, written after the file
at each coding's highest level so the server reads them rather than compressing at every start.
Commit them with the file they encode, and never write one for `mainplate.css` or `mainplate.js`: a
sidecar older than its file is silently ignored, and those two change too often for one to stay
current. `tests/test_vendored.py` decodes each against its row's digest and asks the inventory
whether it serves them.

`mermaid.min.js` is fetched by the script the first time a `mermaid` fence is on the page and by
nothing else. **Do not put it in a `<script>` tag**, which is three and a half megabytes on every
page for the pages with no diagram. What a drawing is put on the page as is an `<img>` with a
`data:` URL, for SVG written by hand and the library's output alike: an image runs no script and
fetches nothing, and inlining the markup instead would rest the page on the library's own
sanitising of text a model wrote. `TestDrawingAFence` is what fails when either goes.

### The faces are upstream, unmodified, and there are two of them

`JuliaMono-Regular.woff2` and its bold, under the OFL beside them. Two static weights rather than
one variable file, so the 600 a panel role asks for resolves to the bold instead of being
synthesised by smearing the regular. No italic face is vendored, which is why code inside a
reasoning panel must not be slanted: an oblique is synthesised by shearing every glyph, and it leans
a gutter while leaving the horizontals flat.

Before swapping the face for a smaller one, read the coverage numbers on the design page. The
megabyte is buying every symbol block on the cell, and a fallback filling a gap does it one
character at a time at the wrong advance.

### The installed app stays online-only

`service-worker.js` is network-only. It may register and control `/`, but it must not write Cache
Storage entries or supply an offline response: the server's checkpoint is the conversation, and a
cached page would be an empty shell or a stale second copy. The `Service-Worker-Allowed` header in
`app.py` is what lets a script under `/assets/` take that root scope.

The manifest's `192x192` and `512x512` PNGs and the `180x180` Apple touch icon are raster forms of
`icon-mono-on-dark.svg` on the console's dark ground. Keep the mark inside the central safe circle
so a platform's mask does not cut it.

### `icon.svg` takes its colours from whoever draws it

The dashboard draws it as a `<use>` of `#plate`, and `.home__mark` sets `--plate`, `--person` and
`--assistant` for it. **Keep the `#plate` id, keep every fill a `style` attribute reading one of
those properties**, and keep the file's own `<style>` setting them on `svg` for when it is the
favicon. A fill written as a colour ignores the theme toggle; one written in the file's stylesheet
instead of on the element may not reach a `<use>` at all. The three icons share one geometry, so a
change to the plate is a change to all three and to the PNGs.

### More that is easy to undo

- **Do not replace `htmax.min.js` with core plus separately vendored extensions.** One file cannot
  drift from itself; two have to be kept on one version, and the failure when they are not is a swap
  that silently misbehaves. Which extensions register is `EXTENSIONS` in `pages.py`.
- **Everything `mainplate.js` does stays an enhancement.** With the file absent the page must still
  render, still post, and still fold. What it holds is what cannot live in the markup, reapplied
  after every swap through one idempotent `repaint()`.
- **`soon` in `mainplate.js` words a duration exactly as `elapsed` in `pages.py` does, including the
  unit that is zero.** The server draws the first figure and the script repaints it a second later,
  into the same element, so dropping a `0m` there is a countdown that changes shape while a reader is
  looking at it. Two units at every width above a minute, on both sides. The widths the two owe each
  other are `WORDED` in `tests/conftest.py`, which both suites are parametrised from, so a width
  added to either goes in that table rather than in either test. See
  [the format is canonical, and the zone is the only thing that varies](https://joshkarpel.github.io/mainplate/design/console/#the-format-is-canonical-and-the-zone-is-the-only-thing-that-varies).
- **Do not format a moment in `mainplate.js`.** `paintClock` writes the reader's zone into a cookie
  and asks for the page again where the one it got was drawn against another; every date and time
  on the page is rendered by `pages.py`. Rewriting `<time>` elements instead looks like the smaller
  change and is the larger one: half the moments on this page are inside sentences a tooltip holds,
  so it buys a second implementation of what a date looks like, in a language that cannot see the
  first. See
  [which clock a moment is printed against](https://joshkarpel.github.io/mainplate/design/console/#which-clock-a-moment-is-printed-against).
  There is also nothing in the script to localise: the format is canonical `%Y-%m-%d %H:%M` at
  every reader, so only *which instant* follows the browser and never how it is written.
- **`paintClock` runs beside `applyTheme`, before the document exists, and needs to.** It reads the
  zone the page was drawn against off `<html>`, whose open tag the parser has already passed; moved
  into `start` with the rest of the wiring it would read `document.body`, which is `null` there, so
  a reader in another zone would paint a whole page of wrong times before asking for the right ones.
  Move the attribute to `<body>` and the check stops firing at all rather than firing late, which is
  what `TestTheClockAPageIsDrawnAgainst`'s browser tests fail on.
- **Nothing that runs before `start` may throw**, because it all sits in one block: an exception
  there takes the rest of the file with it, leaving a page with no folds, no copy buttons, no live
  connection and no composer, which is worse than the script being absent. That is why `held`, `hold`
  and `sameClock` catch, and why `cookieValue` treats a value it cannot decode as nothing: a cookie
  is arbitrary text and `decodeURIComponent` raises on a malformed escape.
- **`start` returns where there is no `<body>`, and that case is reachable.** `paintClock` can ask
  for the page again from the head, which abandons the parse where it stands, and
  `DOMContentLoaded` still fires on what was abandoned. The document is already being replaced, so
  there is nothing to wire; without the guard every first visit from another zone raises.
- **The reload is worth doing once and never twice.** `paintClock` reads its own cookie back before
  reloading, because a browser blocking this origin's cookies makes the write a silent no-op, and a
  guard that trusted it would ask for the page again on every load for ever.

## This repository runs a plugin of its own

`.mainplate/mainplate.yaml` declares a repository-tier plugin like anybody else's: it runs behind
the sandbox, with a network only at `setup`, out of a scratch directory nothing else can write.

### `setup` fetches the toolchain

`.mainplate/setup` is what a mainplate session runs, once, to be able to run `just test` here: it
installs mise into the session's scratch, `mise install`s the tools `mise.toml` pins, and runs `just
dependencies` under them. It is the plugin that uses [the two grants a `setup`
has](docs/design/plugins.md#getting-the-repository-ready-is-a-plugin-too): the session's own scratch
bound read-write, so what it installs is where the session's commands look for it, and
`$MAINPLATE_ENV`, whose `KEY=value` lines are what those commands then run under. It declares no
events and **prints nothing**, so nothing asks it anything again.

Three things follow for anybody changing it:

- **`just dependencies` and never `just setup`**, because the other half of `setup` installs a git
  hook, and whether a session's commits run pre-commit is for that session to decide with `pre-commit
  install` in its own checkout, from `bash` or the composer's `Run`, rather than something a setup
  plugin does to every session.
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
