from __future__ import annotations

from dataclasses import replace

import pytest
from calling import calling
from conftest import CONFIG
from conftest import DEFAULT_CHOICE
from conftest import WHEN
from conftest import already
from without_asgi import ASGIApp

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.app import build_app
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Catalogues
from mainplate.catalogue import Offering
from mainplate.console import LONGEST_PROMPT
from mainplate.console import NotAMessage
from mainplate.console import parse_form_prompt
from mainplate.conversation import messages_key
from mainplate.conversation import prompt_key
from mainplate.service import Service
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import TITLE_LENGTH


async def a_session(app: ASGIApp, said: str = "what is a mainplate", title: str | None = None) -> str:
    """A session started the way a browser starts one, named by the path it was redirected to."""
    async with calling(app) as caller:
        answered = await caller.post("/sessions", starting_form(said, title=title))
        assert answered.status == 303
        return answered.location.rsplit("/", 1)[-1]


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


def starting_form(said: str, chosen: Choice = DEFAULT_CHOICE, title: str | None = None) -> dict[str, str]:
    """
    What the new-session form posts: a message, the pair chosen to answer it, and maybe a name.

    The name is omitted when it is `None`, which is what a browser sends for a field left empty:
    `parse_qs` drops empty values, so an untouched box never reaches the handler as a field at all.
    """
    posted = {"prompt": said, "endpoint": chosen.endpoint, "model": chosen.model}
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


