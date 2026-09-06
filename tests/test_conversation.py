from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import Scripted
from conftest import answered_with
from conftest import calls
from conftest import came_back
from conftest import read_to
from conftest import recorded_turn
from conftest import said_at
from conftest import steered_at
from pydantic import ValidationError
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import FilePart
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.settings import ThinkingLevel
from pydantic_ai.usage import RequestUsage
from without_durability.interfaces import INBOX
from without_durability.interfaces import claimed
from without_durability.interfaces import inbox_key
from without_durability.stepwise import Blocked
from without_durability.stepwise import Completed
from without_durability.stepwise import Run
from without_durability.stepwise import Sleeping
from without_durability.stepwise import resume

from mainplate import records
from mainplate.agent import Choice
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import Guidance
from mainplate.conversation import NeverStarted
from mainplate.conversation import Panel
from mainplate.conversation import Progressed
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
from mainplate.conversation import conversing
from mainplate.conversation import heard_key
from mainplate.conversation import instructions_key
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import opened_key
from mainplate.conversation import panelled
from mainplate.conversation import parse_choice
from mainplate.conversation import parse_delivered
from mainplate.conversation import parse_instructions
from mainplate.conversation import parse_messages
from mainplate.conversation import parted
from mainplate.conversation import reached
from mainplate.conversation import recorded_choice
from mainplate.conversation import recorded_instructions
from mainplate.conversation import requested_at
from mainplate.conversation import responded
from mainplate.conversation import so_far
from mainplate.conversation import spent_on
from mainplate.conversation import system_prompt_in
from mainplate.conversation import tooks_in
from mainplate.conversation import tool_key
from mainplate.conversation import transcript
from mainplate.conversation import tree_key
from mainplate.conversation import turn_prefix
from mainplate.durability import TOOK
from mainplate.durability import stepping
from mainplate.forge import Workspaces
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
    await service.say(session, said)


async def pass_at(
    service: Service, body: Callable[[Run], Awaitable[Progressed]], session: str = SESSION
) -> Completed[Progressed] | Sleeping | Blocked:
    """One pass at a session, claimed and released the way the worker does it."""
    holder = await claimed(service.checkpointer, session)
    try:
        return await resume(holder, service.checkpointer, body)
    finally:
        await service.checkpointer.release(holder)


async def passes_at(
    service: Service, body: Callable[[Run], Awaitable[Progressed]], session: str = SESSION
) -> tuple[Completed[Progressed] | Sleeping | Blocked, ...]:
    """
    Every pass it takes to reach a stop, which is what the worker does with `Progressed`.

    A pass that comes back `Completed` has spent its allowance mid-turn and is owed another at once,
    so this is `readying` in `app.py` with the queue taken out: the same loop, driven by hand, so a
    test can count the passes and say what each one did.
    """
    made: list[Completed[Progressed] | Sleeping | Blocked] = []
    while True:
        made.append(await pass_at(service, body, session))
        if not isinstance(made[-1], Completed):
            return tuple(made)


class TestReadingACheckpoint:
    def test_an_untouched_session_starts_at_the_first_turn(self) -> None:
        assert reached({}) == Reached(turn=0, history=())

    def test_a_turn_with_a_message_but_no_answer_is_still_the_turn_to_run(self) -> None:
        assert reached(said_at(0, "hello")) == Reached(turn=0, history=())

    def test_history_is_every_answered_turn_in_order(self) -> None:
        recorded = {
            messages_key(0): recorded_turn(
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "first"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "one"}]},
            ),
            messages_key(1): recorded_turn(
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "second"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "two"}]},
            ),
        }
        at = reached(recorded)
        assert at.turn == 2
        assert [type(message) for message in at.history] == [ModelRequest, ModelResponse, ModelRequest, ModelResponse]

    def test_an_empty_checkpoint_is_an_empty_transcript(self) -> None:
        assert transcript({}) == Transcript(panels=(), awaiting=False, turns=0)


@dataclass(frozen=True, slots=True)
class Turn:
    """One settled turn, so a history can be built several turns deep without repetition."""

    said: str
    forget: bool = False


def answered_turn(said: str, forget: bool = False) -> Turn:
    return Turn(said=said, forget=forget)


