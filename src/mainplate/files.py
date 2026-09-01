# The file tools, which is the half of anchored editing that touches a disk.
#
# `anchors.py` is the pure core: it takes lines and operations and returns lines. This is the shell
# around it, and it owns the three things that are not pure - where the file is, what encoding and
# line endings it had, and how a refusal reaches the model.
#
# **Every path is resolved inside one worktree and refused outside it.** A session's files are its
# own linked worktree, so the root is a real boundary rather than a convention: `..`, an absolute
# path, and a symlink pointing out of the tree are all the same mistake and all get the same answer.
# `Path.resolve` is what makes the symlink case work, since it is the only check that follows one.
#
# **A refusal is a `ModelRetry`, not an exception.** Everything a tool turns down here is something
# the model can fix by trying again with different arguments: an anchor that has moved, a `find`
# that occurs twice, a batch whose operations overlap. Raising `ModelRetry` puts the sentence in
# front of the model as the result of its own call, which is where it can act on it; raising
# anything else would fail the turn over a typo. The sentences are written to be read by whoever
# has to fix them, which is why they say what to do rather than naming a code.
#
# **There is a `create` and there is no `write`.** A tool that overwrites a whole file is the escape
# hatch that makes anchored editing pointless: the first refused edit becomes a full rewrite, which
# costs the whole file in output tokens and silently discards anything the model had not read.
# `create` refuses an existing path, so making a new file and changing an existing one stay
# different operations with different risks.

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from mainplate.anchors import Anchored
from mainplate.anchors import EditRefused
from mainplate.anchors import Moved
from mainplate.anchors import Operation
from mainplate.anchors import Written
from mainplate.anchors import written

# The most a single read will show without being asked for more. A whole file is the common case and
# the right default, so this is a bound on the pathological one rather than a page size: a generated
# lockfile read in full would crowd out the conversation it was read for.
MAX_LINES: Final = 1500

# The largest file this will decode at all. Well above any source file and well below anything that
# would be worth loading into a conversation, so the refusal lands on a database or a binary that
# slipped past the decode check rather than on anything anybody meant to read.
MAX_BYTES: Final = 2 * 1024 * 1024

# How many times a model may be told it got a call wrong before the turn fails.
#
# Above Pydantic AI's default of one, because a refusal here is *designed* to be corrected: a stale
# anchor, a `find` that occurs twice, a batch whose operations overlap are all things the message
# says how to fix, and one attempt is not enough to make that promise good. Observed rather than
# guessed at: a smaller model got the operation shape wrong on its first call and the default limit
# turned a correctable mistake into a failed turn.
RETRIES: Final = 3


class Refused(ValueError):
    """
    A tool was asked for something about the file *itself* that it will not do.

    Separate from `EditRefused`, which is about an edit not resolving, because these are the
    questions asked before any edit is considered: a path outside the worktree, a file that is not
    text, one too large to read. Both reach the model the same way and the split is for whoever
    reads this code rather than for whoever reads the message.
    """


@dataclass(frozen=True, slots=True)
class Text:
    """
    A file as lines, with the two things `splitlines` throws away kept alongside.

    Both matter for a tool that writes the file back. A file with CRLF endings rewritten with LF
    is a diff on every line of it, attributed to an edit that touched one; a file that ended
    without a newline and gains one is a diff on its last line. Neither is anything the model
    asked for, and both are invisible in the reply that reports the edit.
    """

    lines: tuple[str, ...]
    newline: str
    ends_with_newline: bool

    @classmethod
    def of(cls, content: str) -> Text:
        """
        Split on real line breaks only, which is `split` and deliberately not `splitlines`.

        `str.splitlines` also breaks on form feed, vertical tab, and a handful of Unicode
        separators. A form feed is a page break some source files genuinely use, so splitting on
        one and rejoining with a newline would silently edit a file nobody touched.
        """
        body = content.replace("\r\n", "\n")
        ends = body.endswith("\n")
        lines = body.split("\n")
        return cls(
            lines=tuple(lines[:-1] if ends else lines),
            newline="\r\n" if "\r\n" in content else "\n",
            ends_with_newline=ends,
        )

    def rejoined(self, lines: Sequence[str]) -> str:
        """New lines back into a file, keeping the endings and the final newline this one had."""
        return self.newline.join(lines) + (self.newline if self.ends_with_newline else "")


