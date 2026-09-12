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
#
# It is also what lets a read here reach *into* a checkpoint rather than copying out of one. A
# session's repository is recorded in its `choice`, and a checkpoint is a row per key rather than
# one value, so one join reads that one small row per session and this table stays the settled
# facts it holds. A column copying something already recorded would be the second copy the whole
# console is built to avoid.
#
# `Tending` is the one thing here that is *not* settled, and it is not that second copy either: it
# has no other home. A session's own settings have to be mutable to be settings at all, and the two
# places this console otherwise keeps things both refuse them - the checkpoint keeps the value a key
# was first given, so a setting saved twice would keep its first answer for ever, and `localStorage`
# is in a browser where the worker that reads this may be another process entirely. So it is a
# column, and the table that already answers "which sessions are there" is where it goes.

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Final

from without_durability_sqlite import Database

from mainplate.conversation import ARCHIVED_KEY
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import REPOSITORY_FIELD
from mainplate.tending import Tending
from mainplate.tending import parse_tending
from mainplate.tending import written

# Created here rather than in the store's own `migrate`, which owns three tables of its own and
# knows nothing about sessions. Both run at startup and both are idempotent.
#
# `forked_from` and `forked_at` are the whole of the session tree. It is *not* held inside any
# checkpoint, deliberately: a session stays a flat run of turns, and what relates two of them is a
# fact about the pair rather than about either, so it lives with the rows that name them. That is
# also what keeps a fork portable, since each session's checkpoint remains the entire conversation
# that session had, with nothing to dereference.
#
# `enabled` and `settings` are the two columns a plugin's card is behind, and they are text rather
# than a column apiece because **the columns cannot grow**: a plugin cannot run `ALTER TABLE`, and
# `ADDED` is already several migrations long. So they hold JSON keyed by qualified plugin name, and
# the reads below reach into them the same way `SELECTION` already reaches into a session's `choice`.
#
# Two costs, both real, and both answered rather than hidden. A whole-blob write clobbers, so `set`
# is a `json_patch` against one plugin's own sub-object in a single `UPDATE` and two plugins saving
# at once keep both saves. And `STRICT` stops covering what is in them, so the invariant moves to a
# parse at the boundary against the schema each plugin's card already declares; see `tending.py`.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    title       TEXT NOT NULL,
    forked_from TEXT,
    forked_at   INTEGER,
    forked_aside INTEGER,
    enabled     TEXT,
    settings    TEXT
) STRICT;
"""

# Added to a table that predates them. `CREATE TABLE IF NOT EXISTS` does nothing to a table that
# already exists, so a database written before forking existed would keep its three columns and
# every read naming a fourth would fail.
ADDED = (
    ("forked_from", "ALTER TABLE sessions ADD COLUMN forked_from TEXT"),
    ("forked_at", "ALTER TABLE sessions ADD COLUMN forked_at INTEGER"),
    ("forked_aside", "ALTER TABLE sessions ADD COLUMN forked_aside INTEGER"),
    ("enabled", "ALTER TABLE sessions ADD COLUMN enabled TEXT"),
    ("settings", "ALTER TABLE sessions ADD COLUMN settings TEXT"),
)

# Long enough that an id is not guessable, which matters because a session id *is* its URL: this
# console has no accounts, so a link is the whole of what distinguishes one conversation from
# another on a machine more than one person can reach.
ID_BYTES = 16

# How much of the opening line a session is named after. Cut here rather than at render time so
# the row holds the name and the page holds no rule about how to make one.
TITLE_LENGTH = 80

# What the field naming a session is called on the form that creates one. Here rather than beside
# the other posted field names in `conversation.py`, because those are *checkpoint* keys that a form
# happens to share and this one is not: a title goes to the session index and never into what was
# said. Named once so the page that renders the input and the handler that reads it cannot drift.
TITLE_FIELD: Final = "title"


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

    `aside` is whether the fork was made as a step out that is meant to come back, rather than as a
    way of going somewhere else. Nothing about the two differs mechanically - both are `Service.fork`
    and both copy the same prefix - so this is a fact about what somebody *meant*, recorded because
    only they know it and because the sidebar cannot draw the difference otherwise. Nothing writes it
    any more, since the composer's aside went with its fork; rows written while it existed still
    carry it and are still drawn as what they were. It sits here rather than in the checkpoint for
    the reason the rest of `Origin` does: it relates two sessions and is a fact about neither on its
    own.
    """

    session: str
    turn: int
    aside: bool = False


