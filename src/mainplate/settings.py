# Everything the process is told about *itself*, read once, here.
#
# What it does not hold is anything about which model to talk to or how to authenticate to one.
# That is per-endpoint and lives in `config.yaml`, because a session chooses among endpoints and a
# process-wide answer would be a second answer to a question each session already answers.

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from mainplate.config import config_home
from mainplate.memo import DEFAULT_MEMO_BYTES

# What a fresh checkout gets with nothing set. A file under the working directory rather than
# under the user's data directory, because this is a tool you point at a project.
DEFAULT_DATABASE = Path("mainplate.db")

DEFAULT_INSTRUCTIONS = "You are a helpful assistant, working with a software engineer. Be concise and direct."

# How often a live connection looks for something new. Named here rather than written twice,
# because `Service` carries the value a handler reads and this is where the configured one enters
# the process: two defaults for one interval is a pair that can come to disagree.
DEFAULT_WATCHING = timedelta(milliseconds=200)


class Settings(BaseSettings):
    """
    The process's configuration, parsed from the environment at startup.

    Every field is settable and every field has a default that runs, so the shortest way to a
    working console is `mainplate serve` with nothing set but a `config.yaml` naming one endpoint.
    """

    model_config = SettingsConfigDict(env_prefix="MAINPLATE_", frozen=True)

    database: Path = DEFAULT_DATABASE
    """The SQLite file holding every session's checkpoint, its queue, and the session index."""

    config_home: Path = Field(default_factory=lambda: config_home(os.environ))
    """
    Where to look for `mainplate/config.yaml`, which is where the endpoints are.

    A directory rather than the file, so it moves with `XDG_CONFIG_HOME` the way everything else
    under it does, and so the one place that knows the file's name is the module that parses it.
    """

    instructions: str = DEFAULT_INSTRUCTIONS

    workspaces: Path | None = None
    """
    Where this console keeps repositories and the checkouts sessions work in, or beside the
    database when not named.

    There is no setting naming a *repository*, and that is the design rather than an omission: what
    a session works in is picked when it is created, from whatever the forges reach, and recorded
    on the session. A process-wide answer would be a second answer to a question each session
    already answers, exactly as a process-wide model would be.

    Clones and checkouts both live under here, in `clones/` and `checkouts/`, and both are outside
    any repository they hold. A checkout inside its own repository would be captured by the very
    snapshots it exists to take, so every session would hold a copy of every other session's files.
    """

    host: str = "127.0.0.1"
    """
    The address the console listens on, and the one it listens on for every job a session runs that
    serves.

    One address for both, because whatever reaches the console is what a person opens such a job
    through: a proxy, a tunnel or a tailnet forwarding the one forwards the other on a second port.
    """

    port: int = 8100

    serving_lowest: int = Field(default=3000, gt=0, lt=65536)
    serving_highest: int = Field(default=9999, gt=0, lt=65536)
    """
    The ports a session's jobs that serve may be listened on for, both ends included.

    exe.dev's proxy forwards every port in this range to a VM, privately, so the default is that
    range and a job serving there is reachable with nothing else configured. Anywhere else it is
    whichever ports that machine's tunnel or firewall lets through; a port in the range something
    else already holds is skipped, so the range only bounds where to look.
    """

    passes: int = Field(default=8, gt=0)
    """
    How many sessions this process answers at once.

    A pass spends nearly all of its time waiting on the provider, so the useful number is well
    above the core count and is bounded by what the provider will take rather than by this
    machine. Low by the worker's own standards because a personal console rarely has eight
    conversations in flight, and every pass in flight holds the one SQLite connection in turn.
    """

    refresh: timedelta = Field(default=timedelta(minutes=15), gt=timedelta())
    """
    How often to ask every endpoint what models it serves.

    The list is read once before the server is ready and then kept current by a background task, so
    this decides only how long a model added at the gateway stays invisible here. Fifteen minutes
    is short against how often a provider ships one and long enough that a console left open for a
    week makes a few hundred requests rather than a few hundred thousand.
    """

    reference_every: timedelta = Field(default=timedelta(hours=12), gt=timedelta())
    """
    How often to re-read the model reference database, where one is configured.

    Hours where `refresh` is minutes, because the two are watching different things change. A model
    appears at a gateway when somebody attaches an integration, which is a thing that happens while
    you are looking at the console; a price moves or a record is filled in when somebody upstream
    ships a release, which is not. Re-reading a four-megabyte document every fifteen minutes would
    spend real bandwidth re-learning a value that changes a few times a month.
    """

    archive_every: timedelta = Field(default=timedelta(minutes=1), gt=timedelta())
    """
    How often archived sessions still holding directories are taken off the disk.

    A minute, because the press that archives a session only records that it is archived, and this
    is the loop that acts on it: what the interval decides is how long a session pressed archived
    keeps its files, and a minute is short against the walk that measures them and long against the
    query that finds nothing to do. A session a pass still holds is left for the next round, so the
    interval is also how soon after a turn ends its files go.
    """

    vacuum_every: timedelta = Field(default=timedelta(days=1), gt=timedelta())
    """
    How often the database file is rewritten, which is what gives back what deleting a session freed.

    A day, because what the rewrite undoes accumulates over days - sessions deleted, a table grown a
    row at a time - and because the whole console holds still while it runs, for seconds on a file of
    a few hundred megabytes. It waits for a moment when nothing is working, so the interval is a
    floor rather than a schedule; see `vacuum.py`.
    """

    measure_every: timedelta = Field(default=timedelta(minutes=5), gt=timedelta())
    """
    How often every session's directories are walked to say what it takes on disk.

    Minutes, because what moves the figure is a turn or a command finishing and those are minutes
    apart, and because the walk is real I/O: a session that fetched a toolchain into its scratch
    holds tens of thousands of files, and this is every session's, every interval, in a thread the
    requests never wait on. What the interval decides is only how stale the figure beside a row can
    be, and the row says when it was measured.
    """

    fetch_every: timedelta = Field(default=timedelta(minutes=5), gt=timedelta())
    """
    How often every repository a live session works in is fetched into this console's copy of it,
    while nothing is working in it.

    What the interval decides is how far behind the remote a session's own `git fetch` can be when
    a pass or a command starts, since that reads this console's copy rather than the forge; once one
    has, `fetch_held_every` takes over. Five minutes, because a session nobody is working in reads
    nothing, so all this keeps is the copy from falling far behind between turns; and a fetch that
    finds nothing new is one round trip per repository.
    """

    fetch_held_every: timedelta = Field(default=timedelta(seconds=15), gt=timedelta())
    """
    How often a repository is fetched while a pass or a command holds one of its sessions.

    The interval that decides what a session actually sees, because a held session is the one about
    to read `origin/main`: a model asked to merge the latest `main` runs `git fetch` seconds into its
    turn, and what it gets is this console's copy as of the last fetch. Fifteen seconds, so a merge
    on the forge is in the copy before most turns have made their first call, at the cost of a round
    trip every fifteen seconds per repository somebody is working in, which against a forge that has
    nothing new transfers nothing. The loop looks as often as the shorter of the two intervals, so
    the longer is honoured to within the shorter.
    """

    watching: timedelta = Field(default=DEFAULT_WATCHING, gt=timedelta())
    """
    How often a page's live connection asks whether its session has recorded anything new.

    The whole of what decides how soon a reader sees a reply take shape, because everything else on
    that path is already immediate: a step is recorded the moment it happens and the page morphs
    whatever arrives. Two hundred milliseconds is under what a person reads as a delay and well
    above what the check costs, which is a count over one session's rows and no decoding at all.

    It bounds the *staleness* rather than the work: a connection that finds nothing new sends
    nothing, so a quiet console with ten tabs open makes fifty of those counts a second and no
    renders and no bytes.
    """

    memo_bytes: int = Field(default=DEFAULT_MEMO_BYTES, gt=0)
    """
    How many bytes of memoized results the process keeps, across every function memoized at all.

    One figure for all of them rather than one each, so what this process spends on memoizing is
    this number however many functions come to be memoized; the cost is that a burst of one kind,
    several large documents opened together, can push another kind's results out, and no function
    is promised any room. Read once at startup, so a change needs a restart. The debug page says
    what is held against it and by whom, which is how to tell whether it is the right size.
    """

    collect_young_after: int = Field(default=50_000, gt=0)
    """
    How many objects, net of those freed, the cyclic collector lets pile up before it looks at the
    youngest, which is the first of `gc.set_threshold`'s three and the only one set here.

    Measured rather than copied: over a real console's eight largest sessions drawn cold and then
    warm, the default of two thousand spent 1.8 seconds collecting with its longest pause 162 ms,
    and this with the startup freeze spent 0.06 seconds with its longest at 8 ms and held no more
    memory. A higher figure still collected nothing at all over that workload, which postpones a
    cycle's memory rather than saving the work. Read once at startup, by `serve`.
    """

    @property
    def workspace_root(self) -> Path:
        """
        Where clones and checkouts go, which is what was named or a directory beside the database.

        Beside the database because the two are the halves of one session: the checkpoint says what
        was said and the checkout holds what it was said about, so a console pointed at another
        database gets its own checkouts rather than sharing the first one's.

        **Absolute, always.** A relative path here is not a place, it is a place *plus* whatever
        directory the process happens to be in, and everything below this runs `git` with a `cwd` of
        its own choosing: a clone is made from the clones root, a checkout is added from the
        repository. Handing either a relative destination means git resolves it under that `cwd`
        rather than under this root, so the clone lands at `workspaces/clones/workspaces/clones/...`
        and the checkout lands inside the repository. Worse, the checks that make both operations
        idempotent then look at the path that was *asked for*, never find it, and every pass tries
        again - which is a `SnapshotFailed` on a session's second turn.

        Resolved here rather than in each of those callers because this is where a configured path
        enters the process, and one absolute value cannot be got wrong by the next consumer. The
        default is relative to begin with (`mainplate.db` in the working directory is what a fresh
        checkout gets), so this is the common case rather than the odd one.
        """
        named = self.workspaces if self.workspaces is not None else self.database.parent / "workspaces"
        return named.resolve()

    allowance: int | None = Field(default=1, gt=0)
    """
    How many live model requests one pass may make before it hands the turn back.

    One because a pass reads one checkpoint snapshot. A message typed while its request is running
    is visible to the next pass, so at one it reaches the next model request; a pass allowed several
    live requests would make that message wait behind however many it had left. Heartbeats make the
    wider pass safe from liveness expiry, but they do not refresh its snapshot.

    Raising the allowance trades that steering latency for less replay: every pass replays the
    recorded steps behind the next live request, so fewer passes do less graph work and fewer store
    reads. `None` lets one pass answer a whole turn, bounded only by `budget`.

    A number and not a second code path, which is what keeps it a thing to turn.
    """

    lease: timedelta = Field(default=timedelta(minutes=1), gt=timedelta())
    """
    How long a worker's last sign of life keeps its pass from being taken over.

    The worker renews the claim and its queue delivery while the pass runs, so this measures how
    quickly another worker recovers a session after the process answering it dies. It does not have
    to cover any model request or tool call; `budget` is that separate deadline.

    One minute matches the durability layer's default. Shortening it detects a dead worker sooner
    but spends more writes on renewal; lengthening it writes less often but leaves an interrupted
    session waiting longer.
    """

    budget: timedelta = Field(default=timedelta(hours=1), gt=timedelta())
    """
    How long one pass may hold a session however often its worker reports life.

    This is the deadline renewal cannot lift, so a pass stuck in work that never returns eventually
    loses its claim. It has to exceed the longest honest step: one model request, because a request
    is sent with the model's whole output limit and Anthropic's SDK budgets an hour to generate it.
    `bash` and plugin processes have shorter timeouts of their own.

    Set it too short and healthy work can be fenced and repeated; set it too long and a live process
    stuck inside one step holds the session for that long. A dead process still costs only `lease`.
    """
