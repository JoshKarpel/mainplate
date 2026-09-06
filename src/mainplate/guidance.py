# Where the words a session is answered under come from, and whose they are.
#
# Two scopes, and the difference between them is whose voice it is rather than where the file sits.
# **Console guidance** is the operator's own, in `<config home>/mainplate/guidance/`, and it is true
# of every session this console answers. **Repository guidance** is the project's own `AGENTS.md`,
# read out of the worktree the session works in, and it is true of one repository.
#
# Both go into the agent's `instructions`, which is the console's voice: a per-request parameter
# Pydantic AI re-renders on every request rather than a message at a position. Nothing that arrived
# over the network speaks there except the repository's own file, which is deliberate - the
# repository is where a project's knowledge accretes, and a session that could not read it would be
# answering about code it had been told nothing about.
#
# **The repository wins where the two disagree**, so it is concatenated last. That is not a claim
# about trust: it is that a repository is right about itself, which is the local-conventions rule
# said one layer out, and it is why escapement puts the gains in the repository rather than in the
# server. What the console holds is how to work; what the repository holds is what this project is.
#
# `AGENTS.md` and not a name of this console's own, because a file only mainplate can read is
# knowledge that does not survive turning mainplate off. Every other harness reads that one, so a
# line written there keeps working when the tool is gone, which is the whole reason to write it in
# the repository rather than in a prompt.

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import yaml
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.messages import ToolCallPart

from mainplate.snapshots import SnapshotFailed
from mainplate.snapshots import Worktree

GUIDANCE_NAMES: Final = ("AGENTS.md", "CLAUDE.md")
"""
The names a directory's own guidance is looked for under, in the order they win.

**One file per directory, first name wins**, rather than both concatenated. A repository carrying
both is carrying one set of instructions twice, and reading the pair puts it into the context window
twice; pi reads both and its own docs warn about exactly that. `AGENTS.md` leads because it is the
name the ecosystem converged on and the one a repository is most likely to keep current.
"""

GUIDANCE_DIRECTORY: Final = "guidance"
"""
Where the console's own guidance lives under its config home, beside `config.yaml`.

A directory of files rather than one file, because that is how doctrine is *authored*: one topic
apiece, each a thing another can cite by name. It is not a scoping mechanism and nothing here reads
one file without reading the rest.
"""


def guidance_at(here: Path) -> Path | None:
    """
    The one guidance file a directory holds, or nothing where it holds none.

    Nothing is the ordinary answer rather than a failure: most directories have no guidance, and a
    repository with none at all is a repository this console has simply not been told anything
    about.
    """
    for name in GUIDANCE_NAMES:
        found = here / name
        if found.is_file():
            return found
    return None


def frontmatter(text: str) -> str | None:
    """
    The YAML block a guidance file opens with, or nothing where it opens with prose.

    The other half of `without_frontmatter`, and deliberately the same two conditions: a block that
    opens the file and closes. Split rather than one function returning a pair, because every caller
    wants one or the other and a pair would have both of them discarding half.
    """
    if not text.startswith("---\n"):
        return None
    closed = text.find("\n---", 3)
    if closed == -1:
        return None
    return text[4:closed]


def described(text: str) -> str | None:
    """
    What a guidance file says it is about, in one line, for the index that lists it.

    Authored rather than generated: a summary this console wrote would be a second copy of the file
    that drifts the moment somebody edits it, where a `description` in the file's own frontmatter is
    a thing its author kept current with the rest of it. Absent is the ordinary answer, since most
    `AGENTS.md` files in the world carry no frontmatter at all, and the path alone is already most of
    what the index is for.

    Anything but a plain string is nothing rather than an error, because a repository is free to put
    whatever it likes there and a session that could not be answered because of it would be a console
    broken by somebody else's file.
    """
    block = frontmatter(text)
    if block is None:
        return None
    try:
        document = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    said = document.get("description")
    return said.strip() if isinstance(said, str) and said.strip() else None


