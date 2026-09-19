from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace

from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import ContentFilterError
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

DEFAULT_RETRIES = 1


@dataclass(slots=True)
class Tools:
    by_name: Mapping[str, ToolsetTool[None]]
    retries: dict[str, int]
    context: RunContext[None]

    @classmethod
    async def from_toolsets(
        cls, model: Model, messages: list[ModelMessage], toolsets: Sequence[AbstractToolset[None]]
    ) -> Tools:
        context = RunContext(deps=None, model=model, usage=RunUsage(), messages=messages, max_retries=DEFAULT_RETRIES)
        by_name: dict[str, ToolsetTool[None]] = {}
        for toolset in toolsets:
            for name, tool in (await toolset.get_tools(context)).items():
                if name in by_name:
                    raise ValueError(f"two tools are named {name!r}")
                by_name[name] = tool
        return cls(by_name=by_name, retries={}, context=context)

    async def call(self, scope: Stepping, call: ToolCallPart) -> ToolReturnPart | ValidationError | ModelRetry:
        tool = self.by_name.get(call.tool_name)
        if tool is None:
            return ModelRetry(f"Unknown tool name: {call.tool_name!r}")

        context = replace(
            self.context,
            messages=self.context.messages,
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
        if isinstance(attempted, ToolReturnPart):
            return attempted
        tool = self.by_name.get(call.tool_name)
        maximum = DEFAULT_RETRIES if tool is None else tool.max_retries
        used = self.retries.get(call.tool_name, 0)
        if used >= maximum:
            raise UnexpectedModelBehavior(
                f"Tool {call.tool_name!r} exceeded max retries count of {maximum}."
            ) from attempted
        self.retries[call.tool_name] = used + 1
        return RetryPromptPart.from_error(attempted, tool_name=call.tool_name, tool_call_id=call.tool_call_id)


async def validated(call: ToolCallPart, tool: ToolsetTool[None], context: RunContext[None]) -> dict[str, object]:
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
        messages = [*history, ModelRequest(parts=[UserPromptPart(content=asked)], instructions=self.instructions)]
        start = len(history)
        tools = await Tools.from_toolsets(self.model, messages, self.toolsets)
        ending = 0
        output_retries = 0
        while True:
            await self.before_request(scope, messages)
            response = await self.request(scope, messages, tools)
            messages.append(response)
            tools.context.messages = messages

            calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
            if calls:
                if len({call.tool_call_id for call in calls}) != len(calls):
                    raise ValueError("a model response contains duplicate tool call ids")
                attempted = await asyncio.gather(*(tools.call(scope, call) for call in calls))
                results = [tools.result(call, result) for call, result in zip(calls, attempted, strict=True)]
                messages.append(ModelRequest(parts=results, instructions=self.instructions))
                continue

            if actionable_text(response):
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
            if output_retries >= DEFAULT_RETRIES:
                raise UnexpectedModelBehavior(f"Model output exceeded max retries count of {DEFAULT_RETRIES}.")
            output_retries += 1
            messages.append(
                ModelRequest(
                    parts=[RetryPromptPart(content="Please return text or call a tool.")],
                    instructions=self.instructions,
                )
            )

    async def before_request(self, scope: Stepping, messages: list[ModelMessage]) -> None:
        scope.allow(scope.coming("model"))
        injected = await scope.injected(messages)
        if injected:
            messages.append(ModelRequest(parts=[SystemPromptPart(content=text) for text in injected]))
        steered = await scope.steering()
        if steered:
            messages.append(ModelRequest(parts=[UserPromptPart(content=text) for text in steered]))

    async def request(self, scope: Stepping, messages: list[ModelMessage], tools: Tools) -> ModelResponse:
        parameters = ModelRequestParameters(
            function_tools=[tool.tool_def for tool in tools.by_name.values()],
            instruction_parts=[InstructionPart(content=self.instructions)],
        )
        prepared = self.model.prepare_messages(messages, parameters)
        return await scope.request(self.model, prepared, self.settings or None, parameters)


type Keeping = Callable[[int, int], Awaitable[Sequence[str]]]
"""What the session's plugins say when the turn tries to end, by attempt and response count."""


def actionable_text(response: ModelResponse) -> bool:
    return any(isinstance(part, TextPart) and bool(part.content) for part in response.parts)
