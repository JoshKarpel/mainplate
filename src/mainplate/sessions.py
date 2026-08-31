# Which sessions exist, which is the one question the checkpoint store cannot answer.
#
# `without-durability` addresses a workflow by id and offers no way to enumerate them, which it
# states as a gap rather than hides: a checkpoint answers "what has this session said" and
# nothing answers "what sessions are there". So an index is the application's to keep, and this
# is it.
#
# It holds a title as well as an id, which is worth saying is *not* the denormalization it looks
# like. A session is named after the first thing said in it, and that is written once, by the
# same handler that writes this row, at the same moment; nothing later changes it. What would
# drift is a copy of something that changes, and this is a copy of something that cannot. The
# alternative is loading every session's whole checkpoint to render a sidebar, which is the
# entire conversation history of every session on every page.
#
# It lives in the store's own SQLite file, deliberately. That file is the whole datastore, so a
# statement here and a checkpoint write reach the same tables, which is what would let a later
# version write this row inside a pass's own transaction with `Run.transact`. Today's creation
# path does not need that, and `enrol` says why.

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

from without_durability_sqlite import Database

# Created here rather than in the store's own `migrate`, which owns three tables of its own and
# knows nothing about sessions. Both run at startup and both are idempotent.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title      TEXT NOT NULL
) STRICT;
"""

# Long enough that an id is not guessable, which matters because a session id *is* its URL: this
# console has no accounts, so a link is the whole of what distinguishes one conversation from
# another on a machine more than one person can reach.
ID_BYTES = 16

# How much of the opening line a session is named after. Cut here rather than at render time so
# the row holds the name and the page holds no rule about how to make one.
TITLE_LENGTH = 80


@dataclass(frozen=True, slots=True)
class Session:
    """A session's identity and its name. What was said in it lives in its checkpoint."""

    id: str
    created_at: datetime
    title: str


def mint_session_id() -> str:
    return secrets.token_hex(ID_BYTES)


def name_from(said: str) -> str:
    """A session's name, which is its opening line with its whitespace collapsed and its tail cut."""
    opening = " ".join(said.split())
    if len(opening) <= TITLE_LENGTH:
        return opening
    return f"{opening[: TITLE_LENGTH - 1].rstrip()}\N{HORIZONTAL ELLIPSIS}"


async def prepare(database: Database) -> None:
    await database.run(lambda connection: connection.executescript(SCHEMA))


async def enrol(database: Database, session: Session) -> None:
    """
    Put a session in the index, before anything is asked of it.

    Before rather than after, and the order is the whole of the choice. Enrolling and then failing
    to queue the first message leaves a session in the list with nothing in it, which renders as
    an empty conversation somebody can type into again. Queueing and then failing to enrol would
    leave a session being answered that no list shows, which is work nobody can find. One of those
    is visible and the other is not.
    """
    await database.run(
        lambda connection: connection.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, title) VALUES (?, ?, ?)",
            (session.id, session.created_at.isoformat(), session.title),
        )
    )


async def read_sessions(database: Database) -> tuple[Session, ...]:
    """Every session, newest first, which is the order a chat console reads in."""
    rows = await selecting(database, "SELECT id, created_at, title FROM sessions ORDER BY created_at DESC, id DESC", ())
    return tuple(parse_session(row) for row in rows)


async def read_session(database: Database, session: str) -> Session | None:
    rows = await selecting(database, "SELECT id, created_at, title FROM sessions WHERE id = ?", (session,))
    return parse_session(rows[0]) if rows else None


async def selecting(database: Database, statement: str, parameters: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """
    A query's rows, as the three strings every statement here selects.

    The one place a row's shape is asserted rather than inferred: `sqlite3` hands back `Any`, so
    without this the driver's type would spread into everything that reads a session.
    """

    def query(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
        return [
            (str(identifier), str(created_at), str(title))
            for identifier, created_at, title in connection.execute(statement, parameters)
        ]

    return await database.run(query)


def parse_session(row: tuple[str, str, str]) -> Session:
    identifier, created_at, title = row
    return Session(id=identifier, created_at=datetime.fromisoformat(created_at), title=title)


def now_utc() -> datetime:
    return datetime.now(UTC)
