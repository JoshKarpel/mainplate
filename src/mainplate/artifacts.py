# The console's store of artifacts: HTML documents a session made, kept for as long as the console is.
#
# Two tables beside the checkpoint rather than anything inside it, which is the one decision here
# worth arguing, and `PHILOSOPHY.md` argues it: a version is settled the moment it is written and
# nothing rewrites it, and the two things that move - which version is current, and what the
# artifact is called - are facts nothing else records. So neither table is a copy of anything said.
# What a session *said* about an artifact stays in its checkpoint, as the call that made a version
# and the metadata that call recorded; this is the other end of that call, and each version names
# the call that made it so the two ends can find each other.
#
# Beside the checkpoint rather than in it for the reason the fork cannot carry an artifact: an
# artifact outlives the session that made it, is updated from other sessions, and is served to a
# browser by its own address. A value inside one session's checkpoint could do none of those.

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from without_durability_sqlite import Database

# `current` is a pointer into `artifact_versions`, moved only by `keep` under a write lock, and
# `title` moves with it when a new version names one. Everything in `artifact_versions` is written
# once. `seq` is the order versions were kept in across every artifact, which is what the listings
# page by: one integer a link can carry, where a moment would need a tiebreak beside it.
#
# `(session, turn, call)` is unique because it is the tool call's own key in the checkpoint, and it is
# what makes keeping a version idempotent: a pass that falls over after the version is written and
# before the call's return is recorded runs the call again, and finds the version it already kept.
SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS artifacts (
    id      TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    current INTEGER NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS artifact_versions (
    seq      INTEGER PRIMARY KEY,
    artifact TEXT NOT NULL REFERENCES artifacts (id),
    version  INTEGER NOT NULL,
    html     BLOB NOT NULL,
    made_at  TEXT NOT NULL,
    session  TEXT NOT NULL,
    turn     INTEGER NOT NULL,
    call     TEXT NOT NULL,
    UNIQUE (artifact, version),
    UNIQUE (session, turn, call)
) STRICT;
"""

# Long enough that an id is not guessable, for the reason a session's is: the address is the whole of
# what separates one artifact from another on a console with no accounts.
ID_BYTES: Final = 16

# The most one version may be. A self-contained page carries its scripts, styles and images inline,
# so this is generous for a page and small for a database row that every listing steps over.
LARGEST: Final = 2 * 1024 * 1024

# How many versions one listing holds, for the model and for a page alike.
LISTED: Final = 30

LONGEST_TITLE: Final = 120

# What an artifact kept without a title is called, until a later version names one.
UNTITLED: Final = "Untitled artifact"

# What a document has to open with to be kept as one, compared case-blind after leading whitespace.
OPENINGS: Final = (b"<!doctype html", b"<html")


class Refused(ValueError):
    """What this store turns down, worded for the model that asked, since every one is correctable."""


@dataclass(frozen=True, slots=True)
class Html:
    """
    A document this store will keep: UTF-8, at most `LARGEST` bytes, and opening as an HTML document.

    Parsed once, where bytes arrive, so `keep` takes a value that is already a document rather than
    checking again. The bytes are kept exactly as they arrived, byte order mark and line endings
    included, because the promise is that the preview and the download are the same file.
    """

    raw: bytes

    @classmethod
    def parse(cls, raw: bytes) -> Html:
        if len(raw) > LARGEST:
            raise Refused(f"the file is {len(raw)} bytes, and an artifact may be at most {LARGEST}")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Refused("the file is not UTF-8") from error
        if not raw.removeprefix(b"\xef\xbb\xbf").lstrip().lower().startswith(OPENINGS):
            raise Refused("the file is not an HTML document: it has to open with <!doctype html> or <html>")
        return cls(raw=raw)


@dataclass(frozen=True, slots=True)
class Call:
    """The tool call a version was made by, as the checkpoint keys it: which session, which turn, which call."""

    session: str
    turn: int
    call: str


@dataclass(frozen=True, slots=True)
class Updating:
    """
    Which artifact a new version goes onto, and which version the caller last saw as current.

    One value rather than two optional arguments, because each is meaningless without the other: an
    update that names no expected version is the lost update this exists to refuse, and an expected
    version with no artifact names nothing.
    """

    artifact: str
    expected: int


@dataclass(frozen=True, slots=True)
class Version:
    """
    One version of one artifact, as every listing and page reads it.

    `title` and `current` are the artifact's as they stand now, not as they stood when this version
    was kept, since those are the two things that move: a page drawing an older version says so by
    comparing the two numbers.
    """

    artifact: str
    title: str
    version: int
    current: int
    made_at: datetime
    made_by: Call
    seq: int

    @property
    def is_current(self) -> bool:
        return self.version == self.current


# What every read selects, spelled once so the column order and `parse_version` cannot drift.
COLUMNS: Final = "a.id, a.title, v.version, a.current, v.made_at, v.session, v.turn, v.call, v.seq"
JOINED: Final = "FROM artifact_versions v JOIN artifacts a ON a.id = v.artifact"
SELECTED: Final = f"SELECT {COLUMNS} {JOINED}"


def parse_version(row: tuple[object, ...]) -> Version:
    artifact, title, version, current, made_at, session, turn, call, seq = row
    return Version(
        artifact=str(artifact),
        title=str(title),
        version=int(str(version)),
        current=int(str(current)),
        made_at=datetime.fromisoformat(str(made_at)),
        made_by=Call(session=str(session), turn=int(str(turn)), call=str(call)),
        seq=int(str(seq)),
    )


def mint_artifact_id() -> str:
    return secrets.token_hex(ID_BYTES)


def titled(title: str) -> str:
    """A title as the store keeps it, or a refusal saying what a title may be."""
    stripped = title.strip()
    if not stripped or len(stripped) > LONGEST_TITLE:
        raise Refused(f"a title is between 1 and {LONGEST_TITLE} characters")
    return stripped


def folded(text: str | None) -> str | None:
    """
    Text as the catalogue's search compares it, which is Python's `casefold` and not SQLite's `lower`.

    SQLite's own `lower` folds ASCII and nothing else, so a search for `über` would miss `Über Plan`.
    `None` passes through because SQLite may call this on a `NULL` query even where the `IS NULL`
    beside it has already answered.
    """
    return None if text is None else text.casefold()


# The name `folded` is registered under on the connection, which `catalogue` calls it by.
FOLDED: Final = "casefold"


async def prepare(database: Database) -> None:
    """
    Create the two tables, beside the checkpoint's and the session index, and teach the connection
    `folded`; idempotent, run every boot.

    A function registered on the connection rather than anything in the schema, so it lasts as long as
    the connection does, and every read here goes through the one connection this is handed.
    """

    def prepared(connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)
        connection.create_function(FOLDED, 1, folded, deterministic=True)

    await database.run(prepared)


async def keep(
    database: Database,
    html: Html,
    made_by: Call,
    made_at: datetime,
    onto: Updating | None = None,
    title: str | None = None,
    mint: Callable[[], str] = mint_artifact_id,
) -> Version:
    """
    Keep one version, as a new artifact or as the next version of `onto`, and move `current` to it.

    `mint` names a new artifact. Handed in rather than called here so the seeder can plant the
    gallery's artifacts under the ids its fixture calls already recorded; everything else takes the
    default.

    **Once per call.** A call that has already kept a version gets that version back and writes
    nothing, which is what a pass re-running a call after falling over needs; a call that already
    kept *different* bytes is refused, since that is a caller reusing an identity rather than a
    replay. Looked up inside the same write lock as the write, so two passes cannot both miss it.

    **An update names the version it read.** One that is no longer current is refused and changes
    nothing, so two sessions updating one artifact cannot silently overwrite each other: the second
    is told to look again.
    """
    kept_title = None if title is None else titled(title)

    def write(connection: sqlite3.Connection) -> Version:
        connection.execute("BEGIN IMMEDIATE")
        try:
            found = connection.execute(
                "SELECT artifact, html FROM artifact_versions WHERE session = ? AND turn = ? AND call = ?",
                (made_by.session, made_by.turn, made_by.call),
            ).fetchone()
            if found is not None:
                if bytes(found[1]) != html.raw or (onto is not None and onto.artifact != found[0]):
                    raise Refused("this call has already kept a different version")
            elif onto is None:
                artifact = mint()
                connection.execute(
                    "INSERT INTO artifacts (id, title, current) VALUES (?, ?, 1)",
                    (artifact, kept_title or UNTITLED),
                )
                inserted(connection, artifact, 1, html, made_by, made_at)
            else:
                moved = connection.execute(
                    "UPDATE artifacts SET current = current + 1, title = coalesce(?, title) "
                    "WHERE id = ? AND current = ? RETURNING current",
                    (kept_title, onto.artifact, onto.expected),
                ).fetchone()
                if moved is None:
                    raise stale(connection, onto)
                inserted(connection, onto.artifact, int(moved[0]), html, made_by, made_at)
            row = connection.execute(
                f"{SELECTED} WHERE v.session = ? AND v.turn = ? AND v.call = ?",
                (made_by.session, made_by.turn, made_by.call),
            ).fetchone()
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return parse_version(row)

    return await database.run(write)


def inserted(
    connection: sqlite3.Connection, artifact: str, version: int, html: Html, made_by: Call, made_at: datetime
) -> None:
    connection.execute(
        "INSERT INTO artifact_versions (artifact, version, html, made_at, session, turn, call) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (artifact, version, html.raw, made_at.isoformat(), made_by.session, made_by.turn, made_by.call),
    )


def stale(connection: sqlite3.Connection, onto: Updating) -> Refused:
    """Why an update was turned down, which is one of two things and the model needs to know which."""
    held = connection.execute("SELECT current FROM artifacts WHERE id = ?", (onto.artifact,)).fetchone()
    if held is None:
        return Refused(f"there is no artifact {onto.artifact}")
    return Refused(
        f"artifact {onto.artifact} is at version {held[0]}, not {onto.expected}: "
        "look at what changed before keeping another version onto it"
    )


async def made(database: Database, made_by: Call) -> Version | None:
    """The version one call kept, if it kept one: asked before the call reads its file, so a replay reads nothing."""

    def read(connection: sqlite3.Connection) -> Version | None:
        row = connection.execute(
            f"{SELECTED} WHERE v.session = ? AND v.turn = ? AND v.call = ?",
            (made_by.session, made_by.turn, made_by.call),
        ).fetchone()
        return None if row is None else parse_version(row)

    return await database.run(read)


# Which version `version_of` and `content` read: the one named, or the current one where none is.
PINNED: Final = "a.id = ? AND v.version = coalesce(?, a.current)"


async def version_of(database: Database, artifact: str, version: int | None = None) -> Version | None:
    """
    One version without its bytes, the current one where no version is named.

    What a page about a version needs, which is everything but the document: the frame fetches that
    by its own address, so reading it here as well would carry up to `LARGEST` through the one
    connection every other request is queued on, for nothing.
    """

    def read(connection: sqlite3.Connection) -> Version | None:
        row = connection.execute(f"{SELECTED} WHERE {PINNED}", (artifact, version)).fetchone()
        return None if row is None else parse_version(row)

    return await database.run(read)


async def content(database: Database, artifact: str, version: int | None = None) -> tuple[Version, bytes] | None:
    """One version and its exact bytes, the current one where no version is named."""

    def read(connection: sqlite3.Connection) -> tuple[Version, bytes] | None:
        row = connection.execute(f"SELECT v.html, {COLUMNS} {JOINED} WHERE {PINNED}", (artifact, version)).fetchone()
        return None if row is None else (parse_version(row[1:]), bytes(row[0]))

    return await database.run(read)


async def catalogue(
    database: Database, before: int | None = None, query: str | None = None, limit: int = LISTED
) -> tuple[Version, ...]:
    """
    Every artifact's current version, most recently kept first, `limit` at a time.

    `before` is the `seq` of the last one a previous listing held. `query` is a substring of the title,
    compared after `folded`, rather than a `LIKE` pattern, so a title with `%` or `_` in it is searched
    for as written. `limit` is `LISTED` for a listing somebody pages through, and less for a section
    that only ever shows the first few.
    """

    def read(connection: sqlite3.Connection) -> tuple[Version, ...]:
        rows = connection.execute(
            f"{SELECTED} WHERE v.version = a.current AND (? IS NULL OR v.seq < ?) "
            f"AND (? IS NULL OR instr({FOLDED}(a.title), {FOLDED}(?)) > 0) ORDER BY v.seq DESC LIMIT ?",
            (before, before, query, query, limit),
        ).fetchall()
        return tuple(map(parse_version, rows))

    return await database.run(read)


async def history(database: Database, artifact: str, before: int | None = None) -> tuple[Version, ...]:
    """One artifact's versions, newest first, `LISTED` at a time; `before` is the last version a previous listing held."""

    def read(connection: sqlite3.Connection) -> tuple[Version, ...]:
        rows = connection.execute(
            f"{SELECTED} WHERE a.id = ? AND (? IS NULL OR v.version < ?) ORDER BY v.version DESC LIMIT ?",
            (artifact, before, before, LISTED),
        ).fetchall()
        return tuple(map(parse_version, rows))

    return await database.run(read)