@dataclass(frozen=True, slots=True)
class Footprint:
    """
    What one session takes on disk, as of the sweep that measured it.

    `allocated` is bytes on disk rather than the apparent size, which is what `du` prints and what
    deleting the directories would free: the two differ on a sparse file and on every file smaller
    than a block, and the question this answers is how much of the disk a session is holding.

    `measured_at` is here because the figure is as old as the last sweep, and a page saying a number
    without saying when it was true would be read as current. See `footprint.py` for why it is a
    sweep rather than a walk at render time.
    """

    allocated: int
    measured_at: datetime


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

    repository: str | None = None
    """
    Which repository this session works in, as the id a forge gave it, or nothing for none.

    Read out of the session's own `choice` rather than held in this table, which is why it is here
    at all rather than being a sixth column: it is already recorded, and a second copy would be one.
    The checkpoint is a row per key, so reaching it costs one small row per session and not a word
    of any conversation.

    An id and not a name, for the reason `Choice.repository` holds one: which repository it is is
    settled, and how to reach it is discovered. `Reachable.readable` is what turns it into the
    `owner/repo` a person recognises.
    """

    tending: Tending = field(default_factory=Tending)
    """
    What this session's plugins are set to, which is the one field here that moves.

    A default rather than a demand, because both columns behind it are nullable and `NULL` reads as
    nobody having said: a session nobody has told anything runs every plugin on the name stack's
    answer with every control on its own declared default, for as long as it lives, so moving a
    default moves every such session with it.

    It is not the second copy this table otherwise refuses. A session's own settings have to be
    mutable to be settings at all, and the two places this console keeps things both refuse them -
    the checkpoint keeps the value a key was first given, so a setting saved twice would keep its
    first answer for ever, and `localStorage` is in a browser where the worker that reads this may be
    another process entirely.
    """

    archived: datetime | None = None
    """
    When this session was archived, or nothing at all for one still being answered.

    Reached out of the checkpoint's `archived` key by the same join that reaches the repository, and
    for the same reason: it is recorded there, once, by the press, so a column here would be the
    second copy. Read per row because both readers want it per row - the sidebar to mute the row and
    the reconciler to know which sessions' directories to take off the disk.
    """

    footprint: Footprint | None = None
    """
    What this session takes on disk, as the last sweep measured it, or nothing where none has.

    Not a column and not read here: `parse_session` leaves it empty and `Service` fills it from the
    holder the sweep writes, which is a dictionary lookup per row. A reading of the filesystem
    rather than of anything said, so it is the catalogue's kind of exception and not a copy of what
    is in the checkpoint; see `footprint.py`.

    `None` is a session nothing has measured yet, which every session is between the console
    starting and its first sweep finishing, and every session is for ever on a console keeping no
    workspaces. A page draws nothing for it rather than a zero, since a zero is a claim.
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


# This table's own columns, which are the ones `enrol` writes.
COLUMNS = "id, created_at, title, forked_from, forked_at, forked_aside"

# What every read selects, spelled once so the column order and `parse_session` cannot drift.
#
# The join is what keeps the repository out of this table. A session's checkpoint is a row per key
# rather than one value, so `choice` is one small object per session and reading the field out of
# it costs a row apiece - where a column here would be a second copy of something already recorded,
# which is the one thing this console does not keep. Both tables are in the one file, so this is a
# single statement rather than a fan-out.
#
# `LEFT JOIN` because a session is enrolled before its choice is written, and that window is an
# ordinary state rather than a fault: it renders as a session working in no repository, which is
# also what a JSON `null` there means, so the two need not be told apart.
#
# The second join is the same shape for the same reason: whether a session is archived is recorded
# in its checkpoint, once, by the press, and the sidebar and the reconciler both want it per row
# without loading a conversation. Most rows join nothing there, which is a `NULL` and a session
# still being answered.
SELECTION = """
SELECT sessions.id,
       sessions.created_at,
       sessions.title,
       sessions.forked_from,
       sessions.forked_at,
       sessions.forked_aside,
       sessions.enabled,
       sessions.settings,
       json_extract(choice.value, :repository_path),
       json_extract(archived.value, :archived_path)
  FROM sessions
  LEFT JOIN workflow_checkpoint AS choice
    ON choice.workflow = sessions.id AND choice.step = :choice_key
  LEFT JOIN workflow_checkpoint AS archived
    ON archived.workflow = sessions.id AND archived.step = :archived_key
