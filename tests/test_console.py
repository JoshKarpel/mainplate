from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from re import sub

import pytest
from calling import calling
from conftest import CONFIG
from conftest import DEFAULT_CHOICE
from conftest import INSTRUCTIONS
from conftest import WHEN
from conftest import Provider
from conftest import already
from conftest import answered_with
from conftest import came_back
from conftest import passing
from conftest import recorded_turn
from conftest import registered
from conftest import snapshotted
from without_asgi import ASGIApp
from without_durability.interfaces import INBOX

from mainplate import records
from mainplate.agent import RETENTION
from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.app import build_app
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import Offering
from mainplate.console import LONGEST_PROMPT
from mainplate.console import NotAMessage
from mainplate.console import Sending
from mainplate.console import parse_form_prompt
from mainplate.console import parse_form_send
from mainplate.console import posted_isolation
from mainplate.console import posted_workspace
from mainplate.conversation import DECLARED_KEY
from mainplate.conversation import REPOSITORY_DECLARED_KEY
from mainplate.conversation import Disposition
from mainplate.conversation import choice_of
from mainplate.conversation import conversing
from mainplate.conversation import heard_key
from mainplate.conversation import instructions_key
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import opened_key
from mainplate.conversation import recorded_instructions
from mainplate.conversation import recorded_prompt
from mainplate.conversation import recorded_steer
from mainplate.conversation import refused_key
from mainplate.conversation import registered_in
from mainplate.conversation import tool_key
from mainplate.conversation import tree_key
from mainplate.pages import CACHE_ID
from mainplate.pages import TRANSCRIPT_ID
from mainplate.plugins.asking import Declaring
from mainplate.plugins.asking import recorded_declaration
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.protocol import Payload
from mainplate.plugins.running import PluginFailed
from mainplate.reference import Cost
from mainplate.reference import Facts
from mainplate.reference import Reference
from mainplate.sandbox import Filesystem
from mainplate.service import Service
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import TITLE_LENGTH
from mainplate.sessions import read_tending
from mainplate.snapshots import Worktree
from mainplate.tending import AGAIN
from mainplate.tending import SETTLE_FIELD
from mainplate.tending import SETTLED


def delivered_in(recorded: Mapping[str, object]) -> list[object]:
    """
    Everything put into a session's inbox, in the order it arrived.

    What a test asserting on what was recorded wants, now that no key says which turn a message
    belongs to: which turn takes one is the pass's to decide, so what a handler can be held to is
    what it delivered and in what order.
    """
    return [held for key, held in recorded.items() if key.startswith(INBOX)]


async def taken(service: Service, session: str) -> int:
    """
    The next message a session has waiting, taken into a turn of its own but not yet answered.

    Which is what a pass's first act is, written by hand here for the reason the whole `app` fixture
    runs without a worker: a test asserting on a turn in flight must not be racing one. Until this
    happens a message is only queued, which is a different state and one these tests are rarely about.

    No registration is written here: a session reaching this came through `a_session`, which answers
    the settings step as part of starting one, and the key is write-once.
    """
    recorded = await service.checkpointer.load(session)
    turn = len([key for key in recorded if key.endswith(":opened")])
    await service.checkpointer.supply(
        session, opened_key(turn), [key for key in recorded if key.startswith(INBOX)][turn]
    )
    return turn


async def answered(service: Service, session: str, *said: object) -> int:
    """The same, answered: the cursor saying which entry the turn took and the messages it produced."""
    turn = await taken(service, session)
    await service.checkpointer.supply(session, messages_key(turn), recorded_turn(*said))
    return turn


async def a_session(app: ASGIApp, service: Service, said: str = "what is a mainplate", title: str | None = None) -> str:
    """
    A session started the way a browser starts one, with its first message in it.

    **Two posts, because creating one and saying the first thing in it are separate requests**: a
    repository's plugins cannot be named until its worktree is planted, so creation records the
    choice and the message box is on the session's own page. Written once here rather than at every
    call site, and a test that is about the split posts to `/sessions` itself.

    The settings step in between is answered by writing what a pass would have written, which is two
    empty registrations: this app's service runs no plugins, so there is nothing to confirm, but a
    registration is still what takes a session past the step and a page drawn without one is the step.
    `TestLoadingASessionsPlugins` is where the step itself is driven, through the route.
    """
    async with calling(app) as caller:
        answered = await caller.post("/sessions", starting_form(title=title))
        assert answered.status == 303
        session = answered.location.rsplit("/", 1)[-1]
        assert (await caller.post(f"/sessions/{session}/messages", {"prompt": said})).status == 200
        await registered(service, session)
        return session


async def watched(app: ASGIApp, session: str) -> str:
    """
    The transcript alone, as the page's live connection sends it.

    The one way to see the conversation with no document around it, which several tests below need
    rather than prefer: a session is named after its first message, so a page also carries that text
    in the sidebar, where it is escaped as an ordinary child. Asserting on the page would pass on
    the sidebar's copy whatever the transcript did with the same text, which is a check that cannot
    fail measuring the wrong thing.

    The first message and then out, because a stream has no end: what it opens with is current
    state, which is the whole of what these want.
    """
    async with calling(app) as caller, caller.watching(f"/fragments/stream?session={session}") as events:
        return (await anext(events)).data


def blocks_carrying_markdown(region: str) -> list[dict[str, str | None]]:
    """
    Every element carrying a `data-markdown`, as the attributes a browser would actually see on it.

    Parsed rather than searched, because what these assert is a property of the *document*: a value
    that broke out of its attribute would put a second attribute on the element and leave the value
    truncated, and both are invisible to any assertion about which escaped spelling appears in the
    text.
    """
    found: list[dict[str, str | None]] = []

    class Reading(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            held = dict(attrs)
            if "data-markdown" in held:
                found.append(held)

    reading = Reading()
    reading.feed(region)
    reading.close()
    return found


def opening_lines(region: str) -> list[str]:
    """
    What each panel's row says it stands for, as the text a browser would show in it.

    Parsed for the same reason `blocks_carrying_markdown` is. The opening line is the *source* of
    what is under it rather than the rendering, so a message written to be markup reaches this
    element as characters, and what has to hold is that they are still characters when the document
    is read back - which is a statement about the parse and not about which escaped spelling appears.
    """
    found: list[str] = []

    class Reading(HTMLParser):
        inside = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if self.inside:
                self.inside += 1
            elif dict(attrs).get("class") == "opening":
                self.inside = 1
                found.append("")

        def handle_endtag(self, tag: str) -> None:
            self.inside = max(0, self.inside - 1)

        def handle_data(self, data: str) -> None:
            if self.inside:
                found[-1] += data

    reading = Reading()
    reading.feed(region)
    reading.close()
    return found


# A catalogue offering something else entirely, for the session whose pair went away. It stands in
# for both ways that happens - an endpoint edited out of the file, and an endpoint that stopped
# listing a model - because the console cannot tell them apart and does not try to.
OTHER_CATALOGUE = Catalogue(
    offered={
        "elsewhere": Offering(
            endpoint="elsewhere",
            format="anthropic",
            url=None,
            models=(Listed(id="plain/different", label="Different", provider="plain"),),
        )
    },
    default=Choice(endpoint="elsewhere", model="plain/different"),
)


def starting_form(chosen: Choice = DEFAULT_CHOICE, title: str | None = None) -> dict[str, str]:
    """
    What the new-session form posts: the pair chosen to answer it, and maybe a name.

    **No message**, which is the change the plugin protocol forced: a session is created and then
    typed into, so this page decides what a session *is* and nothing about what is said in it.

    The name is omitted when it is `None`, which is what a browser sends for a field left empty:
    `parse_qs` drops empty values, so an untouched box never reaches the handler as a field at all.
    """
    posted = {"endpoint": chosen.endpoint, "model": chosen.model}
    return posted if title is None else {**posted, TITLE_FIELD: title}


class TestReadingAForm:
    def test_a_message_is_the_field_a_browser_sends(self) -> None:
        assert parse_form_prompt(b"prompt=what+is+a+mainplate") == "what is a mainplate"

    def test_surrounding_whitespace_is_not_part_of_the_message(self) -> None:
        assert parse_form_prompt(b"prompt=%20%20spaced%20%20") == "spaced"

    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            (b"", "no field at all"),
            (b"prompt=", "an empty box"),
            (b"prompt=%20%20", "only whitespace"),
            (b"other=something", "some other field"),
        ],
    )
    def test_what_is_not_a_message_is_refused(self, raw: bytes, why: str) -> None:
        with pytest.raises(NotAMessage):
            parse_form_prompt(raw)

    def test_a_body_too_large_to_be_a_message_is_refused_before_it_is_parsed(self) -> None:
        with pytest.raises(NotAMessage):
            parse_form_prompt(b"prompt=" + b"x" * LONGEST_PROMPT)


