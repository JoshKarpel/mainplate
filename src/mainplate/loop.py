# The model-and-tool loop one turn is answered by, over Pydantic AI's models, messages, settings and
# toolsets, and none of its agent graph.
#
# What this owns is the *order* of a turn: what is put in front of a request, when the request is
# made, when a batch of tool calls runs, when text is an answer, and when a plugin gets to say it is
# not one yet. Every one of those that has an effect is a step `Stepping` records, and a loop whose
# order is written here hands the `Stepping` to each as an argument. The graph this replaced could be
# reached only through a capability wrapped around it and a context variable under that, and the
# order was its to change between releases. `docs/design/durability.md` is the argument, and what was
# given up with the graph: structured output, native and deferred tools, usage limits, and the rest
# of what an `Agent` there does that this console never asked for.
#
# **Everything here that is not a step has to be deterministic.** A pass replays a turn by running
# this loop again over the recorded responses and returns, so it must ask the same questions in the
# same order on every pass, and nothing it reads may come from anywhere but its arguments and the
# records.

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from typing import Final

from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import ContentFilterError
from pydantic_ai.exceptions import IncompleteToolCall
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import InstructionPart
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets import ToolsetTool
from pydantic_ai.usage import RunUsage

from mainplate.durability import Stepping

RETRIES: Final = 1
"""
How many times one tool, or an answer with nothing in it, may be corrected before the turn fails.

Pydantic AI's own default, kept rather than chosen: a model that mis-calls the same tool twice
running is not going to be talked out of it by a third prompt, and a turn that fails here is one
`conversing` records and the page draws, where a turn that loops is one nobody sees the end of.
"""


type Keeping = Callable[[int, int], Awaitable[Sequence[str]]]
"""
What the session's plugins say when the turn tries to end, by which attempt at ending this is and
how many responses the turn has made, taken under the key that records it.

A function for `Injecting`'s reason: what answers it runs somebody else's script, and injecting the
one question keeps this loop ignorant of what a plugin is.
"""


@dataclass(slots=True)
class Tools:
    """
    Every tool the model may call this turn, by name, and how many times each has been corrected.

    Resolved once per turn rather than per request, which is what Pydantic AI's toolsets allow for
    and what determinism asks for: a toolset whose tools changed between two requests of one turn
    would change the recorded response's meaning on replay. `context` is the `RunContext` every
    call is validated and run in, and it is the one Pydantic AI object here that is a place rather
    than a value: its `messages` is the turn's own list, so a tool that reads the conversation sees
    it as it stands at the call.
    """

    by_name: Mapping[str, ToolsetTool[None]]
    retries: dict[str, int]
    context: RunContext[None]

    @classmethod
    async def from_toolsets(
        cls, model: Model, messages: list[ModelMessage], toolsets: Sequence[AbstractToolset[None]]
    ) -> Tools:
        context = RunContext(deps=None, model=model, usage=RunUsage(), messages=messages, max_retries=RETRIES)
        by_name: dict[str, ToolsetTool[None]] = {}
        for toolset in toolsets:
            for name, tool in (await toolset.get_tools(context)).items():
                if name in by_name:
                    raise ValueError(f"two tools are named {name!r}")
                by_name[name] = tool
        return cls(by_name=by_name, retries={}, context=context)

    async def call(self, scope: Stepping, call: ToolCallPart) -> ToolReturnPart | ValidationError | ModelRetry:
        """
        One call, validated here and run as a step of `scope`, or the correction it earned instead.

        The correction is *returned* rather than raised, because a batch runs under `gather` and the
        other calls in it have to finish and be recorded whatever this one did: raising would cancel
        siblings whose tools may already have written. `result` is where a correction becomes a
        retry prompt or a failure, once the whole batch is in.

        Validation runs outside the step and the tool inside it, so a call the model got wrong is
        never recorded as having returned anything, and a pass that replays the turn validates it
        again, deterministically, to the same correction. A `ToolFailed` is a return, not a
        correction: the tool ran and said what went wrong, and the model is told so in a result
        marked failed rather than asked to call again.
        """
        tool = self.by_name.get(call.tool_name)
        if tool is None:
            return ModelRetry(f"Unknown tool name: {call.tool_name!r}")

        context = replace(
            self.context,
            retries=self.retries,
            tool_name=call.tool_name,
            tool_call_id=call.tool_call_id,
            retry=self.retries.get(call.tool_name, 0),
            max_retries=tool.max_retries,
        )
        try:
            arguments = await validated(call, tool, context)
            returned = await scope.call(call, arguments, context, tool)
        except (ValidationError, ModelRetry) as error:
            return error
        except ToolFailed as error:
            return ToolReturnPart(
                tool_name=call.tool_name,
                content=error.message,
                tool_call_id=call.tool_call_id,
                tool_kind=call.tool_kind,
                outcome="failed",
            )
        return ToolReturnPart(
            tool_name=call.tool_name,
            content=returned,
            tool_call_id=call.tool_call_id,
            tool_kind=call.tool_kind,
        )

    def result(
        self,
        call: ToolCallPart,
        attempted: ToolReturnPart | ValidationError | ModelRetry,
    ) -> ToolReturnPart | RetryPromptPart:
        """
        The part the next request carries for this call: its return, or the prompt to try again.

        Corrections are counted per tool name across the turn, which is Pydantic AI's accounting
        and is kept for the reason it was chosen there: a model that gets `edit` wrong on three
        different files is stuck on `edit`, not on any one call. Past the count the turn fails
        rather than prompting again, and `UnexpectedModelBehavior` is what `conversing` reads a
        turn that cannot go on as.
        """
        if isinstance(attempted, ToolReturnPart):
            return attempted
        tool = self.by_name.get(call.tool_name)
        maximum = RETRIES if tool is None else tool.max_retries
        used = self.retries.get(call.tool_name, 0)
        if used >= maximum:
            raise UnexpectedModelBehavior(
                f"Tool {call.tool_name!r} exceeded max retries count of {maximum}."
            ) from attempted
        self.retries[call.tool_name] = used + 1
        return RetryPromptPart.from_error(attempted, tool_name=call.tool_name, tool_call_id=call.tool_call_id)


