# Where a repository comes from, as one question asked of whatever can answer it.
#
# A **forge** answers exactly one thing: what repositories can this console reach? Everything below
# it - checkouts, snapshots, forking - takes a git directory and never asks how it got there, which
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
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Final
from typing import Protocol

from mainplate.sandbox import NoSandbox
from mainplate.snapshots import Checkout
from mainplate.snapshots import Checkouts
from mainplate.snapshots import Ran
from mainplate.snapshots import Snapshot
from mainplate.snapshots import Store
from mainplate.snapshots import demanded
from mainplate.snapshots import git_at

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

    web: str | None = None
    """
    Where a person reads the repository in a browser, or nothing where the forge cannot say.

    The forge's to fill because only it knows what is on the other side: exe.dev's integration is
    GitHub behind a proxy, so the page is GitHub's for the same `owner/repo`, and nothing asks.
    """

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
        the sidebar and the session's card in the rail are asking exactly the same question.
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


@dataclass(frozen=True, slots=True)
class Fetched:
    """
    The last time this console's copy of a repository asked the forge for its branches, and how it went.

    `failed` is what git said where it did not work, and nothing where it did. A clone counts, since
    a clone just made is as current as a fetch would have left it.
    """

    at: datetime
    failed: str | None = None


@dataclass(slots=True)
class Fetches:
    """
    What the last fetch of each repository came to, by repository id, held for the page to read.

    Rebound and never edited, as `Footprints` is, and in memory only: a restart forgets it, and the
    page says a repository has not been fetched since the console started until the first round,
    which starts at once. It is a reading of the control plane, not anything anybody said.
    """

    current: Mapping[str, Fetched] = field(default_factory=dict)


