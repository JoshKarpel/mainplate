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
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities import CapabilityOrdering
from pydantic_ai.capabilities import WrapModelRequestHandler
from pydantic_ai.capabilities.abstract import ValidatedToolArgs
from pydantic_ai.capabilities.abstract import WrapToolExecuteHandler
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.tools import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage
from pydantic_core import to_jsonable_python
from without_durability.stepwise import Parse
from without_durability.stepwise import Run
from without_durability.stepwise import StepKey

from mainplate.snapshots import Worktree

ModelResponseTypeAdapter: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)


def parse_model_response(recorded: object) -> ModelResponse:
    """A recorded model response, back as the type the agent expects to receive."""
    return ModelResponseTypeAdapter.validate_python(recorded)


def parse_tree(recorded: object) -> str | None:
    """A recorded tree hash, or nothing at all for a turn taken with no workspace configured."""
    if recorded is None or isinstance(recorded, str):
        return recorded
    raise TypeError(f"a tree must be a hash or nothing, not {recorded!r}")


def parse_returned(recorded: object) -> object:
    """
    What a tool call came back with, which is whatever the codec held onto.

    No narrowing, and deliberately none available: a toolset is a set of unrelated functions with
    unrelated return types, so there is no one type to validate against the way there is for a
    model response. What a caller receives is the JSON round trip of what the tool returned, on the
    pass that ran it exactly as on the pass that replayed it, which is the same bargain every step
    makes and the reason both passes agree.
    """
    return recorded


def snapshotting(worktree: Worktree | None, why: str) -> Callable[[], Awaitable[object]]:
    """
    What the worktree looked like at one model request, as the effect `Run.step` takes.

    A step rather than a plain read, and that is the rule the mechanism asks for rather than a
    preference: reading a worktree returns a different answer every time it is asked, so a pass
    that re-read it would resume a conversation against a directory that has moved since. Recorded
    once, every later pass is handed the hash the first one saw and runs no git at all.

    No worktree records `None` rather than nothing at all, so a turn taken before one was
    configured is distinguishable from a turn nobody has reached yet.
    """

    async def capture() -> object:
        return None if worktree is None else await worktree.capture(why)

    return capture


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


type Pricer = Callable[[RequestUsage], Decimal | None]
"""
What one model request came to, in US dollars, asked of whatever knows the rates.

A function rather than the thing that answers it, and that is a ring rather than a preference: what
prices a model is `reference.py`, which reads `agent.py`, which builds the agent this capability is
attached to. Injecting the one question this module actually has keeps it ignorant of endpoints,
catalogues and databases, which is the same ignorance that lets one capability serve every session.

`None` is "nothing here knows", never a free request, so an unpriced model records no cost rather
than a zero somebody would read as having been given something for nothing.
"""


@dataclass(slots=True)
class Stepping:
    """
    The checkpoint everything inside this scope writes to, and the names it writes under.

    `prefix` is the conversation turn, and `taken` numbers the requests within it, so the *n*th
    model request of turn 3 is `turn:3:model:n` on this pass and on every later one. Positional
    numbering is what makes a key stable across passes without anybody naming each request, and
    it is why the counter is a place rather than a value: the position is state that advances as
    the pass runs. It carries the same determinism requirement the mechanism already states,
    since a pass that issues its requests in a different order finds the wrong records.

    **That numbering is only sound where the order is fixed, which for tool calls it is not.** A
    model can ask for several tools in one response and Pydantic AI runs them at once, so which
    one reaches its step first is a race and a positional key would hand a pass somebody else's
    record. `identified` is what those use instead: a call already carries an id, that id is part
    of the recorded model response, and a replay is handed the same response, so it is stable
    across passes for free where a counter is not.

    Fresh per turn, so nothing survives the scope for another one to see. `worktree` is the one
    thing on it that belongs to the session rather than the turn, and it is here because the point
    where a snapshot may be taken is a model request and this is what stands at one.
    """

    run: Run
    prefix: str
    worktree: Worktree | None = None
    pricer: Pricer | None = None
    taken: Counter[str] = field(default_factory=Counter)

    def key(self, kind: str) -> StepKey:
        """The next key of this kind, numbered by position within the turn."""
        nth = self.taken[kind]
        self.taken[kind] += 1
        return f"{self.prefix}:{kind}:{nth}"

    def identified(self, kind: str, identity: str) -> StepKey:
        """A key named by something already stable, for steps whose order is not fixed."""
        return f"{self.prefix}:{kind}:{identity}"

    async def step[T](self, key: StepKey, effect: Callable[[], Awaitable[object]], parse: Parse[T]) -> T:
        return await self.run.step(key, effect, parse)

    async def snapshot(self) -> str | None:
        """
        Record what the worktree holds right now, at a point where nothing is writing to it.

        Called from `CheckpointedModel.request`, which is the only place that can honestly call it.
        A model request is the boundary at which every tool of the previous batch has returned by
        construction, and it is the only such boundary inside a turn: capture after each tool call
        instead and `git add -A` walks a tree the *other* calls in that batch are still writing to,
        recording a mixture of states that never existed together.

        The step key doubles as the commit message, so a snapshot in the object store says which
        request of which turn it was taken before without a second naming scheme to keep in step.
        """
        key = self.key("tree")
        return await self.step(key, snapshotting(self.worktree, key), parse_tree)

    def price(self, answered: ModelResponse) -> None:
        """
        Fill in what this request cost, **before** it is recorded rather than after.

        Pydantic AI fills `usage.cost` too, from `genai-prices`, but it does so in the agent graph,
        which is outside the step that records the response. So the cost of a turn lands in
        `turn:{n}:messages` and never in `turn:{n}:model:{i}`, and a turn being watched has no cost
        at all until the instant it ends. Written here it is in both, and the reading of a turn in
        flight stays the prefix of the settled reading that the console depends on it being.

        Recorded rather than looked up when a page is drawn, because what a turn cost is settled the
        moment the request is answered and nothing will ever rewrite it, where the rates behind it
        are configuration that moves. Priced again next month the same turn would show a different
        number, and two sessions would stop being comparable. It is the fork's bargain rather than
        the catalogue's: a value that happens to have been true, not a view of something that changes.

        What it is not is authoritative. Nothing on either wire reports what was actually charged, so
        this is an estimate made immutable rather than a bill. Never overwriting an existing cost is
        what leaves room for that to improve: a wire that one day says what it took wins over any
        estimate of it, exactly as Pydantic AI's own filling is written to allow.
        """
        if self.pricer is None or answered.usage.cost is not None:
            return
        answered.usage.cost = self.pricer(answered.usage)


