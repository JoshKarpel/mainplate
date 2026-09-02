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

from mainplate.agent import Choice
from mainplate.catalogue import Catalogues
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import Transcript
from mainplate.conversation import before
from mainplate.conversation import choice_of
from mainplate.conversation import opening_tree_key
from mainplate.conversation import prompt_key
from mainplate.conversation import recorded_choice
from mainplate.conversation import sourced_at
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

    async def recorded_at(self, session: str, turn: int, at: int) -> object | None:
        """
        What the checkpoint holds behind one panel, or nothing at all where there is no such panel.

        One answer for "no session" and "no panel", because they are the same answer to the reader:
        the address names nothing. Nothing recorded can itself be `None`, so this is unambiguous - a
        prompt is refused before it is written and a panel is a run of at least one part.

        The whole checkpoint is loaded to answer it, which is what every read here does and what the
        one idea costs: there is no second index of what a turn holds, and a panel is a reading of
        the record rather than a row in it.
        """
        found = await read_session(self.database, session)
        if found is None:
            return None
        return sourced_at(await self.checkpointer.load(session), turn, at)

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
        # The isolation is settled here rather than taken as posted, which is the same stance that
        # stops a form with no repository field moving a branch out of its repository. A session
        # working in a repository reaches its worktree and nothing else, and one working in none
        # cannot reach a worktree there is none of, so the pair is never recorded contradicting
        # itself and no reader downstream has to reconcile the two.
        chosen = replace(chosen, isolation=chosen.isolation.settled(chosen.repository))
        session = Session(id=mint_session_id(), created_at=self.now(), title=named or name_from(said))
        await enrol(self.database, session)
        # No cloning and no checkout here, deliberately. Somebody is waiting on this request and a
        # clone is a network fetch that can take minutes; the first pass does both, where slow work
        # already lives. Until then the session renders, names its repository, and has no files.
        await self.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(chosen))
        await self.say(session.id, turn=0, said=said)
        return session

    async def fork(self, session: str, *, at: int, chosen: Choice, said: str | None = None) -> Session | None:
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
        chosen = replace(chosen, isolation=chosen.isolation.settled(chosen.repository))
        forked = Session(
            id=mint_session_id(),
            created_at=self.now(),
            # A fork's opening line is its parent's, because it literally carries it: the title is
            # what the first message says, and the first message came across with the rest.
            title=parent.title,
            forked=Origin(session=session, turn=at),
        )
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

    async def say(self, session: str, *, turn: int, said: str) -> None:
        """
        Put a message into a session's checkpoint, and ask for the session to be looked at.

        One call, because `SqliteDurable.arrive` writes the value and queues the workflow in a
        single commit: over one file there is no window where a session holds a message with
        nothing scheduled to answer it. Whichever worker takes it next is the one that answers,
        and this returns without waiting for any of that.
        """
        await self.durable.arrive(session, prompt_key(turn), said)
