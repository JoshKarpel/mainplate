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
import re
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

    **A branch and not a detached `HEAD`**, which is what this used to leave, and the reason is that
    committing is now something somebody does here: `Run` puts `git commit` in the box under the
    conversation, and a commit on a detached `HEAD` is reachable only through the reflog. Reading,
    editing and every question `git` answers are fine detached; the one thing that is not is the
    thing this console just made easy.

    Named from the **session id**, so it is unique by construction: `git worktree add -b` refuses a
    name already in use, and two sessions on one repository must both be able to plant. That is also
    why it is not derived from the session's title - two sessions opened with the same message would
    collide, so the id would have to be in the name anyway, and a title is prose where a ref is not.
    What a session is *called* is in the sidebar; this only has to be somewhere commits can live.

    The cost, stated: one local branch per session in the bare clone, accumulating, with nothing
    pruning them. `git branch --list 'mainplate/*'` is what finds them, which is what the prefix is
    for.
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

    async def default_branch(self) -> str | None:
        """
        What this repository calls its default branch, or nothing where its `HEAD` names no branch.

        Read from the clone's own `HEAD`, which `git clone --bare` sets as a symbolic ref to whatever
        the remote's default was. It is the *name* that is wanted rather than the commit: the commit
        under `refs/heads/` is as old as the clone, and the name is what `resolve` turns into the
        current one by preferring the fetched `refs/remotes/origin/` side.

        `None` is a clone whose `HEAD` is detached, which `--bare` does not produce from an ordinary
        remote. It is read as "there is no branch name to resolve" and the caller falls back to the
        raw `HEAD`, which is parsing the two shapes a `HEAD` can have rather than a second mechanism.
        """
        named = await Worktree(root=self.repo).git("symbolic-ref", "--short", "--quiet", "HEAD")
        return named.out or None if named.ok else None

    async def resolve(self, base: str) -> str:
        """
        The commit something a person typed names, as the hash it is.

        `origin/<base>` first and the bare name second, and that ordering is what makes "start at
        `main`" mean today's `main`. A bare clone keeps the branches it was made with under
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
        repository = Worktree(root=self.repo)
        found = await repository.git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}^{{commit}}")
        if found.ok and found.out:
            return found.out
        return await repository.demand("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")

    async def plant(
        self, session: str, *, tree: str | None = None, base: str | None = None, branch: str | None = None
    ) -> Worktree:
        """
        The session's worktree, checked out where it was asked for, made if it is not there already.

        Three ways to say where, and they are ranked rather than combined:

        - `tree` is a **fork**, and it wins over everything. It is a *tree* and `git worktree add`
          wants a commit, so one is made for it. That is not a wasted object: a commit for a tree is
          three lines of text, the tree and its blobs already exist, and being a worktree's `HEAD` is
          what keeps the whole thing reachable when `gc` runs.
        - `base` is what a session was started at, resolved through `resolve` above.
        - Neither is the repository's default branch, resolved through the *same* call, which is what
          makes a session that said nothing start where the repository is *now*. Reading the clone's
          own `HEAD` commit instead would be reading a value as old as the clone, so a fetch before
          this would refresh `refs/remotes/origin/` and then plant at the stale commit beside it -
          a round trip that changes nothing, which is worse than not making it.

        Ranked and not merged because a fork's tree is the answer to a question a base cannot also
        answer: a branch re-asks its turn against the files that turn saw, so a base beside it would
        be two claims about one checkout. `Service.fork` never sends both, and this is what makes
        that impossible to get wrong from here.

        `branch` starts one at whatever that came to. Without it the worktree is on **no** branch,
        and `--detach` is stated rather than left to git so that is a decision rather than a default -
        though nothing reaches here without one for a session in a repository, since `Choice.branching`
        fills it. This stays answerable either way because it is the layer below that decision: what
        it is handed is what it does.

        Naming a base cannot check that branch out instead, which is the question `branch` answers
        and the reason the two are separate fields: git refuses a branch another worktree already
        holds, so two sessions started at `main` would mean the second failing to plant at all.

        What a branch buys is somewhere for a commit to go, since a detached `HEAD` has nowhere. It
        has to be a name nothing is using, and `git worktree add -b` refusing one that exists is the
        honest failure - two sessions on one branch would be two writers in one history, and the
        whole reason each session gets a worktree of its own is that two writers make a record
        unattributable.

        Idempotent, because the alternative is worse than the check. A worktree already planted is
        one a session has been working in, and re-planting would either fail the request or throw
        that work away.
        """
        here = self.at(session)
        if here in await self.planted():
            return Worktree(root=here)
        self.root.mkdir(parents=True, exist_ok=True)
        repository = Worktree(root=self.repo)
        if tree is not None:
            commit = await repository.demand("commit-tree", tree, "-m", f"session {session}")
        elif (named := base if base is not None else await self.default_branch()) is not None:
            commit = await self.resolve(named)
        else:
            commit = await repository.demand("rev-parse", "HEAD")
        placing = ("-b", branch) if branch is not None else ("--detach",)
        await repository.demand("worktree", "add", *placing, str(here), commit)
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
