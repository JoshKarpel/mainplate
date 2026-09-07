# The composition root: what is constructed once, what lives for the process, and how the routes
# reach it.
#
# Two halves share this process and one file. The console answers requests and never runs an
# agent; the worker takes sessions off the queue, drives the agent, and never answers a request.
# They are joined only by the store, which is what makes the split real rather than cosmetic, and
# the assembly below keeps them separable: `build_app` takes whatever supplies a `Service`, so the
# console runs over a store with a worker beside it (`open_console`), over one without
# (`open_store`), or over one whose worker is another process entirely.
#
# They are in one process today because a personal console should be one command, and because
# SQLite is one machine anyway. The cost is stated rather than hidden: a worker holding the one
# connection through a commit is a connection a page render queues behind, and a model call that
# hangs holds a pass for the whole lease.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Final
from typing import assert_never

from without_asgi import ASGIApp
from without_asgi import HttpScope
from without_asgi import Lifespan
from without_asgi import Response
from without_asgi import inventory
from without_asgi import make_asgi_app
from without_asgi.routing import stack
from without_async import background_task
from without_async import sleep_forever
from without_durability.interfaces import Durable
from without_durability.stepwise import Run
from without_durability.worker import work
from without_durability_sqlite import SqliteCheckpointer
from without_durability_sqlite import SqliteDurable
from without_durability_sqlite import SqliteScheduler
from without_durability_sqlite import connect
from without_durability_sqlite import migrate
from without_http import LifespanError
from without_http import serving
from without_web import Router
from without_web import catching
from without_web import handle
from without_web import http_scope
from without_web import static_files

from mainplate import records
from mainplate.agent import Wires
from mainplate.agent import build_wires
from mainplate.catalogue import Catalogues
from mainplate.catalogue import discover
from mainplate.catalogue import refreshing
from mainplate.catalogue import summarise
from mainplate.commands import Commands
from mainplate.config import Config
from mainplate.config import config_path
from mainplate.config import read_config
from mainplate.console import ASSETS
from mainplate.console import CONSOLE_ROUTES
from mainplate.console import LINKS
from mainplate.console import page_response
from mainplate.console import recover
from mainplate.conversation import Ended
from mainplate.conversation import Noting
from mainplate.conversation import Progressed
from mainplate.conversation import Stalled
from mainplate.conversation import conversing
from mainplate.exe import ExeDevGitHub
from mainplate.forge import Clones
from mainplate.forge import Forge
from mainplate.forge import Reaching
from mainplate.forge import Workspaces
from mainplate.forge import discover as reachable
from mainplate.pages import refusal_page
from mainplate.plugins.asking import Declaring
from mainplate.plugins.installed import Tier
from mainplate.plugins.installed import bundled
from mainplate.plugins.installed import installed_by
from mainplate.plugins.running import Spawned
from mainplate.reference import Prices
from mainplate.reference import References
from mainplate.reference import refreshed
from mainplate.reference import refreshing as refreshing_reference
from mainplate.sandbox import NoSandbox
from mainplate.sandbox import sandbox_command
from mainplate.service import Service
from mainplate.sessions import prepare
from mainplate.sessions import read_tending
from mainplate.sessions import set_settings
from mainplate.settings import DEFAULT_PATIENCE
from mainplate.settings import DEFAULT_WATCHING
from mainplate.settings import Settings

# Every place a repository can come from. One entry today because one exists; a `GitHub` through an
# App is another line here and nothing else, which is the whole point of the interface. Declared
# rather than configured, because a forge that needs configuring will carry its own settings and a
# forge that does not needs no switch to turn it on: `ExeDevGitHub` reaches nothing off exe.dev,
# which is the correct behaviour there rather than something to disable.
FORGES: Final[tuple[Forge, ...]] = (ExeDevGitHub(),)

ASSET_ROOT: Final = Path(__file__).parent / "assets"

logger = logging.getLogger(__name__)


class DidNotStart(RuntimeError):
    """
    The server never took traffic, carrying whatever the lifespan said about why.

    A startup failure reaches an ASGI server as a message rather than as the exception behind it,
    so this hands the CLI that message under a name of this program's own instead of the web
    library's.
    """


async def missing(service: Service, scope: HttpScope) -> Response:
    """A path nothing matched, answered with a page rather than a status: a browser is what arrives."""
    return page_response(404, refusal_page(LINKS, 404, f"no page at {scope.path}"))


