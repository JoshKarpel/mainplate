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
from itertools import count
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
from mainplate.conversation import command_key
from mainplate.conversation import commands_in
from mainplate.conversation import opening_tree_key
from mainplate.conversation import prompt_key
from mainplate.conversation import recorded_choice
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_prompt
from mainplate.conversation import recorded_steer
from mainplate.conversation import requested_at
from mainplate.conversation import steer_key
from mainplate.conversation import steers_in
from mainplate.conversation import transcript
from mainplate.forge import Reachable
from mainplate.forge import Workspaces
from mainplate.reference import References
from mainplate.sessions import Origin
from mainplate.sessions import Session
from mainplate.sessions import enrol
from mainplate.sessions import mint_session_id
from mainplate.sessions import name_from
from mainplate.sessions import now_utc
from mainplate.sessions import read_session
from mainplate.sessions import read_sessions
from mainplate.settings import DEFAULT_WATCHING

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
        return Conversation(
            session=found,
            said=transcript(recorded),
            chosen=chosen,
            answerable=chosen is not None and self.catalogues.current.models_of(chosen.endpoint) is not None,
            repository=self.repository_of(chosen),
            worktree=self.workspaces.at(session) if self.workspaces is not None and working else None,
            runnable=self.commands is not None and self.workspaces is not None and working,
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

    async def start(self, said: str, chosen: Choice, title: str | None = None) -> Session:
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
        # No cloning and no checkout here, deliberately. Somebody is waiting on this request and a
        # clone is a network fetch that can take minutes; the first pass does both, where slow work
        # already lives. Until then the session renders, names its repository, and has no files.
        await self.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(chosen))
        await self.say(session.id, turn=0, said=said)
        return session

    async def fork(
        self, session: str, *, at: int, chosen: Choice, said: str | None = None, aside: bool = False
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
            await self.say(forked.id, turn=at, said=said)
        return forked

    async def steer(self, session: str, *, turn: int, said: str) -> int | None:
        """
        Put a message into a turn that is already being answered, and say which number it took, or
        nothing at all where the turn had already stopped listening.

        Written straight into the checkpoint rather than handed to the pass, because the pass may be
        in another process: the two halves of this console are joined only by the store, so the store
        is the only channel a steer can travel down. Pydantic AI's own `enqueue` is what *delivers*
        it once a pass picks it up, and is in-memory, so it could never be the transport.

        Nothing is queued, unlike `say`. The workflow is already running by definition - that is what
        makes this a steer - and queueing it again would ask a second worker to take a session the
        first one holds a lease on.

        The number is claimed by trying and checking rather than by counting, because `supply` keeps
        the value a key was first given and hands back the winner. Two steers racing for one number
        would otherwise leave the loser's message in the store under a key nobody reads, which is a
        message silently on the floor. Bounded by how many are already there, since each attempt that
        loses has found one more.

        **The pass competes for the same slots, and losing to it is `None` rather than the next
        number up.** It writes `CLOSED` into the next free one when it is about to stop listening, so
        getting that back is the store saying this turn will never be read again - and stepping past
        it to write at the number above would put the message exactly where it could not be seen. The
        caller answers by saying it into a turn of its own; see `CLOSED` and `Service.send`.
        """
        written = recorded_steer(said)
        for said_at in count(len(steers_in(await self.checkpointer.load(session), turn))):
            stored = await self.checkpointer.supply(session, steer_key(turn, said_at), written)
            if stored == written:
                return said_at
            if not isinstance(records.SAID.validate_python(stored), records.Steer):
                return None
        raise AssertionError("unreachable: `count` does not end")  # pragma: no cover

    async def run(self, session: str, said: str) -> int | None:
        """
        Run `said` in this session's own worktree, and say which slot recorded it, or nothing at all
        where this session has nowhere to run one.

        **The turn is the last one started**, whether or not it is still being answered, because that
        is where the command happened: everything said so far is above it and nothing has been said
        since. A queued turn counts as started, so a command run while a reply is coming lands under
        the message the person typed ahead rather than above it.

        The slot is claimed by trying and checking, exactly as `steer` claims its own and for the
        same reason: `supply` keeps the value a key was first given, so two commands posted at once
        would otherwise leave the loser's under a key nothing reads. Unlike a steer nothing else
        competes for these, so there is no `CLOSED` to get back - the only writer is whoever is
        typing, and the loop is against another of them.

        Recorded *before* it is started, and both of those are here rather than in the handler so the
        pair cannot come apart: a command started without a record would run with nothing on the page
        saying it had, and a record with nothing running would be a panel that never resolves.

        Nowhere to run one is `None` and not a raise, matching what `send` does with a turn that
        stopped listening: it is a state the page can explain, not a fault.
        """
        if self.commands is None or self.workspaces is None:
            return None
        found = await self.read(session)
        if found is None or found.chosen is None or found.chosen.repository is None:
            return None
        # The last turn started, which for any session this console made is at least turn 0: `start`
        # writes the choice and then the first message, so a session with a row has a prompt.
        turn = found.said.turns - 1
        if turn < 0:  # pragma: no cover - a session is created with its first message
            return None
        where = self.workspaces.at(session)
        written = recorded_command(said)
        for ran_at in count(len(commands_in(await self.checkpointer.load(session), turn))):
            stored = await self.checkpointer.supply(session, command_key(turn, ran_at), written)
            if stored == written:
                self.commands.start(Slot(session=session, turn=turn, at=ran_at), said, where)
                return ran_at
        raise AssertionError("unreachable: `count` does not end")  # pragma: no cover

    async def say(self, session: str, *, turn: int, said: str, forget: bool = False) -> None:
        """
        Put a message into a session's checkpoint, and ask for the session to be looked at.

        One call, because `SqliteDurable.arrive` writes the value and queues the workflow in a
        single commit: over one file there is no window where a session holds a message with
        nothing scheduled to answer it. Whichever worker takes it next is the one that answers,
        and this returns without waiting for any of that.

        `forget` opens this turn on a clean history, and it is a field of the record this already
        writes rather than a second call: one write, so a worker cannot take the turn between the two
        and answer it on a history the record was about to contradict. See `records.Prompt.forget`.
        """
        await self.durable.arrive(session, prompt_key(turn), recorded_prompt(said, forget=forget))

    async def send(self, session: str, said: str) -> int | None:
        """
        Put a message into a session at whichever moment its checkpoint is actually in, and say which
        turn it was steered into, or nothing at all where it was queued as a turn of its own.

        **The decision belongs here rather than in the composer**, and that is what makes it
        consistent. A page is rendered from a checkpoint, and by the time somebody has typed a
        paragraph into it that checkpoint has moved: a reader choosing between `Steer` and `Send` is
        choosing against a state that no longer holds, and two controls meant the server honoured a
        decision about the wrong turn. Read and write in one place and the answer is whatever the
        record says at the instant of writing.

        `answering` and not `turns - 1`, because a person can type again while a reply is coming: the
        turns behind the one in flight are queued rather than running, so a message steered into one
        of those would reach a model that has not been asked anything yet.

        **What the read decides is which to *try*, and the store decides which happens.** A read alone
        cannot settle it: the pass stops listening at the boundary where its run would end, which is
        some milliseconds before the turn's messages land, so a checkpoint saying a turn is being
        answered is not the same as a turn that will still hear you. So a steer that comes back
        `None` is one the pass shut the door on, and this says it into a turn of its own instead.

        That is a compare-and-swap re-decided on the true answer rather than a fallback: there is no
        second mechanism here, only the same two calls this always had, chosen with what the failed
        attempt reported. See `CLOSED`.
        """
        said_in = transcript(await self.checkpointer.load(session))
        if said_in.answering is not None:
            steered = await self.steer(session, turn=said_in.answering, said=said)
            if steered is not None:
                return said_in.answering
        # Re-read rather than trusting the count from before the attempt: losing the slot means the
        # turn ended while this was deciding, so what the next free turn is may have moved with it.
        await self.say(session, turn=transcript(await self.checkpointer.load(session)).turns, said=said)
        return None
