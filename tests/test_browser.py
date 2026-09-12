from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import pytest_asyncio
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import FIXTURE_NAME
from conftest import LEASE
from conftest import already
from conftest import answered_with
from conftest import came_back
from conftest import recorded_turn
from conftest import registered
from conftest import run
from conftest import started
from playwright.async_api import Browser
from playwright.async_api import Locator
from playwright.async_api import Page
from playwright.async_api import Route
from playwright.async_api import ViewportSize
from playwright.async_api import async_playwright
from playwright.async_api import expect
from without_durability.interfaces import INBOX
from without_http import serving

from mainplate.agent import RETENTION
from mainplate.app import build_app
from mainplate.app import open_store
from mainplate.catalogue import Catalogues
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import Result
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import opened_key
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_result
from mainplate.conversation import recorded_steer
from mainplate.conversation import result_key
from mainplate.conversation import tool_key
from mainplate.forge import Workspaces
from mainplate.pages import CACHE_ID
from mainplate.pages import OPENING
from mainplate.plugins.asking import Declaring
from mainplate.plugins.installed import BUNDLED_ROOT
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.protocol import Described
from mainplate.plugins.running import Spawned
from mainplate.service import Service
from mainplate.sessions import read_tending
from mainplate.snapshots import Worktree
from scripts.gallery import pages
from scripts.gallery import write

# The gallery rather than a running console, which is the bargain `scripts/gallery.py` already
# makes: a page is a pure function of already-answered questions, so a browser needs no server, no
# database, no provider and no `config.yaml` to be shown exactly what a reader gets. What it does
# need is an *origin*, because the console's script keeps a reader's own decisions in
# `localStorage` and a `file://` page has nowhere to keep them, so the pages are served over HTTP.

# The async binding rather than the sync one, which is not a preference. `sync_playwright` drives an
# event loop of its own on the calling thread, and this suite is already running one: every test
# here passes, and every *async* test in the session then fails its teardown with "cannot run the
# event loop while another loop is running".
#
# One loop for the session, so the browser can be too. Launching Chromium costs more than everything
# it is then asked to do, and a session-scoped fixture bound to a per-test loop is an object held
# across loops that have already closed.
pytestmark = pytest.mark.asyncio(loop_scope="session")

# Wide enough that the rail, the sidebar and the transcript are all drawn: the layout collapses on a
# narrow window and half of what these drive would not be on the page.
VIEWPORT = ViewportSize(width=1400, height=1000)

# And a phone, which is the collapsed layout the width above exists to avoid. Comfortably under the
# stylesheet's 48rem, because what these ask is whether the narrow shape is the one it means to draw
# rather than where exactly it starts drawing it.
PHONE = ViewportSize(width=390, height=844)

# Every page the gallery renders, named at collection so each is a test of its own rather than a
# loop that can only fail at the first one to break. Asked of `pages()` rather than listed here,
# which is what stops a page added later from being one nothing measures; it is pure, so the render
# nobody looks at costs only itself.
EVERY_PAGE = tuple(sorted(pages()))

# Every panel drawn as *where the reader is*, however they got there. The stylesheet draws `:target`
# and `data-landed` alike deliberately - a landing and an arrival by link should read the same - so
# the invariant worth pinning is over both at once rather than over either alone.
LANDED = ".panel:target, .panel[data-landed]"

# How long a retrying assertion waits before calling it. Well under the suite's own per-test timeout,
# and deliberately: a test making two of these and failing both would otherwise be killed by the
# timeout and report as a hang rather than as the assertion that actually failed. Three seconds is a
# long time for a static page a loopback server just handed over.
expect.set_options(timeout=3_000)


