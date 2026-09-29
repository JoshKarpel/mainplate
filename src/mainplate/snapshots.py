# The worktree as something a session can go back to, recorded beside what was said about it.
#
# A snapshot is a git *tree*, taken through a shadow index so nothing the reader can see moves: not
# their staged changes, not `HEAD`, not a branch, not `git log`. The trees are chained into commits
# under refs of this program's own in the repository's *store*, which is the only reason they
# survive `git gc` and the reason they outlive the worktree they were taken from.
#
# The session ledger is authoritative and git is the content store. What a checkpoint records is a
# tree hash; what the hash *means* is git's business. That split is what keeps a snapshot cheap
# (an unchanged tree is the same hash, so there is nothing to write) and what keeps a session's
# checkpoint small enough to stay the whole of the conversation.
#
# Snapshots are gitignore-aware, and that is a decision rather than an inheritance. A tree holds
# what is version-controlled and nothing else, so what a fork checks out is the source as that turn
# saw it and never a `.venv`, a build directory, or an untracked file holding a secret. It is the
# contract git already offers, so nobody has to learn a second one, and it is why going back is
# always *forward* into a new session: a fork plants a fresh worktree at a recorded tree rather than
# putting an existing one back, which is a thing no reader of this module can do at all.
#
# **Two kinds of repository, and git runs differently against each.** The `Store` is the bare clone
# this console made and nothing in a sandbox can write, so git runs against it here, in the parent.
# A `Worktree` is a session's own, whose `.git` the session writes and whose configuration can
# therefore name any program git will run; git runs against it only inside the session's sandbox,
# and what comes out crosses back as a bundle the store fetches. No parent process ever reads a
# worktree's configuration. See `docs/design/security.md`.

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from secrets import token_hex
from typing import Final

from mainplate.processes import reaped
from mainplate.sandbox import Bind
from mainplate.sandbox import Sandbox
from mainplate.sandbox import Venue
from mainplate.sandbox import worktree_places

# Where every session's refs live in the store, one namespace per session: its `base`, the commit it
# was planted at; its `snapshots`, the chain its trees hang from; and whatever it is importing at
# the moment. Under `refs/` but outside `refs/heads/`, so none of them is a branch: they do not
# appear in `git branch`, are not walked by a bare `git log`, and cannot be checked out by accident.
SESSIONS: Final = "refs/mainplate/sessions"

# Where a bundle's one head sits inside a worktree while it is being made. Its own name per capture,
# so two captures of one worktree never write the same ref.
TRANSFER: Final = "refs/mainplate/transfer"

# What `commit-tree` is told to put in the author and committer fields. Supplied rather than left
# to the machine's own configuration, because a snapshot must not fail on a worktree where nobody
# has set `user.email`, and because these commits are this program's rather than anybody's.
IDENTITY: Final[Mapping[str, str]] = {
    "GIT_AUTHOR_NAME": "mainplate",
    "GIT_AUTHOR_EMAIL": "mainplate@localhost",
    "GIT_COMMITTER_NAME": "mainplate",
    "GIT_COMMITTER_EMAIL": "mainplate@localhost",
}

# Spelled out for the reason the sandbox spells its own out: what git finds is what this console put
# there, so a `PATH` the service happened to be started with cannot decide which `git` runs.
WHERE_GIT_IS: Final = "/usr/bin:/bin:/usr/local/bin"

# What a worktree keeps its git directory under, which the file tools refuse by name: they write
# from the parent and pass through no sandbox, and a line edit of git's own files would bypass its
# locking and formats. Here rather than in `tools/`, beside the worktree whose shape it is.
POINTER: Final = ".git"

# How many times a capture re-reads the chain's tip when another capture moved it first. Two captures
# of one session race only where an archive meets a pass, so this is a bound on a rare collision
# rather than a retry policy.
CHAINING: Final = 3

# What a hash git printed has to look like before anything here passes it on: forty hex characters
# for SHA-1 and sixty-four for SHA-256, and nothing else. A hash out of a sandbox is text a session's
# git wrote, so a leading `-` or a revision expression in its place must not reach an argument.
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")

