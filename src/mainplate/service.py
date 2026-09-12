# What a request handler is allowed to see, and every question it may ask.
#
# One object, constructed once at startup and handed to every route. It holds the store and the
# models on offer, and nothing else: no agent, no worker, no in-flight state. That is what makes
# the console restartable and, in principle, separable, since answering a session is the worker's
# job and nothing here waits on one.
#
# Every read of what was *said* goes to the checkpoint, which is the only record of it. So there is
# no cache to invalidate and nothing to keep in step: two tabs open on one session render the same
# thing because they are reading the same rows, and a page rendered after a crash is right for
# the same reason.
#
# The models are the one thing here that is not read from the checkpoint, and they are not a cache
# of anything anybody said: they are configuration discovered from the endpoints and refreshed off
# the request path, so a handler reads a value out of memory rather than reaching a gateway.

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from functools import partial
from pathlib import Path

from without_durability_sqlite import Database
from without_durability_sqlite import SqliteCheckpointer
from without_durability_sqlite import SqliteDurable

from mainplate import records
from mainplate.agent import Choice
from mainplate.catalogue import Catalogues
from mainplate.commands import Commands
from mainplate.commands import Slot
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import Transcript
from mainplate.conversation import before
from mainplate.conversation import choice_of
from mainplate.conversation import declared_in
from mainplate.conversation import failure_in
from mainplate.conversation import opening_tree_key
from mainplate.conversation import plugins_refused_in
from mainplate.conversation import recorded_choice
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_prompt
from mainplate.conversation import recorded_steer
from mainplate.conversation import refusal_in
from mainplate.conversation import registered_in
from mainplate.conversation import requested_at
from mainplate.conversation import setup_declared_in
from mainplate.conversation import setup_key
from mainplate.conversation import setup_refused_in
from mainplate.conversation import setups_in
from mainplate.conversation import transcript
from mainplate.forge import Reachable
from mainplate.forge import Workspaces
from mainplate.plugins.asking import Declaring
from mainplate.plugins.asking import Live
from mainplate.plugins.asking import acted
from mainplate.plugins.asking import composed
from mainplate.plugins.asking import running
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.installed import Installed
from mainplate.plugins.protocol import Setting
from mainplate.plugins.protocol import Switch
from mainplate.plugins.protocol import number_of
from mainplate.reference import References
from mainplate.reference import Resending
from mainplate.reference import facts_of
from mainplate.reference import resending
from mainplate.sessions import Origin
from mainplate.sessions import Session
from mainplate.sessions import enrol
from mainplate.sessions import mint_session_id
from mainplate.sessions import name_from
from mainplate.sessions import now_utc
from mainplate.sessions import read_session
from mainplate.sessions import read_sessions
from mainplate.sessions import rename
from mainplate.sessions import set_settings
from mainplate.sessions import switch
from mainplate.settings import DEFAULT_WATCHING

# How many steps a session has recorded, whether a pass holds it, and when the queue next hands it
# over. A count rather than a hash of the steps, because what it is asked for is whether to look again
# rather than what changed, and the store's own primary key already orders the rows this scans.
#
# The two moments come back as the store wrote them, with the store's own now beside them, rather
# than as durations already subtracted. Both readings need them that way: `attention` subtracts, so
# that the one clock either moment was written against is the one it is measured against and no two
# machines' clocks ever meet; and `token` must not, because a duration shrinks between two polls with
# nothing having happened, and a token that moves on its own is a page that re-renders for ever.
# `NULL` from either subquery is a row that does not exist, which is its own answer in both cases.
#
# NOTE: `workflow_claim` and `workflow_queue` are `without-durability-sqlite`'s own tables, read here
# for the same reason and with the same cost as the count above. See `Service.attended`.
ATTENDED = """
SELECT
    (SELECT COUNT(*) FROM workflow_checkpoint WHERE workflow = :workflow),
    (SELECT held_until FROM workflow_claim WHERE workflow = :workflow),
    (SELECT visible_at FROM workflow_queue WHERE namespace = :namespace AND workflow = :workflow),
    unixepoch('now', 'subsec')
"""


@dataclass(frozen=True, slots=True)
class Claimed:
    """A pass holds this session right now, which is what a live claim on it means."""


@dataclass(frozen=True, slots=True)
class Queued:
    """No pass holds this session and the queue will hand it to the next worker that reads."""


@dataclass(frozen=True, slots=True)
class Delayed:
    """
    No pass holds this session and the delivery for it is held back until `until` from now.

    Which is what a pass that fell over leaves behind: the worker deliberately does not answer for a
    delivery whose pass raised, so the queue keeps the row it reserved and reclaims it once the lease
    elapses. That is the state this whole reading exists to name, because on the page it used to be
    indistinguishable from a reply being written.

    A duration and not a moment, so the page never subtracts two machines' clocks: the store measured
    it against its own, and the script counts down from what it was handed. `cache_note` is the same
    bargain one field along.
    """

    until: timedelta


@dataclass(frozen=True, slots=True)
class Idle:
    """
    No pass holds this session and nothing is scheduled to.

    The ordinary state of a settled conversation, and a real fault where something is outstanding:
    a message nobody will ever answer, which nothing else on the page can show.
    """


type Attention = Claimed | Queued | Delayed | Idle
"""
What the worker is doing about one session, as the claim and the queue answer between them.

**Not a fact about the conversation, so it is not in the checkpoint and must not be.** It is live
control-plane state that changes several times per pass and is true only at the instant it is read,
where a checkpoint holds what was said and never changes at all. Read on every render, next to the
count that decides whether to render.

Four arms rather than two booleans, because two of the four combinations cannot happen and a reader
of a pair would have to know which. A sealed union in the shape `Ended` already has here.
"""


