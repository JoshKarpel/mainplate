from __future__ import annotations

from dataclasses import replace
from functools import partial

import pytest
from calling import calling
from conftest import CATALOGUE
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import Scripted
from conftest import calls
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RequestUsage
from test_conversation import pass_at
from test_conversation import passes_at
from test_conversation import started
from without_asgi import ASGIApp
from without_durability.stepwise import Blocked
from without_durability.stepwise import Completed
from without_durability.stepwise import Sleeping

from mainplate import records
from mainplate.agent import agent_for
from mainplate.catalogue import Catalogues
from mainplate.conversation import Crossed
from mainplate.conversation import Ended
from mainplate.conversation import Tendings
from mainplate.conversation import conversing
from mainplate.conversation import handing_through
from mainplate.conversation import messages_key
from mainplate.conversation import opened_key
from mainplate.conversation import opening
from mainplate.conversation import transcript
from mainplate.forge import Workspaces
from mainplate.reference import Facts
from mainplate.reference import Prices
from mainplate.reference import Reference
from mainplate.reference import References
from mainplate.service import Service
from mainplate.sessions import TENDING
from mainplate.sessions import read_tending
from mainplate.tending import HANDS_OFF_FIELD
from mainplate.tending import LEAST_ROOM
from mainplate.tending import RESERVE_FIELD
from mainplate.tending import THOUSAND
from mainplate.tending import Tending
from mainplate.tools import ASKING
from mainplate.tools.handoff.tools import LEAST

SESSION = "a-session"

DOCUMENT = (
    "## Objective\n\nMake the parser accept a trailing comma.\n\n"
    "## Where it stands\n\n`parse_list` in `src/parse.py` handles the empty case; the trailing "
    "comma still raises. Tests pass apart from `test_trailing_comma`.\n\n"
    "## Ruled out\n\nRewriting the tokenizer: the comma never reaches it, so the fix belongs in "
    "the parser.\n\n## Next\n\nRead `src/parse.py` and take the `Expected value` branch out.\n"
)


def handing_script(document: str = DOCUMENT) -> Scripted:
    """
    An ordinary turn, then one that writes a handoff, then whatever the document opens.

    The leading answer is not padding: a script is consumed in order across the whole conversation,
    so without it the person's own first message would be the turn that called the tool and the ask
    would never be the thing under test.
    """
    return Scripted(
        script=(
            ModelResponse(parts=[TextPart("hello back")]),
            calls(("hand_off", {"document": document})),
            ModelResponse(parts=[TextPart("carrying on from the handoff")]),
        )
    )


def asking_at(call: str, document: str) -> ModelResponse:
    """One `hand_off` call under an id a test names, so two attempts do not share a step key."""
    return ModelResponse(parts=[ToolCallPart(tool_name="hand_off", args={"document": document}, tool_call_id=call)])


async def handing_off(service: Service, workspaces: Workspaces, scripted: Scripted, session: str) -> None:
    """
    Every pass it takes to get from the ask to the turn the document opens.

    **Two rounds and not one, and that is the mechanism rather than the fixture.** A handoff is
    *delivered*, so the entry it writes is invisible to the pass that wrote it - `receive` reads the
    snapshot loaded at the top of a pass, which is what makes a drain replayable - and the pass ends
    `Blocked` on a turn whose message is already in the inbox. What the queue then does is exactly
    what this second round stands in for: the delivery makes the session ready and the pass that
    takes it reads a fresh snapshot.
    """
    body = conversing(
        scripted.endpoints(), INSTRUCTIONS, workspaces, handoffs=partial(handing_through, service.durable)
    )
    await passes_at(service, body, session)
    await passes_at(service, body, session)