def build_router() -> Router[Service]:
    """
    Every route this server answers, in one trie, over an inventory walked once here.

    `inventory` is not a directory mount: it walks the tree at this call and answers every later
    request out of the resulting mapping, so a request never contributes a filesystem path and
    there is no traversal to get wrong. The cost is the one in the name, that nothing may write
    into `assets/` while the process runs.
    """
    assets = inventory(ASSET_ROOT)
    return Router(
        routes=(*CONSOLE_ROUTES, static_files(ASSETS, assets)),
        fallback=handle(http_scope(), fn=missing),
        middleware=stack(catching(recover)),
    )


@asynccontextmanager
async def open_store(
    database: Path,
    lease: timedelta,
    catalogues: Catalogues,
    workspaces: Workspaces | None = None,
    references: References | None = None,
    watching: timedelta = DEFAULT_WATCHING,
    patience: timedelta = DEFAULT_PATIENCE,
    declaring: Declaring | None = None,
) -> AsyncIterator[Service]:
    """
    The file, migrated, as the service both halves read and write through.

    `migrate` and `prepare` both run every boot and both are idempotent. They are separate calls
    because they own different tables: the store's three are the store's, and `sessions` is ours.
    """
    opened = connect(database)
    try:
        await migrate(opened)
        await prepare(opened)
        checkpointer = SqliteCheckpointer(opened)
        # Only where there are workspaces, since a command runs in a session's worktree and a
        # console keeping none has nowhere to put one. The same pairing the file tools already have,
        # one level out.
        running = Commands(checkpointer=checkpointer, patience=patience) if workspaces is not None else None
        try:
            yield Service(
                database=opened,
                durable=SqliteDurable(checkpointer, SqliteScheduler(opened, lease=lease)),
                checkpointer=checkpointer,
                catalogues=catalogues,
                workspaces=workspaces,
                commands=running,
                # A holder either way, so nothing downstream has to ask whether there is one. An
                # empty holder is a console that was never told to look anything up, which is a
                # different state from one whose database would not load and the state a card must
                # not report.
                references=references if references is not None else References(),
                watching=watching,
                # How to run a plugin, for the two events a request handler fires rather than a pass:
                # a leader somebody typed and a control somebody pressed. Absent is a console with no
                # plugins, which is what a store opened on its own is.
                declaring=declaring,
            )
        finally:
            # Inside the store's own `finally`, and the nesting is the point: cancelling a command
            # is what makes it record that it was stopped, so the connection has to outlive that
            # write. Closed the other way round, every command in flight at a shutdown would leave a
            # panel saying it is still running.
            if running is not None:
                await running.aclose()
    finally:
        # Never `connection.close()`: the store's own `aclose` waits out any statement still
        # running on a worker thread, and closing under one segfaults the process rather than
        # raising.
        await opened.aclose()


