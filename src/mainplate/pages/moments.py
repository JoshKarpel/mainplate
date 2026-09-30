# Whose clock a moment is printed against, and how it is printed.
#
# `Reader` is the answer to the first question, settled once per request, and everything else here
# turns a recorded instant or a duration into the words a page shows. The format is canonical at
# every reader and only the zone varies, which is why none of this takes a locale.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Final
from zoneinfo import ZoneInfo

from without_html import Element
from without_html import time

from mainplate.sessions import Session


@dataclass(frozen=True, slots=True)
class Reader:
    """
    What this browser has said about the person reading, as one value every page is handed.

    **Whose clock, and in time whose conventions.** Every moment this console shows is recorded in
    UTC and printed in somebody's local time, and the question each page function has to answer is
    whose. This is that answer, settled once per request by `reader_in` and threaded down.

    One value rather than a parameter apiece, and the reason is the threading rather than any
    second field: the clock reached two dozen page signatures as a parameter of its own, so the next
    reader-scoped answer would touch all of them again. A field here costs one line.

    **`locale` is deliberately not one of them, and will not become one.** The same
    `resolvedOptions()` this takes `timeZone` out of names a `locale` and an `hourCycle` beside it,
    and both are declined: every moment on this console is printed `%Y-%m-%d %H:%M`, at everybody,
    because this is a console for programmers and an unambiguous stamp reads the same to all of
    them. That closes the axis rather than leaving it open - which is also what keeps `strftime`
    sufficient, since a genuinely locale-aware render would need CLDR data through `babel`, the
    `locale` module being process-global and so no use per request.

    **What does not belong here is anything that is not a rendering input.** It shares a `Cookie`
    header with whatever else this console ever sets, and that is not a reason to carry the rest: a
    credential is a gate in *front* of drawing a page rather than something a page draws with, so it
    never becomes a field no matter that it arrives alongside. That line is what keeps this a value
    and not a drawer.

    So the zone may well stay the only field, and that is fine: what it buys is that finding a
    second one is an edit here rather than an edit everywhere.
    """

    zone: ZoneInfo


def elapsed(took: timedelta) -> str:
    """
    How long something took, at the scale it actually happened on.

    Three widths rather than one, because what is timed here spans four orders of magnitude: a file
    read comes back in milliseconds and a build runs for minutes, and the one format that suits
    either writes the first as `0.0s` - which reads as free rather than as fast - or the second as
    `184.7s`, which a reader has to divide before it means anything.

    A second is the boundary because it is where the digit that matters moves: under one, the whole
    figure is in the milliseconds, and over it a tenth is the finest thing worth reporting about a
    round trip whose length nobody controls.

    **Five widths now rather than three**, and the two on the end are the wait a provider asks for
    rather than anything a turn does: a subscription's allowance resets days out, and `6623m 0s` is a
    figure a reader has to divide twice before it means anything. Each step drops the finest unit it
    had, which is the same rule the first three follow: nobody reading a four-day wait is counting
    its seconds.
    """
    seconds = took.total_seconds()
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes:.0f}m {rest:.0f}s"
    hours, spare = divmod(minutes, 60)
    if hours < 24:
        return f"{hours:.0f}h {spare:.0f}m"
    days, left = divmod(hours, 24)
    return f"{days:.0f}d {left:.0f}h"


def timed(when: datetime, reader: Reader) -> str:
    """
    The time of day a moment fell at, which is the shortest true thing a rule can say about one.

    No date, because a rule stands inside a conversation a reader is already reading down: the date
    is the same for almost every rule in one and the minute is the part that moves. The whole moment
    is in the title beside it, which is where a conversation that ran overnight is told apart.

    Already the canonical form, which is why it is the one of the three that never changed: a
    24-hour `HH:MM` is what ISO 8601 asks for and what a reader of this console expects.
    """
    return when.astimezone(reader.zone).strftime("%H:%M")