class TestAskingASessionToHandItselfOff:
    """
    The ask, which is an ordinary message the console happens to have written.

    Nothing here is a second mechanism for summarising a conversation. The summariser is the session
    itself, answering on the prefix it has already cached, with the tools it already had.
    """

    async def test_the_ask_is_a_message_of_its_own_kind_rather_than_a_prompt(self, service: Service) -> None:
        """
        A message nobody typed has to say so, which is the whole reason it is a record of its own.

        The control is the session's real first message, which is a `Prompt`: if both parsed the same
        way there would be nothing for a reader or a page to tell apart.
        """
        await started(service, "hello")

        await service.hand_off(SESSION)

        recorded = await service.checkpointer.load(SESSION)
        held = [records.DELIVERED.validate_python(value) for key, value in recorded.items() if key.startswith("inbox:")]
        assert [type(each).__name__ for each in held] == ["Prompt", "Handoff"]
        assert isinstance(held[1], records.Handoff)
        assert held[1].said == ASKING
        assert not held[1].forget, "the context has to survive long enough to be summarised"

    async def test_it_opens_a_turn_of_its_own_rather_than_joining_the_one_running(
        self, service: Service, provider: Provider
    ) -> None:
        """
        A handoff folded into a turn already being answered would be a question nobody asked.

        `records.opens` is what decides that, and it answers for a `Handoff` exactly as it does for a
        `Prompt`, so a draining pass stops at one.
        """
        await started(service, "hello")
        await service.hand_off(SESSION)

        await passes_at(service, provider.body())

        recorded = await service.checkpointer.load(SESSION)
        first = opening(recorded, 0)
        second = opening(recorded, 1)
        assert isinstance(first, records.Prompt), "the control: the person's message opened its own turn"
        assert isinstance(second, records.Handoff), "and the ask opened the next one rather than joining it"

    async def test_a_note_is_appended_to_the_standing_ask_rather_than_replacing_it(self, service: Service) -> None:
        """
        The two say different things, so a note that replaced the ask would drop what a handoff is.

        "Focus on the parser" on its own is an instruction to summarise a summary; appended, it is
        what this handoff should dwell on, which is the only thing a person can usefully add to a
        standing instruction that already says what the document is for.
        """
        await started(service, "hello")

        await service.hand_off(SESSION, "  dwell on the parser work  ")

        recorded = await service.checkpointer.load(SESSION)
        asked = [
            records.DELIVERED.validate_python(value) for key, value in recorded.items() if key.startswith("inbox:")
        ][-1]
        assert isinstance(asked, records.Handoff)
        assert asked.said.startswith(ASKING), "the standing ask is still the whole of what a handoff is"
        assert asked.said.endswith("dwell on the parser work"), "and the note is what this one dwells on"

    async def test_saying_nothing_is_a_whole_answer(self, service: Service) -> None:
        """
        The ordinary case, which is why the field is optional and why it is not the message box.

        Whitespace reads as nothing said, so a person who tabbed through the field gets the plain ask
        rather than one with a blank line stuck on the end of it.
        """
        await started(service, "hello")

        await service.hand_off(SESSION, "   ")

        recorded = await service.checkpointer.load(SESSION)
        asked = [
            records.DELIVERED.validate_python(value) for key, value in recorded.items() if key.startswith("inbox:")
        ][-1]
        assert isinstance(asked, records.Handoff)
        assert asked.said == ASKING

    async def test_a_session_nobody_can_answer_is_not_asked(self, service: Service) -> None:
        """
        A handoff nobody will ever write is a panel that waits for ever.

        The same state the stall sentence exists to prevent, reached from the other direction: the
        route refuses rather than queueing a message into a conversation with no provider behind it.
        """
        assert await service.read("never-enrolled") is None


