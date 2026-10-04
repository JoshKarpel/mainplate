"""
Discover skill and command names once; read their bodies only when they are used.

The index is settled for a session, while a file the model has not read can still
change. The tool return or expanded message records the bytes that were seen.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import TypeAdapter

Kind = Literal["skill", "command"]
Tier = Literal["bundled", "user", "repository"]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class Entry(BaseModel):
    """A discovered skill or command, identified independently of its changing body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    tier: Tier
    name: str
    description: str
    path: str

    @property
    def qualified(self) -> str:
        """Distinguish identical directory names installed by different tiers."""
        return f"{self.tier}:{self.name}"


CATALOGUE = TypeAdapter(tuple[Entry, ...])


class BadContext(ValueError):
    """A context entry cannot be described or read without violating its boundary."""


def beneath(root: Path, relative: Path) -> str:
    """Read repository text through directory descriptors, never following a link."""
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise BadContext(f"{relative} is not under {root}")
    opened = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in relative.parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=opened)
            os.close(opened)
            opened = following
        file = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=opened)
    finally:
        os.close(opened)
    with os.fdopen(file, "rb") as held:
        if not stat.S_ISREG(os.fstat(held.fileno()).st_mode):
            raise BadContext(f"{relative} is not a regular file")
        return held.read().decode("utf-8")


def described(text: str, path: Path) -> str:
    """A one-line frontmatter description for the index, not for the model's body."""
    if text.startswith("---\n"):
        for line in text[4:].split("\n---\n", 1)[0].splitlines():
            if line.startswith("description:") and (value := line[12:].strip().strip("\"'")):
                return value
    raise BadContext(f"{path} needs a one-line description in frontmatter")


def discover(root: Path, tier: Tier) -> tuple[Entry, ...]:
    """Find directories with entry files; directory names, not frontmatter, name leaders."""
    if tier == "repository" and root.is_symlink():
        raise BadContext(f"{root} is a link, not a directory in the checkout")
    found: list[Entry] = []
    for kind, directory, filename in (("skill", "skills", "SKILL.md"), ("command", "commands", "COMMAND.md")):
        parent = root / directory
        if tier == "repository" and parent.is_symlink():
            raise BadContext(f"{parent} is a link, not a directory in the checkout")
        if not parent.exists():
            continue
        for child in sorted(parent.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            if not NAME.fullmatch(child.name):
                raise BadContext(f"{child} is not a valid context name")
            relative = Path(directory) / child.name / filename
            path = root / relative
            if not path.exists():
                continue
            text = beneath(root, relative) if tier == "repository" else path.read_text(encoding="utf-8")
            if len(text.encode("utf-8")) > MAX_BODY:
                raise BadContext(f"{path} is larger than {MAX_BODY} bytes")
            found.append(
                Entry(kind=kind, tier=tier, name=child.name, description=described(text, path), path=str(path))
            )
    names = [entry.name for entry in found]
    if len(names) != len(set(names)):
        raise BadContext(f"{root} declares a skill and a command under the same name")
    return tuple(found)


def catalogue(config_home: Path, checkout: Path | None) -> tuple[Entry, ...]:
    """The union of bundled, operator and repository context, without running any of it."""
    roots: tuple[tuple[Tier, Path], ...] = (
        ("bundled", Path(__file__).parent / "bundled-context"),
        ("user", config_home / "mainplate"),
        *((("repository", checkout / ".mainplate"),) if checkout is not None else ()),
    )
    return tuple(entry for tier, root in roots for entry in discover(root, tier))


def parse_catalogue(raw: object) -> tuple[Entry, ...]:
    """Recover names settled by a session's first pass."""
    return CATALOGUE.validate_python(raw)


def skill_roots(entries: tuple[Entry, ...]) -> tuple[tuple[Tier, Path], ...]:
    """Only installed non-repository skill directories become readable roots."""
    return tuple(
        (tier, Path(next(item.path for item in entries if item.kind == "skill" and item.tier == tier)).parent.parent)
        for tier in ("bundled", "user")
        if any(item.kind == "skill" and item.tier == tier for item in entries)
    )


def index(entries: tuple[Entry, ...]) -> str:
    """
    Advertise skills but not commands; a skill's contents stay on demand.

    Where to read comes before the description and the description ends the row, so it is printed
    exactly as its author wrote it. A path after it would need a full stop between the two, doubling
    the punctuation of any description that brings its own.
    """
    rows = [
        f"- `{entry.qualified}` ("
        + (
            f"`{entry.path}`"
            if entry.tier == "repository"
            else f"`{entry.name}/SKILL.md` with root `{entry.tier}_skills`"
        )
        + f"): {entry.description}"
        for entry in entries
        if entry.kind == "skill"
    ]
    return (
        "Skills (read a skill's file when its description is relevant, then its supporting files as needed):\n"
        + "\n".join(rows)
        if rows
        else ""
    )


def leaders(entries: tuple[Entry, ...], reserved: frozenset[str]) -> dict[str, Entry]:
    """Expose a bare name only where no other source already claims it."""
    result = {entry.qualified: entry for entry in entries}
    for entry in entries:
        if entry.name not in reserved and sum(item.name == entry.name for item in entries) == 1:
            result[entry.name] = entry
    return result


def body(entry: Entry, checkout: Path | None) -> str:
    """Read what a manual invocation actually sends, refusing links in a checkout."""
    if entry.tier == "repository":
        if checkout is None:
            raise BadContext(f"{entry.qualified} has no checkout")
        base = Path(".mainplate") / ("skills" if entry.kind == "skill" else "commands") / entry.name
        relative = base / ("SKILL.md" if entry.kind == "skill" else "COMMAND.md")
        if Path(entry.path) != checkout / relative:
            raise BadContext(f"{entry.qualified} no longer belongs to this checkout")
        text = beneath(checkout, relative)
    else:
        text = Path(entry.path).read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > MAX_BODY:
        raise BadContext(f"/{entry.qualified} is larger than {MAX_BODY} bytes")
    return text


MAX_BODY = 100_000
"""The maximum manual expansion, so a small post cannot secretly become a huge prompt."""


def expanded(entry: Entry, text: str, input_text: str) -> str:
    """Keep the invocation's name and actual body visible in the sent message."""
    prose = text.split("\n---\n", 1)[1] if text.startswith("---\n") else text
    if len(prose.encode("utf-8")) + len(input_text.encode("utf-8")) > MAX_BODY:
        raise BadContext(f"/{entry.qualified} expands beyond {MAX_BODY} bytes")
    return f"/{entry.qualified}\n\n{prose.strip()}\n\n{input_text}".strip()
