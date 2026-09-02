from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from decimal import Decimal
from itertools import pairwise
from typing import Never

import pytest
from conftest import DEFAULT_CHOICE
from conftest import Provider
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import FilePart
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.settings import ThinkingLevel
from pydantic_ai.usage import RequestUsage
from without_durability.interfaces import claimed
from without_durability.stepwise import Completed
from without_durability.stepwise import Run
from without_durability.stepwise import Sleeping
from without_durability.stepwise import Waiting
from without_durability.stepwise import resume

from mainplate.agent import Choice
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import NeverStarted
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reached
from mainplate.conversation import Reasoning
from mainplate.conversation import Request
from mainplate.conversation import Returned
from mainplate.conversation import Spent
from mainplate.conversation import Steering
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.conversation import altogether
from mainplate.conversation import blocks_of
from mainplate.conversation import heard_key
from mainplate.conversation import late_key
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import panelled
from mainplate.conversation import parse_choice
from mainplate.conversation import parse_messages
from mainplate.conversation import parse_prompt
from mainplate.conversation import parted
from mainplate.conversation import prompt_key
from mainplate.conversation import reached
from mainplate.conversation import recorded_choice
from mainplate.conversation import requested_at
from mainplate.conversation import so_far
from mainplate.conversation import spent_on
from mainplate.conversation import steer_key
from mainplate.conversation import steers_in
from mainplate.conversation import tool_key
from mainplate.conversation import transcript
from mainplate.conversation import turn_prefix
from mainplate.durability import stepping
from mainplate.sandbox import Filesystem
from mainplate.service import Service

SESSION = "a-session"


def spoken(said: Transcript) -> list[tuple[str, str]]:
    """
    A transcript as who said what, in order.

    The tests below are about a session being answered rather than about how a panel is cut, so
    they assert on this rather than on whole `Panel` values; the cutting has its own tests above.
    """
    return [
        (panel.kind, block.text)
        for panel in said.panels
        for block in panel.blocks
        if isinstance(block, Prose | Reasoning | Steering)
    ]


async def started(service: Service, said: str, session: str = SESSION) -> None:
    """
    A session recorded on the default choice, with its first message waiting.

    The choice before the prompt, in the order `Service.start` writes them: the prompt is what
    queues a session, so a worker taking it before the choice landed would find nothing to answer
    on.
    """
    await service.checkpointer.supply(session, CHOICE_KEY, recorded_choice(DEFAULT_CHOICE))
    await service.say(session, turn=0, said=said)


async def pass_at(
    service: Service, body: Callable[[Run], Awaitable[Never]], session: str = SESSION
) -> Completed[Never] | Sleeping | Waiting:
    """One pass at a session, claimed and released the way the worker does it."""
    holder = await claimed(service.checkpointer, session)
    try:
        return await resume(holder, service.checkpointer, body)
    finally:
        await service.checkpointer.release(holder)


