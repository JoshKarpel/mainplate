# Where a repository comes from, as one question asked of whatever can answer it.
#
# A **forge** answers exactly one thing: what repositories can this console reach? Everything below
# it - worktrees, snapshots, forking - takes a git directory and never asks how it got there, which
# is what makes this a seam rather than a layer. A second forge is one class here, not an edit in
# four files, and that is deliberate: exe.dev is the one that exists because it is the one we are
# on, and a GitHub App or another git host is the same two answers from a different place.
#
# What a forge does *not* do is hold a credential. On exe.dev there is nothing to hold: the
# integration proxies the repository at a hostname that only resolves inside the VM and injects
# credentials at exe.dev's own edge, so a clone needs nothing on disk and nothing in the
# environment. A forge that does need one is where that decision goes, and it goes there alone.

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mainplate.snapshots import Workspace
from mainplate.snapshots import Worktrees

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Repository:
    """
    One *way of reaching* a repository, which is not the same thing as one repository.

    The distinction is the whole reason `key` exists beside `name`. The same repository can be
    reachable twice over with different rights: on exe.dev, one integration acting as your GitHub
    user and another acting as the app, or one read-only beside one that can push. Those are
    different things to start a session on, and a value keyed by `owner/repo` could not tell them
    apart.

    So `key` is the forge's own identifier for the *attachment* - the integration name on exe.dev -
    and `name` is the repository a person recognises. `id` is what a session records, and it names
    the forge too, because two forges can reach the same attachment name.

    `url` is deliberately *not* recorded anywhere. It is how to reach the repository now, which is
    a discovery-time fact, and it is the same split the models already have: a session records what
    answered it, and what reaches that is resolved fresh.
    """

    forge: str
    key: str
    name: str
    url: str

    @property
    def id(self) -> str:
        return f"{self.forge}:{self.key}"


class Forge(Protocol):
    """
    Somewhere repositories come from.

    A protocol rather than a base class, for the reason `Endpoint` is one: there is no shared
    implementation to inherit. What an exe.dev VM does to answer this is read one hostname that
    exists only inside it; what a GitHub App would do is sign a JWT and call an API. The two share
    the question and nothing else.
    """

    @property
    def name(self) -> str:
        """What this forge is called where a repository id names it."""
        ...

    async def offers(self) -> tuple[Repository, ...]:
        """
        Every repository this forge can currently reach, or nothing at all where it cannot answer.

        Nothing rather than raising, and that is the contract rather than a convenience: a forge
        answers about an environment this process merely happens to be in, so "not on exe.dev" and
        "no integrations attached" are ordinary answers. A forge that raised would turn a console
        being run somewhere else into a console that will not start.
        """
        ...


@dataclass(frozen=True, slots=True)
class Reachable:
    """
    Every repository every forge offers, as one value replaced whole rather than edited.

    The same shape as `Catalogue`, and for the same reasons: a page reads it out of memory rather
    than reaching a forge, and a reader holding one holds a consistent answer even if a newer one
    lands mid-render.
    """

    repositories: tuple[Repository, ...]

    @property
    def by_id(self) -> Mapping[str, Repository]:
        return {repository.id: repository for repository in self.repositories}

    def offers(self, identifier: str) -> Repository | None:
        """
        The repository this id names, or nothing where no forge currently reaches it.

        Asked when a session is *started*, which is form validation: a select is a suggestion the
        page made rather than a constraint on what can be posted.
        """
        return self.by_id.get(identifier)

    def labelled(self) -> tuple[tuple[Repository, str], ...]:
        """
        Every repository with what to call it, disambiguated only where it has to be.

        Two attachments of the same repository - one acting as you and one as an app, say - would
        otherwise be two identical rows in a picker, and picking between them would be guessing.
        Naming the attachment on *every* row instead would put an integration name in front of
        somebody on the far more common case where there is only one, so the qualifier appears
        exactly where it distinguishes something.
        """
        seen = Counter(repository.name for repository in self.repositories)
        return tuple(
            (repository, f"{repository.name} ({repository.key})" if seen[repository.name] > 1 else repository.name)
            for repository in self.repositories
        )