def conversation_of(*turns: Turn) -> dict[str, object]:
    """Several settled turns as one checkpoint, each opening on an entry of its own."""
    return {
        key: value
        for turn, held in enumerate(turns)
        for key, value in (
            *said_at(turn, held.said, forget=held.forget).items(),
            (
                messages_key(turn),
                recorded_turn(
                    {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": held.said}]},
                    {"kind": "response", "parts": [{"part_kind": "text", "content": f"answering {held.said}"}]},
                ),
            ),
        )
    }


class TestWhatAStepHolds:
    """
    Every checkpoint value as a record, told apart by its own tag.

    `records.Step` has no caller yet - readers parse by key, where they already know what they asked
    for - so without these a missing arm or a tag two records shared would go unnoticed until the
    first dump or migration needed it.
    """

    @pytest.mark.parametrize(
        "held",
        [
            pytest.param(records.Prompt(said="go", forget=True), id="prompt"),
            pytest.param(records.Steer(said="be brief"), id="steer"),
            pytest.param(records.Command(said="git status"), id="command"),
            pytest.param(records.Result(status=1, output="", took=timedelta(seconds=0.08)), id="result"),
            pytest.param(records.Tree(tree="a" * 40), id="tree"),
            pytest.param(records.Response(response={"kind": "response", "parts": []}), id="model"),
            pytest.param(records.Returned(returned={"lines": [1, 2]}, took=timedelta(seconds=0.25)), id="tool"),
            pytest.param(records.Messages(messages=[]), id="messages"),
        ],
    )
    def test_a_record_comes_back_as_itself_through_the_union(self, held: records.Step) -> None:
        """The round trip the codec makes, read back by tag rather than by the key it was under."""
        assert records.STEP.validate_python(held.recorded()) == held

    def test_a_choice_is_deliberately_not_an_arm(self) -> None:
        """
        The one exception, and a decision rather than an oversight: its parser encodes defaulting an
        absent isolation and re-parsing a base and a branch, which no schema here says. It carries the
        tag all the same, so a bag holding one still says what it is.
        """
        assert recorded_choice(DEFAULT_CHOICE)["kind"] == "choice"
        with pytest.raises(ValidationError):
            records.STEP.validate_python(recorded_choice(DEFAULT_CHOICE))

    def test_an_unknown_kind_is_refused_where_an_unknown_field_is_ignored(self) -> None:
        """
        The asymmetry worth knowing before relying on the rollback story: a newer build's *field* is
        survivable and a newer build's *kind* is not, which is why readers parse by key.
        """
        assert records.Prompt.model_validate({"kind": "prompt", "said": "go", "invented_later": 1}).said == "go"
        with pytest.raises(ValidationError):
            records.STEP.validate_python({"kind": "approval", "said": "go"})


class TestForgettingWhatCameBefore:
    """
    A turn that opens on a clean history, which is what `/forget` records.

    The property worth pinning is the split: the checkpoint keeps everything and the *model* is told
    nothing above the boundary. A test that only checked one of those would pass on a console that
    deleted the backlog, which is the one thing this must never do.
    """

    def test_a_turn_that_forgets_is_answered_on_nothing_before_it(self) -> None:
        recorded = conversation_of(answered_turn("first"), answered_turn("second", forget=True))
        at = reached(recorded)

        assert at.turn == 2
        # The second turn's own exchange and nothing from the first.
        assert [block.content for message in at.history for block in message.parts] == [  # type: ignore[union-attr]
            "second",
            "answering second",
        ]

    def test_the_turn_about_to_run_is_asked_too_rather_than_only_the_ones_behind_it(self) -> None:
        """
        The half this can be quietly wrong about, and the one a resumed pass would disagree with.

        The walk is conditioned on a turn having answered, so a forget on the turn *about to run* is
        never reached by it: missed, the first pass answers on the whole conversation and the pass
        that resumes it answers on nothing, which is two passes asking different questions and pairing
        one of the answers with the other.
        """
        recorded = {
            **conversation_of(answered_turn("first")),
            **said_at(1, "second", forget=True),
        }
        assert reached(recorded) == Reached(turn=1, history=())

    def test_a_fresh_pass_and_a_resumed_one_build_the_same_history(self) -> None:
        """
        Stated as the equality it is, because the two are reached by different routes: the turn about
        to run is asked outside the walk and every turn behind it inside it.
        """
        unanswered = {
            **conversation_of(answered_turn("first")),
            **said_at(1, "second", forget=True),
        }
        settled = conversation_of(answered_turn("first"), answered_turn("second", forget=True))

        # Compared by what was said rather than by whole messages, because a `UserPromptPart` with no
        # recorded timestamp is stamped afresh on every parse: two readings of one record are equal
        # in everything this is about and unequal in a field neither reading chose.
        said = [block.content for message in reached(settled).history for block in message.parts]  # type: ignore[union-attr]

        assert reached(unanswered).history == ()
        assert said == ["second", "answering second"]

    def test_a_turn_before_the_boundary_is_still_in_the_checkpoint_and_still_drawn(self) -> None:
        """
        The whole point of the word being `forget` rather than `clear`: nothing is deleted, and the
        page still shows the conversation that the model is no longer being told about.
        """
        recorded = conversation_of(answered_turn("first"), answered_turn("second", forget=True))
        said = transcript(recorded)

        assert [(panel.turn, panel.kind) for panel in said.panels if panel.kind == "prompt"] == [
            (0, "prompt"),
            (1, "prompt"),
        ]
        assert said.turns == 2

    def test_the_turn_that_forgets_says_so_on_the_panel_that_opens_it(self) -> None:
        """Where the rule reads it from, beside the tree, because both are facts about the turn."""
        recorded = conversation_of(answered_turn("first"), answered_turn("second", forget=True))
        opening = [panel for panel in transcript(recorded).panels if panel.kind == "prompt"]

        assert [panel.forget for panel in opening] == [False, True]

    def test_a_second_boundary_starts_the_history_again_from_there(self) -> None:
        """The last one wins, because each empties what the ones before it left."""
        recorded = conversation_of(
            answered_turn("first"),
            answered_turn("second", forget=True),
            answered_turn("third", forget=True),
        )
        at = reached(recorded)

        assert [block.content for message in at.history for block in message.parts] == [  # type: ignore[union-attr]
            "third",
            "answering third",
        ]

    def test_a_turn_that_forgets_nothing_is_what_every_older_session_records(self) -> None:
        """An absent field is an ordinary optional, which is what keeps every message already written readable."""
        said = parse_delivered({"kind": "prompt", "said": "written before there was a boundary"})
        assert isinstance(said, records.Prompt)
        assert said.forget is False

    def test_a_prompt_with_no_answer_yet_is_the_persons_panel_and_a_turn_still_awaited(self) -> None:
        assert transcript(said_at(0, "what is it")) == Transcript(
            panels=(Panel(turn=0, at=0, kind="prompt", blocks=(Prose(text="what is it"),)),),
            awaiting=True,
            turns=1,
            # The turn a steer would reach: the first one unanswered, which here is the only one.
            answering=0,
            # Nothing has composed this stretch's instructions yet, which is a message queued ahead of
            # the pass that will. The page draws the panel with nothing in it rather than nothing at
            # all, so what is coming is visible from the moment the message is.
            system_prompts={0: None},
        )

    def test_an_answered_turn_is_the_question_and_the_answer_as_two_panels(self) -> None:
        recorded = {
            **said_at(0, "what is it"),
            messages_key(0): recorded_turn(
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "what is it"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "a mainplate"}]},
            ),
        }
        assert transcript(recorded) == Transcript(
            panels=(
                Panel(turn=0, at=0, kind="prompt", blocks=(Prose(text="what is it"),)),
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
        assert tuple(panelled(3, parted(turn, {}))) == (
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
        assert [(panel.kind, panel.asked) for panel in panelled(0, parted(turn, {}))] == [
            ("tool", 0),
            ("steer", None),
            ("assistant", 1),
        ]

    def test_a_call_with_no_result_recorded_is_still_out(self) -> None:
        """The run ended between the call and its return, which is what a reader has to be able to see."""
        turn: list[ModelMessage] = [ModelResponse(parts=[ToolCallPart("grep", {"q": "z"}, "c9")])]
        assert blocks_of(turn, {}) == (ToolUse(tool="grep", arguments='{"q":"z"}', returned=None),)

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
        assert blocks_of(turn, {}) == (
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
        assert blocks_of(turn, {}) == (Prose(text="and"),)

    def test_a_message_that_is_not_text_is_refused_rather_than_rendered(self) -> None:
        with pytest.raises(ValidationError):
            parse_delivered({"kind": "prompt", "content": "nice try"})

    def test_something_that_is_not_a_message_at_all_is_refused_rather_than_reinterpreted(self) -> None:
        """
        What the tag buys, and why it is load-bearing here rather than a second copy of the key: the
        store names an entry, so nothing else says whether what is in it may be told to a model.
        """
        with pytest.raises(ValidationError):
            parse_delivered(records.Tree(tree="a" * 40).recorded())


# One turn holding a panel of every kind, so the pairing below is asked against a checkpoint whose
# panels are known: the person at 0, reasoning at 1, the call at 2, and the answer at 3.
FOUR_PANELS: dict[str, object] = {
    **said_at(0, "go"),
    messages_key(0): recorded_turn(
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
    ),
}


class TestWhatASessionIsAnsweredUnder:
    """
    The system prompt a page draws, which it reads from `instructions:{n}` and not from any turn.

    That is what puts the panel on the page before a turn has landed: the record is written by a pass
    before it makes the stretch's first request, where a turn's messages do not exist until it ends.
    """

    def test_a_stretch_shows_what_it_is_answered_under_before_its_turn_has_landed(self) -> None:
        recorded = {
            **said_at(0, "what is it"),
            instructions_key(0): recorded_instructions("what this session is answered under"),
        }
        said = transcript(recorded)

        assert said.awaiting, "the control: nothing has answered, so this is the state being pinned"
        assert said.system_prompts == {0: "what this session is answered under"}

    def test_a_stretch_nothing_has_composed_for_yet_is_pending_rather_than_absent(self) -> None:
        """
        A message is queued before the pass that composes for it has planted a worktree to read, so
        the page draws the panel with nothing in it rather than nothing at all.
        """
        assert transcript(said_at(0, "what is it")).system_prompts == {0: None}

    def test_a_turn_answered_under_instructions_nobody_recorded_draws_no_panel(self) -> None:
        """
        Every session written before this console recorded them, which is the loss taken knowingly.

        Absent rather than pending, and that is the distinction worth pinning: a turn that has landed
        will never compose anything now, so a panel waiting for ever on a record nobody will write is
        the one state a reader cannot diagnose.
        """
        assert transcript(conversation_of(answered_turn("first"))).system_prompts == {}

    def test_each_stretch_of_context_carries_its_own(self) -> None:
        """
        A forget composes again, so there is one per stretch under the rule that opens it. Asserted as
        the whole mapping, because what a single panel at the top of the page would do is stand the
        newest instructions over turns answered under the older ones.
        """
        recorded = {
            **conversation_of(answered_turn("first"), answered_turn("second", forget=True)),
            instructions_key(0): recorded_instructions("told this to begin with"),
            instructions_key(1): recorded_instructions("told this from the boundary on"),
        }
        assert transcript(recorded).system_prompts == {
            0: "told this to begin with",
            1: "told this from the boundary on",
        }

    def test_a_turn_that_continues_a_stretch_carries_none_of_its_own(self) -> None:
        """
        One per stretch and not one per turn, which is what keeps the panel out of every rule.

        The second turn is left unanswered on purpose: read per turn it would be a stretch with
        nothing composed for it yet, so the pending panel this draws elsewhere would appear in the
        middle of a conversation that is answering perfectly well under what turn 0 recorded.
        """
        recorded = {
            **conversation_of(answered_turn("first")),
            **said_at(1, "and another thing"),
            instructions_key(0): recorded_instructions("told this to begin with"),
        }
        assert transcript(recorded).system_prompts == {0: "told this to begin with"}


class TestWhereARequestBeganAndWhatItHeld:
    """
    Which round trip each panel came out of, and what that round trip came back with.

    A request is the unit the checkpoint has keys for, where a panel is a reading. That is the whole
    point of hanging the record on the rule at a request's boundary rather than under a panel - it is
    a lookup rather than a slice of a stored value reached by indices one walk had to hand to another.
    """

    def test_a_request_is_the_step_the_checkpoint_holds_for_it(self) -> None:
        recorded = {
            **FOUR_PANELS,
            model_key(0, 0): answered_with(THINKING_AND_CALL),
            model_key(0, 1): answered_with(THE_ANSWER),
        }
        assert requested_at(recorded, 0, 0) == answered_with(THINKING_AND_CALL)
        assert requested_at(recorded, 0, 1) == answered_with(THE_ANSWER)

    def test_a_request_nobody_made_is_nothing(self) -> None:
        assert requested_at(FOUR_PANELS, 0, 0) is None
        assert requested_at(FOUR_PANELS, 7, 0) is None

    def test_each_panel_says_which_request_it_came_out_of(self) -> None:
        """
        Two responses here, and three panels of them: the reasoning and the call are the first
        response, and the answer is the second.
        """
        drawn = [panel for panel in transcript(FOUR_PANELS).panels if panel.kind != "prompt"]
        assert [panel.asked for panel in drawn] == [0, 0, 1]

    def test_two_requests_never_share_a_panel_even_answering_the_same_way(self) -> None:
        """
        A response ending in prose and the next beginning in prose would merge into one run of one
        kind, and did. Cut by the request as well, they are two panels, which is what leaves a gap
        between them for the second request's rule to stand in.
        """
        recorded: dict[str, object] = {
            **said_at(0, "go"),
            messages_key(0): recorded_turn(
                {"kind": "response", "parts": [{"part_kind": "text", "content": "first"}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": "second"}]},
            ),
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
ASKED: dict[str, object] = said_at(0, "go")
REASONED: dict[str, object] = {**ASKED, model_key(0, 0): answered_with(THINKING_AND_CALL)}
READ: dict[str, object] = {**REASONED, tool_key(0, "c1"): came_back("b")}
ANSWERED: dict[str, object] = {**READ, model_key(0, 1): answered_with(THE_ANSWER)}


def answering(asked: int, answered: int, cost: str | None, took: float | None = None) -> ModelResponse:
    """One response with the usage a wire reported for it, as the two summing rules are fed."""
    return ModelResponse(
        parts=[TextPart("said")],
        usage=RequestUsage(input_tokens=asked, output_tokens=answered, cost=None if cost is None else Decimal(cost)),
        metadata=None if took is None else {TOOK: took},
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

    def test_a_turn_took_as_long_as_the_round_trips_it_made(self) -> None:
        """What a turn spent waiting on the provider, which is one duration per request summed."""
        spent = spent_on([answering(100, 20, "0.001", took=1.5), answering(300, 40, "0.002", took=0.25)])
        assert spent.took == timedelta(milliseconds=1_750)

    def test_one_untimed_request_leaves_the_whole_turn_untimed(self) -> None:
        """
        The rule the cost follows, for the reason the cost follows it.

        Every response recorded before this console timed anything is untimed, so a turn reporting
        the sum of the ones it could time would say a long turn was quick and nothing on the page
        could say otherwise.
        """
        assert spent_on([answering(100, 20, "0.001", took=1.5), answering(300, 40, "0.002")]).took is None

    def test_a_session_took_as_long_as_its_turns(self) -> None:
        total = altogether([Spent(1, 2, None, timedelta(seconds=3)), Spent(3, 4, None, timedelta(seconds=1.5))])
        assert total.took == timedelta(milliseconds=4_500)

    def test_one_untimed_turn_leaves_the_session_untimed(self) -> None:
        assert altogether([Spent(1, 2, None, timedelta(seconds=3)), Spent(3, 4, None)]).took is None


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
        assert opened_key(3) == "turn:3:opened"

    def test_a_steer_nothing_has_been_told_yet_is_drawn_at_the_end_of_what_there_is(self) -> None:
        """
        A message must not disappear between being sent and being answered, which is what Send
        deciding to steer would otherwise do: it lands in `turn:{n}:messages` only when the turn ends.
        """
        waiting = {**REASONED, **steered_at(1, "be brief")}
        assert so_far(waiting, 0)[-1] == Steering(text="be brief")

    def test_a_steer_already_told_is_drawn_above_the_response_it_was_appended_to(self) -> None:
        """
        The cursor is what says which request took it, so a running turn puts it where the settled
        reading will rather than at the end of what there happens to be.
        """
        told = {**REASONED, **steered_at(1, "be brief"), **read_to(0, 0, 1)}
        assert so_far(told, 0)[0] == Steering(text="be brief")

    def test_a_steer_is_drawn_once_whether_it_has_been_told_or_not(self) -> None:
        """The two halves of the walk cannot both claim it, or the page shows one message twice."""
        told = {**REASONED, **steered_at(1, "be brief"), **read_to(0, 0, 1)}
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
        returned = so_far({**REASONED, tool_key(0, "c1"): came_back(None)}, 0)[1]
        assert returned == ToolUse(tool="read", arguments='{"path":"x"}', returned=Returned("success", ""))

    def test_a_structured_result_reads_as_the_model_was_handed_it(self) -> None:
        """
        The same text `ToolReturnPart.model_response_str` produces, down to the spacing, because the
        settled reading of this call uses that and this one has to agree with it.
        """
        found = so_far({**REASONED, tool_key(0, "c1"): came_back({"lines": [1, 2]})}, 0)[1]
        assert found == ToolUse(tool="read", arguments='{"path":"x"}', returned=Returned("success", '{"lines":[1,2]}'))

    def test_the_two_readings_of_a_finished_turn_agree(self) -> None:
        """
        The property the whole thing rests on. Once every step is in, reading the turn from its
        steps and reading it from its messages produce the same blocks, so the moment the messages
        land the page morphs into markup it is already showing.
        """
        assert so_far(ANSWERED, 0) == blocks_of(parse_messages(FOUR_PANELS[messages_key(0)]), {})

    def test_a_call_carries_how_long_it_took_in_both_readings(self) -> None:
        """
        A duration is in neither reading's own source - not in the model steps and not in the turn's
        messages - so it comes from a key beside both, and both have to be handed it. Read from one
        and not the other, a call's time would appear or vanish at the moment the turn landed.
        """
        timed = {**ANSWERED, tool_key(0, "c1"): came_back("b", took=0.25)}
        called = ToolUse(
            tool="read",
            arguments='{"path":"x"}',
            returned=Returned(outcome="success", content="b"),
            took=timedelta(milliseconds=250),
        )
        assert so_far(timed, 0)[1] == called
        settled = blocks_of(parse_messages(FOUR_PANELS[messages_key(0)]), tooks_in(timed, 0, responded(timed, 0)))
        assert settled[1] == called

    def test_a_call_nothing_timed_carries_no_duration_rather_than_none_of_one(self) -> None:
        """A call recorded before durations existed, which must read as unknown and not as instant."""
        assert so_far(ANSWERED, 0)[1] == ToolUse(
            tool="read", arguments='{"path":"x"}', returned=Returned(outcome="success", content="b"), took=None
        )

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
        priced = {**ASKED, model_key(0, 0): answered_with({**THINKING_AND_CALL, "usage": SPENDING})}
        assert transcript(priced).spent == {0: Spent(asked=1_200, answered=64, cost=Decimal("0.004"))}

    def test_the_turn_in_flight_is_drawn_and_the_ones_queued_behind_it_are_not(self) -> None:
        """
        One reply is actually being written. A message typed while it runs has been said and not yet
        started, so it is a person's panel and nothing else until its own turn comes up.
        """
        said = transcript({**READ, inbox_key(1): records.Prompt(said="and another thing").recorded()})
        assert [(panel.turn, panel.kind) for panel in said.panels] == [
            (0, "prompt"),
            (0, "thinking"),
            (0, "tool"),
            (1, "prompt"),
        ]
        assert said.awaiting is True

    def test_a_running_turn_says_which_request_each_panel_came_out_of(self) -> None:
        """
        A rule and its record are offered mid-turn, unlike the panel record they replaced. A step's
        key is written once and never rewritten, so the response behind a rule is settled the moment
        it exists - where a panel's record came out of `turn:{n}:messages`, which is not written
        until the turn ends.
        """
        drawn = [panel for panel in transcript(READ).panels if panel.kind != "prompt"]
        assert [panel.asked for panel in drawn] == [0, 0]


class TestCountingTheTurns:
    def test_a_session_nobody_has_written_to_has_no_turns(self) -> None:
        assert transcript({}).turns == 0

    def test_an_answered_turn_is_one_turn(self) -> None:
        recorded = {
            **said_at(0, "a"),
            messages_key(0): recorded_turn({"kind": "response", "parts": [{"part_kind": "text", "content": "b"}]}),
        }
        assert transcript(recorded).turns == 1

    def test_a_message_nobody_has_opened_a_turn_on_is_counted_as_one(self) -> None:
        """
        A queued message has no turn of its own until a pass takes it, and the page draws one anyway:
        what a reader must never see is a message they sent going missing until a worker gets to it.
        """
        recorded = {
            **said_at(0, "a"),
            messages_key(0): recorded_turn({"kind": "response", "parts": [{"part_kind": "text", "content": "b"}]}),
            inbox_key(1): records.Prompt(said="c").recorded(),
            inbox_key(2): records.Prompt(said="d").recorded(),
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
        chosen = Choice(
            endpoint="gateway",
            model="wide/steady",
            repository="exe-github:blog",
            base="release/2.1",
            branch="try-the-other-way",
            thinking="high",
        )
        assert recorded_choice(chosen) == {
            "kind": "choice",
            "endpoint": "gateway",
            "model": "wide/steady",
            "repository": "exe-github:blog",
            "base": "release/2.1",
            "branch": "try-the-other-way",
            "isolation": {"filesystem": "nothing", "network": False},
            "thinking": "high",
        }

    def test_what_a_session_did_not_choose_is_recorded_rather_than_left_out(self) -> None:
        """Stated, so a reader can tell "asked for nothing" from "written before there was a knob"."""
        recorded = recorded_choice(Choice(endpoint="here", model="ripe/fast"))
        assert recorded == {
            "kind": "choice",
            "endpoint": "here",
            "model": "ripe/fast",
            "repository": None,
            "base": None,
            "branch": None,
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
        assert await pass_at(service, provider.body()) == Blocked(listening=frozenset({opened_key(0)}))
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
        assert await pass_at(service, provider.body()) == Blocked(listening=frozenset({opened_key(1)}))
        said = transcript(await service.checkpointer.load(SESSION))
        assert spoken(said) == [("prompt", "hello"), ("assistant", "answer 1")]
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
        await service.say(SESSION, "again")
        await pass_at(service, body)
        assert provider.asked == 2
        assert spoken(transcript(await service.checkpointer.load(SESSION))) == [
            ("prompt", "hello"),
            ("assistant", "answer 1"),
            ("prompt", "again"),
            ("assistant", "answer 2"),
        ]

    async def test_a_turn_that_forgets_is_asked_with_nothing_behind_it(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The worker's own half of the boundary, which `reached` does not cover.

        A pass carries its history forward between turns rather than reading it again, so a turn that
        forgets has to empty what the pass is holding at the moment it takes the prompt. `carried`
        counts the messages each request brought, which is the only way to see it: the turn is
        answered either way, and what differs is what the model was handed.

        **Both messages are written before the single pass runs**, which is what makes this about the
        loop rather than about `reached`. Answered by two passes, the second would re-read the
        checkpoint from the top and get the boundary right whatever the loop did.
        """
        body = provider.body()
        await started(service, said="hello")
        await service.say(SESSION, "start again", forget=True)
        await pass_at(service, body)

        assert provider.carried == [1, 1], "the second request carried its own message and nothing else"
        # And the conversation is all still there, which is the half that says nothing was deleted.
        assert spoken(transcript(await service.checkpointer.load(SESSION))) == [
            ("prompt", "hello"),
            ("assistant", "answer 1"),
            ("prompt", "start again"),
            ("assistant", "answer 2"),
        ]

    async def test_a_turn_after_a_boundary_carries_only_what_came_after_it(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The boundary holds for the turns that follow it, rather than only for the one that set it.

        One pass again, and three turns, so the history the loop carries has to grow from the
        boundary rather than from the start of the conversation.
        """
        body = provider.body()
        await started(service, said="hello")
        await service.say(SESSION, "start again", forget=True)
        await service.say(SESSION, "and then")
        await pass_at(service, body)

        assert provider.carried == [1, 1, 3], "the third request carried the two messages since the boundary"

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

        assert await pass_at(service, provider.body()) == Blocked(listening=frozenset({opened_key(1)}))
        assert provider.asked == 1

    async def test_a_turn_carries_the_conversation_so_far_to_the_model(
        self, service: Service, provider: Provider
    ) -> None:
        """A second turn must reach the model with the first exchange behind it, or it is a fresh chat."""
        body = provider.body()
        await started(service, said="hello")
        await pass_at(service, body)
        await service.say(SESSION, "again")
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
        await service.send(SESSION, "actually, be brief")
        await pass_at(service, provider.body())

        said = spoken(transcript(await service.checkpointer.load(SESSION)))
        assert ("steer", "actually, be brief") in said

    async def test_a_steer_is_drawn_as_the_person_and_not_as_the_model(
        self, service: Service, provider: Provider
    ) -> None:
        """
        `Steering` is its own block for exactly this: a panel's kind is read off its blocks, so a
        steer arriving as `Prose` would be drawn as the model answering itself.
        """
        await started(service, said="hello")
        await service.send(SESSION, "one more thing")
        await pass_at(service, provider.body())

        drawn = transcript(await service.checkpointer.load(SESSION)).panels
        steering = [panel for panel in drawn if panel.kind == "steer"]
        assert len(steering) == 1
        assert steering[0].blocks == (Steering(text="one more thing"),)

    async def test_guidance_handed_over_mid_turn_is_its_own_panel_and_not_a_steer(self) -> None:
        """
        The two things a request can carry that nobody in the conversation said, told apart.

        A steer is a `UserPromptPart` and guidance is a `SystemPromptPart`, which is the whole of why
        the delivery uses one: read as a steer it would be drawn as the person having typed what the
        console handed over, and read as prose it would be drawn as the model saying it.
        """
        said: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="what is it")]),
            ModelResponse(parts=[TextPart(content="looking")]),
            ModelRequest(
                parts=[
                    SystemPromptPart(content="`apps/web/AGENTS.md`, guidance for this part of the repository:"),
                    UserPromptPart(content="one more thing"),
                ]
            ),
            ModelResponse(parts=[TextPart(content="right")]),
        ]

        drawn = tuple(panelled(0, parted(said, {})))

        assert [panel.kind for panel in drawn] == ["assistant", "system-prompt", "steer", "assistant"]
        assert drawn[1].blocks == (Guidance(text="`apps/web/AGENTS.md`, guidance for this part of the repository:"),)

    async def test_two_messages_sent_at_once_keep_their_order_and_neither_is_lost(
        self, service: Service, provider: Provider
    ) -> None:
        """
        What the queue removed the need to check: the store names each entry, so two writers cannot
        pick one key and lose the loser's message. There is nothing to claim and nothing to re-try.
        """
        await started(service, said="hello")
        await service.send(SESSION, "first")
        await service.send(SESSION, "second")
        await pass_at(service, provider.body())

        assert [said for kind, said in spoken(transcript(await service.checkpointer.load(SESSION)))][:3] == [
            "hello",
            "first",
            "second",
        ]

    async def test_a_message_sent_after_a_turn_ended_opens_one_of_its_own(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The race this whole shape removes, played out in the order that used to lose the message.

        Somebody reads a checkpoint that says turn 0 is being answered, the pass finishes while they
        are typing, and the write lands afterwards. That used to be a slot the pass had shut, so the
        message had to be refused and said again somewhere else. In a queue it is simply the next
        thing nobody has read, and the next turn opens on it.
        """
        await started(service, said="hello")
        await pass_at(service, provider.body())

        await service.send(SESSION, "actually, be brief")
        assert spoken(transcript(await service.checkpointer.load(SESSION))) == [
            ("prompt", "hello"),
            ("assistant", "answer 1"),
            ("prompt", "actually, be brief"),
        ]

    async def test_a_message_sent_before_the_pass_reaches_the_model_is_carried_by_it(
        self, service: Service, provider: Provider
    ) -> None:
        """The other side of the same fact: nothing was listening yet, so the turn takes it."""
        await started(service, said="hello")
        await service.send(SESSION, "actually, be brief")
        await pass_at(service, provider.body())

        assert ("steer", "actually, be brief") in spoken(transcript(await service.checkpointer.load(SESSION)))

    async def test_two_sessions_do_not_see_each_other(self, service: Service, provider: Provider) -> None:
        body = provider.body()
        await started(service, said="first session", session="one")
        await started(service, said="second session", session="two")
        await pass_at(service, body, session="one")
        await pass_at(service, body, session="two")
        assert spoken(transcript(await service.checkpointer.load("one"))) == [
            ("prompt", "first session"),
            ("assistant", "answer 1"),
        ]
        assert spoken(transcript(await service.checkpointer.load("two"))) == [
            ("prompt", "second session"),
            ("assistant", "answer 2"),
        ]


class TestWhatOnePassDoes:
    """
    How a turn of several round trips is cut into passes, and what that must not change.

    A pass is one live model request and the tool batch behind it, so a turn is answered by as many
    passes as it has requests. What has to hold across that is everything: the provider is asked
    once per request whatever the cut, and the conversation the store ends up holding is the same
    one either way. The fixture repository is here because a turn needs a *tool* to be worth more
    than one request, and a tool needs a worktree to run in.
    """

    def scripted(self) -> Scripted:
        """A turn of two requests: a tool call, then the answer once its result comes back."""
        return Scripted(
            script=(
                calls(("create", {"path": "src/added.txt", "content": "written by a tool\n"})),
                ModelResponse(parts=[TextPart("made it")]),
            )
        )

    async def test_a_turn_of_two_requests_takes_a_pass_each(self, service: Service, workspaces: Workspaces) -> None:
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        body = conversing(self.scripted().endpoints(), INSTRUCTIONS, workspaces, allowance=1)

        made = await passes_at(planting, body, session.id)

        assert made == (
            Completed(Progressed()),
            Blocked(listening=frozenset({opened_key(1)})),
        ), "the first pass handed the rest of the turn back; the second finished it and waited"

    async def test_what_a_stretch_records_is_exactly_what_its_requests_carried(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """
        The claim the page rests on, since it draws the panel from the record and never from a turn.

        The two ends of one fact, held against each other: `agent_for` speaks the recorded string
        verbatim, so anything composed on top of it out there would be a sentence the model was sent
        that no record holds - a page reporting less than was said, and instructions moving under a
        conversation whose cached prefix they sit in front of.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        scripted = Scripted(script=(ModelResponse(parts=[TextPart("one")]),))
        await pass_at(planting, conversing(scripted.endpoints(), INSTRUCTIONS, workspaces), session.id)

        recorded = await planting.checkpointer.load(session.id)
        told = system_prompt_in(parse_messages(recorded[messages_key(0)]))
        assert told is not None, "the control: nothing carried means nothing to differ over"
        assert str(workspaces.root / session.id) in told, (
            "the other control: the note about this session's own worktree is the part that used to "
            "be composed after the record was written, so without it the two agree by having no "
            "chance to disagree"
        )
        assert parse_instructions(recorded[instructions_key(0)]) == told

    async def test_the_system_prompt_is_settled_before_the_first_answer_and_never_recomposed(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """
        Instructions sit in front of the cached prefix, so a session must be answered under one.

        The case that says it: the repository's own `AGENTS.md` changes mid-session, which is what a
        session working on a repository's guidance does constantly and what the *model* is the most
        likely thing to have done. Recomposed on the next turn, every later request would re-price
        the whole conversation, and re-reading buys nothing against that because the model already
        knows what it wrote.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        scripted = Scripted(script=(ModelResponse(parts=[TextPart("one")]), ModelResponse(parts=[TextPart("two")])))
        body = conversing(scripted.endpoints(), INSTRUCTIONS, workspaces, allowance=1)
        await pass_at(planting, body, session.id)

        planted = workspaces.root / session.id
        (planted / "AGENTS.md").write_text("guidance nobody had when this session opened\n", encoding="utf-8")
        await planting.say(session.id, "again")
        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        told = [system_prompt_in(parse_messages(recorded[messages_key(turn)])) for turn in (0, 1)]
        assert told[0] is not None, "the control: a session with no system prompt would pass either way"
        assert told[1] == told[0], "the second turn carried what the first was answered under"
        assert "guidance nobody had when this session opened" not in told[0]

    async def test_a_forget_composes_the_system_prompt_again(self, service: Service, workspaces: Workspaces) -> None:
        """
        The unit is a stretch of context rather than a session, and a forget is what ends one.

        Recomposing costs the requests that would have read the prefix from cache, and a forget has
        just thrown the whole prefix away, so composing again exactly there is free. It is also the
        one moment a reader might expect a repository's edited guidance to be picked up.
        """
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        scripted = Scripted(script=(ModelResponse(parts=[TextPart("one")]), ModelResponse(parts=[TextPart("two")])))
        body = conversing(scripted.endpoints(), INSTRUCTIONS, workspaces, allowance=1)
        await pass_at(planting, body, session.id)

        planted = workspaces.root / session.id
        (planted / "AGENTS.md").write_text("guidance written after the session opened\n", encoding="utf-8")
        await planting.say(session.id, "again", forget=True)
        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        told = [system_prompt_in(parse_messages(recorded[messages_key(turn)])) for turn in (0, 1)]
        assert "guidance written after the session opened" not in (told[0] or "")
        assert "guidance written after the session opened" in (told[1] or "")

    async def test_a_request_the_pass_handed_back_is_made_once_by_the_next_one(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """The unwind must not cost a provider call, which is the one way this could be expensive."""
        planting = replace(service, workspaces=workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        scripted = self.scripted()

        await passes_at(planting, conversing(scripted.endpoints(), INSTRUCTIONS, workspaces, allowance=1), session.id)

        assert scripted.asked == 2, "two requests, one per pass, and neither asked twice"

    async def test_a_turn_is_recorded_the_same_however_the_passes_fall(
        self, service: Service, workspaces: Workspaces
    ) -> None:
        """
        The claim the whole change rests on: the allowance decides how much one pass does and
        nothing about what the conversation comes to. Two sessions, the same script, cut two ways.
        """
        planting = replace(service, workspaces=workspaces)
        cut = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        whole = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE))

        await passes_at(
            planting, conversing(self.scripted().endpoints(), INSTRUCTIONS, workspaces, allowance=1), cut.id
        )
        await passes_at(planting, conversing(self.scripted().endpoints(), INSTRUCTIONS, workspaces), whole.id)

        one = await planting.checkpointer.load(cut.id)
        other = await planting.checkpointer.load(whole.id)
        # The store mints inbox keys from one sequence across every session, so two sessions never
        # hold the same ones. What compares is the shape either side of that: every key this console
        # names for itself, and the entries in the order they arrived.
        assert [key for key in one if not key.startswith(INBOX)] == [
            key for key in other if not key.startswith(INBOX)
        ], "the same keys in the same order, so nothing was written that the other did not write"
        assert [one[key] for key in one if key.startswith(INBOX)] == [
            other[key] for key in other if key.startswith(INBOX)
        ]
        assert spoken(transcript(one)) == spoken(transcript(other))
        assert [panel.kind for panel in transcript(one).panels] == [panel.kind for panel in transcript(other).panels]
        # The trees are the values that can be compared outright, holding neither a duration nor a
        # timestamp nor a key from the store's own space. A tool that ran again and wrote something
        # else shows here; so does a snapshot taken at a different point.
        settled = (tree_key(0, 0), tree_key(0, 1))
        assert {key: one[key] for key in settled} == {key: other[key] for key in settled}
        # And the cursors say the same thing in each session's own terms: nothing was steered into
        # either turn, so every request read no further than the message the turn opened on.
        for held in (one, other):
            assert held[heard_key(0, 0)] == held[heard_key(0, 1)] == held[opened_key(0)]


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