class TestReadingWhereAMessageIsGoing:
    def test_a_message_with_no_disposition_is_sent_here(self) -> None:
        """
        What Send has always done, and what a form predating the control still means.

        It is also what Shift-Enter posts: `requestSubmit()` with no submitter carries no button's
        name at all, so the keyboard shortcut arrives here rather than at whichever destination was
        pressed last.
        """
        assert parse_form_send(b"prompt=go") == Sending(said="go", where=Disposition.HERE)

    def test_the_submit_button_s_own_value_is_where_it_goes(self) -> None:
        assert parse_form_send(b"prompt=go&disposition=fork") == Sending(said="go", where=Disposition.FORK)

    def test_a_destination_this_console_does_not_offer_is_refused(self) -> None:
        """
        Refused rather than defaulted to `HERE`, which is the one place a default would be wrong:
        guessing puts somebody's message in a conversation they did not address it to, and the
        message is already sent by the time anybody could notice.
        """
        with pytest.raises(NotAMessage):
            parse_form_send(b"prompt=go&disposition=sideways")

    def test_a_message_is_still_required_whatever_it_is_addressed_to(self) -> None:
        with pytest.raises(NotAMessage):
            parse_form_send(b"disposition=fork")


class TestTheConsole:
    async def test_the_start_page_offers_the_choices_and_creates_nothing(self, app: ASGIApp, service: Service) -> None:
        """
        **No message box here**, which is the visible half of the two-step creation.

        A plugin's settings are the controls on its card, its card comes back from `describe`, and a
        repository's plugin cannot be described until its worktree is planted - which a pass does. So
        this page decides what a session *is* and the box is on the session's own page.
        """
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert answered.status == 200
        assert 'class="picker"' in answered.text
        assert 'class="composer"' not in answered.text
        assert ">Create session</button>" in answered.text
        assert await service.listed() == ()

    async def test_the_first_message_creates_a_session_and_redirects_to_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app, service)
        listed = await service.listed()
        assert [(each.id, each.title, each.created_at) for each in listed] == [(session, "what is a mainplate", WHEN)]

    async def test_the_first_message_is_waiting_in_the_checkpoint(self, app: ASGIApp, service: Service) -> None:
        """
        A `Steer` and not a `Prompt`, because the first message goes through the composer like every
        other one.

        Nothing about that is a weaker state: Send decides nothing, so what a message *becomes* is
        the pass's answer, and a steer arriving at a session with nothing running opens the next
        turn. What changed is only who wrote it - creation used to say the first thing itself, and
        now there is somebody at a box.
        """
        session = await a_session(app, service)
        assert delivered_in(await service.checkpointer.load(session)) == [recorded_steer("what is a mainplate")]

    async def test_an_unanswered_session_renders_the_question_and_watches_for_the_answer(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert answered.status == 200
        assert "what is a mainplate" in answered.text
        assert f'hx-sse:connect="/fragments/stream?session={session}"' in answered.text

    async def test_the_connection_is_held_outside_everything_that_swaps(self, app: ASGIApp, service: Service) -> None:
        """
        The connection is the page's, not the transcript's, and the difference is load-bearing.

        Every message morphs the transcript, so a connection held by that region would be one its
        own traffic kept tearing down and re-establishing. Pinned as an ordering because that is
        what a reader of the markup can check: the connecting element opens before the region it
        updates and is closed before it, so it cannot be inside it.
        """
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        connecting = answered.text.index("hx-sse:connect")
        assert answered.text.index('id="stream"') < connecting
        assert connecting < answered.text.index('id="transcript"')

    async def test_the_system_prompt_is_drawn_under_the_rule_that_opens_its_stretch(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The order a reader meets it in: the boundary, then what the model is told from here, then the
        message it is told it about. Pinned as an ordering because that is what a reader of the markup
        can check, and the placement is the whole of what moved it off the top of the page.
        """
        session = await a_session(app, service)
        await service.checkpointer.supply(
            session, instructions_key(0), recorded_instructions("what this session is answered under")
        )
        drawn = await watched(app, session)

        assert "what this session is answered under" in drawn
        assert drawn.index('id="rule-0"') < drawn.index('id="system-prompt-0"')
        assert drawn.index('id="system-prompt-0"') < drawn.index('data-kind="prompt"')

    async def test_the_system_prompt_is_drawn_as_the_markdown_it_is(self, app: ASGIApp, service: Service) -> None:
        """
        What is under the fold is `.md` files, so its headings and lists are the structure their
        authors wrote. The source rides along as `data-markdown`, which is what the copy button hands
        back, so drawing it costs nothing about the claim that this is what was sent.
        """
        said = "## Conventions\n\n- say less\n"
        session = await a_session(app, service)
        await service.checkpointer.supply(session, instructions_key(0), recorded_instructions(said))
        drawn = await watched(app, session)

        assert "<h2>Conventions</h2>" in drawn
        assert "<li>say less</li>" in drawn
        assert said in [held["data-markdown"] for held in blocks_carrying_markdown(drawn)]

    async def test_a_stretch_nothing_has_composed_for_yet_draws_the_panel_with_no_prompt_in_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        A session's first message is queued before the pass that composes for it has planted a
        worktree to read, so the panel is there from the moment the message is and fills in on the
        swap the first answer arrives on. Asserted against the *fold*, because the panel is drawn
        either way and what tells the two apart is whether there is anything to unfold.
        """
        drawn = await watched(app, await a_session(app, service))

        assert 'id="system-prompt-0"' in drawn
        assert 'id="system-prompt-0-fold"' not in drawn

    @pytest.mark.parametrize("settled", [True, False])
    async def test_the_transcript_asks_for_nothing_on_its_own(
        self, app: ASGIApp, service: Service, settled: bool
    ) -> None:
        """
        The region is markup and nothing else, whether or not a turn is in flight.

        It neither fetches itself nor decides when to, so there is no trigger to get right and none
        to remember to remove. Asserted against the region's *own opening tag* rather than against
        the page, which is the difference between a check and a spelling: a settled panel carries a
        disclosure that fetches what the checkpoint holds behind it, so `hx-` appears all over this
        page and only here does it mean the conversation asking for itself.
        """
        session = await a_session(app, service)
        if settled:
            await answered(
                service, session, {"kind": "response", "parts": [{"part_kind": "text", "content": "it is a plate"}]}
            )
        async with calling(app) as caller:
            page = (await caller.get(f"/sessions/{session}")).text
        drawn = page[page.index(f'<div class="transcript" id="{TRANSCRIPT_ID}"') :]
        opening = drawn[: drawn.index(">") + 1]
        assert "hx-" not in opening, f"the region asks for something on its own: {opening}"

    async def test_a_stream_sends_the_conversation_as_soon_as_it_is_opened(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        What makes a reconnect need no replay: the only thing this ever sends is current state, so
        a page that has just connected and one that has been connected for an hour are handed the
        same thing.
        """
        session = await a_session(app, service)
        async with calling(app) as caller, caller.watching(f"/fragments/stream?session={session}") as events:
            first = await anext(events)
        assert "what is a mainplate" in first.data
        assert f'hx-target="#{TRANSCRIPT_ID}"' in first.data
        assert 'hx-swap="outerMorph"' in first.data

    async def test_a_message_names_the_region_it_is_for_rather_than_the_connection(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        A message made only of partials leaves the connecting element alone, which is what lets one
        connection drive several regions and what keeps the sink inert.
        """
        session = await a_session(app, service)
        async with calling(app) as caller, caller.watching(f"/fragments/stream?session={session}") as events:
            first = await anext(events)
        assert first.data.startswith("<hx-partial")
        assert first.type == "message", "unnamed, so htmx swaps it rather than firing an event"

    async def test_a_stream_for_a_session_nobody_started_is_refused_rather_than_opened(self, app: ASGIApp) -> None:
        """
        A refusal has to be a refusal. An event stream that opened and ended would be reconnected
        by the client forever, where a `404` is an answer it can act on.
        """
        async with calling(app) as caller:
            answered = await caller.get("/fragments/stream?session=nothing-here")
        assert answered.status == 404

    async def test_a_message_into_a_session_answers_with_the_transcript_alone(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session}/messages", {"prompt": "and another thing"})
        assert answered.status == 200
        assert "<html" not in answered.text
        assert "and another thing" in answered.text

    async def test_a_second_message_is_delivered_as_one_the_running_turn_may_take(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        Send does not ask which moment it is, and neither does the server: the message goes in the
        queue and the pass that takes it decides.

        The page it was typed on was rendered from a checkpoint that has moved since, so a reader
        choosing between steering and queueing would have been choosing against a state that no longer
        held; so would a read here, since a turn can end between the read and the write. What is
        recorded is a message that *may* be folded in, which is the whole of what Send means.
        """
        session = await a_session(app, service)
        async with calling(app) as caller:
            await caller.post(f"/sessions/{session}/messages", {"prompt": "and another thing"})
        recorded = await service.checkpointer.load(session)
        assert [held for key, held in recorded.items() if key.startswith(INBOX)][-1] == recorded_steer(
            "and another thing"
        )

    async def test_a_steered_message_is_on_the_page_before_any_model_has_seen_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The property that makes Send safe to let steer, and the one a plausible reading breaks.

        A steer lands in `turn:{n}:messages` only when the turn *ends*, so a transcript reading a
        running turn from its steps alone would take somebody's message and show nothing at all until
        the reply finished. It is drawn from the entry instead, which exists the instant it is
        delivered.
        """
        session = await a_session(app, service)
        await taken(service, session)
        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session}/messages", {"prompt": "actually, be brief"})
        assert "actually, be brief" in answered.text
        assert 'data-kind="steer"' in answered.text

    async def test_a_steer_already_put_to_the_model_is_drawn_above_the_answer_it_shaped(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        `turn:{n}:heard:{i}` is the cursor that says where it went, so a running turn puts it where
        the settled reading will: above the response it was appended to rather than at the end.
        """
        session = await a_session(app, service)
        await taken(service, session)
        async with calling(app) as caller:
            await caller.post(f"/sessions/{session}/messages", {"prompt": "actually, be brief"})
        steered = [key for key in await service.checkpointer.load(session) if key.startswith(INBOX)][-1]
        await service.checkpointer.supply(session, heard_key(0, 0), steered)
        await service.checkpointer.supply(session, model_key(0, 0), answered_with(ANSWERED[0]))
        region = await watched(app, session)
        assert region.index("actually, be brief") < region.index("it is a plate")

    async def test_waiting_for_the_next_turn_queues_rather_than_steering(self, app: ASGIApp, service: Service) -> None:
        """
        The one answer the checkpoint cannot settle, which is why it stays an explicit control.

        Wanting to be taken up *after* the reply that is coming is an intent no record carries, so
        this is the override and everything else about Send is the pass's to decide. What it records
        is the other kind of message: one a draining pass stops at rather than folds in.
        """
        session = await a_session(app, service)
        async with calling(app) as caller:
            await caller.post(f"/sessions/{session}/messages", {"prompt": "and another thing", "disposition": "next"})
        recorded = await service.checkpointer.load(session)
        assert [held for key, held in recorded.items() if key.startswith(INBOX)][-1] == recorded_prompt(
            "and another thing"
        )

    async def test_a_message_queued_behind_an_unanswered_one_is_still_shown(
        self, app: ASGIApp, service: Service
    ) -> None:
        """It is recorded and it will be answered, so a page that hid it would be lying about it."""
        session = await a_session(app, service, "the first thing")
        async with calling(app) as caller:
            await caller.post(f"/sessions/{session}/messages", {"prompt": "the second thing", "disposition": "next"})
            answered = await caller.get(f"/sessions/{session}")
        assert "the first thing" in answered.text
        assert "the second thing" in answered.text

    async def test_the_sidebar_lists_every_session_newest_first(self, app: ASGIApp, service: Service) -> None:
        await a_session(app, service, "the older one")
        await a_session(app, service, "the newer one")
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert answered.text.index("the newer one") < answered.text.index("the older one")

    async def test_a_session_nobody_started_is_a_page_with_a_way_back(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/sessions/nothing-here")
        assert answered.status == 404
        assert 'href="/"' in answered.text

    async def test_a_message_to_a_session_nobody_started_is_refused(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.post("/sessions/nothing-here/messages", {"prompt": "hello"})
        assert answered.status == 404

    async def test_a_path_nothing_serves_is_a_page_rather_than_a_bare_status(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/nowhere")
        assert answered.status == 404
        assert "/nowhere" in answered.text

    async def test_an_empty_message_is_refused_rather_than_recorded(self, app: ASGIApp, service: Service) -> None:
        """A client refusal, not a server fault: a request that is not a message did not break anything."""
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session}/messages", {"prompt": "   "})
        assert answered.status == 422

    async def test_creating_one_without_an_endpoint_is_refused_rather_than_recorded(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        What a form that is not a session looks like now that it carries no message.

        The pair is the whole of what creation takes, so a post naming neither is not half a request,
        it is not a request - which is the same refusal an empty message used to be one field along.
        """
        async with calling(app) as caller:
            answered = await caller.post("/sessions", {"model": DEFAULT_CHOICE.model})
        assert answered.status == 422
        assert await service.listed() == ()

    async def test_a_send_refuses_a_swap_of_anything_that_is_not_a_transcript(
        self, app: ASGIApp, service: Service
    ) -> None:
        """Every status but 204 and 304 swaps in htmx 4, so a refusal would otherwise replace the conversation."""
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert 'hx-status:4xx="swap:none"' in answered.text
        assert 'hx-status:5xx="swap:none"' in answered.text

    async def test_the_page_serves_its_own_stylesheet_and_scripts(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            for asset in ("/assets/mainplate.css", "/assets/mainplate.js", "/assets/htmax.min.js"):
                assert (await caller.get(asset)).status == 200

    async def test_the_start_page_offers_every_profile_and_the_defaults_models(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")
        for name in CONFIG.endpoints:
            assert f'value="{name}"' in answered.text
        assert 'value="ripe/careful"' in answered.text, "the default endpoint's second model"
        assert 'value="ripe/fast" checked' in answered.text, "the first it listed, preselected"

    async def test_a_session_is_named_by_the_box_when_one_is_given(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service, "the first thing said", title="Reading the checkpointer")
        found = await service.read(session)
        assert found is not None
        assert found.session.title == "Reading the checkpointer"

    async def test_a_session_with_no_name_given_is_named_after_its_first_message(
        self, app: ASGIApp, service: Service
    ) -> None:
        """The behaviour every session had before the field existed, and still the default."""
        session = await a_session(app, service, "the first thing said")
        found = await service.read(session)
        assert found is not None
        assert found.session.title == "the first thing said"

    @pytest.mark.parametrize("given", ["", "   "], ids=["empty", "whitespace"])
    async def test_an_empty_name_is_the_same_as_not_naming_it(self, app: ASGIApp, service: Service, given: str) -> None:
        """
        A box somebody tabbed through must not name a session after nothing.

        `parse_qs` drops empty values, so an untouched field and an absent one already arrive
        alike; whitespace is the case that would otherwise get through and title a session `"   "`.
        """
        session = await a_session(app, service, "the first thing said", title=given)
        found = await service.read(session)
        assert found is not None
        assert found.session.title == "the first thing said"

    async def test_a_given_name_is_cut_by_the_same_rule_a_message_is(self, app: ASGIApp, service: Service) -> None:
        """
        One rule about what a session name is, rather than one per way of arriving at one.

        A sidebar seventeen rems wide can hold only so much, and a name typed into a box can be
        arbitrarily long where one taken from a message was already cut.
        """
        session = await a_session(app, service, "hello", title="w " * 200)
        found = await service.read(session)
        assert found is not None
        assert len(found.session.title) <= TITLE_LENGTH

    async def test_the_start_page_offers_a_box_to_name_a_session(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert f'name="{TITLE_FIELD}"' in answered.text

    async def test_a_models_own_name_is_what_the_picker_shows(self, app: ASGIApp) -> None:
        """An endpoint that writes a name for a person is why the id is a value and not the text."""
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert ">Careful<" in answered.text

    async def test_models_are_grouped_by_the_family_they_come_from(self, app: ASGIApp) -> None:
        """
        What makes a gateway's seventy models a list somebody can read.

        Both families are asserted, because a rendering that emitted one group holding everything
        would satisfy a check for either alone while grouping nothing.
        """
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert '<h2 class="models__heading">ripe</h2>' in answered.text
        assert '<h2 class="models__heading">wide</h2>' in answered.text

    async def test_changing_the_profile_asks_for_that_profiles_models(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert 'hx-get="/fragments/models"' in answered.text
        assert 'hx-target="#model"' in answered.text

    async def test_the_models_fragment_answers_with_one_profiles_models(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/models?endpoint=gateway")
        assert answered.status == 200
        assert "<html" not in answered.text
        assert 'value="wide/steady"' in answered.text
        assert "careful" not in answered.text, "'careful' belongs to the other endpoint"

    async def test_the_models_fragment_for_a_profile_nothing_offers_is_refused(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/models?endpoint=gone")
        assert answered.status == 404

    async def test_a_session_records_the_pair_it_was_started_on(self, app: ASGIApp, service: Service) -> None:
        chosen = Choice(endpoint="gateway", model="wide/steady")
        async with calling(app) as caller:
            answered = await caller.post("/sessions", starting_form(chosen))
        session = answered.location.rsplit("/", 1)[-1]
        assert (await service.read(session)).chosen == chosen  # type: ignore[union-attr]

    async def test_a_pair_no_profile_offers_is_refused_rather_than_recorded(
        self, app: ASGIApp, service: Service
    ) -> None:
        """A select is a suggestion the page made, not a constraint on what somebody can post."""
        async with calling(app) as caller:
            answered = await caller.post("/sessions", starting_form(Choice(endpoint="here", model="nope")))
        assert answered.status == 422
        assert await service.listed() == ()

    async def test_a_session_page_says_what_it_is_answered_on(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert f"{DEFAULT_CHOICE.endpoint}" in answered.text
        assert f"{DEFAULT_CHOICE.model}" in answered.text
        assert 'name="endpoint"' not in answered.text, "a session's choice is fixed, so offering one would lie"

    async def test_a_session_whose_profile_is_gone_says_so_and_draws_no_spinner(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The one state a person cannot otherwise diagnose: a spinner that will never resolve.

        The worker cannot answer the session, so a page drawing a pending panel would show it
        forever with nothing saying why. Naming the endpoint is the whole of the fix, because
        putting it back is what makes the conversation continue where it stopped.

        The connection stays open, and that is not the same question. It is the page's rather than
        the turn's, so it is held whether or not anything is expected down it; what a stalled
        session must not do is claim something is coming.
        """
        session = await a_session(app, service)
        narrowed = replace(service, catalogues=Catalogues(current=OTHER_CATALOGUE))
        async with calling(build_app(already(narrowed))) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert DEFAULT_CHOICE.endpoint in answered.text
        assert "no longer declares" in answered.text
        assert 'id="waiting"' not in answered.text, "nothing is coming, so nothing may say it is"

    async def test_a_session_the_provider_refused_says_so_and_points_at_the_fork(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The second way to be stuck, drawn the same way and pointing somewhere different.

        A missing endpoint is a configuration file somebody can put back. A refused request cannot be
        put back at all, because what the provider turned down is the recorded history itself, so the
        sentence names the one thing that does work: forking at the turn drops that turn's own
        requests and keeps everything under them.
        """
        session = await a_session(app, service)
        await service.checkpointer.supply(
            session, refused_key(0, 0), records.Refused(why="prompt is too long", status=400).recorded()
        )

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")

        assert "prompt is too long" in answered.text, "the provider's own words rather than a code standing in"
        assert "(400)" in answered.text
        assert "Fork at this turn" in answered.text
        assert 'id="waiting"' not in answered.text, "nothing is coming, so nothing may say it is"

    async def test_a_session_on_a_model_the_picker_stopped_listing_is_not_stuck(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        An endpoint routes more ids than it advertises, so a missing model is not a stuck session.

        The case is real rather than hypothetical: exe.dev's gateway answers `claude-sonnet-4-6`
        while listing it as `anthropic/claude-sonnet-4-6`, so every session recorded before that
        prefix appeared names a model discovery will never return. Calling those stuck would tell
        somebody to restore a model nobody removed, and would take the spinner off a conversation
        the worker is still going to answer.
        """
        session = await a_session(app, service)
        thinned = Catalogue(
            offered={
                DEFAULT_CHOICE.endpoint: Offering(
                    endpoint=DEFAULT_CHOICE.endpoint,
                    format="anthropic",
                    url=None,
                    models=(Listed(id="ripe/other", label="Other", provider="ripe"),),
                )
            },
            default=Choice(endpoint=DEFAULT_CHOICE.endpoint, model="ripe/other"),
        )
        narrowed = replace(service, catalogues=Catalogues(current=thinned))
        async with calling(build_app(already(narrowed))) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert "no longer" not in answered.text
        assert 'id="waiting"' in answered.text, "the worker can still answer it, so an answer is coming"

    async def test_a_message_is_rendered_as_the_markdown_it_was_written_as(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app, service, "a **strong** point")
        region = await watched(app, session)
        assert "<strong>strong</strong>" in region

    async def test_markup_in_a_message_does_not_become_markup(self, app: ASGIApp, service: Service) -> None:
        """
        Asserted on the fragment rather than the page, which is not a detail.

        A session is named after its first message, so the page also carries that text in the
        sidebar, where it is escaped as an ordinary child. A page-level assertion would therefore
        pass on the sidebar's copy whatever the transcript did with it, which is the check that
        cannot fail measuring the wrong thing.

        Asserted on what is *drawn*, with the two places the source is deliberately carried taken
        back out. A block carries the Markdown it was written as for its copy button, and a panel's
        row carries the front of it as the line a shut panel stands for, so both hold that message's
        angle brackets by construction; what neither may do is let them become anything, which is
        what the two tests below ask of each.
        """
        session = await a_session(app, service, "<script>alert(1)</script> and <img src=x onerror=alert(2)>")
        drawn = sub(r'( data-markdown="[^"]*"|<span class="opening">[^<]*</span>)', "", await watched(app, session))
        assert "<script" not in drawn
        assert "alert(1)" not in drawn
        assert "onerror" not in drawn

    async def test_markup_in_a_message_is_still_text_in_the_line_its_panel_stands_for(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        A panel's opening line is the source rather than the rendering, so the sanitiser never sees
        it and the escaping of a text child is the whole of what keeps it inert. The message is
        written to close that element and open a script beside it.

        Read back with a real HTML parser, because what has to hold is that the document parses to
        one element whose text is exactly the front of the message - the same statement whether the
        renderer spells the bracket `&lt;` or `&#60;`.

        The empty ones are dropped rather than counted: a panel with nothing to stand for yet carries
        the working dots in this element instead of a line, and how many of those a page happens to
        have is not what this asks.
        """
        said = "</span><script>alert(1)</script>"
        session = await a_session(app, service, said)
        assert [line for line in opening_lines(await watched(app, session)) if line] == [said]

    async def test_the_source_a_copy_button_hands_over_cannot_break_out_of_its_attribute(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The message a block carries for its copy button is the raw thing somebody typed, so what
        stops it being markup is the escaping of the attribute holding it and nothing else. The
        message here is written to close that attribute and open an event handler on the element.

        Read back with a real HTML parser rather than by looking for the escaped form, because what
        has to hold is that the document parses to one element whose attribute is exactly the message
        - which is the same statement whether the renderer spells a quote `&#34;` or `&quot;`.
        """
        said = '" onmouseover="alert(1)'
        session = await a_session(app, service, said)
        carrying = blocks_carrying_markdown(await watched(app, session))
        assert [held["data-markdown"] for held in carrying] == [said]
        # And the element carries nothing else, which is what says the value did not close its own
        # attribute and open a handler beside it.
        assert [sorted(held) for held in carrying] == [["class", "data-markdown"]]


ANSWERED = [
    {
        "kind": "response",
        "parts": [
            {"part_kind": "thinking", "content": "the trigger fires once"},
            {"part_kind": "text", "content": "it is a plate"},
        ],
        # As the store holds one: the counts nest, so the fresh input here is 1,200, and the cost is
        # a string because that is what a `Decimal` dumped through `mode="json"` becomes.
        "usage": {"input_tokens": 5_300, "cache_read_tokens": 4_100, "output_tokens": 640, "cost": "0.0123"},
    }
]

# One call, as a response holds it, for the pages that need a turn with a tool in it.
CALLED = {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "x"}, "tool_call_id": "c1"}


def a_reference(context: int) -> Reference:
    """
    A database that knows one thing about the model these sessions run on: how big its window is.

    Under the routed id, which is the key `Reference.look_up` tries first and the only one a listing
    with no upstream name has at all. The console's own default is no database, so a test that wants
    a window says so; every other test here is the case where nothing does.
    """
    return Reference(qualified={DEFAULT_CHOICE.model: Facts(context=context)}, upstream={})


class TestForgettingFromTheComposer:
    """
    The answer that keeps the message here and drops what the model was told.

    Two things to pin and both of them matter: that the record says so, and that nothing above the
    boundary left the checkpoint. A console that deleted the backlog would satisfy the first on its
    own, which is exactly the failure the word `forget` exists to rule out.
    """

    async def sent(self, app: ASGIApp, session: str, said: str) -> None:
        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session}/messages", {"prompt": said, "disposition": "forget"})
        assert answered.status == 200

    async def test_the_message_opens_a_turn_recorded_as_forgetting(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        await self.sent(app, session, "start again")

        held = await service.checkpointer.load(session)
        assert delivered_in(held)[-1] == recorded_prompt("start again", forget=True)

    async def test_everything_above_the_boundary_is_still_recorded(self, app: ASGIApp, service: Service) -> None:
        """Nothing is deleted, which is the whole difference between this and a `clear`."""
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        await self.sent(app, session, "start again")

        held = await service.checkpointer.load(session)
        assert delivered_in(held)[0] == recorded_steer("what is a mainplate")
        assert messages_key(0) in held

    async def test_it_opens_a_turn_of_its_own_rather_than_steering_the_one_in_flight(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        A boundary between turns is the only place one can go, so this never reaches `send`: what
        `Send` delivers is a message a running turn may fold in, and this must not be one.
        """
        session = await a_session(app, service)

        await self.sent(app, session, "start again")

        assert delivered_in(await service.checkpointer.load(session))[-1] == recorded_prompt(
            "start again", forget=True
        ), "a forget is a message that opens its own turn, never one a turn may take"

    async def test_the_transcript_says_the_model_was_told_nothing_above_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        await self.sent(app, session, "start again")

        region = await watched(app, session)
        assert "rule--forget" in region
        assert "context cleared" in region
        # And the turn it closed is still on the page, which is the half a rendering can get wrong.
        assert "what is a mainplate" in region

    async def test_a_conversation_nobody_forgot_draws_no_boundary(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        region = await watched(app, session)
        assert "rule--forget" not in region
        assert 'data-stop="forget"' not in region


class TestBranchingFromTheComposer:
    """
    Sending a message into a *new* session carrying this one whole, which is pi's `/clone`.

    The mechanism already existed - `Service.fork` at the end, which the fork route has always
    accepted - so what these pin is the disposition reaching it and the answer getting the reader to
    the branch rather than leaving them at the parent showing somebody else's conversation.
    """

    async def branched(self, app: ASGIApp, service: Service, session: str) -> str:
        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session}/messages", {"prompt": "try that again", "disposition": "fork"}
            )
        assert answered.status == 200
        return dict(answered.headers)["hx-redirect"].rsplit("/", 1)[-1]

    async def test_branching_makes_a_new_session_and_leaves_this_one_alone(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app, service)
        branch = await self.branched(app, service, session)

        assert branch != session
        held = await service.checkpointer.load(session)
        assert len(delivered_in(held)) == 1, "the parent was not sent anything"

    async def test_the_branch_carries_the_whole_conversation_and_asks_the_new_message(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        Forking the *end*, so nothing is left behind and nothing is re-asked: every turn of the
        parent comes across settled and the message goes into the turn after them.
        """
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        branch = await self.branched(app, service, session)

        held = await service.checkpointer.load(branch)
        assert delivered_in(held) == [
            recorded_steer("what is a mainplate"),
            recorded_prompt("try that again"),
        ], "the parent's message came across and the new one is behind it"
        assert messages_key(0) in held, "and the parent's turn came across answered"

    async def test_the_answer_navigates_rather_than_swapping_the_parent(self, app: ASGIApp, service: Service) -> None:
        """
        `HX-Redirect` and not a `303`, and the difference is where the reader ends up.

        htmx follows a redirect itself and swaps what comes back into the target, so a `303` would
        put the branch's transcript inside the parent's page and leave the address bar naming the
        parent. Pinned as the header rather than as a status, because a `200` with the wrong header
        would swap an empty body over the conversation.
        """
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session}/messages", {"prompt": "elsewhere", "disposition": "fork"}
            )
        headers = dict(answered.headers)
        assert "hx-redirect" in headers
        assert headers["hx-redirect"].startswith("/sessions/")
        assert answered.text == "", "nothing to swap over the conversation being left"

    async def test_the_branch_is_recorded_as_a_fork_of_its_parent(self, app: ASGIApp, service: Service) -> None:
        """So the sidebar draws it under what it came from, exactly as a fork from a turn is."""
        session = await a_session(app, service)
        branch = await self.branched(app, service, session)

        listed = {each.id: each for each in await service.listed()}
        origin = listed[branch].forked
        assert origin is not None
        assert origin.session == session

    async def test_the_branch_is_answered_on_the_parent_s_own_choice(self, app: ASGIApp, service: Service) -> None:
        """
        The composer offers no picker, so there is nothing posted to take a choice from.

        Inherited rather than defaulted, because a branch of a session on one model that quietly
        started on the configured default would be answering a different question.
        """
        session = await a_session(app, service)
        branch = await self.branched(app, service, session)

        assert choice_of(await service.checkpointer.load(branch)) == choice_of(await service.checkpointer.load(session))

    async def test_a_conversation_with_nothing_in_it_offers_no_branch(self, app: ASGIApp) -> None:
        """Branching an empty session makes a session identical to starting one, so it is not offered."""
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert "sender__option" not in answered.text

    async def test_a_conversation_with_a_turn_in_it_does(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert "sender__option" in answered.text
        assert 'value="fork"' in answered.text


class TestSteppingOutAndComingBack:
    """
    An aside, and the way back from one.

    Nothing mechanical separates an aside from a fork - both are `Service.fork` at the end - so what
    these pin is the half that is not mechanical: that what somebody meant is recorded, and that a
    fork of any kind can send a message back to what it came out of.
    """

    async def sent(self, app: ASGIApp, session: str, said: str, where: str) -> dict[str, str]:
        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session}/messages", {"prompt": said, "disposition": where})
        assert answered.status == 200
        return dict(answered.headers)

    async def test_an_aside_is_recorded_as_one(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        headers = await self.sent(app, session, "just checking something", "aside")
        stepped = headers["hx-redirect"].rsplit("/", 1)[-1]

        listed = {each.id: each for each in await service.listed()}
        origin = listed[stepped].forked
        assert origin is not None
        assert origin.session == session
        assert origin.aside

    async def test_a_plain_fork_is_not_an_aside(self, app: ASGIApp, service: Service) -> None:
        """The flag is what somebody meant, so it has to be off unless they said so."""
        session = await a_session(app, service)
        headers = await self.sent(app, session, "going another way", "fork")
        forked = headers["hx-redirect"].rsplit("/", 1)[-1]

        listed = {each.id: each for each in await service.listed()}
        origin = listed[forked].forked
        assert origin is not None
        assert not origin.aside

    async def test_an_aside_carries_the_conversation_exactly_as_a_fork_does(
        self, app: ASGIApp, service: Service
    ) -> None:
        """Nothing about the copy differs, which is the claim that keeps this one call and not two."""
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        headers = await self.sent(app, session, "just checking something", "aside")
        stepped = headers["hx-redirect"].rsplit("/", 1)[-1]

        assert delivered_in(await service.checkpointer.load(stepped)) == [
            recorded_steer("what is a mainplate"),
            recorded_prompt("just checking something"),
        ]

    async def test_going_back_puts_a_message_in_the_session_this_one_came_from(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        A message and not a merge. Splicing the aside's turns in would leave the parent holding
        requests whose context never existed, since they were asked against the history at the
        branch point.
        """
        session = await a_session(app, service)
        headers = await self.sent(app, session, "just checking something", "aside")
        stepped = headers["hx-redirect"].rsplit("/", 1)[-1]

        back = await self.sent(app, stepped, "here is what I found", "parent")

        held = await service.checkpointer.load(session)
        assert delivered_in(held)[-1] == recorded_prompt("here is what I found")
        assert back["hx-redirect"].endswith(session), "and the reader is taken back there"

    async def test_the_aside_itself_is_not_sent_the_message_it_sent_back(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        headers = await self.sent(app, session, "just checking", "aside")
        stepped = headers["hx-redirect"].rsplit("/", 1)[-1]
        await self.sent(app, stepped, "here is what I found", "parent")

        # The second message is the aside's *own* opening one, carried in when it was made. What must
        # not be here is a third: going back sends to the parent instead of to both.
        assert delivered_in(await service.checkpointer.load(stepped))[1:] == [recorded_prompt("just checking")]

    async def test_a_session_that_came_from_nowhere_cannot_send_back(self, app: ASGIApp, service: Service) -> None:
        """
        Refused rather than dropped, and read off the row rather than posted: a form naming a
        destination is how a message reaches a conversation nobody was looking at.
        """
        session = await a_session(app, service)
        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session}/messages", {"prompt": "back to what", "disposition": "parent"}
            )
        assert answered.status == 422

    async def test_the_way_back_is_offered_only_where_there_is_one(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        async with calling(app) as caller:
            plain = await caller.get(f"/sessions/{session}")
        assert 'value="parent"' not in plain.text

        headers = await self.sent(app, session, "just checking", "aside")
        stepped = headers["hx-redirect"].rsplit("/", 1)[-1]
        # A branch answers its own settings step before it draws a composer, because it carries its
        # parent's turns and none of its plugins. Written by hand for the reason `a_session` writes
        # one: this app runs no worker, so nothing else ever records a registration.
        await registered(service, stepped)
        async with calling(app) as caller:
            branched = await caller.get(f"/sessions/{stepped}")
        assert 'value="parent"' in branched.text


class TestSayingWhetherTheCacheIsStillWarm:
    """
    The line above the box, which says whether the next request pays full price for the whole context.

    Read from the last response's own timestamp against the service's clock, so what these fix is the
    one-sided rule: `cold` is asserted where it is certain, and `warm` never is.
    """

    async def test_a_conversation_answered_just_now_says_when_its_prefix_was_written(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        An absolute time and not a relative one, because nothing here re-renders on the clock: what
        the server can say without rotting is when the prefix was last written, and the script is what
        turns that into `warm as of 12m`.
        """
        service.references.current = a_reference(context=200_000)
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        region = await watched(app, session)

        assert f'id="{CACHE_ID}"' in region
        assert f"cached at {WHEN:%H:%M}" in region
        assert "cold" not in region, "warm is never asserted, so neither is its opposite while it holds"

    async def test_a_conversation_older_than_the_retention_is_cold(self, app: ASGIApp, service: Service) -> None:
        """
        The one state the server can assert, because it was already true when this was rendered and
        nothing makes a cold prefix warm again.

        The *response* is aged rather than the clock moved, which is both the real case and the only
        one available: a `Service` is frozen, and what decides this is when the prefix was written.
        """
        service.references.current = a_reference(context=200_000)
        session = await a_session(app, service)
        stale = WHEN - RETENTION - timedelta(seconds=1)
        await answered(service, session, {**ANSWERED[0], "timestamp": stale.isoformat()})

        region = await watched(app, session)

        assert "cold" in region
        assert "cached at" not in region

    async def test_it_prices_re_sending_at_both_ends_of_what_a_cache_might_hold(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The pair is the point: what it costs now against what it costs once the prefix is gone, which
        is what makes the cost of waiting legible. `ANSWERED` leaves 5,300 tokens of context at $3 per
        million and a tenth of that cached, so re-sending it is $0.0016 or $0.0159.

        The `+` matters as much as either number: what these price is the input of the next turn's
        first request, and the answer, the tools and any further requests are all on top, so a bare
        figure would read as what the next turn costs.
        """
        service.references.current = Reference(
            qualified={DEFAULT_CHOICE.model: Facts(context=200_000, cost=Cost(input=3, output=15, cache_read=0.3))},
            upstream={},
        )
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        region = await watched(app, session)

        assert "\N{UPWARDS ARROW}5K at \N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}$0.0016 / $0.0159+" in region

    async def test_a_model_whose_record_prices_no_cache_shows_one_figure_rather_than_two_alike(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        `priced` falls back to the input rate where a record publishes no cache one, so a warm figure
        there would be the cold figure printed twice - which reads as a bug rather than as a database
        that does not say. What is drawn is the one end that is known.
        """
        service.references.current = Reference(
            qualified={DEFAULT_CHOICE.model: Facts(context=200_000, cost=Cost(input=3, output=15))},
            upstream={},
        )
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        region = await watched(app, session)

        assert "\N{UPWARDS ARROW}5K at $0.0159+" in region
        assert "\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}$" not in region

    async def test_a_model_nobody_wrote_a_price_for_still_says_when_the_prefix_was_written(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The money goes and the state stays, exactly as the gauge's fraction goes while its counts stay:
        when the prefix was last written is worth saying whether or not anything can price it.
        """
        service.references.current = a_reference(context=200_000)
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        region = await watched(app, session)

        assert f"cached at {WHEN:%H:%M}" in region
        assert "at $" not in region

    async def test_a_conversation_nothing_has_answered_says_nothing_and_still_leaves_the_anchor(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        There is no prefix to have been cached before the first answer. The region stays all the same,
        because it is what the page's own connection swaps into once there is.
        """
        session = await a_session(app, service)

        region = await watched(app, session)

        assert f'<p class="cache" id="{CACHE_ID}"></p>' in region

    async def test_the_stream_carries_it_beside_the_transcript(self, app: ASGIApp, service: Service) -> None:
        """
        Two regions on one connection, which is what the partial exists for. It has to be sent rather
        than rendered once: it lives in the composer, so nothing else replaces it, and what it prices
        is the context, which grows with every turn.
        """
        service.references.current = a_reference(context=200_000)
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)

        region = await watched(app, session)

        assert f'hx-target="#{TRANSCRIPT_ID}"' in region
        assert f'hx-target="#{CACHE_ID}"' in region


class TestWhatARuleSays:
    """
    The line that opens a turn, which is where everything true of the turn rather than of a panel is.

    Rendered from the same checkpoint the panels are, so what this pins is that a turn's boundary
    reaches the page carrying its fork link, its tree and what it spent.
    """

    async def test_a_turn_opens_with_a_rule_carrying_what_it_spent(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert 'class="rule rule--turn"' in region
        assert "\N{UPWARDS ARROW}5K" in region
        assert "\N{DOWNWARDS ARROW}640" in region
        assert "(\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}4K)" in region
        assert "\N{GREEK CAPITAL LETTER DELTA}$0.0123" in region

    async def test_a_rule_says_how_full_the_window_is_and_draws_the_same_fact_as_a_gauge(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The percentage and the `--filled` the line is drawn to are one figure said twice.

        Both come from the reference's window and the last request's own input, so this is also what
        pins that a session picks its window up from the database rather than from anything recorded:
        nothing about the checkpoint changes between this test and the one below it.
        """
        service.references.current = a_reference(context=10_000)
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert "53%" in region, "5,300 of 10,000 tokens"
        assert "--filled: 53.0%" in region, "and the line drawn to the same fraction"

    async def test_a_session_whose_model_nobody_wrote_a_window_down_for_draws_no_gauge(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The counts still say what they say; only the fraction goes, because nothing could compute it.

        Three ways to know nothing arrive as one answer here - no database, an endpoint that no
        longer lists the recorded id, a model with no record - and this is the first of them, which
        is the console's own default.
        """
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert "\N{UPWARDS ARROW}5K" in region, "how much context there is is known either way"
        assert "rule__full" not in region
        assert "--filled" not in region

    async def test_a_rule_says_what_the_conversation_has_cost_by_the_time_it_reaches_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        What one turn cost is only readable against what has been spent so far, so a rule says both.

        The first turn is the case the running total is left off, since there it *is* the turn's own
        figure and printing one number twice says nothing. The second turn is where it starts.
        """
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        assert "\N{N-ARY SUMMATION}" not in await watched(app, session), "one turn in, the total is the turn"
        await service.say(session, "and again")
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert "\N{GREEK CAPITAL LETTER DELTA}$0.0123" in region, "what this turn added"
        assert "\N{N-ARY SUMMATION}$0.0246" in region, "and two turns at $0.0123 each"

    async def test_one_unpriced_turn_takes_the_running_total_off_every_rule_below_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        `altogether`'s rule, one rule at a time: a total quietly missing a turn understates it.

        The turn after an unpriced one is priced perfectly well and still shows no total, which is
        the point rather than a shortcoming: what the total would say is the sum of everything the
        reference happened to know about, and nothing on the page could say it was doing that.
        """
        session = await a_session(app, service)
        await answered(
            service, session, {"kind": "response", "parts": [], "usage": {"input_tokens": 300, "output_tokens": 12}}
        )
        await service.say(session, "and again")
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert "\N{GREEK CAPITAL LETTER DELTA}$0.0123" in region, "the priced turn still says what it cost"
        assert "\N{N-ARY SUMMATION}" not in region, "and the conversation says nothing about its total"

    async def test_the_fork_link_is_on_the_rule_rather_than_inside_the_turn(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The branch point is *before* a turn's message, which is exactly where the rule is.

        On a panel the link was revealed by hover, so it did not exist on a touch screen at all, and
        forking is the only way a session changes its mind.
        """
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert 'class="rule__fork"' in region
        assert "panel__fork" not in region

    async def test_a_turn_nobody_has_priced_shows_its_counts_and_no_money(self, app: ASGIApp, service: Service) -> None:
        """
        The two fail independently: the counts are on every response, the price needs a record.

        A turn drawn as `free` here would be a claim nobody made, which is the one way to be wrong
        about money that a reader cannot catch.
        """
        session = await a_session(app, service)
        await answered(
            service, session, {"kind": "response", "parts": [], "usage": {"input_tokens": 300, "output_tokens": 12}}
        )
        region = await watched(app, session)
        assert "\N{UPWARDS ARROW}300" in region
        assert "\N{DOWNWARDS ARROW}12" in region
        assert "rule__cost" not in region
        assert "free" not in region

    async def test_a_rule_says_how_long_the_turn_spent_waiting_on_the_model(
        self, app: ASGIApp, service: Service
    ) -> None:
        """Read off the response's own `metadata`, which is where a pass stamps it before recording."""
        session = await a_session(app, service)
        await answered(service, session, {**ANSWERED[0], "metadata": {"took": 4.25}})
        region = await watched(app, session)
        assert "4.2s" in region

    async def test_a_turn_nothing_timed_says_nothing_about_time(self, app: ASGIApp, service: Service) -> None:
        """Every response recorded before this console timed anything, which must draw no figure."""
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert "rule__took" not in region

    async def test_a_call_says_how_long_it_ran(self, app: ASGIApp, service: Service) -> None:
        """
        From the record of the call itself, because neither of the two readings of a turn holds a
        duration: a `ToolReturnPart` has nowhere for one, and what a turn's messages say about a call
        is the tool's own value.
        """
        session = await a_session(app, service)
        await answered(
            service,
            session,
            {"kind": "response", "parts": [CALLED], "usage": {"input_tokens": 1, "output_tokens": 1}},
            {
                "kind": "request",
                "parts": [{"part_kind": "tool-return", "tool_name": "read", "content": "x", "tool_call_id": "c1"}],
            },
        )
        await service.checkpointer.supply(session, tool_key(0, "c1"), came_back("x", took=0.184))
        region = await watched(app, session)
        assert "184ms" in region

    async def test_a_turn_out_on_a_call_says_so_on_the_call_and_nowhere_else(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The call's own panel is drawn working, so a second panel of dots under it says it twice.

        And says it in a shape nothing is writing: an empty reply below a call the model is waiting
        on reads as a turn that has started answering, where what is happening is a tool running.
        """
        session = await a_session(app, service)
        turn = await taken(service, session)
        await service.checkpointer.supply(
            session,
            model_key(turn, 0),
            answered_with({"kind": "response", "parts": [CALLED], "usage": {"input_tokens": 1, "output_tokens": 1}}),
        )
        region = await watched(app, session)
        assert 'id="waiting"' not in region, "nothing claims a reply is being written"
        # Which the dots still on the page must therefore belong to: the call's own panel.
        assert 'aria-label="working"' in region, "and the call is drawn as still out"

    async def test_a_turn_whose_calls_have_all_come_back_is_waiting_on_the_model_again(
        self, app: ASGIApp, service: Service
    ) -> None:
        """The control: with the result in, what is being waited on is the next request."""
        session = await a_session(app, service)
        turn = await taken(service, session)
        await service.checkpointer.supply(
            session,
            model_key(turn, 0),
            answered_with({"kind": "response", "parts": [CALLED], "usage": {"input_tokens": 1, "output_tokens": 1}}),
        )
        await service.checkpointer.supply(session, tool_key(turn, "c1"), came_back("x"))
        region = await watched(app, session)
        assert 'id="waiting"' in region


class TestShowingWhatWasRecorded:
    """
    The rule at each model request's boundary, and the fragment behind it.

    A request is a thing the checkpoint has a key for, unlike a panel, so what these pin is a lookup
    rather than an agreement between two walks.
    """

    async def answered_session(self, app: ASGIApp, service: Service) -> str:
        session = await a_session(app, service)
        await answered(service, session, *ANSWERED)
        await service.checkpointer.supply(session, model_key(0, 0), answered_with(ANSWERED[0]))
        return session

    async def test_a_rule_is_pointed_at_the_request_it_stands_at(self, app: ASGIApp, service: Service) -> None:
        session = await self.answered_session(app, service)
        region = await watched(app, session)
        assert f'hx-get="/fragments/sessions/{session}/requests/0/0"' in region

    async def test_every_request_of_a_turn_gets_its_own_rule_and_the_turn_gets_one(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        Two round trips, so two rules: the turn's own, and one where the second request began.

        The `rule--turn` count is what keeps the dock's turn arrows stepping turns rather than
        requests, and it is why the modifier exists rather than the selector being every rule.
        """
        session = await a_session(app, service)
        await service.checkpointer.supply(session, tree_key(0, 1), snapshotted("b" * 40))
        await answered(service, session, *ANSWERED, *ANSWERED)
        region = await watched(app, session)
        assert region.count('class="rule rule--turn"') == 1
        assert region.count('class="rule"') == 1, "the second request, which opens no turn"
        assert '/requests/0/1"' in region
        assert "bbbbbbbb" in region, "the tree the second request was made against"

    async def test_a_rule_carries_what_the_request_cost_and_the_tree_it_saw(
        self, app: ASGIApp, service: Service
    ) -> None:
        """The three things that are true of a request, which were previously homeless or on a panel."""
        session = await a_session(app, service)
        await service.checkpointer.supply(session, tree_key(0, 0), snapshotted("a" * 40))
        await answered(service, session, *ANSWERED)
        region = await watched(app, session)
        assert "aaaaaaaa" in region, "the tree taken before the ask"
        assert "\N{UPWARDS ARROW}5K" in region, "and what the answer cost"
        assert 'class="tag__at">r0.0<' in region, "and the record behind it, named turn and request"

    async def test_the_record_is_not_carried_by_the_transcript_itself(self, app: ASGIApp, service: Service) -> None:
        """
        Fetched rather than rendered, which is the whole reason it is an endpoint: the transcript
        is re-rendered whenever the turn in flight records anything, and this is several times the
        size of the reading of it.
        """
        session = await self.answered_session(app, service)
        region = await watched(app, session)
        assert "the trigger fires once" in region, "the reading of the response is on the page"
        assert '"part_kind"' not in region, "the record behind it is not"

    async def test_the_disclosure_survives_the_poll_that_replaces_the_conversation(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        `hx-preserve` is load-bearing and invisible to a reader of the markup, so it is pinned here.

        The region morphs, and the server renders this closed. Without the attribute a morph takes
        the `open` attribute back off and shuts the disclosure under the reader's hand once a
        second, which a driven Chromium confirms and no string assertion can. htmx reads it off the
        incoming markup, so this response is where it has to be.
        """
        session = await self.answered_session(app, service)
        region = await watched(app, session)
        assert "hx-preserve" in region
        assert 'hx-trigger="toggle once"' in region, "settled for good, so asked for once"

    async def test_a_request_answers_with_the_whole_response_the_step_holds(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The step and not a slice of the turn's messages, which is the simplification the tag bought:
        `turn:{n}:model:{i}` is what the provider answered, and a request has a key of its own.
        """
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/requests/0/0")
        assert answered.status == 200
        assert '"part_kind": "thinking"' in answered.text
        assert '"the trigger fires once"' in answered.text
        assert "it is a plate" in answered.text, "the whole response, not one panel's worth of it"

    async def test_a_request_nobody_made_is_refused_rather_than_rendered_empty(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/requests/0/9")
        assert answered.status == 404
        assert "Nothing is recorded" in answered.text

    async def test_a_session_nobody_started_is_refused(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/sessions/deadbeef/requests/0/0")
        assert answered.status == 404

    async def test_markup_inside_a_record_does_not_become_markup(self, app: ASGIApp, service: Service) -> None:
        """
        The raw record carries whatever the model said, which is shaped by whatever reached the box.

        Shown as text and not as rendered Markdown, so the sanitiser this page uses elsewhere is not
        in the path at all: what stands in for it is that a node tree escapes a text child.
        """
        session = await a_session(app, service)
        await service.checkpointer.supply(
            session,
            model_key(0, 0),
            {"kind": "response", "parts": [{"part_kind": "text", "content": "<script>alert(1)</script>"}]},
        )
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/requests/0/0")
        assert answered.status == 200
        assert "<script" not in answered.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in answered.text


class TestWhatTheIsolationControlsPost:
    """
    The two new axes as the form carries them, and the one that a form cannot decide by itself.

    Both are closed sets, so this layer settles them; neither is reconciled with the repository here,
    because that is `Service.start`'s job and doing it twice is how the two come to disagree.
    """

    async def test_a_form_naming_the_whole_machine_starts_a_session_on_it(self, app: ASGIApp, service: Service) -> None:
        async with calling(app) as caller:
            said = await caller.post(
                "/sessions",
                {
                    "prompt": "look around",
                    "endpoint": DEFAULT_CHOICE.endpoint,
                    "model": DEFAULT_CHOICE.model,
                    "workspace": "everything",
                    "network": "on",
                },
            )

        assert said.status == 303
        chosen = choice_of(await service.checkpointer.load(said.location.rsplit("/", 1)[-1]))
        assert chosen is not None
        assert chosen.isolation.filesystem is Filesystem.EVERYTHING
        assert chosen.isolation.network

    async def test_a_form_naming_no_isolation_at_all_is_the_safe_answer(self, app: ASGIApp) -> None:
        """
        A form predating either control still names a whole choice, and names the tightest one.

        The absent field has to mean what a session had before there was anything to ask, or every
        such form would silently widen the sessions it starts.
        """
        asked = posted_isolation({})

        assert asked.filesystem is Filesystem.NOTHING
        assert not asked.network

    async def test_a_filesystem_this_console_does_not_know_is_refused(self) -> None:
        """A card is a suggestion the page made, so a value outside it came from something else."""
        with pytest.raises(NotAMessage, match="is not something a session can work in"):
            posted_workspace({"workspace": ["the-whole-internet"]})

    async def test_the_network_is_on_only_when_the_on_card_posted(self) -> None:
        """
        A radio that is not checked posts no field, so absent has to be the off answer.

        Asserted against the empty string too, which is what the off card itself posts: the two have
        to mean one thing or forgetting either would turn the switch on.
        """
        assert posted_isolation({"network": ["on"]}).network
        assert not posted_isolation({"network": [""]}).network
        assert not posted_isolation({}).network


class TestOneQuestionAboutFiles:
    """
    A repository and the filesystem level it implies come out of one posted value.

    That is the whole point of merging the two groups: they cannot arrive disagreeing, so nothing
    downstream reconciles them and no control has to be kept in step with another.
    """

    def test_a_repository_settles_both(self) -> None:
        assert posted_workspace({"workspace": ["exe-github:blog"]}) == ("exe-github:blog", Filesystem.WORKTREE)

    def test_the_two_that_are_not_a_repository_settle_both(self) -> None:
        assert posted_workspace({"workspace": ["nothing"]}) == (None, Filesystem.NOTHING)
        assert posted_workspace({"workspace": ["everything"]}) == (None, Filesystem.EVERYTHING)

    def test_an_id_is_told_from_a_level_by_its_colon(self) -> None:
        """
        What makes the encoding unambiguous rather than lucky.

        A repository id is `forge:key`, so it always holds a colon and can never be either level's
        name. A prefix would work too and would be one more thing to strip on both sides.
        """
        found, level = posted_workspace({"workspace": ["test:nothing"]})

        assert found == "test:nothing", "a repository whose key spells a level is still a repository"
        assert level is Filesystem.WORKTREE

    def test_an_absent_field_is_the_tightest_answer(self) -> None:
        assert posted_workspace({}) == (None, Filesystem.NOTHING)


# What a session's first pass would have read out of files, which is what the step draws a switch
# for. Two of the operator's own, so a test can leave one on and turn the other off and ask which
# was actually run.
DECLARES: tuple[Installed, ...] = (
    Installed(tier=Tier.USER, name="lint", path=Path("/plugins/lint")),
    Installed(tier=Tier.USER, name="notify", path=Path("/plugins/notify")),
)


@dataclass
class Answering:
    """
    A stand-in for running a plugin, which records who was asked and may turn one of them down.

    A function rather than a process, which is what `Speaking` is injected for: that a plugin really
    is a file spoken to over a pipe is `test_plugins.py`'s claim, and what these ask is the *order* -
    declared, then confirmed, then run, and never one of them without the one before it.
    """

    refusing: str | None = None
    asked: list[str] = field(default_factory=list)

    async def __call__(self, plugin: Installed, payload: Payload, worktree: Worktree | None) -> object:
        self.asked.append(plugin.qualified)
        if self.refusing == plugin.qualified:
            raise PluginFailed(f"{plugin.qualified} exited 1: saying nothing")
        return {"events": ["after_turn"]}


class TestLoadingASessionsPlugins:
    """
    The settings step, which is the one thing standing between a declaration and a program running.

    Every test here turns on the same claim: nothing spawns until somebody presses the button, and
    what the press runs is exactly what the switches left on.
    """

    async def console(self, service: Service, answering: Answering) -> tuple[ASGIApp, Service]:
        """The console over a service that can run a plugin, which the shared fixture's cannot."""
        running = replace(service, declaring=Declaring(console=DECLARES, speaking=answering))
        return build_app(already(running)), running

    async def declared(self, service: Service) -> str:
        """A session whose first pass has planted and read the files, and done nothing else."""
        session = await service.start(DEFAULT_CHOICE)
        await service.checkpointer.supply(session.id, DECLARED_KEY, recorded_declaration(DECLARES))
        await service.checkpointer.supply(session.id, REPOSITORY_DECLARED_KEY, recorded_declaration(()))
        return session.id

    async def setting_up(self, service: Service, session: str, answering: Answering) -> object:
        """
        The pass the press asks for, which is where a plugin is actually run.

        Driven by hand because these tests have no worker, and driven at all because the press is
        only half the claim: what somebody confirms and what then runs have to be the same set, and
        that is two moments now rather than one.
        """
        declaring = Declaring(console=DECLARES, speaking=answering)
        body = conversing(
            Provider().endpoints(),
            INSTRUCTIONS,
            declaring=declaring,
            tendings=lambda held: read_tending(service.database, held),
        )
        return await passing(service, session, body)

    async def test_the_step_offers_switches_and_nothing_to_type_into(self, service: Service) -> None:
        """
        A session being set up has no transcript, no message box and no rail: every control in those
        is pointed at a conversation that does not exist, and the rail's own cards are the surface of
        the plugins this screen exists to decide about.
        """
        answering = Answering()
        app, running = await self.console(service, answering)
        session = await self.declared(running)
        async with calling(app) as caller:
            page = await caller.get(f"/sessions/{session}")

        assert page.status == 200
        # The hidden field beside the box, which is what makes "off" representable at all: an
        # unchecked checkbox posts no field, so without it a plugin somebody turned off arrives
        # looking exactly like one this form never carried.
        assert 'type="hidden" name="on:user:lint" value="off"' in page.text
        assert 'type="checkbox" name="on:user:lint"' in page.text
        assert ">Load plugins</button>" in page.text
        assert 'class="composer"' not in page.text
        assert 'class="rail"' not in page.text
        assert answering.asked == [], "and nothing has been run to draw it"

    async def test_the_press_itself_runs_nothing_and_the_pass_runs_what_was_left_on(self, service: Service) -> None:
        """
        Which is the whole of the boundary, in the two moments it now takes: the press records the
        switches and asks for a pass, and the pass runs exactly the plugins they left on.

        **The press spawning nothing is the half that moved**, and it moved because setting a plugin
        up fetches things: a repository whose plugin installs a toolchain would otherwise hold this
        very request open for minutes.
        """
        answering = Answering()
        app, running = await self.console(service, answering)
        session = await self.declared(running)
        async with calling(app) as caller:
            pressed = await caller.post(
                f"/sessions/{session}/setup",
                {"on:user:lint": "on", "on:user:notify": "off", SETTLE_FIELD: SETTLED},
            )

        assert pressed.status == 303, "the conversation is where to go next"
        assert pressed.location.endswith(session)
        assert answering.asked == [], "and the press itself ran none of them"
        assert registered_in(await running.checkpointer.load(session)) is None

        await self.setting_up(running, session, answering)

        assert answering.asked == ["user:lint"]
        enrolled = registered_in(await running.checkpointer.load(session))
        assert enrolled is not None
        assert [each.qualified for each in enrolled] == ["user:lint"]

    async def test_a_step_with_every_switch_off_runs_none_of_them(self, service: Service) -> None:
        """
        The case a form cannot express on its own, and what the hidden field beside each box is for:
        an unchecked checkbox posts nothing, so without it this post is indistinguishable from a form
        that carried no switches at all.
        """
        answering = Answering()
        app, running = await self.console(service, answering)
        session = await self.declared(running)
        async with calling(app) as caller:
            pressed = await caller.post(
                f"/sessions/{session}/setup",
                {"on:user:lint": "off", "on:user:notify": "off", SETTLE_FIELD: SETTLED},
            )

        assert pressed.status == 303
        await self.setting_up(running, session, answering)
        assert answering.asked == []
        assert registered_in(await running.checkpointer.load(session)) == ()

    async def test_a_plugin_that_will_not_set_up_comes_back_to_the_step(self, service: Service) -> None:
        """
        Rather than stalling a conversation, because the thing to do about a plugin that will not set
        up is turn it off - and the switch is on the screen this answers with.

        **The reason is recorded now, against the attempt it belongs to**, which is what the move
        into a pass forced: nobody is waiting on a response any more, so a failure with nowhere to go
        would be a spinner that never resolves.
        """
        answering = Answering(refusing="user:notify")
        app, running = await self.console(service, answering)
        session = await self.declared(running)
        async with calling(app) as caller:
            pressed = await caller.post(
                f"/sessions/{session}/setup",
                {"on:user:lint": "on", "on:user:notify": "on", SETTLE_FIELD: SETTLED},
            )
            assert pressed.status == 303
            await self.setting_up(running, session, answering)
            page = await caller.get(f"/sessions/{session}")

        assert page.status == 200
        assert "user:notify exited 1" in page.text
        assert ">Load plugins</button>" in page.text, "with the switches still there to change"
        recorded = await running.checkpointer.load(session)
        assert registered_in(recorded) is None, "and nothing was registered, so pressing again is a fresh attempt"

    async def test_pressing_again_after_a_failure_is_a_fresh_attempt_with_no_sentence_on_it(
        self, service: Service
    ) -> None:
        """
        Which is what the attempt number buys, and the reason the confirmation records no list of its
        own: a write-once one would have the second press run exactly what the first one ran, and a
        write-once refusal would be the sentence every later press showed.
        """
        answering = Answering(refusing="user:notify")
        app, running = await self.console(service, answering)
        session = await self.declared(running)
        async with calling(app) as caller:
            await caller.post(
                f"/sessions/{session}/setup",
                {"on:user:lint": "on", "on:user:notify": "on", SETTLE_FIELD: SETTLED},
            )
            await self.setting_up(running, session, answering)
            await caller.post(
                f"/sessions/{session}/setup",
                {"on:user:lint": "on", "on:user:notify": "off", SETTLE_FIELD: SETTLED},
            )
            waiting = await caller.get(f"/sessions/{session}")
            await self.setting_up(running, session, answering)
            page = await caller.get(f"/sessions/{session}")

        assert "user:notify exited 1" not in waiting.text, "the failed attempt's sentence is behind us"
        assert "Setting up" in waiting.text, "and what is on screen is the pass that was asked for"
        assert page.status == 200
        enrolled = registered_in(await running.checkpointer.load(session))
        assert enrolled is not None
        assert [each.qualified for each in enrolled] == ["user:lint"]

    async def test_a_session_that_has_already_loaded_its_plugins_is_refused(self, service: Service) -> None:
        """
        A tool definition leaving the cached prefix invalidates everything under it exactly as one
        arriving late does, so which plugins run is settled the moment a registration is recorded.

        The registration and not the first message, which is the distinction a fork turns on: a branch
        carries a whole conversation and no registration, so it has to be able to answer this step
        while already holding turns. What it may not do is answer it twice.
        """
        answering = Answering()
        app, running = await self.console(service, answering)
        session = await self.declared(running)
        await running.say(session, "hello")
        async with calling(app) as caller:
            first = await caller.post(f"/sessions/{session}/setup", {SETTLE_FIELD: SETTLED})
            # What the pass the press asked for would record, written directly so that this stays a
            # test about the route rather than one about what a plugin answers to every later event.
            await registered(running, session)
            pressed = await caller.post(f"/sessions/{session}/setup", {SETTLE_FIELD: SETTLED})

        assert first.status == 303, "a session holding a message has settled nothing yet"
        assert pressed.status == 422
        assert "fork it instead" in pressed.text

    async def test_try_again_asks_for_another_pass_and_runs_nothing(self, service: Service) -> None:
        """
        The other button, and the reason the two are told apart by a field rather than by the shape of
        the post: a step with every switch off posts the same emptiness a retry does.
        """
        answering = Answering()
        app, running = await self.console(service, answering)
        session = await running.start(DEFAULT_CHOICE)
        async with calling(app) as caller:
            pressed = await caller.post(f"/sessions/{session.id}/setup", {SETTLE_FIELD: AGAIN})

        assert pressed.status == 303
        assert answering.asked == []
        assert registered_in(await running.checkpointer.load(session.id)) is None
