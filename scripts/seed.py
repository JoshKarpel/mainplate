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
# moment the console started, which on a real profile means a real request and real money for a
# fixture nobody asked to have answered. Send a message yourself to see that path.

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from gallery import CATALOGUE
from gallery import CONVERSATION
from gallery import LISTED
from gallery import TOOL_IN_FLIGHT
from gallery import recorded

from mainplate.agent import Choice
from mainplate.app import open_store
from mainplate.catalogue import Catalogues
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import recorded_choice
from mainplate.service import Service
from mainplate.sessions import Session
from mainplate.sessions import enrol
from mainplate.sessions import read_session

DEFAULT_DATABASE = Path("mainplate-demo.db")

# Long enough that nothing here is ever fenced; nothing takes a pass anyway.
LEASE = timedelta(minutes=10)

# What each seeded session is on. Different models across the branches, because that is the whole
# point of a branch: the sidebar shows one conversation answered three ways.
ON_SONNET = Choice(profile="llm-anthropic", model="anthropic/claude-sonnet-4-6", thinking="high")
ON_OPUS = Choice(profile="llm-anthropic", model="anthropic/claude-opus-4-8", thinking="xhigh")
ON_GPT = Choice(profile="llm-openai", model="openai/gpt-5.5", thinking=None)


def planted() -> tuple[tuple[Session, Choice, dict[str, object]], ...]:
    """
    Every session to write: its row, what it is on, and the checkpoint under it.

    The branch relationships are the ones `LISTED` already declares, so the tree the sidebar draws
    here is the tree the screenshots show.
    """
    parent, branch, deeper, other = LISTED
    return (
        (parent, ON_SONNET, recorded(CONVERSATION, TOOL_IN_FLIGHT)),
        # Branched at turn 1 and answered on a different model, so it carries turn 0 and nothing
        # after it. What a branch inherits is exactly the turns before the one it branched at, and
        # these checkpoints say so rather than being a plausible-looking approximation.
        (branch, ON_OPUS, recorded(CONVERSATION)),
        # A branch of that branch, at turn 2, on the other wire entirely: turns 0 and 1 come across.
        (deeper, ON_GPT, recorded(CONVERSATION, TOOL_IN_FLIGHT)),
        (other, ON_SONNET, recorded(CONVERSATION)),
    )


async def plant(service: Service, session: Session, chosen: Choice, checkpoint: dict[str, object]) -> bool:
    """One session, or nothing at all if the index already has it."""
    if await read_session(service.database, session.id) is not None:
        return False
    await enrol(service.database, session)
    await service.checkpointer.supply(session.id, CHOICE_KEY, recorded_choice(chosen))
    for key, value in checkpoint.items():
        await service.checkpointer.supply(session.id, key, value)
    return True


async def seed(database: Path) -> None:
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        for session, chosen, checkpoint in planted():
            wrote = await plant(service, session, chosen, checkpoint)
            origin = f" (forked from turn {session.forked.turn})" if session.forked else ""
            print(f"  {'wrote  ' if wrote else 'skipped'} {session.id[:12]}… {session.title}{origin}")
    print(f"\n{database} is ready. `just demo` serves it; the fixtures are on {ON_SONNET.profile}.")


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATABASE))