current_stepping: ContextVar[Stepping | None] = ContextVar("mainplate_stepping", default=None)


@contextmanager
def stepping(
    run: Run, prefix: str, worktree: Worktree | None = None, pricer: Pricer | None = None
) -> Iterator[Stepping]:
    """
    Make every model request and tool call in this block a step of `run`, named under `prefix`.

    A context variable rather than an argument because the hooks that read it
    (`StepwiseDurability.wrap_model_request` and `wrap_tool_execute`) are called by Pydantic AI,
    not by us: there is no parameter anywhere between here and there to thread a checkpoint
    through. It is the same place DBOS reads its workflow id from, and the same place Pydantic AI
    keeps its own ambient run context.
    """
    scope = Stepping(run=run, prefix=prefix, worktree=worktree, pricer=pricer)
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

        It is priced on the way past for the same reason it is recorded at all: see `Stepping.price`,
        which has to run here because everything further out happens after the record is written.

        The worktree is snapshotted first, because this is the moment it is worth snapshotting:
        no tool is running, so the tree is a coherent thing to read, and what is recorded is the
        state the model is about to be asked to reason about. A pass that replays this request
        replays the snapshot too and runs no git, so the pair stay in step whatever happens
        between them.
        """
        await self.scope.snapshot()

        async def ask() -> object:
            answered = await self.wrapped.request(messages, model_settings, model_request_parameters)
            self.scope.price(answered)
            return ModelResponseTypeAdapter.dump_python(answered, mode="json")

        return await self.scope.step(self.scope.key("model"), ask, parse_model_response)

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

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentDepsT],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> object:
        """
        Run a tool once across every pass of this conversation, recording what it came back with.

        Required rather than an optimisation, and for both halves of what a tool does. A tool that
        *reads* returns a different answer every time it is asked, so a pass that re-ran one would
        resume the conversation against a file that has moved since the model was told what it
        said. A tool that *writes* has already written: running it again would either repeat the
        effect or, here, fail against anchors its own first run invalidated, which is a refusal
        for an edit that actually succeeded.

        Keyed by the call's own id rather than by position, because a model may ask for several
        tools in one response and they run concurrently: which reaches this first is a race, so a
        counter would hand a pass another call's record. The id is part of the model response this
        conversation recorded, so a replay is handed the same one.

        This is `step` and not `transact`, so it is at-least-once: a crash between the tool
        returning and the record landing re-runs it on the next pass. That window is one store
        round trip, and the failure it produces is the mild one, because an anchored edit whose
        anchors no longer resolve is refused rather than applied somewhere wrong.
        """
        scope = current_stepping.get()
        if scope is None:
            return await handler(args)

        async def perform() -> object:
            return to_jsonable_python(await handler(args))

        return await scope.step(scope.identified("tool", call.tool_call_id), perform, parse_returned)
