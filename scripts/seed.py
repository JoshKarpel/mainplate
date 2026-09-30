# The gallery's fixtures, written into a real database, so the console can be driven rather than
# looked at.
#
# The same sessions `gallery.py` renders to files, out of the same table: `FIXTURES` is one
# declaration of which sessions exist, what each is on, and what its checkpoint holds, and this
# plants every row of it. A fixture defined twice is a fixture that drifts, and the point of both
# scripts is that what you look at is what the console produces. What this adds is everything a
# static page cannot show - the sidebar reordering as you click between branches, a fork actually
# being made, the rail projecting onto a transcript that came from a store.
#
# It replaces its own sessions and nothing else: every fixture's session is taken out of the index
# and its records discarded, then planted again from the table, so a changed fixture reaches the demo
# on the next run without the database or the server being restarted. A session that is not one of
# the fixtures' ids is never touched, so running it against a database with real conversations in it
# leaves them as they were. The page a reader has open on a replaced session is stale until it is
# loaded again, since nothing tells a live connection that its session was rewritten underneath it.
#
# Every fixture is *settled*, and that is the table's rule rather than this script's: a turn left
# unanswered would be a page reading as answered by nothing, and were a worker ever to pick one up
# on a real endpoint it would be a real request and real money for a fixture nobody asked to have
# answered. Send a message yourself to see that path.

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from mainplate import artifacts
from mainplate import records
from mainplate.app import open_store
from mainplate.catalogue import Catalogues
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import DECLARED_KEY
from mainplate.conversation import PLUGINS_KEY
from mainplate.conversation import REPOSITORY_DECLARED_KEY
from mainplate.conversation import REPOSITORY_PLUGINS_KEY
from mainplate.conversation import recorded_choice
from mainplate.conversation import setup_key
from mainplate.plugins.asking import recorded_declaration
from mainplate.plugins.asking import recorded_registration
from mainplate.service import Service
from mainplate.sessions import enrol
from mainplate.sessions import read_session
from scripts.gallery import CATALOGUE
from scripts.gallery import DECLARED
from scripts.gallery import ENROLLED
from scripts.gallery import FIXTURES
from scripts.gallery import Fixture

DEFAULT_DATABASE = Path("mainplate-demo.db")

# Long enough that nothing here is ever fenced; nothing takes a pass anyway.
LEASE = timedelta(minutes=10)


# The console never takes a session out of its index, since a conversation ends by being archived,
# so there is no function for it beside `enrol` and this is written here instead: a way to delete a
# session is something the console would then be tempted to use.
UNENROL = "DELETE FROM sessions WHERE id = ?"


async def discard(service: Service, session: str) -> bool:
    """Take one session out of the index and forget everything recorded under it, if it was there."""
    if await read_session(service.database, session) is None:
        return False
    await service.durable.delete(session)
    await service.database.run(lambda connection: connection.execute(UNENROL, (session,)))
    return True


async def plant(service: Service, fixture: Fixture) -> None:
    """One session, from nothing."""
    await enrol(service.database, fixture.session)
    # The choice as `Fixture.of` settled it against the row, which is where the two rules
    # `Service.start` applies are applied to a fixture: writing the checkpoint directly bypasses the
    # service, and without them a seeded session names a repository while recording that it reaches
    # no files, or that it is on no branch - contradictory pairs nothing else in this console can
    # produce.
    await service.checkpointer.supply(fixture.session.id, CHOICE_KEY, recorded_choice(fixture.chosen))
    # Every moment of the settings step, planted directly for the reason everything else here is: a
    # seeded session has had no pass and nobody has pressed anything, and a session with no
    # registration is one still sitting on that step rather than one you can read. The gallery's own
    # fixtures, so the demo console and the stills show the same switches and the same card.
    #
    # **The confirmation goes in too, and leaving it out would be a contradictory record** - which is
    # this file's standing hazard, since it is the one writer that is not `Service`. A registration
    # with no `plugins:setup:0` beside it says a session ran plugins nobody pressed the button for,
    # which is a state the console cannot produce and nothing here should invent.
    await service.checkpointer.supply(fixture.session.id, DECLARED_KEY, recorded_declaration(DECLARED))
    await service.checkpointer.supply(fixture.session.id, REPOSITORY_DECLARED_KEY, recorded_declaration(()))
    await service.checkpointer.supply(fixture.session.id, setup_key(0), records.Confirmed().recorded())
    await service.checkpointer.supply(fixture.session.id, PLUGINS_KEY, recorded_registration(ENROLLED))
    await service.checkpointer.supply(fixture.session.id, REPOSITORY_PLUGINS_KEY, recorded_registration(()))
    for key, value in fixture.checkpoint.items():
        await service.checkpointer.supply(fixture.session.id, key, value)


DEMO_HTML = (
    b'<!doctype html><html><head><meta charset="utf-8">'
    b"<style>body{font:2rem system-ui;margin:4rem;background:#151b2a;color:#f4e2a0}</style>"
    b"</head><body><h1>Independent artifact</h1><p>Previewed without a model call.</p></body></html>"
)


async def seed(database: Path) -> None:
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        for fixture in FIXTURES:
            replaced = await discard(service, fixture.session.id)
            await plant(service, fixture)
            session = fixture.session
            origin = f" (forked from turn {session.forked.turn})" if session.forked else ""
            print(f"  {'replaced' if replaced else 'wrote   '} {session.id[:12]}… {session.title}{origin}")
        if await artifacts.completed(service.database, "demo-artifact") is None:
            saved = await artifacts.import_html(service.database, DEMO_HTML, "demo-artifact", title="Demo artifact")
            print(f"  artifact {saved.id} version {saved.version}: {saved.title}")
    print(f"\n{database} is ready. `just demo` serves it; the fixtures are on {CATALOGUE.default.endpoint}.")


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATABASE))
