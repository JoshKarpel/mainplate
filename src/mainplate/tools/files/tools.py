# The file tools: the half of anchored editing that touches a disk, and the listing that finds
# something to edit in the first place.
#
# `anchors.py` is the pure core: it takes lines and operations and returns lines. This is the shell
# around it, and it owns what is not pure - where the file is, what encoding and line endings it
# had, what git says is in a directory, and how a refusal reaches the model. The rendering is pure
# and lives here anyway, next to the call whose answer it shapes rather than off in the core, since
# none of it is about anchors.
#
# **Every path is resolved before it is compared, and refused if it lands somewhere out of reach.**
# `..`, an absolute path, and a symlink pointing out of the tree are all the same mistake and all get
# the same answer. `Path.resolve` is what makes the symlink case work, since it is the only check
# that follows one.
#
# There are *two* places a session may reach, and they are not symmetric. A relative path is inside
# the worktree unless a call names another root, because what a conversation is about is the
# repository; anywhere else is reached by naming it rather than by writing a session id out. `list`
# is the exception to both and
# stays on the worktree alone: it answers by asking git, and the scratch is deliberately not in git,
# so extending it would mean a second implementation that walks a directory instead. What `list`
# earns its keep for is bounding a large repository tree, which a scratch directory does not have,
# and `ls` under `bash` answers that question there perfectly well.
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
from collections.abc import Iterator
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Final
from typing import assert_never

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from mainplate.roots import RootName
from mainplate.snapshots import Worktree
from mainplate.tools.files.anchors import Anchored
from mainplate.tools.files.anchors import EditRefused
from mainplate.tools.files.anchors import Moved
from mainplate.tools.files.anchors import Operation
from mainplate.tools.files.anchors import Written
from mainplate.tools.files.anchors import written

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

# The most rows one listing will show. A bound on the pathological case rather than a page size, the
# way `MAX_LINES` is: `depth` is the knob, and this is what stops a large `depth` on a large
# repository from spending a context window before the model has asked its first real question.
MAX_ROWS: Final = 400


class ListingFailed(RuntimeError):
    """
    git could not say what is in a directory.

    Not a `Refused`, because it is not something a model can retry its way out of: every worktree
    these tools are built against is a linked worktree, so this is a broken environment rather than
    a badly-aimed call.
    """


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
class GitTracked:
    """
    A git worktree: files a conversation is *about*, and the only kind of root git can be asked about.

    It owns how to enumerate itself rather than leaving that to whoever holds it, because "ask git"
    is the one thing that is true of this root and false of every other. A second worktree is one
    more of these in `Files.roots` and nothing else.

    It holds the `Worktree` rather than its path, because enumerating it means running a program
    against a directory a session may write, and *how* to do that safely is one answer this console
    has already worked out: which git directory to name, and what environment to build. Holding the
    path would be holding half of it, and the other half would be reassembled here and drift.
    """

    worktree: Worktree

    @property
    def path(self) -> Path:
        """Where this root is, which is what every other kind of root carries as a field."""
        return self.worktree.root

    @property
    def name(self) -> RootName:
        """What a model calls this place, which is what it is rather than where it is."""
        return "worktree"

    async def entries(self, here: Path) -> tuple[str, ...]:
        """
        What git says is under `here`, which is everything committed or new but nothing ignored.

        Asked of git rather than walked, because the alternative is a hand-kept list of names to
        skip that is wrong the moment a repository uses a build directory nobody thought of. A
        worktree here has a `.venv` or a `node_modules` more often than not, and one of those walked
        in full is tens of thousands of paths through a context window. `--cached --others` is the
        pair that also shows a file the agent itself just created, which is untracked and is exactly
        what it will want to look for.

        What comes back is *flat*: one full path per line, relative to `here`. Git records files and
        never directories, so there is no tree to ask it for and none to be had - an empty directory
        does not exist as far as this is concerned. `catalogue` is what turns those paths into one.

        A failure is a fault rather than a `Refused`: this root is a git worktree by construction,
        so git failing here is not something a model can retry its way out of, and returning nothing
        would be a silent wrong answer.

        **Through `Worktree.git` rather than a subprocess of its own**, which is what gets this the
        named git directory and the built environment: running a program in the parent against the
        one directory a session may write is exactly what that method exists to make safe, and
        rebuilding it here would be a second copy to keep in step. `at` is where git runs and is
        never what `--work-tree` names, so a listing of a subdirectory comes back relative to it.

        `stdout` rather than `out` because `-z` separates paths with NUL, which is not text to strip.
        """
        listed = await self.worktree.git("ls-files", "--cached", "--others", "--exclude-standard", "-z", at=here)
        if not listed.ok:
            raise ListingFailed(f"git ls-files failed ({listed.code}): {listed.err}")
        return tuple(sorted(found for found in listed.stdout.decode().split("\0") if found))


