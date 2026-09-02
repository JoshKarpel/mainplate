from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from mainplate.sandbox import Sandbox
from mainplate.sandbox import Venue
from mainplate.snapshots import Workspace

SHELL: Final = "/bin/sh"

# What a command may spend, and what it may say back. Both are bounds on somebody else's code
# rather than on ours, so both are enforced here rather than hoped for.
DEFAULT_SECONDS: Final = 120
MAX_SECONDS: Final = 600

# Head and tail, because the two ends are where the answer is: a build names what it is doing at the
# top and what went wrong at the bottom, and the thousand lines between them are the part that would
# cost a context window without saying anything.
HEAD_LINES: Final = 120
TAIL_LINES: Final = 80
MAX_LINE: Final = 2_000

RETRIES: Final = 3


class Refused(ValueError):
    """Something a model asked for that this tool will not do, phrased so it can ask again."""


def clipped(line: str) -> str:
    return line if len(line) <= MAX_LINE else f"{line[:MAX_LINE]}… ({len(line) - MAX_LINE} more characters)"


def shortened(output: str) -> str:
    """
    The output as the model sees it, with the middle taken out of anything long.

    A count of what was dropped rather than a bare ellipsis, because "how much did I not see" is the
    thing that decides whether to run it again more narrowly.
    """
    lines = [clipped(each) for each in output.split("\n")]
    if len(lines) <= HEAD_LINES + TAIL_LINES:
        return "\n".join(lines)
    hidden = len(lines) - HEAD_LINES - TAIL_LINES
    middle = f"… {hidden} more lines, not shown. Narrow the command or filter it to see them."
    return "\n".join([*lines[:HEAD_LINES], middle, *lines[-TAIL_LINES:]])


def reported(command: str, code: int | None, output: str) -> str:
    """
    What one command hands back, which is its output and how it ended.

    The status is stated even when it is zero. A model that has to infer success from the *absence*
    of an error line reads an empty result as a failure and runs the thing again.
    """
    ended = "exit 0" if code == 0 else f"exit {code}"
    body = shortened(output).rstrip()
    return "\n".join((f"$ {command}", "", body, "", ended)) if body else "\n".join((f"$ {command}", "", ended))


async def ran(workspace: Workspace, scratch: Path, bwrap: str, command: str, seconds: int) -> str:
    """
    One command, inside a namespace of its own, for at most `seconds`.

    The sandbox is resolved here rather than held, because the worktree does not exist yet when the
    agent is built: a session's first pass plants it, and the agent that will use it is constructed
    before that happens. Asking git where the clone is costs one short subprocess against a call
    that is already spawning one, and it is right about a worktree that moved.

    `stderr` is folded into `stdout` rather than reported beside it, so what comes back is the
    interleaving the command actually produced. Split into two blocks, a warning and the line it is
    about end up in different halves of the answer.
    """
    if not command.strip():
        raise Refused("a command to run is required")
    # Made here rather than when the session was planted, because bwrap will not bind a source that
    # does not exist and this is the one place that knows a command is about to run. Idempotent, so
    # every later call reaches it and does nothing.
    await asyncio.to_thread(lambda: scratch.mkdir(parents=True, exist_ok=True))
    sandbox = await Sandbox.around(workspace, scratch)
    process = await asyncio.create_subprocess_exec(
        bwrap,
        *sandbox.argv(at=str(sandbox.worktree), venue=Venue.CONFINED),
        SHELL,
        "-c",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(min(max(1, seconds), MAX_SECONDS)):
            out, _ = await process.communicate()
    except TimeoutError:
        # Killing `bwrap` is enough because of `--unshare-pid`: the namespace's init goes with it and
        # takes every process the command started, so nothing survives the timeout it ran past.
        process.kill()
        # `communicate` again rather than `wait`, and that is not belt-and-braces. The timeout
        # cancelled the first one part-way through, which leaves the pipes it was reading still
        # open: `wait` reaps the process and closes none of them, so the transport is collected
        # later with its file descriptors still held and complains from `__del__` at whatever the
        # process happens to be doing by then. Draining is what actually closes them.
        await process.communicate()
        raise Refused(
            f"{command!r} ran longer than {seconds}s and was stopped. Nothing of what it printed "
            f"was kept. Run something smaller, or raise `seconds` up to {MAX_SECONDS}."
        ) from None
    return reported(command, process.returncode, out.decode(errors="replace"))


def bash_tools(workspace: Workspace, scratch: Path, bwrap: str) -> FunctionToolset[None]:
    """
    One tool, bound to one session's worktree and to the sandbox binary that confines it.

    Built per session for the same reason the file tools are: the worktree is what makes a command
    safe to run, and every session has its own.
    """
    toolset = FunctionToolset[None]()

    async def bash(command: str, seconds: int = DEFAULT_SECONDS) -> str:
        """
        Run a shell command in the session's workspace.

        Reach for this to build, test, search, or inspect. Prefer `read`, `edit` and `create` for
        working on a file, and `list` for seeing what is there: those are anchored and bounded, where
        this is neither.

        The command runs in a namespace holding the workspace and a read-only system, and nothing
        else. Four things follow from that, and all four are worth knowing before writing a command:

        - **There is no network.** No fetching, no installing, no cloning. A command that needs one
          will fail rather than hang.
        - **Nothing outside the workspace exists.** No home directory, no other session's files, no
          console configuration. A path leading out points at nothing.
        - **Git can be read but not written.** `status`, `diff`, `log`, `show`, `blame` and
          `ls-files` all work. `add`, `commit`, `stash`, `checkout` and anything else that writes
          fails on a read-only filesystem. That is deliberate: this conversation's own history is
          how work is recorded here, and committing is the person's to do.
        - **No shell state persists between calls.** Each command gets a new namespace, so a `cd`,
          an exported variable, a background process, and anything written to `/tmp` are gone by the
          next call. Chain what belongs together into one command with `&&`.

        Files do persist in two places, and the difference between them is what gets recorded. The
        workspace is the repository, and every change there is snapshotted with the conversation.
        The scratch directory, named in your instructions, is not snapshotted by anything: put a
        build cache, a downloaded artifact, or a note to yourself there, and it is still there next
        call and next turn without ever appearing in the repository.

        Output is capped: a long result comes back as its first and last lines with a count of what
        was dropped in between. Filter the command rather than asking twice.

        Args:
            command: The shell command, run with `sh -c` from the workspace root.
            seconds: How long to allow before it is stopped and its output discarded.

        """
        try:
            return await ran(workspace, scratch, bwrap, command, seconds)
        except Refused as refusal:
            raise ModelRetry(str(refusal)) from None

    toolset.add_function(bash, retries=RETRIES)
    return toolset