def without_frontmatter(text: str) -> str:
    """
    A guidance file's prose, with a leading YAML block taken off.

    Frontmatter is addressed to whatever loads the file rather than to the model, so passing it on
    spends a context window on a `paths:` list nobody reads and invites the model to answer about
    it. Only a block that *opens* the file counts, and only one that closes, so a document whose
    first line happens to be a rule of dashes is left exactly as it was written.
    """
    if not text.startswith("---\n"):
        return text
    closed = text.find("\n---", 3)
    if closed == -1:
        return text
    return text[closed:].removeprefix("\n---").lstrip("\n")


def readable(path: Path) -> str | None:
    """
    What a file says, or nothing where it cannot be read as text.

    Nothing rather than a raise, because every caller here is answering "what has this console been
    told", and a file that is unreadable this second is not a reason a session cannot be answered.
    A repository is free to commit a symlink to nowhere.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def console_guidance(config_home: Path) -> str:
    """
    Everything the operator has written for every session, as one block of prose.

    Sorted by path so that two consoles reading one directory compose it identically, and recursive
    so the directory can be organised without this having to be taught how. Files rather than one
    file is an authoring convenience only: all of it loads, always, because doctrine that loads
    conditionally is doctrine that is absent exactly when nothing said it was.
    """
    found = sorted((config_home / "mainplate" / GUIDANCE_DIRECTORY).glob("**/*.md"))
    said = (without_frontmatter(text).strip() for path in found if (text := readable(path)) is not None)
    return "\n\n".join(block for block in said if block)


def repository_guidance(root: Path) -> str | None:
    """
    What the repository says about itself, named so the model knows whose words these are.

    The name is carried into the text rather than left implicit, because the model can open the file
    and should know which one it is already holding. It is also what keeps the block honest about
    being the project's rather than this console's, which matters where the two disagree.
    """
    found = guidance_at(root)
    if found is None:
        return None
    said = readable(found)
    if said is None:
        return None
    body = without_frontmatter(said).strip()
    if not body:
        return None
    return f"`{found.name}`, this repository's own guidance:\n\n{body}"


async def guidance_under(worktree: Worktree) -> tuple[Path, ...]:
    """
    Every guidance file in the repository below its root, as paths relative to it.

    **Asked of git rather than walked**, for the reason `list` asks: a walk here would descend a
    `.venv` and a `node_modules` looking for a file that is never in either. The pathspecs keep the
    answer to the handful of files that matter however large the repository is, and `--others` is
    what makes one the agent itself just wrote appear.

    Sorted, so an index composed twice is the same index. The root's own file is left out because it
    is already in the instructions whole; what this is for is the ones that are not.

    **One file per directory, the same first-name-wins rule `guidance_at` applies**, because git
    answers with every path matching either pathspec. A repository that pairs an `AGENTS.md` with a
    `CLAUDE.md` importing it - which is how this one wires Claude Code to the file every other
    harness reads - would otherwise index each directory twice, and the second row would point at a
    file whose whole content is a line naming the first.
    """
    spec = tuple(f"*/{name}" for name in GUIDANCE_NAMES)
    try:
        said = await worktree.demand("ls-files", "--cached", "--others", "--exclude-standard", "-z", *spec)
    except SnapshotFailed:
        # `forge.offers`'s promise rather than `catalogue.discover`'s refusal: a repository that
        # cannot be listed costs an index rather than the ability to answer the session at all.
        return ()
    held = {Path(each) for each in said.split("\0") if each}
    return tuple(
        sorted(
            next(chosen for name in GUIDANCE_NAMES if (chosen := directory / name) in held)
            for directory in {each.parent for each in held}
        )
    )


def indexing(worktree: Path, found: Sequence[Path]) -> str | None:
    """
    One line per guidance file elsewhere in the repository, and nothing where there are none.

    **The index is identity and the file is the content**, which is the whole shape of this: that
    `apps/web` has conventions is one line and what they are is a page, so the line rides in the
    instructions on every request and the page is read when it is wanted. It is also the half that
    serves the goal path scoping never did, which is knowing that a part of the repository *has*
    rules before reaching into it and breaking them.

    Nothing where a repository has none, which is most of them: a heading over an empty list reports
    a feature rather than a fact.
    """
    rows = []
    for each in sorted(found):
        text = readable(worktree / each)
        said = None if text is None else described(text)
        rows.append(f"- `{each}`{f': {said}' if said else ''}")
    if not rows:
        return None
    return "\n".join(
        (
            "Other parts of this repository carry their own guidance. Read the one covering a "
            "directory before you change much in it:",
            "",
            *rows,
        )
    )


def heading(found: Path) -> str:
    """
    The line a delivered block opens with, which is also how a later request knows it was delivered.

    One string built in one place, because it is read back: the predicate below asks whether the
    model has already been handed this file, and it answers by looking for this exact line in what
    the model will be handed. Built at both ends separately they would drift and every approach
    would deliver the same guidance again.
    """
    return f"`{found}`, guidance for this part of the repository:"


def touched_in(messages: Sequence[ModelMessage]) -> tuple[str, ...]:
    """
    Every path the model has named to a file tool, in the order it named them.

    Read off the calls rather than off anything recorded beside them, which is what makes this free:
    a call is already in the history for its own sake. A call naming a `root` is passed over, since
    the only root that is not the worktree is the scratch and nothing there is the repository's.

    `bash` is not here and cannot be: its argv is the model's, so a path inside it is a string this
    console has no business parsing. That is the hole the index exists to cover.
    """
    found: list[str] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            arguments = part.args_as_dict()
            path = arguments.get("path")
            if isinstance(path, str) and not arguments.get("root"):
                found.append(path)
    return tuple(found)


def covering(root: Path, path: str) -> tuple[Path, ...]:
    """
    The guidance files that apply to one path, from the top down, as paths relative to the root.

    The repository's own root file is left out because it is already in the instructions whole. What
    is left is every directory between there and the file, which is the ecosystem's own reading of
    nested `AGENTS.md` and is why nesting needs no mechanism of this console's: a repository states
    which of its rules are local by where it puts them.

    A path that climbs out of the repository yields nothing rather than raising. It cannot arrive
    from a tool, which refuses one long before this, but this reads what a model wrote and a value
    that cannot happen is not one to crash on.
    """
    here = (root / path).resolve()
    if root.resolve() not in here.parents:
        return ()
    found: list[Path] = []
    for each in reversed(here.parents):
        if each == root.resolve():
            continue
        guidance = guidance_at(each)
        if guidance is not None:
            found.append(guidance.relative_to(root.resolve()))
    return tuple(found)


def delivered_in(messages: Sequence[ModelMessage], said: str) -> bool:
    """
    Whether the model has already been handed this, asked of the history it is about to be handed.

    **The history is the ledger**, which is what makes a set kept anywhere else wrong rather than
    merely redundant: one on the session would survive a `forget` and leave the model working
    without guidance it can no longer see, and one on the pass would deliver again on every pass.
    Asked here, every case answers itself - the console delivered it, the model read the file for
    itself, the model wrote the file, a fork carried it across, a forget dropped it.
    """
    return any(
        isinstance(part, SystemPromptPart) and part.content.startswith(said)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def approaching(root: Path, messages: Sequence[ModelMessage]) -> tuple[str, ...]:
    """
    What to hand the model, given where it has been reaching and what it has already been told.

    Delivered on the request *after* the calls that reached in, which is the same round trip the
    tool results themselves arrive on: there is no earlier moment, since a batch of calls and the
    reply to them are one exchange. So the model has read one file without the local guidance and
    has not yet done the next fifteen things.
    """
    blocks: list[str] = []
    said: set[Path] = set()
    for path in touched_in(messages):
        for found in covering(root, path):
            if found in said or delivered_in(messages, heading(found)):
                continue
            body = readable(root / found)
            if body is None or not without_frontmatter(body).strip():
                continue
            said.add(found)
            blocks.append(f"{heading(found)}\n\n{without_frontmatter(body).strip()}")
    return tuple(blocks)


def instructing(*blocks: str | None) -> str:
    """
    The instructions one request carries, composed from every scope that had something to say.

    In order of increasing specificity, so the last word belongs to whatever is most local. Empty
    blocks are dropped rather than joined, so a console with no guidance and a repository with no
    `AGENTS.md` produce exactly what this console produced before either existed.
    """
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())