class TestReadingACheckpoint:
    def test_an_untouched_session_starts_at_the_first_turn(self) -> None:
        assert reached({}) == Reached(turn=0, history=())

    def test_a_turn_with_a_prompt_but_no_answer_is_still_the_turn_to_run(self) -> None:
        assert reached({prompt_key(0): "hello"}) == Reached(turn=0, history=())

    def test_history_is_every_answered_turn_in_order(self) -> None:
        recorded = {
            messages_key(0): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "first"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "one"}]},
            ],
            messages_key(1): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "second"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "two"}]},
            ],
        }
        at = reached(recorded)
        assert at.turn == 2
        assert [type(message) for message in at.history] == [ModelRequest, ModelResponse, ModelRequest, ModelResponse]

    def test_an_empty_checkpoint_is_an_empty_transcript(self) -> None:
        assert transcript({}) == Transcript(panels=(), awaiting=False, turns=0)

    def test_a_prompt_with_no_answer_yet_is_the_persons_panel_and_a_turn_still_awaited(self) -> None:
        assert transcript({prompt_key(0): "what is it"}) == Transcript(
            panels=(Panel(turn=0, at=0, kind="person", blocks=(Prose(text="what is it"),)),),
            awaiting=True,
            turns=1,
            # The turn a steer would reach: the first one unanswered, which here is the only one.
            answering=0,
        )

    def test_an_answered_turn_is_the_question_and_the_answer_as_two_panels(self) -> None:
        recorded = {
            prompt_key(0): "what is it",
            messages_key(0): [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "what is it"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "a mainplate"}]},
            ],
        }
        assert transcript(recorded) == Transcript(
            panels=(
                Panel(turn=0, at=0, kind="person", blocks=(Prose(text="what is it"),)),
                # The answer came out of the turn's one model request, which is what the rule above
                # it stands at.
                Panel(turn=0, at=1, kind="assistant", blocks=(Prose(text="a mainplate"),), asked=0),
            ),
            awaiting=False,
            turns=1,
            # An answered turn always has a spend, even where every count on it is zero: what makes
            # it absent is a turn that has recorded no response at all, not one that cost nothing.
            spent={0: Spent(asked=0, answered=0, cost=None)},
            requests={0: (Request(at=0, tree=None, spent=Spent(asked=0, answered=0, cost=None)),)},
        )

    def test_a_panel_is_a_run_of_one_kind_in_the_order_the_model_worked(self) -> None:
        """Reasoning, then a call, then the answer: three panels in that sequence, not one of each hoisted."""
        turn: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="go")]),
            ModelResponse(parts=[ThinkingPart(content="have a look"), ToolCallPart("read", {"path": "x"}, "c1")]),
            ModelRequest(parts=[ToolReturnPart("read", "the body", "c1")]),
            ModelResponse(parts=[TextPart("it says hello")]),
        ]
        assert tuple(panelled(3, parted(turn))) == (
            Panel(turn=3, at=1, kind="thinking", blocks=(Reasoning(text="have a look"),), asked=0),
            Panel(
                turn=3,
                at=2,
                kind="tool",
                blocks=(
                    ToolUse(
                        tool="read",
                        arguments='{"path":"x"}',
                        returned=Returned(outcome="success", content="the body"),
                    ),
                ),
                asked=0,
            ),
            Panel(turn=3, at=3, kind="assistant", blocks=(Prose(text="it says hello"),), asked=1),
        )

    def test_a_steer_is_drawn_below_the_results_it_travelled_with_and_above_the_answer_it_shaped(
        self,
    ) -> None:
        """
        Where a steer belongs, which is what the capability appending it to the request buys.

        A person types while a batch of tool calls runs, so their message travels up with those
        results and the answer *after* it is the first one that could have been shaped by it. Drawn
        below that answer instead, the transcript would say the model had already replied when it
        arrived, which is the opposite of what happened.
        """
        turn: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="go")]),
            ModelResponse(parts=[ToolCallPart("read", {"path": "x"}, "c1")]),
            ModelRequest(parts=[ToolReturnPart("read", "the body", "c1")]),
            ModelRequest(parts=[UserPromptPart(content="be brief")]),
            ModelResponse(parts=[TextPart("hello")]),
        ]
        assert [(panel.kind, panel.asked) for panel in panelled(0, parted(turn))] == [
            ("tool", 0),
            ("steering", None),
            ("assistant", 1),
        ]

    def test_a_call_with_no_result_recorded_is_still_out(self) -> None:
        """The run ended between the call and its return, which is what a reader has to be able to see."""
        turn: list[ModelMessage] = [ModelResponse(parts=[ToolCallPart("grep", {"q": "z"}, "c9")])]
        assert blocks_of(turn) == (ToolUse(tool="grep", arguments='{"q":"z"}', returned=None),)

    def test_a_failed_call_carries_why_rather_than_a_bare_flag(self) -> None:
        """
        What the *model* was handed, which for a failure is the error wrapped as Pydantic AI wraps
        it. A transcript showing something the model never saw would be a second account of the
        turn rather than a reading of it.
        """
        turn: list[ModelMessage] = [
            ModelResponse(parts=[ToolCallPart("write", {}, "c2")]),
            ModelRequest(parts=[ToolReturnPart("write", "no such directory", "c2", outcome="failed")]),
        ]
        assert blocks_of(turn) == (
            ToolUse(
                tool="write",
                arguments="{}",
                returned=Returned(outcome="failed", content='{"error":"no such directory"}'),
            ),
        )

    def test_a_part_this_console_cannot_draw_is_passed_over_rather_than_refused(self) -> None:
        """A provider adding a part kind must not break a console that never asked for one."""
        turn: list[ModelMessage] = [
            ModelResponse(parts=[FilePart(content=BinaryContent(b"\x00", media_type="image/png")), TextPart("and")])
        ]
        assert blocks_of(turn) == (Prose(text="and"),)

    def test_a_prompt_that_is_not_text_is_refused_rather_than_rendered(self) -> None:
        with pytest.raises(TypeError):
            parse_prompt({"content": "nice try"})


