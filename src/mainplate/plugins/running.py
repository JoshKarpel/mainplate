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
from mainplate.plugins.protocol import SETUP
from mainplate.plugins.protocol import Event
from mainplate.plugins.protocol import Payload
from mainplate.roots import RootName
from mainplate.roots import environment_named
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

SETTING_UP: Final = timedelta(minutes=20)
"""
How long one plugin has to set itself up, which is a different question from answering an event.

Longer than `PATIENCE` because what a setup does is fetch and build: the plugin's own dependencies, a
hook environment per entry in a `.pre-commit-config.yaml`, a toolchain. That is minutes on a cold
cache and seconds on a warm one, and the number has to cover the cold case or the first session on a
repository is the one that fails.

Still bounded, and bounded by wall clock rather than by progress, because nothing here can see
progress: a plugin that has said nothing for twenty minutes is one to stop and report rather than one
to keep a worker slot for. It is paid once per session, since the answer is recorded.
"""

CONFIG_HOME: Final = "MAINPLATE_CONFIG_HOME"
"""
Where a plugin outside a worktree finds the operator's own files, said in the environment.

The bundled guidance plugin is what needs it: what a session is told includes the operator's own
guidance directory, which sits beside `config.yaml` and not in any repository. Passed rather than
assumed, so a console started with a config home of its own is one whose plugins read that one.

A repository's plugin never sees it, because a repository's plugin runs behind `--clearenv`.
"""

WORKTREE: Final = environment_named("worktree")
"""
Where this session's files are, said in the environment as well as in the payload.

Both, because the two readers are different: the payload is what a plugin parses, and this is what a
line of shell in a plugin can reach without parsing anything. It costs one variable and makes a
five-line plugin possible.

Derived rather than spelled, for the reason `roots.py` exists: a place this console names has one
word for it, and two spellings of that word is two places that answer to different names depending
on which of them was edited last.
"""

SCRATCH_NAMED: Final[RootName] = "plugin_scratch"
"""
What a confined plugin's own directory is called inside its namespace.

**Not `scratch`**, which is what a model's `bash` finds the *session's* under. They are two
directories with two owners, and one word for both, told apart by which process happened to read it,
is exactly what a shared vocabulary of place names exists to stop.

Named here because this is the module that decides a plugin's namespace gets one; what the variable
holding it is called in an environment is `sandbox.py`'s to derive, from this, so the name a plugin
reads and the name the sandbox sets cannot come to differ.
"""


class PluginFailed(RuntimeError):
    """
    A plugin did not answer, naming the plugin and what happened instead.

    One type for every way that can go: a script that is not there, one that exited non-zero, one
    that printed something that is not JSON, and one that ran past its patience. All four are the
    same thing to a reader - this plugin did not answer - and all four are reported as a sentence on
    the session's page naming which plugin.

    **A failure at `setup` ends the pass with no registration written**, so the settings step is
    drawn again with this sentence above the switches and pressing the button is a fresh attempt.
    That is forced rather than chosen: the store keeps the value a key was first given, so a
    registration written with one plugin missing could never be corrected.
    """


type Speaking = Callable[[Installed, Payload, Worktree | None], Awaitable[object]]
"""
How this console says one thing to one plugin, injected rather than reached for.

A function for the reason `Pricer`, `Draining` and `Guiding` are: what answers it spawns processes
and knows about sandboxes, and injecting the one question keeps everything above it - the pass, the
service, the routes - ignorant of how a plugin is run. It is also what lets a test drive the whole
mechanism with a mapping of answers and no subprocess at all.

**The worktree is passed beside the payload rather than read out of it**, and the two are not the
same thing: the payload's is a string a plugin parses, and this is the tree as this console knows
it - including the git directory it was told, which is what keeps a confined run from leaving git to
discover one from a pointer file the session can write. Reconstructing a `Worktree` from the wire
string is exactly the mistake `Worktree.gitdir` exists to stop. See
[what runs, and as whom](../../docs/design/security.md).
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
    fault, since the first pass is what plants one and no plugin has run by then.
    """
    if worktree is None:
        return None
    return worktree if await asyncio.to_thread(Path(worktree).is_dir) else None


