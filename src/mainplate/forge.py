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
from datetime import timedelta
from pathlib import Path
from typing import Final
from typing import Protocol

from mainplate.snapshots import Worktree
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

    def readable(self, identifier: str) -> str:
        """
        What to call this repository, which is `owner/repo` while a forge reaches it and the
        recorded id once none does.

        Not a fallback but the honest reading: the session is still on that repository, and the id
        is all anybody knows about it now. One function rather than the rule written twice, because
        the sidebar and the note under a message box are asking exactly the same question.
        """
        found = self.offers(identifier)
        return found.name if found is not None else identifier

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
    endpoint that lists no models is an endpoint you can select and then cannot use, where a forge
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


# What `git ls-remote --heads` puts in front of every branch it names.
HEADS: Final = "refs/heads/"

# How long a repository has to say what branches it has. Short, because this is a swap somebody is
# watching: a host that is not answering should leave the field taking free text, which is what it
# did before it offered anything, rather than holding the page.
LISTING: Final = timedelta(seconds=5)


def named(listed: str) -> tuple[str, ...]:
    r"""
    Branch names out of what `git ls-remote --heads` printed, which is `<sha>\trefs/heads/<name>`.

    Pure, so the parsing is testable against the shapes git actually prints without a repository to
    ask. The name is whatever follows the prefix and may hold slashes of its own, so this cuts once
    at the prefix rather than splitting on every separator.
    """
    found: list[str] = []
    for line in listed.splitlines():
        _, _, ref = line.partition("\t")
        if ref.startswith(HEADS) and (name := ref.removeprefix(HEADS)):
            found.append(name)
    return tuple(found)


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

    async def refresh(self, repository: Repository) -> None:
        """
        Bring this clone's idea of the remote up to date, so a branch name means today's commit.

        Into `refs/remotes/origin/` and never over `refs/heads/`, which is the whole care here. A
        bare clone's branches live under `refs/heads/` and are as old as the clone; fetching over
        them with a forcing refspec would also walk over a branch a session started and has been
        committing to, which is somebody's work rather than a stale copy. Fetching beside them costs
        one namespace and takes nothing away, and `Worktrees.resolve` is what prefers the fresh side.

        Logged rather than raised, because this is an improvement on what a name resolves to and not
        a precondition for planting: a machine that is offline, or a repository whose integration was
        detached this morning, still gets the worktree it would have got before this existed.
        """
        here = self.at(repository.id)
        fetched = await Worktree(root=here).git(
            "fetch", "--prune", "--tags", repository.url, "+refs/heads/*:refs/remotes/origin/*"
        )
        if not fetched.ok:
            logger.warning(f"could not refresh {repository.name}, so a branch name may be stale: {fetched.err}")

    async def branches(self, repository: Repository) -> tuple[str, ...]:
        """
        What branches this repository has right now, for the page to offer as a starting point.

        Asked of the **remote** rather than of the clone, which is what lets the very first session on
        a repository be started on a branch: there is no clone yet at that moment, and cloning to find
        out what to check out is minutes of network inside a request somebody is waiting on.
        `ls-remote` transfers no objects, so it is one round trip and no disk.

        It also cannot go stale in the way reading the clone would. A clone is refreshed when a
        session plants a worktree in it, so a list read from one would be as old as the last session
        on that repository - which is exactly the trap a named base already had.

        **It promises not to raise**, which is `forge.offers`'s promise one level down: this describes
        an environment rather than deciding anything, and what it produces is a list of suggestions
        beside a field that takes free text. A repository that cannot be reached, a host that hangs,
        a git that is not there: all of them are a field with no completions and nothing else.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            async with asyncio.timeout(LISTING.total_seconds()):
                listed = await Worktree(root=self.root).git("ls-remote", "--heads", "--refs", "--", repository.url)
        except TimeoutError:
            logger.warning(f"{repository.name} did not say what branches it has within {LISTING}")
            return ()
        if not listed.ok:
            logger.warning(f"could not read {repository.name}'s branches: {listed.err}")
            return ()
        return named(listed.out)

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
        await Worktree(root=self.root).demand("clone", "--bare", repository.url, str(here))
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
    scratch: Path
    reaching: Reaching

    def at(self, session: str) -> Path:
        """Where a session's files are, which is a question a page asks and never a call that fails."""
        return self.root / session

    def scratch_at(self, session: str) -> Path:
        """
        Somewhere a session may keep things that are not its repository's.

        Outside the worktree rather than inside it, which is what keeps it out of everything git
        answers: a directory under the worktree is `--others` to `git ls-files`, so it would show up
        in `list` and in `status`, and excluding it means writing an exclusion into a git directory
        that is read-only wherever a command can see it.

        Nothing snapshots this, deliberately and for the reason snapshots are gitignore-aware in the
        first place: going back to before a call should not uninstall what was installed between
        then and now. The cost is the same one an ignored path already carries, that what is in here
        goes stale while the source around it moves back.
        """
        return self.scratch / session

    def worktree(self, session: str) -> Worktree:
        return Worktree(root=self.at(session))

    def named(self, repository: str) -> Repository | None:
        return self.reaching.current.offers(repository)

    async def branches(self, repository: str) -> tuple[str, ...]:
        """
        What a session started on this repository could begin at, or nothing where none can be read.

        Nothing for a repository no forge currently reaches, which is the same answer as one that
        cannot be asked: what this feeds is a list of completions beside a field that takes free
        text, so having none costs a suggestion rather than an ability.
        """
        found = self.named(repository)
        return () if found is None else await self.clones.branches(found)

    async def plant(
        self,
        session: str,
        repository: str,
        *,
        tree: str | None = None,
        base: str | None = None,
        branch: str | None = None,
    ) -> Worktree | None:
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

        **Planting a worktree fetches first**, whether or not a base was named, and that is where a
        person gets to say when this console's copy of a repository catches up. Nothing else here
        ever refreshes a clone: it is made once and would otherwise answer out of whatever the
        repository looked like the first time anybody used it, for as long as the machine lives. So
        starting a session is the refresh, which is both the moment it is affordable and the moment
        somebody actually wants current code.

        Two cases skip it and both would be round trips that cannot change an answer. A clone that
        has just been *made* is current by construction. And a **fork** plants at a recorded tree,
        which is an object this console wrote and therefore already holds.

        Only where the worktree is about to be made, which keeps the cost to one fetch per session
        rather than one per pass: a session's second turn finds its worktree planted and never
        reaches this at all.
        """
        cloning = not self.clones.cloned(repository)
        if cloning:
            found = self.named(repository)
            if found is None:
                return None
            await self.clones.ensure(found)
        worktrees = self.clones.worktrees(repository, self.root)
        if not cloning and tree is None and worktrees.at(session) not in await worktrees.planted():
            reached = self.named(repository)
            if reached is not None:
                await self.clones.refresh(reached)
        return await worktrees.plant(session, tree=tree, base=base, branch=branch)
