# The durable effects in mainplate's model-and-tool loop, over `without-durability`'s stepwise
# mechanism.
#
# The loop in `loop.py` owns its order explicitly. Before each model request it records the inbox
# cursor and plugin injections here, then `Stepping.request` records the response before the loop
# acts on it. Tool calls come back through `Stepping.call`, keyed by the call id the recorded response
# supplied. A later pass drives the same loop over those records and reaches the first effect that has
# not happened without paying for or repeating anything before it.
#
# Effects live inside `Run.step`; everything around them must be deterministic. A model response and
# a tool return are lowered to JSON before the write and parsed on the way out, on the pass that made
# them as much as on a replay. Tool calls remain at-least-once across the window between the effect
# returning and its record landing.

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from time import monotonic
from typing import Final

from pydantic import TypeAdapter
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturn
from pydantic_ai.models import Model
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import ToolsetTool
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


def unwrapped(came_back: object) -> tuple[object, object]:
    """
    What a tool returned as the two halves the record keeps: for the model, and beside it for the page.

    A plain return is the whole of the first half and none of the second. A `ToolReturn` is split,
    and one carrying what this loop cannot honour is refused; see `Stepping.call`.
    """
    if not isinstance(came_back, ToolReturn):
        return came_back, None
    if came_back.content is not None or came_back.tools is not None:
        raise TypeError("a ToolReturn here may carry a return_value and metadata, and nothing else")
    return came_back.return_value, came_back.metadata


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


def parse_injected(recorded: object) -> tuple[str, ...]:
    """
    What was appended to one model request on a plugin's behalf, as the record holding it.

    Read as well as written, which is the whole reason it is recorded: a resumed pass replays this
    rather than asking the plugin again, so a request that was answered with one sentence in front of
    it is re-made with the same sentence in front of it.
    """
    return records.Injected.model_validate(recorded).said


async def as_recorded(record: records.Record) -> object:
    """
    A record already in hand, as the effect `Run.step` takes.

    Every other step here wraps work that has yet to happen, so its effect is where the work goes. A
    refusal is the one whose value is known before the step is taken, because the thing it records is
    the exception being handled.
    """
    return record.recorded()


def parse_refused(recorded: object) -> records.Refused:
    """Why a model request will never be accepted, as the record the pass that hit it wrote."""
    return records.Refused.model_validate(recorded)


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


class RequestRefused(Exception):
    """
    The provider will not accept this request, and will not accept it on a later pass either.

    Raised from `Stepping.request` after the refusal has been recorded, so it unwinds
    the model loop the way `AllowanceSpent` does and `conversing` catches it outside the run. What it
    reports is not the same thing, though: an allowance spent means the session is owed another pass
    at once, and this means the session is owed nothing, because another pass would ask the identical
    question and get the identical answer.

    **The bug it exists to close is a retry loop nobody can see.** `without-durability`'s worker
    leaves a delivery unanswered when a pass raises, deliberately, since it cannot tell a workflow's
    own failure from a store that was briefly unreachable - and its own docstring says the cost out
    loud: "a workflow that fails on every pass is retried for as long as it keeps failing, once per
    lease, and nothing here backs that off or gives up. A deployment that needs a limit keeps the
    count where it keeps everything else it needs to survive a crash, which is the checkpoint." This
    is that count, in the only form a refusal needs: written down once, and never asked again.

    Terminal is decided by `terminally`, and the split is between an answer that will not change and
    one that might. A 400 for a prompt over the window is arithmetic on values already recorded; a
    429 or a 503 is a fact about the next few seconds.
    """


def terminally(error: Exception) -> records.Refused | None:
    """
    What to record about an error that will never come out differently, or nothing for one that may.

    **The default is transient**, which is the safe way round: a transient error read as terminal
    stalls a session that would have recovered on its own, where a terminal one read as transient
    costs a redelivery per lease until somebody looks. Only what is positively known to be settled is
    named here.

    A 4xx is the provider saying the request itself is wrong, which no amount of asking again fixes:
    a prompt over the context window, a model the endpoint will not route, a malformed body, a
    credential it will not accept. The exceptions are the 4xx codes that are about *now* rather than
    about the request - `408` timed out, `409` collided, `425` was too early, `429` was too fast -
    and every one of those is what a redelivery is for.

    A 5xx is never terminal. It is the provider saying it failed, which is the case a retry answers.
    """
    if not isinstance(error, ModelHTTPError):
        return None
    if not (400 <= error.status_code < 500) or error.status_code in RETRYABLE:
        return None
    return records.Refused(why=str(error), status=error.status_code)


