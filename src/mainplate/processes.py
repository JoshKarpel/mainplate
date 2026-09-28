from __future__ import annotations

import asyncio


def reaped(process: asyncio.subprocess.Process) -> None:
    """
    End a process that is still running and close the pipes it was talking through.

    For a `communicate` that will never resume, which is every one whose task was cancelled: a pass
    is cancelled when the worker is, and a background loop when the console stops. Killing the
    process is not enough, because what is left open is the pipes it was reading, and a child it
    started (the `ssh` under a `git fetch`) survives the kill holding their other ends. An unclosed
    transport is collected at an arbitrary later moment, as a `ResourceWarning` raised into whatever
    happens to be running then.

    **The pipes closed rather than a drain**, because the caller is a task that has just been
    cancelled: an `await` there is cancelled again before it does anything, so the only thing that
    can close a pipe is a synchronous call.

    **The pipes and not the transport they belong to.** Closing the subprocess transport asks the
    child whether it has finished, which is a `waitpid`: a child the kill has already ended is reaped
    right there, ahead of asyncio's own watcher, whose `waitpid` then finds nothing and reports the
    process as exiting 255 rather than as killed. How often that happens is how fast the kill lands,
    so it was a failure that came and went with the load on the machine. Left alone, the transport
    closes itself once the watcher has seen the exit and the pipes are gone, with the status already
    known, so nothing polls ahead of the watcher.

    `_transport` is private and is reached for deliberately, guarded so that an asyncio which renames
    it degrades to a warning at collection rather than to an `AttributeError` on every shutdown. It
    is the same bargain `Service.token` takes in reading the store's own table: named here, so a
    rename is a change to make in one place.
    """
    if process.returncode is None:
        process.kill()
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    for fd in (0, 1, 2):
        pipe = transport.get_pipe_transport(fd)
        if pipe is not None:
            pipe.close()