class TestWhatAHandoffWrites:
    """
    The turn that writes one, and what the conversation looks like on the other side of it.

    Driven with a scripted model rather than a live one, which is what makes it a test: what has to
    hold is the wiring from the tool through the inbox to a cleared history, and a real provider
    would only make that slower and more expensive to check.
    """

    async def test_the_document_becomes_the_message_that_opens_the_next_turn(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await planting.hand_off(session.id)

        await handing_off(planting, workspaces, handing_script(), session.id)

        recorded = await planting.checkpointer.load(session.id)
        written = opening(recorded, 2)
        assert isinstance(written, records.Handoff), "what the tool wrote is a message of the console's own"
        assert written.said == DOCUMENT.strip()
        assert written.forget, "and it is the document that clears the context, never the ask"

    async def test_the_turn_it_opens_is_answered_on_nothing_that_came_before_it(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """
        The point of the whole mechanism: the model after a handoff is told the document and no more.

        Asserted against what each request was *handed*, which is the only reading that can answer
        it. Neither `reached` nor the checkpoint can: `turn:{n}:messages` records what a turn
        produced rather than what it carried, and `reached` after the fact holds the last turn's own
        messages and is supposed to, so a conversation whose context was never cleared and one
        cleared a turn ago look alike in both.

        The control is the request before the boundary, which carried the whole conversation. Without
        it this would pass on a run where nothing reached the model at all.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await planting.hand_off(session.id)
        scripted = handing_script()

        await handing_off(planting, workspaces, scripted, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert messages_key(2) in recorded, "the control: the document really did open a turn that ran"
        # The person's turn, then the handoff turn's two requests, then the one the document opened.
        opening_the_handoff, tool_result_back, after_the_boundary = scripted.carried[1:4]
        assert opening_the_handoff > 1, "the control: the ask was put to a model that had the conversation"
        assert tool_result_back > opening_the_handoff, "and the turn grew as it worked, as any turn does"
        assert after_the_boundary == 1, "and the turn the document opened carried the document and nothing else"

    async def test_every_turn_above_the_boundary_is_still_in_the_transcript(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """
        Nothing is deleted and nothing is hidden, which is why the word is `forget` and not `clear`.

        The same promise `/forget` already makes, arrived at by a different route: what starts again
        is the model's history, and the checkpoint is still the whole conversation.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await planting.hand_off(session.id)

        await handing_off(planting, workspaces, handing_script(), session.id)

        said = transcript(await planting.checkpointer.load(session.id))
        assert [panel.kind for panel in said.panels if panel.at == 0] == ["prompt", "handoff", "handoff"]
        assert said.turns >= 3, "the person's turn, the one that wrote the handoff, and the one it opened"

    async def test_a_handoff_panel_says_it_was_not_typed(self, service: Service, workspaces: Workspaces) -> None:
        """
        Every other message in a conversation was written by somebody, so this one has to say it was not.

        The kind is what carries it, which is why a `Handoff` is its own record rather than a `Prompt`
        with a flag: `said_by` reads the panel's kind off the tag.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await planting.hand_off(session.id)

        await handing_off(planting, workspaces, handing_script(), session.id)

        said = transcript(await planting.checkpointer.load(session.id))
        carried = [panel for panel in said.panels if panel.kind == "handoff"]
        assert len(carried) == 2, "the ask and the document"
        assert carried[0].blocks[0].text == ASKING  # type: ignore[union-attr]
        assert carried[1].forget, "and only the document draws a boundary"


class TestWhatTheToolRefuses:
    async def test_an_acknowledgement_is_refused_rather_than_recorded_as_a_handoff(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """
        The failure the tool exists to catch: a model that says it wrote one instead of writing one.

        A `ModelRetry`, so it is correctable from the message, which is what every other refusal in
        this console's tools is. The retry budget is above Pydantic AI's default for exactly this.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        await planting.hand_off(session.id)
        # Two goes at it: the short one is refused, and the real one lands. Without the second the
        # test would be measuring a turn that ran out of retries rather than a refusal.
        #
        # The ids are spelled out rather than taken from `calls`, which numbers within one response:
        # two attempts sharing an id would share a step key, and the store refuses that before the
        # refusal under test has a chance to happen.
        script = Scripted(
            script=(
                ModelResponse(parts=[TextPart("hello back")]),
                asking_at("first", "Done, I have written the handoff."),
                asking_at("second", DOCUMENT),
                ModelResponse(parts=[TextPart("carrying on from the handoff")]),
            )
        )

        await handing_off(planting, workspaces, script, session.id)

        recorded = await planting.checkpointer.load(session.id)
        written = opening(recorded, 2)
        assert isinstance(written, records.Handoff)
        assert written.said == DOCUMENT.strip(), "the refused one was never recorded as a handoff"

    def test_the_floor_is_below_any_real_handoff_and_above_any_acknowledgement(self) -> None:
        """
        The control on the number itself, since a floor set wrong fails in whichever direction it is wrong.

        A judgement is not available here, so what the length can tell apart is a document from a
        sentence about one, and the fixture is a short but genuine handoff.
        """
        assert len("Done, I have written the handoff.") < LEAST < len(DOCUMENT)


class TestWhereTheToolLives:
    def test_every_session_carries_it_rather_than_gaining_it_when_a_handoff_is_wanted(self) -> None:
        """
        A decision about the cache rather than about convenience, and the reason is arithmetic.

        Tool definitions sit at the top of the cached prefix, above the system prompt, so adding one
        invalidates the whole conversation beneath it: introduced at handoff time it would cost a
        full uncached read of the window, where a permanent one costs its own description at
        cache-read prices. The session with no files is the case that would be tempting to skip, and
        it is exactly the one that must not be.
        """

        async def nowhere(document: str) -> None:  # pragma: no cover - never called, only offered
            raise AssertionError("the tool is not run here")

        no_files = agent_for(Provider().endpoints(), DEFAULT_CHOICE, INSTRUCTIONS, handing=nowhere)

        offered = {
            name for toolset in no_files.toolsets if isinstance(toolset, FunctionToolset) for name in toolset.tools
        }
        assert offered == {"hand_off"}, "a session with no repository has no file tools and still has this one"


WINDOW = 200_000
"""A window a long way from any default here, so nothing below passes by coinciding with a constant."""

RESERVED = 50_000
"""A reserve that is neither the shipped default nor the floor, for the same reason."""

IN_THOUSANDS = str(RESERVED // THOUSAND)
"""The same reserve as the box holds it, which is what a form posts."""

TENDED = Tending(reserve=RESERVED)
"""What every pass below is driven under unless it says otherwise: on, at a reserve of its own."""


def priced_at(window: int) -> Prices:
    """A reference that knows one thing about the default choice's model: how big its context is."""
    return Prices(
        catalogues=Catalogues(current=CATALOGUE),
        references=References(current=Reference(qualified={DEFAULT_CHOICE.model: Facts(context=window)}, upstream={})),
    )


def waiting_at(turn: int) -> Blocked:
    """A pass that answered everything there was and is waiting to be told turn `turn`."""
    return Blocked(listening=frozenset({opened_key(turn)}))


def reading(tended: Tending) -> Tendings:
    async def read(session: str) -> Tending:
        return tended

    return read


def carrying(context: int) -> ModelResponse:
    """One answer that says how much of the window the request carrying it used."""
    return ModelResponse(parts=[TextPart("answered")], usage=RequestUsage(input_tokens=context, output_tokens=10))


async def one_pass(
    service: Service,
    context: int,
    tended: Tending | None = TENDED,
    window: int | None = WINDOW,
    session: str = SESSION,
) -> Completed[Ended] | Sleeping | Blocked:
    """One pass whose single turn is answered against `context` tokens of window."""
    scripted = Scripted(script=(carrying(context),))
    body = conversing(
        scripted.endpoints(),
        INSTRUCTIONS,
        prices=None if window is None else priced_at(window),
        tendings=None if tended is None else reading(tended),
    )
    return await pass_at(service, body, session)


class TestHandingOffWithoutBeingAsked:
    """
    What the pass decides at the boundary that crosses the reserve, as a value rather than a delivery.

    Every case here is one pass and one answer, which is the whole argument for `Crossed` being an
    arm of `Ended`: what the console owes the session is decided by arithmetic over recorded numbers,
    with no store, no scheduler and no provider anywhere near it. Delivering the ask is `readying`'s.
    """

    async def test_a_turn_that_crosses_the_reserve_comes_back_asking_for_a_handoff(self, service: Service) -> None:
        await started(service, "hello")

        ended = await one_pass(service, context=WINDOW - RESERVED)

        assert ended == Completed(Crossed())

    async def test_a_turn_with_talking_left_in_it_is_left_alone(self, service: Service) -> None:
        """The control, one token below the reserve: the same pass, the same everything, no ask."""
        await started(service, "hello")

        ended = await one_pass(service, context=WINDOW - RESERVED - 1)

        assert ended == waiting_at(1), "nothing owed but the next thing somebody says"

    async def test_a_turn_that_overshot_the_window_is_not_asked_for_one_it_cannot_write(self, service: Service) -> None:
        """
        Past the close there is no room for the ask, the looking and the document, so asking would
        spend a request on a handoff that cannot land. What is left is the person's: forget, or fork.
        """
        await started(service, "hello")

        ended = await one_pass(service, context=WINDOW - LEAST_ROOM + 1)

        assert ended == waiting_at(1)

    async def test_a_turn_that_opened_on_a_handoff_never_asks_for_another(self, service: Service) -> None:
        """
        The whole of what stops this recursing. The reserve stays crossed for as long as the context
        is large, so the ask turn - whose own context is the conversation it is summarising - would
        cross it again the instant it ended, and so would every turn after that.
        """
        await started(service, "hello")
        await service.hand_off(SESSION)

        first = await one_pass(service, context=WINDOW - RESERVED)
        assert first == Completed(Crossed()), "the person's own turn crosses it"

        answering = await one_pass(service, context=WINDOW - RESERVED)

        assert answering == waiting_at(2), "and the handoff turn behind it asks for nothing"

    async def test_a_session_told_not_to_hand_itself_off_is_not_asked(self, service: Service) -> None:
        await started(service, "hello")

        ended = await one_pass(service, context=WINDOW - RESERVED, tended=Tending(hands_off=False, reserve=RESERVED))

        assert ended == waiting_at(1)

    async def test_a_console_given_no_way_to_read_the_settings_tends_nothing(self, service: Service) -> None:
        """
        A capability absent is the feature absent, which is the reading `prices` and `handoffs` take.
        It is also what keeps every test that never asks for this inert by construction rather than
        by the accident of some other value being missing.
        """
        await started(service, "hello")

        ended = await one_pass(service, context=WINDOW - RESERVED, tended=None)

        assert ended == waiting_at(1)

    async def test_a_model_nothing_knows_the_window_of_is_never_asked(self, service: Service) -> None:
        """
        No window is no fraction and no way to know a reserve was crossed, so there is nothing to act
        on. The card says which of the two silences this is; the pass simply does nothing.
        """
        await started(service, "hello")

        ended = await one_pass(service, context=WINDOW - RESERVED, window=None)

        assert ended == waiting_at(1)


class TestSayingWhenASessionHandsItselfOff:
    """The settings themselves: a row somebody writes, and the pass that reads it on its way in."""

    async def test_what_was_set_is_what_the_next_pass_reads(self, service: Service) -> None:
        session = await service.start("hello", DEFAULT_CHOICE)

        await service.tend(session.id, Tending(hands_off=False, reserve=RESERVED))

        assert await read_tending(service.database, session.id) == Tending(hands_off=False, reserve=RESERVED)

    async def test_a_session_nobody_has_told_anything_runs_on_the_constants(self, service: Service) -> None:
        session = await service.start("hello", DEFAULT_CHOICE)

        assert await read_tending(service.database, session.id) == Tending()

    async def test_a_session_is_started_on_what_the_picker_asked_for(self, service: Service) -> None:
        """The pair is part of deciding what a session is, so it is answerable before there is one."""
        session = await service.start("hello", DEFAULT_CHOICE, tended=Tending(hands_off=False, reserve=RESERVED))

        assert await read_tending(service.database, session.id) == Tending(hands_off=False, reserve=RESERVED)

    async def test_starting_on_the_defaults_writes_no_column_at_all(self, service: Service) -> None:
        """
        Which is what keeps `NULL` meaning "nobody has said anything", and keeps the defaulting branch
        the ordinary path rather than one only a database written before this existed can take. The
        picker posts this pair on every session, so recording it unconditionally would make every
        column explicit and leave a moved constant reaching nothing.
        """
        session = await service.start("hello", DEFAULT_CHOICE, tended=Tending())

        held = await service.database.run(lambda connection: connection.execute(TENDING, (session.id,)).fetchone())
        assert held == (None, None)

    async def test_a_fork_is_started_on_what_its_own_form_asked_for(self, service: Service) -> None:
        """
        Settled afresh rather than inherited, because a reserve is a decision about how much room one
        conversation's context has left and a branch's context is not that conversation's. The fork
        page starts the control on the parent's, so wanting the same thing needs nothing touched.
        """
        parent = await service.start("hello", DEFAULT_CHOICE, tended=Tending(hands_off=False, reserve=RESERVED))

        forked = await service.fork(parent.id, at=0, chosen=DEFAULT_CHOICE, tended=Tending(reserve=RESERVED * 2))

        assert forked is not None
        assert await read_tending(service.database, forked.id) == Tending(reserve=RESERVED * 2)

    async def test_both_columns_are_written_together_so_neither_is_left_behind(self, service: Service) -> None:
        """
        A switch saved without the amount beside it would leave a session running on a reserve nobody
        had looked at, so the write is one statement and this is what would catch it becoming two.
        """
        session = await service.start("hello", DEFAULT_CHOICE)
        await service.tend(session.id, Tending(hands_off=False, reserve=RESERVED))

        await service.tend(session.id, Tending(hands_off=True, reserve=RESERVED * 2))

        assert await read_tending(service.database, session.id) == Tending(hands_off=True, reserve=RESERVED * 2)

    async def test_the_card_comes_back_rather_than_the_transcript(self, service: Service, app: ASGIApp) -> None:
        """
        Nothing about the conversation changed, so swapping the transcript would replace the whole
        region in order to show what is already in the rail. What comes back is the box holding the
        value as it was recorded, in the thousands the box is denominated in.
        """
        session = await service.start("hello", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session.id}/tending", {HANDS_OFF_FIELD: "on", RESERVE_FIELD: IN_THOUSANDS}
            )

        assert answered.status == 200
        assert 'id="handoff"' in answered.text
        assert f'value="{IN_THOUSANDS}"' in answered.text
        assert await read_tending(service.database, session.id) == Tending(hands_off=True, reserve=RESERVED)

    async def test_a_session_nobody_can_answer_may_still_be_told_to_stop_spending(
        self, service: Service, app: ASGIApp
    ) -> None:
        """
        Unlike the handoff beside it, which such a session refuses: one nobody can answer is exactly
        one somebody might want the console to stop spending on, and that is the only useful thing
        left to do with it.
        """
        session = await service.start("hello", replace(DEFAULT_CHOICE, endpoint="gone"))

        async with calling(app) as caller:
            answered = await caller.post(f"/sessions/{session.id}/tending", {RESERVE_FIELD: IN_THOUSANDS})

        assert answered.status == 200
        assert await read_tending(service.database, session.id) == Tending(hands_off=False, reserve=RESERVED)

    async def test_a_session_nothing_has_heard_of_is_a_404(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.post("/sessions/nobody/tending", {RESERVE_FIELD: IN_THOUSANDS})

        assert answered.status == 404


@pytest.fixture
def provider() -> Provider:
    return Provider()
