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
from collections.abc import Sequence
from contextlib import asynccontextmanager
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from decimal import Decimal
from time import monotonic
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities import CapabilityOrdering
from pydantic_ai.capabilities import WrapModelRequestHandler
from pydantic_ai.capabilities.abstract import ValidatedToolArgs
from pydantic_ai.capabilities.abstract import WrapToolExecuteHandler
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import UserPromptPart
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

from mainplate import records
from mainplate.records import StepKind
from mainplate.snapshots import Worktree

ModelResponseTypeAdapter: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)

TOOK = "took"
"""
What a duration is called inside a response's `metadata`.

A `ModelResponse` is a thing this console fills in before recording it and has a slot for exactly
this, so a round trip's duration needs no key of its own and rides into `turn:{n}:model:{i}` and
`turn:{n}:messages` alike. A tool call's duration is a field on `records.Returned` instead, which is
the same word in the record that holds what the call came back with.
"""


def parse_model_response(recorded: object) -> ModelResponse:
    """A recorded model response, back as the type the agent expects to receive."""
    return ModelResponseTypeAdapter.validate_python(records.Response.model_validate(recorded).response)


def parse_tree(recorded: object) -> str | None:
    """
    A recorded tree hash, or nothing at all for a turn taken with no workspace configured.

    Absent reads the same as recorded-with-no-hash, which is what every caller wants: they all reach
    this through `recorded.get(...)`, and a request nobody has made yet and one made with no worktree
    are both drawn as no tree. The two are still *told apart in the store*, which is what the record
    is for - a `Tree` holding nothing says a snapshot was taken and there was nothing to take.
    """
    return None if recorded is None else records.Tree.model_validate(recorded).tree


def parse_took(recorded: object) -> timedelta | None:
    """
    How long one recorded thing took, or nothing where the pass that wrote it could not say.

    Seconds in the store and a `timedelta` out of it, because seconds are what the codec takes and a
    bare number is a unit somebody downstream has to remember. `None` is "nothing timed this", which
    is what a record written by a pass that replayed the work rather than doing it holds.
    """
    if recorded is None:
        return None
    if isinstance(recorded, int | float) and not isinstance(recorded, bool):
        return timedelta(seconds=float(recorded))
    raise TypeError(f"how long something took must be seconds or nothing, not {recorded!r}")


def parse_returned(recorded: object) -> records.Returned:
    """
    What a tool call came back with and how long it took, as the record holding both.

    The return itself is not narrowed, and deliberately cannot be: a toolset is a set of unrelated
    functions with unrelated return types, so there is no one type to validate against the way there
    is for a model response. What a caller receives is the JSON round trip of what the tool returned,
    on the pass that ran it exactly as on the pass that replayed it, which is the same bargain every
    step makes and the reason both passes agree.
    """
    return records.Returned.model_validate(recorded)


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
        return records.Tree(tree=None if worktree is None else await worktree.capture(why)).recorded()

    return capture


class AllowanceSpent(Exception):
    """
    The pass has made as many live model requests as it may, and the turn is not finished.

    Raised from `CheckpointedModel.request` at the point a further request would be made, so it
    unwinds `agent.run` and every node above it. `conversing` catches it outside that call and
    returns, which ends the pass with the turn part-answered and the session owed another one.

    Deliberately **not** a `Suspended`. Nothing is owed by the outside world here - no key is
    unanswered and there is nothing for `arrive` to deliver - so reporting one would tell a driver
    to wait for a write that is never coming. It is an ordinary exception for the same reason: a
    `Suspended` this console caught and carried on from is exactly what `resume`'s `Swallowed` check
    refuses, and that check is what keeps the honest suspensions honest.
    """


