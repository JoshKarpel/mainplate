from __future__ import annotations

import pytest
from calling import calling
from conftest import WHEN
from without_asgi import ASGIApp

from mainplate.console import LONGEST_PROMPT
from mainplate.console import NotAMessage
from mainplate.console import next_turn
from mainplate.console import parse_form_prompt
from mainplate.conversation import Exchange
from mainplate.conversation import Transcript
from mainplate.conversation import prompt_key
from mainplate.service import Service


async def a_session(app: ASGIApp, said: str = "what is a mainplate") -> str:
    """A session started the way a browser starts one, named by the path it was redirected to."""
    async with calling(app) as caller:
        answered = await caller.post("/sessions", {"prompt": said})
        assert answered.status == 303
        return answered.location.rsplit("/", 1)[-1]


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


class TestChoosingATurn:
    def test_the_first_message_goes_into_the_first_turn(self) -> None:
        assert next_turn(Transcript(exchanges=(), pending=())) == 0

    def test_a_message_after_an_answer_goes_into_the_next_turn(self) -> None:
        assert next_turn(Transcript(exchanges=(Exchange(prompt="a", reply="b"),), pending=())) == 1

    def test_a_slot_already_asked_in_is_spoken_for_even_unanswered(self) -> None:
        """Otherwise a second message posted while the first is in flight would overwrite it."""
        assert next_turn(Transcript(exchanges=(Exchange(prompt="a", reply="b"),), pending=("c", "d"))) == 3


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
            answered = await caller.post("/sessions", {"prompt": "   "})
        assert answered.status == 422
        assert await service.listed() == ()

    async def test_a_send_refuses_a_swap_of_anything_that_is_not_a_transcript(self, app: ASGIApp) -> None:
        """Every status but 204 and 304 swaps in htmx 4, so a refusal would otherwise replace the conversation."""
        session = await a_session(app)
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert 'hx-status:4xx="swap:none"' in answered.text
        assert 'hx-status:5xx="swap:none"' in answered.text

    async def test_the_page_serves_its_own_stylesheet_and_script(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            for asset in ("/assets/mainplate.css", "/assets/htmx.min.js"):
                assert (await caller.get(asset)).status == 200

    async def test_what_the_model_says_is_escaped_rather_than_rendered(self, app: ASGIApp, service: Service) -> None:
        """A prompt is somebody else's text on this page, so markup in it must not become markup."""
        session = await a_session(app, "<script>alert(1)</script>")
        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session}")
        assert "<script>alert(1)</script>" not in answered.text
        assert "&lt;script&gt;" in answered.text