@dataclass(frozen=True, slots=True)
class Scratch:
    """
    Somewhere to keep what is not any repository's, and which nothing snapshots.

    It answers no question git answers, which is the whole reason it exists, so it carries no way to
    enumerate itself: `ls` under `bash` does that, and a walk here would be a second implementation
    of listing inside the one tool whose design is that it asks rather than walks.
    """

    path: Path

    @property
    def name(self) -> RootName:
        return "scratch"


@dataclass(frozen=True, slots=True)
class System:
    """
    The whole machine, for a session that chose to work on it rather than in a repository.

    Like `Scratch` it answers no question git answers, so `list` refuses it for the same reason and
    points at `bash`. Unlike `Scratch` it is not somewhere to *keep* things, it is everywhere: a
    relative path lands here only because it is the session's first and only root.
    """

    path: Path

    @property
    def name(self) -> RootName:
        return "machine"


type Root = GitTracked | Scratch | System


@dataclass(frozen=True, slots=True)
class Located:
    """
    A resolved path together with the root it turned out to be in.

    Both halves, because every caller needs both and working the second one out twice is how they
    come to disagree. A tool that has one of these is holding proof the path is reachable *and* what
    kind of place it landed in, so nothing downstream re-asks either question.
    """

    path: Path
    root: Root


