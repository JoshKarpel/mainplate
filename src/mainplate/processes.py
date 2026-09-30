# Ending a process somebody stopped waiting on, which three unrelated things here all have to do.
#
# Git, a person's command and a plugin each start a process and read it through pipes, and each
# can be cancelled part-way: a pass when the worker is, a background loop and a command when the
# console stops. What is left then is the same in all three - a process nobody will read, and pipes
# nobody will close - so the answer is one function rather than three.
#
# A module of its own because it belongs to none of them. Beside git it would have the command
# runner and the plugin runner importing process handling from the module about snapshots, and
# beside either of those it would have snapshots importing from a module about something else.

from __future__ import annotations

import asyncio
from datetime import timedelta


def reaped(process: asyncio.subprocess.Process, grace: timedelta | None = None) -> None:
    """
    End a process that is still running and close the pipes it was talking through.

    For a `communicate` that will never resume, which is every one whose task was cancelled: a pass
    is cancelled when the worker is, and a background loop when the console stops. Killing the
    process is not enough, because what is left open is the pipes it was reading, and a child it
    started (the `ssh` under a `git fetch`) survives the kill holding their other ends. An unclosed
    transport is collected at an arbitrary later moment, as a `ResourceWarning` raised into whatever
    happens to be running then.

    **`grace` asks the process to stop before it is made to.** With one, the process is sent
    `SIGTERM` now and `SIGKILL` once the grace is up, if it is still running then; without one it is
    killed outright. It exists for git, which removes the lock files it holds when it is terminated
    and leaves them behind when it is killed, so a `fetch` killed while updating a ref leaves a
    `.lock` beside it and every later write to that ref fails with "File exists" until somebody
    deletes it by hand. The kill is scheduled on the loop rather than awaited, for the reason below;
    the cost is that a loop closed inside the grace never sends it, which leaves a process that was
    asked to stop and ignored the asking.

    **The pipes closed rather than a drain**, because the caller is a task that has just been
    cancelled: an `await` there is cancelled again before it does anything, so the only thing that
    can close a pipe is a synchronous call.

    **The pipes and not the transport they belong to.** Closing the subprocess transport asks the
    child whether it has finished, which is a `waitpid`: a child the kill has already ended is reaped
    right there, ahead of asyncio's own watcher, whose `waitpid` then finds nothing and reports the
    process as exiting 255 rather than as killed. How often that happens is how fast the kill lands,
    so it is a failure that comes and goes with the load on the machine. Left alone, the transport
    closes itself once the watcher has seen the exit and the pipes are gone, with the status already
    known, so nothing polls ahead of the watcher.

    `_transport` is private and is reached for deliberately, guarded so that an asyncio which renames
    it degrades to a warning at collection rather than to an `AttributeError` on every shutdown. It
    is the same bargain `Service.token` takes in reading the database's own table: named here, so a
    rename is a change to make in one place.
    """
    if process.returncode is None:
        if grace is None:
            process.kill()
        else:
            process.terminate()
            asyncio.get_running_loop().call_later(grace.total_seconds(), killed, process)
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    for fd in (0, 1, 2):
        pipe = transport.get_pipe_transport(fd)
        if pipe is not None:
            pipe.close()


def killed(process: asyncio.subprocess.Process) -> None:
    """A process asked to stop, killed if it has not, which is where a grace in `reaped` runs out."""
    if process.returncode is None:
        process.kill()