async def validated(call: ToolCallPart, tool: ToolsetTool[None], context: RunContext[None]) -> dict[str, object]:
    """
    The call's arguments as the tool's own schema accepts them, raising `ValidationError` otherwise.

    Both shapes a model produces arguments in, because the two wires differ: a JSON string on one
    and a mapping on the other, and a recorded response replays whichever it was.
    """
    if isinstance(call.args, str):
        arguments = tool.args_validator.validate_json(call.args or "{}", context=context.validation_context)
    else:
        arguments = tool.args_validator.validate_python(call.args or {}, context=context.validation_context)
    if tool.args_validator_func is not None:
        result = tool.args_validator_func(context, **arguments)
        if inspect.isawaitable(result):
            await result
    return arguments


@dataclass(frozen=True, slots=True)
class Agent:
    """
    What answers one session: a model, what it is told, how it is asked, and what it may call.

    A value, built by `agent_for` for the pass that is about to run it, and holding nothing a pass
    could leave behind: every effect of a run goes through the `Stepping` handed to `run`, so one
    `Agent` could answer the same turn on two passes and record the same thing.
    """

    model: Model
    instructions: str
    settings: ModelSettings
    toolsets: tuple[AbstractToolset[None], ...]

    async def run(
        self,
        asked: str,
        history: Sequence[ModelMessage],
        scope: Stepping,
        keeping: Keeping | None = None,
    ) -> tuple[ModelMessage, ...]:
        """
        One turn's messages: every request made and every response to it, from `asked` to the answer.

        The loop is the ordinary one and its order is the whole of what is being pinned. A response
        that calls tools runs them as a batch and carries their returns into the next request. One
        that says something is an answer, unless the session's plugins keep the turn going, in which
        case what they said is put to the model in the console's voice and the loop goes round again.
        One that does neither is corrected once and then given up on.

        **The gate in front of the turn ending is what a Claude Code `Stop` hook is.** The model has
        answered and would stop; `keeping` is asked; what it injected is a `SystemPromptPart` and not
        a `UserPromptPart`, because nobody typed it, and `interjected` draws the two apart by which
        part carried them. One request rather than one per plugin, so what several said arrives as
        one thing to answer. `keeping` absent is a session none of whose plugins asked, and the turn
        ends the first time the model answers.

        **A tool call the model was cut off writing is raised, not corrected.** Its arguments fail
        validation like any bad call's, so left to the retry path the model would be told they were
        malformed and asked again, spending a request on an answer that was never wrong. It is the
        same cut-off as a thinking-only response stopped at the limit, and `conversing` settles both
        the same way; see `cut_off_writing_a_call`.

        The turn is unbounded. What a session may spend is a question about money and not round
        trips, and it is answered where money is counted.
        """
        messages = [*history, ModelRequest(parts=[UserPromptPart(content=asked)], instructions=self.instructions)]
        start = len(history)
        tools = await Tools.from_toolsets(self.model, messages, self.toolsets)
        ending = 0
        corrected = 0
        while True:
            await self.before_request(scope, messages)
            response = await self.request(scope, messages, tools)
            messages.append(response)

            calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
            if calls:
                if cut_off_writing_a_call(response):
                    raise IncompleteToolCall("the model was cut off at its output limit while writing a tool call")
                if len({call.tool_call_id for call in calls}) != len(calls):
                    raise ValueError("a model response contains duplicate tool call ids")
                attempted = await asyncio.gather(*(tools.call(scope, call) for call in calls))
                results = [tools.result(call, result) for call, result in zip(calls, attempted, strict=True)]
                messages.append(ModelRequest(parts=results, instructions=self.instructions))
                continue

            if said_something(response):
                if keeping is None:
                    return tuple(messages[start:])
                injected = await keeping(
                    ending, sum(isinstance(message, ModelResponse) for message in messages[start:])
                )
                if not injected:
                    return tuple(messages[start:])
                ending += 1
                messages.append(
                    ModelRequest(
                        parts=[SystemPromptPart(content=text) for text in injected],
                        instructions=self.instructions,
                    )
                )
                continue

            if response.finish_reason == "length":
                raise UnexpectedModelBehavior("Model token limit exceeded before any response was generated.")
            if response.finish_reason == "content_filter":
                body = ModelMessagesTypeAdapter.dump_json([response]).decode()
                raise ContentFilterError("Content filter triggered.", body=body)
            if corrected >= RETRIES:
                raise UnexpectedModelBehavior(f"Model output exceeded max retries count of {RETRIES}.")
            corrected += 1
            messages.append(
                ModelRequest(
                    parts=[RetryPromptPart(content="Please return text or call a tool.")],
                    instructions=self.instructions,
                )
            )

    async def before_request(self, scope: Stepping, messages: list[ModelMessage]) -> None:
        """
        Put what arrived mid-turn to the model, in *this* request, appended to the history it keeps.

        **The allowance is spent first**, before anything is recorded for the request, and that is
        not tidying: the drain below records how far this turn has read, so a pass that recorded one
        and *then* refused the request would leave a cursor for a request nobody made. The next pass
        replays that cursor, so a message delivered while the refused request was being decided
        waits a further round trip, or, if the turn ends first, opens a turn of its own. Refusing
        before the drain is what makes a message reach the very next request the pass after this
        one makes. `Stepping.request` spends it again under the same key, which is how whichever
        runs first is the one that pays; see `Stepping.allowed`.

        What a plugin injects first, then the steer, which is the order they were produced in: what
        a plugin has to say about the request was true before the person typed anything into it.
        Each is a new message rather than a part added to the last one, so `turn:{n}:messages`
        keeps the steer apart from the tool returns beside it and the transcript draws it with
        nothing taught about it. The wire takes them that way too: both providers accept a request
        arriving as consecutive messages, and an injection with no user text beside it lands as
        system voice however each format carries one.

        An injection is a `SystemPromptPart` because nobody typed it. What it costs the cached prefix
        is nothing, and that is the load-bearing half: appended, it is one more entry at the end,
        where an instruction re-prices every request from the system block onward. How it *reaches*
        the model is the provider's business and varies, so do not write code here that depends on
        which; see `docs/plugins/guidance.md`.

        Both are read from recorded steps and never live, because a live read of the queue is an
        effect and a resumed pass would see a different one. See `Stepping.injected` and
        `Stepping.steering`.
        """
        scope.allow(scope.coming("model"))
        injected = await scope.injected(messages)
        if injected:
            messages.append(ModelRequest(parts=[SystemPromptPart(content=text) for text in injected]))
        steered = await scope.steering()
        if steered:
            messages.append(ModelRequest(parts=[UserPromptPart(content=text) for text in steered]))

    async def request(self, scope: Stepping, messages: list[ModelMessage], tools: Tools) -> ModelResponse:
        """
        The provider's answer to the conversation as it stands, made or replayed by `scope`.

        Instructions travel as `instruction_parts` on the parameters, which is what a model reads,
        and are also written on each `ModelRequest` this loop makes, which is what the transcript
        reads back; the two are one string written twice for two readers, and `told` in
        `conversation.py` is the reader that would notice them drifting.

        `prepare_messages` is the model's own chance to reshape a history for its wire, and is the
        one thing between this loop and `Model.request` that the graph used to do; the settings and
        parameter preparation the graph also did are done again inside every model's `request`, so
        there is nothing else to call.
        """
        parameters = ModelRequestParameters(
            function_tools=[tool.tool_def for tool in tools.by_name.values()],
            instruction_parts=[InstructionPart(content=self.instructions)],
        )
        prepared = self.model.prepare_messages(messages, parameters)
        return await scope.request(self.model, prepared, self.settings or None, parameters)


def said_something(response: ModelResponse) -> bool:
    """Whether the model answered in words, which is what ends a turn nothing keeps going."""
    return any(isinstance(part, TextPart) and bool(part.content) for part in response.parts)


def cut_off_writing_a_call(response: ModelResponse) -> bool:
    """
    Whether the output limit fell in the middle of the last tool call's arguments.

    Only the last part can have been cut, and only a response stopped at the limit can have cut it.
    A call whose arguments still parse is a whole call the limit happened to fall after, and it runs;
    `TestTheModelAndToolLoop` holds the control.
    """
    if response.finish_reason != "length" or not response.parts:
        return False
    last = response.parts[-1]
    if not isinstance(last, ToolCallPart):
        return False
    try:
        last.args_as_dict(raise_if_invalid=True)
    except ValueError, AssertionError:
        return True
    return False