# One turn holding a panel of every kind, so the pairing below is asked against a checkpoint whose
# panels are known: the person at 0, reasoning at 1, the call at 2, and the answer at 3.
FOUR_PANELS: dict[str, object] = {
    prompt_key(0): "go",
    messages_key(0): [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "go"}]},
        {
            "kind": "response",
            "parts": [
                {"part_kind": "thinking", "content": "have a look"},
                {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "x"}, "tool_call_id": "c1"},
            ],
        },
        {
            "kind": "request",
            "parts": [{"part_kind": "tool-return", "tool_name": "read", "content": "b", "tool_call_id": "c1"}],
        },
        {"kind": "response", "parts": [{"part_kind": "text", "content": "it says hello"}]},
    ],
}


class TestWhereARequestBeganAndWhatItHeld:
    """
    Which round trip each panel came out of, and what that round trip came back with.

    A request is the unit the checkpoint has keys for, where a panel is a reading. That is the whole
    point of hanging the record on the rule at a request's boundary rather than under a panel - it is
    a lookup rather than a slice of a stored value reached by indices one walk had to hand to another.
    """

    def test_a_request_is_the_step_the_checkpoint_holds_for_it(self) -> None:
        recorded = {**FOUR_PANELS, model_key(0, 0): THINKING_AND_CALL, model_key(0, 1): THE_ANSWER}
        assert requested_at(recorded, 0, 0) == THINKING_AND_CALL
        assert requested_at(recorded, 0, 1) == THE_ANSWER

    def test_a_request_nobody_made_is_nothing(self) -> None:
        assert requested_at(FOUR_PANELS, 0, 0) is None
        assert requested_at(FOUR_PANELS, 7, 0) is None

    def test_each_panel_says_which_request_it_came_out_of(self) -> None:
        """
        Two responses here, and three panels of them: the reasoning and the call are the first
        response, and the answer is the second.
        """
        drawn = [panel for panel in transcript(FOUR_PANELS).panels if panel.kind != "person"]
        assert [panel.asked for panel in drawn] == [0, 0, 1]

    def test_two_requests_never_share_a_panel_even_answering_the_same_way(self) -> None:
        """
        A response ending in prose and the next beginning in prose would merge into one run of one
        kind, and did. Cut by the request as well, they are two panels, which is what leaves a gap
        between them for the second request's rule to stand in.
        """
        recorded: dict[str, object] = {
            prompt_key(0): "go",
            messages_key(0): [
                {"kind": "response", "parts": [{"part_kind": "text", "content": "first"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "second"}]},
            ],
        }
        drawn = [panel for panel in transcript(recorded).panels if panel.kind == "assistant"]
        assert [panel.asked for panel in drawn] == [0, 1]
        assert [panel.blocks for panel in drawn] == [(Prose(text="first"),), (Prose(text="second"),)]


# The same exchange `FOUR_PANELS` holds, as the steps written while it was still running: the two
# model responses one at a time, and the call's result between them. Written out rather than derived
# from the messages above, because what these tests are for is that two independent recordings of
# one turn read the same way, and deriving either from the other would assume the answer.
THINKING_AND_CALL: dict[str, object] = {
    "kind": "response",
    "parts": [
        {"part_kind": "thinking", "content": "have a look"},
        {"part_kind": "tool-call", "tool_name": "read", "args": {"path": "x"}, "tool_call_id": "c1"},
    ],
}
THE_ANSWER: dict[str, object] = {"kind": "response", "parts": [{"part_kind": "text", "content": "it says hello"}]}

# Usage as the store holds it, which is where the cost is a *string*: a `Decimal` dumped through
# `mode="json"` is written that way, and reading it back as one is what keeps a recorded price exact.
SPENDING: dict[str, object] = {"input_tokens": 1_200, "output_tokens": 64, "cost": "0.004"}

