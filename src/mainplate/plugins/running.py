# How a plugin is actually spoken to: one process, one JSON object in, one JSON object out.
#
# **A plugin is a single executable.** That is the whole contract, and four properties follow from
# it: any language, because this cares about two file descriptors; its own dependencies, because it
# is launched rather than imported; testable by hand with an `echo` and a pipe; and it reaches
# nothing it was not handed, which is what makes a repository's plugin possible at all.
#
# **The protocol is transport-independent, and that is deliberate.** Today it is a process per
# event, which is plainly right for a turn boundary and a control press and is a real cost on a tool
# call. If measurement says so, the fix is a persistent process spoken to over framed stdio, which
# changes this module and not one word of `protocol.py`. So the decision is deferred as a value
# rather than as a second code path.

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from pathlib import Path
from typing import Final

from mainplate.plugins.installed import Installed
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.sandbox import starting_at
from mainplate.snapshots import Worktree

logger = logging.getLogger(__name__)

PATIENCE: Final = timedelta(minutes=5)
"""
How long one plugin has to answer one event before it is stopped.

Bounded because nothing else bounds it: a plugin that never returns would hold a pass for the whole
lease and take the session's worker slot with it. Five minutes rather than the ten a person's own
command gets, because this runs *inside* a pass and the lease covers the pass: a plugin that needs
longer than a model round trip and a batch of tool calls is doing something the console should be
told about rather than waiting for.
"""

CONFIG_HOME: Final = "MAINPLATE_CONFIG_HOME"
"""
Where a plugin outside a worktree finds the operator's own files, said in the environment.

The bundled guidance plugin is what needs it: what a session is told includes the operator's own
guidance directory, which sits beside `config.yaml` and not in any repository. Passed rather than
assumed, so a console started with a config home of its own is one whose plugins read that one.

A repository's plugin never sees it, because a repository's plugin runs behind `--clearenv`.
"""

WORKTREE: Final = "MAINPLATE_WORKTREE"
"""
Where this session's files are, said in the environment as well as in the payload.

Both, because the two readers are different: the payload is what a plugin parses, and this is what a
line of shell in a plugin can reach without parsing anything. It costs one variable and makes a
five-line plugin possible.
"""


class PluginFailed(RuntimeError):
    """
    A plugin did not answer, naming the plugin and what happened instead.

    One type for every way that can go: a script that is not there, one that exited non-zero, one
    that printed something that is not JSON, and one that ran past its patience. All four are the
    same thing to a reader - this plugin did not answer - and all four are reported as a sentence on
    the session's page naming which plugin.

    **A failure ends the pass with nothing written**, so retrying is another pass and `describe` runs
    again from scratch with nothing recorded to conflict with. That is forced rather than chosen: the
    store keeps the value a key was first given, so a turn-0 record written with one plugin missing
    could never be corrected.
    """


type Speaking = Callable[[Installed, Mapping[str, object]], Awaitable[object]]
"""
How this console says one thing to one plugin, injected rather than reached for.

A function for the reason `Pricer`, `Draining` and `Guiding` are: what answers it spawns processes
and knows about sandboxes, and injecting the one question keeps everything above it - the pass, the
service, the routes - ignorant of how a plugin is run. It is also what lets a test drive the whole
mechanism with a mapping of answers and no subprocess at all.
"""


def reaped(process: asyncio.subprocess.Process) -> None:
    """
    End a plugin that is still running and close the pipes it was talking through.

    **The transport rather than a drain**, because the one caller is a task that has just been
    cancelled: an `await` there is cancelled again before it does anything, so the only thing that
    can close a pipe is a synchronous call.

    `_transport` is private and is reached for deliberately, guarded so that an asyncio which renames
    it degrades to what this console did before - a warning at collection - rather than to an
    `AttributeError` on every shutdown. It is the same bargain `Service.token` takes in reading the
    store's own table: named here, so a rename is a change to make in one place.
    """
    if process.returncode is None:
        process.kill()
    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()


async def planted_at(worktree: str | None) -> str | None:
    """
    Where a plugin starts, which is its session's worktree once there is one on disk.

    `None` for a session with no repository and for one whose worktree is not planted yet, which are
    two states with one answer: start where the console is. The second is ordinary rather than a
    fault, since the first pass is what plants one and `describe` runs on that pass.
    """
    if worktree is None:
        return None
    return worktree if await asyncio.to_thread(Path(worktree).is_dir) else None