# How long a git in the parent has, once it has been asked to stop, before it is made to. Git
# removes its lock files on `SIGTERM` in well under this; what the bound is for is a git that does
# not stop at all, which nothing should then wait on for long. See `reaped`.
STOPPING: Final = timedelta(seconds=2)


# What may appear in something a session names a commit by. Every one of these characters is one git
# itself accepts in a revision expression: a ref name, a tag, an abbreviated hash, and the suffixes
# that walk from one (`main~2`, `v2^{commit}`, `@{upstream}`).
#
# A pattern rather than a call to `git check-ref-format`, because what has to be true here is
# narrower than what git will accept and is true *before* any git runs: this value becomes an
# argument, so what it must not be is an option. A leading `-` is the whole of that risk and the
# anchored pattern refuses it along with everything else nobody types on purpose.
COMMITISH = re.compile(r"[0-9A-Za-z_][0-9A-Za-z_./~^@{}+-]*\Z")

# Stricter, because this one is *created* rather than resolved, and a branch git accepts but nobody
# can address later is worse than a refusal now. These are `git check-ref-format --branch`'s rules
# written out for the shapes a person types: no leading dot or dash, nothing that ends a component
# in `.lock`, no `..`, and none of the characters git reserves for revision syntax.
BRANCH = re.compile(r"[0-9A-Za-z_][0-9A-Za-z_./-]*\Z")

# Long enough for the longest branch name anybody types and short enough that neither of these is a
# way to put a paragraph into a `git` argument.
LONGEST_REF: Final = 250


def parse_commitish(named: str) -> str | None:
    """
    Something a session may be started at, or nothing at all where it is not one.

    `None` rather than a refusal, so the caller decides whether an unusable value is a mistake to
    report or a field somebody left blank - the split `parse_disposition` already makes. An empty box
    reaches here as an empty string and is nothing, which is the common case.
    """
    named = named.strip()
    if not named or len(named) > LONGEST_REF or ".." in named:
        return None
    return named if COMMITISH.fullmatch(named) else None


# What a branch this console made for a session is called. A namespace of its own, so `git branch`
# says where these came from and `git branch --list 'mainplate/*'` is how you find them all again.
BRANCH_PREFIX: Final = "mainplate/"

# How much of a session's id names its branch. Eight hex characters is git's own abbreviation length
# and the same number a rule prints for a tree, so it is a length a reader here is already used to.
BRANCH_ID: Final = 8


def branch_named(session: str) -> str:
    """
    The branch a session gets when nobody named one, which is every session working in a repository.

    **A branch and not a detached `HEAD`**, because committing is something that happens here: a
    commit on a detached `HEAD` is reachable only through the reflog, and pushing one needs a name
    to push it under. Reading, editing and every question `git` answers are fine detached; the one
    thing that is not is the thing a worktree of its own exists to make possible.

    Named from the **session id**, so it is unique by construction and pushing two sessions' work
    never collides on the remote. That is also why it is not derived from the session's title - two
    sessions opened with the same message would collide, so the id would have to be in the name
    anyway, and a title is prose where a ref is not.
    """
    return f"{BRANCH_PREFIX}{session[:BRANCH_ID]}"


def parse_branch(named: str) -> str | None:
    """
    A branch name a session may start, or nothing at all where it is not one.

    The `.lock` rule is git's and is easy to miss: a component ending in it collides with the file
    git writes while updating a ref, so `git branch` refuses the name and the refusal arrives from a
    worktree that failed to plant rather than from the box it was typed in.
    """
    named = named.strip().removeprefix("refs/heads/")
    if not named or len(named) > LONGEST_REF or ".." in named:
        return None
    if any(part.endswith(".lock") or not part for part in named.split("/")):
        return None
    return named if BRANCH.fullmatch(named) else None


class NotAWorktree(ValueError):
    """
    A repository was configured that is not one git can use.

    Loud, and at startup, for the reason an unusable `config.yaml` is: a console that accepted the
    path and silently recorded no snapshots would look like it was keeping a history it was not,
    and the first time anybody wanted to go back would be the first time they found out.
    """


class SnapshotFailed(RuntimeError):
    """A git invocation this depends on did not succeed, carrying what git said about it."""