# What the turn looked like at each moment, from nothing recorded to every step in. The person's
# message is in every one of them, because it is what queues the turn in the first place.
ASKED: dict[str, object] = {prompt_key(0): "go"}
REASONED: dict[str, object] = {**ASKED, model_key(0, 0): THINKING_AND_CALL}
READ: dict[str, object] = {**REASONED, tool_key(0, "c1"): "b"}
ANSWERED: dict[str, object] = {**READ, model_key(0, 1): THE_ANSWER}


def answering(asked: int, answered: int, cost: str | None) -> ModelResponse:
    """One response with the usage a wire reported for it, as the two summing rules are fed."""
    return ModelResponse(
        parts=[TextPart("said")],
        usage=RequestUsage(input_tokens=asked, output_tokens=answered, cost=None if cost is None else Decimal(cost)),
    )


class TestWhatATurnSpent:
    """
    Summing a turn's requests, and a session's turns, under one rule about not knowing.

    A turn is several requests and a session is several turns, so the same question is asked twice
    at two scales, and the interesting half of it is what happens when one part is unpriced.
    """

    def test_a_turn_is_the_sum_of_the_requests_it_took(self) -> None:
        """One exchange to a reader is one request per batch of tool calls to a provider."""
        spent = spent_on([answering(100, 20, "0.001"), answering(300, 40, "0.002")])
        assert spent == Spent(asked=400, answered=60, cost=Decimal("0.003"))

    def test_a_turn_with_no_responses_yet_has_spent_nothing(self) -> None:
        assert spent_on([]) == Spent(asked=0, answered=0, cost=None)

    def test_one_unpriced_request_leaves_the_whole_turn_unpriced(self) -> None:
        """
        Not the sum of the ones that were priced, which is the failure worth a test.

        A partial total reads as the whole of what a turn cost and understates it, and nothing on
        the page could say it was doing that. The counts still add up, because those are on every
        response whatever the database knows.
        """
        spent = spent_on([answering(100, 20, "0.001"), answering(300, 40, None)])
        assert spent == Spent(asked=400, answered=60, cost=None)

    def test_a_session_is_the_sum_of_its_turns(self) -> None:
        total = altogether([Spent(asked=100, answered=20, cost=Decimal("0.5")), Spent(1, 2, Decimal("0.25"))])
        assert total == Spent(asked=101, answered=22, cost=Decimal("0.75"))

    def test_one_unpriced_turn_leaves_the_session_total_unknown(self) -> None:
        """The same rule one scale up: a total quietly missing a turn is worse than no total."""
        total = altogether([Spent(asked=100, answered=20, cost=Decimal("0.5")), Spent(1, 2, None)])
        assert total == Spent(asked=101, answered=22, cost=None)

    def test_a_conversation_with_nothing_in_it_has_no_total(self) -> None:
        assert altogether([]) == Spent(asked=0, answered=0, cost=None)