@dataclass(frozen=True, slots=True)
class Attended:
    """
    One reading of what the store holds about a session outside its checkpoint values.

    The raw four, so that the two things made of them are made of the same one: `attention_of` reads a
    state and `Service.token` reads a change token, and a token built from the state would have to be
    built from words.

    Unix seconds and not `datetime`s, because they are the store's own numbers and the only thing done
    with them is comparing two of them against a third that came back beside them.
    """

    recorded: int
    held_until: float | None
    due_at: float | None
    asked_at: float


def attention_of(attended: Attended) -> Attention:
    """
    What the worker is doing about a session, out of what the store said about it.

    The claim is asked first and settles it, because a live claim *is* a pass in flight and a queue row
    beside one is only the delivery that pass is answering for. A claim outliving the process that took
    it reads as held until its lease elapses, which is the honest answer rather than a wrong one: the
    claim is what stops another worker starting, so for that interval a pass genuinely does hold the
    session, and `reclaim` is what ends it.

    With no claim, the delivery says the rest. Due already is a session the next worker read will take;
    due later is one held back, which is what the worker leaving a failed pass's delivery unanswered
    produces; no row at all is a session nothing is coming for.

    Pure, and taking the reading rather than the session, so the states a page draws are testable
    without a store: four values in, one arm out.
    """
    if attended.held_until is not None and attended.held_until > attended.asked_at:
        return Claimed()
    if attended.due_at is None:
        return Idle()
    waiting = attended.due_at - attended.asked_at
    return Queued() if waiting <= 0 else Delayed(until=timedelta(seconds=waiting))


def token_of(attended: Attended) -> str:
    """
    Whether a session is worth reading again, out of what the store said about it.

    **`asked_at` is the one field deliberately not in it**, and that is the whole of this function. The
    two moments go in as the store wrote them, so a reading taken a second later is the same token;
    put in as durations they would shrink between two polls with nothing having happened, and a token
    that differs from itself is a page that re-renders for ever. Pure and separate from `attention_of`
    for exactly that reason: the difference between the two is which fields each is allowed to touch,
    which is a thing a test can hold rather than a thing a reader has to notice.
    """
    return f"{attended.recorded}:{attended.held_until}:{attended.due_at}"


