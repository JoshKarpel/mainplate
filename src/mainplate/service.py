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
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import datetime
from datetime import timedelta
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
from mainplate.conversation import opening_tree_key
from mainplate.conversation import recorded_ask
from mainplate.conversation import recorded_choice
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_prompt
from mainplate.conversation import recorded_steer
from mainplate.conversation import refusal_in
from mainplate.conversation import requested_at
from mainplate.conversation import transcript
from mainplate.forge import Reachable
from mainplate.forge import Workspaces
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
from mainplate.sessions import tend
from mainplate.settings import DEFAULT_WATCHING
from mainplate.tending import TENDED
from mainplate.tending import Tending

# How many steps a session has recorded. A count and not a hash of them, because what it is asked
# for is whether to look again rather than what changed, and the store's own primary key already
# orders the rows this scans.
RECORDED = "SELECT COUNT(*) FROM workflow_checkpoint WHERE workflow = ?"


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
        return Conversation(
            session=found,
            said=said,
            chosen=chosen,
            answerable=chosen is not None and self.catalogues.current.models_of(chosen.endpoint) is not None,
            refused=refusal_in(recorded),
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

    async def token(self, session: str) -> int:
        """
        How much has been recorded for a session, as the one number that says whether to read again.

        What a live connection asks several times a second, so it has to be cheaper than the answer
        it guards: `load` decodes every step's JSON, which for a long conversation is megabytes to
        find out that nothing happened. This counts rows over the primary key's own prefix and reads
        no value at all.

        A count is a sound change token because a checkpoint is append-only: a step's key is written
        once and `ON CONFLICT` keeps the value it already had, so nothing is ever rewritten and the
        only way this moves is a record that did not exist before. It says how much, never what, and
        that is all a reader needs to decide to look properly.

        NOTE: this reaches past `SqliteCheckpointer` into `without-durability-sqlite`'s own table,
        which is the one place this console knows the store's schema rather than its interface. It
        belongs upstream as a method on the checkpointer; until it is one, a rename of that table is
        a change that has to be made here too.
        """
        counted = await self.database.run(lambda connection: connection.execute(RECORDED, (session,)).fetchone())
        return int(counted[0])

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

    async def start(self, said: str, chosen: Choice, title: str | None = None, tended: Tending = TENDED) -> Session:
        """
        A new session on `chosen`, named `title` or after the first thing said in it, with that
        message sent.

        Three writes, and the order is the whole of the choice. The choice is recorded before the
        message because the message is what *queues* the session: written the other way round, a
        worker could take the session between the two and find no endpoint to answer on. Enrolment
        comes first for the reason it always did, that a session in the list with nothing in it is
        visible where work nobody can find is not.

        A given name goes through `name_from` exactly as the message would, so there is one rule
        about what a session name is - whitespace collapsed, cut to a length a sidebar can hold -
        rather than one for a name somebody typed and another for one taken from a message. A name
        that is only whitespace collapses to nothing and is the same as not having named it, which
        is what an empty box posts.

        Nothing renames a session afterwards, and that is why the index may hold the title at all:
        it is a copy of something settled rather than of something that changes. Naming it here does
        not alter that, because this is still the one moment it is decided.

        `tended` is written **only where it differs from the shipped defaults**, and that is what keeps
        `NULL` meaning "nobody has said anything". The picker posts this pair on every session, so
        recording it unconditionally would make every column explicit, leave a moved constant reaching
        nothing, and make the defaulting branch a path only a database written before this existed can
        take - which is a path nothing exercises. Somebody who sets exactly the defaults is
        indistinguishable from somebody who left them, which is the correct reading of both.
        """
        named = name_from(title) if title else ""
        # Settled here rather than taken as posted, which is the same stance that stops a form with
        # no repository field moving a branch out of its repository. Everything that depends on the
        # repository is made to agree with it in one place: a session working in one reaches its
        # worktree and nothing else, and one working in none cannot reach a worktree there is none
        # of, be checked out at a commit, or start a branch. So no reader downstream reconciles
        # anything, and the form cannot record a contradiction. See `Choice.settled`.
        chosen = chosen.settled()
        session = Session(id=mint_session_id(), created_at=self.now(), title=named or name_from(said))
        # A branch of its own where nobody named one, which is what stops a session working in a
        # repository landing on a detached `HEAD`. That was the default until `Run` put `git commit`
        # in the box under the conversation, and a commit on a detached `HEAD` is reachable only
        # through the reflog. Filled *here* rather than in `settled`, because it takes the session's
        # own id and `settled` is a rule about a choice rather than about a session.
        chosen = chosen.branching(session.id)
        await enrol(self.database, session)
        if tended != TENDED:
            await tend(self.database, session.id, tended)
        # No cloning and no checkout here, deliberately. Somebody is waiting on this request and a
        # clone is a network fetch that can take minutes; the first pass does both, where slow work
        # already lives. Until then the session renders, names its repository, and has no files.
        await self.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(chosen))
        await self.say(session.id, said)
        return session

    async def fork(
        self,
        session: str,
        *,
        at: int,
        chosen: Choice,
        said: str | None = None,
        aside: bool = False,
        tended: Tending = TENDED,
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
        # By `start`'s rule, and a fork settles this afresh rather than inheriting it: a reserve is a
        # decision about how much room one conversation's context has left, and a branch's context is
        # not that conversation's.
        if tended != TENDED:
            await tend(self.database, forked.id, tended)
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
        await self.checkpointer.supply(forked.id, CHOICE_KEY, recorded_choice(chosen))
        if said:
            await self.say(forked.id, said)
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

    async def hand_off(self, session: str, guiding: str | None = None) -> None:
        """
        Ask this session to write down where it has got to, and to start its context again from that.

        `guiding` is whatever the person wants the handoff pointed at, appended to the standing ask
        rather than replacing it. Appended, because the two say different things: the base is what a
        handoff *is* and has to be there whether or not anybody adds to it, and this is what this one
        should dwell on. Replacing it would make a note like "focus on the parser" the whole of the
        instruction, which is a summary of a summary nobody asked for.

        Deliberately not a template with a slot in it. What somebody picking up a refactor needs and
        what somebody picking up an investigation needs are different documents, so the ask says what
        a handoff is about and leaves the shape to the model that read the conversation.

        A message like any other, which is what makes this cheap: the turn it opens is answered by
        the same pass, on the same agent, over the prefix already cached, and what comes back is
        recorded by the same tool machinery as every other call. Nothing here is a second mechanism
        for summarising a conversation - the summariser is the session itself, with its own tools, so
        it can check the working tree rather than recalling it.

        **In this session rather than in an aside**, which was the first design and was worse in four
        ways at once. An aside plants a fresh worktree at a recorded tree, so the agent asked to
        describe the work would be looking at a directory without any of it in it; its cost would
        land on a different session's total; it would need its own settings copied and its auto
        handoff turned off so it could not recurse; and its first request would pay full price,
        because instructions differ per session and sit in front of the whole cached prefix. Here
        there is no aside, no copy, and no cold read.

        Delivered rather than appended, because nothing else is going to queue this: `Service.run`
        appends since a command reaches no model, and this is a message that must be answered.

        No boundary on the ask, and one on what comes back. The context has to survive long enough
        to be summarised, so it is the *document* that clears it, which the tool records for itself.

        What it says is `recorded_ask`'s rather than this method's, because a pass that finds its own
        reserve crossed asks for exactly the same thing: two writers of one record, and the words in
        one place so they cannot come apart.
        """
        await self.durable.deliver(session, recorded_ask(guiding))

    async def tend(self, session: str, tending: Tending) -> None:
        """
        Say what this console should do for a session unasked, which the next pass reads.

        The one write here that is not an append, and the one that has nowhere else to go: a
        checkpoint keeps the value a key was first given, so a setting saved twice there would keep
        its first answer for ever, and `localStorage` is in a browser where the worker that acts on
        this may be another process. So it is a column, and this is the only thing that writes one.

        Next pass rather than at once, and that is the value the pass took saying so: a pass snapshots
        these on its way in, so a switch flicked while a turn is being answered reaches the turn after
        it. Nothing here waits for that, exactly as nothing here waits for a message to be answered.
        """
        await tend(self.database, session, tending)

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
        await self.durable.deliver(session, recorded_prompt(said, forget=forget))

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
        await self.durable.deliver(session, recorded_steer(said))