RETRYABLE: Final = frozenset({408, 409, 425, 429})
"""The 4xx codes that describe the moment rather than the request, so asking again is the answer."""


class RequestDeferred(Exception):
    """
    The provider will not take this request now, and said when it will.

    Raised from `CheckpointedModel.request` the way `RequestRefused` and `AllowanceSpent` are, so it
    unwinds `agent.run` and is caught by `conversing` outside it. What it reports is the third
    answer those two leave room for: the session is owed another pass, and not until `until`.

    **It exists because the provider knows something the worker does not.** A pass that raises is
    redelivered when its lease elapses, which for a subscription that resets next Tuesday is one real
    request to a provider already saying no, once a minute, for days. The moment is the whole value
    here, so an error that names none is left exactly as it was: raised, and retried on the lease.

    Deliberately not a `Refused`: nothing about the request is wrong, and recording one would stop
    the session for good over a limit that lifts by itself.
    """

    def __init__(self, until: datetime, why: str) -> None:
        self.until = until
        self.why = why
        super().__init__(f"the provider deferred this request until {until.isoformat()}: {why}")


def deferred_until(error: Exception, now: datetime) -> datetime | None:
    """
    The moment a provider named for coming back, or nothing where it named none worth waiting for.

    Two ways a provider says it, and both are read because they are one statement in two spellings.
    `Retry-After` is the standard header and Pydantic AI already parses either of its forms; a
    subscription's usage limit arrives in the body instead, as `resets_at` in seconds since the
    epoch, which is what an OpenAI plan sends when its allowance is spent.

    **Only ahead of now**, which is the parse rather than a nicety: a moment already past is not a
    wait, and honouring one would schedule a wakeup for the past, get an immediate redelivery, and
    ask the provider the same question as fast as the queue can turn it around. Behind, or absent,
    the answer is nothing at all and the error is raised exactly as it was.

    **Each spelling is held against that test on its own**, and the first one that passes is the
    answer. Tried in turn, a body echoing the window that has just closed - or a clock a few seconds
    apart from the provider's - takes the body's moment, fails the test, and throws away a perfectly
    good header on the same response, which puts the session back on the worker's redelivery that
    this exists to get it off.

    The body is read as a mapping and nothing more is assumed about it. A provider that sends a
    number where this expects one is honoured; anything else is an error with no moment in it, which
    is the common case and the safe one.

    **A `429` and nothing else**, which is a narrower door than `Retry-After` opens and is deliberate.
    `terminally` calls every 5xx transient, so without this they arrive here too, and a gateway
    answering `503` with a day in its header would park the session for a day. The difference is
    whether the provider *knows*: a rate or usage limit is a window it is keeping itself, so the
    moment it names is a fact, where a 5xx is a guess about when something it is not in control of
    will be fixed. The cost of believing the first is a wait that was going to happen anyway; the
    cost of believing the second is turning a blip into a day of silence, at a moment when the
    ordinary redelivery would have got an answer on its next attempt.

    The cost, stated: **a provider that shed load through a `503` with an honest `Retry-After` is
    asked again on the lease instead**, which is a handful of requests it did not want, against the
    console's own worst case being an hour rather than however long somebody else's header said.
    """
    if not isinstance(error, ModelHTTPError) or error.status_code != 429:
        return None
    for named in (resets_at(error.body), retry_after(error, now)):
        if named is not None and named > now:
            return named
    return None


def resets_at(body: object) -> datetime | None:
    """
    When the plan's allowance comes back, as a subscription's own `429` body says it.

    `{"type": "usage_limit_reached", ..., "resets_at": 1790303879, "resets_in_seconds": 398418}` is
    the shape, and the moment is read rather than the duration for `Run.sleep`'s reason: a deadline
    survives the pass that heard it, where seconds from a moment nobody recorded do not.
    """
    if not isinstance(body, Mapping):
        return None
    said = body.get("resets_at")
    if not isinstance(said, int | float) or isinstance(said, bool):
        return None
    try:
        return datetime.fromtimestamp(float(said), UTC)
    except OverflowError, OSError, ValueError:
        return None