class TestWatchingATurnHappen:
    """
    The turn being answered, read from the steps behind it rather than from messages it has not
    written yet.

    The risk here is two readings of one turn drifting apart. The page morphs one into the other as
    the turn lands, so a disagreement is not a wrong render but a render that silently rewrites
    itself under whoever is reading it.
    """

    def test_the_keys_are_the_ones_the_capability_writes(self) -> None:
        """
        Asserted against the literal strings rather than built with `Stepping`, which is the whole
        point: these names are built at both ends and nothing makes the two agree, so a test that
        round-tripped through the writer would pass while the reader looked in the wrong place.
        """
        assert model_key(3, 1) == "turn:3:model:1"
        assert tool_key(3, "toolu_017") == "turn:3:tool:toolu_017"
        assert heard_key(3, 1) == "turn:3:heard:1"
        assert late_key(3, 0) == "turn:3:late:0"

    def test_a_steer_nothing_has_been_told_yet_is_drawn_at_the_end_of_what_there_is(self) -> None:
        """
        A message must not disappear between being sent and being answered, which is what Send
        deciding to steer would otherwise do: it lands in `turn:{n}:messages` only when the turn ends.
        """
        waiting = {**REASONED, steer_key(0, 0): "be brief"}
        assert so_far(waiting, 0)[-1] == Steering(text="be brief")

    def test_a_steer_already_told_is_drawn_above_the_response_it_was_appended_to(self) -> None:
        """
        `heard:{i}` is what says which request took it, so a running turn puts it where the settled
        reading will rather than at the end of what there happens to be.
        """
        told = {**REASONED, steer_key(0, 0): "be brief", heard_key(0, 0): ["be brief"]}
        assert so_far(told, 0)[0] == Steering(text="be brief")

    def test_a_steer_is_drawn_once_whether_it_has_been_told_or_not(self) -> None:
        """The two halves of the walk cannot both claim it, or the page shows one message twice."""
        told = {**REASONED, steer_key(0, 0): "be brief", heard_key(0, 0): ["be brief"]}
        assert [block for block in so_far(told, 0) if isinstance(block, Steering)] == [Steering(text="be brief")]

    def test_a_turn_nothing_has_been_recorded_for_yet_has_produced_nothing(self) -> None:
        assert so_far(ASKED, 0) == ()

    def test_a_response_is_readable_as_soon_as_it_is_recorded(self) -> None:
        assert so_far(REASONED, 0) == (
            Reasoning(text="have a look"),
            ToolUse(tool="read", arguments='{"path":"x"}', returned=None),
        )

    def test_a_call_carries_its_result_as_soon_as_that_lands(self) -> None:
        assert so_far(READ, 0) == (
            Reasoning(text="have a look"),
            ToolUse(tool="read", arguments='{"path":"x"}', returned=Returned(outcome="success", content="b")),
        )

    def test_a_call_that_returned_nothing_is_finished_rather_than_still_out(self) -> None:
        """
        A step holding `None` is a step that ran, which the store keeps distinguishable from a key
        that was never written. Read together, a tool that answers with nothing would show a spinner
        for as long as the turn lasted.
        """
        returned = so_far({**REASONED, tool_key(0, "c1"): None}, 0)[1]
        assert returned == ToolUse(tool="read", arguments='{"path":"x"}', returned=Returned("success", ""))

    def test_a_structured_result_reads_as_the_model_was_handed_it(self) -> None:
        """
        The same text `ToolReturnPart.model_response_str` produces, down to the spacing, because the
        settled reading of this call uses that and this one has to agree with it.
        """
        held = so_far({**REASONED, tool_key(0, "c1"): {"lines": [1, 2]}}, 0)[1]
        assert held == ToolUse(tool="read", arguments='{"path":"x"}', returned=Returned("success", '{"lines":[1,2]}'))

    def test_the_two_readings_of_a_finished_turn_agree(self) -> None:
        """
        The property the whole thing rests on. Once every step is in, reading the turn from its
        steps and reading it from its messages produce the same blocks, so the moment the messages
        land the page morphs into markup it is already showing.
        """
        assert so_far(ANSWERED, 0) == blocks_of(parse_messages(FOUR_PANELS[messages_key(0)]))

    def test_a_turn_grows_at_the_end_and_never_in_the_middle(self) -> None:
        """
        What makes the morph safe: a panel keeps its position for the life of the turn, so nothing a
        reader has unfolded or scrolled to moves under them. Only the last block ever changes, and
        only by a call gaining the result it was waiting for.
        """
        stages = [so_far(recorded, 0) for recorded in (ASKED, REASONED, READ, ANSWERED)]
        assert [len(blocks) for blocks in stages] == [0, 2, 2, 3]
        for earlier, later in pairwise(stages):
            settled = max(len(earlier) - 1, 0)
            assert earlier[:settled] == later[:settled]

    def test_a_turn_is_priced_while_it_is_still_being_answered(self) -> None:
        """
        The half of the pricing decision that is visible on the page rather than in the store.

        A response is priced before the step records it, so what a turn has spent is readable from
        the same steps its blocks are, and a rule fills in as the turn runs instead of appearing
        whole at the end. Priced afterwards - which is where Pydantic AI does it - this would be
        nothing until `turn:0:messages` landed.
        """
        priced = {**ASKED, model_key(0, 0): {**THINKING_AND_CALL, "usage": SPENDING}}
        assert transcript(priced).spent == {0: Spent(asked=1_200, answered=64, cost=Decimal("0.004"))}

    def test_the_turn_in_flight_is_drawn_and_the_ones_queued_behind_it_are_not(self) -> None:
        """
        One reply is actually being written. A message typed while it runs has been said and not yet
        started, so it is a person's panel and nothing else until its own turn comes up.
        """
        said = transcript({**READ, prompt_key(1): "and another thing"})
        assert [(panel.turn, panel.kind) for panel in said.panels] == [
            (0, "person"),
            (0, "thinking"),
            (0, "tool"),
            (1, "person"),
        ]
        assert said.awaiting is True

    def test_a_running_turn_says_which_request_each_panel_came_out_of(self) -> None:
        """
        A rule and its record are offered mid-turn, unlike the panel record they replaced. A step's
        key is written once and never rewritten, so the response behind a rule is settled the moment
        it exists - where a panel's record came out of `turn:{n}:messages`, which is not written
        until the turn ends.
        """
        drawn = [panel for panel in transcript(READ).panels if panel.kind != "person"]
        assert [panel.asked for panel in drawn] == [0, 0]


