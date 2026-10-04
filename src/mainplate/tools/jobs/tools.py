# The job tools: starting a command that keeps running in the background, reading what it printed,
# waiting for it, stopping it, and listing what this session has.
#
# A job is the one thing a session runs that outlives the call that asked for it, so these tools run
# nothing themselves. `start_job` records that a job should be running and asks the console to make
# it so; the console's `jobs.py` owns the process, the port, and starting it again after a restart.
# That is what keeps a replayed call from starting a second job: the call finds the record it
# already made.
#
# **The descriptions are the model's whole picture of how this works**, and `start_job`'s is long on
# purpose. A job here does not behave like a background process in a terminal, in ways a model would
# otherwise get wrong and waste turns on: its own `bash` cannot reach it, it is started again from
# the top after a restart, and the person reaches a server at an address the server never sees.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from typing import Protocol

from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

# How much of what a job printed a listing shows per job, from the end: the last lines are where it
# says it failed or what it is listening on.
LISTING_LINES: Final = 20

# How much `read_job` shows by default, and the most it will show.
READING_LINES: Final = 200
MOST_LINES: Final = 2_000

# How many jobs a listing names, newest first, so a session that started dozens over a long
# conversation does not spend a page of context on ones that ended hours ago.
LISTED: Final = 10

# How long `wait_job` waits by default and at most, which is `bash`'s bound for the same reason: a
# pass waiting is a pass holding its session.
DEFAULT_WAIT: Final = 60
MOST_WAIT: Final = 600


@dataclass(frozen=True, slots=True)
class Listed:
    """
    One job as the tools report it: what it runs, where, and how it is doing.

    `ended` is the exit status once it has stopped and nothing while it should be running, which is
    the same split a command's result makes. `output` is what it has printed, the live buffer while it
    runs and the recorded result once it has stopped. `outside` is the port the person opens it on
    and `inside` the one it listens on, both nothing for a job that serves nothing.
    """

    entry: str
    said: str
    inside: int | None
    outside: int | None
    ended: int | None
    output: str


class Jobs(Protocol):
    """
    What these tools need of the console's jobs, and nothing else.

    A protocol rather than the class in `jobs.py`, because that module builds a sandbox from a
    session's recorded choice and so reaches `agent.py`, which reaches this package: importing it
    here would be a cycle. The console's `Jobs` satisfies this by shape.
    """

    async def start(self, session: str, said: str, port: int | None, asked: str | None, plugin: str | None) -> str: ...

    async def stop(self, session: str, entry: str, why: str, telling: bool) -> bool: ...

    async def listed(self, session: str) -> Sequence[Listed]: ...

    async def waited(self, session: str, entry: str, within: timedelta) -> Listed | None: ...


@dataclass(frozen=True, slots=True)
class JobsInTurn:
    """
    The console's jobs as one turn of one session reaches them.

    The turn is carried because a job records the call that started it, and a call's id is only
    unique within its turn of its session, which is `Artifacts`' reason one module over.
    """

    jobs: Jobs
    session: str
    turn: int


def tail(output: str, lines: int) -> str:
    """The end of what a job printed, with a count of what was left off the front."""
    held = output.rstrip("\n").split("\n")
    if len(held) <= lines:
        return "\n".join(held)
    return "\n".join((f"… {len(held) - lines} earlier lines not shown", *held[-lines:]))


def described(job: Listed, lines: int) -> str:
    """One job as the model is told it, which is everything it needs to decide what to do next."""
    state = "running" if job.ended is None else f"ended, exit {job.ended}"
    where = (
        ""
        if job.inside is None
        else f", listening inside on port {job.inside}"
        + ("" if job.outside is None else f", opened by the person on port {job.outside}")
    )
    said = tail(job.output, lines) if job.output.strip() else "(nothing printed yet)"
    return f"job {job.entry}: `{job.said}`, {state}{where}\n{said}"