@dataclass(frozen=True, slots=True)
class Spawned:
    """
    A plugin run as a process, with a repository's behind the same namespace `bash` uses.

    `bwrap` and `scratch` are what a confined run needs, and both are absent on a console that has
    no sandbox. Without them a repository's plugin is **not run at all**: the grant is a real
    boundary rather than a disclosure precisely because the process is confined, so a console that
    cannot confine one has nothing to offer in its place. Running it unconfined instead would be the
    second path this console refuses everywhere else.

    `scratch` is the root the per-session directories sit under rather than one directory, because
    one of these serves every session and a scratch belongs to one.
    """

    bwrap: str | None = None
    scratch: Path | None = None
    patience: timedelta = PATIENCE
    config_home: Path | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))

    def can_confine(self) -> bool:
        """Whether this console can run a repository's plugin at all, which needs a sandbox."""
        return self.bwrap is not None and self.scratch is not None

    async def __call__(self, plugin: Installed, payload: Mapping[str, object]) -> object:
        """
        Say one thing to one plugin and read its answer, or fail naming the plugin.

        The payload goes in on stdin as one JSON object and the answer comes back on stdout as one
        JSON value. `stderr` is kept apart rather than folded in, unlike a command a person runs:
        what a plugin prints there is its own diagnostics, and mixing it into the answer would make
        a `print` left in while debugging into a protocol error.
        """
        argv, environment, where = await self.invocation(plugin, payload)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(environment),
                cwd=where,
            )
        except OSError as unreachable:
            raise PluginFailed(f"{plugin.qualified} could not be run: {unreachable}") from unreachable
        try:
            async with asyncio.timeout(self.patience.total_seconds()):
                out, err = await process.communicate(json.dumps(dict(payload)).encode())
        except TimeoutError:
            process.kill()
            # Drained rather than waited on, for the reason `bash` drains one: the cancelled
            # `communicate` left the pipes it was reading open, and only reading them closes them.
            await process.communicate()
            raise PluginFailed(f"{plugin.qualified} did not answer within {self.patience}") from None
        except BaseException:
            # **Every other way out, cancellation included, and that is the one that matters.** A
            # pass is cancelled when the worker is, which is every shutdown, and a plugin part-way
            # through answering then leaves a `communicate` that will never resume: killing the
            # process is not enough, because what is left open is the pipes it was reading. Draining
            # them is what the timeout above does and is exactly what cannot be done here, since an
            # `await` inside a cancelled task is cancelled again the moment it is reached.
            #
            # So the transport is closed instead, which is what asyncio itself does when it collects
            # one - only here it happens while somebody is still holding the reference, rather than
            # at an arbitrary later moment as a `ResourceWarning` raised into whatever is running.
            reaped(process)
            raise
        said = err.decode(errors="replace").strip()
        if process.returncode:
            raise PluginFailed(f"{plugin.qualified} exited {process.returncode}: {said or 'saying nothing'}")
        if said:
            logger.info(f"{plugin.qualified} said: {said}")
        try:
            return json.loads(out.decode(errors="replace") or "{}")
        except json.JSONDecodeError as broken:
            raise PluginFailed(f"{plugin.qualified} printed something that is not JSON: {broken}") from broken

    async def invocation(
        self, plugin: Installed, payload: Mapping[str, object]
    ) -> tuple[tuple[str, ...], Mapping[str, str], str | None]:
        """
        What to run, in what environment, and where: the one place the tiers stop being alike.

        A plugin outside a worktree is the operator's own and runs as this process does, with the
        environment it has and two variables added saying where the operator's files are and where
        this session's are. A repository's runs behind `--clearenv` inside a namespace that reaches
        the worktree it was handed, its clone read-only, and nothing else - so it is handed no
        environment here at all, because `bwrap` has already taken this process's away.

        The network is always shut for a repository's plugin, whatever the session chose. The
        network switch a session picked is a decision about what the *model* may reach, and
        inheriting it would mean the one control that widens a session quietly widening somebody
        else's code along with it.
        """
        held = payload.get("worktree")
        worktree = held if isinstance(held, str) else None
        session = payload.get("session")
        if not plugin.confined:
            environment = dict(self.environ)
            if self.config_home is not None:
                environment[CONFIG_HOME] = str(self.config_home)
            if worktree is not None:
                environment[WORKTREE] = worktree
            # Started *in* the worktree where there is one on disk, so a plugin reaches its
            # session's files the way a command does and needs no path parsed out of the payload.
            # A worktree named and not yet planted is an ordinary state - the first pass plants one -
            # so this falls back to wherever the console is rather than refusing to run at all.
            #
            # Off the loop, like the `mkdir` below: a stat is a syscall that blocks, and a pass is
            # answering other sessions while this one spawns.
            return (str(plugin.path),), environment, await planted_at(worktree)
        if self.bwrap is None or self.scratch is None:
            raise PluginFailed(f"{plugin.qualified} is a repository's, and this console has no sandbox to run it in")
        if worktree is None or not isinstance(session, str):
            raise PluginFailed(f"{plugin.qualified} is a repository's, and this session has no worktree")
        planted = Path(worktree)
        scratch = self.scratch / session
        await asyncio.to_thread(lambda: scratch.mkdir(parents=True, exist_ok=True))
        confinement = InAWorktree(worktree=Worktree(root=planted), scratch=scratch)
        sandbox = await confined_by(confinement)
        argv = (
            self.bwrap,
            *sandbox.argv(at=str(starting_at(confinement)), venue=Venue.CONFINED),
            str(plugin.path),
        )
        return argv, {}, None
