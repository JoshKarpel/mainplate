---
description: "How the suite is driven: the in-memory client, the browser tests, and what a test has to write to have a turn."
---

# The suite

`just test` is mypy then pytest, and extra arguments go straight to pytest (`just test
tests/test_console.py::TestTheConsole`). Run it, or `just check`, before saying anything is done. CI
runs the same pre-commit configuration, so there is one definition of what the checks are.

`pytest` runs under `xdist` (`-n auto`), `pytest-randomly`, and a 10-second per-test timeout, all
from `addopts`. A test that needs longer raises it with `@pytest.mark.timeout(...)` rather than
changing the global; Playwright's retrying `expect` is capped well under it so a failed assertion
reports as itself rather than as a hang. `pythonpath = ["."]` is what puts `scripts/` on the path,
so the browser tests render the gallery with the same code `just gallery` runs.

## Driving the app

Tests drive it through `without-http`'s in-memory loopback client (`tests/calling.py`), so nothing
binds a port and the suite parallelizes; `Caller.watching` consumes a real event stream through the
same encoder and decoder a socket would, which is how the console tests see the transcript with no
document around it now that no endpoint serves one.

The `app` fixture deliberately runs the console over a store with **no worker**, so a test asserting
on a pending turn cannot race one. What the worker does is tested in `test_conversation.py`, a pass
at a time.

**A test that wants a turn writes the two records a pass would**, which is the cursor saying which
entry the turn took and the messages saying what came of it. The suites that run without a worker
have a helper apiece for that (`taken` and `answered` in `test_console.py`, `taking` in
`test_browser.py`, `said_at` in `conftest.py`), because a message that is only *delivered* is a
different state: it is queued, and the page draws it as a message waiting for a turn rather than as
one being answered.

**Creating a session and saying the first thing in it are two calls**, which `started` in
`conftest.py` does together. A test about the split posts to `/sessions` itself; every other test
wants a session with a message in it and should not have to know why that is two steps.

**A test that renders a session's *page* may also want a registration**, which is `registering` in
`test_browser.py`. A session that has registered no plugins is one whose setup pass has not finished,
so its page draws the settings step rather than a transcript - which is honest, and not what most of
these tests are about. It only matters where a page is being looked at: a session with a turn in it
is past that step whatever it registered.

**Every response fixture carries a timestamp.** `ModelResponse.timestamp` defaults to the moment it
was constructed, so a fixture without one is the moment the test ran, and an assertion over a whole
`Transcript` becomes a comparison against the wall clock.

## The browser tests

`test_browser.py` asks a different kind of question from the screenshots, mostly over the same
gallery. What a still cannot show is that *two* panels are drawn as where the reader is, or that a
form posts controls that sit outside it, so behaviour gets a real Chromium and its own assertions.

**It is in the suite rather than in a recipe of its own because a check nobody runs is a check that
catches nothing**: both of the bugs it first pinned were live while an equivalent script sat beside
it unrun. A browser that is not installed fails loudly rather than skipping, for the same reason.
So does a missing `bwrap` in `test_sandbox.py`, where every assertion is about what a mount
namespace actually does and a skipped one catches nothing.

**Sideways scroll is asserted here rather than in the shots, and that is the same lesson again.**
`shoot.mjs` prints it beside the screenshot it is measuring and fails nothing, which is a diagnostic
for somebody already looking; `TestTheShapeOfANarrowWindow` fails a build.

Its `console` fixture is the one thing there that leaves the gallery, and it has to. The gallery
proves how a conversation *renders*; what the live connection has to prove is that the page changes
when the checkpoint does, which is a second render arriving at a page nobody reloaded. So that
fixture serves the real app on a real port and hands the test the `Service` behind it, and the test
writes the steps a pass would write while the browser is looking, which is also the only way to hold
a turn half-finished long enough to assert on it. It has no worker either, for the same reason and
one more.

`working` is the same fixture with workspaces behind it, which the command box needs and nothing
else there does: a session must have a worktree before `Run` is offered at all. Two fixtures rather
than workspaces on the one, so a test that only drives a conversation does not get a clone and a
worktree it never looks at. The real repository they are both built on lives in `conftest.py`, since
two suites want one now.

It drives Playwright's **async** binding, which is not a preference: `sync_playwright` runs an event
loop on the calling thread, and this suite is already running one, so the sync API leaves every
browser test passing and every *other* async test failing its teardown. One session-scoped loop
(`pytestmark = pytest.mark.asyncio(loop_scope="session")`) so the browser can be session-scoped too.

## What has to be a browser, and why

A rule worth applying before writing an assertion: **where every rendering is a correct picture of
*some* page, what is wrong with the broken one is something no still and no markup assertion can
see.** These are the ones that turn on it:

- `TestTheGridMonospaceIsDrawnOn` draws a run of box drawing into a canvas and reads the pixels
  back, because the failure is a hairline.
- `TestTheBoxYouTypeIn` asserts how one box *changes* across what is put in it, since every height
  is a correct rendering of some box.
- `TestOpeningTheRecordBehindARequest` measures one element's box across a press, within its rule
  rather than within the window, because the page follows the end.
- `TestNamingAModeFromTheKeyboard` measures the message box across the press that enters a mode,
  because both layouts are correct markup.
- `TestShuttingAFoldFromItsFrame` asks where the frame stops and the output starts.
- `TestTheFocusRingHasRoomToBeDrawn` measures the gap against the ring's own `outline-width` and
  `outline-offset` rather than against a number written down twice, at a window narrower than the
  suite's own, since at 1400 the picker sits inside its `max-width` and the clipping does not
  happen.
- `TestWhatAFormPosts` and `TestFoldingAGroupOfCards` ask what `form.elements` holds, because the
  picker's controls are associated with their form by name rather than by nesting.
- `TestWhereTheComposerSendsTo`, `TestTurningTheBoxIntoACommandBox` and
  `TestWhereTheCursorIsAfterSending` pin htmx's and the browser's own behaviour, which looks
  identical in the markup either way.
- `TestWatchingATurnArrive` pins a second render reaching a page nobody reloaded.

## Real processes and real clocks

`test_commands.py` runs real processes against a real worktree, and synchronises on the *record*
rather than on a clock: a command is run by a task nobody holds a handle to, so what a test waits
for is `result:{entry}` appearing. Any fixed sleep there is either racy or wasted, and the record is
the actual signal.

`test_plugins.py` runs the bundled plugins as real subprocesses over a real pipe, because a plugin
*is* a file this console runs: a fake answering in-process would exercise everything but the one
claim that matters. Its repository-tier tests need `bwrap` and fail loudly without it, for
`test_sandbox.py`'s reason - what they assert is what a mount namespace actually does.

`test_snapshots.py` goes the whole way from a `Settings` with a relative database, because the other
fixtures there hand an absolute workspace root and so would never notice a path resolved against the
wrong directory. It parametrises over naming a base and naming nothing, because the no-base arm of
planting a worktree is the one that is easy to get wrong.