@asynccontextmanager
async def open_console(settings: Settings, config: Config, endpoints: Wires) -> AsyncIterator[Service]:
    """
    The store, with a worker answering its sessions and a refresher keeping the models current.

    Discovery happens here rather than in `serve`, and before the store is opened, because it is
    the last thing that can refuse: a lifespan that raises never lets the server take traffic, so
    an endpoint that cannot say what it serves is a start that fails naming the endpoint rather than
    a console whose picker is empty.

    `background_task` starts each task before the first request and cancels it on shutdown, so
    there is no task to outlive the server and no lifetime to manage by hand. A cancelled pass
    leaves its session in the queue for the next process rather than losing it, which is the whole
    of the recovery story here.
    """
    catalogues = Catalogues(current=await discover(endpoints, config))
    logger.info(f"models discovered: {summarise(catalogues.current)}")
    # Discovered beside the models and for the same reasons, with one difference that matters: a
    # forge reaching nothing is an ordinary answer rather than a startup failure. Off exe.dev this
    # finds no repositories and the console is exactly what it was before there were any - a place
    # to talk, with no files.
    reaching = Reaching(current=await reachable(FORGES))
    logger.info(f"repositories reachable: {len(reaching.current.repositories)}")
    workspaces = Workspaces(
        clones=Clones(root=settings.workspace_root / "clones"),
        root=settings.workspace_root / "worktrees",
        scratch=settings.workspace_root / "scratch",
        reaching=reaching,
    )
    # Read before ready like the other two, and unlike either of them it cannot refuse to start.
    # `refreshed` never raises: an unreachable database leaves the holder empty and every card
    # simply says less, where an endpoint that cannot list its models is an endpoint somebody can
    # select and then not use. Skipped entirely when nothing is configured, which is the default,
    # so a console nobody asked to look anything up calls nobody but its own gateways.
    references = References()
    if config.model_reference is not None:
        await refreshed(references, config.model_reference)
    # Reported rather than refused, which is `forge.offers`'s promise and not `catalogue.discover`'s
    # refusal: a console with no sandbox is one whose sessions keep every file tool and are offered
    # no `bash`, which is exactly what this was before there was one. Nothing here leaves somebody
    # holding a choice they cannot use, so it is not a reason not to start. Logged because the
    # alternative - a shell tool that quietly is not there - is the state nobody can diagnose.
    try:
        bwrap: str | None = sandbox_command()
        logger.info(f"sandbox found: {bwrap}")
    except NoSandbox as missing:
        bwrap = None
        logger.warning(f"no sandbox, so sessions get no shell: {missing}")
    # Every plugin outside a worktree, which is the bundled set and whatever `config.yaml` installs.
    # Both are files this process cannot see change, so the *set* is fixed at startup; what each one
    # says is asked per session, on that session's own first pass, so a plugin edited on disk reaches
    # the next new session without the console being restarted. That matters most while somebody is
    # writing one.
    #
    # Nothing here is a name stack: an operator's `handoff` and the bundled `handoff` are two
    # plugins, both on, both listed on a session's settings step. See `Config.plugins`.
    declaring = Declaring(
        console=(*bundled(), *installed_by(Tier.USER, config.plugins)),
        speaking=Spawned(
            bwrap=bwrap,
            # `plugins` and not `scratch`, and the two roots being different is the whole of what
            # `Spawned.scratch` promises. The session's scratch is bound read-write into the model's
            # own namespace and is a root its file tools reach, so a plugin whose directory sat
            # anywhere under it would be `$HOME` for a program the model can overwrite - and this
            # console then runs that program, unattended, at every turn boundary.
            scratch=settings.workspace_root / "plugins",
            config_home=settings.config_home,
        ),
        # A repository's plugin runs behind the namespace `bash` already uses, so a console without
        # one runs none at all. A refusal rather than a fallback: running somebody else's script
        # unconfined because the sandbox is missing is the second path this console refuses
        # everywhere else.
        confining=bwrap is not None,
    )
    logger.info(f"plugins installed: {', '.join(each.qualified for each in declaring.console) or 'none'}")
    async with open_store(
        settings.database,
        settings.lease,
        catalogues,
        workspaces,
        references,
        settings.watching,
        settings.patience,
        declaring,
    ) as service:
        answering = work(
            service.durable,
            readying(
                service.durable,
                conversing(
                    endpoints,
                    settings.instructions,
                    workspaces,
                    bwrap=bwrap,
                    # The holders rather than what they currently hold, so a turn is priced at the
                    # rates in force when it ran. What that costs the worker is two dictionary
                    # lookups per model request; what it buys is a figure in the checkpoint that
                    # nothing later re-derives, so a session's total means the same thing next month
                    # as today.
                    prices=Prices(catalogues=catalogues, references=references),
                    allowance=settings.allowance,
                    # The database rather than the `Service`, because what a pass needs is two
                    # columns of one row and a `Service` is every question a request handler may ask.
                    # Given these, a pass can read what a session's plugins are set to and write what
                    # one of them remembers; given neither, every plugin runs on its declared
                    # defaults and forgets everything between events.
                    tendings=partial(read_tending, service.database),
                    storings=partial(set_settings, service.database),
                    declaring=declaring,
                    # The whole `Durable` rather than the checkpointer a pass already holds, because
                    # a note put in an inbox *queues* the session as well as being recorded: an entry
                    # appended mid-pass is invisible to the pass that appended it, so a plugin's
                    # delivery made that way would leave the session waiting on a message already in
                    # its own inbox with nothing that will ever wake it.
                    delivering=delivering(service.durable),
                ),
            ),
            limit=settings.passes,
        )
        keeping_current = refreshing(catalogues, endpoints, config, settings.refresh)
        # A stack rather than nested `async with`, because one of these tasks is conditional and
        # the alternative is the same body written twice or a branch around a `yield`.
        async with AsyncExitStack() as running:
            await running.enter_async_context(background_task(answering))
            await running.enter_async_context(background_task(keeping_current))
            if config.model_reference is not None:
                await running.enter_async_context(
                    background_task(refreshing_reference(references, config.model_reference, settings.reference_every))
                )
            yield service