@pytest.fixture(scope="session")
def gallery(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """
    Every page, rendered and served, as the base URL to point a browser at.

    Port zero, so a suite running beside a console, or beside another `xdist` worker's copy of
    itself, takes whatever the kernel hands it rather than fighting over a number somebody chose.
    Rendered once for the session because the pages are values: nothing a test does can change what
    is on disk.
    """
    into = tmp_path_factory.mktemp("gallery")
    write(into)
    serving = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(into)))
    thread = Thread(target=serving.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{serving.server_address[1]}"
    finally:
        serving.shutdown()
        thread.join()
        serving.server_close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def browser() -> AsyncIterator[Browser]:
    """
    One Chromium for the session, since launching one costs more than everything it is asked to do.

    A browser that is not installed fails loudly rather than skipping. These are the only checks
    that can see what a markup assertion cannot, so a suite that quietly went green without them
    would be reporting the very thing they exist to deny; Playwright's own message names the command
    that installs it, and `just setup` runs that command.
    """
    async with async_playwright() as driving:
        launched = await driving.chromium.launch()
        try:
            yield launched
        finally:
            await launched.close()


@pytest_asyncio.fixture(loop_scope="session")
async def console(tmp_path: Path, catalogues: Catalogues) -> AsyncIterator[tuple[str, Service]]:
    """
    The real console on a real port, with the store it reads handed back beside it.

    The gallery cannot answer what these need. A page is a pure function of a checkpoint, so a still
    of one proves how a conversation *renders*; what a live connection has to prove is that the page
    changes when the checkpoint does, and nothing on disk changes. So this is a running server,
    a browser pointed at it, and a `Service` a test writes through to make the thing it is watching
    for actually happen.

    No worker, for the reason the console tests have none: a pass answering the session at a moment
    no test chose would make every assertion here a race against how fast the machine is. What a
    turn records, a test records itself, a step at a time - which is also the only way to hold a
    turn half-finished for long enough to look at it.
    """
    async with open_store(tmp_path / "mainplate.db", LEASE, catalogues) as opened:
        # A console that can answer a plugin's card without ever running one, which is what these
        # tests are about: what is under test is the console's own drawing of a declaration and the
        # writes a press makes, and the plugin behind `CARDED` wants no `action`, so `speaking` is
        # never reached. It is supplied all the same and would fail loudly if it were, since a
        # console that quietly did nothing here would pass these tests while doing nothing.
        service = replace(opened, declaring=Declaring(speaking=Spawned(environ={})))
        async with serving(build_app(already(service)), port=0) as server:
            yield f"http://{server.host}:{server.port}", service


@pytest_asyncio.fixture(loop_scope="session")
async def page(browser: Browser) -> AsyncIterator[Page]:
    """
    A page in a context of its own, so what one test leaves in `localStorage` cannot reach another.

    The scoping matters here for the reason it matters in the console itself: every page is one
    origin, so a shared context would let one test's folds, theme and muted kinds decide what
    the next test renders.
    """
    context = await browser.new_context(viewport=VIEWPORT)
    try:
        yield await context.new_page()
    finally:
        await context.close()


@pytest_asyncio.fixture(loop_scope="session")
async def phone(browser: Browser) -> AsyncIterator[Page]:
    """The same thing on a phone, in a context of its own for the reason `page` is."""
    context = await browser.new_context(viewport=PHONE)
    try:
        yield await context.new_page()
    finally:
        await context.close()


@pytest_asyncio.fixture(loop_scope="session")
async def unscripted(browser: Browser) -> AsyncIterator[Page]:
    """
    A page with scripting off, for the parts that have to work without it.

    Its own context because `javaScriptEnabled` is a context setting rather than a page one, and it
    is the only fixture here that wants the console's own file *not* to run.
    """
    context = await browser.new_context(viewport=VIEWPORT, java_script_enabled=False)
    try:
        yield await context.new_page()
    finally:
        await context.close()


async def taking(service: Service, session: str, turn: int = 0) -> None:
    """
    The next message a session has waiting, taken into a turn of its own, with no pass to do it.

    Which is a pass's first act, written by hand because the `console` fixture runs no worker: a
    test writing a turn's model steps needs a turn for them to be steps *of*, and until a message is
    taken it is only queued.
    """
    recorded = await service.checkpointer.load(session)
    await service.checkpointer.supply(
        session, opened_key(turn), [key for key in recorded if key.startswith(INBOX)][turn]
    )


# A plugin with a card of both kinds of control, which is what the rail draws and what the card tests
# press. Written out rather than taken from the bundled handoff, because what these assert is the
# *rendering* of a declaration: a fixture that changed when a bundled plugin's copy changed would be
# a test that fails for a reason nobody reading it would expect.
CARDED = Enrolled(
    # Pointed at the plugin this repository actually ships, because two of the tests below press its
    # answer in the composer and what has to run then is the real script over a real pipe. The
    # declaration is written out all the same: what the card tests assert is the console's *rendering*
    # of a declaration, and a fixture that changed when the bundled plugin's copy changed would be a
    # test failing for a reason nobody reading it would expect.
    installed=Installed(tier=Tier.BUNDLED, name="handoff", path=BUNDLED_ROOT / "handoff"),
    described=Described.model_validate(
        {
            "events": ["tool", "after_turn", "compose"],
            "answers": [
                {
                    "leader": "handoff",
                    "saying": "Have it write down where it has got to and carry on from that",
                    "demands": False,
                }
            ],
            "card": {
                "heading": "handoff",
                "rows": [
                    {"switch": {"name": "hands_off", "label": "auto at reserve", "default": True}},
                    {"number": {"name": "reserve", "label": "reserve", "unit": "K", "default": 40, "least": 8}},
                ],
            },
        }
    ),
)


async def showing_model(page: Page) -> str:
    """The id on the one model card a shut group is drawn as."""
    return str(
        await page.evaluate("() => document.querySelector('.model:has(.model__pick:checked) .model__id').textContent")
    )


async def posted(page: Page, field: str) -> str:
    """What the form would actually submit for one field, which is the browser's answer, not markup's."""
    return str(await page.evaluate(f"() => new FormData(document.querySelector('form#choosing')).get({field!r})"))


async def posted_model(page: Page) -> str:
    return await posted(page, "model")


async def opened(page: Page, toggle: str) -> bool:
    """Whether one group is folded open, asked of the checkbox that decides it."""
    return bool(await page.evaluate(f"() => document.getElementById({toggle!r}).checked"))


async def lands_on(page: Page, *expected: str) -> None:
    """
    Assert that exactly these panels are drawn as where the reader is, in this order.

    Through `expect`, which retries, rather than a wait and a read: the projection is reapplied by a
    script the browser runs when it is ready to, so asserting once is asserting against whichever
    moment the assertion happened to land in.
    """
    landed = page.locator(LANDED)
    await expect(landed).to_have_count(len(expected))
    for at, panel in enumerate(expected):
        await expect(landed.nth(at)).to_have_attribute("id", panel)


class TestWhereTheReaderIs:
    """
    At most one panel is ever drawn as where the reader is, however they arrived at it.

    A behaviour rather than an appearance, and that is why it is here rather than in a screenshot:
    two highlighted panels look exactly like one highlighted panel plus one the reader scrolled
    past. It is invisible to a markup assertion too, because `:target` is a live pseudo-class the
    server never renders and `data-landed` is written by a script.
    """

    async def test_a_fresh_load_lands_on_nothing(self, page: Page, gallery: str) -> None:
        # The one case the script cannot change: with no hash it marks nothing, so this pins what
        # the server rendered. Everything below is what the script does on top of it.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await lands_on(page)

    async def test_following_a_panels_permalink_lands_on_it(self, page: Page, gallery: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('a.panel__anchor[href="#panel-0-1"]')
        await lands_on(page, "panel-0-1")

    async def test_reloading_on_a_hash_lands_where_it_names(self, page: Page, gallery: str) -> None:
        await page.goto(f"{gallery}/session.html#panel-0-1", wait_until="load")
        await lands_on(page, "panel-0-1")

    async def test_a_second_permalink_after_a_reload_moves_the_landing(self, page: Page, gallery: str) -> None:
        # The sequence from the report, and the one that used to draw two: a reload leaves the
        # script holding the hash it started on, and following a link moves `:target` without going
        # through the code that set it. The stylesheet draws them alike, so the page showed two
        # panels highlighted and nothing said which one the reader was on.
        await page.goto(f"{gallery}/session.html#panel-0-1", wait_until="load")
        await lands_on(page, "panel-0-1")
        await page.click('a.panel__anchor[href="#panel-0-4"]')
        await lands_on(page, "panel-0-4")

    async def test_stepping_the_dock_lands_on_exactly_one_panel(self, page: Page, gallery: str) -> None:
        # The dock lands without navigating, so `:target` never follows it. Which panel it reaches
        # is the dock's business; that it reaches exactly one is this invariant.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('button[data-step="1"][data-stop="panel"]:not([data-side])')
        await expect(page.locator(LANDED)).to_have_count(1)

    async def test_leaping_to_the_start_lands_on_the_rule_that_opens_the_first_turn(
        self, page: Page, gallery: str
    ) -> None:
        # The top of a conversation is that rule and not the first panel under it: the rule carries
        # the turn's own facts and its fork link, and where the stretch has instructions there is a
        # system prompt panel between the two, so landing on the panel left both above the reader.
        # Asked of a browser because the two landings are the same markup and differ only in which
        # element the script chose.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('button[data-leap="start"]')
        landed = page.locator(".rule[data-landed]")
        await expect(landed).to_have_count(1)
        await expect(landed).to_have_attribute("id", "rule-0")
        await expect(page.locator(LANDED)).to_have_count(0)

    async def test_stepping_by_turn_lands_on_a_rule_and_not_on_a_panel(self, page: Page, gallery: str) -> None:
        # The coarse column steps the boundaries rather than the messages, which is the whole of
        # what it is for: a reader stepping turns wants the line carrying the fork and what the turn
        # cost, not the first few words under it. Both halves are asserted, because landing on the
        # rule *and* on a panel would be the two-highlight bug in a new place.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('button[data-step="1"][data-stop="turn"]')
        await expect(page.locator(".rule[data-landed]")).to_have_count(1)
        await expect(page.locator(LANDED)).to_have_count(0)

    async def test_stepping_by_turn_steps_turns_and_not_the_requests_within_one(self, page: Page, gallery: str) -> None:
        # A rule stands at every model request now, so the selector this column steps has to be
        # `rule--turn` rather than every rule, or a turn with several round trips in it gives the
        # coarse column several stops and stops being the coarse column. The fixture's first turn
        # took two requests, which is what makes this asked here rather than assumed.
        #
        # From the *start*, because the page opens following the end and one step from there lands on
        # the last stop whichever selector is in force, so the assertion would hold with the bug in.
        #
        # The start is turn 0's own rule, which is the top of the transcript, so one step from there
        # is the next stop *below* it: `rule-1` with this column's selector and `rule-0-1` with every
        # rule, which is what makes the one assertion discriminate rather than merely hold.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        assert await page.locator(".rule").count() > await page.locator(".rule--turn").count()
        await page.click('button[data-leap="start"]')
        landed = page.locator(".rule[data-landed]")
        await page.click('button[data-step="1"][data-stop="turn"]')
        await expect(landed).to_have_count(1)
        await expect(landed).to_have_attribute("id", "rule-1")

    async def test_stepping_by_forget_lands_on_the_boundary_and_not_on_every_turn(
        self, page: Page, gallery: str
    ) -> None:
        # The column steps where the model's history starts again, which is one stop in a fixture
        # with several turns in it. Asserted against the turn column beside it, because "lands on a
        # rule" would hold for both and the whole point is that this one lands on fewer.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('button[data-leap="start"]')
        await page.click('button[data-step="1"][data-stop="forget"]')
        landed = page.locator(".rule[data-landed]")
        await expect(landed).to_have_count(1)
        await expect(landed).to_have_class(re.compile(r"\brule--forget\b"))

    async def test_the_forget_column_is_drawn_in_a_session_that_has_never_forgotten(
        self, page: Page, gallery: str
    ) -> None:
        # The rail lives outside the region that swaps, so a column that appeared with the first
        # forget would not appear until a reload. Drawn always and stepping nothing is the honest
        # shape, and it is what lets one recorded mid-session be reachable at once.
        await page.goto(f"{gallery}/stalled.html", wait_until="load")
        await expect(page.locator(".rule--forget")).to_have_count(0)
        await expect(page.locator('button[data-stop="forget"]')).to_have_count(2)

    async def test_a_permalink_followed_after_stepping_moves_the_landing(self, page: Page, gallery: str) -> None:
        # The other half of the same disagreement, arrived at the other way round: the dock marks a
        # panel the URL does not name, and then the URL names a different one.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('button[data-step="1"][data-stop="panel"]:not([data-side])')
        await expect(page.locator(LANDED)).to_have_count(1)
        await page.click('a.panel__anchor[href="#panel-0-2"]')
        await lands_on(page, "panel-0-2")


# What each page's form has to carry for the handler on the other end to read a request at all:
# `parse_form_start` refuses a message naming no endpoint and no model, and `parse_form_fork`
# refuses one naming no turn as well.
CHOOSING = (
    # No `prompt` on the start page: creating a session and saying the first thing in it are two
    # steps, so this form decides what a session *is* and the box is on the session's own page.
    ("start.html", frozenset({"endpoint", "model", "thinking"})),
    ("forking.html", frozenset({"at", "prompt", "endpoint", "model", "thinking"})),
)


class TestWhatAFormPosts:
    """
    The form that starts a session carries the whole of what a session is chosen by.

    A behaviour rather than markup, because the failure is invisible in the markup: a picker
    rendered *outside* the form it posts to looks exactly like one rendered inside it, and every
    control on it is correct in isolation. What decides it is form association, which is the
    browser's own answer and no string's - on the start page the choosing fills `main`'s growing row
    with the box pinned under it, so the controls are siblings of the form rather than children and
    the only thing submitting them is the `form` attribute naming it.
    """

    @pytest.mark.parametrize(("name", "carrying"), CHOOSING)
    async def test_the_form_carries_every_field_its_handler_requires(
        self, page: Page, gallery: str, name: str, carrying: frozenset[str]
    ) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        # `form.elements` is exactly the set the browser would submit, however the association was
        # made, which is the point: asking the DOM for the controls *inside* the form would pass on
        # the arrangement this is here to refuse.
        associated = await page.evaluate(
            "() => [...document.querySelector('form#choosing').elements].map((element) => element.name)"
        )
        assert carrying <= set(associated)

    @pytest.mark.parametrize(("name", "carrying"), CHOOSING)
    async def test_the_form_is_submittable_the_moment_it_loads(
        self, page: Page, gallery: str, name: str, carrying: frozenset[str]
    ) -> None:
        # Association is not enough on its own: a radio group that is associated but has nothing
        # checked posts no field at all, and the picker's whole promise is that the choice is
        # already made. So this asserts what would be *posted* rather than what is associated, which
        # are the same question only when both hold.
        await page.goto(f"{gallery}/{name}", wait_until="load")
        posted = await page.evaluate("() => [...new FormData(document.querySelector('form#choosing')).keys()]")
        assert carrying <= set(posted)


# Record every form the page tries to submit, and let none of it leave: the gallery is static files,
# so an actual post would be a 404 rather than an answer. What is being asked is whether the box
# asked its form to submit at all, which is the whole of what a key press is responsible for.
WATCH_SUBMITS = """
() => {
    window.submitted = [];
    document.addEventListener("submit", (event) => {
        window.submitted.push(event.target.className);
        event.preventDefault();
    });
}
"""

# Every page with a box to type a message into, and the form each one belongs to.
BOXES = (("session.html", "composer"), ("forking.html", "forking"))


class TestSendingFromTheKeyboard:
    """
    Shift-Enter sends the message; plain Enter breaks the line.

    That way round because a message here is prose that often wants a second paragraph and a fenced
    block, and a box where the obvious key sends is a box you cannot write one in without learning a
    second key first. Both halves are checked, because a wiring that sent on *every* Enter would
    pass a test that only asserted the sending one.
    """

    @pytest.mark.parametrize(("name", "form"), BOXES)
    async def test_shift_enter_asks_the_form_to_submit(self, page: Page, gallery: str, name: str, form: str) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        await page.evaluate(WATCH_SUBMITS)
        await page.fill("textarea[name=prompt]", "does shift-enter send?")
        await page.press("textarea[name=prompt]", "Shift+Enter")
        assert await page.evaluate("() => window.submitted") == [form]

    @pytest.mark.parametrize(("name", "form"), BOXES)
    async def test_plain_enter_breaks_the_line_rather_than_sending(
        self, page: Page, gallery: str, name: str, form: str
    ) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        await page.evaluate(WATCH_SUBMITS)
        await page.fill("textarea[name=prompt]", "one line")
        await page.press("textarea[name=prompt]", "Enter")
        assert await page.evaluate("() => window.submitted") == []
        assert await page.input_value("textarea[name=prompt]") == "one line\n"

    async def test_shift_enter_in_an_empty_box_sends_nothing(self, page: Page, gallery: str) -> None:
        # The composer's box is `required`, and `requestSubmit` honours that where `submit` would
        # not: an empty box refuses from the keyboard exactly as it refuses from the button, rather
        # than recording a message nobody typed.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.evaluate(WATCH_SUBMITS)
        await page.press("textarea[name=prompt]", "Shift+Enter")
        assert await page.evaluate("() => window.submitted") == []


BOX = "textarea[name=prompt]"


async def box_height(page: Page, text: str) -> float:
    """How tall the box is drawn with that text in it, typed rather than assigned."""
    await page.fill(BOX, text)
    return float(await page.evaluate(f"() => document.querySelector({BOX!r}).getBoundingClientRect().height"))


async def box_cap(page: Page) -> float:
    """The height the box stops growing at, in pixels, asked of the browser rather than restated."""
    return float(await page.evaluate(f"() => parseFloat(getComputedStyle(document.querySelector({BOX!r})).maxHeight)"))


async def box_scrolls(page: Page) -> bool:
    return bool(
        await page.evaluate(
            f"() => {{ const box = document.querySelector({BOX!r}); return box.scrollHeight > box.clientHeight; }}"
        )
    )


class TestTheBoxYouTypeIn:
    """
    One line at rest, a line taller for each line typed, and a cap it scrolls inside rather than
    passes.

    A still cannot show any of this: every height here is a correct rendering of *some* box, and what
    is asserted is how one box changes across what is put in it. The growth is `field-sizing:
    content`, which makes it the browser's behaviour rather than ours - the other reason to drive it,
    since `rows` is still in the markup as the floor for a browser without the property, so one that
    ignored it would render a three-line box and look entirely deliberate.
    """

    @pytest.mark.parametrize("name", [name for name, _ in BOXES])
    async def test_the_box_starts_at_one_line_and_grows_a_line_at_a_time(
        self, page: Page, gallery: str, name: str
    ) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        empty = await box_height(page, "")
        one, two, three = [await box_height(page, "\n".join(f"line {n}" for n in range(lines))) for lines in (1, 2, 3)]

        assert empty == pytest.approx(one), "a box with nothing in it is a box with one line in it"
        # Equal steps are what say the first line is the *first*: a three-line floor would swallow
        # two of these and only start growing at the third, so the steps would not match.
        assert two - one == pytest.approx(three - two)
        assert three > two > one

    @pytest.mark.parametrize("name", [name for name, _ in BOXES])
    async def test_a_long_message_stops_at_the_cap_and_scrolls_inside_the_box(
        self, page: Page, gallery: str, name: str
    ) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        cap = await box_cap(page)
        tall = await box_height(page, "\n".join(f"line {n}" for n in range(60)))

        assert tall == pytest.approx(cap)
        assert await box_scrolls(page), "past the cap the box scrolls rather than the page"
        # And the cap leaves the window to whatever it is under, which is the half of it a viewport
        # term rather than a count of lines is there to promise.
        assert cap < VIEWPORT["height"] / 2


SHOWING = "(selector) => [...document.querySelectorAll(selector)].filter((each) => each.offsetParent !== null).length"

# The narrowing box of the thinking group, which is the one used to drive these: eight short names
# with one a prefix of another (`high`, `xhigh`), which is exactly the case worth pinning.
THINKING_FILTER = ".picker__part:has(#open-thinking) .picker__filter-field"


class TestFoldingAGroupOfCards:
    """
    A shut group is the card that is picked, and it is still the card the form posts.

    Nothing here is visible to a markup assertion, which is the whole reason it drives a browser:
    every card is in the document either way, and what decides whether one is *drawn* is a CSS
    `:has()` rule reading the radio beside it. Rendered markup looks identical shut and open.

    The last of these is the one that would hurt. The collapse hides every card but the checked one,
    so a rule that stopped exempting the checked card would hide the only radio that is on, and the
    form would post no model at all - a page refusing itself with a 422, from a stylesheet edit.
    """

    @pytest.mark.parametrize("name", ["start.html", "forking.html"])
    async def test_a_group_shows_only_what_is_picked_until_it_is_opened(
        self, page: Page, gallery: str, name: str
    ) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        # Every kind of card, because all four groups are one component now: a picker that stopped
        # folding would be a regression in `choosing` rather than in any one of them.
        for card in (".model", ".endpoint", ".think"):
            assert await page.evaluate(SHOWING, card) == 1, card

        await page.click('label[for="open-model"]')
        # More than one, rather than a count: what the fixture catalogue offers is not this test's
        # business, only that opening the group stops hiding the rest of it.
        assert await page.evaluate(SHOWING, ".model") > 1
        # And opening one group leaves the others exactly as they were.
        assert await page.evaluate(SHOWING, ".endpoint") == 1
        assert await page.evaluate(SHOWING, ".think") == 1

    async def test_a_group_counts_the_cards_it_actually_holds(self, page: Page, gallery: str) -> None:
        # `names` feeds the count, the `<datalist>` completions and the filter from one list, so the
        # three cannot disagree - which is the invariant, and which a group given a model's label
        # *and* its routed id broke by announcing 54 options over 27 cards.
        await page.goto(f"{gallery}/start.html", wait_until="load")
        groups = await page.evaluate(
            "() => [...document.querySelectorAll('.picker__part')].map((part) => ({"
            " legend: part.querySelector('.picker__legend').textContent,"
            " said: Number((part.querySelector('.picker__more--shut').textContent.match(/\\d+/) || [0])[0]),"
            " cards: part.querySelectorAll('label:has(input[type=radio])').length,"
            " listed: part.querySelector('datalist').options.length }))"
        )
        assert groups
        for group in groups:
            assert group["said"] == group["cards"] == group["listed"], group

    async def test_picking_folds_the_group_back_to_what_was_just_picked(self, page: Page, gallery: str) -> None:
        await page.goto(f"{gallery}/start.html", wait_until="load")
        await page.click('label[for="open-model"]')
        await page.locator(".model").nth(1).click()
        # Shut again without anybody pressing the control, which is the script's one contribution
        # here, and showing the card just chosen rather than the one the server rendered as picked.
        assert await page.evaluate(SHOWING, ".model") == 1
        assert await showing_model(page) == await posted_model(page)

    async def test_the_fold_needs_no_script_at_all(self, unscripted: Page, gallery: str) -> None:
        # The reason the closed state is drawn by `:has()` rather than by a summary the script keeps
        # in step. With no script the group cannot fold itself on a pick, so it stays open - but the
        # card it collapses to is read off the radio, so shutting it by hand shows what was picked
        # rather than what the server rendered. A summary would name the wrong model here.
        await unscripted.goto(f"{gallery}/start.html", wait_until="load")
        assert await unscripted.evaluate(SHOWING, ".model") == 1

        await unscripted.click('label[for="open-model"]')
        assert await unscripted.evaluate(SHOWING, ".model") > 1

        await unscripted.locator(".model").nth(1).click()
        await unscripted.click('label[for="open-model"]')
        assert await unscripted.evaluate(SHOWING, ".model") == 1
        assert await showing_model(unscripted) == await posted_model(unscripted)

    async def test_naming_an_option_exactly_picks_it_and_shuts_the_group(self, page: Page, gallery: str) -> None:
        # Taking an entry from the browser's completion menu puts the whole name in the box, and
        # that is the reader having chosen: leaving them to reach for the one card still showing is
        # a step they already took.
        await page.goto(f"{gallery}/start.html", wait_until="load")
        await page.click('label[for="open-thinking"]')
        await page.locator(THINKING_FILTER).fill("xhigh")
        assert await posted(page, THINKING_FIELD) == "xhigh"
        assert await opened(page, "open-thinking") is False

    async def test_typing_toward_a_longer_name_picks_nothing_on_the_way(self, page: Page, gallery: str) -> None:
        # Only ever an exact match on a whole name, so the keystrokes spelling `xhigh` do not stop
        # at `high` - and a partial name narrows the group rather than choosing from it. Compared
        # against what the page loaded with rather than a named level, because what is posted is
        # the level's *name* and the fixture's default is the level itself.
        await page.goto(f"{gallery}/start.html", wait_until="load")
        before = await posted(page, THINKING_FIELD)
        await page.click('label[for="open-thinking"]')
        for typed in ("h", "hi", "hig"):
            await page.locator(THINKING_FILTER).fill(typed)
        assert await posted(page, THINKING_FIELD) == before
        assert await opened(page, "open-thinking") is True

    @pytest.mark.parametrize("name", ["start.html", "forking.html"])
    async def test_a_shut_group_still_posts_its_choice(self, page: Page, gallery: str, name: str) -> None:
        await page.goto(f"{gallery}/{name}", wait_until="load")
        posted = await page.evaluate("() => [...new FormData(document.querySelector('form#choosing')).keys()]")
        assert {"endpoint", "model"} <= set(posted)


class TestTheShapeOfANarrowWindow:
    """
    A phone gets one column, and no page pushes the document sideways to get it.

    Here rather than in `scripts/shoot.py`, which prints the same overflow beside the screenshot it
    is measuring and fails nothing: that is a diagnostic for somebody already looking at a shot, and
    the layout it is measuring is a deliverable that regresses without anybody looking. It is the
    reason the rest of this module exists, applied once more.

    Two questions rather than one, because overflow alone can be right for the wrong reason. The
    stylesheet's two breakpoints overlap and every session page matched both, so the rail's own
    `max-width: 78rem` block put the 17rem sidebar column back under the narrow one and left the
    conversation about a hundred pixels to render in. Whether the shell is *one track* is what
    catches that directly; whether the document scrolls sideways is what catches the ways it shows.
    """

    @pytest.mark.parametrize("name", EVERY_PAGE)
    async def test_no_page_pushes_the_document_sideways(self, phone: Page, gallery: str, name: str) -> None:
        await phone.goto(f"{gallery}/{name}", wait_until="load")
        # The transcript may scroll its own wide blocks and the session list scrolls inside its own
        # card; what must never move is the document, which has nowhere to overflow to.
        room = await phone.evaluate(
            "() => ({ document: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth })"
        )
        assert room["document"] <= room["viewport"]

    async def test_a_page_with_a_rail_is_still_one_column(self, phone: Page, gallery: str) -> None:
        # A session page is the one that carries a rail, so it is the one that matched both
        # breakpoints and the only one where the columns could disagree with the width.
        await phone.goto(f"{gallery}/session.html", wait_until="load")
        columns = await phone.evaluate("() => getComputedStyle(document.querySelector('.shell')).gridTemplateColumns")
        # The used value of the one track rather than a count of them, so this says the whole width
        # goes to the conversation. A count alone is satisfied by `none`, which is what a shell that
        # had stopped being a grid at all would report.
        assert columns.split() == [f"{PHONE['width']}px"]


class TestTheSessionListOnAPhone:
    """
    The session list lies off the left edge of a phone until its clasp is pressed, as the rail does
    off the right, and only one of the two is out at a time.

    A browser because both states are correct markup: the list is on the page whether it is parked
    or slid out, and what changes is a transform and a visibility that only a rendering resolves.
    """

    async def test_the_list_is_shut_until_its_clasp_is_pressed(self, phone: Page, gallery: str) -> None:
        await phone.goto(f"{gallery}/session.html", wait_until="load")
        start = phone.locator(".sessions .start")
        await expect(start).to_be_hidden()
        await phone.locator(".sessions__clasp").click()
        # In the viewport rather than merely visible, because visibility flips at the start of the
        # slide and the list is still off the edge for a fifth of a second after it.
        await expect(start).to_be_in_viewport()

    async def test_opening_the_list_shuts_the_rail(self, phone: Page, gallery: str) -> None:
        await phone.goto(f"{gallery}/session.html", wait_until="load")
        await phone.locator(".rail__clasp").click()
        await expect(phone.locator(".search")).to_be_visible()
        await phone.locator(".sessions__clasp").click()
        await expect(phone.locator(".sessions .start")).to_be_visible()
        await expect(phone.locator(".search")).to_be_hidden()
        assert await phone.get_attribute(".rail__clasp", "aria-expanded") == "false"

    async def test_escape_shuts_the_list(self, phone: Page, gallery: str) -> None:
        await phone.goto(f"{gallery}/session.html", wait_until="load")
        await phone.locator(".sessions__clasp").click()
        await expect(phone.locator(".sessions .start")).to_be_visible()
        await phone.keyboard.press("Escape")
        await expect(phone.locator(".sessions .start")).to_be_hidden()

    async def test_the_rail_scrolls_itself_while_out(self, phone: Page, gallery: str) -> None:
        # The rail is fixed to the viewport, so nothing else can scroll it, and on a phone it is
        # taller than the window: shut, its cards were cut off at the bottom with no way to them.
        await phone.goto(f"{gallery}/session.html", wait_until="load")
        await phone.locator(".rail__clasp").click()
        await expect(phone.locator(".search")).to_be_visible()
        scrolled = await phone.evaluate(
            "() => { const rail = document.querySelector('.rail'); rail.scrollTop = 10000; return rail.scrollTop; }"
        )
        assert scrolled > 0

    async def test_a_wide_window_draws_no_clasp(self, page: Page, gallery: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await expect(page.locator(".sessions__clasp")).to_be_hidden()
        await expect(page.locator(".sessions .start")).to_be_visible()


# Short enough that the choosing with a group open is longer than the window, which is the whole
# point of it: at the suite's own thousand the start page fits and a layout that had stopped bounding
# anything still looks right.
SHORT = ViewportSize(width=1400, height=620)


class TestWhatScrollsOnTheStartPage:
    """
    The choosing scrolls inside itself, the model list is what gives, and the document never moves.

    A browser, and for `TestTheShapeOfANarrowWindow`'s reason one axis over: every rendering of this
    is a correct picture of *some* page, and what is wrong when it breaks is that the page grew past
    the window. The symptom a reader meets is not the growth. `.models` is bounded by the room left,
    so a block that never took a bound leaves it at its full height - a scroll container with nothing
    to scroll - and a wheel anywhere over the model list then moves nothing at all.
    """

    async def test_the_choosing_takes_the_window_rather_than_growing_past_it(self, page: Page, gallery: str) -> None:
        await page.set_viewport_size(SHORT)
        await page.goto(f"{gallery}/start.html", wait_until="load")
        await page.click('label[for="open-model"]')

        room = await page.evaluate(
            "() => ({ document: document.documentElement.scrollHeight,"
            " viewport: document.documentElement.clientHeight })"
        )
        assert room["document"] <= room["viewport"]

    async def test_the_model_list_is_the_part_that_gives(self, page: Page, gallery: str) -> None:
        await page.set_viewport_size(SHORT)
        await page.goto(f"{gallery}/start.html", wait_until="load")
        await page.click('label[for="open-model"]')

        # Bounded by the room left, so it holds more than it shows. The negation is the broken shape:
        # a list at its content height reports these equal and scrolls nowhere.
        held = await page.evaluate(
            "() => { const models = document.querySelector('.models');"
            " return { holds: models.scrollHeight, shows: models.clientHeight }; }"
        )
        assert held["holds"] > held["shows"]

    async def test_a_wheel_over_the_model_list_reaches_the_end_of_the_list_and_then_the_page(
        self, page: Page, gallery: str
    ) -> None:
        """
        What a reader actually does, and the assertion the two above exist to explain.

        Both ends of one gesture: the list moves, and once it has nowhere left to go the block around
        it takes the rest. `overscroll-behavior: contain` here made the second half of that a wall,
        so a wheel that started over the models could not reach the questions under them.
        """
        await page.set_viewport_size(SHORT)
        await page.goto(f"{gallery}/start.html", wait_until="load")
        await page.click('label[for="open-model"]')
        over = await page.locator(".models").bounding_box()
        assert over is not None
        await page.mouse.move(over["x"] + over["width"] / 2, over["y"] + 20)

        for _ in range(12):
            await page.mouse.wheel(0, 300)

        moved = await page.evaluate(
            "() => ({ models: document.querySelector('.models').scrollTop,"
            " choosing: document.querySelector('.setup').scrollTop })"
        )
        assert moved["models"] > 0
        assert moved["choosing"] > 0


# Where a conversation draws monospace, which is the fenced blocks a model answers in and the body of
# every tool call it makes. Both are on one grid, and both have to be, because a read's gutter is a
# column of box drawing and it arrives in the second.
MONOSPACE = (".text pre code", ".tool__body pre")

# Three rows of box drawing, which is a corner reaching down, a bar reaching both ways, and a corner
# reaching up. The corners are here because they reach one way only and so have the least ink to
# join with; the run is drawn as one column because that is what a read's gutter is.
JOINING = ("┌", "│", "└")


class TestTheGridMonospaceIsDrawnOn:
    """
    Box drawing joins into lines rather than into dashes.

    A model answers in tables and trees, and every `read` comes back as lines behind a `│` gutter, so
    this is most of what a panel in this console ever shows. Two rows of it join on two conditions:
    the row pitch is no more than the span of the glyph's own ink, and the pitch is a whole number of
    pixels, or each row lands on a different subpixel phase and the joins falling between two device
    rows draw as two half-lit ones.

    A browser, because neither is visible to a markup assertion and neither is visible in a still
    either: every one of these renderings is a correct picture of *some* grid, and what is wrong with
    the broken one is a hairline.

    The join is asked by drawing it and reading the pixels back, rather than by comparing the pitch
    against a number. `measureText` looks like the number to compare against and is not: it reports
    a box Chromium clamps to the line, 16px where the same glyph rasterises 20px of ink, so an
    assertion built on it demands a pitch four pixels tighter than the one that actually joins.
    """

    async def test_the_vendored_face_is_what_a_conversation_is_drawn_in(self, page: Page, gallery: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.evaluate("() => document.fonts.ready")
        # `check` asks whether the face is loaded and usable, which a stack naming it cannot: with
        # the file missing every assertion below still passes against whatever the reader has.
        assert await page.evaluate("""() => document.fonts.check('13px "JuliaMono"')""") is True
        drawn = await page.evaluate(
            "(where) => where.map(one => getComputedStyle(document.querySelector(one)).fontFamily)", list(MONOSPACE)
        )
        # Unquoted, because a computed `font-family` quotes a name only where one is needed.
        assert [family.split(",")[0].strip('"') for family in drawn] == ["JuliaMono"] * len(MONOSPACE)

    @pytest.mark.parametrize("where", MONOSPACE)
    async def test_a_run_of_box_drawing_has_no_gap_in_it(self, page: Page, gallery: str, where: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.evaluate("() => document.fonts.ready")
        assert (await self.rasterised(page, where))["gaps"] == 0

    @pytest.mark.parametrize("where", MONOSPACE)
    async def test_the_pitch_stays_inside_the_ink_it_joins_with(self, page: Page, gallery: str, where: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.evaluate("() => document.fonts.ready")
        measured = await self.rasterised(page, where)
        # Strictly inside, which is the stylesheet's own rule: the pitch is a pixel under the span,
        # so that the two ways of being wrong are not equally likely. The gap check above is the
        # coarse half and this is the exact one - a canvas rasterises a glyph about a pixel longer
        # than the same text laid out in the document, so a run can still draw joined at a pitch the
        # page itself breaks at, and only the comparison catches that.
        assert measured["pitch"] < measured["span"]

    @staticmethod
    async def rasterised(page: Page, where: str) -> dict[str, float]:
        """
        A run of box drawing drawn at `where`'s own grid: the gaps in it, and one glyph's ink.

        Rows rather than a column, because the stroke is a pixel wide and lands across two columns of
        which each is half lit, so the inkiest single column misses whichever glyph sits a fraction
        the other way and reports joins that are there as gaps.
        """
        return await page.evaluate(
            """([where, glyphs]) => {
              const style = getComputedStyle(document.querySelector(where));
              const size = parseFloat(style.fontSize), pitch = parseFloat(style.lineHeight);
              const draw = (run, apart) => {
                const canvas = document.createElement('canvas');
                canvas.width = Math.ceil(size * 2);
                canvas.height = Math.ceil(apart * (run.length + 2));
                const ink = canvas.getContext('2d', {willReadFrequently: true});
                ink.font = `${style.fontSize} ${style.fontFamily}`;
                ink.textBaseline = 'alphabetic';
                run.forEach((glyph, row) => ink.fillText(glyph, 0, apart * (row + 1)));
                const pixels = ink.getImageData(0, 0, canvas.width, canvas.height).data;
                const lit = [];
                for (let y = 0; y < canvas.height; y++) {
                  let total = 0;
                  for (let x = 0; x < canvas.width; x++) total += pixels[(y * canvas.width + x) * 4 + 3];
                  lit.push(total > 24);
                }
                return {first: lit.indexOf(true), last: lit.lastIndexOf(true), lit};
              };
              // The run at the page's own pitch, and then the tallest glyph alone, far enough from
              // anything else that what is measured is its ink and not a join.
              const run = draw(glyphs, pitch);
              const alone = draw(["│"], size * 4);
              return {pitch, gaps: run.lit.slice(run.first, run.last + 1).filter(on => !on).length,
                      span: alone.last - alone.first + 1};
            }""",
            [where, list(JOINING)],
        )

    @pytest.mark.parametrize("where", MONOSPACE)
    async def test_every_row_lands_on_the_same_subpixel_phase(self, page: Page, gallery: str, where: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        grid = await page.evaluate(
            "(where) => { const s = getComputedStyle(document.querySelector(where));"
            "return [parseFloat(s.fontSize), parseFloat(s.lineHeight)]; }",
            where,
        )
        # The pitch is what phase depends on; the size is here because it is what a pitch stated as a
        # ratio would be multiplied by, which is how a fractional one gets in.
        assert [value % 1 for value in grid] == [0, 0]


class TestHowReasoningIsSet:
    """
    A reasoning panel is set in italic, and the code a model quotes inside it is not.

    A browser rather than a markup assertion, because a `font-style` on the panel is inherited by
    everything in it: the rule that stops it is one the cascade has to win, and what is on the page
    is what the two elements each *end up* with rather than what any one rule says.
    """

    async def test_code_a_model_quotes_while_reasoning_is_not_slanted(self, page: Page, gallery: str) -> None:
        await page.goto(f"{gallery}/session.html", wait_until="load")
        drawn = await page.evaluate(
            """() => {
              const block = document.querySelector('.block--thinking');
              const style = one => getComputedStyle(block.querySelector(one)).fontStyle;
              return [getComputedStyle(block).fontStyle, style('p code'), style('pre code')];
            }"""
        )
        assert drawn == ["italic", "normal", "normal"]


class TestWhatComesOutOfACopyButton:
    """
    What a panel says and what a block of code holds, lifted off the page.

    A browser twice over. The clipboard is a live thing the server never renders, so all a markup
    assertion or a still could ask is whether a button is drawn, which is not the question. And what
    comes out of one has to be the same text whatever the reader has open, which is a property of two
    presses in two states rather than of either rendering.
    """

    async def copying(self, page: Page, gallery: str, kind: str) -> Locator:
        """One kind's first panel on a settled conversation, with the clipboard readable."""
        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await page.goto(f"{gallery}/session.html", wait_until="load")
        panel = page.locator(f".panel[data-kind={kind}]").first
        await expect(panel.locator(".copy").first).to_be_visible()
        return panel

    async def clipboard(self, page: Page) -> str:
        return str(await page.evaluate("() => navigator.clipboard.readText()"))

    async def test_a_panel_copies_what_it_says_and_none_of_the_chrome_around_it(self, page: Page, gallery: str) -> None:
        panel = await self.copying(page, gallery, "prompt")
        said = str(await panel.locator(".block").first.inner_text()).strip()
        await panel.locator(".panel__meta > .copy").click()
        taken = await self.clipboard(page)
        assert taken == said
        # The role, the permalink and the buttons themselves are on the panel and are not what it says.
        assert "copy" not in taken.lower()

    async def test_a_panel_copies_the_markdown_it_was_written_as(self, page: Page, gallery: str) -> None:
        """
        What is on the page is rendered Markdown, and the rendering is lossy in exactly the way
        somebody copying cares about: a reading of the markup gives text with the fences, the
        emphasis and the table gone from it, and nothing gets them back. So the source rides along in
        the markup and is what the button hands over.
        """
        await self.copying(page, gallery, "assistant")
        # The one that was written with everything a rendering loses in it.
        panel = page.locator(".panel[data-kind=assistant]:has(pre)").first
        await panel.locator(".panel__meta > .copy").click()
        taken = await self.clipboard(page)
        assert "```python" in taken
        assert "**kept**" in taken
        assert "| trigger | fires | survives a morph |" in taken
        # And none of that can be read off the page, which is what makes carrying the source
        # load-bearing rather than a convenience.
        drawn = str(await panel.inner_text())
        assert "```" not in drawn
        assert "**kept**" not in drawn

    async def test_a_block_of_code_copies_itself_rather_than_the_panel_it_is_in(self, page: Page, gallery: str) -> None:
        """
        The two are one control in two places, told apart by where each sits, so the one inside a
        fence has to hand over the fence: seated but reading the panel it happened to be in, every
        button on a panel would answer identically and three of them would be one.
        """
        panel = await self.copying(page, gallery, "thinking")
        code = panel.locator("pre").first
        await code.locator(".copy").click()
        taken = await self.clipboard(page)
        assert taken.strip() == str(await code.locator("code").inner_text()).strip()
        assert "The trigger is" not in taken

    async def test_a_call_copies_the_same_whether_it_is_folded_or_open(self, page: Page, gallery: str) -> None:
        """
        `textContent` rather than `innerText`, which is the whole of what makes this true: what is
        *rendered* of a folded `<details>` is its summary alone, so one button would answer two
        different things a click apart depending on what the reader happened to have open.
        """
        panel = await self.copying(page, gallery, "tool")
        fold = panel.locator("details.tool").first
        await expect(fold).not_to_have_attribute("open", "")
        await panel.locator(".panel__meta > .copy").click()
        folded = await self.clipboard(page)
        await fold.locator("summary").click()
        await expect(fold).to_have_attribute("open", "")
        await panel.locator(".panel__meta > .copy").click()
        assert await self.clipboard(page) == folded
        # And it is the call rather than the one line of its summary: what it was handed is in there.
        assert "called with" in folded

    async def test_a_stretch_of_reasoning_copies_the_same_whether_it_is_open_or_folded(
        self, page: Page, gallery: str
    ) -> None:
        """
        The panel whose row stands for it with a *prefix of its own body*, which is what makes this
        worth asking twice: a button reading the page rather than the source would hand back the
        opening line and then the whole thing again behind it. It reads `data-markdown`, which is on
        the block whichever way the panel is turned - and the button is *in* the row that folds it,
        so it has to keep answering with the panel shut under it.
        """
        panel = await self.copying(page, gallery, "thinking")
        await expect(panel).to_have_attribute("open", "")
        await panel.locator(".panel__meta > .copy").click()
        open_ = await self.clipboard(page)
        await panel.locator(".panel__role").click()
        await expect(panel).not_to_have_attribute("open", "")
        await panel.locator(".panel__meta > .copy").click()
        assert await self.clipboard(page) == open_
        # The source, so the fence is in there as it was written and the opening is in there once.
        assert "```html" in open_
        assert open_.count("The trigger is") == 1

    async def test_the_button_says_so_and_then_stops_saying_so(self, page: Page, gallery: str) -> None:
        panel = await self.copying(page, gallery, "assistant")
        button = panel.locator(".panel__meta > .copy")
        await expect(button).to_have_text("copy")
        await button.click()
        await expect(button).to_have_text("copied")
        await expect(button).to_have_text("copy", timeout=5_000)

    async def test_only_the_button_that_was_pressed_says_it_was(self, page: Page, gallery: str) -> None:
        """One panel holds several of these, so the confirmation names one rather than a place."""
        panel = await self.copying(page, gallery, "thinking")
        await panel.locator("pre .copy").first.click()
        await expect(page.locator(".copy[data-copied]")).to_have_count(1)
        assert await page.locator(".copy[data-copied]").first.evaluate("(one) => one.parentElement.tagName") == "PRE"


class TestSayingSomethingWasCopiedThroughASwap:
    """
    The confirmation survives the turn recording something, which is exactly when it has to.

    A running turn morphs the transcript every time anything lands, and every one of these buttons is
    inside it, so a label written onto the markup would be written back off a moment later - while
    the reader is watching the very turn they just lifted a result out of. It is held as a value and
    projected after every swap instead, and only a real server writing real steps under a real
    browser can tell the two apart.
    """

    async def test_what_was_copied_still_says_so_when_the_turn_records_more(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        button = page.locator(".panel[data-kind=prompt] .panel__meta > .copy")
        await expect(button).to_be_visible()
        await button.click()
        await expect(button).to_have_text("copied")
        await service.checkpointer.supply(session.id, model_key(0, 0), PARTWAY)
        # The swap has landed - a panel that was not there before is - and the button still says it.
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(1)
        await expect(button).to_have_text("copied")

    async def test_a_panel_that_arrives_is_seated_too(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The other half of the same swap. These are elements the server never sent, so they are taken
        off before a morph and put back after it, and a panel that arrived while the page was open
        would otherwise be the one panel a reader could not copy.
        """
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(0)
        await service.checkpointer.supply(session.id, model_key(0, 0), PARTWAY)
        await expect(page.locator(".panel[data-kind=thinking] .panel__meta > .copy")).to_have_count(1)
        # And nothing is seated twice, which is what an unguarded re-seating after every swap does.
        await service.checkpointer.supply(session.id, tool_key(0, "call-1"), came_back("the first file"))
        await expect(page.locator(".panel[data-kind=thinking] .copy")).to_have_count(1)


# One response of a turn, as the capability records it partway through: the model reasoned and asked
# for two files at once. Two calls because that is the state worth watching arrive - they run
# together, so one comes back while the other is still out.
PARTWAY = answered_with(
    {
        "kind": "response",
        "parts": [
            {"part_kind": "thinking", "content": "Two files to look at."},
            {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "a.py"}, "tool_call_id": "call-1"},
            {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "b.py"}, "tool_call_id": "call-2"},
        ],
    }
)


class TestWatchingATurnArrive:
    """
    A turn appearing in the page as it is recorded, which is the whole point of the live connection.

    Nothing in a still can show this and no markup assertion can either: what both would check is
    one render, where what has to hold is that a *second* render reaches a page nobody reloaded.
    Every step here is written into the checkpoint from the test while the browser is looking at it,
    which is exactly what a pass does and the only way to hold a turn half-finished long enough to
    assert on.
    """

    async def started(self, console: tuple[str, Service], page: Page) -> Service:
        """A session with a question in it, open in the browser, with nothing answered yet."""
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
        self.session = session.id
        return service

    async def test_a_response_appears_without_the_page_being_reloaded(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        service = await self.started(console, page)
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(0)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        await expect(page.locator(".panel[data-kind=thinking]")).to_contain_text("Two files to look at.")

    async def test_a_call_still_out_is_working_and_fills_in_when_its_result_lands(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The state `ToolUse` has always been able to describe and nothing could previously produce:
        by the time a turn's messages are written every call has an answer, so a call still out
        exists only while the turn is running.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        await expect(page.locator(".tool .waiting")).to_have_count(2)
        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), came_back("the first file"))
        await expect(page.locator(".tool .waiting")).to_have_count(1)
        await expect(page.locator(".tool").first).to_contain_text("the first file")

    async def test_a_reader_keeps_what_they_unfolded_while_the_turn_goes_on(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The reason every message is morphed rather than swapped. A turn records several times a
        second while it runs, so a replacement would shut a call the reader opened to watch, over
        and over, exactly while they were reading it.

        The reader presses, which is what makes this a test about a *decision*. Left to the state the
        server happened to render - a call still out is drawn open - it would pass on a page where
        nobody had decided anything, and so could not tell a kept decision from a default that had
        not moved yet.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        calls = page.locator("details.tool")
        await expect(calls).to_have_count(2)
        # Shut and opened again, so what is on the page is this reader's answer and not the console's.
        await calls.first.locator("summary").click()
        await expect(calls.first).not_to_have_attribute("open", "")
        await calls.first.locator("summary").click()

        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), came_back("the first file"))
        await service.checkpointer.supply(self.session, tool_key(0, "call-2"), came_back("the second file"))

        # Both are back, so the server now renders both shut; the reader's answer is what holds.
        await expect(calls.first).to_contain_text("the first file")
        await expect(calls.first).to_have_attribute("open", "")

    async def test_a_call_drawn_open_because_it_was_out_stays_open_when_its_result_lands(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Nobody pressed anything here, and it stays open all the same, which is what recording a
        morph's own toggle buys. A reader watching a call fill in is reading it at exactly the moment
        the result arrives, so collapsing it to a line then would take the thing being watched away
        at the moment it became worth having. The dock's third button is how a reader asks for the
        console's answer back.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        call = page.locator("details.tool").first
        await expect(call).to_have_attribute("open", "")

        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), came_back("the first file"))
        await expect(call).to_contain_text("the first file")

        await expect(call).to_have_attribute("open", "")

    async def test_the_dock_puts_every_fold_back_where_the_console_had_it(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The third answer beside fold-everything and unfold-everything, and not a midpoint between
        them: those set every fold one way, and this hands the question back, so what comes out is a
        call shut and a reply open rather than any single state.

        It withdraws the decisions rather than making new ones, which is what `data-opens` is for.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), came_back("the first file"))
        await service.checkpointer.supply(self.session, tool_key(0, "call-2"), came_back("the second file"))
        call = page.locator("details.tool").first
        reply = page.locator(".panel[data-kind=thinking]").first
        await expect(call).not_to_have_attribute("open", "")

        await page.locator('[data-fold="open"]').click()
        await expect(call).to_have_attribute("open", "")
        await expect(reply).to_have_attribute("open", "")
        await page.locator('[data-fold="shut"]').click()
        await expect(call).not_to_have_attribute("open", "")
        await expect(reply).not_to_have_attribute("open", "")

        await page.locator('[data-fold="default"]').click()

        # Each back where the console put it, which for these two is not the same answer.
        await expect(call).not_to_have_attribute("open", "")
        await expect(reply).to_have_attribute("open", "")

    async def test_the_settings_step_becomes_the_conversation_when_the_session_loads(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The step and the conversation are two shapes of one page, and a page drawing the first has
        no region the second's messages could land in. So what the stream says when the shape is
        over is a named event rather than a partial, and the script's answer is the page again. This
        is the one that used to need a reload by hand.
        """
        url, service = console
        session = await service.start(DEFAULT_CHOICE)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator("#setup")).to_be_visible()
        await expect(page.locator("#transcript")).to_have_count(0)
        await service.say(session.id, "what is a mainplate")
        await registered(service, session.id)
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
        await expect(page.locator("#setup")).to_have_count(0)

    async def test_a_message_leaves_the_connection_and_its_sink_alone(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        A message made only of partials updates the regions it names and nothing else. If it ever
        swapped into the connecting element instead, the conversation would stop updating and the
        connection would be replaced by the markup it delivered.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(1)
        assert await page.locator("#stream").inner_html() == ""
        assert await page.get_attribute("#stream", "hx-sse:connect") is not None

    async def test_a_panel_that_arrives_is_marked_and_the_rest_are_not(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The mark says *which* part of a filling-in turn moved, so it has to be only the part that
        did. A conversation that marked itself top to bottom on every update would be pointing at
        everything, which is pointing at nothing.
        """
        service = await self.started(console, page)
        await expect(page.locator(".panel[data-fresh]")).to_have_count(0)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        await expect(page.locator(".panel[data-fresh]")).to_have_count(2)
        assert await page.locator(".panel[data-fresh]").first.get_attribute("data-kind") == "thinking"

    async def test_a_panel_that_changes_is_marked_and_an_unchanged_one_is_not(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        A result filling into a call already on the page changes no panel but that one, and it is
        the change a reader is most likely to be waiting on. The mark has to survive being told
        about, so the animation is what takes it off rather than the next update.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        # Both edges of the arrival, and both are needed. Waiting only for the marks to *clear*
        # would be satisfied the instant it was asked, before the response had even reached the
        # page, and the next assertion would then be measuring the arrival rather than the result.
        marked = page.locator(".panel[data-fresh]")
        await expect(marked).to_have_count(2)
        await expect(marked).to_have_count(0, timeout=5_000)
        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), came_back("the first file"))
        await expect(marked).to_have_count(1)
        assert await marked.first.get_attribute("data-kind") == "tool"

    async def test_a_reader_keeps_a_command_they_shut_while_the_turn_goes_on(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The other direction of the same decision, and the one a command needs. The server renders a
        command open, so a script that only remembered what a reader *unfolded* would reopen one
        they had just put away on the very next thing the turn recorded.
        """
        service = await self.started(console, page)
        await service.checkpointer.append(self.session, recorded_command("git status"))
        shut = page.locator("details.ran").first
        await expect(shut).to_have_attribute("open", "")
        await shut.locator("summary").click()
        await expect(shut).not_to_have_attribute("open", "")
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(1)
        await expect(shut).not_to_have_attribute("open", "")

    async def test_a_conversation_is_not_marked_top_to_bottom_when_it_is_opened(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Every panel is new to the script on a first render, so the mark has to be suppressed there
        rather than fall out of the comparison. A reader opening a long conversation would otherwise
        watch the whole of it flash at them.
        """
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await service.checkpointer.supply(session.id, model_key(0, 0), PARTWAY)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(1)
        await expect(page.locator(".panel[data-fresh]")).to_have_count(0)


class TestShuttingAFoldFromItsFrame:
    """
    The other way out of an open fold, which is the one a long output needs.

    A summary is a single row at the top of a box that may be several screens, so reading to the end
    of an output meant scrolling back up to the one place that would put it away. The frame shuts it
    too, for a command and for a call alike: one is drawn open so shutting is the press made oftenest
    there, and the other is what a reader opened to check a return that runs to hundreds of lines.

    A browser, because every half of this is a press landing on a particular part of a box. Where the
    frame stops and the output starts is a fact about the rendered layout, and no markup assertion can
    see which of the two a click reached.
    """

    async def a_command_with_output(self, console: tuple[str, Service], page: Page) -> None:
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        entry = await service.checkpointer.append(session.id, recorded_command("git status"))
        await service.checkpointer.supply(
            session.id,
            result_key(entry.key),
            recorded_result(Result(status=0, output="on branch main\n", took=timedelta(seconds=0.2))),
        )
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator("details.ran")).to_have_attribute("open", "")

    async def test_pressing_the_room_under_the_output_shuts_it(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The band at the bottom of the body, which is where a reader who has just read to the end of a
        long output already is.
        """
        await self.a_command_with_output(console, page)
        box = await page.locator(".ran__body").bounding_box()
        assert box

        await page.mouse.click(box["x"] + 6, box["y"] + box["height"] - 3)

        await expect(page.locator("details.ran")).not_to_have_attribute("open", "")

    async def test_pressing_what_the_command_said_leaves_it_open(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The exemption the whole shape rests on: a press in the output is usually the start of lifting
        a line out of it, and a panel that folded under somebody selecting from it would cost more
        than the scroll it saves.
        """
        await self.a_command_with_output(console, page)

        await page.locator(".ran__body pre").click()

        await expect(page.locator("details.ran")).to_have_attribute("open", "")

    async def test_shutting_it_that_way_is_a_decision_that_survives_a_swap(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Setting `open` dispatches `toggle`, so this is recorded the way a press on the summary is
        rather than through a second path. Without that, the next thing the turn recorded would draw
        the command open again, because the server renders it open.
        """
        _, service = console
        await self.a_command_with_output(console, page)
        session = page.url.rsplit("/", 1)[-1]
        box = await page.locator(".ran__body").bounding_box()
        assert box
        await page.mouse.click(box["x"] + 6, box["y"] + box["height"] - 3)
        await expect(page.locator("details.ran")).not_to_have_attribute("open", "")

        await service.checkpointer.supply(session, model_key(0, 0), PARTWAY)

        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(1)
        await expect(page.locator("details.ran")).not_to_have_attribute("open", "")

    async def a_call_a_reader_opened(self, console: tuple[str, Service], page: Page) -> Locator:
        """A finished call, which the server renders shut, opened the way a reader opens one."""
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await service.checkpointer.supply(session.id, model_key(0, 0), PARTWAY)
        await service.checkpointer.supply(session.id, tool_key(0, "call-1"), came_back("the first file"))
        await service.checkpointer.supply(session.id, tool_key(0, "call-2"), came_back("the second file"))
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        opened = page.locator("details.tool").first
        await expect(opened).not_to_have_attribute("open", "")
        await opened.locator("summary").click()
        await expect(opened).to_have_attribute("open", "")
        return opened

    async def test_pressing_the_room_under_a_call_shuts_it_too(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The same complaint one panel along: a return that runs to hundreds of lines is one a reader
        would otherwise scroll back to the top of to put away. Two panels of the same shape answering
        the same press differently would be the thing to explain.
        """
        opened = await self.a_call_a_reader_opened(console, page)
        box = await opened.locator(".tool__body").bounding_box()
        assert box

        await page.mouse.click(box["x"] + 6, box["y"] + box["height"] - 3)

        await expect(opened).not_to_have_attribute("open", "")

    async def test_pressing_what_a_call_returned_leaves_it_open(self, page: Page, console: tuple[str, Service]) -> None:
        opened = await self.a_call_a_reader_opened(console, page)

        await opened.locator(".tool__body pre").first.click()

        await expect(opened).to_have_attribute("open", "")


class TestFoldingAPanel:
    """
    Every panel folds, from its own row of facts, with the mark immediately right of its title.

    A browser, because none of it is visible in the markup. That a press on the permalink or the copy
    button inside the row does *not* fold the panel is the browser's own rule - a summary's
    activation behaviour skips a press whose target is interactive content - and this row is the fold
    only because that holds. And where the mark sits relative to the title is a rendered position
    rather than document order: it is drawn by the stylesheet on the role and belongs to no node the
    markup names.
    """

    async def a_turn_that_reasoned(self, console: tuple[str, Service], page: Page, said: str) -> Locator:
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await service.checkpointer.supply(
            session.id,
            model_key(0, 0),
            answered_with({"kind": "response", "parts": [{"part_kind": "thinking", "content": said}]}),
        )
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        return page.locator(".panel[data-kind=thinking]")

    async def test_reasoning_is_drawn_open(self, page: Page, console: tuple[str, Service]) -> None:
        """
        Reasoning arrives while the turn is being answered, and watching a model think is one of the
        things a live transcript is for: rendered shut it would hide the thing being watched at the
        moment it is worth watching.
        """
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await expect(panel).to_have_attribute("open", "")
        await expect(panel.locator(".block--thinking")).to_be_visible()

    async def test_it_carries_no_fold_of_its_own_inside_the_panel(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The row it used to spend on a marker standing for the very text below it, which is the whole
        of what moving the fold up to the panel row saves. A call and a command keep theirs, because
        neither summary is a prefix of anything; a stretch of reasoning has nothing to say that its
        panel's row does not already say.
        """
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await expect(panel.locator("details")).to_have_count(0)

    async def test_the_row_folds_it(self, page: Page, console: tuple[str, Service]) -> None:
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await panel.locator(".panel__role").click()

        await expect(panel).not_to_have_attribute("open", "")
        await expect(panel.locator(".block--thinking")).to_be_hidden()

    async def test_the_mark_is_drawn_right_of_the_title(self, page: Page, console: tuple[str, Service]) -> None:
        """
        Which is the whole of the ask: one row, the title, and the control beside it. The mark is a
        `::after` on the role rather than the summary's own, because a disclosure's marker always
        leads the row and this one has to come *between* the title and the line it stands for.
        """
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        where = await panel.locator(".panel__role").evaluate("""
            node => getComputedStyle(node, "::after").content
        """)

        assert where.strip('"') == "\N{BLACK DOWN-POINTING SMALL TRIANGLE}", "open, the mark points down"

    async def test_the_permalink_inside_the_row_navigates_without_folding_it(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The row is the summary, so everything in it is inside a `<summary>`: without the browser's
        carve-out for interactive content, following a panel's own permalink would fold the panel it
        just took the reader to.
        """
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await panel.locator(".panel__anchor").click()

        await expect(panel).to_have_attribute("open", "")
        assert await page.evaluate("location.hash") == "#panel-0-1"

    async def test_the_copy_button_inside_the_row_copies_without_folding_it(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await panel.locator(".panel__meta > .copy").click()

        await expect(panel.locator(".panel__meta > .copy")).to_have_text("copied")
        await expect(panel).to_have_attribute("open", "")


class TestTheLineAShutPanelStandsFor:
    """
    What a shut panel says it holds, which is what makes folding prose worth offering at all.

    A browser twice over. What a shut panel shows is decided by `text-overflow`, which is the browser
    measuring a line against a box the server cannot know the width of, so whether it clips at all is
    invisible to any markup assertion. And the bound on what is *carried* into the row is only right
    if it exceeds what the widest panel can show, which is a question about rendered glyphs.
    """

    async def a_turn_that_reasoned(self, console: tuple[str, Service], page: Page, said: str) -> Locator:
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await service.checkpointer.supply(
            session.id,
            model_key(0, 0),
            answered_with({"kind": "response", "parts": [{"part_kind": "thinking", "content": said}]}),
        )
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        return page.locator(".panel[data-kind=thinking]")

    async def test_shut_it_stands_for_itself_with_its_opening_line(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The opening rather than a count, because a stretch of reasoning has no name the way a call
        has one, and what it opens with is what tells one stretch from the next.
        """
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await panel.locator(".panel__role").click()

        await expect(panel).not_to_have_attribute("open", "")
        await expect(panel.locator(".opening")).to_have_text("Two files to look at, so let me read both.")

    async def test_the_opening_is_shown_only_shut_and_nothing_on_the_row_moves(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Open, a prefix of the body standing directly above the body says nothing twice, so it goes.
        What must not go with it is anything's *place*: a control that moves under the finger that
        pressed it cannot be pressed twice, which is the rule every fold here is drawn to. That is
        why the line is hidden rather than removed - taken out of the flow, the row's free space
        collapses and the permalink slides left across the panel.
        """
        panel = await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")
        role, anchor = panel.locator(".panel__role"), panel.locator(".panel__anchor")
        await expect(panel.locator(".opening")).to_be_hidden()
        was = [await each.bounding_box() for each in (role, anchor)]
        assert all(was)

        await role.click()

        await expect(panel.locator(".opening")).to_be_visible()
        now = [await each.bounding_box() for each in (role, anchor)]
        assert [(box["x"], box["y"]) for box in now if box] == [(box["x"], box["y"]) for box in was if box]

    async def test_a_long_opening_is_clipped_at_the_width_of_the_panel(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The browser's own ellipsis, which is the whole reason no length is decided in the markup: the
        server cannot know the window, and any character count it picked would cut in the wrong place
        at every other width.
        """
        panel = await self.a_turn_that_reasoned(console, page, "The trigger is load. " * 40)
        await panel.locator(".panel__role").click()

        opening = panel.locator(".opening")
        clipped = await opening.evaluate("node => node.scrollWidth > node.clientWidth")
        assert clipped, "the line runs past its box, which is what makes the ellipsis appear"
        assert await opening.evaluate("node => getComputedStyle(node).textOverflow") == "ellipsis"

    async def test_more_is_carried_than_the_widest_panel_can_ever_show(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        What keeps the clipping honest. Clipped short of `OPENING` the ellipsis says there is more,
        and clipped *at* it with no ellipsis it would say there is not, so the bound has to exceed
        what fits - which is a bounded question, because the transcript is capped at `--measure`.

        Measured against the worst case there is: the narrowest glyph this console's prose face
        draws, repeated. Asked of a canvas rather than of the element, since a run of 320 of anything
        is clipped by the box being measured.
        """
        panel = await self.a_turn_that_reasoned(console, page, "The trigger is load. " * 40)
        await panel.locator(".panel__role").click()

        fits = await panel.locator(".opening").evaluate("""
            node => {
                const style = getComputedStyle(node);
                const ctx = document.createElement("canvas").getContext("2d");
                ctx.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
                let narrowest = Infinity;
                for (let c = 33; c < 127; c++) {
                    const per = ctx.measureText(String.fromCharCode(c).repeat(100)).width / 100;
                    if (per > 0) narrowest = Math.min(narrowest, per);
                }
                return Math.ceil(node.getBoundingClientRect().width / narrowest);
            }
        """)

        assert fits < OPENING, f"{fits} of the narrowest character fit, and only {OPENING} are carried"

    async def test_a_word_in_the_opening_is_found_once_rather_than_twice(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one place in this transcript where a line is a second copy of the body under it, so the
        search has to skip it: found in both, the dock would step through one sentence at two stops
        and the count would say there is twice as much of it as there is.
        """
        await self.a_turn_that_reasoned(console, page, "Two files to look at, so let me read both.")

        await page.locator(".search__input").fill("files")

        await expect(page.locator("mark.hit")).to_have_count(1)

    async def test_a_batch_of_calls_is_named_rather_than_quoted(self, page: Page, gallery: str) -> None:
        """
        A tool call has no prose to take a front off, so the panel names what is in it instead. What
        a reader scanning a shut turn wants from a batch is which tools ran, and a panel that stood
        for a batch with the first call's name alone would be hiding the rest of it.
        """
        await page.goto(f"{gallery}/answering.html", wait_until="load")

        named = await page.locator(".panel[data-kind=tool] > .panel__meta > .opening").evaluate_all(
            "lines => lines.map(line => line.textContent)"
        )

        assert named == ["read_file", "grep", "read, read"]


class TestFoldingADocumentTheConsoleHandedOver:
    """
    The two panels drawn shut: the session's standing system prompt, and the guidance a turn is
    handed when it reaches into a part of the repository carrying its own.

    One shape for both, because on the page they are the same thing - a document somebody committed,
    shown shut because it is reference rather than conversation. What separates them is where each
    sits in the request, and that is what each panel's kind says: `instructions` in front of the
    cached prefix, against a `SystemPromptPart` at a position in the history. So they are two panel
    kinds the key can quiet apart, drawn as one kind of block.

    Over the gallery, which is the one place both are on a page together.
    """

    async def documents(self, page: Page, gallery: str) -> Locator:
        """The gallery's own conversation, which forgets once and so carries two stretches."""
        await page.goto(f"{gallery}/session.html", wait_until="load")
        panels = page.locator(".panel:has(> .block--document)")
        await expect(panels).to_have_count(3)
        return panels

    async def test_the_two_of_them_are_one_block_under_two_kinds_of_panel(self, page: Page, gallery: str) -> None:
        """
        Which is the whole of the split: one shape on the page, two words in the key, because the two
        sit in different places in the request and a reader may want to quiet them apart.
        """
        panels = await self.documents(page, gallery)

        drawn = await panels.evaluate_all("panels => panels.map(panel => panel.dataset.kind)")

        assert drawn == ["system-prompt", "guidance", "system-prompt"]

    async def test_both_are_drawn_shut(self, page: Page, gallery: str) -> None:
        """
        Both are long and both are reference, so unfolded either would be most of what a reader sees.
        The opening line is what makes that affordable rather than a loss.
        """
        panels = await self.documents(page, gallery)

        for panel in await panels.all():
            await expect(panel).not_to_have_attribute("open", "")

    async def test_a_shut_one_names_what_is_in_it(self, page: Page, gallery: str) -> None:
        """
        Which is the whole reason drawing them shut costs nothing: a guidance block opens by naming
        the file it came from, and a system prompt by saying what the session is for.
        """
        panels = await self.documents(page, gallery)

        told = await panels.locator("> .panel__meta > .opening").evaluate_all(
            "lines => lines.map(line => line.textContent)"
        )

        assert told[0].startswith("You are a helpful assistant")
        assert told[1].startswith("`src/mainplate/AGENTS.md`, guidance for this part of the repository:")

    async def test_the_frame_around_it_shuts_the_panel_it_belongs_to(self, page: Page, gallery: str) -> None:
        """
        The way out of the longest box on the page. A press on the room around the prose shuts the
        fold that prose is a body of, which since this one has no fold of its own is the panel - the
        same rule a call and a command already answer, read off the frame rather than off a list of
        folds so that it survived the fold moving up a level.
        """
        panels = await self.documents(page, gallery)
        panel = panels.first
        await panel.locator(".panel__role").click()
        await expect(panel).to_have_attribute("open", "")

        # The band below the prose, which is where a reader who has just read to the end already is.
        box = await panel.locator(".block--document").bounding_box()
        assert box
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] - 2)

        await expect(panel).not_to_have_attribute("open", "")


class TestOpeningTheRecordBehindARequest:
    """
    The `r{i}` on a rule stays exactly where it was when it is pressed.

    A control that moves under the finger that pressed it is a control a reader cannot press twice,
    and it reads as the page having jumped rather than as something having opened. Invisible to a
    markup assertion and to a still alike: both states are correct markup and each screenshot is
    right on its own, so what has to be measured is one element's box across the press.
    """

    async def opened(self, console: tuple[str, Service], page: Page) -> Locator:
        """A conversation with one recorded request in it, as the closed tag on that request's rule."""
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await taking(service, session.id)
        await service.checkpointer.supply(session.id, model_key(0, 0), PARTWAY)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        tag = page.locator(".tag").first
        await expect(tag).to_have_count(1)
        return tag

    async def where_on_its_rule(self, tag: Locator) -> dict[str, float]:
        """
        Where the marker sits within the rule it is on, rather than within the window.

        The window is the wrong frame for this one question: the page follows the end, so a record
        opening at the bottom of a conversation scrolls the transcript under it, which is the
        console doing what it is asked and would report as the marker having moved. What the marker
        must not do is change its place on its own line.
        """
        return dict(
            await tag.evaluate(
                """(tag) => {
                    const summary = tag.querySelector('summary').getBoundingClientRect();
                    const rule = tag.closest('.rule').getBoundingClientRect();
                    return { x: summary.x - rule.x, y: summary.y - rule.y };
                }"""
            )
        )

    async def test_the_marker_does_not_move_when_it_is_pressed(self, page: Page, console: tuple[str, Service]) -> None:
        tag = await self.opened(console, page)
        before = await self.where_on_its_rule(tag)
        await tag.locator("summary").click()
        await expect(tag).to_have_attribute("open", "")
        assert await self.where_on_its_rule(tag) == before

    async def test_the_record_opens_underneath_the_rule_it_belongs_to(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The other half of the same measurement: nothing moving is also what a tag that never opened
        would report, so the record has to be shown to arrive, below the line and across it.
        """
        tag = await self.opened(console, page)
        summary = tag.locator("summary")
        record = tag.locator(".record__json")
        await expect(record).to_be_hidden()
        await summary.click()
        await expect(record).to_be_visible()
        # All three boxes in one go, because the page follows the end: a record opening at the
        # bottom scrolls the transcript under it, so two measurements taken a call apart are two
        # measurements of different scroll positions and their difference means nothing.
        placed = await tag.evaluate(
            """(tag) => {
                const box = one => { const {x, y, width, height} = one.getBoundingClientRect();
                                     return {x, y, width, height}; };
                return { summary: box(tag.querySelector('summary')),
                         record: box(tag.querySelector('.record__json')),
                         rule: box(tag.closest('.rule')) };
            }"""
        )
        assert placed["record"]["y"] >= placed["summary"]["y"] + placed["summary"]["height"]
        assert placed["record"]["width"] > placed["rule"]["width"] / 2


async def landed_on_the_branch(console: tuple[str, Service], page: Page) -> None:
    """
    Answer the settings step of the branch a send just navigated to, so a transcript is drawn.

    **A fork lands on that step**, because it carries its parent's turns and none of its plugins, and
    that is the console working rather than a fixture to loosen: a branch plants a fresh worktree and
    may be planted at a tree whose `.mainplate/` says something new, so it asks again. This console
    runs no worker, so the pass that press would ask for never happens and the registration is written
    here instead of clicked.
    """
    _, service = console
    await registered(service, page.url.rsplit("/", 1)[-1])
    await page.reload(wait_until="load")


async def a_conversation(console: tuple[str, Service], page: Page) -> str:
    """One session with a turn being answered, on the page, as the id to write further steps against."""
    url, service = console
    session = await started(service, "what is a mainplate", DEFAULT_CHOICE, enrolled=(CARDED,))
    await taking(service, session.id)
    await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
    await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
    return session.id


class TestSayingWhetherTheCacheIsStillWarm:
    """
    The line above the box, where the server states a fact and the script states the reading of it.

    A browser, because the whole point is that the two differ: the server can only say when the prefix
    was written, since nothing here re-renders on the clock, and turning that into `warm as of 12m` is
    the script's. Both are in the markup as the same element, so nothing short of running it can tell
    which one a reader actually sees.
    """

    async def test_the_script_turns_the_written_time_into_how_long_ago_it_was(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        What no-script gets is `cached at 15:09`, which is a fact that cannot rot. What a reader with
        the file gets is the reading of it, recomputed as the page sits there.
        """
        _, service = console
        session = await a_conversation(console, page)
        await service.checkpointer.supply(
            session,
            messages_key(0),
            recorded_turn({"kind": "response", "parts": [{"part_kind": "text", "content": "a plate"}]}),
        )
        await page.reload(wait_until="load")

        await expect(page.locator(".cache__state")).to_contain_text("warm as of")

    async def test_a_prefix_past_the_retention_stays_cold_under_the_script(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one-sided rule, driven: the script recomputes the state and must not talk a cold prefix
        back into being warm. Aged past the retention on the record rather than by moving any clock,
        which is what decides it.
        """
        _, service = console
        session = await a_conversation(console, page)
        stale = datetime.now(UTC) - RETENTION - timedelta(minutes=1)
        await service.checkpointer.supply(
            session,
            messages_key(0),
            recorded_turn(
                {
                    "kind": "response",
                    "parts": [{"part_kind": "text", "content": "a plate"}],
                    "timestamp": stale.isoformat(),
                }
            ),
        )
        await page.reload(wait_until="load")

        await expect(page.locator(".cache__state")).to_have_text("cold")

    async def test_a_conversation_nothing_has_answered_draws_no_line_at_all(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        `:empty` rather than an absent element, so the region the page's connection swaps into is
        still there. What a reader must not see is a blank row above the box.
        """
        await a_conversation(console, page)

        await expect(page.locator(f"#{CACHE_ID}")).to_have_count(1)
        await expect(page.locator(f"#{CACHE_ID}")).to_be_hidden()


class TestAPluginsOwnCard:
    """
    A card the console draws from what a plugin declared, where the two controls deliberately differ.

    A browser, twice over: which of them takes effect on the press is htmx's trigger rather than
    anything in the markup, and whether the button says there is something unsaved is a comparison
    against a property no server renders. Both look identical in what is sent either way.

    **A card is declared, not rendered**, so what is under test is the console's own drawing of
    somebody else's declaration - which is the whole reason a repository may have one.
    """

    async def test_the_switch_takes_effect_on_the_press(self, page: Page, console: tuple[str, Service]) -> None:
        """
        A checkbox says the whole of what it means the moment it moves, so waiting for `Set` leaves a
        console that looks switched off and is not.
        """
        _, service = console
        session = await a_conversation(console, page)
        await expect(page.locator(".plugin__switch input")).to_be_checked()

        await page.uncheck(".plugin__switch input")

        await expect(page.locator(".plugin__switch input")).not_to_be_checked()
        assert (await read_tending(service.database, session)).of("bundled:handoff")["hands_off"] is False

    async def test_the_number_waits_to_be_set_and_says_that_it_is_waiting(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The other half, and why the two differ: a number is half-written for as long as somebody is
        writing it, so it cannot take effect on a keystroke - which leaves the button beside it to say
        there is something to press. Nothing is recorded until it is.
        """
        _, service = console
        session = await a_conversation(console, page)
        before = await read_tending(service.database, session)

        await page.fill(".plugin__number input", "120")

        await expect(page.locator(".plugin__settings")).to_have_attribute("data-dirty", "")
        assert await read_tending(service.database, session) == before, "typing records nothing"

        await page.click(".plugin__set")

        await expect(page.locator(".plugin__settings")).not_to_have_attribute("data-dirty", "")
        assert (await read_tending(service.database, session)).of("bundled:handoff")["reserve"] == 120

    async def test_typing_the_recorded_value_back_leaves_nothing_to_press(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The mark is a comparison against what was rendered rather than a flag set by the first
        keystroke, so undoing a change unmarks the button rather than leaving it lit for ever.
        """
        await a_conversation(console, page)
        box = page.locator(".plugin__number input")
        recorded = await box.input_value()

        await box.fill("120")
        await expect(page.locator(".plugin__settings")).to_have_attribute("data-dirty", "")
        await box.fill(recorded)

        await expect(page.locator(".plugin__settings")).not_to_have_attribute("data-dirty", "")

    async def test_the_unit_the_plugin_declared_is_drawn_beside_the_box(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Which is what lets a reserve of forty thousand tokens be two digits rather than six to count
        the zeroes of. The console does no arithmetic with it: the stored value is whatever the
        plugin says it is.
        """
        await a_conversation(console, page)
        await expect(page.locator(".plugin__unit")).to_have_text("K")


class TestTheSwitchOnATiersHeading:
    """
    One control over the controls under it, in three states, drawn by the script and nothing else.

    A browser because none of it is in the markup: the server renders the heading's three states from
    what it knows, and after that every one of them is a property the script sets on an element. Both
    a heading that has gone stale and one stuck on "some of them" are correct markup showing the wrong
    thing, and the switches under it stay right either way, so nothing else here would notice.
    """

    HEADING = '.tier[data-tier="bundled"] .tier__switch input'
    UNDER = '.tier[data-tier="bundled"] .plugin__switch input[type=checkbox]'

    async def test_a_heading_says_full_when_every_switch_under_it_is_on(self, page: Page, gallery: str) -> None:
        """
        Which the hidden `off` field beside each switch is what made hard: counted as a switch, it is
        one that is never on, so a full group could report at most half of itself and the heading was
        stuck indeterminate however many were ticked.
        """
        await page.goto(f"{gallery}/settings.html", wait_until="load")
        heading = page.locator(self.HEADING)
        await expect(heading).to_be_checked()

        await page.locator(self.UNDER).first.uncheck()

        assert await self.state(page) == {"checked": False, "indeterminate": True}, "some of them, and it says so"

        await page.locator(self.UNDER).first.check()

        assert await self.state(page) == {"checked": True, "indeterminate": False}

    async def test_the_heading_sets_every_switch_under_it_and_none_beside_it(self, page: Page, gallery: str) -> None:
        """The other direction, and the tier it is not: what a session records is a switch per plugin."""
        await page.goto(f"{gallery}/settings.html", wait_until="load")
        elsewhere = '.tier[data-tier="user"] .plugin__switch input[type=checkbox]'
        await expect(page.locator(elsewhere)).to_be_checked()

        await page.locator(self.HEADING).uncheck()

        for each in await page.locator(self.UNDER).all():
            await expect(each).not_to_be_checked()
        await expect(page.locator(elsewhere)).to_be_checked()

    async def state(self, page: Page) -> dict[str, bool]:
        return await page.evaluate(
            "(selector) => { const box = document.querySelector(selector);"
            " return {checked: box.checked, indeterminate: box.indeterminate}; }",
            self.HEADING,
        )


async def a_branch(console: tuple[str, Service], page: Page) -> tuple[str, str]:
    """
    A conversation and a branch of it from its end, on the page, as the two ids.

    Made the way a rule's fork link makes one, through the service rather than a press, because
    nothing in the composer forks: what these tests drive is the way *back*.
    """
    url, service = console
    session = await a_conversation(console, page)
    found = await service.read(session)
    assert found is not None
    assert found.chosen is not None
    branch = await service.fork(session, at=found.said.turns, chosen=found.chosen, said="let me check something")
    assert branch is not None
    await page.goto(f"{url}/sessions/{branch.id}", wait_until="load")
    await landed_on_the_branch(console, page)
    await expect(page.locator("#transcript")).to_contain_text("let me check something")
    return session, branch.id


class TestWhereTheComposerSendsTo:
    """
    That pressing Parent lands the reader in a *different* session, driven by a real htmx.

    Two things this rests on are htmx's rather than ours, and both were read out of a minified
    bundle: that it appends the submit button's own `name`/`value` to the request, and that it
    honours `HX-Redirect` by navigating. Either being wrong looks identical in the markup and in
    every in-memory test, and shows up only as a button that quietly sends to the wrong place.
    """

    async def test_sending_back_returns_to_where_the_branch_started(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The whole round trip, which no single request shows: a navigation and a message that ends up
        in the conversation the reader left rather than the one they were in.
        """
        session, _ = await a_branch(console, page)

        await page.fill(".composer textarea", "here is what I found")
        await page.click(".sender__caret")
        await page.click('.sender__option[value="parent"]')
        await page.wait_for_url(lambda url: session in url)

        await expect(page.locator("#transcript")).to_contain_text("here is what I found")
        await expect(page.locator("#transcript")).not_to_contain_text("let me check something")

    async def test_the_menu_shuts_when_the_reader_looks_elsewhere(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        A `<details>` closes only on its own summary, which is right for a fold and wrong for a menu:
        left open it lies over the conversation until the reader finds the one press that works.
        """
        await a_conversation(console, page)
        await page.click(".sender__caret")
        await expect(page.locator(".sender__more")).to_have_attribute("open", "")
        await page.click(".composer textarea")
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")

    async def test_sending_stays_in_this_conversation(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The control beside it, on the same form, posting no disposition at all. Asserted here rather
        than left to the in-memory tests because what makes the two differ is which button htmx
        treats as the submitter, and that is a browser behaviour.
        """
        session = await a_conversation(console, page)
        await page.fill(".composer textarea", "and another thing")
        await page.click(".sender > button")

        await expect(page.locator("#transcript")).to_contain_text("and another thing")
        assert session in page.url, "sending swaps the conversation rather than leaving it"

    async def test_handing_off_from_an_empty_box_is_taken_rather_than_refused(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one answer whose box may be empty, and the whole of what `formnovalidate` has to buy.

        A browser, because everything that decides this is the browser's: the box is `required`, so a
        form submitted without one is refused before any request leaves, and what lifts that for one
        submitter and no other is an attribute on the button. Nothing in the markup distinguishes a
        control that works here from one that silently does nothing, and no in-memory test can see the
        difference because neither ever reaches the boundary.
        """
        await a_conversation(console, page)
        await page.click(".sender__caret")
        await page.click('.sender__option[value="plugin:handoff"]')

        await expect(page.locator('.panel[data-kind="note"]')).to_have_count(1)

    async def test_a_handoff_carries_what_was_typed_as_what_to_dwell_on(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The other half of the same control: the box is optional here rather than ignored, and what is
        in it is appended to the standing ask rather than replacing it.
        """
        await a_conversation(console, page)
        await page.fill(".composer textarea", "dwell on the parser work")
        await page.click(".sender__caret")
        await page.click('.sender__option[value="plugin:handoff"]')

        asked = page.locator('.panel[data-kind="note"]')
        await expect(asked).to_contain_text("dwell on the parser work")
        await expect(asked).to_contain_text("Hand this conversation off")

    async def test_sending_into_a_turn_being_answered_steers_it_and_shows_the_message_at_once(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The whole of what letting Send decide has to be true for, driven rather than argued about.

        The button says nothing about steering and neither does the reader, so the two things that
        must hold are that the record shows a steer and that the page shows the message straight away.
        The second is the one a plausible implementation loses: a steer reaches `turn:{n}:messages`
        only when the turn *ends*, so drawn from those alone it would be a message that vanished for
        as long as the reply took.
        """
        _, service = console
        session = await a_conversation(console, page)
        await page.fill(".composer textarea", "actually, be brief")
        await page.click(".sender > button")

        await expect(page.locator('.panel[data-kind="steer"]')).to_have_count(1)
        await expect(page.locator('.panel[data-kind="steer"]')).to_contain_text("actually, be brief")
        recorded = await service.checkpointer.load(session)
        assert [held for key, held in recorded.items() if key.startswith(INBOX)][-1] == recorded_steer(
            "actually, be brief"
        )

    async def test_the_menu_offers_the_wait_only_while_something_is_being_answered(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one answer the record cannot settle, offered exactly where it differs from Send.

        With nothing running, waiting for the next turn *is* what Send does, so a control for it would
        be a second way to ask one question - which is what this console removes wherever it finds it.
        """
        _, service = console
        session = await a_conversation(console, page)
        await page.click(".sender__caret")
        await expect(page.locator('.sender__option[value="next"]')).to_have_count(1)

        await service.checkpointer.supply(
            session,
            messages_key(0),
            recorded_turn({"kind": "response", "parts": [{"part_kind": "text", "content": "a plate"}]}),
        )
        await page.reload(wait_until="load")
        await page.click(".sender__caret")
        await expect(page.locator('.sender__option[value="next"]')).to_have_count(0)

    async def test_escape_shuts_the_menu(self, page: Page, console: tuple[str, Service]) -> None:
        await a_conversation(console, page)
        await page.click(".sender__caret")
        await expect(page.locator(".sender__more")).to_have_attribute("open", "")
        await page.keyboard.press("Escape")
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")


@pytest_asyncio.fixture(loop_scope="session")
async def working(tmp_path: Path, catalogues: Catalogues, workspaces: Workspaces) -> AsyncIterator[tuple[str, Service]]:
    """
    The console over a store with files, which is what a command needs somewhere to run in.

    A second fixture rather than workspaces on the first, because the one above is deliberately a
    console with none: what most of these drive is a conversation, and giving every one of them a
    real repository would put a clone and a worktree behind tests that never look at either.
    """
    async with open_store(tmp_path / "mainplate.db", LEASE, catalogues, workspaces) as service:
        async with serving(build_app(already(service)), port=0) as server:
            yield f"http://{server.host}:{server.port}", service


async def a_session_with_files(working: tuple[str, Service], page: Page) -> str:
    url, service = working
    session = await started(service, "what is a mainplate", replace(DEFAULT_CHOICE, repository=FIXTURE))
    await taking(service, session.id)
    await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
    await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
    return session.id


class TestTurningTheBoxIntoACommandBox:
    """
    `! ` in an empty box, which is `/run`'s own key and never a second way of saying it.

    A browser twice over. The mode is an attribute the script sets and nothing the server renders, so
    no markup assertion can see it; and what the mode has to *do* is make the keyboard post a
    different button's pair, which is `requestSubmit`'s behaviour rather than ours and looks identical
    either way in the markup.

    The point of pinning it is the one failure a control like this can have: a box that runs what was
    meant to be said, or says what was meant to be run. Both are irreversible by the time anybody
    notices, so what these ask is that the mode is visible before the press and honoured at it.
    """

    async def test_a_leading_bang_and_a_space_turn_an_empty_box_into_a_command_box(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("! ")

        await expect(page.locator(".composer")).to_have_attribute("data-leading", "run")
        # The whole safety property: what the reader is about to press says what it does.
        await expect(page.locator('.sender__leader[data-leader="run"]')).to_be_visible()
        await expect(page.locator(".sender__send")).to_be_hidden()
        assert await page.input_value(".composer textarea") == "", "the leader and its space are consumed"

    async def test_a_bang_on_its_own_is_a_character_and_an_offer(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        The space is what commits, so until it is pressed `!` is a character somebody typed with the
        menu open beside it. That is what makes a mode something a reader finishes rather than
        something that happens to them on a keystroke they were in the middle of.
        """
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.press("!")

        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "run")
        await expect(page.locator(".sender__more")).to_have_attribute("open", "")
        await expect(page.locator('.sender__option[value="run"]')).to_be_visible()
        await expect(page.locator('.sender__option[value="forget"]')).to_be_hidden()
        assert await page.input_value(".composer textarea") == "!"

    async def test_a_command_box_stays_one_once_a_command_has_gone(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        The one mode that outlives what was sent from it, because a command is rarely the only one.
        Each answer decides that for itself and carries the decision on its own button, so this is
        `Run`'s answer rather than a rule over all of them - see `Keep`, which comes back to `Send`.
        """
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("! ")
        await page.keyboard.type("echo one")
        await page.keyboard.press("Shift+Enter")

        await expect(page.locator("#transcript")).to_contain_text("echo one")
        await expect(page.locator(".composer")).to_have_attribute("data-leading", "run")
        await expect(page.locator('.sender__leader[data-leader="run"]')).to_be_visible()

    async def test_a_bang_inside_a_message_is_an_ordinary_character(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        Which is the whole reason the leader is a mode rather than something the server strips off
        the front of what was posted: a paragraph that opens with `!` has to stay a paragraph.
        """
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("wow!")

        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "run")
        assert await page.input_value(".composer textarea") == "wow!"

    async def test_escape_puts_it_back_to_a_message_box(self, page: Page, working: tuple[str, Service]) -> None:
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("! ")
        await expect(page.locator(".composer")).to_have_attribute("data-leading", "run")

        await page.keyboard.press("Escape")

        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "run")
        await expect(page.locator(".sender__send")).to_be_visible()

    async def test_a_slash_in_a_command_box_is_an_ordinary_character(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        The half of "only in the default mode" that a command box makes urgent: `/` is the front of
        half the paths anybody types, so a palette opening over one would be in the way on every
        command.
        """
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("! ")
        await page.keyboard.type("/usr/bin/env")

        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        await expect(page.locator(".composer")).to_have_attribute("data-leading", "run")
        assert await page.input_value(".composer textarea") == "/usr/bin/env"

    async def test_the_keyboard_runs_what_is_in_a_command_box_rather_than_saying_it(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        The one that matters, and the one only a browser can ask. `requestSubmit()` with no submitter
        posts no button's pair at all, so an unattributed send would arrive as an ordinary message -
        a command said to the model instead of run, which is both wrong things at once.
        """
        _, service = working
        session = await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("! ")
        await page.keyboard.type("echo from the keyboard")
        await page.keyboard.press("Shift+Enter")

        await expect(page.locator("#transcript")).to_contain_text("echo from the keyboard")
        recorded = await service.checkpointer.load(session)
        delivered = [held for key, held in recorded.items() if key.startswith(INBOX)]
        assert delivered[-1] == recorded_command("echo from the keyboard")
        assert len(delivered) == 2, "a command is not a message, so it queued nothing for a model"

    async def test_a_session_with_no_files_has_no_command_box_to_turn_into(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Which modes exist is read off the buttons the server drew, so a page that offers no `Run`
        cannot be put into one: the two cannot drift, because there is only the one thing that
        decides it.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("! ")

        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "run")
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        assert await page.input_value(".composer textarea") == "! "


class TestNamingAModeFromTheKeyboard:
    """
    `/forget` and the rest, which reach the sending menu's own rows without the pointer.

    A browser, for the reasons the command box is one and one more: the palette *is* the menu, so
    what these ask is that one control answers two questions without confusing them. A row pressed
    with a message in the box sends that message; the same row pressed with `/fo` in the box chooses
    a mode and sends nothing, and the markup is identical either way.
    """

    async def test_a_slash_in_an_empty_box_offers_the_menus_own_answers(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/")

        await expect(page.locator(".sender__more")).to_have_attribute("open", "")
        await expect(page.locator('.sender__option[value="forget"]')).to_be_visible()
        await expect(page.locator(".sender__option[data-shelf]")).to_be_visible()

    async def test_typing_narrows_the_offer_to_what_still_fits(self, page: Page, console: tuple[str, Service]) -> None:
        """
        A prefix rather than anywhere in the word, which is the opposite of the branch field: a
        leader is a short word typed from the front.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/f")

        await expect(page.locator('.sender__option[value="forget"]')).to_be_visible()
        await expect(page.locator(".sender__option[data-shelf]")).to_be_hidden()

    async def test_a_space_after_the_whole_word_takes_that_answer(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The whole of what a leader does: nothing is sent, nothing is recorded, and the box is in that
        answer's mode with the button beside it saying so. The space is what commits, which is what
        leaves every keystroke before it an ordinary character.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/forget ")

        await expect(page.locator(".composer")).to_have_attribute("data-leading", "forget")
        await expect(page.locator('.sender__leader[data-leader="forget"]')).to_be_visible()
        await expect(page.locator(".sender__send")).to_be_hidden()
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        assert await page.input_value(".composer textarea") == "", "the leader and its space are consumed"

    async def test_every_answer_the_server_drew_has_a_mode_to_be_in(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one drift the mode's shape can have, asked over every answer rather than over a chosen
        one. Which modes exist is read off the buttons the server drew, but which of them is *shown*
        is a list of names in `mainplate.css`, and CSS cannot ask whether a descendant's attribute
        matches an ancestor's. So an answer added without a line there enters a mode that hides Send
        and reveals nothing: a composer with no primary button and no sentence saying where the text
        is about to go.

        A browser because the failure is entirely in the cascade - the markup is identical either
        way, and both buttons are in the document in both.
        """
        await a_conversation(console, page)
        leaders = await page.locator(".sender__leader").evaluate_all("row => row.map(one => one.dataset.leader)")
        assert leaders, "the menu drew no answers at all"

        for leader in leaders:
            await page.click(".composer textarea")
            await page.keyboard.type(f"/{leader} ")

            await expect(page.locator(".composer")).to_have_attribute("data-leading", leader)
            await expect(page.locator(f'.sender__leader[data-leader="{leader}"]')).to_be_visible()
            await expect(page.locator(f'.leading[data-leader="{leader}"]')).to_be_visible()
            await expect(page.locator(".sender__leader:visible")).to_have_count(1)
            await expect(page.locator(".sender__send")).to_be_hidden()

            await page.keyboard.press("Escape")

    async def test_a_space_after_part_of_a_word_is_an_ordinary_space(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        A prefix is somebody still typing, so the key that commits a finished word cannot take the row
        the keyboard happens to be sitting on. What is left is text, and the menu puts itself away
        because `/fo ` is no longer a leader.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/fo ")

        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "forget")
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        assert await page.input_value(".composer textarea") == "/fo "

    async def test_enter_takes_the_row_the_keyboard_is_on(self, page: Page, working: tuple[str, Service]) -> None:
        """
        The palette's own key, which is what a half-typed word is finished with: the space names an
        answer in full, and Enter takes whichever row the arrows have arrived at.

        A bare `/` is the one prefix every answer fits, so on a session with files and a turn being
        answered it offers `next`, `forget`, `run` and `keep` at once, which is what makes this a
        test of the *position* rather than of there happening to be one row left. The arrow is what
        proves it: without it, taking the first row and taking the row the keyboard is on are the
        same thing and the key could be wrong in a way nothing here would see.
        """
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/")
        await expect(page.locator(".sender__option:visible")).to_have_count(4)
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")

        await expect(page.locator(".composer")).to_have_attribute("data-leading", "forget")
        await expect(page.locator('.sender__leader[data-leader="forget"]')).to_be_visible()
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        assert await page.input_value(".composer textarea") == "", "the leader is consumed, not sent"

    async def test_enter_on_an_open_offer_takes_the_row_it_starts_on(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """The other half of the pair above: untouched, the keyboard is on the first row that fits."""
        await a_session_with_files(working, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/")
        await page.keyboard.press("Enter")

        await expect(page.locator(".composer")).to_have_attribute("data-leading", "next")

    async def test_a_word_no_answer_answers_to_is_ordinary_text(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The refusal the whole shape rests on. The server parses no leader out of what was posted, so
        a paragraph that opens with a slash has to stay a paragraph rather than becoming a mode, a
        refusal, or anything else the reader has to undo.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/etc/hosts is where it lives")

        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "forget")
        assert await page.input_value(".composer textarea") == "/etc/hosts is where it lives"

    async def test_a_slash_partway_into_a_message_offers_nothing(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Only at the start of the box, which is the same rule `!` is under: mid-message a slash is an
        ordinary character and a menu opening over one would be in the way of writing a path.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("look in /forget")

        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        assert await page.input_value(".composer textarea") == "look in /forget"

    async def test_the_mode_decides_where_the_keyboard_sends(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The one that matters, and the one only a browser can ask: a leader is worth nothing unless the
        send it sets up actually goes where the button says. Parent answers `HX-Redirect`, so what a
        working one looks like is the parent session's URL.
        """
        session, branch = await a_branch(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/parent ")
        await page.keyboard.type("here is what I found")
        await page.keyboard.press("Shift+Enter")

        await page.wait_for_url(lambda url: session in url and branch not in url)
        await expect(page.locator("#transcript")).to_contain_text("here is what I found")
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")

    async def test_pressing_a_row_while_a_leader_is_typed_chooses_the_mode_rather_than_sending(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one failure reusing the menu as the palette can have: the rows are submit buttons, so a
        press with `/fo` in the box would post `/fo` as the message and forget on it - which is a
        message nobody wrote, sent with the context cleared.
        """
        session = await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/fo")
        await page.click('.sender__option[value="forget"]')

        await expect(page.locator(".composer")).to_have_attribute("data-leading", "forget")
        assert await page.input_value(".composer textarea") == ""
        assert session in page.url, "choosing a mode sends nothing, so nothing navigates"
        await expect(page.locator("#transcript")).not_to_contain_text("/fo")

    async def test_escape_puts_the_offer_away_and_leaves_what_was_typed(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Two presses rather than one doing both, because dismissing the offer and leaving a mode are
        different things to want: the first leaves the text where it is to keep writing.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/f")
        await expect(page.locator(".sender__more")).to_have_attribute("open", "")

        await page.keyboard.press("Escape")

        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "forget")
        assert await page.input_value(".composer textarea") == "/f"

    async def test_a_session_with_no_files_is_offered_no_run(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The palette is the menu, so what it offers is what the server drew: a session with nowhere to
        run a command has no `Run` row and therefore no `/run` at all.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/run")

        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")
        assert await page.input_value(".composer textarea") == "/run"

    async def test_entering_a_mode_leaves_the_box_where_it_was(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The composer is the bottom of the page, so a row appearing anywhere in its column pushes
        everything above that row upward - and the sentence saying what the mode does is a row that
        appears. Under the box it moved the box itself out from under the cursor at the moment
        somebody entered the mode; above it, what grows is the composer's top edge.

        A browser, because both layouts are correct markup and each screenshot is right on its own:
        what is wrong with the other one is one element's box across a press.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        before = await page.locator(".composer textarea").bounding_box()

        await page.keyboard.type("/forget ")

        await expect(page.locator('.leading[data-leader="forget"]')).to_be_visible()
        after = await page.locator(".composer textarea").bounding_box()
        assert before
        assert after
        assert after["y"] == before["y"], "entering a mode moved the box the reader is typing in"

    async def test_keeping_from_the_keyboard_shelves_it_rather_than_sending_it(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one answer that posts nothing at all, so it is a `type=button` and cannot be the
        submitter `requestSubmit` is handed. What the keyboard has to do there is press it.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/keep ")
        await page.keyboard.type("worth remembering")
        await page.keyboard.press("Shift+Enter")

        await expect(page.locator(".shelf__take")).to_have_text("worth remembering")
        await expect(page.locator("#transcript")).not_to_contain_text("worth remembering")
        assert await page.input_value(".composer textarea") == ""

    async def test_a_mode_that_does_not_stay_puts_the_box_back_to_a_message(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The other half of `Run` staying: what a mode does once the text has gone is the answer's own
        decision, carried on its button, and `Keep` is one of the ones that means a thing once.
        """
        await a_conversation(console, page)
        await page.click(".composer textarea")
        await page.keyboard.type("/keep ")
        await page.keyboard.type("worth remembering")
        await page.keyboard.press("Shift+Enter")

        await expect(page.locator(".composer")).not_to_have_attribute("data-leading", "keep")
        await expect(page.locator(".sender__send")).to_be_visible()


async def a_start_page(working: tuple[str, Service], page: Page, workspace: str = FIXTURE_NAME) -> None:
    """
    The new-session page with the `workspace` card picked, which is what puts branches on the field.

    Two things a browser makes true that a markup test would not, and both of them look like a hang
    rather than a missing step. The group folds down to the card that is picked, so it has to be
    *opened* before any other card is on the page to press. And a card is a `<label>`: the radio in
    it is a pixel at zero opacity with no pointer events, so pressing the input is pressing something
    a reader could never reach.
    """
    url, _ = working
    await page.goto(f"{url}/", wait_until="load")
    await page.click('label[for="open-repository"]')
    await page.click(f'.repo[data-name="{workspace}"]')
    await expect(page.locator(".basis__box")).to_be_attached()


async def showing_branches(page: Page) -> list[str]:
    """Which branches the narrowed list is actually offering, in the order it draws them."""
    return await page.eval_on_selector_all(
        ".basis__found-one",
        "(all) => all.filter((one) => one.parentElement.offsetParent !== null).map((one) => one.dataset.branch)",
    )


class TestNarrowingTheBranches:
    """
    The one field in the picker that is a search rather than a set of cards.

    A browser throughout, and not because the markup is awkward to assert on: the list is `hidden` in
    what the server sends, and everything that makes it a search - narrowing it, stepping it, taking
    one - happens after that. A markup test would be looking at a hidden list and calling it a
    feature.
    """

    async def test_the_branches_are_offered_once_a_repository_is_picked(
        self, page: Page, working: tuple[str, Service], worktree: Worktree
    ) -> None:
        await run("git", "branch", "release/2.1", cwd=worktree.root)

        await a_start_page(working, page)

        await expect(page.locator(".basis__count")).to_have_text("2 branches")
        await page.click(".basis__box")
        await expect(page.locator(".basis__found")).to_be_visible()
        await expect(page.locator(".basis__found-one")).to_have_count(2)

    async def test_typing_cuts_the_list_to_what_matches_anywhere_in_a_name(
        self, page: Page, working: tuple[str, Service], worktree: Worktree
    ) -> None:
        """
        Anywhere rather than at the front, because a branch is named `feature/the-thing` far more
        often than it is named for the word you remember about it.
        """
        await run("git", "branch", "feature/anchored-edits", cwd=worktree.root)
        await run("git", "branch", "release/2.1", cwd=worktree.root)
        await a_start_page(working, page)

        await page.click(".basis__box")
        await page.keyboard.type("anchor")

        assert await showing_branches(page) == ["feature/anchored-edits"]

    async def test_pressing_one_puts_it_in_the_box(
        self, page: Page, working: tuple[str, Service], worktree: Worktree
    ) -> None:
        """
        The press has to survive the focus leaving the box, which is what `mousedown` is for: on a
        `click` the list would shut under the press and nothing would be taken.
        """
        await run("git", "branch", "release/2.1", cwd=worktree.root)
        await a_start_page(working, page)
        await page.click(".basis__box")

        await page.click('.basis__found-one[data-branch="release/2.1"]')

        assert await page.input_value(".basis__box") == "release/2.1"
        await expect(page.locator(".basis__found")).to_be_hidden()

    async def test_the_keyboard_steps_the_list_and_takes_one(
        self, page: Page, working: tuple[str, Service], worktree: Worktree
    ) -> None:
        """
        What makes it a search box rather than a mouse-only menu. Enter is only swallowed while the
        reader is actually on an entry, because this field's form is the one that starts the session.
        """
        await run("git", "branch", "release/2.1", cwd=worktree.root)
        await a_start_page(working, page)
        await page.click(".basis__box")

        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")

        assert await page.input_value(".basis__box") in {"main", "release/2.1"}
        await expect(page.locator(".basis__found")).to_be_hidden()

    async def test_escape_shuts_the_list_without_leaving_the_page(
        self, page: Page, working: tuple[str, Service], worktree: Worktree
    ) -> None:
        await a_start_page(working, page)
        await page.click(".basis__box")
        await expect(page.locator(".basis__found")).to_be_visible()

        await page.keyboard.press("Escape")

        await expect(page.locator(".basis__found")).to_be_hidden()

    async def test_the_browser_s_own_completion_is_taken_off_once_this_takes_over(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        Two dropdowns over one box is one more than a reader can use. The `list` attribute is what
        the field has with this file absent, so it is removed at the moment the script replaces it -
        and only then, which is why a repository offering nothing keeps it.
        """
        await a_start_page(working, page)

        await expect(page.locator(".basis__found-one").first).to_be_attached()

        assert await page.get_attribute(".basis__box", "list") is None

    async def test_a_workspace_that_is_not_a_repository_has_no_fields_at_all(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        A base and a branch are answers *about* a repository, so `no files` has none for them to be
        about. Driven through a repository first, because the assertion worth making is that the
        fields somebody was already offered are taken back off.
        """
        await a_start_page(working, page)
        await expect(page.locator(".basis__box")).to_be_attached()

        # Opened again, because picking a card shuts the group down to what was picked.
        await page.click('label[for="open-repository"]')
        await page.click('.repo[data-name="no files"]')

        await expect(page.locator(".basis__box")).to_have_count(0)
        await expect(page.locator('[name="branch"]')).to_have_count(0)
        # The block itself stays, since it is what the next pick swaps over.
        await expect(page.locator("#basis")).to_be_attached()

    async def test_the_dots_stand_in_for_the_fields_while_the_forge_is_being_asked(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        """
        A cold clone is seconds of a block that has not changed, which reads as a card that did
        nothing. The request is held open here rather than raced, because the window it is shown in
        is exactly as long as a loopback round trip and a test that waited for it would be asserting
        on whichever side of it the scheduler landed.
        """
        url, _ = working
        await page.goto(f"{url}/", wait_until="load")

        # Hidden by `display` and not by `opacity`, so the block carries no row for it at rest. The
        # difference is invisible to `to_be_hidden`, which is why the height is asked for too.
        await expect(page.locator("#basis-loading")).to_be_hidden()
        assert await page.evaluate("() => document.querySelector('#basis-loading').getBoundingClientRect().height") == 0

        answering = asyncio.Event()

        async def hold(route: Route) -> None:
            await answering.wait()
            await route.continue_()

        await page.route("**/fragments/branches*", hold)
        await page.click('label[for="open-repository"]')
        await page.click(f'.repo[data-name="{FIXTURE_NAME}"]')

        await expect(page.locator("#basis-loading")).to_be_visible()

        answering.set()
        await expect(page.locator(".basis__box")).to_be_attached()
        await expect(page.locator("#basis-loading")).to_be_hidden()


class TestWhereTheCursorIsAfterSending:
    """
    Back in the box, whichever way the message left it.

    Both ways lose the focus, for reasons no markup assertion can see. Pressing Send moves it to the
    button, and `hx-disable` blurs the box itself while the post is in flight, so by the time the
    answer swaps in the cursor is on nothing at all. Since the next thing anybody does in a
    conversation is type again, that is a click or a Tab of finding the box before every message
    after the first.

    It is also a matter of *when*: htmx re-enables what it disabled just after the event this is
    driven from, so a focus asked for any sooner is asked of a box that is still disabled and takes
    nothing. That failure looks exactly like no focus rule at all.
    """

    async def test_the_cursor_returns_to_the_box_after_the_button_sends(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        await a_conversation(console, page)
        await page.fill(".composer textarea", "and another thing")
        await page.click(".sender > button")

        await expect(page.locator("#transcript")).to_contain_text("and another thing")
        await expect(page.locator(".composer textarea")).to_be_focused()

    async def test_the_cursor_stays_in_the_box_when_the_keyboard_sends(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        await a_conversation(console, page)
        await page.fill(".composer textarea", "one more thing")
        await page.press(".composer textarea", "Shift+Enter")

        await expect(page.locator("#transcript")).to_contain_text("one more thing")
        await expect(page.locator(".composer textarea")).to_be_focused()


class TestTheShelf:
    """
    Text written and not sent, kept for one conversation.

    None of it can be seen anywhere but a browser: what it holds lives in `localStorage`, the list is
    rendered by the script, and the property that matters most - that a branch inherits its parent's
    shelf - is a copy between two stores that only exist once a page has been opened on each.
    """

    async def opened(self, console: tuple[str, Service], page: Page) -> tuple[str, Service]:
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
        return session.id, service

    async def keep(self, page: Page, said: str) -> None:
        """
        Keeping is an answer in the send menu, so it is reached the way any of them is.

        The row rather than a bare `[data-shelf]`, which now names two controls: `Keep` is also one
        of the modes a leader reaches, and the button that stands beside the box in it carries the
        same attribute because it is the same answer.
        """
        await page.fill(".composer textarea", said)
        await page.click(".sender__caret")
        await page.click('.sender__option[data-shelf="keep"]')

    async def test_keeping_takes_the_text_out_of_the_box_and_names_it(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """Keeping clears the box, because the reason to shelve a paragraph is to write another."""
        await self.opened(console, page)
        await self.keep(page, "the first thing I noticed\nand more about it")

        await expect(page.locator(".shelf__take")).to_have_count(1)
        await expect(page.locator(".shelf__take")).to_have_text("the first thing I noticed")
        assert await page.input_value(".composer textarea") == ""

    async def test_taking_adds_to_the_box_rather_than_replacing_what_is_in_it(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The property that lets several kept notes be assembled into one message, and the one that
        makes the control safe: taking can never lose something already typed.
        """
        await self.opened(console, page)
        await self.keep(page, "the first point")
        await self.keep(page, "the second point")
        await page.fill(".composer textarea", "here is what I found")
        await page.click(".shelf__take >> nth=0")
        await page.click(".shelf__take >> nth=1")

        assert await page.input_value(".composer textarea") == (
            "here is what I found\n\nthe first point\n\nthe second point"
        )

    async def test_dropping_takes_one_off_and_leaves_the_rest(self, page: Page, console: tuple[str, Service]) -> None:
        await self.opened(console, page)
        await self.keep(page, "keep this one")
        await self.keep(page, "drop this one")
        await page.click(".shelf__drop >> nth=1")

        await expect(page.locator(".shelf__take")).to_have_count(1)
        await expect(page.locator(".shelf__take")).to_have_text("keep this one")

    async def test_the_shelf_is_still_there_after_a_reload(self, page: Page, console: tuple[str, Service]) -> None:
        """Unsent text surviving the tab being closed is the whole of what makes it worth keeping."""
        await self.opened(console, page)
        await self.keep(page, "something for later")
        await page.reload(wait_until="load")

        await expect(page.locator(".shelf__take")).to_have_text("something for later")

    async def test_one_conversation_s_shelf_is_not_another_s(self, page: Page, console: tuple[str, Service]) -> None:
        """
        Scoped by session for the reason the muted kinds are: every session shares one origin, so
        an unscoped store would be one conversation's drafts turning up in all of them.
        """
        url, service = console
        await self.opened(console, page)
        await self.keep(page, "meant for the first one")
        other = await started(service, "a different conversation", DEFAULT_CHOICE)
        await page.goto(f"{url}/sessions/{other.id}", wait_until="load")

        await expect(page.locator(".shelf__take")).to_have_count(0)

    async def test_a_branch_inherits_what_its_parent_kept(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The copy `Service.fork` cannot make, because the server has never seen a draft. It says which
        conversation this one came from and the page holding both stores does the rest.
        """
        url, service = console
        session, _ = await self.opened(console, page)
        await self.keep(page, "worth carrying across")
        forked = await service.fork(session, at=1, chosen=DEFAULT_CHOICE, said="try again")
        assert forked is not None
        await registered(service, forked.id)
        await page.goto(f"{url}/sessions/{forked.id}", wait_until="load")

        await expect(page.locator(".shelf__take")).to_have_text("worth carrying across")

    async def test_a_branch_that_clears_its_shelf_does_not_get_the_parent_s_back(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        An empty shelf is a decision and a missing one is not, so the inheritance has to tell them
        apart. Read as "nothing here", a reader who cleared theirs would be handed it back on every
        load with no way to refuse it.
        """
        url, service = console
        session, _ = await self.opened(console, page)
        await self.keep(page, "worth carrying across")
        forked = await service.fork(session, at=1, chosen=DEFAULT_CHOICE, said="try again")
        assert forked is not None
        await registered(service, forked.id)
        await page.goto(f"{url}/sessions/{forked.id}", wait_until="load")
        await page.click(".shelf__drop >> nth=0")
        await expect(page.locator(".shelf__take")).to_have_count(0)
        await page.reload(wait_until="load")

        await expect(page.locator(".shelf__take")).to_have_count(0)


class TestFollowingTheEnd:
    """
    Following is being at the end, so scrolling decides it in both directions.

    A mode you leave by scrolling up and return to by scrolling back down, rather than a setting to
    remember you switched off. None of it can be seen in markup: the toggle's `aria-pressed` is
    written by the script, and what drives it is where a real box has actually been scrolled to.
    """

    async def a_long_conversation(self, console: tuple[str, Service], page: Page) -> None:
        """Enough turns that the transcript scrolls, which is the precondition for any of this."""
        url, service = console
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        for turn in range(12):
            # The two records a pass writes for a turn, by hand: which entry it took, and what came
            # of it. The `console` fixture runs no worker, so a test that wants twelve settled turns
            # writes what twelve passes would have.
            recorded = await service.checkpointer.load(session.id)
            await service.checkpointer.supply(
                session.id, opened_key(turn), [key for key in recorded if key.startswith(INBOX)][turn]
            )
            await service.checkpointer.supply(
                session.id,
                messages_key(turn),
                recorded_turn(
                    {"kind": "response", "parts": [{"part_kind": "text", "content": f"answer {turn} " + "x " * 400}]}
                ),
            )
            await service.say(session.id, f"and then {turn}")
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        # The precondition itself, rather than a count standing in for it: none of this means
        # anything in a transcript short enough to have no end to be away from.
        assert await page.eval_on_selector("#transcript", "box => box.scrollHeight > box.clientHeight + 100")

    def toggle(self, page: Page) -> Locator:
        return page.locator('[data-follow="toggle"]')

    async def test_a_page_opens_following_the_end(self, page: Page, console: tuple[str, Service]) -> None:
        await self.a_long_conversation(console, page)
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "true")

    async def test_scrolling_away_from_the_end_stops_following(self, page: Page, console: tuple[str, Service]) -> None:
        await self.a_long_conversation(console, page)
        await page.eval_on_selector("#transcript", "box => { box.scrollTop = 0 }")
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "false")

    async def test_scrolling_back_to_the_end_starts_following_again(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        await self.a_long_conversation(console, page)
        await page.eval_on_selector("#transcript", "box => { box.scrollTop = 0 }")
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "false")
        await page.eval_on_selector("#transcript", "box => { box.scrollTop = box.scrollHeight }")
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "true")

    async def test_sending_a_message_starts_following_again(self, page: Page, console: tuple[str, Service]) -> None:
        """
        Whatever the reader had scrolled up to check before typing, what they want to see now is the
        answer to what they just sent.
        """
        await self.a_long_conversation(console, page)
        await page.eval_on_selector("#transcript", "box => { box.scrollTop = 0 }")
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "false")
        await page.fill("textarea[name=prompt]", "one more thing")
        await page.press("textarea[name=prompt]", "Shift+Enter")
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "true")

    async def test_landing_on_the_last_panel_does_not_start_following(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The trap the two-way rule opens. The dock's "to the end" lands on the last panel, which
        scrolls to the bottom, so a listener that could not tell this file's scrolls from the
        reader's would switch following back on at the very moment the reader asked to be put on a
        particular panel instead.
        """
        await self.a_long_conversation(console, page)
        await page.eval_on_selector("#transcript", "box => { box.scrollTop = 0 }")
        await page.click('[data-leap="end"]')
        await expect(page.locator("[data-landed]")).to_have_count(1)
        await expect(self.toggle(page)).to_have_attribute("aria-pressed", "false")


class TestTheFocusRingHasRoomToBeDrawn:
    """
    A scroller clips, so a control flush with its edge loses its focus ring on that side.

    `.setup` asks for `overflow-y: auto`, which computes `overflow-x` to `auto` as well, and the two
    fields that fill their grid columns are where that shows: the ring is drawn *outside* the box, so
    without room inside the scroller the gold is cut off at the left of one field and the right of the
    other. The room is `.picker`'s inline padding, inside the scroller for the reason its block
    padding is.

    A browser, because both renderings are correct markup and each is a correct picture of some page:
    what is wrong with the clipped one is three pixels of gold, and what it is measured against is the
    ring's own reach rather than a number written down twice.
    """

    async def test_the_fields_are_not_flush_with_the_box_that_scrolls(
        self, page: Page, working: tuple[str, Service]
    ) -> None:
        # Narrower than the suite's own window, because that one is wide enough for the picker to sit
        # inside its `max-width` with room to spare on either side, where every narrower window has it
        # filling the scroller. The clipped case is the common one, so it is the one to measure.
        await page.set_viewport_size({"width": 1100, "height": 900})
        await a_start_page(working, page)
        await page.focus(".basis__box")

        room = await page.evaluate(
            """() => {
                const setup = document.querySelector('.setup');
                const edge = setup.getBoundingClientRect().left + setup.clientLeft;
                const focused = getComputedStyle(document.activeElement);
                return {
                  reach: parseFloat(focused.outlineWidth) + parseFloat(focused.outlineOffset),
                  left: document.querySelector('.basis__box').getBoundingClientRect().left - edge,
                  right:
                    edge +
                    setup.clientWidth -
                    document.querySelector('[name="branch"]').getBoundingClientRect().right,
                };
            }"""
        )

        assert room["reach"] > 0, "the focused field has no ring to leave room for"
        assert room["left"] >= room["reach"]
        assert room["right"] >= room["reach"]


class TestTheLineWhereNothingIsHappening:
    """
    The box at the end of a transcript that says why nothing is happening, and the two things on it
    a still cannot show.

    Both are script, and both are the difference between a page that is waiting and a page that is
    stuck. The countdown moves while the page holds still, which is exactly the interval the stream
    sends nothing in, so a server-rendered figure would sit at its first value for the whole wait.
    The copy button is seated by the same walk that seats a panel's, and it is outside a panel, so a
    change to that walk takes it away with nothing else noticing.
    """

    async def test_the_reason_is_copyable(self, page: Page, gallery: str) -> None:
        """
        Which is the whole point of setting it apart: it is the one thing on the page somebody pastes
        into an issue, a search, or a message to whoever wrote the plugin.
        """
        await page.goto(f"{gallery}/failed.html", wait_until="load")
        reason = page.locator("#attention .attention__reason")
        button = reason.locator("button.copy")

        await expect(button).to_have_count(1)
        assert (await reason.inner_text()).startswith("PluginFailed"), "and the reason is really under it"

    async def test_the_countdown_moves_while_the_page_holds_still(self, page: Page, gallery: str) -> None:
        """
        Driven by moving the clock rather than by waiting, so this costs no wall time and cannot be
        flaky: what is under test is that the figure is computed from `data-due` and the time since
        the element was seen, not that a timer fires.
        """
        await page.goto(f"{gallery}/failed.html", wait_until="load")
        due = page.locator("#attention .attention__due")
        before = await due.inner_text()

        moved = await page.evaluate(
            """() => {
                const line = document.getElementById("attention");
                line.seenAt = Date.now() - 300_000;
                return Number(line.dataset.due);
            }"""
        )
        await page.evaluate("() => document.dispatchEvent(new CustomEvent('htmx:after:swap'))")

        assert moved > 0, "the control: the server really did hand over a figure to count down from"
        await expect(due).not_to_have_text(before)

    async def test_a_page_with_nothing_wrong_draws_no_such_line(self, page: Page, gallery: str) -> None:
        """
        The control the rest of this rests on. A line drawn through healthy turns would be a console
        that cries wolf, and every ordinary page here is one where nothing is wrong.
        """
        await page.goto(f"{gallery}/answering.html", wait_until="load")

        await expect(page.locator("#attention")).to_have_count(0)