class TestChoosingATurn:
    def test_the_first_message_goes_into_the_first_turn(self) -> None:
        assert transcript({}).turns == 0

    def test_a_message_after_an_answer_goes_into_the_next_turn(self) -> None:
        recorded = {
            prompt_key(0): "a",
            messages_key(0): [{"kind": "response", "parts": [{"part_kind": "text", "content": "b"}]}],
        }
        assert transcript(recorded).turns == 1

    def test_a_slot_already_asked_in_is_spoken_for_even_unanswered(self) -> None:
        """Otherwise a second message posted while the first is in flight would overwrite it."""
        recorded = {
            prompt_key(0): "a",
            messages_key(0): [{"kind": "response", "parts": [{"part_kind": "text", "content": "b"}]}],
            prompt_key(1): "c",
            prompt_key(2): "d",
        }
        assert transcript(recorded).turns == 3


class TestTheRecordedChoice:
    """
    The written shape, asserted against literals rather than round-tripped through the writer.

    A round trip through `recorded_choice` would agree with itself however the scheme moved, which
    is exactly what these exist to catch: a session on disk was written by an older version of this
    file and has to keep reading back as the same choice.
    """

    def test_a_choice_is_recorded_as_the_things_it_is(self) -> None:
        chosen = Choice(endpoint="gateway", model="wide/steady", repository="exe-github:blog", thinking="high")
        assert recorded_choice(chosen) == {
            "endpoint": "gateway",
            "model": "wide/steady",
            "repository": "exe-github:blog",
            "isolation": {"filesystem": "nothing", "network": False},
            "thinking": "high",
        }

    def test_what_a_session_did_not_choose_is_recorded_rather_than_left_out(self) -> None:
        """Stated, so a reader can tell "asked for nothing" from "written before there was a knob"."""
        recorded = recorded_choice(Choice(endpoint="here", model="ripe/fast"))
        assert recorded == {
            "endpoint": "here",
            "model": "ripe/fast",
            "repository": None,
            "isolation": {"filesystem": "nothing", "network": False},
            "thinking": None,
        }

    def test_a_choice_written_before_repositories_existed_still_parses(self) -> None:
        """A session started when this console could only talk works in no repository, not a broken one."""
        parsed = parse_choice({"endpoint": "here", "model": "ripe/fast", "thinking": "low"})

        assert parsed == Choice(endpoint="here", model="ripe/fast", repository=None, thinking="low")

    def test_a_repository_survives_the_checkpoint(self) -> None:
        chosen = Choice(endpoint="here", model="ripe/fast", repository="exe-github:mainplate")

        assert parse_choice(recorded_choice(chosen)) == chosen

    def test_a_repository_the_checkpoint_should_not_hold_is_refused_loudly(self) -> None:
        with pytest.raises(TypeError, match="not 17"):
            parse_choice({"endpoint": "here", "model": "ripe/fast", "repository": 17})

    def test_a_choice_written_before_thinking_existed_still_parses(self) -> None:
        """The compatibility that matters: no `thinking` key at all is the level that asks nothing."""
        assert parse_choice({"endpoint": "here", "model": "ripe/fast"}) == Choice(endpoint="here", model="ripe/fast")

    @pytest.mark.parametrize("level", [False, True, "minimal", "low", "medium", "high", "xhigh"])
    def test_every_level_survives_the_checkpoint(self, level: ThinkingLevel) -> None:
        chosen = Choice(endpoint="gateway", model="wide/steady", thinking=level)
        assert parse_choice(recorded_choice(chosen)) == chosen

    def test_a_level_the_checkpoint_should_not_hold_is_refused_loudly(self) -> None:
        with pytest.raises(TypeError, match="not 'ferocious'"):
            parse_choice({"endpoint": "here", "model": "ripe/fast", "thinking": "ferocious"})