@dataclass(frozen=True, slots=True)
class Files:
    """
    One session's worktree, as the three things a model may do to it.

    Frozen and holding one path, so it is a value rather than a handle: every method is an effect
    against the filesystem, and two callers sharing one share no state.
    """

    root: Path

    def resolved(self, path: str) -> Path:
        """
        Where `path` actually is, or a refusal if that turns out to be outside the worktree.

        Resolved before it is compared, which is the whole of the check: `..` collapses, an
        absolute path replaces the root outright under `/`, and a symlink is followed to whatever
        it really points at. Comparing the unresolved join would pass all three.
        """
        root = self.root.resolve()
        here = (root / path).resolve()
        if here != root and root not in here.parents:
            raise Refused(f"{path!r} is outside this session's workspace, which is the only place these tools reach")
        return here

    def loaded(self, path: str) -> tuple[Path, Text]:
        here = self.resolved(path)
        if not here.exists():
            raise Refused(f"there is no file at {path!r}")
        if here.is_dir():
            raise Refused(f"{path!r} is a directory, not a file")
        if here.stat().st_size > MAX_BYTES:
            raise Refused(f"{path!r} is larger than {MAX_BYTES // (1024 * 1024)}MiB, which is too large to read here")
        try:
            # `newline=""` turns off universal newlines, which would otherwise translate every
            # `\r\n` to `\n` on the way in. `Text` exists to carry those endings back out again,
            # and with the translation left on there would be nothing left for it to notice.
            content = here.read_text(encoding="utf-8", newline="")
        except UnicodeDecodeError:
            raise Refused(f"{path!r} is not UTF-8 text, so it has no lines to anchor") from None
        return here, Text.of(content)

    async def read(self, path: str, offset: int, limit: int) -> str:
        _, text = await asyncio.to_thread(self.loaded, path)
        anchored = Anchored.over(text.lines)
        start = max(0, offset - 1)
        stop = min(len(text.lines), start + max(1, limit))
        return "\n".join((reading(path, len(text.lines), start, stop), "", anchored.rendered(start, stop)))

    async def edit(self, path: str, operations: Sequence[Operation]) -> str:
        found, text = await asyncio.to_thread(self.loaded, path)
        done = written(Anchored.over(text.lines), operations)
        # `newline=""` again, so the endings `Text` just put back are written as they are rather
        # than translated a second time on the way out.
        await asyncio.to_thread(found.write_text, text.rejoined(done.lines), encoding="utf-8", newline="")
        return reported(path, done)

    async def create(self, path: str, content: str) -> str:
        here = self.resolved(path)
        if here.exists():
            raise Refused(f"{path!r} already exists; `create` never overwrites, so edit it instead")
        text = Text.of(content if content.endswith("\n") else content + "\n")

        def write() -> None:
            here.parent.mkdir(parents=True, exist_ok=True)
            here.write_text(text.rejoined(text.lines), encoding="utf-8", newline="")

        await asyncio.to_thread(write)
        anchored = Anchored.over(text.lines)
        return "\n".join((f"created {path}, {counted(len(text.lines))}", "", anchored.rendered(0, MAX_LINES)))


def counted(lines: int) -> str:
    return f"{lines} line" if lines == 1 else f"{lines} lines"


def reading(path: str, total: int, start: int, stop: int) -> str:
    """What a read says about itself, which is where in the file it stopped and whether it did."""
    if start == 0 and stop >= total:
        return f"{path}, {counted(total)}"
    return f"{path}, lines {start + 1}-{stop} of {total}; pass `offset` to read further"


def remapping(moved: Moved) -> str:
    return f"  {moved.was} is now {moved.now}   {moved.line.strip()[:60]}"


def reported(path: str, done: Written) -> str:
    """
    What an edit hands back, which is everything needed to keep editing without reading again.

    Three parts, and the third is the one that is easy to leave out and expensive to. The changed
    regions come back with fresh anchors so the next operation can be written against them. The
    remapping covers what those regions do not: an anchor takes in the lines above it when its own
    content is not unique, so an edit can rename an untouched line elsewhere in the file, and an
    anchor the model still believes in is worth one line here rather than a whole re-read later.
    """
    parts = [f"edited {path}, now {counted(len(done.lines))}", ""]
    for start, stop in done.regions:
        parts.append(f"lines {start + 1}-{stop}:")
        parts.append(done.after.rendered(start, stop))
        parts.append("")
    if done.remapped:
        parts.append("anchors that moved elsewhere in the file:")
        parts.extend(remapping(each) for each in done.remapped)
    return "\n".join(parts).rstrip()


