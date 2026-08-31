# What a request handler is allowed to see, and every question it may ask.
#
# One object, constructed once at startup and handed to every route. It holds the store and
# nothing else: no agent, no worker, no in-flight state. That is what makes the console
# restartable and, in principle, separable, since answering a session is the worker's job and
# nothing here waits on one.
#
# Every read goes to the checkpoint, which is the only record of what was said. So there is no
# cache to invalidate and nothing to keep in step: two tabs open on one session render the same
# thing because they are reading the same rows, and a page rendered after a crash is right for
# the same reason.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from without_durability_sqlite import Database
from without_durability_sqlite import SqliteCheckpointer
from without_durability_sqlite import SqliteDurable

from mainplate.conversation import Transcript
from mainplate.conversation import prompt_key
from mainplate.conversation import transcript
from mainplate.sessions import Session
from mainplate.sessions import enrol
from mainplate.sessions import mint_session_id
from mainplate.sessions import name_from
from mainplate.sessions import now_utc
from mainplate.sessions import read_session
from mainplate.sessions import read_sessions


@dataclass(frozen=True, slots=True)
class Conversation:
    """A session and everything said in it, which is the pair every page is rendered from."""

    session: Session
    said: Transcript


@dataclass(frozen=True, slots=True)
class Service:
    database: Database
    durable: SqliteDurable
    checkpointer: SqliteCheckpointer
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
        return Conversation(session=found, said=transcript(await self.checkpointer.load(session)))

    async def start(self, said: str) -> Session:
        """A new session, named after the first thing said in it, with that message already sent."""
        session = Session(id=mint_session_id(), created_at=self.now(), title=name_from(said))
        await enrol(self.database, session)
        await self.say(session.id, turn=0, said=said)
        return session

    async def say(self, session: str, *, turn: int, said: str) -> None:
        """
        Put a message into a session's checkpoint, and ask for the session to be looked at.

        One call, because `SqliteDurable.arrive` writes the value and queues the workflow in a
        single commit: over one file there is no window where a session holds a message with
        nothing scheduled to answer it. Whichever worker takes it next is the one that answers,
        and this returns without waiting for any of that.
        """
        await self.durable.arrive(session, prompt_key(turn), said)