"""

# Named rather than positional, so the two statements below can add their own without counting
# question marks. They name the key scheme, which lives in `conversation.py` for exactly this
# reason: the code writing a choice and the statement reading one cannot drift apart.
SCHEME: Final = {
    "choice_key": CHOICE_KEY,
    "repository_path": f"$.{REPOSITORY_FIELD}",
    "archived_key": ARCHIVED_KEY,
    "archived_path": "$.at",
}


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
            f"INSERT OR IGNORE INTO sessions ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.created_at.isoformat(),
                session.title,
                session.forked.session if session.forked else None,
                session.forked.turn if session.forked else None,
                int(session.forked.aside) if session.forked else None,
            ),
        )
    )


async def rename(database: Database, session: str, title: str) -> None:
    """
    Name a session after the first thing said in it, which happens once and never again.

    **The one write to this column, and it is still a write of something settled.** A session is
    named when its first message arrives rather than when it is created, because creating one no
    longer carries a message; nothing renames it afterwards, so what the index holds is a copy of
    something that cannot change, which is the whole of why it may hold a title at all.

    Guarded on the column still being empty rather than on a read beside it, so two messages posted
    at once cannot have the second one win: the statement is the check.
    """
    await database.run(
        lambda connection: connection.execute(
            "UPDATE sessions SET title = ? WHERE id = ? AND title = ''", (title, session)
        )
    )


async def read_sessions(database: Database) -> tuple[Session, ...]:
    """Every session, newest first, which is the order a chat console reads in."""
    rows = await selecting(database, f"{SELECTION} ORDER BY sessions.created_at DESC, sessions.id DESC", SCHEME)
    return tuple(parse_session(row) for row in rows)


async def read_session(database: Database, session: str) -> Session | None:
    rows = await selecting(database, f"{SELECTION} WHERE sessions.id = :session", {**SCHEME, "session": session})
    return parse_session(rows[0]) if rows else None


# The two columns on their own, without the join every other read here makes. A pass wants a
# session's settings and nothing else about it, and what the join fetches is the repository, which
# the pass already has out of the recorded choice.
TENDING = "SELECT enabled, settings FROM sessions WHERE id = ?"

# One plugin's own sub-object, patched rather than the whole column rewritten. `json_patch` merges
# the fields given into whatever is there, and `json_set` puts the result back under that plugin's
# key, so two plugins saving at once each keep their save. `coalesce` is what makes it work on a
# session nobody has ever set anything on, where both the column and the sub-object are absent.
PATCH = """
UPDATE sessions
   SET settings = json_set(
           coalesce(settings, '{}'),
           '$.' || json_quote(:plugin),
           json_patch(coalesce(json_extract(settings, '$.' || json_quote(:plugin)), '{}'), json(:values))
       )
 WHERE id = :session
"""

# Which plugins a session runs, under the same rule and in the other column. A switch is the
# console's answer rather than a plugin's, so it is patched apart from the settings beside it.
SWITCH = """
UPDATE sessions
   SET enabled = json_patch(coalesce(enabled, '{}'), json(:values))
 WHERE id = :session