def retry_after(error: ModelHTTPError, now: datetime) -> datetime | None:
    """
    When the provider's own `Retry-After` header says to come back, as a moment rather than a wait.

    Pydantic AI parses both of the header's forms into seconds from now, so this is that answer put
    back on the clock: what is recorded and scheduled here is a deadline, and a duration would be one
    more place for "from when" to be got wrong.
    """
    seconds = error.retry_after
    return None if seconds is None else now + timedelta(seconds=seconds)


class AllowanceSpent(Exception):
    """
    The pass has made as many live model requests as it may, and the turn is not finished.

    Raised before `Stepping.request` at the point a further request would be made, so it
    unwinds the model loop. `conversing` catches it outside that call and
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
    `stepping`: a pass that finds two prompts already recorded answers two turns, and the budget
    covers the pass rather than either of them.

    `limit` of `None` is unbounded, which is what a pass was before there was an allowance: it runs
    the whole turn, however many round trips that takes. What the number trades is replay against
    the pass budget and against how long a steer waits, since a pass reads the checkpoint once: see
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


type Draining = Callable[[StepKey], Awaitable[Sequence[str]]]
"""
What the person has said into this turn that no request has carried, taken under the key it records.

A function for the reason `Pricer` is one: what answers it reads the session's inbox, and
`conversation.py` reads `agent.py`, which builds the loop this records. Injecting the one question
keeps this module ignorant of what a message is.

It takes the *key* rather than a count of what has already been said, because the record it writes is
a cursor: how far down the inbox this turn has read. Where a count had to be carried on the scope and
kept in step with the store, the cursor is the record, so two drains in one turn need nothing between
them.

One of these rather than the two there were. Reading the queue and shutting it used to be different
acts - the second claimed the next steer slot so a message arriving as the turn ended could not be
written where nothing would read it - and with a queue there is no slot to claim: a message nobody
took is still in the queue, and the next turn opens on it.
"""


type Injecting = Callable[[StepKey, Sequence[ModelMessage]], Awaitable[Sequence[str]]]
"""
What a session's plugins want appended to the request about to go out, taken under the key that
records it.

A function for the reason `Pricer` and `Draining` are: what answers it runs somebody else's script,
and injecting the one question keeps this module ignorant of what a plugin is and of where a
session's files are.

It is handed the messages because that is what a plugin decides on: which paths the model reached
for, and whether it has already been handed what covers them. The history is the ledger, so a
`forget` re-delivers and a replay does not.

**It takes a key, and that is the difference from what it replaced.** `guiding` was a pure function
of the history it was handed, so a replay recomputed the same answer and nothing had to be written
down. A plugin cannot be trusted to be pure, so what was injected is recorded and a resumed pass
replays it rather than asking again.
"""

type Gating = Callable[[str, Mapping[str, object]], Awaitable[str | None]]
"""
Whether a tool call the model just made may run, asked of the session's plugins before it does.

The call as the model wrote it goes in; what comes back is what to hand the model in the call's
place, or nothing where the call may go ahead. A function for `Injecting`'s reason, and it takes no
key because it needs none: it is asked *inside* the step that records the call, so what it answered
is in that record and a resumed pass replays the refusal as it would replay the return.
"""


type Pricer = Callable[[RequestUsage], Decimal | None]
"""
What one model request came to, in US dollars, asked of whatever knows the rates.

