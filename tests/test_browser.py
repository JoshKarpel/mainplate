from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import pytest_asyncio
from conftest import DEFAULT_CHOICE
from conftest import LEASE
from conftest import already
from playwright.async_api import Browser
from playwright.async_api import Locator
from playwright.async_api import Page
from playwright.async_api import ViewportSize
from playwright.async_api import async_playwright
from playwright.async_api import expect
from without_http import serving

from mainplate.app import build_app
from mainplate.app import open_store
from mainplate.catalogue import Catalogues
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import steers_in
from mainplate.conversation import tool_key
from mainplate.service import Service
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
    async with open_store(tmp_path / "mainplate.db", LEASE, catalogues) as service:
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
        await page.goto(f"{gallery}/session.html", wait_until="load")
        assert await page.locator(".rule").count() > await page.locator(".rule--turn").count()
        await page.click('button[data-leap="start"]')
        await page.click('button[data-step="1"][data-stop="turn"]')
        landed = page.locator(".rule[data-landed]")
        await expect(landed).to_have_count(1)
        await expect(landed).to_have_attribute("id", "rule-1")

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
    ("start.html", frozenset({"prompt", "endpoint", "model", "thinking"})),
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
BOXES = (("start.html", "composer"), ("session.html", "composer"), ("forking.html", "forking"))


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
        # than starting a session on a message nobody typed.
        await page.goto(f"{gallery}/start.html", wait_until="load")
        await page.evaluate(WATCH_SUBMITS)
        await page.press("textarea[name=prompt]", "Shift+Enter")
        assert await page.evaluate("() => window.submitted") == []


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

    Here rather than in `scripts/shoot.mjs`, which prints the same overflow beside the screenshot it
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
        # The transcript may scroll its own wide blocks and the session strip scrolls itself; what
        # must never move is the document, which has nowhere to overflow to.
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


# One response of a turn, as the capability records it partway through: the model reasoned and asked
# for two files at once. Two calls because that is the state worth watching arrive - they run
# together, so one comes back while the other is still out.
PARTWAY = {
    "kind": "response",
    "parts": [
        {"part_kind": "thinking", "content": "Two files to look at."},
        {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "a.py"}, "tool_call_id": "call-1"},
        {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "b.py"}, "tool_call_id": "call-2"},
    ],
}


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
        session = await service.start("what is a mainplate", DEFAULT_CHOICE)
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
        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), "the first file")
        await expect(page.locator(".tool .waiting")).to_have_count(1)
        await expect(page.locator(".tool").first).to_contain_text("the first file")

    async def test_a_reader_keeps_what_they_unfolded_while_the_turn_goes_on(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The reason every message is morphed rather than swapped. A turn records several times a
        second while it runs, so a replacement would shut a call the reader opened to watch, over
        and over, exactly while they were reading it.
        """
        service = await self.started(console, page)
        await service.checkpointer.supply(self.session, model_key(0, 0), PARTWAY)
        opened = page.locator("details.tool").first
        await expect(opened).to_have_attribute("open", "")
        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), "the first file")
        await service.checkpointer.supply(self.session, tool_key(0, "call-2"), "the second file")
        # Both are back, so the server renders both closed; the one the reader has open stays open.
        await expect(page.locator("details.tool")).to_have_count(2)
        await expect(opened).to_have_attribute("open", "")

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
        await service.checkpointer.supply(self.session, tool_key(0, "call-1"), "the first file")
        await expect(marked).to_have_count(1)
        assert await marked.first.get_attribute("data-kind") == "tool"

    async def test_a_conversation_is_not_marked_top_to_bottom_when_it_is_opened(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        Every panel is new to the script on a first render, so the mark has to be suppressed there
        rather than fall out of the comparison. A reader opening a long conversation would otherwise
        watch the whole of it flash at them.
        """
        url, service = console
        session = await service.start("what is a mainplate", DEFAULT_CHOICE)
        await service.checkpointer.supply(session.id, model_key(0, 0), PARTWAY)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator(".panel[data-kind=thinking]")).to_have_count(1)
        await expect(page.locator(".panel[data-fresh]")).to_have_count(0)


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
        session = await service.start("what is a mainplate", DEFAULT_CHOICE)
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
        above = await summary.bounding_box()
        below = await record.bounding_box()
        rule = await page.locator(".rule").first.bounding_box()
        assert above is not None
        assert below is not None
        assert rule is not None
        assert below["y"] >= above["y"] + above["height"]
        assert below["width"] > rule["width"] / 2