"""


async def read_tending(database: Database, session: str) -> Tending:
    """
    What a session's plugins are set to, as the pass that answers it reads.

    A session this console has never heard of reads as nobody having said anything rather than
    raising. Nothing here can produce one - a session is enrolled before its first pass - so what a
    raise would buy is a stalled conversation in exchange for a state that cannot arise, and the
    answer to "nobody has said" is the same answer either way.
    """

    def query(connection: sqlite3.Connection) -> Tending:
        for enabled, settings in connection.execute(TENDING, (session,)):
            return parse_tending(
                None if enabled is None else str(enabled),
                None if settings is None else str(settings),
            )
        return Tending()

    return await database.run(query)


async def set_settings(database: Database, session: str, plugin: str, values: Mapping[str, object]) -> None:
    """
    Write one plugin's settings, leaving every other plugin's exactly as they were.

    **A patch and not a whole-blob write**, which is the cost the column had to answer for: written
    whole, two plugins saving at once lose one of the saves. Nothing about the values is checked
    here, because what checks them is the plugin's own card and that is a parse the reader does; see
    `tending.parse_tending`.
    """
    if not values:
        return
    await database.run(
        lambda connection: connection.execute(
            PATCH, {"session": session, "plugin": plugin, "values": written(dict(values))}
        )
    )


async def switch(database: Database, session: str, enabled: Mapping[str, bool]) -> None:
    """
    Say which plugins a session runs, patching the switches given and leaving the rest alone.

    Settled for the session's life once anything has been asked, which this does not enforce and
    cannot: what decides that is whether a turn has been recorded, and that is a checkpoint question.
    The route that offers the control is where it is refused.
    """
    if not enabled:
        return
    await database.run(
        lambda connection: connection.execute(SWITCH, {"session": session, "values": written(dict(enabled))})
    )


type Row = tuple[str, str, str, str | None, int | None, int | None, str | None, str | None, str | None, str | None]


async def selecting(database: Database, statement: str, parameters: Mapping[str, str]) -> list[Row]:
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
                None if forked_aside is None else int(forked_aside),
                None if enabled is None else str(enabled),
                None if settings is None else str(settings),
                None if repository is None else str(repository),
                None if archived is None else str(archived),
            )
            for (
                identifier,
                created_at,
                title,
                forked_from,
                forked_at,
                forked_aside,
                enabled,
                settings,
                repository,
                archived,
            ) in connection.execute(statement, parameters)
        ]

    return await database.run(query)


def parse_session(row: Row) -> Session:
    identifier, created_at, title, forked_from, forked_at, forked_aside, enabled, settings, repository, archived = row
    return Session(
        id=identifier,
        created_at=datetime.fromisoformat(created_at),
        title=title,
        # Both or neither, which is what `Origin` exists to make true. A row holding one without
        # the other is a database somebody edited by hand, and reading it as "not a fork" is the
        # quieter wrong answer; this says so instead.
        forked=parse_origin(identifier, forked_from, forked_at, forked_aside),
        repository=repository,
        tending=parse_tending(enabled, settings),
        archived=None if archived is None else datetime.fromisoformat(archived),
    )


def parse_origin(session: str, forked_from: str | None, forked_at: int | None, aside: int | None) -> Origin | None:
    """
    Where a session came from, or nothing where it came from nowhere.

    `aside` is defaulted rather than demanded, unlike the pair above it: every fork written before
    asides existed has `NULL` there and was a plain fork, so reading it as one is ordinary parsing of
    an optional and not a guess. The other two are demanded because half an origin is a state nothing
    here can produce.
    """
    if forked_from is None and forked_at is None:
        return None
    if forked_from is None or forked_at is None:
        raise ValueError(f"session {session!r} names half an origin: {forked_from!r} at {forked_at!r}")
    return Origin(session=forked_from, turn=forked_at, aside=bool(aside))


def now_utc() -> datetime:
    return datetime.now(UTC)