@dataclass(frozen=True, slots=True)
class Conversation:
    """
    A session, everything said in it, and what it is being said to.

    `chosen` is absent only for a session enrolled but never spoken to, which is the window between
    its row and its first message.

    `answerable` is the separate question of whether the endpoint it was started on still exists: a
    endpoint edited out from under a session leaves it readable and stuck, and the page says so
    rather than showing a spinner that will never resolve. It asks about the endpoint and not the
    model, matching exactly what the worker checks, because a model missing from the catalogue is
    not a reason a pass cannot run - an endpoint routes more ids than it advertises, and its own
    refusal is the authoritative answer about any one of them.
    """

    session: Session
    said: Transcript
    chosen: Choice | None
    answerable: bool

    refused: records.Refused | None = None
    """
    Why the turn being answered stopped and will not start again, where one did.

    A second way to be stuck, and a different question from `answerable`: that one asks whether the
    endpoint still exists, and this asks whether the provider took the request. Both end in the same
    place - a sentence in place of a spinner - because a spinner that will never resolve is the one
    state a person cannot diagnose.

    It is about the turn being answered rather than about any turn in the conversation. A refusal
    recorded against a turn that later answered is history, and history is what the transcript is
    for; only a refusal on the turn nothing has got past says the session has stopped.
    """

    failed: records.Failed | None = None
    """
    Why the last pass at this session raised, where one did and nothing has happened since.

    **The fourth way to be stuck, and the only one that gets better on its own.** A missing endpoint
    needs a configuration file put back, a refused request needs a fork, and a setup that would not
    run needs a switch moved; this needs the plugin, the tool or the store that fell over to be fixed,
    after which the redelivery the worker was already going to make carries on from where the pass
    stopped. So the sentence says what fell over *and* that it will be tried again, which is the one
    of the four where waiting is the right thing to do.

    Read against how far the session has got, so a failure something later got past is history. See
    `failure_in`.
    """

    attention: Attention = field(default_factory=Idle)
    """
    What the worker is doing about this session right now: whether a pass holds it, and when the next
    one is due.

    **The one field here that is not read from the checkpoint and is not configuration either.** It is
    the store's claim and queue rows, which are live control-plane state, and it is here because the
    page had no way to tell a reply being written from a session nothing was ever going to pick up:
    both drew the same three dots, for as long as the second lasted.

    `Idle` by default, which is what a `Conversation` built with no store behind it should say: the
    gallery renders from fixtures and a fixture has no worker.
    """

    repository: str | None = None
    """
    The repository this session works in, as a person reads it, or nothing where it works in none.

    `owner/repo` while a forge still reaches it, and the recorded id once none does. A name rather
    than a path, because the path is an implementation detail of this console and the name is the
    thing somebody recognises.
    """

    worktree: Path | None = None
    """
    This session's own worktree of it, which is where its files actually are.

    Both, because they answer different questions and a page needs each: the repository is what a
    reader recognises, and the worktree is where to point an editor. The worktree's own name is the
    session id, so it is worth nothing on its own.
    """

    runnable: bool = False
    """
    Whether this session can run a command the person types, which needs somewhere to run it.

    Its own field rather than `worktree is not None`, because it is two questions and only one of
    them is about the session: whether this session has files, and whether this console was built to
    run anything at all. A console with no `Commands` offers no `Run`, exactly as one with no sandbox
    offers no `bash`, and neither is a session's fault.
    """

    window: int | None = None
    """
    How large this session's model's context window is, or nothing where nothing says.

    Here because it is what turns a token count into a fraction, and the count is on every rule the
    transcript draws. It is not a fact about the conversation, so it cannot come out of the
    checkpoint: it is the reference database's answer about the model the session is bound to, read
    at render time exactly as a card's is, so a database that learns a model's window shows it on
    every session already running on that model.

    `None` for each of the three ways to know nothing - no database configured, an endpoint that no
    longer lists the recorded id, a model with no record - and the page draws the counts with no
    fraction beside them, which is what it drew before there was one.
    """

    since: timedelta | None = None
    """
    How long ago this conversation was last answered, as of this read, or nothing where it never was.

    **The one place this console subtracts two clocks**, and the caveat is worth having here rather
    than in a comment somewhere downstream. `Transcript.answered_at` is stamped by whichever process
    ran the pass and this is taken in a request handler, so the difference is sound exactly as long as
    those are one machine - which today they are, since the console, the worker and the file are one
    process. Split across machines it becomes as approximate as the two clocks' agreement, which for a
    threshold measured in hours is still fine and for anything finer would not be.

    Measured *here* rather than on the page, because a page is a pure function of already-answered
    questions and `datetime.now()` is not one of those. The page turns it into words and the script
    keeps it current; see `cache_note`.
    """

    declared: tuple[Installed, ...] | None = None
    """
    Every plugin this session *may* run, read out of files, or nothing while its worktree is planted.

    What the settings step draws a switch for, and the reason it can be drawn without a single plugin
    having been executed: a name, a tier and a path are all read from a directory listing and two YAML
    mappings. What each of them *is* is not here, because asking is running.

    `None` is the state the step's spinner is drawn for: the choice is recorded, the worktree is being
    planted, and what the session even declares is not yet known.
    """

    plugins: tuple[Enrolled, ...] | None = None
    """
    Everything this session set up, or nothing at all where that has not happened yet.

    **`None` is what puts a session on that step, and it is the trust boundary rather than a loading
    state.** A plugin is a program this console executes, so the list is empty of consequence until
    somebody has said which programs to run; the pass that follows the press is what fills it, from
    `setting_plugins_up`, once they have.

    What is here is exactly the set that was set up, which is the set that was switched on: a plugin
    somebody turned off contributes nothing because it was never run at all.
    """

    refused_plugins: records.Refused | None = None
    """
    Why this session's plugins could not be declared, where they could not.

    A third way to be stuck, beside a missing endpoint and a refused request, and it ends in the same
    place: a sentence in place of a spinner. Read only where `declared` is absent, since a later pass
    that succeeded wrote the declaration and that is the authoritative answer.

    Against `refused_load` below, which is the *other* failure and is not read from anywhere: this one
    happened in a pass with nobody waiting on it, so it had to be written down.
    """

    setup_script: bool = False
    """
    Whether this session's repository carries a `.mainplate/setup`, which the step draws a switch for.

    Off the repository's declaration, so it is false for a session that does not trust its
    repository, has none, or has not planted its worktree yet. It is not a plugin and never appears
    in `declared` or `plugins`: the console runs it itself, and what it asked for is a record of its
    own rather than a registration.
    """

    attempts: int = 0
    """
    How many times somebody has answered this session's settings step.

    Carried rather than counted again, because the next press has to be numbered against it and the
    count is a reading of the whole checkpoint: `Service.token`'s own note applies, since loading one
    decodes every step's JSON and for a long conversation that is megabytes. The read that produced
    this page already did it.
    """

    settling_up: bool = False
    """
    Whether a pass is out setting this session's plugins up, which is a press with no answer yet.

    The state that exists because setting up moved into a pass: somebody answered the step, the
    worker has the session, and what is on screen has to say so rather than showing the button they
    just pressed. It ends when the registration lands, and the live connection is what redraws the
    page it lands on.
    """

    refused_setup: records.Refused | None = None
    """
    Why the latest attempt at setting this session's plugins up stopped, where it did.

    Recorded, unlike the sentence this replaced, and the move into a pass is what forced it: nobody
    is waiting on a response any more, so a failure with nowhere to go is a session sitting under a
    spinner for ever. It is written against the attempt it belongs to, which is what keeps a
    write-once store honest here - pressing again opens a new attempt, and this reads the newest.
    """

    resending: Resending | None = None
    """
    What putting this conversation to the model again costs, cached and uncached.

    A **floor on the next turn** at either end rather than a price for one: both figures are the input
    of that turn's first request and nothing else, so the answer, the tools and any further requests
    are on top of both. The composer draws them with a `+` for exactly that reason; see `Resending`.

    Absent for the same three reasons `window` is, and for a fourth - a model whose record carries no
    price at all - so the composer says how long ago the prefix was written without saying what that
    is worth, which is what it says with no reference database configured.
    """


