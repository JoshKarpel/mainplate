# Durability as a Pydantic AI capability, over `without-durability`'s stepwise mechanism.
#
# The shape is the one the bundled Temporal, DBOS, and Prefect capabilities use: a capability
# whose `wrap_model_request` swaps a wrapper model in for the request's, so every model call the
# agent makes goes through the engine's unit of durable work instead of straight to the provider.
# Here that unit is `Run.step`, so a model response is written to the checkpoint before the agent
# proceeds on it, and a pass that re-runs the same conversation is handed the recorded response
# rather than paying for a second one.
#
# It is built on `AbstractCapability` and `WrapperModel`, the surface Pydantic AI documents for
# third-party integrations, rather than on `durable_exec._base`, whose module docstring reserves
# it for the three bundled engines. Almost everything that base class carries is about crossing a
# *serialization* boundary: a `Model` cannot be pickled into a Temporal activity, so a request
# carries a `model_id` string and the worker rebuilds the model on the far side. There is no such
# boundary here. `without-durability` runs the workflow body in this process, and only a step's
# *result* is ever encoded, so the model instance is simply in scope and none of that machinery
# has anything to do.
#
# What the capability cannot get from Pydantic AI is which checkpoint it is writing to, because
# no hook carries one. That arrives through a context variable the conversation sets around its
# `agent.run(...)`, which is the same mechanism DBOS reads (`DBOS.workflow_id`) and Pydantic AI
# uses for its own ambient run context. Outside such a scope the capability is transparent and
# the agent is an ordinary, non-durable agent.

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import asynccontextmanager
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities import CapabilityOrdering
from pydantic_ai.capabilities import WrapModelRequestHandler
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.tools import RunContext
from without_durability.stepwise import Parse
from without_durability.stepwise import Run
from without_durability.stepwise import StepKey

ModelResponseTypeAdapter: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)


def parse_model_response(recorded: object) -> ModelResponse:
    """A recorded model response, back as the type the agent expects to receive."""
    return ModelResponseTypeAdapter.validate_python(recorded)


class StreamingNotRecorded(NotImplementedError):
    """
    A streamed model request was made inside a checkpointed scope, which records nothing.

    Loud rather than transparent, and that is the whole reason this exists. `WrapperModel`
    delegates `request_stream` to the model it wraps, so an unimplemented streaming path is not
    a missing feature but a model call that happens for real and leaves no record: a crash after
    it re-runs it, and a resumed conversation pays for it twice. Refusing says so at the call.

    Closing it means recording the completed response *and* the events the stream produced, then
    handing both back as a `CompletedStreamedResponse` so the agent replays them. Nothing here
    needs it yet: the console drives `agent.run`, which asks for a whole response.
    """


@dataclass(slots=True)
class Stepping:
    """
    The checkpoint a model request inside this scope writes to, and the name it writes under.

    `prefix` is the conversation turn, and `taken` numbers the requests within it, so the *n*th
    model request of turn 3 is `turn:3:model:n` on this pass and on every later one. Positional
    numbering is what makes a key stable across passes without anybody naming each request, and
    it is why the counter is a place rather than a value: the position is state that advances as
    the pass runs. It carries the same determinism requirement the mechanism already states,
    since a pass that issues its requests in a different order finds the wrong records.

    Fresh per turn, so nothing survives the scope for another one to see.
    """

    run: Run
    prefix: str
    taken: Counter[str] = field(default_factory=Counter)

    def key(self, kind: str) -> StepKey:
        nth = self.taken[kind]
        self.taken[kind] += 1
        return f"{self.prefix}:{kind}:{nth}"

    async def step[T](self, kind: str, effect: Callable[[], Awaitable[object]], parse: Parse[T]) -> T:
        return await self.run.step(self.key(kind), effect, parse)


current_stepping: ContextVar[Stepping | None] = ContextVar("mainplate_stepping", default=None)


@contextmanager
def stepping(run: Run, prefix: str) -> Iterator[Stepping]:
    """
    Make every model request in this block a step of `run`, named under `prefix`.

    A context variable rather than an argument because the hook that reads it
    (`StepwiseDurability.wrap_model_request`) is called by Pydantic AI, not by us: there is no
    parameter anywhere between here and there to thread a checkpoint through. It is the same
    place DBOS reads its workflow id from, and the same place Pydantic AI keeps its own ambient
    run context.
    """
    scope = Stepping(run=run, prefix=prefix)
    token = current_stepping.set(scope)
    try:
        yield scope
    finally:
        current_stepping.reset(token)


class CheckpointedModel(WrapperModel):
    """
    A model whose every request is a recorded step of one workflow pass.

    Swapped in for the request's model by `StepwiseDurability`, so the agent, the toolsets, and
    every other capability are untouched: what changes is only where the request goes.
    """

    def __init__(self, wrapped: Model, *, scope: Stepping) -> None:
        super().__init__(wrapped)
        self.scope = scope

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """
        The provider's answer, asked for once across every pass at this conversation.

        The step records what the *codec* will take, so the response is lowered to JSON here
        rather than handed over as a dataclass: `JsonCodec` is the standard library's `json`, and
        a `ModelResponse` is not something it can encode. `parse_model_response` is the other
        half, and it is required rather than a convenience, since what comes back out of the
        store is an `object` on the pass that ran the request as much as on the one that
        resumed it.
        """

        async def ask() -> object:
            answered = await self.wrapped.request(messages, model_settings, model_request_parameters)
            return ModelResponseTypeAdapter.dump_python(answered, mode="json")

        return await self.scope.step("model", ask, parse_model_response)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        raise StreamingNotRecorded(
            "a streamed model request inside a checkpointed conversation would not be recorded; "
            "drive the agent with `agent.run` until this records the stream's events too"
        )
        # Unreachable, and here so that this is a generator: `asynccontextmanager` takes one, and
        # a method that raised without being one would fail at the decorator rather than at the
        # call. The pinned code is the assertion: implement the stream and mypy fails this line.
        yield  # type: ignore[unreachable]


class StepwiseDurability(AbstractCapability[AgentDepsT]):
    """
    Route an agent's model requests through the checkpoint of the conversation running it.

    Attach it once, at construction:

    ```python
    agent = Agent("anthropic:claude-sonnet-5", name="mainplate", capabilities=[StepwiseDurability()])
    ```

    Inside a `stepping(run, prefix)` block the agent's requests become recorded steps of `run`;
    outside one the capability does nothing at all and the agent behaves normally. That split is
    deliberate and matches the bundled engines: a durable-capable agent stays usable in a script,
    a test, or a one-off call with no workflow around it.

    It holds no state, so one instance serves every conversation: what varies is the checkpoint,
    and that arrives through the context variable rather than through the capability.
    """

    def get_ordering(self) -> CapabilityOrdering:
        """
        Innermost, so every other capability's contribution is already applied when the step runs.

        A step records what the model was actually asked, so anything that edits the request has
        to have edited it by then; recorded from further out, a later capability's change would
        be absent from the record and present in the live call.
        """
        return CapabilityOrdering(position="innermost")

    @classmethod
    def get_serialization_name(cls) -> str | None:
        """
        Not loadable from an agent spec, because a spec cannot carry what makes this work.

        The checkpoint is supplied at run time by the conversation, not at construction, so an
        agent built from a spec with this attached would look durable and record nothing.
        """
        return None

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentDepsT],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        scope = current_stepping.get()
        if scope is None:
            return await handler(request_context)
        request_context.model = CheckpointedModel(request_context.model, scope=scope)
        return await handler(request_context)