class TestAnsweringASession:
    async def test_a_started_session_waits_to_be_told_something(self, service: Service, provider: Provider) -> None:
        await service.checkpointer.supply(SESSION, CHOICE_KEY, recorded_choice(DEFAULT_CHOICE))
        assert await pass_at(service, provider.body()) == Waiting(key=prompt_key(0))
        assert provider.asked == 0

    async def test_a_workflow_with_no_recorded_choice_is_refused_rather_than_guessed_at(
        self, service: Service, provider: Provider
    ) -> None:
        """`Service.start` writes the choice first, so reaching this means something else queued it."""
        with pytest.raises(NeverStarted):
            await pass_at(service, provider.body())

    async def test_a_message_is_answered_and_the_session_waits_again(
        self, service: Service, provider: Provider
    ) -> None:
        await started(service, said="hello")
        assert await pass_at(service, provider.body()) == Waiting(key=prompt_key(1))
        said = transcript(await service.checkpointer.load(SESSION))
        assert spoken(said) == [("person", "hello"), ("assistant", "answer 1")]
        assert not said.awaiting

    async def test_a_later_pass_replays_the_recorded_answer_rather_than_asking_again(
        self, service: Service, provider: Provider
    ) -> None:
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await pass_at(service, body)
        await pass_at(service, body)
        assert provider.asked == 1

    async def test_a_second_message_is_answered_without_re_asking_the_first(
        self, service: Service, provider: Provider
    ) -> None:
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, turn=1, said="again")
        await pass_at(service, body)
        assert provider.asked == 2
        assert spoken(transcript(await service.checkpointer.load(SESSION))) == [
            ("person", "hello"),
            ("assistant", "answer 1"),
            ("person", "again"),
            ("assistant", "answer 2"),
        ]

    async def test_a_pass_that_died_after_the_model_answered_does_not_ask_it_again(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The window the capability exists for: recorded by the model step, not by the turn's own.

        A pass that reaches the provider and dies before recording the turn leaves `turn:0:model:0`
        written and `turn:0:messages` absent, so the next pass re-runs the agent for real. Without
        the capability that second run is a second call to the provider, and a paid one.
        """
        agent = provider.agent()
        await started(service, said="hello")
        holder = await claimed(service.checkpointer, SESSION)
        run = Run(holder=holder, checkpointer=service.checkpointer, recorded=await service.checkpointer.load(SESSION))
        with stepping(run, turn_prefix(0)):
            await agent.run("hello")
        await service.checkpointer.release(holder)
        recorded = await service.checkpointer.load(SESSION)
        assert messages_key(0) not in recorded

        assert await pass_at(service, provider.body()) == Waiting(key=prompt_key(1))
        assert provider.asked == 1

    async def test_a_turn_carries_the_conversation_so_far_to_the_model(
        self, service: Service, provider: Provider
    ) -> None:
        """A second turn must reach the model with the first exchange behind it, or it is a fresh chat."""
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, turn=1, said="again")
        await pass_at(service, body)
        assert provider.carried == [1, 3]

    async def test_a_steer_written_before_the_pass_reaches_the_model_and_the_transcript(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The whole of steering, end to end: written from outside the pass, appended to the request
        being made, and read back out of the turn's own messages as a panel of its own.

        Written before the pass here rather than during one, because what a test can control is the
        store and not the instant a model is asked. What it proves is the same either way: the queue
        is read at the request rather than when the turn started.
        """
        await started(service, said="hello")
        await service.steer(SESSION, turn=0, said="actually, be brief")
        await pass_at(service, provider.body())

        said = spoken(transcript(await service.checkpointer.load(SESSION)))
        assert ("steering", "actually, be brief") in said

    async def test_a_steer_is_drawn_as_the_person_and_not_as_the_model(
        self, service: Service, provider: Provider
    ) -> None:
        """
        `Steering` is its own block for exactly this: a panel's kind is read off its blocks, so a
        steer arriving as `Prose` would be drawn as the model answering itself.
        """
        await started(service, said="hello")
        await service.steer(SESSION, turn=0, said="one more thing")
        await pass_at(service, provider.body())

        drawn = transcript(await service.checkpointer.load(SESSION)).panels
        steering = [panel for panel in drawn if panel.kind == "steering"]
        assert len(steering) == 1
        assert steering[0].blocks == (Steering(text="one more thing"),)

    async def test_two_steers_keep_their_order_and_neither_is_lost(self, service: Service) -> None:
        """
        The clash check, which is what stops the second overwriting the first: the store keeps the
        value a key was first given, so a number claimed by counting alone would drop a message.
        """
        await started(service, said="hello")
        assert await service.steer(SESSION, turn=0, said="first") == 0
        assert await service.steer(SESSION, turn=0, said="second") == 1
        assert steers_in(await service.checkpointer.load(SESSION), 0) == ("first", "second")

    async def test_a_steer_into_a_turn_that_has_stopped_listening_is_refused_rather_than_written(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The race this is built to remove, played out in the order that used to lose the message.

        Somebody reads a checkpoint that says turn 0 is being answered, the pass finishes while they
        are typing, and the write lands afterwards. It used to be accepted into a key nothing would
        ever read again: the steer was in the store, no `heard` or `late` record named it, and no
        panel drew it. The pass claiming the slot on its way out is what turns that into a refusal.
        """
        await started(service, said="hello")
        answering = transcript(await service.checkpointer.load(SESSION)).answering
        await pass_at(service, provider.body())

        assert await service.steer(SESSION, turn=answering or 0, said="actually, be brief") is None
        assert steers_in(await service.checkpointer.load(SESSION), 0) == ()

    async def test_a_message_that_lost_that_race_becomes_a_turn_of_its_own(
        self, service: Service, provider: Provider
    ) -> None:
        """
        What the refusal is *for*: `Service.send` re-decides on the true answer rather than dropping
        it. Nothing about the wording changes, only which turn it lands in.
        """
        await started(service, said="hello")
        await pass_at(service, provider.body())

        assert await service.send(SESSION, "actually, be brief") is None
        assert spoken(transcript(await service.checkpointer.load(SESSION))) == [
            ("person", "hello"),
            ("assistant", "answer 1"),
            ("person", "actually, be brief"),
        ]

    async def test_a_steer_that_won_the_race_is_carried_by_the_pass_rather_than_refused(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The other side of the same claim. Whoever gets the slot first wins it, and where that is the
        person the pass is handed their text instead of its own marker and asks once more to carry it.
        """
        await started(service, said="hello")
        assert await service.steer(SESSION, turn=0, said="actually, be brief") == 0
        await pass_at(service, provider.body())

        assert ("steering", "actually, be brief") in spoken(transcript(await service.checkpointer.load(SESSION)))

    async def test_two_sessions_do_not_see_each_other(self, service: Service, provider: Provider) -> None:
        body = provider.body()
        await started(service, said="first session", session="one")
        await started(service, said="second session", session="two")
        await pass_at(service, body, session="one")
        await pass_at(service, body, session="two")
        assert spoken(transcript(await service.checkpointer.load("one"))) == [
            ("person", "first session"),
            ("assistant", "answer 1"),
        ]
        assert spoken(transcript(await service.checkpointer.load("two"))) == [
            ("person", "second session"),
            ("assistant", "answer 2"),
        ]


class TestReadingBackWhatWasAlreadyRecorded:
    """
    A field that did not exist reads back as what the sessions written without it already had.

    Ordinary parsing of an absent optional, the same way `thinking` is read, and deliberately not a
    place where retired *values* accumulate: a recorded string this console has stopped writing is a
    migration's problem at startup, not a branch on the read path that never goes away.
    """

    def test_a_session_recorded_before_isolation_existed_reads_as_what_it_had(self) -> None:
        with_repository = parse_choice({"endpoint": "here", "model": "ripe/fast", "repository": "test:fixture"})
        without = parse_choice({"endpoint": "here", "model": "ripe/fast"})

        assert with_repository.isolation.filesystem is Filesystem.WORKTREE
        assert without.isolation.filesystem is Filesystem.NOTHING
        assert not without.isolation.network, "there was no way to reach a network then, so it reads as off"

    def test_a_value_this_console_has_never_written_is_still_a_loud_failure(self) -> None:
        """Reading a retired name back is not the same as accepting anything at all."""
        with pytest.raises(TypeError, match="is not a filesystem this console knows"):
            parse_choice({"endpoint": "here", "model": "m", "isolation": {"filesystem": "everywhere"}})