@dataclass(slots=True)
class Allowance:
    """
    How many live model requests one pass may make, and how many it has made.

    One per *pass* rather than one per turn, which is why it is threaded in rather than made inside
    `stepping`: a pass that finds two prompts already recorded answers two turns, and the lease
    covers the pass rather than either of them.

    `limit` of `None` is unbounded, which is what a pass was before there was an allowance: it runs
    the whole turn, however many round trips that takes. What the number trades is replay against
    the lease and against how long a steer waits, since a pass reads the checkpoint once: see
    `Settings.allowance`.

    Only *live* requests count. A pass replaying what an earlier one recorded pays no provider and
    takes no time worth bounding, so a resumed pass reaches the same point the last one stopped at
    rather than stopping short of it.
    """

    limit: int | None
    spent: int = 0

    def take(self) -> None:
        """Account for one live request, refusing the one that would go past the allowance."""
        if self.limit is not None and self.spent >= self.limit:
            raise AllowanceSpent(f"this pass has made its {self.limit} live model request(s)")
        self.spent += 1


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


type Draining = Callable[[StepKey], Awaitable[Sequence[str]]]
"""
What the person has said into this turn that no request has carried, taken under the key it records.

A function for the reason `Pricer` is one: what answers it reads the session's inbox, and
`conversation.py` reads `agent.py`, which builds the agent this capability is attached to. Injecting
the one question keeps the capability ignorant of what a message is, which is the same ignorance
that lets one instance serve every session.

It takes the *key* rather than a count of what has already been said, because the record it writes is
a cursor: how far down the inbox this turn has read. Where a count had to be carried on the scope and
kept in step with the store, the cursor is the record, so two drains in one turn need nothing between
them.

One of these rather than the two there were. Reading the queue and shutting it used to be different
acts - the second claimed the next steer slot so a message arriving as the turn ended could not be
written where nothing would read it - and with a queue there is no slot to claim: a message nobody
took is still in the queue, and the next turn opens on it.
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

    Fresh per turn, so nothing it counts survives the scope for another turn to see. Two things on
    it belong to something wider and are handed in rather than made here: `worktree` is the
    session's, and is here because the point where a snapshot may be taken is a model request and
    this is what stands at one; `allowance` is the *pass's*, and is shared by every scope in it,
    since what it bounds is how long one pass runs rather than how much one turn does.
    """

    run: Run
    prefix: str
    worktree: Worktree | None = None
    pricer: Pricer | None = None
    draining: Draining | None = None
    allowance: Allowance = field(default_factory=lambda: Allowance(limit=None))
    taken: Counter[str] = field(default_factory=Counter)
    allowed: set[StepKey] = field(default_factory=set)
    """
    Which requests this pass has already spent an allowance on, so that no request spends two.

    The allowance is checked twice per request and the two are not redundant. `before_model_request`
    checks it *first*, before anything is recorded for a request that is about to be refused; the
    model's own `request` checks it because that is the request. Keyed by the step name, which is the
    request's identity, so whichever runs first is the one that spends.
    """

    def key(self, kind: StepKind) -> StepKey:
        """
        The next key of this kind, numbered by position within the turn.

        `StepKind` and not a bare string, so the word this builds a key from is the same word the
        record written under it tags itself with. The two used to be independent strings written at
        opposite ends of the console with nothing enforcing that they agreed.
        """
        coming = self.coming(kind)
        self.taken[kind] += 1
        return coming

    def coming(self, kind: StepKind) -> StepKey:
        """The key the next step of this kind will take, without taking it."""
        return f"{self.prefix}:{kind}:{self.taken[kind]}"

    def allow(self, key: StepKey) -> None:
        """
        Account for the model request named by `key`, refusing the one past this pass's allowance.

        Only a *live* request counts: a replayed one pays nobody and takes no time worth bounding, so
        a resumed pass gets further than the last rather than stopping where it did. Whether it is
        live is what the key says.

        Idempotent per request, because it is asked twice: once before anything is recorded for the
        request, and once at the request itself. See `allowed`.
        """
        if key in self.allowed or key in self.run.recorded:
            return
        self.allowed.add(key)
        self.allowance.take()

    def identified(self, kind: StepKind, identity: str) -> StepKey:
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

    async def steering(self) -> tuple[str, ...]:
        """
        The steers to put to the model now, taken under a key that records how far this turn has read.

        Reading the inbox is an *effect*, because a person goes on typing between two passes, so what
        makes it replayable is that the take is recorded. That is not a nicety: `turn:{n}:model:{i}`
        is the answer to a question, and a replay that asked a different one would be pairing an
        answer with a prompt nobody ever gave. It is also what keeps the *shape* of the run the same,
        since a read that found something where the first pass found nothing would ask a question the
        first pass never asked.

        The recording is `Run.pending`'s rather than this one's, and the value is a cursor: the key
        of the last entry taken. Which is why nothing is carried on the scope any more - where the
        record was a list of texts, and the next request needed a count of them, it is now a place in
        a queue that the next drain simply reads.

        `heard:{i}` is one per request, in step with `tree:{i}` and `model:{i}`, and there is only
        the one kind now: a drain where the run would have ended could never find anything, since a
        pass reads a snapshot that was fixed before its first request.
        """
        if self.draining is None:
            return ()
        return tuple(await self.draining(self.key("heard")))

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

    def stamp(self, answered: ModelResponse, took: timedelta) -> None:
        """
        Say how long the provider took to answer, on the response and before it is recorded.

        `metadata` is Pydantic AI's own slot for what the application knows and the model is not
        told, and it is the reason this needs no key of its own: a response carries it into
        `turn:{n}:model:{i}` and into `turn:{n}:messages` alike, so both readings of a turn find the
        same figure without either being taught where to look. That is the same bargain `price`
        makes one field along, and it is what keeps a running turn's reading a prefix of the settled
        one.

        Measured across the wrapped model's own call and nothing else, so it is the round trip to
        the provider rather than the pass around it: the snapshot before it and the store write after
        it are this console's time, not the model's.

        Never overwritten, because a duration is a fact about the request that was actually made and
        a resumed pass did not make it. What a replay is handed is what the first pass timed.
        """
        stamped = dict(answered.metadata or {})
        if TOOK in stamped:
            return
        answered.metadata = stamped | {TOOK: took.total_seconds()}


