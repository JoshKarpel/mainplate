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

from without_durability_sqlite import Database
from without_durability_sqlite import SqliteCheckpointer
from without_durability_sqlite import SqliteDurable

from mainplate.agent import Choice
from mainplate.catalogue import Catalogues
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import Transcript
from mainplate.conversation import choice_of
from mainplate.conversation import prompt_key
from mainplate.conversation import recorded_choice
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
        await self.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(chosen))
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
