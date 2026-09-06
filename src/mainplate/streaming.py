# The one connection a page holds open, and what goes down it.
#
# A page watches its conversation over a single event stream rather than asking for it once a
# second. What changed is only *when* the server speaks: it renders the same regions from the same
# checkpoint by the same functions, so nothing here is a second account of a conversation and a
# reader with the stream broken sees exactly what the last message left on screen.
#
# **Every message is a whole current render, never a delta.** That one rule is what keeps this
# small. A dropped connection costs nothing, a reconnect needs no replay and no cursor, a message
# that arrives twice morphs to a no-op, and the server keeps no idea of what any reader has already
# been sent. `id` rides along as the change token so a reader can see how far a stream has got, and
# nothing depends on it coming back.
#
# The stream belongs to the *page*, not to the region it currently updates. Each message carries
# `<hx-partial>` elements that name their own target, so one connection can drive several regions
# and a region added later is one more partial in a message that already exists rather than a
# second connection. htmx applies each partial and leaves the connecting element alone.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

from without_asgi.sse import Event
from without_asgi.sse import ServerSentEvent
from without_html import Element
from without_html import Node
from without_html import element
from without_html import render

from mainplate.pages import CACHE_ID
from mainplate.pages import SWAP
from mainplate.pages import TRANSCRIPT_ID
from mainplate.pages import Links
from mainplate.pages import cache_note
from mainplate.pages import transcript_region
from mainplate.service import Service


def partial(target: str, swap: str, children: Node) -> Element:
    """
    One region of the page, addressed by the message that carries it rather than by the connection.

    `<hx-partial>` and not an out-of-band swap, because the target and the swap are the *message's*
    to state: the element carrying the connection says nothing about which regions a message
    touches, so a message may touch a different set from the one before it. A message made only of
    these leaves the connecting element untouched, which is what lets that element be an inert sink
    rather than a region somebody is reading.
    """
    return element("hx-partial", attrs={"hx-target": f"#{target}", "hx-swap": swap}, children=children)


async def watching(service: Service, links: Links, session: str, every: timedelta) -> AsyncIterator[ServerSentEvent]:
    """
    One session's conversation, sent whenever it has recorded anything new.

    The token is what makes this cheap enough to ask often: it counts a session's steps and decodes
    none of them, so a quiet conversation costs one indexed count per tick and no render, no markup
    and no bytes. Only a token that moved is worth the read that follows it, and a checkpoint is
    append-only, so the only thing that can move it is a record that did not exist before.

    The first pass through always sends, because nothing has been seen yet. That is deliberate
    rather than incidental: it is what makes a reconnect correct with no replay and no resumption
    cursor, since what a reconnecting page needs is the current state and that is the only thing
    this ever sends. The cost is one redundant morph on connect, over markup the page is already
    showing, which changes nothing on screen.

    Polled rather than woken, so the two halves of this process stay joined only by the store. The
    console does not learn that a session moved from the worker that moved it; it learns by looking,
    exactly as it would if the worker were another process entirely.
    """
    seen: int | None = None
    while True:
        now = await service.token(session)
        if now != seen:
            seen = now
            showing = await service.read(session)
            if showing is None:  # pragma: no cover - the route checked, and nothing deletes a session
                return
            # Two regions on one connection, which is what `partial` exists for. The cache note lives
            # in the composer rather than in the transcript, so nothing else replaces it, and what it
            # says goes stale on every turn: the context it prices grows, and when the prefix was last
            # written moves. `outerHTML` rather than the transcript's morph, because it is one short
            # line with nothing in it worth preserving across a swap.
            yield Event(
                data=render(
                    [
                        partial(TRANSCRIPT_ID, SWAP, transcript_region(links, showing)),
                        partial(CACHE_ID, "outerHTML", cache_note(showing)),
                    ]
                ),
                id=str(now),
            )
        await asyncio.sleep(every.total_seconds())
