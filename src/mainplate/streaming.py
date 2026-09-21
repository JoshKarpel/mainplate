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
from mainplate.pages import LISTED_ID
from mainplate.pages import LOADED
from mainplate.pages import SETUP_ID
from mainplate.pages import SWAP
from mainplate.pages import TRANSCRIPT_ID
from mainplate.pages import Links
from mainplate.pages import Reader
from mainplate.pages import cache_note
from mainplate.pages import listed_region
from mainplate.pages import settling
from mainplate.pages import setup_step
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


async def token(service: Service, session: str | None) -> str:
    """
    Whether anything the page is showing has moved: the session's own token beside the list's.

    Two tokens joined rather than one, because they guard different regions and the list is on every
    page: a page showing no session, which is the start page, is the list's token alone.
    """
    listing = await service.listing_token()
    return listing if session is None else f"{await service.token(session)}|{listing}"


async def watching(
    service: Service,
    links: Links,
    reader: Reader,
    session: str | None,
    every: timedelta,
    on_step: bool = False,
) -> AsyncIterator[ServerSentEvent]:
    """
    What one page is showing, sent whenever any of it has recorded anything new.

    `session` is the conversation the page is looking at, or nothing for the start page, which looks at
    none; the session list is on every page and is sent to every one.

    `reader` is what the page it is talking to was drawn against, taken from that page's own
    connecting request rather than from this process: every region here is rendered by the same
    function the page was, so a stream that used the console's own clock would morph UTC moments into
    a transcript whose rules are in Chicago, one turn at a time.

    `on_step` is the shape the page was drawn in, which the page states when it connects: whether it
    is the settings step or the conversation. The regions sent are that shape's, and the moment the
    checkpoint's shape stops being the page's the stream says `LOADED` once and ends, because
    nothing it could send would land anywhere on the page it is talking to. A page on the step whose
    session has loaded is the case; the other direction cannot happen, since a registration is
    written once.

    The token is what makes this cheap enough to ask often: it counts a session's steps and decodes
    none of them, so a quiet conversation costs three indexed reads per tick and no render, no markup
    and no bytes. Only a token that moved is worth the read that follows it. The list's half is three
    aggregates the same way, so a quiet console costs the same.

    **Nothing here marks the session as seen, and that is deliberate.** A send is not a showing: the
    server learns that a page has gone only when a write to it fails, and the first write after a tab
    is hidden goes into a socket the browser has already left, so a mark made here would land on
    exactly the answer that arrived while nobody was looking. The page marks instead, after it has
    swapped a message in; see `Links.to_seen`.

    It carries where the session stands with the worker as well as how much it has recorded, which is
    what makes a broken pass visible here: a pass that falls over records nothing, so a token made of
    the count alone would hold still and this would send nothing while the page sat under a spinner.
    What moves instead is the claim being taken and let go and the next attempt being scheduled. So the
    token is not monotone, and nothing here needs it to be: `!=` is the whole of what it is asked.

    The first pass through always sends, because nothing has been seen yet. That is deliberate
    rather than incidental: it is what makes a reconnect correct with no replay and no resumption
    cursor, since what a reconnecting page needs is the current state and that is the only thing
    this ever sends. The cost is one redundant morph on connect, over markup the page is already
    showing, which changes nothing on screen.

    Polled rather than woken, so the two halves of this process stay joined only by the store. The
    console does not learn that a session moved from the worker that moved it; it learns by looking,
    exactly as it would if the worker were another process entirely.
    """
    seen: str | None = None
    while True:
        now = await token(service, session)
        if now != seen:
            regions: list[Element] = []
            if session is not None:
                showing = await service.read(session)
                if showing is None:  # pragma: no cover - the route checked, and nothing deletes a session
                    return
                # The page's shape against the checkpoint's, before anything is rendered: a partial
                # with nowhere to go is silently dropped, so a page on the step being sent the
                # conversation's regions would sit under its spinner for ever with nothing saying
                # why. Once, and then out, because a page told this reloads and the reload opens a
                # connection of its own.
                if on_step != settling(showing):
                    yield Event(data="", type=LOADED, id=now)
                    return
                # Whichever regions the page's *shape* has, which `settling` decides for both sides:
                # a page drawing the settings step has no transcript and no message box on it, and
                # one past the step has no step. That is the shape and not the checkpoint - a branch
                # has turns and still draws the step - so it is read from the same predicate the
                # page is built with rather than from what the session holds. Sending both sets
                # would name a target that is not there on either page, which is the case the check
                # above exists for.
                #
                # Settled is two regions on one connection, which is what `partial` exists for. The
                # cache note lives in the composer rather than in the transcript, so nothing else
                # replaces it, and what it says goes stale on every turn: the context it prices
                # grows, and when the prefix was last written moves. `outerHTML` rather than the
                # transcript's morph, because it is one short line with nothing in it worth
                # preserving across a swap.
                #
                # Settling is the one region there is, and it is the only thing that makes the step
                # resolve: the registration lands in a worker, so a page that did not watch for it
                # would spin until somebody reloaded.
                regions.extend(
                    [partial(SETUP_ID, "outerHTML", setup_step(links, showing))]
                    if settling(showing)
                    else [
                        partial(TRANSCRIPT_ID, SWAP, transcript_region(links, reader, showing)),
                        partial(CACHE_ID, "outerHTML", cache_note(showing, reader)),
                    ]
                )
            seen = now
            # The list on every page, morphed like the transcript because a row's archive disclosure
            # may be open under somebody's pointer when another session moves the list.
            regions.append(
                partial(
                    LISTED_ID, SWAP, listed_region(links, reader, await service.listed(), session, service.reachable)
                )
            )
            yield Event(data=render(regions), id=now)
        await asyncio.sleep(every.total_seconds())