class TestTheConsole:
    async def test_the_start_page_offers_a_box_and_creates_nothing(self, app: ASGIApp, service: Service) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert answered.status == 200
        assert 'class="composer"' in answered.text
        assert await service.listed() == ()

    async def test_the_first_message_creates_a_session_and_redirects_to_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await a_session(app)
        listed = await service.listed()
        assert [(each.id, each.title, each.created_at) for each in listed] == [(session, "what is a mainplate", WHEN)]

    async def test_the_first_message_is_waiting_in_the_checkpoint(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app)
        recorded = await service.checkpointer.load(session)
        assert recorded[prompt_key(0)] == "what is a mainplate"

    async def test_an_unanswered_session_renders_the_question_and_asks_again(self, app: ASGIApp) -> None:
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert answered.status == 200
        assert "what is a mainplate" in answered.text
        assert f'hx-get="/fragments/sessions/{session}"' in answered.text

    async def test_an_unanswered_session_asks_again_on_a_timer_rather_than_on_a_load(self, app: ASGIApp) -> None:
        """
        The swap is a morph, which keeps the element rather than replacing it.

        A `load` trigger fires once per element load, so it repeats only where each answer replaces
        the region. Under a morph it fires exactly once and the conversation then waits forever on
        an answer that has already arrived, with nothing on the page saying so. Pinned here because
        that failure is invisible to every other assertion in this file: the markup is identical
        either way.
        """
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert 'hx-trigger="every 1s"' in answered.text
        assert 'hx-swap="outerMorph"' in answered.text

    async def test_an_answered_session_carries_no_trigger_at_all(self, app: ASGIApp, service: Service) -> None:
        """
        A console with nothing running asks for nothing, which is what stops the polling.

        Named against the poll's own trigger and its own URL rather than against `hx-get` and
        `hx-trigger` at large. A settled panel carries a disclosure that fetches what the
        checkpoint holds behind it, which is an htmx attribute this assertion must not read as a
        conversation still waiting for an answer.
        """
        session = await a_session(app)
        await service.checkpointer.supply(
            session,
            messages_key(0),
            [{"kind": "response", "parts": [{"part_kind": "text", "content": "it is a plate"}]}],
        )
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert 'hx-trigger="every 1s"' not in answered.text
        assert f'hx-get="/fragments/sessions/{session}"' not in answered.text

    async def test_a_message_into_a_session_answers_with_the_transcript_alone(self, app: ASGIApp) -> None:
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session}/messages", {"prompt": "and another thing"})
        assert answered.status == 200
        assert "<html" not in answered.text
        assert "and another thing" in answered.text

    async def test_a_second_message_takes_the_next_turn(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app)
        async with calling(app) as caller:
            await caller.post(f"/sessions/{session}/messages", {"prompt": "and another thing"})
        recorded = await service.checkpointer.load(session)
        assert recorded[prompt_key(1)] == "and another thing"

    async def test_a_message_queued_behind_an_unanswered_one_is_still_shown(self, app: ASGIApp) -> None:
        """It is recorded and it will be answered, so a page that hid it would be lying about it."""
        session = await a_session(app, "the first thing")
        async with calling(app) as caller:
            await caller.post(f"/sessions/{session}/messages", {"prompt": "the second thing"})
            answered = await caller.get(f"/sessions/{session}")
        assert "the first thing" in answered.text
        assert "the second thing" in answered.text

    async def test_the_sidebar_lists_every_session_newest_first(self, app: ASGIApp) -> None:
        await a_session(app, "the older one")
        await a_session(app, "the newer one")
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

    async def test_a_fragment_for_a_session_nobody_started_is_refused(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/sessions/nothing-here")
        assert answered.status == 404

    async def test_a_path_nothing_serves_is_a_page_rather_than_a_bare_status(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/nowhere")
        assert answered.status == 404
        assert "/nowhere" in answered.text

    async def test_an_empty_message_is_refused_rather_than_recorded(self, app: ASGIApp, service: Service) -> None:
        """A client refusal, not a server fault: a request that is not a message did not break anything."""
        async with calling(app) as caller:
            answered = await caller.post("/sessions", starting_form("   "))
        assert answered.status == 422
        assert await service.listed() == ()

    async def test_a_send_refuses_a_swap_of_anything_that_is_not_a_transcript(self, app: ASGIApp) -> None:
        """Every status but 204 and 304 swaps in htmx 4, so a refusal would otherwise replace the conversation."""
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert 'hx-status:4xx="swap:none"' in answered.text
        assert 'hx-status:5xx="swap:none"' in answered.text

    async def test_the_page_serves_its_own_stylesheet_and_scripts(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            for asset in ("/assets/mainplate.css", "/assets/mainplate.js", "/assets/htmx.min.js"):
                assert (await caller.get(asset)).status == 200

    async def test_the_start_page_offers_every_profile_and_the_defaults_models(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")
        for name in CONFIG.endpoints:
            assert f'value="{name}"' in answered.text
        assert 'value="ripe/careful"' in answered.text, "the default endpoint's second model"
        assert 'value="ripe/fast" checked' in answered.text, "the first it listed, preselected"

    async def test_a_session_is_named_by_the_box_when_one_is_given(self, app: ASGIApp, service: Service) -> None:
        session = await a_session(app, "the first thing said", title="Reading the checkpointer")
        found = await service.read(session)
        assert found is not None
        assert found.session.title == "Reading the checkpointer"

    async def test_a_session_with_no_name_given_is_named_after_its_first_message(
        self, app: ASGIApp, service: Service
    ) -> None:
        """The behaviour every session had before the field existed, and still the default."""
        session = await a_session(app, "the first thing said")
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
        session = await a_session(app, "the first thing said", title=given)
        found = await service.read(session)
        assert found is not None
        assert found.session.title == "the first thing said"

    async def test_a_given_name_is_cut_by_the_same_rule_a_message_is(self, app: ASGIApp, service: Service) -> None:
        """
        One rule about what a session name is, rather than one per way of arriving at one.

        A sidebar seventeen rems wide can hold only so much, and a name typed into a box can be
        arbitrarily long where one taken from a message was already cut.
        """
        session = await a_session(app, "hello", title="w " * 200)
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
            answered = await caller.post("/sessions", starting_form("hello", chosen))
        session = answered.location.rsplit("/", 1)[-1]
        assert (await service.read(session)).chosen == chosen  # type: ignore[union-attr]

    async def test_a_pair_no_profile_offers_is_refused_rather_than_recorded(
        self, app: ASGIApp, service: Service
    ) -> None:
        """A select is a suggestion the page made, not a constraint on what somebody can post."""
        async with calling(app) as caller:
            answered = await caller.post("/sessions", starting_form("hello", Choice(endpoint="here", model="nope")))
        assert answered.status == 422
        assert await service.listed() == ()

    async def test_a_session_page_says_what_it_is_answered_on(self, app: ASGIApp) -> None:
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert f"{DEFAULT_CHOICE.endpoint}" in answered.text
        assert f"{DEFAULT_CHOICE.model}" in answered.text
        assert 'name="endpoint"' not in answered.text, "a session's choice is fixed, so offering one would lie"

    async def test_a_session_whose_profile_is_gone_says_so_and_stops_asking(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The one state a person cannot otherwise diagnose: a spinner that will never resolve.

        The worker cannot answer the session, so a page that kept polling would show a pending
        panel forever with nothing saying why. Naming the endpoint is the whole of the fix, because
        putting it back is what makes the conversation continue where it stopped.
        """
        session = await a_session(app)
        narrowed = replace(service, catalogues=Catalogues(current=OTHER_CATALOGUE))
        async with calling(build_app(already(narrowed))) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert DEFAULT_CHOICE.endpoint in answered.text
        assert "no longer declares" in answered.text
        assert f'hx-get="/fragments/sessions/{session}"' not in answered.text, (
            "a session nothing will answer must stop asking"
        )

    async def test_a_session_on_a_model_the_picker_stopped_listing_is_not_stuck(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        An endpoint routes more ids than it advertises, so a missing model is not a stuck session.

        The case is real rather than hypothetical: exe.dev's gateway answers `claude-sonnet-4-6`
        while listing it as `anthropic/claude-sonnet-4-6`, so every session recorded before that
        prefix appeared names a model discovery will never return. Calling those stuck would tell
        somebody to restore a model nobody removed, and would stop the poll on a conversation the
        worker can still answer.
        """
        session = await a_session(app)
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
        assert f'hx-get="/fragments/sessions/{session}"' in answered.text, (
            "the worker can still answer it, so the page must keep asking"
        )

    async def test_a_message_is_rendered_as_the_markdown_it_was_written_as(self, app: ASGIApp) -> None:
        session = await a_session(app, "a **strong** point")
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert "<strong>strong</strong>" in answered.text

    async def test_markup_in_a_message_does_not_become_markup(self, app: ASGIApp) -> None:
        """
        Asserted on the fragment rather than the page, which is not a detail.

        A session is named after its first message, so the page also carries that text in the
        sidebar, where it is escaped as an ordinary child. A page-level assertion would therefore
        pass on the sidebar's copy whatever the transcript did with it, which is the check that
        cannot fail measuring the wrong thing.
        """
        session = await a_session(app, "<script>alert(1)</script> and <img src=x onerror=alert(2)>")
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert "<script" not in answered.text
        assert "alert(1)" not in answered.text
        assert "onerror" not in answered.text


ANSWERED = [
    {
        "kind": "response",
        "parts": [
            {"part_kind": "thinking", "content": "the trigger fires once"},
            {"part_kind": "text", "content": "it is a plate"},
        ],
    }
]


class TestShowingWhatWasRecorded:
    """
    The disclosure under a panel, and the fragment behind it.

    A panel is a *reading* of the checkpoint rather than a row in it, so what these pin is that the
    two agree about which stored parts a given panel was read out of.
    """

    async def answered_session(self, app: ASGIApp, service: Service) -> str:
        session = await a_session(app)
        await service.checkpointer.supply(session, messages_key(0), ANSWERED)
        return session

    async def test_a_panel_carries_a_disclosure_pointed_at_its_own_record(self, app: ASGIApp, service: Service) -> None:
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert f'hx-get="/fragments/sessions/{session}/panels/0/1"' in answered.text
        assert f'hx-get="/fragments/sessions/{session}/panels/0/2"' in answered.text

    async def test_the_record_is_not_carried_by_the_transcript_itself(self, app: ASGIApp, service: Service) -> None:
        """
        Fetched rather than rendered, which is the whole reason it is an endpoint: the transcript
        is swapped once a second while a turn is in flight, and this is several times the size of
        the reading of it.
        """
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert "the trigger fires once" in answered.text, "the reading of the part is on the page"
        assert '"part_kind"' not in answered.text, "the record behind it is not"

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
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert "hx-preserve" in answered.text
        assert 'hx-trigger="toggle once"' in answered.text, "settled for good, so asked for once"

    async def test_a_model_panel_answers_with_the_parts_it_was_read_out_of(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/panels/0/1")
        assert answered.status == 200
        assert '"part_kind": "thinking"' in answered.text
        assert '"the trigger fires once"' in answered.text
        assert "it is a plate" not in answered.text, "the prose panel is a panel of its own"

    async def test_a_person_panel_answers_with_the_one_key_that_is_that_panel(
        self, app: ASGIApp, service: Service
    ) -> None:
        """The exception, and the only panel the checkpoint has a key for on its own."""
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/panels/0/0")
        assert answered.status == 200
        assert '"what is a mainplate"' in answered.text

    async def test_a_panel_nobody_recorded_is_refused_rather_than_rendered_empty(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await self.answered_session(app, service)
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/panels/0/9")
        assert answered.status == 404
        assert "Nothing is recorded" in answered.text

    async def test_a_session_nobody_started_is_refused(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/sessions/deadbeef/panels/0/0")
        assert answered.status == 404

    async def test_markup_inside_a_record_does_not_become_markup(self, app: ASGIApp, service: Service) -> None:
        """
        The raw record carries whatever the model said, which is shaped by whatever reached the box.

        Shown as text and not as rendered Markdown, so the sanitiser this page uses elsewhere is not
        in the path at all: what stands in for it is that a node tree escapes a text child.
        """
        session = await a_session(app)
        await service.checkpointer.supply(
            session,
            messages_key(0),
            [{"kind": "response", "parts": [{"part_kind": "text", "content": "<script>alert(1)</script>"}]}],
        )
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}/panels/0/1")
        assert answered.status == 200
        assert "<script" not in answered.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in answered.text