A function rather than the thing that answers it, and that is a ring rather than a preference: what
prices a model is `reference.py`, which reads `agent.py`, which builds the loop this records.
Injecting the one question this module actually has keeps it ignorant of endpoints, catalogues and
databases.

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
    injecting: Injecting | None = None
    gating: Gating | None = None
    allowance: Allowance = field(default_factory=lambda: Allowance(limit=None))

    halted: bool = False
    """
    Whether a plugin has asked for this turn to end, which the loop carrying it reads after each step.

    Here rather than anywhere wider because this scope is already fresh per turn, which is the
    lifetime the answer has: a handoff ending turn 6 must not also end the turn that opens on its
    document, and a scope that cannot outlive its turn cannot get that wrong.

    Set from the *record* of each call rather than from what a plugin said, so it is set the same way
    on the pass that asked and on every pass that replays. It only ever goes from false to true: two
    calls in one response can both end the turn, and a later one that did not must not take it back.
    """

    taken: Counter[str] = field(default_factory=Counter)
    allowed: set[StepKey] = field(default_factory=set)
    """
    Which requests this pass has already spent an allowance on, so that no request spends two.

    The allowance is checked twice per request and the two are not redundant. `Agent.before_request`
    checks it *first*, before anything is recorded for a request that is about to be refused;
    `Stepping.request` checks it because that is the request. Keyed by the step name, which is the
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

    def at(self, kind: StepKind) -> int:
        """
        Which position the next step of this kind will take, for a second key that must ride beside it.

        A refusal is named after the *request* it refused rather than after its own position, so that
        a pass which reaches further than the last one records it under the request that actually
        failed. Two counters would drift the moment a turn refused anywhere but its first request.
        """
        return self.taken[kind]

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

        Called from `Stepping.request`, which is the only place that can honestly call it.
        A model request is the boundary at which every tool of the previous batch has returned by
        construction, and it is the only such boundary inside a turn: capture after each tool call
        instead and `git add -A` walks a tree the *other* calls in that batch are still writing to,
        recording a mixture of states that never existed together.

        The step key doubles as the commit message, so a snapshot in the object store says which
        request of which turn it was taken before without a second naming scheme to keep in step.
        """
        key = self.key("tree")
        return await self.step(key, snapshotting(self.worktree, key), parse_tree)

    def refused(self, at: int) -> records.Refused | None:
        """
        Why request `at` of this turn was refused before, where a pass has already been refused it.

        Read straight off the snapshot rather than through `step`, because what it decides is whether
        to take a step at all. A replay that asked again would be putting an identical question to
        the provider - same recorded history, same recorded message - and paying for the identical
        refusal, once per pass, for as long as anybody keeps the session queued.
        """
        said = self.run.recorded.get(self.identified("refused", str(at)))
        return None if said is None else parse_refused(said)

    async def refuse(self, at: int, refused: records.Refused) -> None:
        """
        Write down that request `at` of this turn will never be accepted, under that request's own key.

        Keyed by the request's position, so that the pass which replays this turn reaches the same
        request and finds the answer already under the key it is about to use.
        """
        await self.step(self.identified("refused", str(at)), lambda: as_recorded(refused), parse_refused)

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

    async def injected(self, messages: Sequence[ModelMessage]) -> tuple[str, ...]:
        """
        What the session's plugins want appended to this request, under the key that records it.

        `injected:{i}` is one per request, in step with `tree:{i}`, `heard:{i}` and `model:{i}`, and
        it is a step for the reason the drain is: a plugin is somebody else's program, so asking it
        again on a resumed pass could put a different sentence in front of a recorded answer.
        """
        if self.injecting is None:
            return ()
        return tuple(await self.injecting(self.key("injected"), messages))

    async def gated(self, call: ToolCallPart) -> str | None:
        """
        What to hand the model in this call's place, or nothing where the session's plugins let it run.

        No key of its own, unlike the drain and the injection: this is asked from inside
        `Stepping.call`'s step, so the answer lands in the call's own `Returned` and needs no
        second record to be replayed from.
        """
        if self.gating is None:
            return None
        return await self.gating(call.tool_name, call.args_as_dict())

    async def request(
        self,
        model: Model,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """
        The provider's answer, asked for once across every pass at this conversation.

        The step records what the *codec* will take, so the response is lowered to JSON here rather
        than handed over as a dataclass: `JsonCodec` is the standard library's `json`, and a
        `ModelResponse` is not something it can encode. `parse_model_response` is the other half,
        and it is required rather than a convenience, since what comes back out of the store is an
        `object` on the pass that ran the request as much as on the one that resumed it.

        It is priced and timed on the way past for the same reason it is recorded at all: see
        `price` and `stamp`, both of which have to run here because everything further out happens
        after the record is written.

        The worktree is snapshotted first, because this is the moment it is worth snapshotting: no
        tool is running, so the tree is a coherent thing to read, and what is recorded is the state
        the model is about to be asked to reason about. A pass that replays this request replays the
        snapshot too and runs no git, so the pair stay in step whatever happens between them.

        The allowance is spent *before* any of that, so a request that was never made leaves no tree
        recorded in front of it. It is ordinarily spent earlier still, in `Agent.before_request`,
        because that runs before this and records a cursor of its own; see `allowed`.

        A refusal the provider will never take back is recorded here and re-raised as
        `RequestRefused`, which is the one failure this console answers for rather than letting the
        worker retry: see that exception for the loop it closes.

        A refusal that is only about *now* and says when it lifts is re-raised as `RequestDeferred`,
        which is the same move one answer along: the pass waits out the moment the provider named
        instead of being redelivered onto it every lease until it passes. Nothing is recorded here,
        because what is worth recording is the wait rather than the error, and the wait is
        `conversing`'s to take. Everything else propagates exactly as it did, because a redelivery is
        the right answer to an error that might come out differently and named no moment.

        A request already known to be refused is not made again, which is the first thing checked
        and therefore ahead of the allowance and the snapshot alike: a pass must spend nothing on a
        question whose answer is recorded, and a tree captured in front of a request nobody makes is
        a record of a moment that did not happen.
        """
        at = self.at("model")
        if (already := self.refused(at)) is not None:
            raise RequestRefused(already.why)
        key = self.key("model")
        self.allow(key)
        await self.snapshot()

        async def ask() -> object:
            started = monotonic()
            answered = await model.request(messages, model_settings, model_request_parameters)
            self.stamp(answered, timedelta(seconds=monotonic() - started))
            self.price(answered)
            return records.Response(response=ModelResponseTypeAdapter.dump_python(answered, mode="json")).recorded()

        try:
            return await self.step(key, ask, parse_model_response)
        except Exception as error:
            refused = terminally(error)
            if refused is not None:
                await self.refuse(at, refused)
                raise RequestRefused(refused.why) from error
            until = deferred_until(error, self.run.now())
            if until is None:
                raise
            raise RequestDeferred(until, str(error)) from error

    async def call(
        self,
        call: ToolCallPart,
        arguments: dict[str, object],
        context: RunContext[None],
        tool: ToolsetTool[None],
    ) -> records.Returned:
        """
        Run a tool once across every pass of this conversation, recording what it came back with.

        Required rather than an optimisation, and for both halves of what a tool does. A tool that
        *reads* returns a different answer every time it is asked, so a pass that re-ran one would
        resume the conversation against a file that has moved since the model was told what it
        said. A tool that *writes* has already written: running it again would either repeat the
        effect or, here, fail against anchors its own first run invalidated, which is a refusal for
        an edit that actually succeeded.

        Keyed by the call's own id rather than by position, because a model may ask for several
        tools in one response and `Agent.run` runs them concurrently: which reaches this first is a
        race, so a counter would hand a pass another call's record. The id is part of the model
        response this conversation recorded, so a replay is handed the same one.

        This is `step` and not `transact`, so it is at-least-once: a crash between the tool
        returning and the record landing re-runs it on the next pass. That window is one store
        round trip, and the failure it produces is the mild one, because an anchored edit whose
        anchors no longer resolve is refused rather than applied somewhere wrong.

        **A call the tool turned down or failed at is recorded exactly as one it answered**, with the
        outcome saying which, and that is the half of this that is easy to get wrong. A `ModelRetry`
        and a `ToolFailed` are both a result the model is sent, so both have to be in the checkpoint
        for the same reason a return is: a replay that ran the tool again to find out what it would
        say would be asking a live question, against a worktree the rest of the batch has since
        written to, and pairing whatever it said with a recorded response the model made believing
        the first answer. Under the graph the retry prompt was built outside anything this could wrap,
        which is why they were once left unrecorded; the loop builds it now, from this record.

        **How long it took is a field of the same record**, written by the same step, so there is no
        window where a return was recorded and its duration was not. Timed around the tool alone,
        so what is recorded is the call rather than the store write after it, and a failure is timed
        like a return because the tool ran either way. Anything else a tool raises is its own bug
        and propagates, recording nothing.

        **A call a plugin refused is recorded as one that returned the refusal**, inside this same
        step, so the record is the whole of what a replay needs and the plugin is never asked twice.
        It has no duration, because nothing ran: what took time was the asking, which is the
        plugin's and not the tool's.

        **A call a plugin ended the turn on is recorded as one**, for the same reason and with the
        same payoff: the turn stops once this call is answered, and a resumed pass stops it in the
        same place without asking anybody. `ending_here` is what the call itself writes and this is
        the only reader of it, so a call running beside one that ended the turn records nothing of
        its neighbour's decision. `halted` is set from the record rather than from the variable, which
        is what makes the pass that asked and every pass that replays agree.

        **A tool that hands back a `ToolReturn` is unwrapped here**, into the return the model is
        sent and the metadata it is not, because this loop and not Pydantic AI's graph is what builds
        the part the model reads. Lowered whole instead, the model would be sent an object with a
        `return_value` in it and the page would be handed the same. Only those two fields are taken:
        a `content` or a `tools` on one would be a promise to the model this loop does not keep, so
        either is refused rather than dropped on the floor.
        """

        async def perform() -> object:
            refused = await self.gated(call)
            if refused is not None:
                return records.Returned(returned=refused).recorded()
            started = monotonic()
            ending_here.set(False)
            try:
                came_back = await tool.toolset.call_tool(call.tool_name, arguments, context, tool)
            except (ModelRetry, ToolFailed) as failed:
                return records.Returned(
                    returned=failed.message,
                    took=timedelta(seconds=monotonic() - started),
                    outcome="failed",
                    ended=ending_here.get(),
                ).recorded()
            returned, metadata = unwrapped(came_back)
            return records.Returned(
                returned=to_jsonable_python(returned),
                took=timedelta(seconds=monotonic() - started),
                ended=ending_here.get(),
                metadata=to_jsonable_python(metadata),
            ).recorded()

        recorded = await self.step(self.identified("tool", call.tool_call_id), perform, parse_returned)
        if recorded.ended:
            self.halted = True
        return recorded

    def price(self, answered: ModelResponse) -> None:
        """
        Fill in what this request cost, **before** it is recorded rather than after.

        Filled before the response is recorded, so `turn:{n}:model:{i}` and the settled turn carry
        the same value and a reader watching a turn never waits until its end to see the cost.

        Recorded rather than looked up when a page is drawn because the rates move while what one
        request cost is settled. An existing provider-reported cost wins over the estimate.
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


ending_here: ContextVar[bool] = ContextVar("mainplate_ending_here", default=False)
"""
Whether the tool call running in this context asked for its turn to end.

**A context variable and not a field on the scope, because the scope is shared and this is not.** A
model can ask for several tools in one response and `loop.together` runs each in a task of its own; a
task inherits a copy of the context, so what one call sets here is invisible to its siblings and to
the loop above them. That is exactly the attribution `Stepping.call` needs, since what it records is
one call's record and not the turn's: a `hand_off` running beside a `read` must not make the `read`'s
record claim the turn stopped there.

Read once, by the step that wraps the call that set it, and never by anything outside this module.
"""


def ending_turn() -> None:
    """
    Say that the tool call running right now is the last thing its turn does.

    The whole of what the plugins layer is handed, so that nothing over there has to know that a call
    is recorded at all, let alone under what key. See `plugins.asking.Halting`.
    """
    ending_here.set(True)


@contextmanager
def stepping(
    run: Run,
    prefix: str,
    worktree: Worktree | None = None,
    pricer: Pricer | None = None,
    draining: Draining | None = None,
    allowance: Allowance | None = None,
    injecting: Injecting | None = None,
    gating: Gating | None = None,
) -> Iterator[Stepping]:
    """The durable model requests and tool calls of one turn in one workflow pass."""
    yield Stepping(
        run=run,
        prefix=prefix,
        worktree=worktree,
        pricer=pricer,
        draining=draining,
        injecting=injecting,
        gating=gating,
        allowance=allowance if allowance is not None else Allowance(limit=None),
    )
