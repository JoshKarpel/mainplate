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
from datetime import datetime
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
from mainplate.conversation import parse_tree
from mainplate.conversation import prompt_key
from mainplate.conversation import recorded_choice
from mainplate.conversation import transcript
from mainplate.conversation import tree_key
from mainplate.sessions import Origin
from mainplate.sessions import Session
from mainplate.sessions import enrol
from mainplate.sessions import mint_session_id
from mainplate.sessions import name_from
from mainplate.sessions import now_utc
from mainplate.sessions import read_session
from mainplate.sessions import read_sessions
from mainplate.snapshots import Worktrees


@dataclass(frozen=True, slots=True)
class Conversation:
    """
    A session, everything said in it, and what it is being said to.

    `chosen` is absent only for a session enrolled but never spoken to, which is the window between
    its row and its first message.

    `answerable` is the separate question of whether the profile it was started on still exists: a
    profile edited out from under a session leaves it readable and stuck, and the page says so
    rather than showing a spinner that will never resolve. It asks about the profile and not the
    model, matching exactly what the worker checks, because a model missing from the catalogue is
    not a reason a pass cannot run - an endpoint routes more ids than it advertises, and its own
    refusal is the authoritative answer about any one of them.
    """

    session: Session
    said: Transcript
    chosen: Choice | None
    answerable: bool

    repository: Path | None = None
    """The repository this session works in, or nothing where the console has none."""

    workspace: Path | None = None
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

    What a profile offers is discovered from the endpoint rather than written down, so it is
    configuration that arrives over the network and is refreshed by a task that answers no
    requests. A handler reads `catalogues.current` and gets a whole value; nothing it does causes a
    request to a gateway, so this is still a service that holds no in-flight state and no cache of
    anything anybody said.
    """

    worktrees: Worktrees | None = None
    """
    The repository each session gets a worktree of, or nothing at all to keep no workspaces.

    Held here because creating a session is what plants one, and because a page has to be able to
    say where a session works. What the *worker* does with it is take snapshots, and it is handed
    the same value separately: this object answers questions and never runs an agent.
    """

    now: Callable[[], datetime] = now_utc

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
        return Conversation(
            session=found,
            said=transcript(recorded),
            chosen=chosen,
            answerable=chosen is not None and self.catalogues.current.models_of(chosen.profile) is not None,
            repository=self.worktrees.repo if self.worktrees is not None else None,
            workspace=self.worktrees.at(session) if self.worktrees is not None else None,
        )

    async def start(self, said: str, chosen: Choice) -> Session:
        """
        A new session on `chosen`, named after the first thing said in it, with that message sent.

        Three writes, and the order is the whole of the choice. The choice is recorded before the
        message because the message is what *queues* the session: written the other way round, a
        worker could take the session between the two and find no profile to answer on. Enrolment
        comes first for the reason it always did, that a session in the list with nothing in it is
        visible where work nobody can find is not.
        """
        session = Session(id=mint_session_id(), created_at=self.now(), title=name_from(said))
        await enrol(self.database, session)
        # Before the message, with the choice, for the same reason: the message is what queues the
        # session, and a worker taking it before the worktree exists would snapshot a directory
        # that is not there. A session started rather than forked begins where the repository is.
        if self.worktrees is not None:
            await self.worktrees.plant(session.id)
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
        what *queues* a session, so a worker taking this one between the two would find no profile
        to answer on.
        """
        parent = await read_session(self.database, session)
        if parent is None:
            return None
        recorded = await self.checkpointer.load(session)
        carried = before(recorded, at)
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
        # The worktree the forked turn *originally started on*, so the branch answers the same
        # question against the same files. Planting at the repository's head instead would ask the
        # new model to redo turn 3 against whatever the disk holds now, which is a different
        # question wearing the same words, and the disagreement would be invisible in the
        # transcript. Nothing recorded means the parent ran with no workspace, so there is no
        # earlier state to reproduce and the fork starts where the repository is.
        if self.worktrees is not None:
            await self.worktrees.plant(forked.id, tree=parse_tree(recorded.get(tree_key(at))))
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
