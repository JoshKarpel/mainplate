# Where a command a model asked for actually runs, and what it can reach from there.
#
# The boundary here is a *mount namespace*, not a list of commands that are allowed. A denylist over
# commands loses on contact with reality: `git stash` reads as safe and reverts the worktree, `git
# config` can set `core.hooksPath`, and a release next year adds something nobody has classified. A
# mount says what a process can reach and is therefore already right about commands nobody has
# thought of, including the ones a repository's own build script runs.
#
# **Per call, never a long-lived executor.** A pass re-runs the conversation body from the top and
# `wrap_tool_execute` *replays* recorded results rather than re-running them, so a sandbox holding
# state between calls would offer that state on a first pass and withhold it on a resumed one, with
# nothing to tell the agent which it is in. State that survives sometimes is worse than state that
# never survives. A fresh namespace per call costs a couple of milliseconds against a call that
# costs hundreds, and it leaves no process to supervise, reap, or reconstruct.
#
# The credential is never in here, and that is structural rather than careful: the agent loop that
# needs it stays in the parent, and only the command crosses. `--clearenv` is what keeps it that way
# for everything else the parent happens to be holding.

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Final
from typing import assert_never

from mainplate.roots import RootName
from mainplate.roots import environment_named
from mainplate.snapshots import Worktree

BWRAP: Final = "bwrap"

# Read-only, and bound with `-try` because which of these exist is a property of the distribution
# rather than of anything this console knows. A missing one is not an error; a missing `/usr` will
# announce itself the moment a command fails to start.
SYSTEM: Final = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt")

# Spelled out rather than inherited, since `--clearenv` takes the parent's away. `HOME` is the
# tmpfs, so a tool that writes a dotfile succeeds and the file goes away with the call.
WHERE_COMMANDS_ARE: Final = "/usr/local/bin:/usr/bin:/bin"
SOMEWHERE_TO_WRITE: Final = "/tmp"


class NoSandbox(RuntimeError):
    """No `bwrap` on this machine, so there is nowhere safe to run a command."""


class Venue(Enum):
    """
    Whether a command can reach the network, which is the sandbox's other axis.

    Separate from `Filesystem` on purpose: what a command may *read* and whether it may *dial out* are
    independent questions, and keeping them apart is what lets a session have the whole machine and
    no network, or a worktree and a network, without one implying the other.
    """

    CONFINED = "confined"
    CONNECTED = "connected"


class Filesystem(Enum):
    """
    How much of the filesystem a session's tools can touch.

    Recorded on the session's `choice` and fixed for its life, like everything else there. It is not
    free of the repository: a session that picked one is `WORKTREE` and cannot be anything else,
    because the worktree is the point of having picked it. The other two are what a session with no
    repository chooses between, and `Service.start` is where that is made true rather than trusted.

    `EVERYTHING` still runs inside a sandbox, with `/` bound instead of a worktree. That buys nothing
    about the filesystem and everything about the rest: the network switch is `--unshare-net`, the
    credential is kept out by `--clearenv`, and teardown is `--unshare-pid`, so dropping the sandbox
    for this arm would silently take all three with it and leave one axis unrepresentable.
    """

    WORKTREE = "worktree"
    NOTHING = "nothing"
    EVERYTHING = "everything"


