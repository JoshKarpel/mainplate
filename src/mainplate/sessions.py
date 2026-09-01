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
#
# `forked_from` and `forked_at` are the whole of the session tree. It is *not* held inside any
# checkpoint, deliberately: a session stays a flat run of turns, and what relates two of them is a
# fact about the pair rather than about either, so it lives with the rows that name them. That is
# also what keeps a fork portable, since each session's checkpoint remains the entire conversation
# that session had, with nothing to dereference.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    title       TEXT NOT NULL,
    forked_from TEXT,
    forked_at   INTEGER
) STRICT;
"""

# Added to a table that predates them. `CREATE TABLE IF NOT EXISTS` does nothing to a table that
# already exists, so a database written before forking existed would keep its three columns and
# every read naming a fourth would fail.
ADDED = (
    ("forked_from", "ALTER TABLE sessions ADD COLUMN forked_from TEXT"),
    ("forked_at", "ALTER TABLE sessions ADD COLUMN forked_at INTEGER"),
)

# Long enough that an id is not guessable, which matters because a session id *is* its URL: this
# console has no accounts, so a link is the whole of what distinguishes one conversation from
# another on a machine more than one person can reach.
ID_BYTES = 16

# How much of the opening line a session is named after. Cut here rather than at render time so
# the row holds the name and the page holds no rule about how to make one.
TITLE_LENGTH = 80


@dataclass(frozen=True, slots=True)
class Origin:
    """
    Where a fork came from: the session it branched off, and the turn it branched at.

    One value rather than two nullable fields on `Session`, because the two are only ever
    meaningful together: a session that names a parent and no turn, or a turn and no parent, is not
    a half-known origin but an impossible one. Keeping them in a value that is either wholly there
    or wholly absent is what stops that state being representable at all.

    `turn` is the first turn this session does *not* share with its parent. So a fork at turn 3
    carries turns 0 to 2 and is waiting to be told turn 3, which is exactly the point somebody
    picked when they said "go back to here and try again".
    """

    session: str
    turn: int


@dataclass(frozen=True, slots=True)
class Session:
    """A session's identity, its name, and where it branched from. What was said lives in its checkpoint."""

    id: str
    created_at: datetime
    title: str

    forked: Origin | None = None
    """
    The session and turn this one branched from, or nothing at all for one that began on its own.

    Written once, when the fork is made, and never again. It is not a copy of anything that
    changes: which session a fork came from is settled the moment it exists, exactly as the title
    is, so this is the same kind of fact the title already is and not the denormalization it
    resembles.
    """


def mint_session_id() -> str:
    return secrets.token_hex(ID_BYTES)


def name_from(said: str) -> str:
    """A session's name, which is its opening line with its whitespace collapsed and its tail cut."""
    opening = " ".join(said.split())
    if len(opening) <= TITLE_LENGTH:
        return opening
    return f"{opening[: TITLE_LENGTH - 1].rstrip()}\N{HORIZONTAL ELLIPSIS}"


async def prepare(database: Database) -> None:
    def migrate(connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)
        held = {str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")}
        for column, statement in ADDED:
            if column not in held:
                connection.execute(statement)

    await database.run(migrate)


# What every read selects, spelled once so the column order and `parse_session` cannot drift.
COLUMNS = "id, created_at, title, forked_from, forked_at"


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
            f"INSERT OR IGNORE INTO sessions ({COLUMNS}) VALUES (?, ?, ?, ?, ?)",
            (
                session.id,
                session.created_at.isoformat(),
                session.title,
                session.forked.session if session.forked else None,
                session.forked.turn if session.forked else None,
            ),
        )
    )


async def read_sessions(database: Database) -> tuple[Session, ...]:
    """Every session, newest first, which is the order a chat console reads in."""
    rows = await selecting(database, f"SELECT {COLUMNS} FROM sessions ORDER BY created_at DESC, id DESC", ())
    return tuple(parse_session(row) for row in rows)


async def read_session(database: Database, session: str) -> Session | None:
    rows = await selecting(database, f"SELECT {COLUMNS} FROM sessions WHERE id = ?", (session,))
    return parse_session(rows[0]) if rows else None


type Row = tuple[str, str, str, str | None, int | None]


async def selecting(database: Database, statement: str, parameters: tuple[str, ...]) -> list[Row]:
    """
    A query's rows, as the columns every statement here selects.

    The one place a row's shape is asserted rather than inferred: `sqlite3` hands back `Any`, so
    without this the driver's type would spread into everything that reads a session.
    """

    def query(connection: sqlite3.Connection) -> list[Row]:
        return [
            (
                str(identifier),
                str(created_at),
                str(title),
                None if forked_from is None else str(forked_from),
                None if forked_at is None else int(forked_at),
            )
            for identifier, created_at, title, forked_from, forked_at in connection.execute(statement, parameters)
        ]

    return await database.run(query)


def parse_session(row: Row) -> Session:
    identifier, created_at, title, forked_from, forked_at = row
    return Session(
        id=identifier,
        created_at=datetime.fromisoformat(created_at),
        title=title,
        # Both or neither, which is what `Origin` exists to make true. A row holding one without
        # the other is a database somebody edited by hand, and reading it as "not a fork" is the
        # quieter wrong answer; this says so instead.
        forked=parse_origin(identifier, forked_from, forked_at),
    )


def parse_origin(session: str, forked_from: str | None, forked_at: int | None) -> Origin | None:
    if forked_from is None and forked_at is None:
        return None
    if forked_from is None or forked_at is None:
        raise ValueError(f"session {session!r} names half an origin: {forked_from!r} at {forked_at!r}")
    return Origin(session=forked_from, turn=forked_at)


def now_utc() -> datetime:
    return datetime.now(UTC)