def utc_now() -> datetime:
    """The moment it is, which is what `Workspaces.clock` reads unless a test hands it another."""
    return datetime.now(UTC)


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
    Where this console keeps the repositories it has been given, one store each.

    **Bare**, and that is the whole shape of it: a store is a bare clone, which has no working tree,
    so there is no "main" checkout to be confused with a session's. Each is the `Store` every
    session's checkout of that repository borrows its objects from, which is also what makes a fork
    cheap, since the tree a fork checks out is already an object here.

    Stores live under one root and are named by the repository id rather than by its URL, so the
    same repository reached through a different forge tomorrow is still the same directory.
    """

    root: Path

    def at(self, repository: str) -> Path:
        """Where a repository's store is, or would be, which is a question with no I/O in it."""
        return self.root / f"{repository.replace('/', '%')}.git"

    def store(self, repository: str) -> Store:
        """
        A repository's store, as the value git in the parent runs against, whether or not it is cloned.

        A value rather than a lookup, like `Checkouts.checkout`, so naming one costs nothing and
        cannot fail; the first git against a store not cloned yet is what says so.
        """
        return Store(path=self.at(repository))

    def checkouts(self, repository: str, under: Path, bwrap: str, identity: tuple[tuple[str, str], ...]) -> Checkouts:
        """The checkouts of one repository, which is what a session is actually planted in."""
        return Checkouts(store=self.store(repository), root=under, bwrap=bwrap, identity=identity)

    def cloned(self, repository: str) -> bool:
        """Whether this repository is already on disk, which is a question with no I/O in it."""
        return (self.at(repository) / "HEAD").exists()

    async def refresh(self, repository: Repository) -> Ran:
        """
        Bring this store's idea of the remote up to date, so a branch name means today's commit.

        Into `refs/remotes/origin/` and never over `refs/heads/`, which is the whole care here. The
        store's own branches live under `refs/heads/` and are as old as the clone; fetching over
        them with a forcing refspec would also walk over a branch a session started and has been
        committing to, which is somebody's work rather than a stale copy. Fetching beside them costs
        one namespace and takes nothing away, and `Checkouts.resolve` is what prefers the fresh side.

        **Nothing unreachable is ever pruned from the store**, and this is where that is set, before
        every fetch, because a fetch is what takes a commit a checkout borrows out of the store's
        reach: `--prune` drops a branch the remote deleted and the forcing refspec drops a commit it
        rewrote, and the fetch then runs git's automatic `gc` itself. A checkout borrows the store's
        objects without the store knowing which, so a commit a session merged from a branch the
        remote has since deleted is one only the checkout still refers to, and a `gc` pruning it
        would break that checkout's history. Set
        here rather than once at the clone, so a store cloned before the setting was written gets it
        at its next fetch, and before anything that fetch drops can be pruned. A store that will not
        take the setting is not fetched, and that is reported the way a failed fetch is. **Read
        before it is written**, because a write takes the configuration's lock and git does not wait
        for one: two refreshes of one store at once, the loop's round and a session being planted,
        would race for it, and the one that lost would skip its fetch and report a failure the
        remote never had. The cost, stated: a store only grows, and every fetch is one more `git
        config` read beside it.

        Logged rather than raised, because this is an improvement on what a name resolves to and not
        a precondition for planting: a machine that is offline, or a repository whose integration was
        detached this morning, still gets the checkout it would have got anyway. The store keeps the
        last refs it fetched, so a failed round leaves sessions exactly as current as the one before
        it and the next round tries again. What git said comes back, so the caller can hold it where
        a page can say so; see `Workspaces.refresh`.
        """
        store = self.store(repository.id)
        held = await store.git("config", "--get", "gc.pruneExpire")
        kept = held if held.out == "never" else await store.git("config", "gc.pruneExpire", "never")
        fetched = (
            await store.git("fetch", "--prune", "--tags", repository.url, "+refs/heads/*:refs/remotes/origin/*")
            if kept.ok
            else kept
        )
        if not fetched.ok:
            logger.warning(f"could not refresh {repository.name}, so a branch name may be stale: {fetched.err}")
        return fetched

    async def branches(self, repository: Repository) -> tuple[str, ...]:
        """
        What branches this repository has right now, for the page to offer as a starting point.

        Asked of the **remote** rather than of the store, which is what lets the very first session on
        a repository be started on a branch: there is no store yet at that moment, and cloning to find
        out what to check out is minutes of network inside a request somebody is waiting on.
        `ls-remote` transfers no objects, so it is one round trip and no disk.

        It also cannot go stale in the way reading the store would. A store is refreshed only while
        some session works in it, so a list read from one would be as old as the last such session -
        which is exactly the trap a named base already had.

        **It promises not to raise**, which is `forge.offers`'s promise one level down: this describes
        an environment rather than deciding anything, and what it produces is a list of suggestions
        beside a field that takes free text. A repository that cannot be reached, a host that hangs,
        a git that is not there: all of them are a field with no completions and nothing else.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            async with asyncio.timeout(LISTING.total_seconds()):
                listed = await git_at(self.root, "ls-remote", "--heads", "--refs", "--", repository.url)
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

        Idempotent, so a second session on the same repository plants a checkout from the store
        rather than making a second clone. The store is bare and the URL is passed as an argument to
        `git clone` deliberately: on exe.dev it carries no credential at all, because there is none to
        carry.

        What keeps a store from pruning what a checkout borrows is set by `refresh`, before the first
        fetch that could take any of it out of reach, which nothing does to a store just cloned.
        """
        here = self.at(repository.id)
        if not self.cloned(repository.id):
            self.root.mkdir(parents=True, exist_ok=True)
            logger.info(f"cloning {repository.name} from {repository.forge}")
            cloning = ("clone", "--bare", repository.url, str(here))
            demanded(await git_at(self.root, *cloning), cloning)
            # A clone just made is current, so its branches are what a `refresh` would have fetched.
            # Copied under `refs/remotes/origin/` from the store itself, with no network, because
            # that is the namespace `resolve` prefers and a checkout's `git fetch` reads.
            await self.store(repository.id).demand("fetch", "--quiet", ".", "+refs/heads/*:refs/remotes/origin/*")
        return here