@dataclass(frozen=True, slots=True)
class Service:
    database: Database
    durable: SqliteDurable
    checkpointer: SqliteCheckpointer
    catalogues: Catalogues
    """
    The one thing here that changes while the process runs, and deliberately so.

    What an endpoint offers is discovered from the endpoint rather than written down, so it is
    configuration that arrives over the network and is refreshed by a task that answers no
    requests. A handler reads `catalogues.current` and gets a whole value; nothing it does causes a
    request to a gateway, so this is still a service that holds no in-flight state and no cache of
    anything anybody said.
    """

    workspaces: Workspaces | None = None
    """
    Where sessions' files come from and live, or nothing at all to keep no workspaces.

    Held here so a page can say which repository a session works in and where its worktree is, both
    of which are questions with no I/O in them. Making the worktree *exist* is the worker's, and it
    is handed the same value separately: this object answers questions and never runs an agent.
    """

    references: References = field(default_factory=References)
    """
    What is known about the models on offer beyond their names, refreshed off the request path.

    The same shape as `catalogues` and for the same reasons, with one difference that matters: what
    it holds may be `None`, meaning no reference was configured. That is not an empty answer but the
    absence of a question, and it is what stops a card reporting a missing record on a console
    nobody asked to look one up.
    """

    watching: timedelta = DEFAULT_WATCHING
    """
    How often a page's live connection asks whether the session it is showing has moved.

    Here because a route reads it and a route is handed this and nothing else. The value is
    `Settings.watching`, put in at startup; the default is the same constant that setting defaults
    to, so a `Service` built without one behaves as a configured console does.
    """

    commands: Commands | None = None
    """
    What runs a command a person typed, or nothing at all for a console that runs none.

    The one thing here that holds work in flight, which the module note above says this object does
    not. Stated rather than quietly excepted: a running command belongs to this process and does not
    survive a restart, where everything else here is a read of the store. What keeps it from
    spreading is that the *answers* are still only in the checkpoint - the command and its result are
    both recorded - so a page renders the same thing whichever process is asked. See `commands.py`.

    Absent is a console that cannot run one, the way `workspaces` absent is a console with no files.
    """

    declaring: Declaring | None = None
    """
    How to run a plugin, for the two events a request handler fires rather than a pass.

    Here because `compose` and `action` are answered by a handler: a leader somebody typed and a
    control somebody pressed are both requests, and the effects they ask for are writes this object
    already makes. Everything else a plugin is asked happens inside a pass, which is handed the same
    value separately - this object answers questions and never runs an agent.

    Absent is a console with no plugins, which is what every test that is not about them wants: a
    leader that names one is then a request naming nothing, exactly as it is on a session whose
    plugins do not include it.
    """

    now: Callable[[], datetime] = now_utc

    def repository_of(self, chosen: Choice | None) -> str | None:
        """
        What a page calls the repository a session works in, or nothing where it works in none.

        The repository a *forge* currently reaches when there is one, so a page shows `owner/repo`
        rather than the id, and the recorded id itself when no forge reaches it any more. The rule
        is `Reachable.readable`'s, so the note under a message box and a row in the sidebar cannot
        come to call one repository two different things.
        """
        if chosen is None or chosen.repository is None:
            return None
        return self.reachable.readable(chosen.repository)

    def reaches(self, repository: str) -> bool:
        """Whether a forge currently offers this repository, which is what a new session needs."""
        return self.workspaces is not None and self.workspaces.named(repository) is not None

    @property
    def reachable(self) -> Reachable:
        """What the picker offers, which is nothing at all where there are no workspaces."""
        return self.workspaces.reaching.current if self.workspaces is not None else Reachable(repositories=())

    async def listed(self) -> tuple[Session, ...]:
        return await read_sessions(self.database)

    async def read(self, session: str) -> Conversation | None:
        """
        One session, or nothing at all where the index has never heard of it.

        The index is what decides a session exists, rather than its checkpoint being non-empty: a
        workflow id nobody enrolled has an empty checkpoint and would otherwise render as a
        perfectly good blank conversation at whatever URL was typed.
        """
        found = await read_session(self.database, session)
        if found is None:
            return None
        recorded = await self.checkpointer.load(session)
        chosen = choice_of(recorded)
        working = chosen is not None and chosen.repository is not None
        facts = facts_of(self.catalogues.current, self.references.current, chosen) if chosen is not None else None
        said = transcript(recorded)
        # The one subtraction of two clocks in this console, taken here rather than on the page
        # because a page is a pure function of already-answered questions. See `Conversation.since`.
        since = None if said.answered_at is None else self.now() - said.answered_at
        registered = registered_in(recorded)
        declared = declared_in(recorded)
        attempted = setups_in(recorded)
        return Conversation(
            session=found,
            said=said,
            chosen=chosen,
            plugins=registered,
            # Read only where there is no registration to read instead, which is what makes a
            # write-once breadcrumb sound: a later pass that succeeded wrote the registration.
            declared=declared,
            # Asked of the *declaration* rather than of the registration, because that is what this
            # breadcrumb is about: a pass that failed to read the files wrote it, and a later pass
            # that succeeded wrote the declaration, which is the authoritative answer.
            refused_plugins=None if declared is not None else plugins_refused_in(recorded),
            setup_script=setup_declared_in(recorded),
            # Whether a pass is out setting this session's plugins up right now, which is one press
            # answered and nothing recorded against it yet. Two facts rather than one, because the
            # third state is the one worth drawing: pressed and still working, pressed and stopped
            # with a reason, or never pressed at all.
            attempts=attempted,
            settling_up=registered is None and attempted > 0,
            # Read only where there is no registration to read instead, exactly as the declaration's
            # own breadcrumb is: an attempt that failed before one that worked is history, and what
            # this field means is why the session is *still* on its step.
            refused_setup=None if registered is not None else setup_refused_in(recorded),
            answerable=chosen is not None and self.catalogues.current.models_of(chosen.endpoint) is not None,
            refused=refusal_in(recorded),
            # Why the last pass raised, where one did, and what the worker is doing about it now. The
            # pair is deliberate: the reason is a fact about a pass that is over and the attention is
            # true only at this instant, so one is in the checkpoint and the other is a read.
            failed=failure_in(recorded),
            attention=await self.attention(session),
            repository=self.repository_of(chosen),
            worktree=self.workspaces.at(session) if self.workspaces is not None and working else None,
            runnable=self.commands is not None and self.workspaces is not None and working,
            window=facts.context if facts is not None else None,
            since=since,
            resending=(
                resending(facts.cost, said.total.context)
                if facts is not None and facts.cost is not None and said.total.context
                else None
            ),
        )

    async def attended(self, session: str) -> Attended:
        """
        What both readings below are made of: how much this session has recorded, what the claim and
        the delivery say, and the moment the store answered.

        One query rather than three, because a render asks for both readings and they want overlapping
        subsets of it: a page that took the count on one statement and the claim on another could draw
        a session as claimed against a checkpoint from before that pass wrote anything.

        The moment is the *store's*, read in the same statement as the two it will be subtracted from,
        so `attention_of` measures each against the clock that wrote it and no two machines' clocks
        ever meet. `Conversation.since` is the one place in this console that does subtract two, and it
        says so; this one does not have to.

        NOTE: this reaches past `SqliteCheckpointer` and `SqliteScheduler` into
        `without-durability-sqlite`'s own three tables, which is the one place in this console that
        knows the store's schema rather than its interface. All three belong upstream - the count as a
        method on the checkpointer, the claim and the delivery as a status read on the checkpointer and
        the scheduler - and until they are, renaming any of those tables is a change that has to be
        made here too. They are one query behind one method so that there is one place to change.
        """
        row = await self.database.run(
            lambda connection: connection.execute(
                ATTENDED, {"workflow": session, "namespace": self.durable.scheduler.namespace}
            ).fetchone()
        )
        recorded, held, due, asked = row
        return Attended(
            recorded=int(recorded),
            held_until=None if held is None else float(held),
            due_at=None if due is None else float(due),
            asked_at=float(asked),
        )

    async def attention(self, session: str) -> Attention:
        """What the worker is doing about this session, as `attention_of` reads one `attended`."""
        return attention_of(await self.attended(session))

    async def token(self, session: str) -> str:
        """
        Whether this session is worth reading again: how much it has recorded, and where it stands
        with the worker.

        What a live connection asks several times a second, so it has to be cheaper than the answer
        it guards: `load` decodes every step's JSON, which for a long conversation is megabytes to
        find out that nothing happened. This reads three rows by primary key and decodes no value.

        **The count alone is not enough, and that is what this change is.** The count moves only when
        something is recorded, which is exactly what a broken pass does *not* do: a session whose pass
        fell over records nothing on the retry after that, so a token made of the count alone holds
        still while the page sits under a spinner, which is the failure this whole reading exists to
        end. What moves when the worker picks a session up, lets it go, and schedules the next attempt
        is the claim and the delivery, so those belong in the token that decides whether to redraw.

        **The two moments go in as the store wrote them and never as durations**, which is the one
        thing here that is easy to get wrong: a duration shrinks between two polls with nothing having
        happened, so a token carrying one differs from itself and the page re-renders for ever.

        **It is a change token and not a cursor**, which is what makes a non-monotone one sound: the
        stream compares it for inequality and nothing reads it as a position. A checkpoint is
        append-only and a claim is not, so this goes up and comes back down, and `!=` is true either
        way. A string rather than a number for the same reason it is three values: there is no
        arithmetic anybody may do on it.
        """
        return token_of(await self.attended(session))

    async def requested_at(self, session: str, turn: int, at: int) -> object | None:
        """
        What one model request came back with, or nothing at all where there is no such request.

        One answer for "no session" and "no request", because they are the same answer to the reader:
        the address names nothing. Nothing recorded can itself be `None`, so this is unambiguous.

        The whole checkpoint is loaded to answer it, which is what every read here does and what the
        one idea costs: there is no second index of what a turn holds.
        """
        found = await read_session(self.database, session)
        if found is None:
            return None
        return requested_at(await self.checkpointer.load(session), turn, at)

    async def start(self, chosen: Choice, title: str | None = None) -> Session:
        """
        A new session on `chosen`, ready to be set up, with nothing said in it yet.

        **Creating a session, loading its plugins and saying the first thing in it are three steps
        now**, and the split is forced rather than chosen. A repository's plugins cannot be *named*
        until its worktree is planted, which the worker does on a pass; and none of them may be *run*
        until somebody has seen the list, because running one is executing a program somebody may not
        want executed. So creation records the choice, enrols the session, and asks for a pass; that
        pass plants, reads what each tier declares, and blocks on an empty inbox; the page draws the
        settings step; and `Service.load` runs what that step left on. The first message comes after
        all of it.

        The cost, stated: **creation stops being fire-and-forget.** Time to a first answer is
        unchanged, since the clone happens either way, but you now create, wait, confirm, and come
        back to type, where before you could type and walk away. That is bigger than an extra click,
        and it is taken deliberately: a boundary in front of executing somebody else's program is
        worth more here than the convenience, and the convenience is recoverable later in a way a
        session that ran a plugin nobody looked at is not.

        `make_ready` rather than a delivery, which is the other half of the same split: what queues a
        session used to be a message, and there is no message yet. Nothing else about the queue
        changes, and a session nobody types into holds no lease and no worker slot, because a blocked
        pass has released its claim.

        A given name goes through `name_from` exactly as a message would, so there is one rule about
        what a session name is - whitespace collapsed, cut to a length a sidebar can hold. A name
        that is only whitespace collapses to nothing and is the same as not having named it, which is
        what an empty box posts.

        **A session is `UNTITLED` until its first message lands**, where a name was not given. The
        name still comes from that message and is still written once, so the claim the session index
        rests on survives with one word moved: written when the first message arrives rather than at
        creation.
        """
        named = name_from(title) if title else ""
        # Settled here rather than taken as posted, which is the same stance that stops a form with
        # no repository field moving a branch out of its repository. Everything that depends on the
        # repository is made to agree with it in one place: a session working in one reaches its
        # worktree and nothing else, and one working in none cannot reach a worktree there is none
        # of, be checked out at a commit, or start a branch. So no reader downstream reconciles
        # anything, and the form cannot record a contradiction. See `Choice.settled`.
        chosen = chosen.settled()
        session = Session(id=mint_session_id(), created_at=self.now(), title=named)
        # A branch of its own where nobody named one, which is what stops a session working in a
        # repository landing on a detached `HEAD`. That was the default until `Run` put `git commit`
        # in the box under the conversation, and a commit on a detached `HEAD` is reachable only
        # through the reflog. Filled *here* rather than in `settled`, because it takes the session's
        # own id and `settled` is a rule about a choice rather than about a session.
        chosen = chosen.branching(session.id)
        await enrol(self.database, session)
        # No cloning and no checkout here, deliberately. Somebody is waiting on this request and a
        # clone is a network fetch that can take minutes; the first pass does both, where slow work
        # already lives. Until then the session renders, names its repository, and has no files.
        await self.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(chosen))
        # The choice before the queueing, for the reason it always came before the first message: a
        # worker taking this session between the two would find no endpoint to answer on.
        await self.durable.scheduler.make_ready(session.id)
        return session

    async def fork(
        self,
        session: str,
        *,
        at: int,
        chosen: Choice,
        said: str | None = None,
        aside: bool = False,
    ) -> Session | None:
        """
        A new session carrying this one's turns before `at`, on `chosen`, and asking `said` next.

        A *copy* of an immutable prefix rather than a pointer into the parent, and that is the
        design rather than an implementation detail. Turns are append-only and a recorded turn is
        never rewritten, so the two sessions can never come to disagree about a turn they share:
        they are two values that happen to have been equal, not two views of one thing. It is what
        keeps a session's checkpoint the whole of its conversation, so a fork stays as readable and
        as portable on its own as the session it came from.

        It is also why this does not offend the rule against a second copy of what was said. That
        rule is about a copy that has to be kept in step with something that changes; nothing here
        changes.

        The branch point is *before* turn `at`, so that turn's own message does not come across as
        a settled turn. It comes across as `said`, to be asked again: the whole reason to fork a
        turn is usually to see it answered differently, and a fork that made you retype the
        question first would be answering a different one. The caller decides what that message is,
        because the other reason to fork a turn is to rephrase it.

        `said` of `None` leaves the fork waiting instead, which is the honest state when there is
        no message to re-ask - forking from the end of a conversation to carry on somewhere else.

        `aside` records that this fork is meant to come back, and changes nothing else: the copy, the
        worktree and the choice are the same either way, because what differs is only what somebody
        intended. It is recorded because nothing else could recover it and because the sidebar cannot
        draw the difference otherwise.

        The message goes last, after the choice, for the reason it does in `start`: a prompt is
        what *queues* a session, so a worker taking this one between the two would find no endpoint
        to answer on.
        """
        parent = await read_session(self.database, session)
        if parent is None:
            return None
        recorded = await self.checkpointer.load(session)
        carried = before(recorded, at)
        # A fork may *attach* a repository to a session that had none, and may not *swap* one for
        # another. The two are not the same act. Swapping asks the new model to redo a turn against
        # different files, which is a different question wearing the same words; attaching asks it
        # to carry on with files where there were none, and the turns being inherited were not
        # asked against other files, they were asked against no files at all. That is the ordinary
        # shape of thinking something through and then going to work on it.
        #
        # Decided here rather than trusted from the caller, so that a form which names nothing
        # cannot quietly move a session out of its repository - which is exactly what the fork
        # form, having no control for it, would otherwise do.
        was = choice_of(recorded)
        held = was.repository if was is not None else None
        chosen = replace(chosen, repository=chosen.repository if held is None else held)
        # Settled *after* the repository is decided, and the order is the whole of it: a fork that
        # inherits its parent's repository reaches that worktree whatever the form said, and one
        # attaching a repository to a session that had none moves to `WORKTREE` by the same rule.
        #
        # `forked` is what drops the base and the branch the parent was started with. A fork plants
        # at the tree of the turn it re-asks, so a base beside that would be a second answer to where
        # its files come from; and `git worktree add -b` refuses a branch already in use, so an
        # inherited one is a worktree that cannot be planted at all.
        chosen = chosen.settled(forked=True)
        forked = Session(
            id=mint_session_id(),
            created_at=self.now(),
            # A fork's opening line is its parent's, because it literally carries it: the title is
            # what the first message says, and the first message came across with the rest.
            title=parent.title,
            forked=Origin(session=session, turn=at, aside=aside),
        )
        # And then a branch of the fork's *own*, which is the other half of `settled` dropping the
        # parent's: dropping it alone would leave every fork on a detached `HEAD`, where a fork is
        # exactly where somebody carries on working and therefore commits.
        chosen = chosen.branching(forked.id)
        await enrol(self.database, forked)
        # **The switches and not the settings**, which is the one column of the two a fork inherits.
        # What a plugin has *stored* is about one conversation's context and a branch's context is not
        # that conversation's, so that half starts empty. Which plugins a session runs is the other
        # half, and it comes across as the *defaults* its settings step is drawn with rather than as a
        # decision already taken: a branch of a session with a plugin turned off draws it turned off,
        # and somebody still presses the button to say so.
        if parent.tending.enabled:
            await switch(self.database, forked.id, parent.tending.enabled)
        for key, value in carried.items():
            await self.checkpointer.supply(forked.id, key, value)
        # The tree of the turn being re-asked, carried across on its own even though that turn's
        # prompt and messages are not. It is what makes the branch answer the *same* question: the
        # first pass plants the fork's worktree at this tree rather than at the repository's head,
        # so the new model sees the files the original turn saw. Redoing turn 3 against whatever
        # the disk holds now would be a different question wearing the same words, and the
        # disagreement would be invisible in the transcript.
        #
        # Recorded here rather than planted here for the reason `start` clones nothing: this is a
        # request, and a checkout is not.
        started_on = recorded.get(opening_tree_key(at))
        if started_on is not None:
            await self.checkpointer.supply(forked.id, opening_tree_key(at), started_on)
        # **Nothing any plugin declared, contributed, or was confirmed for comes across**, and that is
        # what makes a fork the place a session changes its plugins. A branch's checkpoint holds no
        # declaration, no registration and no press, so its first pass reads what the tree it is
        # planted at declares, its page draws the settings step over the turns it carries, and the
        # press that answers the step is what runs `setup` again. Editing `.mainplate/` and forking is
        # therefore how a conversation iterates on its own plugins, the setup script that installs its
        # toolchain included - which a fork *has* to run again, since it plants a fresh worktree and an
        # ignored directory does not come across in a recorded tree.
        #
        # **The press being asked for again is the trust boundary rather than a papercut.** A fork
        # plants at a *recorded tree*, which is a tree a model wrote: a snapshot is `git add -A`, so a
        # `.mainplate/` file the model created on turn 4 is in the tree recorded for turn 5. What makes
        # running it legitimate is that somebody chose to fork this conversation and then confirmed the
        # switches in the branch, which is the same confirmation every new session gives, asked for in
        # the same place and drawn from the parent's switches as defaults.
        await self.checkpointer.supply(forked.id, CHOICE_KEY, recorded_choice(chosen))
        if said:
            await self.say(forked.id, said)
        else:
            # A fork with nothing to re-ask is queued all the same, because its first pass is what
            # plants its worktree and registers its plugins. Without this it would sit un-set-up
            # until somebody typed, and the settings step would have nothing to draw.
            await self.durable.scheduler.make_ready(forked.id)
        return forked

    async def run(self, session: str, said: str) -> str | None:
        """
        Run `said` in this session's own worktree, and say which entry recorded it, or nothing at all
        where this session has nowhere to run one.

        Delivered to the session's inbox like a message, and read out of it by nobody: a pass passes
        over a command on its way down the queue. What being an entry buys is the one thing a slot
        could not give it, which is a *place*: the store files it in the order it arrived, so where it
        sits among the turn's model records is where it was run, and its panel stays there rather
        than sinking as later answers land above it.

        Nothing has to be claimed by trying any more, and nothing has to decide which turn it belongs
        to. Both of those were answers to questions the key space asked; the store names the key, so
        two commands posted at once are simply two entries.

        Recorded *before* it is started, and both of those are here rather than in the handler so the
        pair cannot come apart: a command started without a record would run with nothing on the page
        saying it had, and a record with nothing running would be a panel that never resolves.

        Nowhere to run one is `None` and not a raise: it is a state the page can explain, not a fault.
        """
        if self.commands is None or self.workspaces is None:
            return None
        found = await self.read(session)
        if found is None or found.chosen is None or found.chosen.repository is None:
            return None
        where = self.workspaces.at(session)
        # Appended rather than delivered, because there is nothing for a worker to do about it: a
        # command reaches no model, so waking a pass to look at one would be a pass with no work.
        entry = await self.checkpointer.append(session, recorded_command(said))
        self.commands.start(Slot(session=session, entry=entry.key), said, where)
        return entry.key

    async def live(self, session: str, found: Conversation) -> Live | None:
        """
        One session's running plugins, as a request handler asks them things.

        Built per request rather than held, because everything in it is that session's: what it set
        up, what its plugins are set to, and where its files are. `None` where its plugins have not
        been set up yet, which is the one state a handler has to refuse rather than guess at - a
        plugin nothing has run has no leader to answer to and no control to press.

        The effects are bound to this session here, so nothing below can reach another one: a
        delivery goes into this inbox and a write goes into this row.

        **Thinned the way a pass thins it**, so a leader a handler answers and a leader the model can
        reach are one list. A refusal is deliberately not raised here, unlike in a pass: what a pass
        is about to do with the set is open a turn, and what this is about to do is draw a menu.
        """
        if self.declaring is None or found.plugins is None:
            return None
        return Live(
            session=session,
            enrolled=running(found.plugins, found.session.tending),
            tending=found.session.tending,
            speaking=self.declaring.speaking,
            # The tree as a value rather than the path the page prints, because what runs a confined
            # plugin builds a sandbox around it and needs the git directory it was told. See
            # `Speaking`.
            worktree=(
                None
                if self.workspaces is None or found.repository is None or found.worktree is None
                else self.workspaces.worktree(session, found.repository)
            ),
            delivering=partial(self.note, session),
            storing=partial(set_settings, self.database, session),
        )

    async def note(self, session: str, note: records.Note) -> None:
        """
        Put a note a plugin asked for into a session's inbox, and ask for a look at it.

        Delivered rather than appended, because a note is a message that must be answered: `run`
        appends since a command reaches no model, and this is not that.
        """
        await self.durable.deliver(session, note.recorded())

    async def compose(self, session: str, found: Conversation, leader: str, said: str) -> int | None:
        """
        Hand one plugin its own answer in the composer, with whatever was in the box.

        `None` where no running plugin answers to that leader, which the route turns into a refusal:
        a leader is a word somebody typed, so one this session does not have is a request naming
        nothing rather than a fault. Otherwise **how many notes were delivered**, which is what tells
        the route whether the checkpoint moved: an answer asking only for a `set`, or for nothing at
        all, leaves the conversation byte for byte as the route already has it.

        The effects are performed here rather than by the plugin, exactly as they are inside a pass,
        and here there is no step to sit in: a handler is where this console already writes.
        """
        live = await self.live(session, found)
        if live is None:
            return None
        answering = live.answering(leader)
        if answering is None:
            return None
        plugin, own = answering
        notes = await composed(live, plugin, own, said)
        for note in notes:
            await self.note(session, note)
        return len(notes)

    async def press(self, session: str, found: Conversation, plugin: str, posted: Mapping[str, str]) -> Enrolled | None:
        """
        Save a plugin's card as it was posted, and tell the plugin about the controls that moved.

        **The whole card is written and only the changes are announced**, which is the difference
        between what a form can say and what an `action` means. A form posts every control it holds
        whether the trigger was `Set` or a switch changing, so what moved is not in the post; it is
        the difference between the post and what is stored, and this holds both.

        A switch that is off posts nothing at all, and that is resolvable rather than ambiguous: the
        card declares every control it has, so an absent name on a card that was posted is off. That
        is why this walks the *card* rather than the post.

        The write happens before the plugin is asked, because that is what a control is: a card that
        told the plugin and left the column alone would draw one answer and hold another.

        **The plugin is handed back rather than a yes**, because the route's next move is to redraw
        that plugin's card and finding it again is the same lookup made twice, in two modules, over
        two different resolutions of what this session runs. `None` where the plugin is not one it
        runs, which is a posted value naming nothing rather than a fault.
        """
        live = await self.live(session, found)
        if live is None:
            return None
        enrolled = live.named(plugin)
        if enrolled is None or enrolled.described.card is None:
            return None
        was = live.settings(enrolled)
        wanted: dict[str, Setting] = {}
        for row in enrolled.described.card.rows:
            control = row.control
            if isinstance(control, Switch):
                wanted[control.name] = control.name in posted
                continue
            # Both the parse and the bounds, because the box carries the plugin's own `min` and `max`
            # and a browser is the only thing those two attributes bind. See `number_of` for what an
            # unreadable one and an out-of-range one each come back as.
            wanted[control.name] = number_of(control, posted.get(control.name), was[control.name])
        await set_settings(self.database, session, plugin, wanted)
        for name, value in wanted.items():
            if was.get(name) == value:
                continue
            for note in await acted(live, enrolled, name, value):
                await self.note(session, note)
        return enrolled

    async def setup_again(self, session: str) -> None:
        """
        Ask for another setup pass, which is the whole of what retrying a refused declaration is.

        `make_ready` and nothing else: a pass that could not read what a session declares wrote no
        declaration, so it is read again from scratch with nothing recorded to conflict with. That is
        forced rather than chosen - the store keeps the value a key was first given, so a declaration
        written with one tier missing could never be corrected and a control that rewrote it would be
        writing into a slot that ignores it.
        """
        await self.durable.scheduler.make_ready(session)

    async def settle(self, session: str, enabled: Mapping[str, bool], attempt: int) -> None:
        """
        Answer the settings step: record the switches, say that somebody pressed, and ask for a pass.

        **The press is the confirmation and the pass is what it confirms.** A plugin is a program, so
        nothing has run one before this: the pass that planted the worktree read what each tier
        declares out of files, and `setup_key` is the recorded fact that a person has since looked at
        that list. The pass that follows sets up exactly the plugins the switches left on.

        **Nothing is run here**, which is the change this console made after asking a plugin to
        install things: a setup fetches and builds, and a request somebody is waiting on is the wrong
        place for minutes of that. It is the same argument that put the clone in a pass.

        Three writes and no wait: the column, the key, and the queue. The key is numbered, so pressing
        again after a setup that would not finish is a new attempt rather than a slot that keeps its
        first answer, and the number is `Conversation.attempts` - the route read the conversation to
        draw the step, so counting the attempts again here would be a second full decode to learn an
        integer that read already had. A number that has moved under the press writes into a key
        something else holds, and the store keeps the value a key was first given, so the losing press
        changes nothing rather than corrupting anything.
        """
        await switch(self.database, session, enabled)
        await self.checkpointer.supply(session, setup_key(attempt), records.Confirmed().recorded())
        await self.durable.scheduler.make_ready(session)

    async def say(self, session: str, said: str, *, forget: bool = False) -> None:
        """
        Put a message into a session that must be answered on its own, and ask for a look at it.

        A `Prompt` rather than a `Steer`, which is the whole of what "on its own" means: a pass
        draining what arrived while it was working stops at one of these, so this is never folded
        into the turn already running. What `Send` does is `send`, which delivers the other one.

        One call, because `SqliteDurable.deliver` appends the value and queues the workflow in a
        single commit: over one file there is no window where a session holds a message with nothing
        scheduled to answer it. Whichever worker takes it next is the one that answers, and this
        returns without waiting for any of that.

        No turn is named, and that is the inbox: which turn a message opens is decided by the pass
        that takes it, so there is no number here to be stale by the time it is written.

        `forget` opens that turn on a clean history, and it is a field of the record this already
        writes rather than a second entry: one append, so a worker cannot take the message between
        the two and answer it on a history the record was about to contradict.
        """
        await self.naming(session, said)
        await self.durable.deliver(session, recorded_prompt(said, forget=forget))

    async def naming(self, session: str, said: str) -> None:
        """
        Name a session after the first thing said in it, where nobody named it and nothing has been.

        **Written once, when the first message arrives**, which is one word moved from where it used
        to be rather than a new kind of write: a session is named after its opening line and nothing
        ever renames it, so the index still holds a copy of something settled rather than of
        something that changes.

        A session created with a title keeps it, which is `Choice.branching`'s existing rule one
        field along: a name somebody typed always wins over a generated one.

        The statement is the check, so there is nothing to read first: `rename` matches on the title
        still being empty, and a session created without one is written `''` rather than `NULL`. A
        `SELECT` in front of it would be the same condition asked twice, once of a row and once of a
        join, with a window between them.
        """
        await rename(self.database, session, name_from(said))

    async def send(self, session: str, said: str) -> None:
        """
        Put a message into a session at whichever moment it is actually in, which the pass decides.

        **The decision belongs to the pass rather than to the composer or to this**, and that is what
        makes it consistent. A page is rendered from a checkpoint, and by the time somebody has typed
        a paragraph into it that checkpoint has moved: a reader choosing between `Steer` and `Send`
        is choosing against a state that no longer holds. So was this, when it read the checkpoint
        and chose which of two writes to make; the read and the write were two moments, and a turn
        could end between them.

        With a queue there is no moment to get right. A `Steer` is delivered, and where it lands is
        where the pass finds it: folded into the request the turn is about to make, or, if nothing is
        listening by then, opening the next turn. Nothing is claimed, nothing is refused, and nothing
        has to be re-decided on a failed attempt - which is why this now returns nothing at all,
        where it used to have to say which turn had taken the message.
        """
        await self.naming(session, said)
        await self.durable.deliver(session, recorded_steer(said))
