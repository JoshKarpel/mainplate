---
description: The gallery, the seeder, the screenshot driver and the replay benchmark, and the invariants each has to repeat because none of them is the service.
---

# The scripts

Five of them. Four exist so that a change can be looked at or measured without a provider ever
being asked anything, and the fifth is how anything somebody else wrote gets into `assets/`.

**A real turn costs real money, so do not spend one to see something a fixture already shows.** That
is the right tool for a rendering, a stylesheet, a control, or anything downstream of a checkpoint,
which is most of what changes here.

## `gallery.py`

Renders every page from fixture checkpoints, as files a browser can open (`just gallery`). It needs
no server, no database, no provider and no `config.yaml`, because a page is a pure function of
already-answered questions: this answers them with fixtures and the assets are copied beside the
output, so a static server renders what the console renders.

**`FIXTURES` is the one table of sessions, and `seed.py` plants every row of it.** A `Fixture` is a
session's row, what it is on, and the checkpoint under it, and everything in the table is settled:
nothing in a fixture's checkpoint is waiting for a pass, because the demo has no worker behind it
and a session drawn as being answered by nothing reads as broken. The states a worker produces on the
way to settled - a message waiting, a turn part way through, a pass that fell over, a handoff whose
document is unread, a command still running - are built by `pages()` as variations on a fixture's
checkpoint and are not fixtures. Something new for the demo to show goes into a fixture's checkpoint;
something new for a still to show that a worker would be in the middle of goes into `pages()`.

`just shots` drives a real Chromium over that output and screenshots every page wide and phone.
Every shot also prints whether the document scrolls sideways, which is how the `:target` rule that
widened a panel past its container was found. It fails nothing, deliberately: it is a diagnostic for
somebody already looking, and the build-failing version of that question is
`TestTheShapeOfANarrowWindow` in the suite. Which pages it shoots it asks of `pages()`, so a page
added here is shot without being listed anywhere else.

`tests/test_browser.py` renders this same gallery, through `pythonpath = ["."]`, rather than through
a second set of fixtures that resembles it.

**The documentation site renders it too, at build time, and checks nothing in.** `docs/hooks.py`
calls `pages()` with a relative asset prefix, adds every page and every asset under `gallery/`, and
writes the index page from `CAPTIONS`, one sentence per page. A page added to `pages()` needs a
caption there, and a test holds the two sets equal. Rendering takes about a second, which is why
the pages are derived on every build rather than committed and guarded.

**Every response fixture carries a timestamp.** `ModelResponse.timestamp` defaults to the moment it
was constructed, so a fixture without one is the moment the render ran, and two `just gallery` runs
then produce two different pages. A screenshot that differs run to run is one nobody can compare
against the last.

**The zone every page is drawn against is stated too**, for that reason and one more. `here()` reads
the machine the render ran on, so a shot taken in one zone would not match a shot taken in another;
and `ZONE` is deliberately not UTC, since every moment in these fixtures is recorded in UTC and a
gallery drawn in UTC would look identical whether or not anything converted anything. `shoot.py` and
the browser tests point their browsers at the same zone, or the script asks for every page again.

## `seed.py`

Plants the gallery's `FIXTURES` into the demo database (`just seed`), so the console can be
*driven* rather than looked at: the sidebar reordering between branches, a fork actually being made,
the rail projecting onto a transcript that came out of SQLite. Idempotent and never destructive. It
keeps no list of its own: which sessions exist and what each holds is the gallery's table, so the
demo and the stills cannot drift apart.

**It is the one writer that is not `Service`, so an invariant the service enforces does not hold
here unless it is repeated.** It supplies checkpoint keys directly, which is what lets it plant a
finished conversation nobody paid for, and is also what makes it the one place a contradictory
record can be written: it once seeded every fixture naming a repository while recording that it
reached no files, because settling lives in `Service.start` and nothing here goes through it.

So a rule about what a recorded `choice` may hold is a rule the fixtures have to apply too.
`Fixture.of` in `gallery.py` calls `Choice.settled()` and `branching()` rather than restating what a
repository decides, which is what keeps that list in one place: a field added to what a repository
settles is settled there without an edit, and is the whole reason `settled` is a method on `Choice`
rather than a line in each of its callers. Both scripts read the settled choice, so the rail in a
still and the rail in the demo say the same thing.

**Rebuild the demo database after any change to a fixture or to what a record holds** (`just
reseed`, which removes the demo database and seeds it again), or it keeps serving the old shape and
`just seed` says `skipped` for every row. That is the whole checkpoint and not only the choice: the
fixtures in `gallery.py` write every kind directly, so a shape change lands here as a database full
of values nothing can parse.

## `replay.py`

Drives one turn twice and prints what the second one paid (`just replay`): unbounded, where a single
pass makes every live request, and then at the allowance the console ships, where pass *k* replays
the *k-1* requests already recorded. What it exists to answer is
[what replay costs](../docs/design/durability.md#what-replay-costs), which was an argument before it
was a number.

**It is the odd one out here, because it is not downstream of a checkpoint: it writes one.** So it
drives a real `Service` over a real store and a real git worktree, with only the provider standing
in - the per-request snapshot leaves the process, and a stand-in for git would leave the one thing a
pass does outside Python out of the measurement.

**The stand-in states its own usage, which is the control rather than a detail.** `FunctionModel`
estimates usage for a response that carries none by splitting every message in the history with a
regular expression, which is itself quadratic across a turn: left to it, the dominant term this
prints is the fixture's own and not the console's. It was, the first time this was run.

**It fits the per-pass series with Theil-Sen rather than least squares**, because a pass that lands
on a git object pack or an sqlite checkpoint costs several times its neighbours and one of those
near the start flattens a real trend to zero. That was also observed rather than anticipated.

It repeats what `tests/conftest.py` sets up - a stand-in endpoint, a fixture repository, a session
with its settings step answered - rather than importing it, so the benchmark does not depend on the
suite's fixtures and the suite does not have to keep this running. The cost is that a change to how
a session is made ready reaches both.

## `vendor.py`

Fetches every row of `vendored.toml` beside it and writes the copy into `src/mainplate/assets/`
(`just vendor`): a pinned URL, the digest of the bytes, and for a file published inside a release
archive the `member` path in the zip. **Every row is checked before any is written**, so a bump with
one wrong digest writes nothing rather than half, and a row with no digest has nowhere to land. That
is the one rule here, applied to every script and face alike; `tests/test_vendored.py` holds the
copies on disk against the same table with no network, and refuses a `.js` or `.woff2` no row names.

Bumping one is editing the version in its URL, running the recipe, and recording the digest it
refuses on once the file has been looked at, with the cooldown the resolver applies to a package: a
week for a minor and a month for a major. Only zip archives are read, because that is what has been
needed; a tarball is a second reader, added deliberately.

The pre-commit hooks and `.gitattributes` both step around the vendored files, and that is this
script's doing rather than theirs: a newline a hook appends, or a line ending git normalises on the
way into the index, is a digest the test then refuses on every other machine.

## `shoot.py`

The browser half of the screenshots, on the same Playwright the suite drives, so a checkout pins one
Chromium and `just dependencies` fetches it once. It serves the gallery itself on a port the kernel
hands it, so a shoot beside a running console needs nothing stopped. It uses the sync binding, which
the suite cannot: this runs on its own with no event loop to collide with, and `tests/AGENTS.md`
says why the suite's choice is forced the other way.
