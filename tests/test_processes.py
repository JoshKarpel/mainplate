from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

from mainplate.processes import reaped


class TestEndingAProcessNobodyWillRead:
    async def test_a_process_that_ignores_being_asked_to_stop_is_killed_once_the_grace_is_up(self) -> None:
        """
        What bounds the grace `reaped` gives: the asking is a courtesy, and a process that declines
        it is made to stop anyway rather than left running for as long as it likes.

        The shell says it is ready only after it has set `SIGTERM` aside, which is what lets the test
        ask at a moment the asking is certain to be ignored rather than racing the `trap`.
        """
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            'trap "" TERM; echo ready; exec sleep 5',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert await process.stdout.readline() == b"ready\n"

        reaped(process, grace=timedelta(milliseconds=50))

        assert await process.wait() == -signal.SIGKILL
