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
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import tool_key
from mainplate.service import Service
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
    origin, so a shared context would let one test's folds, theme and set-aside kinds decide what
    the next test renders.
    """
    context = await browser.new_context(viewport=VIEWPORT)
    try:
        yield await context.new_page()
    finally:
        await context.close()


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
        await page.click('button[data-step="1"]:not([data-side])')
        await expect(page.locator(LANDED)).to_have_count(1)

    async def test_a_permalink_followed_after_stepping_moves_the_landing(self, page: Page, gallery: str) -> None:
        # The other half of the same disagreement, arrived at the other way round: the dock marks a
        # panel the URL does not name, and then the URL names a different one.
        await page.goto(f"{gallery}/session.html", wait_until="load")
        await page.click('button[data-step="1"]:not([data-side])')
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