class TestWhereTheComposerSendsTo:
    """
    That pressing Fork lands the reader in a *different* session, driven by a real htmx.

    Two things this rests on are htmx's rather than ours, and both were read out of a minified
    bundle: that it appends the submit button's own `name`/`value` to the request, and that it
    honours `HX-Redirect` by navigating. Either being wrong looks identical in the markup and in
    every in-memory test, and shows up only as a button that quietly sends to the wrong place.
    """

    async def a_conversation(self, console: tuple[str, Service], page: Page) -> str:
        url, service = console
        session = await service.start("what is a mainplate", DEFAULT_CHOICE)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
        return session.id

    async def test_forking_navigates_to_a_new_session(self, page: Page, console: tuple[str, Service]) -> None:
        session = await self.a_conversation(console, page)
        await page.fill(".composer textarea", "try it another way")
        await page.click(".sender__caret")
        await page.click('.sender__option[value="fork"]')

        await page.wait_for_url(lambda url: session not in url)
        assert "/sessions/" in page.url
        await expect(page.locator("#transcript")).to_contain_text("try it another way")
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")

    async def test_stepping_aside_and_coming_back_returns_to_where_it_started(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The whole round trip, which no single request shows: two navigations and a message that ends
        up in the conversation the reader left rather than the one they were in.
        """
        session = await self.a_conversation(console, page)
        await page.fill(".composer textarea", "let me check something")
        await page.click(".sender__caret")
        await page.click('.sender__option[value="aside"]')
        await page.wait_for_url(lambda url: session not in url)

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
        await self.a_conversation(console, page)
        await page.click(".sender__caret")
        await expect(page.locator(".sender__more")).to_have_attribute("open", "")
        await page.click(".composer textarea")
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")

    async def test_escape_shuts_the_menu(self, page: Page, console: tuple[str, Service]) -> None:
        await self.a_conversation(console, page)
        await page.click(".sender__caret")
        await expect(page.locator(".sender__more")).to_have_attribute("open", "")
        await page.keyboard.press("Escape")
        await expect(page.locator(".sender__more")).not_to_have_attribute("open", "")

    async def test_sending_stays_in_this_conversation(self, page: Page, console: tuple[str, Service]) -> None:
        """
        The control beside it, on the same form, posting no disposition at all. Asserted here rather
        than left to the in-memory tests because what makes the two differ is which button htmx
        treats as the submitter, and that is a browser behaviour.
        """
        session = await self.a_conversation(console, page)
        await page.fill(".composer textarea", "and another thing")
        await page.click(".sender > button")

        await expect(page.locator("#transcript")).to_contain_text("and another thing")
        assert session in page.url, "sending swaps the conversation rather than leaving it"

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
        session = await self.a_conversation(console, page)
        await page.fill(".composer textarea", "actually, be brief")
        await page.click(".sender > button")

        await expect(page.locator('.panel[data-kind="steering"]')).to_have_count(1)
        await expect(page.locator('.panel[data-kind="steering"]')).to_contain_text("actually, be brief")
        assert steers_in(await service.checkpointer.load(session), 0) == ("actually, be brief",)

    async def test_the_menu_offers_the_wait_only_while_something_is_being_answered(
        self, page: Page, console: tuple[str, Service]
    ) -> None:
        """
        The one answer the record cannot settle, offered exactly where it differs from Send.

        With nothing running, waiting for the next turn *is* what Send does, so a control for it would
        be a second way to ask one question - which is what this console removes wherever it finds it.
        """
        _, service = console
        session = await self.a_conversation(console, page)
        await page.click(".sender__caret")
        await expect(page.locator('.sender__option[value="next"]')).to_have_count(1)

        await service.checkpointer.supply(
            session, messages_key(0), [{"kind": "response", "parts": [{"part_kind": "text", "content": "a plate"}]}]
        )
        await page.reload(wait_until="load")
        await page.click(".sender__caret")
        await expect(page.locator('.sender__option[value="next"]')).to_have_count(0)


class TestTheShelf:
    """
    Text written and not sent, kept for one conversation.

    None of it can be seen anywhere but a browser: what it holds lives in `localStorage`, the list is
    rendered by the script, and the property that matters most - that a branch inherits its parent's
    shelf - is a copy between two stores that only exist once a page has been opened on each.
    """

    async def opened(self, console: tuple[str, Service], page: Page) -> tuple[str, Service]:
        url, service = console
        session = await service.start("what is a mainplate", DEFAULT_CHOICE)
        await page.goto(f"{url}/sessions/{session.id}", wait_until="load")
        await expect(page.locator("#transcript")).to_contain_text("what is a mainplate")
        return session.id, service

    async def keep(self, page: Page, said: str) -> None:
        """Keeping is an answer in the send menu, so it is reached the way any of them is."""
        await page.fill(".composer textarea", said)
        await page.click(".sender__caret")
        await page.click('[data-shelf="keep"]')

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
        other = await service.start("a different conversation", DEFAULT_CHOICE)
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
        session = await service.start("what is a mainplate", DEFAULT_CHOICE)
        for turn in range(12):
            await service.checkpointer.supply(
                session.id,
                messages_key(turn),
                [{"kind": "response", "parts": [{"part_kind": "text", "content": f"answer {turn} " + "x " * 400}]}],
            )
            await service.say(session.id, turn=turn + 1, said=f"and then {turn}")
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