@dataclass(frozen=True, slots=True)
class Invocation:
    """
    What one event actually costs to send: a process to run, an environment, a directory, a payload.

    The payload is here rather than passed straight through because one field of it is decided by
    whatever binds the directory it names. A plugin's scratch is a path this console makes, so the
    thing that mounts it is the thing that says where it is, and there is no second place computing
    the same path from the same parts.
    """

    argv: tuple[str, ...]
    environment: Mapping[str, str]
    where: str | None
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Spawned:
    """
    A plugin run as a process, with a repository's behind the same namespace `bash` uses.

    `bwrap` and `scratch` are what a confined run needs, and both are absent on a console that has
    no sandbox. Without them a repository's plugin is **not run at all**: the grant is a real
    boundary rather than a disclosure precisely because the process is confined, so a console that
    cannot confine one has nothing to offer in its place. Running it unconfined instead would be the
    second path this console refuses everywhere else.

    `scratch` is the root every plugin's own directory sits under rather than one directory, because
    one of these serves every session and a plugin's scratch belongs to one plugin in one session.

    **It is not the root the session's own scratch sits under**, and the two being separate is the
    point rather than tidiness: the session's is a place the model writes, so a plugin that installed
    a program into it would be running whatever the model last left at that path, unattended, at
    every turn boundary. Nothing here can check that, so `app.py` is where the two roots are named and
    where they must stay apart. See `scratch_for`.
    """

    bwrap: str | None = None
    scratch: Path | None = None
    patience: timedelta = PATIENCE
    setting_up: timedelta = SETTING_UP
    config_home: Path | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))

    def scratch_for(self, plugin: Installed, session: str) -> Path:
        """
        Where one plugin of one session may keep what it installed, and nobody else may write.

        Under the tier and the name, which together are the qualified name and are held to being one
        path segment apiece where a declaration is read. So two plugins that both call themselves
        `checks` get two directories, and neither is anywhere the model's own scratch reaches.

        Kept across the session's life and never snapshotted, which is the same bargain the session's
        scratch takes: going back to before a call should not uninstall what was installed between
        then and now.
        """
        if self.scratch is None:  # pragma: no cover - only a confined run asks, and that needs one
            raise PluginFailed(f"{plugin.qualified} has nowhere to write: this console has no scratch")
        return self.scratch / session / plugin.tier.value / plugin.name

    def allowed(self, event: Event) -> timedelta:
        """How long this event has to answer, which for setting up is longer than for anything else."""
        return self.setting_up if event == SETUP else self.patience

    def venue(self, event: Event) -> Venue:
        """
        Whether this event reaches the network, which one of them does and the rest never do.

        **Before the conversation, connected; during it, never.** A plugin that needs a program has to
        fetch one, and a repository's plugin is otherwise shut off the network whatever the session
        chose - so without this a setup is a plugin asked to install something with nothing to install
        from, and the whole stage is one that can only fail.

        What makes it safe to offer is *when* it is, rather than a check on what is fetched. `setup`
        runs before the first message: the worktree holds the commit the repository supplied and
        nothing else, no credential of this console's is inside the namespace, and nothing the model
        wrote exists yet. So what a connected run there can carry out is the repository's own code, to
        its own author. Every event after it is confined, which is the half that matters, because a
        turn boundary is where a plugin has read whatever the model has been writing.

        **Read off the event and never off the session.** `Filesystem.EVERYTHING` and a session with
        the network shut are both decisions about what the *model* may reach, and a plugin is not the
        model.
        """
        return Venue.CONNECTED if event == SETUP else Venue.CONFINED

    async def __call__(self, plugin: Installed, payload: Payload, worktree: Worktree | None) -> object:
        """
        Say one thing to one plugin and read its answer, or fail naming the plugin.

        The payload goes in on stdin as one JSON object and the answer comes back on stdout as one
        JSON value. `stderr` is kept apart rather than folded in, unlike a command a person runs:
        what a plugin prints there is its own diagnostics, and mixing it into the answer would make
        a `print` left in while debugging into a protocol error.
        """
        sending = await self.invocation(plugin, payload, worktree)
        allowed = self.allowed(payload.event)
        try:
            process = await asyncio.create_subprocess_exec(
                *sending.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(sending.environment),
                cwd=sending.where,
            )
        except OSError as unreachable:
            raise PluginFailed(f"{plugin.qualified} could not be run: {unreachable}") from unreachable
        try:
            async with asyncio.timeout(allowed.total_seconds()):
                out, err = await process.communicate(json.dumps(dict(sending.payload)).encode())
        except TimeoutError:
            process.kill()
            # Drained rather than waited on, for the reason `bash` drains one: the cancelled
            # `communicate` left the pipes it was reading open, and only reading them closes them.
            await process.communicate()
            raise PluginFailed(f"{plugin.qualified} did not answer within {allowed}") from None
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

    async def invocation(self, plugin: Installed, payload: Payload, worktree: Worktree | None) -> Invocation:
        """
        What to run, in what environment, where, and with what: the one place the tiers stop being
        alike.

        A plugin outside a worktree is the operator's own and runs as this process does, with the
        environment it has and two variables added saying where the operator's files are and where
        this session's are. It is handed no scratch, because it has the operator's own `$HOME` and a
        whole filesystem: what a scratch answers is having nowhere to write, which is a problem only
        the namespace creates, and such a plugin may reach the operator's other scripts and caches to
        do its job. Something to remember per session it already has, in the payload's `state`.

        A repository's runs behind `--clearenv` inside a namespace that reaches the worktree it was
        handed, its clone read-only, a directory of its own, and nothing else - so it is handed no
        environment here at all, because `bwrap` has already taken this process's away. The scratch
        is named on the payload as well as in the environment, so a plugin parsing JSON and a line of
        shell reach the same directory.

        The network is shut for every event but the one that happens before the conversation does,
        and never because of anything the session chose. See `venue`.
        """
        where = None if worktree is None else str(worktree.root)
        if not plugin.confined:
            environment = dict(self.environ)
            if self.config_home is not None:
                environment[CONFIG_HOME] = str(self.config_home)
            if where is not None:
                environment[WORKTREE] = where
            # Started *in* the worktree where there is one on disk, so a plugin reaches its
            # session's files the way a command does and needs no path parsed out of the payload.
            # A worktree named and not yet planted is an ordinary state - the first pass plants one -
            # so this falls back to wherever the console is rather than refusing to run at all.
            #
            # Off the loop, like the `mkdir` below: a stat is a syscall that blocks, and a pass is
            # answering other sessions while this one spawns.
            return Invocation(
                argv=(str(plugin.path),),
                environment=environment,
                where=await planted_at(where),
                payload=payload.spoken(),
            )
        if self.bwrap is None or self.scratch is None:
            raise PluginFailed(f"{plugin.qualified} is a repository's, and this console has no sandbox to run it in")
        if worktree is None:
            raise PluginFailed(f"{plugin.qualified} is a repository's, and this session has no worktree")
        scratch = self.scratch_for(plugin, payload.session)
        await asyncio.to_thread(lambda: scratch.mkdir(parents=True, exist_ok=True))
        # The worktree as this console knows it, git directory and all, rather than one rebuilt from
        # the path on the payload: an unnamed directory is one git discovers by reading the pointer
        # file at the tree's root, which is the single thing in there the session can replace.
        #
        # `plugin_scratch` and not `scratch`, which is the name a model's own `bash` finds the
        # *session's* directory under. One word for two places would have a repository's plugin and
        # the model it is running beside reading the same variable and reaching different disks.
        confinement = InAWorktree(worktree=worktree, scratch=scratch, scratch_named=SCRATCH_NAMED)
        sandbox = await confined_by(confinement)
        argv = (
            self.bwrap,
            *sandbox.argv(
                at=str(starting_at(confinement)),
                venue=self.venue(payload.event),
                # `$HOME` in the plugin's own scratch rather than on the tmpfs a command gets, which
                # is what makes a plugin with dependencies possible at all: everything that fetches
                # keeps what it fetched under `$HOME`, so a `uv run --script` shebang resolves an
                # interpreter and a package tree once, at the one event with a network, and finds
                # them there on every event after it.
                home=str(scratch),
            ),
            str(plugin.path),
        )
        return Invocation(
            argv=argv, environment={}, where=None, payload=payload.model_copy(update={"scratch": str(scratch)}).spoken()
        )
