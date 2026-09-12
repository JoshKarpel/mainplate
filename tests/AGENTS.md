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

**Creating a session, saying the first thing in it, and answering its settings step are three
calls**, which `started` in `conftest.py` does together and `a_session` in `test_console.py` does
over HTTP. A test about the split posts to `/sessions` itself; every other test wants a session ready
to be looked at and should not have to know why that is three steps.

**A registration is the whole of what takes a session past the settings step**, and the turn count is
not read at all. So a suite running without a worker has to write one or every page it renders is
that step. `conftest.registered` is the one place that writes it, and `started` and `a_session` both
call it, which is why neither hands back a session drawing the step.

**It is write-once, so it has to be the first registration a session gets.** A card to draw goes to
`started` as `enrolled=`; supplied over the top of an empty set it records nothing and leaves the
rail empty with no failure to point at. For the same reason nothing writes a second one: `taken`
does not, because every session reaching it came through `a_session`.

**A fork needs one of its own**, written with the same `registered` and, where a test has navigated
to the branch, through `landed_on_the_branch` in `test_browser.py`, which parses the id off the url
and reloads. A branch carries its parent's turns and none of its plugins, so it holds a conversation
and still draws the step - the console working rather than a fixture to loosen.

**A test that wants a session's plugins actually running has to press the button and then run a
pass**, because that is now two moments: the press records the switches and asks for a pass, and the
pass is what runs `setup`. `set_up` in `test_plugins.py` does all three, and `loaded` in
`test_app.py` does it over HTTP. A test that skipped either would find a session refusing to answer,
which is the boundary working rather than a fixture to loosen.

**`passing` in `conftest.py` is how a suite with no worker drives one pass**, and it is there rather
than in one file because two suites want it: `test_console.py` presses the button through a route and
then has to run the pass the press asked for, which is the only way to assert what the press caused.

**A pass that sets plugins up needs `tendings`.** Which plugins are on is a column the *pass* reads
now, so `conversing` built without a way to read it sets up every declared plugin regardless of what
the switches said. A test asserting that a plugin left off was never launched must pass one.

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
`shoot.py` prints it beside the screenshot it is measuring and fails nothing, which is a diagnostic
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
- `TestTheSwitchOnATiersHeading` reads `checked` and `indeterminate` off the heading, because the
  server renders those once and the script sets them after that: a heading stuck on "some of them"
  over a group that is entirely on is correct markup, and the switches under it stay right, so
  nothing that reads the document would notice.

## Real processes and real clocks

`test_commands.py` runs real processes against a real worktree, and synchronises on the *record*
rather than on a clock: a command is run by a task nobody holds a handle to, so what a test waits
for is `result:{entry}` appearing. Any fixed sleep there is either racy or wasted, and the record is
the actual signal.

`test_plugins.py` runs the bundled plugins as real subprocesses over a real pipe, because a plugin
*is* a file this console runs: a fake answering in-process would exercise everything but the one
claim that matters. Its repository-tier tests need `bwrap` and fail loudly without it, for
`test_sandbox.py`'s reason - what they assert is what a mount namespace actually does.

**`TestThisRepositorysOwnPlugin` runs `.mainplate/pre-commit` and never installs anything.** What its
`setup` does for real is fetch - an interpreter, `pre-commit`, and a hook environment per entry in
the config - which is minutes on a cold cache and a dependency on an index, so a stub `pre_commit`
module goes on `PYTHONPATH` and the two runs it makes are driven by exit codes. The `uv run --script`
shebang still resolves the plugin's own dependency, so a cold machine pays for that once, like the
browsers.

**`TestWhatASetupActuallyReaches` runs setup plugins of its own through the real sandbox, and never
this repository's.** What `.mainplate/setup` here does is fetch a toolchain; what the suite asserts
is the mechanism - what it installs lands in the *session's* scratch, only the environment file
crosses back, a malformed line is loud, and the clone is still read-only in there.
`TestAPluginThatSetsTheRepositoryUp` is the switch and the record, driven through a real pass, and it
needs `tendings` for the reason above. Both write their plugin **into the worktree**, which is not
incidental: the namespace binds the tree, its clone and two scratches, so a script anywhere else is
one `bwrap` cannot find.

**What that stub cannot cover is asserted against arguments instead.**
`TestWhereARepositorysPluginRuns` reads the `bwrap` argv this console builds - the network on `setup`
and shut everywhere else, the session's scratch and the environment file on `setup` and nowhere else,
`$HOME` in the plugin's own scratch at every event including that one, a scratch per plugin per
session - because running it to find out would be the same assertions made slowly and over a network.

`test_snapshots.py` goes the whole way from a `Settings` with a relative database, because the other
fixtures there hand an absolute workspace root and so would never notice a path resolved against the
wrong directory. It parametrises over naming a base and naming nothing, because the no-base arm of
planting a worktree is the one that is easy to get wrong.
