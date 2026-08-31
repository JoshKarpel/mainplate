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
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Final

from pydantic_ai import Agent
from without_asgi import ASGIApp
from without_asgi import HttpScope
from without_asgi import Lifespan
from without_asgi import Response
from without_asgi import inventory
from without_asgi import make_asgi_app
from without_asgi.routing import stack
from without_async import background_task
from without_async import sleep_forever
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

from mainplate.agent import build_agent
from mainplate.console import ASSETS
from mainplate.console import CONSOLE_ROUTES
from mainplate.console import LINKS
from mainplate.console import page_response
from mainplate.console import recover
from mainplate.conversation import conversing
from mainplate.pages import refusal_page
from mainplate.service import Service
from mainplate.sessions import prepare
from mainplate.settings import Settings

ASSET_ROOT: Final = Path(__file__).parent / "assets"


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
async def open_store(database: Path, lease: timedelta) -> AsyncIterator[Service]:
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
        yield Service(
            database=opened,
            durable=SqliteDurable(checkpointer, SqliteScheduler(opened, lease=lease)),
            checkpointer=checkpointer,
        )
    finally:
        # Never `connection.close()`: the store's own `aclose` waits out any statement still
        # running on a worker thread, and closing under one segfaults the process rather than
        # raising.
        await opened.aclose()


@asynccontextmanager
async def open_console(settings: Settings, agent: Agent[None, str]) -> AsyncIterator[Service]:
    """
    The store, with a worker answering its sessions for as long as the block lasts.

    `background_task` starts the worker before the first request and cancels it on shutdown, so
    there is no task to outlive the server and no lifetime to manage by hand. A cancelled pass
    leaves its session in the queue for the next process rather than losing it, which is the whole
    of the recovery story here.
    """
    async with open_store(settings.database, settings.lease) as service:
        answering = work(service.durable, conversing(agent), limit=settings.passes)
        async with background_task(answering):
            yield service


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
    agent = build_agent(settings.model, settings.instructions)

    def opening() -> AbstractAsyncContextManager[Service]:
        return open_console(settings, agent)

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