@dataclass(frozen=True, slots=True)
class Ran:
    """
    What one git invocation came to.

    The bytes are what is held and the text is a reading of them, because not everything git prints
    is text to strip: `-z` output is NUL-separated paths, and a caller handed a stripped string has
    already lost the ability to split it safely. `out` and `err` are the common case and stay one
    attribute access away.
    """

    code: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def out(self) -> str:
        return self.stdout.decode().strip()

    @property
    def err(self) -> str:
        return self.stderr.decode().strip()


async def git_at(at: Path, *arguments: str, environment: Mapping[str, str] | None = None) -> Ran:
    """
    One git command in the parent, in a directory this console made and nothing in a sandbox writes.

    **Never against a worktree.** Its configuration is the session's to write and can name a program
    git runs, which is why `Worktree.git` runs in a sandbox instead. This is for the store, for the
    directory stores are cloned under, and for a worktree still being built, before any session has
    had a moment to write to it.

    The environment is built rather than inherited, so the console's own does not cross into a
    program git may run and a machine where nobody set `user.email` still snapshots. It sets no
    `HOME`, so git reads no global configuration either.

    **Stopped part-way, git is asked to stop before it is made to**, because what runs here writes
    the store, and a git killed while holding a ref's lock leaves the `.lock` behind: every later
    fetch or capture that writes that ref then fails, and nothing in a sandbox can reach the store
    to delete it. Terminated, git removes its own locks first. See `reaped`.
    """
    process = await asyncio.create_subprocess_exec(
        "git",
        *arguments,
        cwd=at,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**IDENTITY, "PATH": WHERE_GIT_IS, **(environment or {})},
    )
    try:
        out, err = await process.communicate()
    except BaseException:
        # Cancellation included, which is the case that matters; see the same block in
        # `plugins/running.py` for why it is `BaseException`.
        reaped(process, grace=STOPPING)
        raise
    return Ran(code=process.returncode or 0, stdout=out, stderr=err)


def demanded(ran: Ran, arguments: tuple[str, ...]) -> str:
    """What a git command printed, or a `SnapshotFailed` naming the command and what git said."""
    if not ran.ok:
        raise SnapshotFailed(f"git {' '.join(arguments)} failed ({ran.code}): {ran.err or ran.out}")
    return ran.out


def regular(path: Path) -> bool:
    """Whether a path is a file of its own, rather than a link to one or anything else."""
    return not path.is_symlink() and path.is_file()


def object_id(said: str, what: str) -> str:
    """A hash git printed, refused unless it is shaped like one before it becomes anybody's argument."""
    if not OBJECT_ID.fullmatch(said):
        raise SnapshotFailed(f"{what} is not an object id: {said[:80]!r}")
    return said