@dataclass(frozen=True, slots=True)
class Workspaces:
    """
    Where a session's files come from and where they live: stores of repositories, checkouts of
    stores.

    One value rather than several passed around together, because they only mean anything as a
    set: a checkout is of a store, a store is of something a forge reaches, and git runs against a
    checkout only behind `bwrap`. It is what the worker is handed to make a session's files exist,
    and what the service is handed to say where they are.
    """

    clones: Clones
    root: Path
    scratch: Path
    reaching: Reaching

    bwrap: str | None
    """
    What confines every git that reads a checkout, or nothing on a machine without it.

    Nothing means no session works in a repository here: a checkout's configuration is its
    session's to write, so a machine that cannot confine git has nowhere safe to snapshot one.
    `app.py` offers no repository on such a machine, and anything that reaches for a checkout
    regardless is refused with `NoSandbox`.
    """

    identity: tuple[tuple[str, str], ...]
    """
    The git `user.*` keys every checkout planted from here is configured with.

    Handed in rather than read while planting, so what a session commits as is decided by whoever
    builds this: the operator's own configuration, read once at startup, or a stated identity for a
    suite that must commit the same way on a machine with none. The cost, stated: a change to the
    operator's global identity reaches new sessions only after a restart.
    """

    fetches: Fetches = field(default_factory=Fetches)
    """What each store's last clone or fetch came to, which the dashboard draws. See `refresh`."""

    clock: Callable[[], datetime] = utc_now
    """
    What a fetch is stamped with, handed in so a test can say when "now" is.

    The only moment this reads: a fetch's record is the one thing here that depends on when it
    happened rather than on what git said.
    """

    async def refresh(self, repository: Repository) -> None:
        """
        Fetch one store and hold what came of it, which is the one way anything here fetches.

        One method for planting and for the background loop both, so the page's "fetched at" is the
        last fetch of either kind rather than the last one the loop happened to make.
        """
        came = await self.clones.refresh(repository)
        self.fetched(repository.id, None if came.ok else came.err or came.out or f"git exited {came.code}")

    def fetched(self, repository: str, failed: str | None) -> None:
        """
        Hold what one store's last clone or fetch came to, stamped now, for the dashboard to draw.

        Rebinds `fetches.current` to a new mapping rather than writing into it, so a page reading the
        old one mid-render reads a whole answer.
        """
        self.fetches.current = {**self.fetches.current, repository: Fetched(at=self.clock(), failed=failed)}

    def at(self, session: str) -> Path:
        """Where a session's files are, which is a question a page asks and never a call that fails."""
        return self.root / session

    def scratch_at(self, session: str) -> Path:
        """
        Somewhere a session may keep things that are not its repository's.

        Outside the checkout rather than inside it, which is what keeps it out of everything git
        answers: a directory under the checkout is `--others` to `git ls-files`, so it would show up
        in `list` and in `status`, and the only place to write an exclusion is the checkout's own
        `.git/info/exclude`, which anything the session runs could take out again.

        Nothing snapshots this, deliberately and for the reason snapshots are gitignore-aware in the
        first place: going back to before a call should not uninstall what was installed between
        then and now. The cost is the same one an ignored path already carries, that what is in here
        goes stale while the source around it moves back.
        """
        return self.scratch / session

    def checkouts(self, repository: str) -> Checkouts:
        """Every session's checkout of one repository, refused where nothing could confine git in one."""
        if self.bwrap is None:
            raise NoSandbox(f"nothing confines git on this machine, so no session may work in {repository!r}")
        return self.clones.checkouts(repository, self.root, self.bwrap, self.identity)

    def checkout(self, session: str, repository: str) -> Checkout:
        """
        A session's checkout, knowing which store it borrows from, which is what lets a snapshot of it
        be kept after the checkout is gone.

        The repository is asked for rather than derived from the session because nothing here holds
        a session's choice, and the callers all have it: a checkout is only ever named for a session
        that picked a repository.
        """
        return self.checkouts(repository).checkout(session)

    def named(self, repository: str) -> Repository | None:
        """The repository an id names while some forge reaches it, read from the current answer."""
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
        snapshot: Snapshot | None = None,
        base: str | None = None,
        branch: str | None = None,
    ) -> Checkout | None:
        """
        A session's checkout, cloning the repository first if this console has not seen it before.

        Idempotent at both levels, so this is what every pass calls and only the first one does any
        work: a store that exists is reused, and a checkout that exists is left exactly as the
        session left it.

        A repository **no forge currently reaches is still usable once cloned**, which is the same
        stance a model missing from the catalogue gets. An integration detached this morning does
        not strand a conversation whose files are already on disk; what it stops is starting a new
        session on one that was never cloned, and that is the honest failure because there is
        nowhere to get it from.

        **Planting a checkout fetches first**, whether or not a base was named, because starting a
        session is the moment somebody wants current code and is waiting on it. The background
        loop in `fetching.py` keeps the store current after that, while the session works; this
        fetch is what makes a fast-moving repository's new session start on the `main` of now rather
        than of up to one round ago.

        Two cases skip it and both would be round trips that cannot change an answer. A store that
        has just been *cloned* is current by construction. And a **fork** plants at a recorded
        snapshot, whose tree and commit a capture already carried into the store - where there is a
        commit. A parent whose `HEAD` named none, on an orphan branch, recorded a tree alone, and its
        fork stands on the default branch the way a new session does, so it fetches the way one does:
        skipped, it would stand on a `main` up to a fetch interval old, which is a new session's plant
        without the one step that makes it mean the repository as it is now.

        Only where the checkout is about to be made, which keeps the cost to one fetch per session
        rather than one per pass: a session's second turn finds its checkout planted and never
        reaches this at all.
        """
        checkouts = self.checkouts(repository)
        cloning = not self.clones.cloned(repository)
        if cloning:
            found = self.named(repository)
            if found is None:
                return None
            await self.clones.ensure(found)
            self.fetched(repository, None)
        at_recorded_commit = snapshot is not None and snapshot.head is not None
        if not cloning and not at_recorded_commit and not checkouts.planted(session):
            reached = self.named(repository)
            if reached is not None:
                await self.refresh(reached)
        return await checkouts.plant(session, snapshot=snapshot, base=base, branch=branch)