@dataclass(frozen=True, slots=True)
class Files:
    """
    The places one session may touch, as the four things a model may do to them.

    Frozen and holding only roots, so it is a value rather than a handle: every method is an effect
    against the filesystem, and two callers sharing one share no state.

    The **first** root is where a relative path lands when a call names no other, which keeps every
    path a model writes meaning what it has always meant and stays well defined however many roots
    there are. The asymmetry is deliberate: a bare `notes.md` is about the repository, because that
    is what a conversation is about.

    Anywhere else is reached by naming the root rather than by writing its path out, since a worktree
    sits under 32 hex characters of session id and a model reproducing those from memory eventually
    reproduces them wrong. A root owns its own name, so a kind of place added here brings one with it.
    """

    roots: tuple[Root, ...]

    locks: dict[Path, asyncio.Lock] = field(default_factory=dict, compare=False)
    """
    One lock per path touched, so two calls in one batch cannot lose each other's work.

    A model emits several tool calls in one response and they run **concurrently**, so two `edit`s
    aimed at one file interleave: each reads, each computes against what it read, each writes, and
    the loser's write silently disappears while *both* calls report success. Two `create`s race the
    same way, both seeing a path that does not exist yet.

    Held around the whole read-modify-write rather than around the write, which is what makes the
    difference: serialised that way the second call reads the first one's result, and anchors then
    do the job they already do. If the first edit invalidated the second's anchors the second fails
    loudly, which is correct and is the behaviour anchors were chosen for; if it did not, both land.

    Interior mutability on a frozen value, and out of the comparison, because this is machinery
    rather than any part of what a `Files` *is*. It grows with the paths one pass touches and is
    thrown away with the pass. It does not reach `bash`, whose paths are not knowable in advance:
    a command that rewrites a file under an `edit` is outside what this can see.
    """

    def exclusively(self, here: Path) -> asyncio.Lock:
        """
        The lock for one resolved path, made on first use.

        `setdefault` rather than a check and an insert: there is no `await` between the two halves,
        so it is atomic against every other coroutine and two callers cannot make two locks for one
        path and each hold a different one.
        """
        return self.locks.setdefault(here, asyncio.Lock())

    def against(self, root: str) -> Path:
        """
        Which place a relative path is joined to, named rather than spelled out.

        The refusal names every root this session has, which is what lets the tool descriptions stay
        the same for every session: what a session's places are called varies, so it is taught at the
        one moment a model gets it wrong rather than described in a sentence built per session.
        """
        for each in self.roots:
            if each.name == root:
                return each.path.resolve()
        named = ", ".join(each.name for each in self.roots)
        raise Refused(f"there is no {root!r} here. This session's roots are {named}")

    def resolved(self, path: str, root: str = "") -> Located:
        """
        Where `path` actually is and which root it is in, or a refusal if it is out of reach.

        Resolved before it is compared, which is the whole of the check: `..` collapses, an
        absolute path replaces the root outright under `/`, and a symlink is followed to whatever
        it really points at. Comparing the unresolved join would pass all three.

        `root` names which place a *relative* path is joined to, and nothing else: what a path is
        allowed to reach is still every root, because an absolute path lands where it lands whatever
        was named. Unnamed it is the first root, which is what a bare `notes.md` has always meant.
        """
        wheres = tuple(each.path.resolve() for each in self.roots)
        here = ((self.against(root) if root else wheres[0]) / path).resolve()
        for found, where in zip(self.roots, wheres, strict=True):
            if here == where or where in here.parents:
                return Located(path=here, root=found)
        named = " and ".join(str(each) for each in wheres)
        raise Refused(f"{path!r} is outside this session's workspace. These tools reach {named}")

    def naming(self, path: str, found: Located) -> str:
        """
        What to call a path in what a tool hands back, which says the root only where it has to.

        A bare `notes.md` in a return would be two different files once a session has two roots, so
        the root is named wherever it is not the one a relative path already means. Named only there,
        for the reason `Reachable.labelled` puts an attachment on a row only where two would
        otherwise read alike: a word repeated on every line stops being read.
        """
        if found.root == self.roots[0]:
            return path
        return f"{path} in {found.root.name}"

    def loaded(self, path: str, root: str = "") -> tuple[Path, Text]:
        here = self.resolved(path, root).path
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

    async def read(self, path: str, offset: int, limit: int, root: str = "") -> str:
        found = self.resolved(path, root)
        async with self.exclusively(found.path):
            _, text = await asyncio.to_thread(self.loaded, path, root)
        anchored = Anchored.over(text.lines)
        start = max(0, offset - 1)
        stop = min(len(text.lines), start + max(1, limit))
        said = reading(self.naming(path, found), len(text.lines), start, stop)
        return "\n".join((said, "", anchored.rendered(start, stop)))

    async def listing(self, path: str, depth: int) -> str:
        """
        What is in a directory, answered by whichever root the directory turned out to be in.

        The root decides rather than a check here, which is what keeps `list` honest as roots are
        added: a `Scratch` is refused because it answers no question git answers, and adding a kind
        that *can* enumerate itself is one more arm rather than an edit to the condition above.
        """
        found = self.resolved(path)
        match found.root:
            case Scratch() | System():
                raise Refused(
                    f"{path!r} is not in a repository, and `list` only reads one: it asks git, and "
                    f"nothing there is in git. Use `bash` with `ls` to see what is there."
                )
            case GitTracked() as tree:
                if not found.path.exists():
                    raise Refused(f"there is no directory at {path!r}")
                if not found.path.is_dir():
                    raise Refused(f"{path!r} is a file rather than a directory; `read` is what opens one")
                return catalogued(path, await tree.entries(found.path), max(1, depth))
            case _ as unreachable:
                assert_never(unreachable)

    async def edit(self, path: str, operations: Sequence[Operation], root: str = "") -> str:
        located = self.resolved(path, root)
        # The read and the write are one critical section, not two. Holding this around the write
        # alone would leave each caller writing out a whole file it read *before* the other one's
        # edit landed, which is the same lost write with a smaller window.
        async with self.exclusively(located.path):
            found, text = await asyncio.to_thread(self.loaded, path, root)
            done = written(Anchored.over(text.lines), operations)
            # `newline=""` again, so the endings `Text` just put back are written as they are rather
            # than translated a second time on the way out.
            await asyncio.to_thread(found.write_text, text.rejoined(done.lines), encoding="utf-8", newline="")
        return reported(self.naming(path, located), done)

    async def create(self, path: str, content: str, root: str = "") -> str:
        located = self.resolved(path, root)
        here = located.path
        text = Text.of(content if content.endswith("\n") else content + "\n")

        def write() -> None:
            here.parent.mkdir(parents=True, exist_ok=True)
            here.write_text(text.rejoined(text.lines), encoding="utf-8", newline="")

        # The existence check is *inside* the lock, which is the whole of what makes `create` refuse
        # to overwrite under concurrency: checked outside it, two calls in one batch both see a path
        # that is not there yet and the second silently replaces the first.
        async with self.exclusively(here):
            if here.exists():
                raise Refused(f"{path!r} already exists; `create` never overwrites, so edit it instead")
            await asyncio.to_thread(write)
        anchored = Anchored.over(text.lines)
        made = f"created {self.naming(path, located)}, {counted(len(text.lines), 'line')}"
        return "\n".join((made, "", anchored.rendered(0, MAX_LINES)))


