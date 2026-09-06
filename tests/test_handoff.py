from __future__ import annotations

from dataclasses import replace
from functools import partial

import pytest
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
from test_conversation import passes_at
from test_conversation import started

from mainplate import records
from mainplate.agent import agent_for
from mainplate.conversation import conversing
from mainplate.conversation import handing_through
from mainplate.conversation import messages_key
from mainplate.conversation import opening
from mainplate.conversation import transcript
from mainplate.forge import Workspaces
from mainplate.service import Service
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


@pytest.fixture
def provider() -> Provider:
    return Provider()