def delivering(durable: Durable) -> Callable[[str, records.Note], Awaitable[None]]:
    """
    Where a note a plugin asked for goes, which is the session's own inbox.

    **Delivered and not appended**, which is the whole of what the queue is for here. An entry
    appended mid-pass is invisible to the pass that appended it - `receive` reads the snapshot loaded
    at the top, which is what makes a drain replayable - and an append queues nothing, so a note
    written that way leaves the session `Blocked` on a message already sitting in its inbox, with
    nothing that will ever wake it. Delivering makes the session ready again, and the pass that takes
    it reads a fresh snapshot with the note in it.

    The cost, stated: a note always crosses a pass boundary. That is the same bargain a steer already
    takes, one direction along, and it costs a claim rather than a round trip.
    """

    async def deliver(session: str, note: records.Note) -> None:
        await durable.deliver(session, note.recorded())

    return deliver


def readying(durable: Durable, converse: Callable[[Run], Awaitable[Ended]]) -> Callable[[Run], Awaitable[None]]:
    """
    The conversation body as the worker's, with the one thing a pass ending mid-turn needs.

    A pass stops when it has made its allowance of live model requests, which is `Completed` as far
    as the mechanism is concerned: nothing is owed by the outside world, so there is no key for a
    driver to wait on and nothing to schedule, and the worker's own answer to a completed pass is to
    do nothing at all. What is owed is another pass, immediately, and that is this.

    Asked for from *inside* the pass, while the claim is still held, which is the queue's documented
    shape rather than a race: `make_ready` is a plain upsert onto a running pass's row, and the
    pass's own `done` is conditional on the visibility it took, so the row this writes is the one
    that survives. What it costs is the queue's poll interval, which is 50ms against a round trip
    that takes seconds.

    **`Stalled` is the arm that asks for nothing, and it is why this reads the outcome rather than
    discarding it.** A pass that hit a request the provider will never accept must not be woken
    again, because the next one would ask the same question of the same recorded history. Left to
    the worker's own answer for a raised pass - leave the delivery unanswered, redeliver when the
    lease elapses - that is a session retried for ever with only a log line to show for it.

    **`Noting` is the arm that writes rather than schedules**, and it is here for the reason the
    other two are: what a session whose plugins spoke at the turn boundary is owed is a message in
    its own inbox, and putting one there is queueing, which is this function's whole subject.
    Delivering is the only one of the three that needs no `make_ready` beside it, because `deliver`
    appends the entry and queues the session in a single commit.

    What each note says is the plugin's, so nothing here composes anything: this console has no
    words of its own left to put in a conversation.

    Here rather than in `conversation.py`, because the body is about answering a session and this is
    about the queue in front of it. That split is what lets one console run the worker beside the
    console and another run it somewhere else entirely.
    """

    async def answer(run: Run) -> None:
        match await converse(run):
            case Progressed():
                await durable.scheduler.make_ready(run.workflow)
            case Stalled():
                logger.warning(f"{run.workflow} stalled on a request no pass can make; not waking it again")
            case Noting(notes=notes):
                for note in notes:
                    logger.info(f"{run.workflow}: {note.plugin} asked for a message to be put to it")
                    await durable.deliver(run.workflow, note.recorded())
            case _ as unreachable:
                assert_never(unreachable)

    return answer


def build_app(opening: Lifespan[Service]) -> ASGIApp:
    """
    The routes, over whatever supplies a `Service` for the life of the server.

    A `Lifespan` and not a `Settings`, which is what makes the two halves separable: the console
    cannot tell whether a worker is running inside that block, so the same app serves a process
    that answers its own sessions and one that leaves them to another.
    """
    return make_asgi_app(opening, http=build_router().dispatch)


async def serve(settings: Settings) -> None:
    """Run the console and the worker until cancelled, which for the CLI means until a signal."""
    config = read_config(config_path(settings.config_home))
    endpoints = build_wires(config)

    def opening() -> AbstractAsyncContextManager[Service]:
        return open_console(settings, config, endpoints)

    try:
        async with serving(build_app(opening), host=settings.host, port=settings.port):
            await sleep_forever()
    except LifespanError as unstarted:
        raise DidNotStart(str(unstarted)) from unstarted


def serve_until_stopped(settings: Settings) -> None:
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        return
