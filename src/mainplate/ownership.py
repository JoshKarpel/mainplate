"""
Serialize checkout work across console processes without holding a database transaction.

The lock lives outside every sandbox-writable directory. A model pass holds it until it returns;
commands also check the durable unfinished-turn record under it, so a pass yielding between requests
cannot let a command write into the turn. The cost is polling while ownership belongs elsewhere.
"""

from __future__ import annotations

import asyncio
import fcntl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def owning(root: Path, session: str) -> AsyncIterator[None]:
    """
    Hold one session's files through work and capture; process exit releases ownership.

    Separate open descriptors contend even inside one process. The polling wait is cancellable,
    unlike a blocking flock delegated to a thread whose acquisition could outlive its caller.
    """
    directory = root / ".ownership"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / session).open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