@dataclass(frozen=True, slots=True)
class Store:
    """
    One repository's store, the bare clone this console made: the trusted half of every session on it.

    It holds what the forge said the repository is, the refreshed `refs/remotes/origin/*`, and every
    tree any session on it has snapshotted, so it is what a fork plants from and what a push goes out
    through. Nothing in a sandbox can write it: a session's worktree borrows its objects read-only,
    and what a session made reaches it only as a bundle this process fetched.

    Frozen, and holding only a path, so two callers sharing one share no state.
    """

    path: Path

    async def git(self, *arguments: str) -> Ran:
        """One git command against the store, told which directory is git's rather than left to find one."""
        return await git_at(self.path, "--git-dir", str(self.path), *arguments)

    async def demand(self, *arguments: str) -> str:
        """What a git command against the store printed, or a `SnapshotFailed` naming it and what git said."""
        return demanded(await self.git(*arguments), arguments)

    async def confirm(self) -> None:
        """That this is a bare repository at all, asked once rather than at the first capture."""
        ran = await self.git("rev-parse", "--is-bare-repository")
        if not ran.ok or ran.out != "true":
            raise NotAWorktree(f"{self.path} is not a bare git repository: {ran.err or ran.out}")

    async def commit_at(self, ref: str) -> str | None:
        """The commit a ref names, or nothing where it names none."""
        ran = await self.git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return ran.out if ran.ok and ran.out else None

    async def holds_tree(self, tree: str) -> bool:
        """Whether a tree is already here, which is when a capture has nothing to send."""
        ran = await self.git("cat-file", "-t", tree)
        return ran.ok and ran.out == "tree"

    async def fetched(self, bundle: Path, head: str, into: str) -> str:
        """
        One bundle's head, fetched into a ref of the store's, as the commit it is.

        The bundle is something a sandbox wrote, so it is read as data and nothing else: refused
        unless it is a regular file rather than a link to one somewhere a sandbox cannot see, and
        fetched with `transfer.fsckObjects` so a malformed object is a refused fetch rather than a
        store that holds one. The store's own configuration is the only one read.
        """
        if not await asyncio.to_thread(regular, bundle):
            raise SnapshotFailed(f"{bundle.name} is not a regular file")
        await self.demand(
            "-c",
            "transfer.fsckObjects=true",
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            str(bundle),
            f"+{head}:{into}",
        )
        return object_id(await self.demand("rev-parse", "--verify", f"{into}^{{commit}}"), into)

    async def paths(self, tree: str) -> tuple[str, ...]:
        """
        Every file a tree holds, as paths from its root, which is what a fork of it would check out.

        Asked of the store, which holds every tree a capture recorded, so it answers the same for a
        tree whose worktree has since been taken off the disk.
        """
        listed = await self.demand("ls-tree", "-r", "--name-only", tree)
        return tuple(line for line in listed.splitlines() if line)

    async def diff(self, before: str, after: str) -> str:
        """
        The change between two trees, as the unified diff git prints for it.

        Both trees are immutable objects already in the store, so the answer is settled and could be
        recomputed from the hashes at any time; it is called once and the *text* recorded, rather than
        run again on every replay or render, which is the same bargain `capture` makes. Asked of the
        store rather than of a worktree because the store's configuration is this console's, so no
        diff driver or text conversion a session wrote into its own runs here. `--no-renames` keeps the
        output to added and removed lines rather than `rename from` / `rename to` sections a reader
        gets nothing from, and `--no-ext-diff` is belt and braces over a store that names none.

        An unchanged pair prints nothing and exits 0, which is an empty diff rather than a fault.
        """
        ran = await self.git("diff", "--no-renames", "--no-ext-diff", before, after)
        if not ran.ok:
            raise SnapshotFailed(f"git diff {before[:8]} {after[:8]} failed ({ran.code}): {ran.err or ran.out}")
        return ran.out


