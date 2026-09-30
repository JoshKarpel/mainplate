"""Independent HTML artifacts: immutable bytes and a mutable current-version pointer."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

from without_durability_sqlite import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    current_version INTEGER NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS artifact_versions (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    version INTEGER NOT NULL,
    html BLOB NOT NULL,
    created_at TEXT NOT NULL,
    operation TEXT NOT NULL UNIQUE,
    PRIMARY KEY (artifact_id, version)
) STRICT;
CREATE INDEX IF NOT EXISTS artifact_versions_by_artifact
ON artifact_versions (artifact_id, version DESC);
"""
MAX_HTML = 2 * 1024 * 1024
PAGE_SIZE = 30
MAX_TITLE = 120
MAX_QUERY = 120


class Refused(ValueError):
    """An artifact operation cannot proceed without changing the caller's request."""


@dataclass(frozen=True, slots=True)
class Version:
    """One immutable version, with its identity and creation date."""

    id: str
    title: str
    version: int
    created_at: str


async def prepare(database: Database) -> None:
    """Install the artifact tables alongside, not inside, the checkpoint schema."""
    await database.run(lambda connection: connection.executescript(SCHEMA))


async def import_html(
    database: Database,
    html: bytes,
    operation: str,
    artifact_id: str | None = None,
    expected_version: int | None = None,
    title: str | None = None,
) -> Version:
    """Advance the current version once, atomically; replaying an operation returns its first result."""
    if len(html) > MAX_HTML:
        raise Refused("HTML is too large (2 MiB maximum)")
    try:
        html.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Refused("HTML must be UTF-8") from error
    if not html.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise Refused("The file must be a self-contained HTML document")
    if artifact_id is None and expected_version is not None:
        raise Refused("A new artifact has no expected version")
    if artifact_id is not None and expected_version is None:
        raise Refused("Specify expected_version when updating an artifact")
    if expected_version is not None and expected_version < 1:
        raise Refused("expected_version must be positive")
    if title is not None and (not title.strip() or len(title) > MAX_TITLE):
        raise Refused("Title must be nonempty and at most 120 characters")
    new_id = artifact_id or secrets.token_urlsafe(16)
    created_at = datetime.now(UTC).isoformat()

    def write(connection: sqlite3.Connection) -> Version:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT v.artifact_id, a.title, v.version, v.created_at, v.html FROM artifact_versions v "
                "JOIN artifacts a ON a.id = v.artifact_id WHERE v.operation = ?",
                (operation,),
            ).fetchone()
            if existing is not None:
                result = Version(*existing[:4])
                if bytes(existing[4]) != html or (artifact_id is not None and artifact_id != result.id):
                    raise Refused("This tool call was already used for different HTML")
            else:
                if artifact_id is None:
                    connection.execute(
                        "INSERT INTO artifacts (id, title, current_version) VALUES (?, ?, 1)",
                        (new_id, title or "Untitled artifact"),
                    )
                    version = 1
                else:
                    updated = connection.execute(
                        "UPDATE artifacts SET current_version = current_version + 1, title = COALESCE(?, title) "
                        "WHERE id = ? AND current_version = ? RETURNING current_version",
                        (title, artifact_id, expected_version),
                    ).fetchone()
                    if updated is None:
                        raise Refused("Artifact does not exist or expected_version is no longer current")
                    version = int(updated[0])
                connection.execute(
                    "INSERT INTO artifact_versions (artifact_id, version, html, created_at, operation) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (new_id, version, html, created_at, operation),
                )
                actual_title = connection.execute("SELECT title FROM artifacts WHERE id = ?", (new_id,)).fetchone()[0]
                result = Version(new_id, str(actual_title), version, created_at)
            connection.execute("COMMIT")
            return result
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    return await database.run(write)


async def completed(database: Database, operation: str) -> Version | None:
    """Return a committed import before reading a file that may have changed on replay."""

    def lookup(connection: sqlite3.Connection) -> Version | None:
        row = connection.execute(
            "SELECT v.artifact_id, a.title, v.version, v.created_at FROM artifact_versions v "
            "JOIN artifacts a ON a.id = v.artifact_id WHERE v.operation = ?",
            (operation,),
        ).fetchone()
        return None if row is None else Version(*row)

    return await database.run(lookup)


async def content(database: Database, artifact_id: str, version: int | None = None) -> tuple[Version, bytes] | None:
    """Fetch exactly the bytes of the selected version, defaulting to the current version."""

    def lookup(connection: sqlite3.Connection) -> tuple[Version, bytes] | None:
        row = connection.execute(
            "SELECT a.id, a.title, v.version, v.created_at, v.html FROM artifacts a "
            "JOIN artifact_versions v ON v.artifact_id = a.id "
            "AND v.version = COALESCE(?, a.current_version) WHERE a.id = ?",
            (version, artifact_id),
        ).fetchone()
        return None if row is None else (Version(*row[:4]), bytes(row[4]))

    return await database.run(lookup)


async def listing(
    database: Database,
    query: str | None = None,
    artifact_id: str | None = None,
    cursor: int | None = None,
) -> tuple[Version, ...]:
    """Return bounded metadata; cursor is a version with an id or an offset without one."""
    if cursor is not None and cursor < (1 if artifact_id else 0):
        raise Refused("Cursor must be nonnegative (positive for version history)")

    if query is not None and len(query) > MAX_QUERY:
        raise Refused("Query must be at most 120 characters")

    def lookup(connection: sqlite3.Connection) -> tuple[Version, ...]:
        rows = connection.execute(
            "SELECT a.id, a.title, v.version, v.created_at FROM artifacts a "
            "JOIN artifact_versions v ON v.artifact_id = a.id "
            "WHERE (? IS NULL OR a.id = ?) AND (? IS NULL OR a.title LIKE '%' || ? || '%') "
            "AND (? IS NULL OR v.version < ?) "
            "AND (? IS NOT NULL OR v.version = a.current_version) "
            "ORDER BY v.created_at DESC, a.id, v.version DESC LIMIT ? OFFSET ?",
            (
                artifact_id,
                artifact_id,
                query,
                query,
                cursor if artifact_id else None,
                cursor if artifact_id else None,
                artifact_id,
                PAGE_SIZE,
                0 if artifact_id else cursor or 0,
            ),
        ).fetchall()
        return tuple(Version(*row) for row in rows)

    return await database.run(lookup)