def job_tools(reaching: JobsInTurn) -> FunctionToolset[None]:
    """
    `start_job`, `list_jobs`, `read_job`, `wait_job` and `stop_job`, bound to one turn of one session.

    Offered wherever `bash` is, since a job runs in the same sandbox a command does, and nowhere else:
    a session with no shell has nothing for one to run in.
    """
    toolset = FunctionToolset[None]()
    jobs, session = reaching.jobs, reaching.session

    async def one(job: str) -> Listed:
        found = [each for each in await jobs.listed(session) if each.entry == job]
        if not found:
            raise ModelRetry(f"there is no job {job} in this session; `list_jobs` names them")
        return found[0]

    async def start_job(ctx: RunContext[None], command: str, port: int | None = None) -> str:
        """
        Start a command in the background and keep it running, such as a dev server, a file watcher,
        or a build or test run too long for `bash`.

        It runs until it exits, you or the person stop it, or the session is archived. **It must be
        idempotent**: if the console restarts, a job still running is started again from the top, in
        the checkout as it is then, and its output begins with a line saying so. A server or a
        watcher already is; a migration or a deploy has to be written to be safe to run twice.

        How it runs, which differs from a background process in a terminal:

        - **It has its own sandbox**, with the same files, environment and network setting as
          `bash`, but one that lasts. **Your `bash` commands cannot reach it**: each runs in a
          namespace of its own, so `curl localhost:PORT` there fails even while this job is up, and
          `ps` will not show it. Read what it prints with `read_job`, and wait for it with
          `wait_job`. What it writes to the checkout or the scratch directory you can read as usual.
        - **To serve something the person opens in a browser, pass `port`**: the port it listens on,
          on 127.0.0.1 (or localhost). It is also set as `$PORT`, e.g.
          `npm run dev -- --port $PORT` or `python -m http.server $PORT`. The person opens it through
          a link the console shows them, at the console's own host on a port of its own, often
          behind an HTTPS proxy, so the `Host` header the server sees is not `localhost`: tell
          frameworks that check it to accept any host (Vite's `server.allowedHosts: true`, Next.js's
          `allowedDevOrigins`), and use relative URLs in what it serves. To check a page yourself,
          start a server and fetch or screenshot it inside one `bash` command instead.
        - **Starting the same command twice starts two jobs.** Call `list_jobs` first, and
          `stop_job` one you are replacing.

        Args:
            ctx: The run this call is part of, for the call's own id; never sent to the model.
            command: The shell command, run in the foreground with `sh -c` where `bash` commands start.
            port: The port it serves on, for a job the person opens in a browser; left out for any
                other job.

        """
        if ctx.tool_call_id is None:
            raise ModelRetry("this call has no id to start a job under")
        if not command.strip():
            raise ModelRetry("a command to run is required")
        if port is not None and not 0 < port < 65536:
            raise ModelRetry("port is a TCP port number, from 1 to 65535")
        entry = await jobs.start(session, command.strip(), port, f"{reaching.turn}:{ctx.tool_call_id}", None)
        job = await one(entry)
        if job.ended is not None:
            return f"Job {entry} ended as soon as it started.\n{described(job, LISTING_LINES)}"
        opened = (
            ""
            if job.outside is None
            else f" The person opens it on port {job.outside} of the console's host, through a link the "
            f"console shows them."
        )
        return f"Started job {entry}: `{job.said}`.{opened} Read what it prints with `read_job`."

    async def list_jobs() -> str:
        """
        The jobs this session has started, newest first: what each runs, whether it is still
        running, its ports, and the last lines it printed.
        """
        listed = list(await jobs.listed(session))[-LISTED:]
        if not listed:
            return "No jobs."
        return "\n\n".join(described(each, LISTING_LINES) for each in reversed(listed))

    async def read_job(job: str, lines: int = READING_LINES) -> str:
        """
        What one job has printed, from the end, and whether it is still running.

        Args:
            job: The job's id, from `start_job` or `list_jobs`.
            lines: How many of its last lines to show.

        """
        return described(await one(job), max(1, min(lines, MOST_LINES)))

    async def wait_job(job: str, seconds: int = DEFAULT_WAIT) -> str:
        """
        Wait for one job to end, for at most `seconds`, and say how it is doing then.

        For a build or a test run you started with `start_job`. A server never ends on its own, so
        waiting on one only spends the time.

        Args:
            job: The job's id, from `start_job` or `list_jobs`.
            seconds: The most to wait before reporting it as still running.

        """
        await one(job)
        waited = await jobs.waited(session, job, timedelta(seconds=max(1, min(seconds, MOST_WAIT))))
        if waited is None:
            raise ModelRetry(f"there is no job {job} in this session; `list_jobs` names them")
        return described(waited, LISTING_LINES)

    async def stop_job(job: str) -> str:
        """
        Stop a job this session started. What it printed stays readable with `read_job`.

        Args:
            job: The job's id, from `start_job` or `list_jobs`.

        """
        if not await jobs.stop(session, job, "stopped by the model", False):
            raise ModelRetry(f"there is no running job {job} in this session; `list_jobs` names them")
        return f"Stopped job {job}."

    toolset.add_function(start_job, takes_ctx=True)
    toolset.add_function(list_jobs)
    toolset.add_function(read_job)
    toolset.add_function(wait_job)
    toolset.add_function(stop_job)
    return toolset
