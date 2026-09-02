# The worktree as something a session can go back to, recorded beside what was said about it.
#
# A snapshot is a git *tree*, taken through a shadow index so nothing the reader can see moves: not
# their staged changes, not `HEAD`, not a branch, not `git log`. The trees are chained into commits
# under one ref of this program's own, which is the only reason they survive `git gc`; an
# unreferenced tree is unreachable and gets pruned, so the chain is what makes yesterday's snapshot
# still be there tomorrow.
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
# always *forward* into a new session: a fork plants a clean worktree at a recorded tree rather than
# putting an existing one back, which is a thing no reader of this module can do at all.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Final

# One ref, with the snapshots chained through it by parent, rather than a ref per snapshot. Under
# `refs/` but outside `refs/heads/`, so it is not a branch: it does not appear in `git branch`, is
# not walked by a bare `git log`, and cannot be checked out by accident.
SNAPSHOT_REF: Final = "refs/mainplate/snapshots"

# What `commit-tree` is told to put in the author and committer fields. Supplied rather than left
# to the machine's own configuration, because a snapshot must not fail on a checkout where nobody
# has set `user.email`, and because these commits are this program's rather than anybody's.
IDENTITY: Final[Mapping[str, str]] = {
    "GIT_AUTHOR_NAME": "mainplate",
    "GIT_AUTHOR_EMAIL": "mainplate@localhost",
    "GIT_COMMITTER_NAME": "mainplate",
    "GIT_COMMITTER_EMAIL": "mainplate@localhost",
}


class NotAWorktree(ValueError):
    """
    A worktree was configured that is not a git repository.

    Loud, and at startup, for the reason an unusable `config.yaml` is: a console that accepted the
    path and silently recorded no snapshots would look like it was keeping a history it was not,
    and the first time anybody wanted to go back would be the first time they found out.
    """


class SnapshotFailed(RuntimeError):
    """A git invocation this depends on did not succeed, carrying what git said about it."""


@dataclass(frozen=True, slots=True)
class Ran:
    """What one git invocation came to."""

    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass(frozen=True, slots=True)
class Worktree:
    """
    A git worktree this console can snapshot and put back.

    Frozen, and holding only paths: every method is an effect against the repository rather than
    against anything held here, so two callers sharing one of these share no state.
    """

    root: Path

    @asynccontextmanager
    async def staging(self) -> AsyncIterator[Path]:
        """
        A shadow index for the length of one operation, and never `.git/index`.

        Not the real index, because the reader's staged changes are theirs: building a snapshot
        through it would stage their whole working tree out from under them, and `git add` there
        takes `.git/index.lock`, so it would fight whatever they were running at the time.

        A fresh one *per operation* rather than one per worktree, because two of these can be in
        flight at once. A single shared path is a file two concurrent captures would write over
        each other, and the loser's `write-tree` would then describe a tree that never existed.

        The directory is asked for rather than assumed to be `.git`, because in a *linked* worktree
        it is not: `.git` there is a file holding a pointer, and every session having a worktree of
        its own means almost every worktree here is a linked one.
        """
        index = Path(await self.demand("rev-parse", "--absolute-git-dir")) / f"mainplate-index-{token_hex(8)}"
        try:
            yield index
        finally:
            index.unlink(missing_ok=True)

    async def git(self, *arguments: str, index: Path | None = None) -> Ran:
        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **IDENTITY,
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                **({"GIT_INDEX_FILE": str(index)} if index else {}),
            },
        )
        out, err = await process.communicate()
        return Ran(code=process.returncode or 0, out=out.decode().strip(), err=err.decode().strip())

    async def demand(self, *arguments: str, index: Path | None = None) -> str:
        ran = await self.git(*arguments, index=index)
        if not ran.ok:
            raise SnapshotFailed(f"git {' '.join(arguments)} failed ({ran.code}): {ran.err or ran.out}")
        return ran.out

    async def confirm(self) -> None:
        """That this is a git worktree at all, asked once at startup rather than at the first turn."""
        ran = await self.git("rev-parse", "--is-inside-work-tree")
        if not ran.ok or ran.out != "true":
            raise NotAWorktree(f"{self.root} is not a git worktree: {ran.err or ran.out}")

    async def tip(self) -> str | None:
        """The commit the snapshot chain currently points at, or nothing before the first one."""
        ran = await self.git("rev-parse", "--verify", "--quiet", f"{SNAPSHOT_REF}^{{commit}}")
        return ran.out if ran.ok and ran.out else None

    async def capture(self, why: str) -> str:
        """
        The worktree as a tree object, recorded so it stays reachable, and its hash.

        The tree is what a checkpoint holds, and the commit exists only to keep it alive: git
        prunes an object nothing refers to, so a bare `write-tree` would be a hash that stops
        resolving at the next `gc`. Chaining each commit onto the last is what keeps every earlier
        snapshot reachable through one ref rather than needing a ref apiece.

        An unchanged worktree writes nothing at all. The tree hash is the content, so a turn that
        touched no file produces the hash the last one did, and the commit is skipped: the cost of
        snapshotting tracks what actually changed rather than how often this is called.

        **Call this only where the agent is quiescent**, which means at a model-request boundary
        rather than after each tool call. A model may issue several calls in one response and they
        may run at once, and while they do there is no coherent state to capture: `git add -A`
        walks a tree somebody is still writing to, so what it records is a mixture that never
        existed. Between one model request and the next, every tool of the previous batch has
        returned by construction, so the quiescence costs nothing to arrange.
        """
        async with self.staging() as index:
            await self.demand("add", "-A", index=index)
            tree = await self.demand("write-tree", index=index)
        was = await self.tip()
        if was is not None and await self.demand("rev-parse", f"{was}^{{tree}}") == tree:
            return tree
        parent = ("-p", was) if was is not None else ()
        commit = await self.demand("commit-tree", tree, *parent, "-m", why)
        await self.demand("update-ref", SNAPSHOT_REF, commit)
        return tree

    async def paths(self, tree: str) -> tuple[str, ...]:
        listed = await self.demand("ls-tree", "-r", "--name-only", tree)
        return tuple(line for line in listed.splitlines() if line)