@dataclass(frozen=True, slots=True)
class Isolation:
    """
    How confined a session is, as the axes that answer it.

    One value rather than fields spread across `Choice`, because they are answered together, recorded
    together, fixed for the session's life together, and read together by the one thing that builds
    its tools. A further axis - what a command may *spend*, in a cgroup - belongs here beside them
    when there is one, and lands as a member rather than as a third parameter threaded through four
    signatures.

    The axes stay independent of each other. The whole machine with no network and a worktree with
    one are both ordinary things to want, and both stay expressible because `EVERYTHING` is still a
    sandbox: the network switch is a flag on the same namespace rather than the absence of one.
    """

    filesystem: Filesystem = Filesystem.NOTHING
    network: bool = False

    @property
    def venue(self) -> Venue:
        """The network answer as the sandbox's own vocabulary."""
        return Venue.CONNECTED if self.network else Venue.CONFINED

    def settled(self, repository: str | None) -> Isolation:
        """
        A copy of this made to agree with whether a repository was picked.

        A session working in one reaches its worktree and can reach nothing else, because the
        worktree is the point of having picked it; a session working in none cannot reach a worktree
        there is none of. Applied where a session is created rather than where one is read, so a
        contradictory pair is never recorded and no reader has to reconcile the two.
        """
        if repository is not None:
            return replace(self, filesystem=Filesystem.WORKTREE)
        if self.filesystem is Filesystem.WORKTREE:
            return replace(self, filesystem=Filesystem.NOTHING)
        return self


@dataclass(frozen=True, slots=True)
class Bind:
    """One path a sandbox makes visible, and whether a command may write through it."""

    path: Path
    writable: bool

    name: RootName | None = None
    """
    What a command calls this place, where it is one a model has any business naming.

    A bind rather than a field on the sandbox, so the two shapes still differ only in what is in
    `places`. Absent on the clone, which is bound so that git works and is not somewhere anybody
    should be writing paths into, and absent on `/`, where an environment variable saying `/` would
    be a name for the thing every path already starts with.

    Every path here is bound at *its own* path, so this is not a shorter route to the directory: it
    is the same absolute path under a name, which is what stops a model reproducing 32 hex characters
    from memory and getting them wrong.
    """


@dataclass(frozen=True, slots=True)
class InAWorktree:
    """A session's commands inside its own worktree, its clone, and its scratch directory."""

    worktree: Worktree
    scratch: Path

    scratch_named: RootName = "scratch"
    """
    What the scratch is called inside, which is the one thing a plugin's namespace does differently.

    A plugin gets this same shape around a directory of its **own** rather than the session's, so
    calling it `scratch` in there would give one word two meanings: the place a model writes, and the
    place this console keeps a plugin's installation out of the model's reach. See
    `Spawned.scratch_for`.
    """


@dataclass(frozen=True, slots=True)
class OverEverything:
    """A session's commands over the whole machine, still inside a namespace."""


type Confinement = InAWorktree | OverEverything
"""
Which shape of sandbox a session's commands get, decided when its agent is built.

A value rather than a built `Sandbox`, because the worktree does not exist yet at that moment: a
session's first pass plants it, and the agent that will use it is constructed before that happens.
`Filesystem.NOTHING` has no arm here on purpose - a session reaching nothing is built with no tools
at all, so there is no confinement to describe rather than an empty one to carry.
"""


async def confined_by(confinement: Confinement) -> Sandbox:
    """The sandbox one confinement means, asked of git where that is what decides the paths."""
    match confinement:
        case InAWorktree(worktree=worktree, scratch=scratch, scratch_named=named):
            return await Sandbox.around(worktree, scratch, named)
        case OverEverything():
            return Sandbox.everywhere()
        case _ as unreachable:
            assert_never(unreachable)


def starting_at(confinement: Confinement) -> Path:
    """Where a command starts, which is the worktree for one and the root of everything for the other."""
    match confinement:
        case InAWorktree(worktree=worktree):
            return worktree.root
        case OverEverything():
            return Path("/")
        case _ as unreachable:
            assert_never(unreachable)


def home_in(confinement: Confinement) -> Path | None:
    """
    What `$HOME` is for a session's own commands, which is its scratch where it has one.

    The scratch rather than the tmpfs, because that is where a toolchain the session's `setup` script
    installed keeps what it fetched, and a `$HOME` anywhere else is a shell that cannot find its own
    tools. What a shell leaves in a home directory is therefore scratch by intent rather than by
    accident, which is the cost of a session that can be set up at all. Nothing for a session over
    the whole machine, which has no scratch and gets the tmpfs.
    """
    match confinement:
        case InAWorktree(scratch=scratch):
            return scratch
        case OverEverything():
            return None
        case _ as unreachable:
            assert_never(unreachable)


