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
import shutil
from collections.abc import AsyncIterator
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
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

# Spelled out for the reason the sandbox spells its own out: what git finds is what this console put
# there, so a `PATH` the service happened to be started with cannot decide which `git` runs.
WHERE_GIT_IS: Final = "/usr/bin:/bin:/usr/local/bin"

# What a *linked* worktree has at its root in place of a git directory: one line naming where the
# real one is. Two things guard it and they are in different packages - the sandbox binds it
# read-only, and the file tools refuse it by name because they write from the parent and pass
# through no sandbox - so the name lives here, beside the worktree whose shape it is, rather than in
# either of them. Same reasoning as `roots.py`: a name two packages read belongs to neither.
POINTER: Final = ".git"


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


@dataclass(frozen=True, slots=True)
class Worktree:
    """A session-owned checkout whose Git processes run inside its mount namespace."""

    root: Path
    store: Path | None = None
    store_ref: str | None = None
    trusted: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False)

    @property
    def environment(self) -> Mapping[str, str]:
        return {**IDENTITY, "PATH": WHERE_GIT_IS}

    async def git(self, *arguments: str, index: Path | None = None, at: Path | None = None) -> Ran:
        """Run Git against trusted storage directly, or against a checkout inside bubblewrap."""
        environment: Mapping[str, str] | None = {
            **self.environment,
            **({"GIT_INDEX_FILE": str(index)} if index else {}),
        }
        if self.trusted:
            command = ("git", *arguments)
        else:
            from mainplate.sandbox import Bind
            from mainplate.sandbox import Sandbox
            from mainplate.sandbox import Venue
            from mainplate.sandbox import sandbox_command

            sandbox = Sandbox(places=(Bind(path=self.root, writable=True, name="worktree"),))
            command = (
                sandbox_command(),
                *sandbox.argv(at=str(at or self.root), venue=Venue.CONFINED, environment=environment),
                "git",
                *arguments,
            )
            environment = None
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=at or self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        out, err = await process.communicate()
        return Ran(code=process.returncode or 0, stdout=out, stderr=err)

    async def demand(self, *arguments: str, index: Path | None = None) -> str:
        ran = await self.git(*arguments, index=index)
        if not ran.ok:
            raise SnapshotFailed(f"git {' '.join(arguments)} failed ({ran.code}): {ran.err or ran.out}")
        return ran.out

    async def confirm(self) -> None:
        ran = await self.git("rev-parse", "--is-inside-work-tree")
        if not ran.ok or ran.out != "true":
            raise NotAWorktree(f"{self.root} is not a git worktree: {ran.err or ran.out}")

    async def tip(self) -> str | None:
        ran = await self.git("rev-parse", "--verify", "--quiet", f"{SNAPSHOT_REF}^{{commit}}")
        return ran.out if ran.ok and ran.out else None

    async def capture(self, why: str) -> str:
        """
        Capture a Git-ignore-aware tree without changing the checkout's index or history.

        Git and every program its writable configuration names run inside the checkout's sandbox.
        A bundle is then imported into the trusted repository, so the parent parses an artifact but
        never invokes Git against model-writable metadata. The trusted ref keeps every imported tree
        alive after this checkout is archived.
        """
        async with self.lock:
            gitdir = self.root / ".git"
            index = gitdir / f"mainplate-index-{token_hex(8)}"
            bundle = gitdir / f"mainplate-snapshot-{token_hex(8)}.bundle"
            try:
                await self.demand("add", "-A", index=index)
                tree = await self.demand("write-tree", index=index)
                if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
                    raise SnapshotFailed(f"git write-tree returned an invalid object id: {tree!r}")
                was = await self.tip()
                if was is None or await self.demand("rev-parse", f"{was}^{{tree}}") != tree:
                    parent = ("-p", was) if was is not None else ()
                    commit = await self.demand("commit-tree", tree, *parent, "-m", why)
                    await self.demand("update-ref", SNAPSHOT_REF, commit)
                if self.store is not None and self.store_ref is not None:
                    await self.demand("bundle", "create", str(bundle), SNAPSHOT_REF)
                    store = Worktree(root=self.store, trusted=True)
                    await store.demand("fetch", str(bundle), f"{SNAPSHOT_REF}:{self.store_ref}")
                return tree
            finally:
                index.unlink(missing_ok=True)
                bundle.unlink(missing_ok=True)

    async def paths(self, tree: str) -> tuple[str, ...]:
        listed = await self.demand("ls-tree", "-r", "--name-only", tree)
        return tuple(line for line in listed.splitlines() if line)


@dataclass(frozen=True, slots=True)
class Worktrees:
    """One trusted repository cache and one complete checkout per session."""

    repo: Path
    root: Path

    def at(self, session: str) -> Path:
        return self.root / session

    def snapshot_ref(self, session: str) -> str:
        return f"refs/mainplate/sessions/{session}"

    def worktree(self, session: str) -> Worktree:
        return Worktree(
            root=self.at(session),
            store=self.repo,
            store_ref=self.snapshot_ref(session),
        )

    async def confirm(self) -> None:
        ran = await Worktree(root=self.repo, trusted=True).git("rev-parse", "--is-bare-repository")
        if not ran.ok or ran.out != "true":
            raise NotAWorktree(f"{self.repo} is not a bare git repository: {ran.err or ran.out}")

    async def planted(self) -> frozenset[Path]:
        if not self.root.exists():
            return frozenset()
        return frozenset(path for path in self.root.iterdir() if (path / ".git").is_dir())

    async def default_branch(self) -> str | None:
        repository = Worktree(root=self.repo, trusted=True)
        named = await repository.git("symbolic-ref", "--short", "--quiet", "HEAD")
        return named.out or None if named.ok else None

    async def resolve(self, base: str) -> str:
        repository = Worktree(root=self.repo, trusted=True)
        found = await repository.git(
            "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}^{{commit}}"
        )
        if found.ok and found.out:
            return found.out
        return await repository.demand("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")

    async def plant(
        self, session: str, *, tree: str | None = None, base: str | None = None, branch: str | None = None
    ) -> Worktree:
        """
        Clone an isolated checkout and place it at the requested commit before the session can write it.

        All Git commands here operate on the trusted cache or on a checkout that did not exist before
        this call. Once planted, no parent process invokes Git against the checkout again.
        """
        here = self.at(session)
        if (here / ".git").is_dir():
            return self.worktree(session)
        self.root.mkdir(parents=True, exist_ok=True)
        repository = Worktree(root=self.repo, trusted=True)
        if tree is not None:
            commit = await repository.demand("commit-tree", tree, "-m", f"session {session}")
        elif (named := base if base is not None else await self.default_branch()) is not None:
            commit = await self.resolve(named)
        else:
            commit = await repository.demand("rev-parse", "HEAD")
        planting_ref = f"refs/mainplate/plants/{session}"
        await repository.demand("update-ref", planting_ref, commit)
        try:
            await repository.demand("clone", "--no-checkout", str(self.repo), str(here))
            checkout = Worktree(root=here, trusted=True)
            await checkout.demand("fetch", str(self.repo), planting_ref)
            placing = ("-b", branch) if branch is not None else ("--detach",)
            await checkout.demand("checkout", "--quiet", *placing, commit)
        finally:
            await repository.demand("update-ref", "-d", planting_ref)
        return self.worktree(session)

    async def uproot(self, session: str) -> None:
        here = self.at(session)
        if here.exists():
            await asyncio.to_thread(shutil.rmtree, here)