async def discover(forges: Sequence[Forge]) -> Reachable:
    """
    Ask every forge what it reaches, all at once.

    Concurrent because forges are independent and each is a round trip. A forge that answers with
    nothing contributes nothing, which is *not* the failure `catalogue.discover` refuses: an
    endpoint that lists no models is a profile you can select and then cannot use, where a forge
    reaching no repositories is an ordinary state of a machine that has none attached.
    """
    found = await asyncio.gather(*(forge.offers() for forge in forges), return_exceptions=True)
    reached: list[Repository] = []
    for forge, offered in zip(forges, found, strict=True):
        if isinstance(offered, BaseException):
            # Logged rather than raised, because `offers` promises not to raise and one that does
            # is that forge's mistake rather than a reason the console cannot start.
            logger.warning(f"forge {forge.name!r} could not say what it reaches: {offered!r}")
            continue
        reached.extend(offered)
    return Reachable(repositories=tuple(reached))


@dataclass(slots=True)
class Reaching:
    """The current answer, held so the readers can outlive any one of them. See `Catalogues`."""

    current: Reachable


@dataclass(frozen=True, slots=True)
class Clones:
    """
    Where this console keeps the repositories it has been given, one bare clone each.

    **Bare**, and that is the whole shape of it: a bare repository has no working tree, so there is
    no "main" checkout to be confused with a session's, and every worktree is a linked one made
    from the same object store. It is also what makes a fork cheap, since the tree a fork checks
    out is already an object here.

    Clones live under one root and are named by the repository id rather than by its URL, so the
    same repository reached through a different forge tomorrow is still the same directory.
    """

    root: Path

    def at(self, repository: str) -> Path:
        return self.root / f"{repository.replace('/', '%')}.git"

    def worktrees(self, repository: str, under: Path) -> Worktrees:
        """The worktrees of one repository, which is what a session is actually planted in."""
        return Worktrees(repo=self.at(repository), root=under)

    def cloned(self, repository: str) -> bool:
        """Whether this repository is already on disk, which is a question with no I/O in it."""
        return (self.at(repository) / "HEAD").exists()

    async def ensure(self, repository: Repository) -> Path:
        """
        The repository on disk, cloned if this is the first time it has been asked for.

        Idempotent, so a second session on the same repository is a worktree rather than a second
        clone. The clone is bare and the URL is passed as an argument to `git clone` deliberately:
        on exe.dev it carries no credential at all, because there is none to carry.
        """
        here = self.at(repository.id)
        if self.cloned(repository.id):
            return here
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info(f"cloning {repository.name} from {repository.forge}")
        await Workspace(root=self.root).demand("clone", "--bare", repository.url, str(here))
        return here


@dataclass(frozen=True, slots=True)
class Workspaces:
    """
    Where a session's files come from and where they live: clones of repositories, worktrees of
    clones.

    One value rather than three passed around together, because the three only mean anything as a
    set: a worktree is of a clone, and a clone is of something a forge reaches. It is what the
    worker is handed to make a session's files exist, and what the service is handed to say where
    they are.
    """

    clones: Clones
    root: Path
    reaching: Reaching

    def at(self, session: str) -> Path:
        """Where a session's files are, which is a question a page asks and never a call that fails."""
        return self.root / session

    def workspace(self, session: str) -> Workspace:
        return Workspace(root=self.at(session))

    def named(self, repository: str) -> Repository | None:
        return self.reaching.current.offers(repository)

    async def plant(self, session: str, repository: str, *, tree: str | None = None) -> Workspace | None:
        """
        A session's worktree, cloning the repository first if this console has not seen it before.

        Idempotent at both levels, so this is what every pass calls and only the first one does any
        work: a clone that exists is reused, and a worktree that exists is left exactly as the
        session left it.

        A repository **no forge currently reaches is still usable once cloned**, which is the same
        stance a model missing from the catalogue gets. An integration detached this morning does
        not strand a conversation whose files are already on disk; what it stops is starting a new
        session on one that was never cloned, and that is the honest failure because there is
        nowhere to get it from.
        """
        if not self.clones.cloned(repository):
            found = self.named(repository)
            if found is None:
                return None
            await self.clones.ensure(found)
        worktrees = self.clones.worktrees(repository, self.root)
        return await worktrees.plant(session, tree=tree)
