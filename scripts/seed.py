# The gallery's fixtures, written into a real database, so the console can be driven rather than
# looked at.
#
# The same conversations `gallery.py` renders to files, from the same constants: a fixture defined
# twice is a fixture that drifts, and the point of both scripts is that what you look at is what
# the console produces. What this adds is everything a static page cannot show - the sidebar
# reordering as you click between branches, a fork actually being made, the rail projecting onto a
# transcript that came from a store.
#
# Idempotent, and deliberately not destructive: it skips a session already in the index rather than
# rewriting one, so running it twice does nothing and running it against a database that has real
# conversations in it adds to them without touching what is there.
#
# It seeds only *settled* sessions. A turn left unanswered would be picked up by the worker the
# moment the console started, which on a real endpoint means a real request and real money for a
# fixture nobody asked to have answered. Send a message yourself to see that path.

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from mainplate import records
from mainplate.agent import Choice
from mainplate.app import open_store
from mainplate.catalogue import Catalogues
from mainplate.conversation import ARCHIVED_KEY
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
from mainplate.sessions import Session
from mainplate.sessions import enrol
from mainplate.sessions import read_session
from scripts.gallery import CATALOGUE
from scripts.gallery import CONVERSATION
from scripts.gallery import DECLARED
from scripts.gallery import ENROLLED
from scripts.gallery import LISTED
from scripts.gallery import TOOL_IN_FLIGHT
from scripts.gallery import recorded

DEFAULT_DATABASE = Path("mainplate-demo.db")

# Long enough that nothing here is ever fenced; nothing takes a pass anyway.
LEASE = timedelta(minutes=10)

# What each seeded session is on. Different models across the branches, because that is the whole
# point of a branch: the sidebar shows one conversation answered three ways.
ON_SONNET = Choice(endpoint="llm-anthropic", model="anthropic/claude-sonnet-4-6", thinking="high")
ON_OPUS = Choice(endpoint="llm-anthropic", model="anthropic/claude-opus-4-8", thinking="xhigh")
ON_GPT = Choice(endpoint="llm-openai", model="openai/gpt-5.5", thinking=None)


def planted() -> tuple[tuple[Session, Choice, dict[str, object]], ...]:
    """
    Every session to write: its row, what it is on, and the checkpoint under it.

    The branch relationships are the ones `LISTED` already declares, so the tree the sidebar draws
    here is the tree the screenshots show.
    """
    parent, branch, deeper, other, detached, archived = LISTED
    if archived.archived is None:  # pragma: no cover - the fixture says it is, and this is what reads it
        raise ValueError("the gallery's last session is the archived one, and it records no time")
    return (
        (parent, ON_SONNET, recorded(CONVERSATION, TOOL_IN_FLIGHT)),
        # Branched at turn 1 and answered on a different model, so it carries turn 0 and nothing
        # after it. What a branch inherits is exactly the turns before the one it branched at, and
        # these checkpoints say so rather than being a plausible-looking approximation.
        (branch, ON_OPUS, recorded(CONVERSATION)),
        # A branch of that branch, at turn 2, on the other wire entirely: turns 0 and 1 come across.
        (deeper, ON_GPT, recorded(CONVERSATION, TOOL_IN_FLIGHT)),
        (other, ON_SONNET, recorded(CONVERSATION)),
        # On a repository nothing reaches, so a demo console has the row that renders a bare id.
        (detached, ON_SONNET, recorded(CONVERSATION)),
        # Archived, with the key the press writes rather than only the row's field, because the row's
        # field is *read* out of that key: seeding the field alone would be a session the sidebar
        # draws as open. The reconciler finds nothing on disk for it and leaves it be.
        (
            archived,
            ON_SONNET,
            {**recorded(CONVERSATION), ARCHIVED_KEY: records.Archived(at=archived.archived).recorded()},
        ),
    )


async def plant(service: Service, session: Session, chosen: Choice, checkpoint: dict[str, object]) -> bool:
    """One session, or nothing at all if the index already has it."""
    if await read_session(service.database, session.id) is not None:
        return False
    await enrol(service.database, session)
    # The repository comes off the fixture row rather than being restated on the choice, because a
    # read puts it there: the index holds no such column and the sidebar gets it out of `choice`.
    # Declaring it twice is how a seeded session would come to say one thing and render another.
    #
    # Both of the rules `Service.start` applies, because this is the one place that would otherwise
    # skip them: writing the checkpoint directly bypasses the service, so without them a seeded
    # session names a repository while recording that it reaches no files, or that it is on no
    # branch - contradictory pairs nothing else in this console can produce. `settled` is one call
    # rather than a rule per field, so a field added to what a repository decides needs no edit here;
    # `branching` is separate for the reason it is separate there, that it needs the session's id.
    working = replace(chosen, repository=session.repository)
    await service.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(working.settled().branching(session.id)))
    # Every moment of the settings step, planted directly for the reason everything else here is: a
    # seeded session has had no pass and nobody has pressed anything, and a session with no
    # registration is one still sitting on that step rather than one you can read. The gallery's own
    # fixtures, so the demo console and the stills show the same switches and the same card.
    #
    # **The confirmation goes in too, and leaving it out would be a contradictory record** - which is
    # this file's standing hazard, since it is the one writer that is not `Service`. A registration
    # with no `plugins:setup:0` beside it says a session ran plugins nobody pressed the button for,
    # which is a state the console cannot produce and nothing here should invent.
    await service.checkpointer.supply(session.id, DECLARED_KEY, recorded_declaration(DECLARED))
    await service.checkpointer.supply(session.id, REPOSITORY_DECLARED_KEY, recorded_declaration(()))
    await service.checkpointer.supply(session.id, setup_key(0), records.Confirmed().recorded())
    await service.checkpointer.supply(session.id, PLUGINS_KEY, recorded_registration(ENROLLED))
    await service.checkpointer.supply(session.id, REPOSITORY_PLUGINS_KEY, recorded_registration(()))
    for key, value in checkpoint.items():
        await service.checkpointer.supply(session.id, key, value)
    return True


async def seed(database: Path) -> None:
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        for session, chosen, checkpoint in planted():
            wrote = await plant(service, session, chosen, checkpoint)
            origin = f" (forked from turn {session.forked.turn})" if session.forked else ""
            print(f"  {'wrote  ' if wrote else 'skipped'} {session.id[:12]}… {session.title}{origin}")
    print(f"\n{database} is ready. `just demo` serves it; the fixtures are on {ON_SONNET.endpoint}.")


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATABASE))