@dataclass(frozen=True, slots=True)
class Worktrees:
    """
    One repository, and a worktree of it per session.

    A worktree each rather than one shared tree, and the reason is the one that has run through
    every part of this: two writers in one directory make a snapshot unattributable. The person is
    always one of those writers, so even a console answering a single session at a time has two;
    with a tree apiece, what a session's snapshot holds is what that session and its reader did,
    and nothing else.

    They share the repository's object store, so a worktree costs a checkout rather than a clone,
    and a tree captured in one is immediately readable from every other. That is what makes a fork
    cheap: the branch point's tree is already an object, so planting the fork's worktree at it is a
    checkout of something that exists rather than a copy of anything.
    """

    repo: Path
    root: Path

    def at(self, session: str) -> Path:
        return self.root / session

    def worktree(self, session: str) -> Worktree:
        """
        The session's own worktree, as something to snapshot, whether or not it has been planted.

        A value rather than a lookup, so a caller that only wants to *name* the worktree - a page
        saying where a session works - needs no repository call and cannot fail.
        """
        return Worktree(root=self.at(session))

    async def confirm(self) -> None:
        """That the repository is one, once at startup rather than at the first session."""
        await Worktree(root=self.repo).confirm()

    async def planted(self) -> frozenset[Path]:
        """Every worktree this repository currently has, by where it sits."""
        listed = await Worktree(root=self.repo).demand("worktree", "list", "--porcelain")
        return frozenset(
            Path(line.removeprefix("worktree ")) for line in listed.splitlines() if line.startswith("worktree ")
        )

    async def plant(self, session: str, *, tree: str | None = None) -> Worktree:
        """
        The session's worktree, checked out at `tree`, made if it is not there already.

        `tree` is a *tree* and `git worktree add` wants a commit, so one is made for it. That is
        not a wasted object: a commit for a tree is three lines of text, the tree and its blobs
        already exist, and being a worktree's `HEAD` is what keeps the whole thing reachable when
        `gc` runs.

        `None` plants at the repository's own `HEAD`, which is what a session started rather than
        forked wants: begin where the repository is.

        Idempotent, because the alternative is worse than the check. A worktree already planted is
        one a session has been working in, and re-planting would either fail the request or throw
        that work away.
        """
        here = self.at(session)
        if here in await self.planted():
            return Worktree(root=here)
        self.root.mkdir(parents=True, exist_ok=True)
        repository = Worktree(root=self.repo)
        commit = (
            await repository.demand("rev-parse", "HEAD")
            if tree is None
            else await repository.demand("commit-tree", tree, "-m", f"session {session}")
        )
        await repository.demand("worktree", "add", "--detach", str(here), commit)
        return Worktree(root=here)

    async def uproot(self, session: str) -> None:
        """
        Take a session's worktree away, leaving everything it recorded behind.

        Nothing calls this yet, and it is here because the pair is the thing worth getting right:
        a worktree left behind after its session is gone is a directory nothing will ever look at
        and `git worktree list` will keep naming. What it does *not* touch is the snapshots, which
        live in the object store under a ref of their own and outlive any worktree.
        """
        here = self.at(session)
        if here in await self.planted():
            await Worktree(root=self.repo).demand("worktree", "remove", "--force", str(here))