def counted(many: int, noun: str) -> str:
    return f"{many} {noun}" if many == 1 else f"{many} {noun}s"


def catalogue(paths: Sequence[str], depth: int, level: int) -> Iterator[str]:
    """
    One row per file, and one per directory, indented by how deep it sits.

    A directory at the depth asked for is summarised by a count rather than opened, which is what
    makes `depth` a bound on the answer instead of a hint. Its own files come before its
    subdirectories so that what is *here* stays next to the line naming here, rather than arriving
    after everything nested below it.
    """
    indent = "  " * level
    folders: dict[str, list[str]] = {}
    for path in paths:
        if "/" in path:
            head, rest = path.split("/", 1)
            folders.setdefault(head, []).append(rest)
    for path in sorted(path for path in paths if "/" not in path):
        yield f"{indent}{path}"
    for name, inside in sorted(folders.items()):
        if level + 1 >= depth:
            yield f"{indent}{name}/ ({counted(len(inside), 'file')})"
            continue
        yield f"{indent}{name}/"
        yield from catalogue(inside, depth, level + 1)


def catalogued(path: str, found: Sequence[str], depth: int) -> str:
    """What a listing hands back: what was asked, then the tree, bounded by `MAX_ROWS`."""
    if not found:
        return f"{path} holds no files git knows about"
    rows = list(catalogue(found, depth, 0))
    said = f"{path}, {counted(len(found), 'file')} within {counted(depth, 'level')}"
    if len(rows) > MAX_ROWS:
        return "\n".join(
            (f"{said}; the first {MAX_ROWS} rows, so ask for a smaller `depth` to see less", "", *rows[:MAX_ROWS])
        )
    return "\n".join((said, "", *rows))