async def guarded[T](work: Awaitable[T]) -> T:
    """
    Turn a refusal into something the model is told rather than something the turn dies of.

    Both refusals mean the same thing to whoever gets them: nothing happened, here is why, try
    again. `ModelRetry` is how Pydantic AI puts a sentence in front of the model as the result of
    its own call, which is where it can be acted on. Anything else raised here is a genuine fault
    and is left alone, because a bug in this code is not something a model can retry its way out of.
    """
    try:
        return await work
    except (EditRefused, Refused) as refusal:
        raise ModelRetry(str(refusal)) from None


def file_tools(files: Files) -> FunctionToolset[None]:
    """
    The three tools, bound to one session's worktree.

    Built per session rather than declared once, because the root is what makes a path safe and
    every session has its own. A session with no repository gets no toolset at all, which is the
    honest answer rather than a tool that refuses every call: there are no files.
    """
    toolset = FunctionToolset[None]()

    async def read(path: str, offset: int = 1, limit: int = MAX_LINES) -> str:
        """
        Read a file, with an anchor in front of every line that has one.

        An **anchor** is the four-letter name a line answers to, derived from the line's own
        content. It is how `edit` says which lines to change, and it is not a line number: an edit
        anywhere in the file leaves other lines' anchors alone, so anchors read here stay usable
        after an edit elsewhere. A line that has changed since you read it will not resolve, and
        you will be told so rather than editing the wrong place.

        Blank lines have no anchor, by design. To reach one, address the line above it with
        `after`, or the line below it with `before`. A line shown with `----` instead of an anchor
        is inside a run of identical lines and cannot be named directly; address the unique lines
        around it.

        Args:
            path: Path to the file, relative to the session's workspace.
            offset: First line to show, counting from 1.
            limit: How many lines to show at most.

        """
        return await guarded(files.read(path, offset, limit))

    async def edit(path: str, operations: list[Operation]) -> str:
        r"""
        Change a file by naming lines with their anchors, never by retyping them.

        Every operation is resolved against the file as it is *now*, before anything is written,
        and the whole batch is applied at once or refused entire. So operations in one call cannot
        shift each other, and a batch whose operations overlap is turned down rather than resolved
        in some order you did not choose. Batch freely: a rename and the call sites it affects
        belong in one call, and so do an import and the code that uses it.

        Two shapes of operation, chosen with `op`:

        **`splice`** replaces a span of whole lines. Which lines are in the span is said by the
        field names, so there is no inclusive-or-exclusive flag to get backwards:

        - `{"op": "splice", "from": "abcd", "to": "efgh", "text": "..."}` replaces `abcd` through
          `efgh`, both included.
        - `{"op": "splice", "after": "abcd", "before": "efgh", "text": "..."}` replaces what lies
          between them and keeps both.
        - `{"op": "splice", "from": "abcd", "before": "efgh", "text": "..."}` and
          `{"op": "splice", "after": "abcd", "to": "efgh", "text": "..."}` mix the two.
        - Empty `text` deletes the span. Each line of `text` becomes one line of the file, so
          `"\n"` leaves a blank line behind rather than nothing. Deleting a function usually wants
          `text: ""` and an exclusive end, which keeps the blank lines above the span as they are.

        Give only `after` or only `before` to **insert**: `{"op": "splice", "after": "abcd",
        "text": "..."}` puts new lines directly below `abcd`. This is also how to reach blank
        lines, which have no anchors of their own. To delete a function and the blank lines
        following it, splice `{"from": <its first line>, "before": <the next code line>}`, which
        neither names a blank nor retypes the line it stops short of.

        **`substitute`** replaces text inside one line, for when retyping the whole line is
        wasteful: `{"op": "substitute", "at": "abcd", "find": "old_name", "replace": "new_name"}`.
        `find` must occur exactly once in that line; widen it until it does.

        The reply shows the changed regions with their new anchors, so you can keep editing without
        reading the file again, and lists any anchor elsewhere in the file that changed as a result.

        Args:
            path: Path to the file, relative to the session's workspace.
            operations: The changes to apply together.

        """
        return await guarded(files.edit(path, operations))

    async def create(path: str, content: str) -> str:
        """
        Create a new file. Refuses if there is already something at that path.

        There is deliberately no tool that overwrites an existing file: use `edit`, which changes
        only what you name and cannot silently discard what you have not read. Parent directories
        are created as needed, and the file is given a trailing newline if it lacks one.

        Args:
            path: Path for the new file, relative to the session's workspace.
            content: What to write into it.

        """
        return await guarded(files.create(path, content))

    for tool in (read, edit, create):
        toolset.add_function(tool, retries=RETRIES)
    return toolset
