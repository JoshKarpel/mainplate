---
description: The gallery, the seeder and the screenshot driver, and the invariants the seeder has to repeat because it is not the service.
---

# The scripts

Three of them, and all three exist so that a change downstream of a checkpoint can be looked at
without a provider ever being asked anything.

**A real turn costs real money, so do not spend one to see something a fixture already shows.** That
is the right tool for a rendering, a stylesheet, a control, or anything downstream of a checkpoint,
which is most of what changes here.

## `gallery.py`

Renders every page from fixture checkpoints, as files a browser can open (`just gallery`). It needs
no server, no database, no provider and no `config.yaml`, because a page is a pure function of
already-answered questions: this answers them with fixtures and the assets are copied beside the
output, so a static server renders what the console renders.

`just shots` drives a real Chromium over that output and screenshots every page wide and phone.
Every shot also prints whether the document scrolls sideways, which is how the `:target` rule that
widened a panel past its container was found. It fails nothing, deliberately: it is a diagnostic for
somebody already looking, and the build-failing version of that question is
`TestTheShapeOfANarrowWindow` in the suite.

`tests/test_browser.py` renders this same gallery, through `pythonpath = ["."]`, rather than through
a second set of fixtures that resembles it.

**Every response fixture carries a timestamp.** `ModelResponse.timestamp` defaults to the moment it
was constructed, so a fixture without one is the moment the render ran, and two `just gallery` runs
then produce two different pages. A screenshot that differs run to run is one nobody can compare
against the last.

## `seed.py`

Plants the gallery's checkpoints into the demo database (`just seed`), so the console can be
*driven* rather than looked at: the sidebar reordering between branches, a fork actually being made,
the rail projecting onto a transcript that came out of SQLite. Idempotent and never destructive.

**It is the one writer that is not `Service`, so an invariant the service enforces does not hold
here unless it is repeated.** It supplies checkpoint keys directly, which is what lets it plant a
finished conversation nobody paid for, and is also what makes it the one place a contradictory
record can be written: it once seeded every fixture naming a repository while recording that it
reached no files, because settling lives in `Service.start` and nothing here goes through it.

So a rule about what a recorded `choice` may hold is a rule this file has to apply too. It calls
`Choice.settled()` rather than restating what a repository decides, which is what keeps that list in
one place: a field added to what a repository settles is settled here without an edit, and is the
whole reason `settled` is a method on `Choice` rather than a line in each of its three callers.

**Rebuild the demo database after any change to what a record holds** (`rm mainplate-demo.db*` then
`just seed`), or it keeps serving the old shape. That is the whole checkpoint and not only the
choice: the fixtures in `gallery.py` write every kind directly, so a shape change lands here as a
database full of values nothing can parse.

## `shoot.mjs`

The Node half of the screenshots, and the only reason `package.json` exists. A checkout therefore
pins two Chromiums: Playwright's Python and Node bindings each fetch their own.
