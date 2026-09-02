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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final
from typing import assert_never

from mainplate.snapshots import Workspace

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
    How much of the world a command is run against.

    Two arms, and the axis is the network alone: every venue here binds the same filesystem, because
    what a command may *read* does not vary with why it is being run. `CONFINED` is the default and
    the only one an agent's own tool calls use.
    """

    CONFINED = "confined"
    CONNECTED = "connected"


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
    One session's worktree, as the two paths a command has to be able to see to work in it.

    Both are absolute and both are bound at *their own* paths inside the namespace rather than at a
    tidy `/workspace`. That is forced rather than chosen: a linked worktree's `.git` is a file
    holding an *absolute* pointer into the clone, so a remapped worktree is one whose git is broken
    unless every consumer also carries a `GIT_COMMON_DIR` that any subprocess is free to unset.
    """

    worktree: Path
    clone: Path
    scratch: Path

    @classmethod
    async def around(cls, workspace: Workspace, scratch: Path) -> Sandbox:
        """
        The paths, with the git ones asked of git rather than assembled from a believed layout.

        `--git-common-dir` and not `--absolute-git-dir`: the per-worktree directory sits *inside* the
        bare clone and its `commondir` points back out at it for objects and refs, so binding the
        common one covers both and binding the other covers neither.

        All three are **absolute as a precondition**, which is the same one `Clones` and `Worktrees`
        take: `Settings.workspace_root` resolves once where a configured path enters the process, so
        everything derived from it is already absolute and nothing here re-establishes it. A relative
        path would be resolved against whatever directory bwrap happened to start in, which is not a
        thing to guess at per call.
        """
        common = await workspace.demand("rev-parse", "--path-format=absolute", "--git-common-dir")
        return cls(worktree=workspace.root, clone=Path(common), scratch=scratch)

    def argv(self, at: str, venue: Venue = Venue.CONFINED) -> tuple[str, ...]:
        """
        The `bwrap` prefix a command runs behind, as the arguments before the command itself.

        The clone goes in **read-only**, and that is the load-bearing half. It leaves every read
        working - `ls-files`, `status`, `diff`, `log`, `blame` - while `add`, `commit`, and `stash`
        fail loudly on a read-only `index.lock`. What that buys is not tidiness: a git write from in
        here would be a second history that no panel shows, no fork inherits and no rewind restores,
        which is the second copy of state this whole console is built to refuse. Snapshots keep
        working because they run in the parent, where the clone is writable, so the agent physically
        cannot rewrite the history `refs/mainplate/snapshots` is chained onto.
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
        return (
            *binds,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            SOMEWHERE_TO_WRITE,
            # **After** the tmpfs, and that ordering is the whole of why it works. bwrap applies
            # these in the order given, so a workspace root that happens to live under `/tmp` is
            # covered by the tmpfs and vanishes if these come first, leaving a sandbox with no
            # worktree in it and a command that cannot even change directory into one.
            "--bind",
            str(self.worktree),
            str(self.worktree),
            "--ro-bind",
            str(self.clone),
            str(self.clone),
            # Read-write and outside everything git answers, so what a session keeps here is neither
            # in a `list` nor in a `status` nor in any snapshot. Nothing captures it on purpose:
            # rewinding a conversation should not uninstall what was installed since, which is the
            # same reason snapshots honour a `.gitignore`.
            "--bind",
            str(self.scratch),
            str(self.scratch),
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
            SOMEWHERE_TO_WRITE,
            "--setenv",
            "TERM",
            "dumb",
            "--chdir",
            at,
        )
