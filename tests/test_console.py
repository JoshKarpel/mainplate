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
from mainplate.app import build_app
from mainplate.console import LONGEST_PROMPT
from mainplate.console import NotAMessage
from mainplate.console import parse_form_prompt
from mainplate.conversation import messages_key
from mainplate.conversation import prompt_key
from mainplate.profiles import parse_config
from mainplate.service import Service


async def a_session(app: ASGIApp, said: str = "what is a mainplate") -> str:
    """A session started the way a browser starts one, named by the path it was redirected to."""
    async with calling(app) as caller:
        answered = await caller.post("/sessions", starting_form(said))
        assert answered.status == 303
        return answered.location.rsplit("/", 1)[-1]


# A configuration offering something else entirely, for the session whose profile went away.
OTHER_CONFIG = """
default = "elsewhere"

[profiles.elsewhere]
provider = "anthropic"
api_key = "sk-other"
models = ["different"]
"""


def starting_form(said: str, chosen: Choice = DEFAULT_CHOICE) -> dict[str, str]:
    """What the new-chat form posts: a message and the pair chosen to answer it."""
    return {"prompt": said, "profile": chosen.profile, "model": chosen.model}


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
        """A console with nothing running makes no requests, which is what stops the polling."""
        session = await a_session(app)
        await service.checkpointer.supply(
            session,
            messages_key(0),
            [{"kind": "response", "parts": [{"part_kind": "text", "content": "it is a plate"}]}],
        )
        async with calling(app) as caller:
            answered = await caller.get(f"/fragments/sessions/{session}")
        assert "hx-trigger" not in answered.text
        assert "hx-get" not in answered.text

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
        for name in CONFIG.profiles:
            assert f'value="{name}"' in answered.text
        assert 'value="careful"' in answered.text, "the default profile's second model"
        assert 'value="fast" selected' in answered.text, "its first, preselected"

    async def test_changing_the_profile_asks_for_that_profiles_models(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")
        assert 'hx-get="/fragments/models"' in answered.text
        assert 'hx-target="#model"' in answered.text

    async def test_the_models_fragment_answers_with_one_profiles_models(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/models?profile=gateway")
        assert answered.status == 200
        assert "<html" not in answered.text
        assert 'value="fast"' in answered.text
        assert "careful" not in answered.text, "'careful' belongs to the other profile"

    async def test_the_models_fragment_for_a_profile_nothing_offers_is_refused(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/fragments/models?profile=gone")
        assert answered.status == 404

    async def test_a_session_records_the_pair_it_was_started_on(self, app: ASGIApp, service: Service) -> None:
        chosen = Choice(profile="gateway", model="fast")
        async with calling(app) as caller:
            answered = await caller.post("/sessions", starting_form("hello", chosen))
        session = answered.location.rsplit("/", 1)[-1]
        assert (await service.read(session)).chosen == chosen  # type: ignore[union-attr]

    async def test_a_pair_no_profile_offers_is_refused_rather_than_recorded(
        self, app: ASGIApp, service: Service
    ) -> None:
        """A select is a suggestion the page made, not a constraint on what somebody can post."""
        async with calling(app) as caller:
            answered = await caller.post("/sessions", starting_form("hello", Choice(profile="here", model="nope")))
        assert answered.status == 422
        assert await service.listed() == ()

    async def test_a_session_page_says_what_it_is_answered_on(self, app: ASGIApp) -> None:
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert f"{DEFAULT_CHOICE.profile}" in answered.text
        assert f"{DEFAULT_CHOICE.model}" in answered.text
        assert 'name="profile"' not in answered.text, "a session's choice is fixed, so offering one would lie"

    async def test_a_session_whose_profile_is_gone_says_so_and_stops_asking(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        The one state a person cannot otherwise diagnose: a spinner that will never resolve.

        The worker cannot answer the session, so a page that kept polling would show a pending
        panel forever with nothing saying why. Naming the profile is the whole of the fix, because
        putting it back is what makes the conversation continue where it stopped.
        """
        session = await a_session(app)
        narrowed = replace(service, config=parse_config(OTHER_CONFIG))
        async with calling(build_app(already(narrowed))) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert DEFAULT_CHOICE.profile in answered.text
        assert "no longer offers" in answered.text
        assert "hx-get" not in answered.text, "a session nothing will answer must stop asking"

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