def dated(when: datetime, reader: Reader) -> str:
    """
    A moment in the form the sidebar dates a session in, so every date on a row agrees.

    The year is carried rather than trimmed, at three characters against `03-14 10:20`. One format
    everywhere is the whole point of choosing a canonical one, and a date that silently means "this
    year" is the ambiguity the choice exists to remove; a console holding a conversation from last
    December is exactly where it would bite.
    """
    return when.astimezone(reader.zone).strftime("%Y-%m-%d %H:%M")


def stamped(when: datetime, reader: Reader) -> str:
    """
    A moment whole, for a title: the date, the second, and the clock it is being read against.

    The zone is carried here and nowhere else, because a title is the one place with room for it.
    What it answers is the question a bare `09:32` cannot: whose nine thirty-two, the reader's or the
    console's, which on a page read from another country is the difference between two answers.

    **As an offset and not an abbreviation**, which is the one place the canonical form buys
    correctness rather than only consistency: `CST` is US Central and it is also China Standard, so
    the abbreviation answers "whose" with a value two readers resolve differently. `-05:00` cannot be
    read two ways.

    `isoformat` rather than a `strftime` pattern, because `%z` renders `-0500` without the colon and
    the separator is the half of the offset that makes it RFC 3339. What comes back is the same text
    as the `datetime` attribute `when_element` puts beside it, give or take the date-time separator.
    """
    return when.astimezone(reader.zone).isoformat(sep=" ", timespec="seconds")


def when_element(when: datetime, cls: str, title: str, said: str) -> Element:
    """
    One moment on the page, carrying the instant itself beside whatever was drawn from it.

    `<time>` rather than a span, and the `datetime` attribute is the point rather than the tag:
    the text is the reader's clock and the attribute is the moment, so anything reading this page
    back - a browser's own tooling, a reader copying a value at something else - gets what was
    recorded rather than a rendering of it.

    **It takes no `Reader`, because it converts nothing.** `dated`, `timed` and `stamped` are where a
    moment meets a clock, and every caller has already been through one by the time it gets here;
    taking one would say this element does the conversion, and a second conversion is the one thing
    this page cannot have two of.
    """
    return time(cls=cls, attrs={"datetime": when.isoformat(), "title": title}, children=said)


def moments(session: Session, reader: Reader) -> str:
    """
    Both of a row's moments, for the title over the one it prints.

    The row prints the one it is ordered by, and for a session written to that is not when it was
    made; somebody wondering which of two conversations is the older one hovers rather than guesses.
    """
    created = f"Created {stamped(session.created_at, reader)}"
    if session.last_said_at is None:
        return created
    return f"Last message {stamped(session.last_said_at, reader)}. {created}"


ZONE_COOKIE: Final = "zone"
"""
What the browser calls the clock its reader keeps, as an IANA name in a cookie.

A cookie rather than a header or a query parameter, because the question has to be answered on the
request for the *document*: a header the script adds reaches the swaps and not the page they land
in, and a page drawn against one clock that then swapped in regions drawn against another would be
a transcript disagreeing with itself down the column.

Named here beside the field the page writes back, because the two are one loop with the script in
the middle of it, and the third spelling is in `mainplate.js` where there is nowhere else it could
be. `TestTheClockAPageIsDrawnAgainst` is what fails when they drift.
"""


ZONE_FIELD: Final = "data-zone"
"""
Which clock this page's moments were actually drawn against, said on `<html>`.

The other half of the cookie: the script compares it with the browser's own zone, and a page drawn
against a different one is asked for again rather than rewritten in place. Writing it back is what
stops that being a loop, since a console with no record of the zone it was asked for renders in its
own and says so, and the script sees its request was not honoured rather than asking for ever.

On `<html>` because that is the one element parsed before the script's first block runs, which is
what lets the comparison happen before the wrong times are ever painted; see `document`.
"""