def sandbox_command() -> str:
    """
    Where `bwrap` is, or a refusal naming it.

    Resolved once at startup rather than per call, so a machine without it is a console that will
    not start rather than a session that fails on whichever turn first reached for a command.
    """
    found = shutil.which(BWRAP)
    if found is None:
        raise NoSandbox(
            f"{BWRAP!r} is not on PATH, and it is what confines a command to a session's worktree. "
            "Install bubblewrap, or run a console whose sessions have no repository."
        )
    return found


@dataclass(frozen=True, slots=True)
class Sandbox:
    """
    What one command can see, as the paths bound into its namespace.

    A list rather than named fields, so the two shapes a session can have - a worktree with its clone
    and scratch, or the whole machine - differ only in what is in it and share every other argument.
    That is what keeps the network answer, the cleared environment and the pid namespace from being
    things the filesystem answer can change by accident.

    Every path is absolute and every one is bound at *its own* path inside the namespace rather than
    at a tidy `/worktree`. That is forced rather than chosen: a linked worktree's `.git` is a file
    holding an *absolute* pointer into the clone, so a remapped worktree is one whose git is broken
    unless every consumer also carries a `GIT_COMMON_DIR` that any subprocess is free to unset.
    """

    places: tuple[Bind, ...]

    @classmethod
    async def around(cls, worktree: Worktree, scratch: Path, scratch_named: RootName = "scratch") -> Sandbox:
        """
        A worktree, its clone read-only, and a scratch directory: what a `WORKTREE` session reaches.

        The clone and not the per-worktree directory: the latter sits *inside* the former and its
        `commondir` points back out at it for objects and refs, so binding the common one covers both
        and binding the other covers neither. Taken from `Worktree.common` where the tree's directory
        was named, which is every session's, and asked of git only for a tree that named none - so
        the ordinary path runs no subprocess and reads nothing out of the tree to decide what to bind.

        **The pointer goes back over the worktree read-only, and the order is what makes that work.**
        `.git` in a linked worktree is a one-line file naming the git directory, and it sits in the
        one place a session may write, so without this a command replaces it with a repository of its
        own and every later git in that directory reads *that* repository's configuration - which
        names programs git runs. Bound over itself after the tree, the file cannot be written,
        removed, moved, or unmounted from in here, and reading it and everything around it still
        works. See [what runs, and as whom](../../docs/design/security.md).

        All three are **absolute as a precondition**, which is the same one `Clones` and `Worktrees`
        take: `Settings.workspace_root` resolves once where a configured path enters the process, so
        everything derived from it is already absolute and nothing here re-establishes it. A relative
        path would be resolved against whatever directory bwrap happened to start in, which is not a
        thing to guess at per call.
        """
        common = worktree.common or Path(
            await worktree.demand("rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        return cls(
            places=(
                Bind(path=worktree.root, writable=True, name="worktree"),
                Bind(path=worktree.pointer, writable=False),
                Bind(path=common, writable=False),
                Bind(path=scratch, writable=True, name=scratch_named),
            )
        )

    @classmethod
    def everywhere(cls) -> Sandbox:
        """
        The whole machine, for a session that chose `EVERYTHING`.

        Still a sandbox, and that is the point: the filesystem is wide open here, so what this is
        still buying is the network namespace, the cleared environment, and a pid namespace that
        reaps whatever a command leaves behind. A session on this arm can read the console's own
        configuration and its store, which is what choosing it means.
        """
        return cls(places=(Bind(path=Path("/"), writable=True),))

    def argv(
        self,
        at: str,
        venue: Venue = Venue.CONFINED,
        home: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        """
        The `bwrap` prefix a command runs behind, as the arguments before the command itself.

        `home` is what `$HOME` is inside, and the default is the tmpfs, which is what a session over
        the whole machine gets. A session in a worktree passes its scratch, and a confined plugin
        passes whichever directory is its to keep things in: every tool that fetches keeps what it
        fetched under `$HOME`, so on a tmpfs a `setup` that resolved an interpreter and a package
        tree would find neither at the next call, with the network shut and no way to fetch them
        again. See `home_in` and `Spawned`.

        `environment` is what a session's plugins asked to have set here, and it goes **last**, so a
        plugin's `PATH` is the `PATH` rather than a fragment of one. It is what makes a toolchain a
        `setup` script installed reachable without this console knowing what a shim is. Nothing hands
        it to a plugin's own namespace, and that is the constraint rather than an omission: a
        repository's script setting `PATH` for every plugin would redirect what the repository's
        other plugins execute at every turn boundary.

        A `WORKTREE` sandbox binds the worktree read-write, its clone **read-only**, and the scratch
        read-write. The read-only clone is the load-bearing part: it leaves every read working -
        `ls-files`, `status`, `diff`, `log`, `blame` - while `add`, `commit`, and `stash` fail loudly
        on a read-only `index.lock`. What that buys is not tidiness: a git write from in here would
        be a second history that no panel shows, no fork inherits and no rewind restores, which is
        the second copy of state this whole console is built to refuse. Snapshots keep working
        because they run in the parent, where the clone is writable, so the agent physically cannot
        rewrite the history `refs/mainplate/snapshots` is chained onto.

        A `EVERYTHING` sandbox binds `/` read-write instead, which subsumes all of that and is the point
        of choosing it. Everything below the binds is identical either way, which is why there is one
        of these rather than two: the network, the cleared environment and the pid namespace are not
        things the filesystem answer should be able to change by accident.
        """
        binds: list[str] = []
        for each in SYSTEM:
            binds.extend(("--ro-bind-try", each, each))
        network: tuple[str, ...]
        match venue:
            case Venue.CONFINED:
                network = ("--unshare-net",)
            case Venue.CONNECTED:
                network = ()
            case _ as unreachable:
                assert_never(unreachable)
        places: list[str] = []
        # What each place is called, beside the bind that makes it reachable, so the two cannot come
        # apart: a name here is a name for a path this sandbox actually has. `--clearenv` below takes
        # the parent's environment away and these are added after it, so what a command finds is only
        # ever what this console put there.
        named: list[str] = []
        for bind in self.places:
            places.extend(("--bind" if bind.writable else "--ro-bind", str(bind.path), str(bind.path)))
            if bind.name is not None:
                named.extend(("--setenv", environment_named(bind.name), str(bind.path)))
        asked: list[str] = []
        for name, value in (environment or {}).items():
            asked.extend(("--setenv", name, value))
        return (
            *binds,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            SOMEWHERE_TO_WRITE,
            # **After** the tmpfs, and that ordering is the whole of why it works. bwrap applies
            # these in the order given, so a worktree root that happens to live under `/tmp` is
            # covered by the tmpfs and vanishes if these come first, leaving a sandbox with no
            # worktree in it and a command that cannot even change directory into one.
            *places,
            *network,
            # `--unshare-pid` is teardown as much as isolation: killing the namespace's init reaps
            # whatever the command left running, so a cancelled turn leaves no orphan build behind.
            "--unshare-pid",
            "--die-with-parent",
            # No controlling terminal, so nothing in here can push characters back up one.
            "--new-session",
            "--clearenv",
            "--setenv",
            "PATH",
            WHERE_COMMANDS_ARE,
            "--setenv",
            "HOME",
            home if home is not None else SOMEWHERE_TO_WRITE,
            "--setenv",
            "TERM",
            "dumb",
            *named,
            *asked,
            "--chdir",
            at,
        )