@dataclass(frozen=True, slots=True)
class Worktree:
    """
    A session's own worktree, with the store it borrows objects from and keeps its snapshots in.

    **A complete repository of its own**, `.git` directory and all, so the session commits, merges,
    rebases and fetches as git normally would, and nothing it does to its refs, its index or its
    configuration reaches another session. Its objects come from the store through `alternates`
    rather than a copy, so a worktree costs a checkout rather than a clone; the store is bound
    read-only wherever this is, so the borrowing cannot run the other way.

    Frozen, and holding only values: every method is an effect against a repository rather than
    against anything held here, so two callers sharing one of these share no state.
    """

    root: Path
    store: Store
    session: str
    bwrap: str

    @property
    def refs(self) -> str:
        """Where this session's refs live in the store."""
        return f"{SESSIONS}/{self.session}"

    @property
    def base_ref(self) -> str:
        """
        The commit this session was planted at, held in the store so it stays reachable.

        It is what every capture's bundle is thin against, so a snapshot sends what the session
        changed rather than the repository.
        """
        return f"{self.refs}/base"

    @property
    def snapshots_ref(self) -> str:
        """The tip of the chain every tree this session captured hangs from, which is what keeps them."""
        return f"{self.refs}/snapshots"

    def confined(self, *writable: Path) -> Sandbox:
        """
        What git against this worktree sees: the worktree, the store it borrows from, and nothing else.

        `writable` is somewhere a capture or a push leaves what it made for the parent to collect,
        which a command a model runs is never given.
        """
        return Sandbox(places=(*worktree_places(self), *(Bind(path=each, writable=True) for each in writable)))

    async def git(
        self,
        *arguments: str,
        at: Path | None = None,
        environment: Mapping[str, str] | None = None,
        writable: tuple[Path, ...] = (),
    ) -> Ran:
        """
        One git command against this worktree, inside its sandbox.

        **This is the whole of how git reaches a worktree.** Its configuration is the session's to
        write, and several of its settings name a program git runs - `core.fsmonitor` fires on the
        index refresh inside `add` - so every git that could read it runs where the session's own
        commands do: the worktree and the store, no network, no credential, and the parent's
        environment cleared. The argv is this console's, and what a poisoned configuration can do
        with it is what the session could already do with `bash`.

        `at` is where git runs, defaulting to the root, so a listing of a subdirectory comes back
        relative to it exactly as a command's would.

        **Stopped part-way, it is killed outright**, unlike `git_at`'s, because asking does not reach
        it: the process this holds is `bwrap`, which passes no signal on, and a `SIGTERM` that ends
        `bwrap` has `--die-with-parent` kill the git inside before it can remove a lock. What that
        costs is a lock left in the worktree, which is the session's own directory and its `bash` can
        delete; the store is bound read-only in here, so no lock this leaves is ever in the store.
        """
        where = at or self.root
        process = await asyncio.create_subprocess_exec(
            self.bwrap,
            *self.confined(*writable).argv(
                at=str(where),
                venue=Venue.CONFINED,
                environment={**IDENTITY, **(environment or {})},
            ),
            "git",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await process.communicate()
        except BaseException:
            # Cancellation included, which is the case that matters; see the same block in
            # `plugins/running.py` for why it is `BaseException`.
            reaped(process)
            raise
        return Ran(code=process.returncode or 0, stdout=out, stderr=err)

    async def demand(
        self,
        *arguments: str,
        environment: Mapping[str, str] | None = None,
        writable: tuple[Path, ...] = (),
    ) -> str:
        """What a git command in this worktree's sandbox printed, or a `SnapshotFailed` saying why not."""
        return demanded(await self.git(*arguments, environment=environment, writable=writable), arguments)

    async def bundled(self, head: str, into: str, *, excluding: tuple[str, ...], transfer: Path) -> str:
        """
        One commit of this worktree's, carried into the store under `into`, as the commit it is there.

        Made in the sandbox and fetched in the parent, which is the only way anything crosses: git
        packs `head` into a file in a directory this console made for the purpose, and the store
        fetches that file. Everything reachable from `excluding` is left out, and the store already
        holds all of it, so what crosses is what the session changed rather than the repository.
        """
        bundle = transfer / "carried.bundle"
        await self.demand(
            "bundle", "create", "--quiet", str(bundle), head, *(f"^{each}" for each in excluding), writable=(transfer,)
        )
        return await self.store.fetched(bundle, head, into)

    async def capture(self, why: str) -> str:
        """
        The worktree as a tree object in the store, chained so it stays reachable, and its hash.

        The tree is what a checkpoint holds, and the commit exists only to keep it alive: git prunes
        an object nothing refers to, so a bare `write-tree` would be a hash that stops resolving at the
        next `gc`. Chaining each commit onto the last is what keeps every earlier snapshot reachable
        through one ref rather than needing a ref apiece.

        Three steps, and only the first reads anything a session wrote:

        - **In the sandbox**, `add -A` into a shadow index and `write-tree`, which is the tree the
          worktree holds. The shadow index is fresh per capture and lives in a directory made for this
          capture, never in `.git`, so the reader's staged changes are theirs and two captures in
          flight write two files.
        - **Only where the store lacks that tree**, the sandbox commits it onto the session's base and
          bundles it, and the store fetches the bundle. The base is in the store by construction, so
          the bundle holds what changed since and nothing more. An unchanged worktree, and a fork that
          has not been touched, send nothing at all.
        - **In the store**, the tree is chained onto this session's snapshots, with the tip it read
          as the old value, so two captures racing each other both land rather than one overwriting
          the other.

        The tree the store records is read back from the store, not taken from the sandbox's word:
        a worktree whose git lied about its tree fails here rather than recording a hash it never held.

        **Call this only where the agent is quiescent**, which means at a model-request boundary
        rather than after each tool call. A model may issue several calls in one response and they
        may run at once, and while they do there is no coherent state to capture: `git add -A`
        walks a tree somebody is still writing to, so what it records is a mixture that never
        existed. Between one model request and the next, every tool of the previous batch has
        returned by construction, so the quiescence costs nothing to arrange.
        """
        base = await self.store.commit_at(self.base_ref)
        if base is None:
            raise SnapshotFailed(f"{self.session} has no base in {self.store.path}, so it was never planted")
        with tempfile.TemporaryDirectory(prefix="mainplate-capture-") as made:
            transfer = Path(made)
            shadow = {"GIT_INDEX_FILE": str(transfer / "index")}
            await self.demand("add", "-A", environment=shadow, writable=(transfer,))
            tree = object_id(await self.demand("write-tree", environment=shadow, writable=(transfer,)), "write-tree")
            if not await self.store.holds_tree(tree):
                incoming = f"{self.refs}/incoming/{token_hex(8)}"
                carrying = f"{TRANSFER}/{token_hex(8)}"
                commit = object_id(await self.demand("commit-tree", tree, "-p", base, "-m", why), "commit-tree")
                await self.demand("update-ref", carrying, commit)
                try:
                    tip = await self.store.commit_at(self.snapshots_ref)
                    excluding = (base,) if tip is None else (base, tip)
                    arrived = await self.bundled(carrying, incoming, excluding=excluding, transfer=transfer)
                finally:
                    await self.git("update-ref", "-d", carrying)
                try:
                    held = await self.store.demand("rev-parse", f"{arrived}^{{tree}}")
                    if held != tree:
                        raise SnapshotFailed(f"the worktree said {tree[:8]} and sent {held[:8]}")
                    return await self.chained(tree, why)
                finally:
                    await self.store.git("update-ref", "-d", incoming)
        return await self.chained(tree, why)

    async def chained(self, tree: str, why: str) -> str:
        """A tree the store holds, hung from this session's snapshots unless it is already the tip."""
        for _ in range(CHAINING):
            tip = await self.store.commit_at(self.snapshots_ref)
            if tip is not None and await self.store.demand("rev-parse", f"{tip}^{{tree}}") == tree:
                return tree
            parent = ("-p", tip) if tip is not None else ()
            commit = await self.store.demand("commit-tree", tree, *parent, "-m", why)
            # The empty old value is git's "must not exist yet", so the first link is guarded too.
            moved = await self.store.git("update-ref", self.snapshots_ref, commit, tip or "")
            if moved.ok:
                return tree
        raise SnapshotFailed(f"{self.snapshots_ref} kept moving under {CHAINING} captures")

    async def push(self, url: str, branch: str) -> Ran:
        """
        The worktree's `branch`, pushed to `url` under the same name, from the store.

        **The branch is the caller's, never the worktree's.** It is the one the session recorded, so
        what moves on the remote is what the page names, whatever the worktree's `HEAD` is on: a
        session that checked out `main` and committed there pushes nothing to `main` from here. The
        cost, stated, is that commits on any other branch stay in the worktree without a word, and
        the result says which commit went where.

        Its commit crosses into the store the way a snapshot's does, unless the store already has it,
        and the store pushes from there with its own configuration and this console's credentials. A
        push is never forced: a remote branch that moved on is a refusal to read in the result, not
        one to override.
        """
        head = f"refs/heads/{branch}"
        commit = object_id(await self.demand("rev-parse", "--verify", f"{head}^{{commit}}"), head)
        held = await self.store.git("cat-file", "-t", commit)
        if not (held.ok and held.out == "commit"):
            base = await self.store.commit_at(self.base_ref)
            with tempfile.TemporaryDirectory(prefix="mainplate-push-") as made:
                commit = await self.bundled(
                    head, f"{self.refs}/pushing", excluding=() if base is None else (base,), transfer=Path(made)
                )
        return await self.store.git("push", url, f"{commit}:{head}")

    async def paths(self, tree: str) -> tuple[str, ...]:
        """Every file a tree this session captured holds, asked of the store; see `Store.paths`."""
        return await self.store.paths(tree)

    async def diff(self, before: str, after: str) -> str:
        """The change between two trees this session captured, asked of the store; see `Store.diff`."""
        return await self.store.diff(before, after)


@dataclass(frozen=True, slots=True)
class Worktrees:
    """
    One repository's store, and a worktree of it per session.

    A worktree each rather than one shared tree, and the reason is the one that has run through
    every part of this: two writers in one directory make a snapshot unattributable. With a
    worktree apiece, what a session's snapshot holds is what that session and its reader did, and
    nothing else, and what one session does to its git reaches no other.

    Every worktree borrows the store's objects, so it costs a checkout rather than a clone, and a
    tree captured from one is in the store and so readable from every other. That is what makes a
    fork cheap: the branch point's tree is already an object, so planting the fork at it is a
    checkout of something that exists rather than a copy of anything.
    """

    store: Store
    root: Path
    bwrap: str
    identity: tuple[tuple[str, str], ...]

    def at(self, session: str) -> Path:
        """Where a session's worktree is, or would be, derived from its id and never looked up."""
        return self.root / session

    def worktree(self, session: str) -> Worktree:
        """
        The session's own worktree, whether or not it has been planted.

        A value rather than a lookup, so a caller that only wants to *name* the worktree - a page
        saying where a session works - needs no repository call and cannot fail.
        """
        return Worktree(root=self.at(session), store=self.store, session=session, bwrap=self.bwrap)

    def planted(self, session: str) -> bool:
        """
        Whether this session has a worktree of its own, which is a question with no git in it.

        **The directory, and nothing inside it.** Everything under it is the session's to delete or
        replace, `.git` included, so an answer read from there is one the session chose. The
        directory itself is not: `plant` puts it in place with one rename, and a sandbox cannot
        remove it because it is the mount point the worktree is bound at. So a session that deletes
        its own `.git` is still planted, and what fails is the next capture, which names the cause.
        """
        return self.at(session).is_dir()

    async def default_branch(self) -> str | None:
        """
        What this repository calls its default branch, or nothing where its `HEAD` names no branch.

        Read from the store's own `HEAD`, which `git clone --bare` sets as a symbolic ref to whatever
        the remote's default was. It is the *name* that is wanted rather than the commit: the commit
        under `refs/heads/` is as old as the clone, and the name is what `resolve` turns into the
        current one by preferring the fetched `refs/remotes/origin/` side.

        `None` is a store whose `HEAD` is detached, which `--bare` does not produce from an ordinary
        remote. It is read as "there is no branch name to resolve" and the caller falls back to the
        raw `HEAD`, which is parsing the two shapes a `HEAD` can have rather than a second mechanism.
        """
        named = await self.store.git("symbolic-ref", "--short", "--quiet", "HEAD")
        return named.out or None if named.ok else None

    async def resolve(self, base: str) -> str:
        """
        The commit something a person typed names, as the hash it is.

        `origin/<base>` first and the bare name second, and that ordering is what makes "start at
        `main`" mean today's `main`. The store keeps the branches it was cloned with under
        `refs/heads/`, and those are as old as the clone; a fetch writes the current ones under
        `refs/remotes/origin/`. So the same word names two commits here, and the fresher of them is
        the one somebody typing a branch name means. A tag, a hash and anything with revision syntax
        in it are not under `origin/` at all and fall through to the second try.

        `^{commit}` so a tag object resolves to what it points at rather than to itself, since a
        worktree is planted at a commit and an annotated tag is not one.

        `--verify` and `--quiet`, so a name that resolves to nothing is a failed call naming the
        name rather than git printing the string back and this planting a worktree at a ref that
        does not exist.
        """
        found = await self.store.git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}^{{commit}}")
        if found.ok and found.out:
            return found.out
        return await self.store.demand("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")

    async def plant(
        self, session: str, *, tree: str | None = None, base: str | None = None, branch: str | None = None
    ) -> Worktree:
        """
        The session's worktree, at where it was asked for, made if it is not there already.

        Three ways to say where, and they are ranked rather than combined:

        - `tree` is a **fork**, and it wins over everything. It is a *tree* and `git checkout` wants a
          commit, so one is made for it, which is three lines of text over objects that already exist.
        - `base` is what a session was started at, resolved through `resolve` above.
        - Neither is the repository's default branch, resolved through the *same* call, which is what
          makes a session that said nothing start where the repository is *now*.

        Whichever it is becomes the session's `base` ref in the store, which keeps it reachable and is
        what every capture's bundle is thin against.

        **Built beside where it goes and moved into place**, so a crash part-way leaves a directory
        nothing names rather than a worktree half made. Every git here is in the parent, and that is
        safe for exactly as long as it takes: the directory is this console's until the rename, and
        no session has had a moment to write its configuration.

        `branch` starts one at whatever that came to, and without it the worktree is on **no**
        branch, `--detach` stated rather than left to git so that is a decision rather than a default.

        Idempotent, because the alternative is worse than the check. A worktree already planted is
        one a session has been working in, and re-planting would either fail the request or throw
        that work away.
        """
        if self.planted(session):
            return self.worktree(session)
        if tree is not None:
            commit = await self.store.demand("commit-tree", tree, "-m", f"session {session}")
        elif (named := base if base is not None else await self.default_branch()) is not None:
            commit = await self.resolve(named)
        else:
            commit = await self.store.demand("rev-parse", "HEAD")
        planted = self.worktree(session)
        await self.store.demand("update-ref", planted.base_ref, commit)
        self.root.mkdir(parents=True, exist_ok=True)
        building = Path(tempfile.mkdtemp(prefix=f".planting-{session}-", dir=self.root))
        try:
            gitdir = await self.initialised(building)
            placing = ("-b", branch) if branch is not None else ("--detach",)
            await self.fresh(gitdir, building, "checkout", "--quiet", *placing, commit)
            await asyncio.to_thread(building.rename, self.at(session))
        finally:
            await asyncio.to_thread(shutil.rmtree, building, ignore_errors=True)
        return planted

    async def initialised(self, building: Path) -> Path:
        """
        A new `.git` under `building`, borrowing the store's objects and fetching from the store.

        `origin` is the store and the fetch refspec reads its `refs/remotes/origin/*`, which are the
        ones this console refreshes, so `git fetch` in a session brings the repository's current
        branches without a network or a credential. Nothing can be pushed there: the store is bound
        read-only. The identity this was handed is copied in, so a commit a session makes carries the
        name the person pushing it would give it.
        """
        await git_at(self.root, "init", "--quiet", str(building))
        gitdir = building / POINTER
        (gitdir / "objects" / "info" / "alternates").write_text(f"{self.store.path / 'objects'}\n")
        configured: list[tuple[str, str]] = [
            ("remote.origin.url", str(self.store.path)),
            ("remote.origin.fetch", "+refs/remotes/origin/*:refs/remotes/origin/*"),
            *self.identity,
        ]
        for key, value in configured:
            await self.fresh(gitdir, building, "config", key, value)
        await self.fresh(gitdir, building, "fetch", "--quiet", "--no-tags", "origin")
        return gitdir

    async def fresh(self, gitdir: Path, building: Path, *arguments: str) -> str:
        """Git in the parent against a worktree nobody but this console has written yet."""
        return demanded(
            await git_at(building, "--git-dir", str(gitdir), "--work-tree", str(building), *arguments), arguments
        )

    async def uproot(self, session: str) -> None:
        """
        Take a session's worktree away, leaving everything it recorded behind.

        A worktree is a directory and nothing else: its `.git` is inside it and the store holds no
        record of it, so this is removing it. What it does *not* touch is the session's refs in the
        store, which are what keeps every tree it recorded forkable once the worktree is gone.
        """
        here = self.at(session)
        if here.exists():
            await asyncio.to_thread(shutil.rmtree, here)


async def operator_identity() -> tuple[tuple[str, str], ...]:
    """
    What the operator is called in their own git configuration, for a session's worktree to commit as.

    Read out of the operator's global configuration, which is this console's to read rather than a
    session's, and only the two keys; nothing where neither is set, which leaves git asking a session
    that commits who it is, as git would anywhere else.
    """
    home = os.environ.get("HOME")
    if home is None:
        return ()
    found: list[tuple[str, str]] = []
    for key in ("user.name", "user.email"):
        ran = await git_at(Path(home), "config", "--global", "--get", key, environment={"HOME": home})
        if ran.ok and ran.out:
            found.append((key, ran.out))
    return tuple(found)