current_stepping: ContextVar[Stepping | None] = ContextVar("mainplate_stepping", default=None)


@contextmanager
def stepping(
    run: Run,
    prefix: str,
    worktree: Worktree | None = None,
    pricer: Pricer | None = None,
    draining: Draining | None = None,
    allowance: Allowance | None = None,
) -> Iterator[Stepping]:
    """
    Make every model request and tool call in this block a step of `run`, named under `prefix`.

    A context variable rather than an argument because the hooks that read it
    (`StepwiseDurability.wrap_model_request` and `wrap_tool_execute`) are called by Pydantic AI,
    not by us: there is no parameter anywhere between here and there to thread a checkpoint
    through. It is the same place DBOS reads its workflow id from, and the same place Pydantic AI
    keeps its own ambient run context.

    No allowance is an unbounded one, which is what a block outside a worker wants: a script or a
    test driving one `agent.run` has no driver to hand the rest of the turn to, so a pass that cut
    itself short there would simply leave the turn unfinished.
    """
    scope = Stepping(
        run=run,
        prefix=prefix,
        worktree=worktree,
        pricer=pricer,
        draining=draining,
        allowance=allowance if allowance is not None else Allowance(limit=None),
    )
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

        It is priced and timed on the way past for the same reason it is recorded at all: see
        `Stepping.price` and `Stepping.stamp`, both of which have to run here because everything
        further out happens after the record is written.

        The worktree is snapshotted first, because this is the moment it is worth snapshotting:
        no tool is running, so the tree is a coherent thing to read, and what is recorded is the
        state the model is about to be asked to reason about. A pass that replays this request
        replays the snapshot too and runs no git, so the pair stay in step whatever happens
        between them.

        The allowance is spent *before* any of that, so a request that was never made leaves no tree
        recorded in front of it. It is ordinarily spent earlier still, in `before_model_request`,
        because that runs before this and records a cursor of its own; see `Stepping.allowed`.
        """
        key = self.scope.key("model")
        self.scope.allow(key)
        await self.scope.snapshot()

        async def ask() -> object:
            started = monotonic()
            answered = await self.wrapped.request(messages, model_settings, model_request_parameters)
            self.scope.stamp(answered, timedelta(seconds=monotonic() - started))
            self.scope.price(answered)
            return records.Response(response=ModelResponseTypeAdapter.dump_python(answered, mode="json")).recorded()

        return await self.scope.step(key, ask, parse_model_response)

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

    async def before_model_request(
        self, ctx: RunContext[AgentDepsT], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """
        Put anything the person has said mid-turn to the model, in *this* request.

        Appended to `request_context.messages`, and emphatically not `ctx.enqueue`, which was tried
        and delivered every steer one round trip late. Pydantic AI's own drain capability is ordered
        **outermost**, so it empties the queue in its `before_model_request` before this one runs: a
        message enqueued here misses the request it was read for and reaches the next one, which
        costs a round trip nobody asked for, puts the steer's panel below the answer it was meant to
        shape, and makes `turn:{n}:heard:{i}` a claim about a request that never heard it.

        Appending is sound for the two reasons the enqueue was reached for. The list is a *copy* of
        the run's history and what this returns is adopted whole (`ctx.state.message_history[:] =
        messages`), so the steer lands in `turn:{n}:messages` and the transcript draws it with
        nothing taught about it; and a new message is added rather than an existing one mutated,
        which is the thing the docs actually forbid. Pydantic AI merges consecutive trailing requests
        for the wire with the tool parts first, so a steer travelling beside a batch of results
        arrives after them in one request and is recorded as its own message.

        What it is *told* comes from a recorded step, because a live read of the queue is an effect
        and a resumed pass would see a different one. See `Stepping.steering`.

        **The allowance is spent here rather than at the request**, and that is not tidying: the
        drain below records how far this turn has read, so a pass that recorded one and *then*
        refused the request would leave a cursor for a request nobody made. The next pass replays
        that cursor, so a message delivered while the refused request was being decided waits a
        further round trip - or, if the turn ends first, opens a turn of its own. Refusing before
        the drain is what makes a message reach the very next request the pass after this one makes.
        """
        scope = current_stepping.get()
        if scope is None:
            return request_context
        scope.allow(scope.coming("model"))
        if scope.draining is None:
            return request_context
        said = await scope.steering()
        if said:
            request_context.messages.append(ModelRequest(parts=[UserPromptPart(content=text) for text in said]))
        return request_context

    # There was an `after_node_run` here, and the inbox is what deleted it. It drained again where a
    # run would otherwise have ended, so that a message arriving during the last response could
    # redirect the run into one more request rather than being answered by nobody. Two things it
    # answered are now answered better. A message can no longer be lost at the end of a turn, because
    # there is no slot for a pass to shut - what nobody took is still in the queue. And nothing can
    # arrive *during* a pass at all: a pass reads its own snapshot, fixed the moment it started, so
    # the drain before the first request already sees everything this pass ever will. What used to
    # cost the ending turn an extra round trip now opens the turn after it.

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

        **How long it took is a field of the same record**, written by the same step. It was a
        `turn:{n}:took:{id}` of its own for as long as a tool return was stored bare, because a
        duration beside somebody else's value would have been indistinguishable from a tool that
        happened to return a field of that name. An envelope removes that objection, and with it the
        window where a return was recorded and its duration was not.

        Timed around the handler alone, so what is recorded is the call rather than the store write
        after it. A tool that raises records nothing at all - the `ModelRetry` propagates out of the
        step and the call stays out until a retry lands - so a failed call has no duration for the
        same reason it has no return.
        """
        scope = current_stepping.get()
        if scope is None:
            return await handler(args)

        async def perform() -> object:
            started = monotonic()
            came_back = to_jsonable_python(await handler(args))
            return records.Returned(returned=came_back, took=timedelta(seconds=monotonic() - started)).recorded()

        recorded = await scope.step(scope.identified("tool", call.tool_call_id), perform, parse_returned)
        return recorded.returned