def reading(path: str, total: int, start: int, stop: int) -> str:
    """What a read says about itself, which is where in the file it stopped and whether it did."""
    if start == 0 and stop >= total:
        return f"{path}, {counted(total, 'line')}"
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
    parts = [f"edited {path}, now {counted(len(done.lines), 'line')}", ""]
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
    The four tools, bound to one session's worktree.

    Built per session rather than declared once, because the root is what makes a path safe and
    every session has its own. A session with no repository gets no toolset at all, which is the
    honest answer rather than a tool that refuses every call: there are no files.
    """
    toolset = FunctionToolset[None]()

    async def listing(path: str = ".", depth: int = 2) -> str:
        """
        See what files are in a directory, before guessing at a name.

        Reach for this first, rather than trying `read` on a path you are hoping exists. At its
        default `depth` one call usually orients you in a repository:

        ```
        ., 8 files within 2 levels

        .gitignore
        README.md
        pyproject.toml
        src/
          demo/ (4 files)
        tests/
          test_app.py
        ```

        A name ending in `/` is a directory. One shown with a count, like `demo/ (4 files)`, sits at
        the depth asked for and was summarised rather than opened; list it directly or ask for a
        larger `depth` to see inside. A directory's own files come before its subdirectories, so
        what is at a level stays next to the line naming that level.

        Only what git tracks or would track is shown: anything matched by a `.gitignore` is left
        out, so a build directory or an installed environment will not appear, and a file you have
        just created will. A directory holding no files of its own is therefore not listed at all,
        since git records files rather than directories. Nothing here is a promise that a path is
        readable text; `read` says so.

        Args:
            path: Directory to list, relative to the repository. Defaults to its root.
            depth: How many levels of directory to open. A directory deeper than this is shown
                with a count of what is inside it instead of its contents.

        """
        return await guarded(files.listing(path, depth))

    async def read(path: str, offset: int = 1, limit: int = MAX_LINES, root: str = "") -> str:
        r"""
        Read a file, with an anchor in front of every line that has one.

        Each line comes back as its anchor, then a `│`, then the line itself:

        ```
        greet.py, 7 lines

        idpf│import sys
        ----│
        ----│
        cxec│def greet(name):
        zcbk│    if not name:
        uzsa│        return "hello, world"
        pelr│    return f"hello, {name}"
        ```

        **Everything left of the `│` is this tool talking, and is not in the file.** That file's
        first line is `import sys`, not `idpf│import sys`, and its `def` line is `def greet(name):`
        with nothing before it. The `│` is what marks where the file starts, so read up to it and
        no further. Never write an anchor or a `│` into `text`, `replace`, or `content`: it is not
        part of the line, and putting it back corrupts the line and re-anchors everything after it.

        An **anchor** is the four-letter name a line answers to, derived from the line's own
        content. It is how `edit` says which lines to change, and it is not a line number: an edit
        anywhere in the file leaves other lines' anchors alone, so anchors read here stay usable
        after an edit elsewhere. A line that has changed since you read it will not resolve, and
        you will be told so rather than editing the wrong place.

        `----` in place of an anchor means that line cannot be named, and there are two ways to be
        unnameable. A blank line is one, as lines 2 and 3 above are: reach it by addressing the
        line above with `after` or the line below with `before`. The other is a line inside a run
        of identical lines, which shows as `----│    pass` with its content still there; address
        the unique lines around the run.

        Args:
            path: Path to the file, relative to `root`.
            offset: First line to show, counting from 1.
            limit: How many lines to show at most.
            root: Which of this session's places `path` is relative to. Your instructions name them.
                Left out, it is the first one, which is what a bare name has always meant.

        """
        return await guarded(files.read(path, offset, limit, root))

    async def edit(path: str, operations: list[Operation], root: str = "") -> str:
        r"""
        Change a file by naming lines with their anchors, never by retyping them.

        Every operation is resolved against the file as it is *now*, before anything is written,
        and the whole batch is applied at once or refused entire. So operations in one call cannot
        shift each other, and a batch whose operations overlap is turned down rather than resolved
        in some order you did not choose. Batch freely: a rename and the call sites it affects
        belong in one call, and so do an import and the code that uses it.

        **`text` and `replace` are file content, so no anchor and no `│` belongs in either.** An
        anchor names a line in the arguments that address it (`from`, `to`, `after`, `before`,
        `at`); it is never part of what gets written. Replacing `zcbk│    if not name:` is
        `{"from": "zcbk", ..., "text": "    if not name:"}`, with the gutter dropped. Writing the
        gutter back puts it in the file as if it were code, and the next read then shows a fresh
        anchor in front of the one you wrote, which is a mess to unpick.

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
            path: Path to the file, relative to `root`.
            operations: The changes to apply together.
            root: Which of this session's places `path` is relative to. Your instructions name them.
                Left out, it is the first one, which is what a bare name has always meant.

        """
        return await guarded(files.edit(path, operations, root))

    async def create(path: str, content: str, root: str = "") -> str:
        """
        Create a new file. Refuses if there is already something at that path.

        There is deliberately no tool that overwrites an existing file: use `edit`, which changes
        only what you name and cannot silently discard what you have not read. Parent directories
        are created as needed, and the file is given a trailing newline if it lacks one.

        Args:
            path: Path for the new file, relative to `root`.
            content: What to write into it.
            root: Which of this session's places `path` is relative to. Your instructions name them.
                Left out, it is the first one, which is what a bare name has always meant.

        """
        return await guarded(files.create(path, content, root))

    for tool in (read, edit, create):
        toolset.add_function(tool, retries=RETRIES)
    # Asked for as `list`, which is the word a model reaches for, and defined as `listing`, because
    # `list` is a builtin and shadowing one inside this scope is a lint error rather than a style
    # question. The name the model sees is the only one that matters, so it is set here explicitly.
    toolset.add_function(listing, name="list", retries=RETRIES)
    return toolset
