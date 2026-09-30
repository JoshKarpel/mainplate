# The frame every page but a refusal is drawn in: the session list down the side, the bar a phone
# gets across the top, and the pane between. The list is a region the live connection redraws on
# every page, which is why a row is drawn here once and the dashboard borrows it.

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence
from typing import Final
from typing import assert_never

from without_html import Element
from without_html import a
from without_html import aside
from without_html import button
from without_html import div
from without_html import element
from without_html import header
from without_html import li
from without_html import main
from without_html import span
from without_html import svg
from without_html import ul

from mainplate.forge import Reachable
from mainplate.pages.archive import archive_action
from mainplate.pages.document import DASHBOARD
from mainplate.pages.document import UNTITLED
from mainplate.pages.document import Links
from mainplate.pages.figures import footprint_note
from mainplate.pages.figures import sized
from mainplate.pages.moments import Reader
from mainplate.pages.moments import dated
from mainplate.pages.moments import moments
from mainplate.pages.moments import stamped
from mainplate.pages.moments import when_element
from mainplate.sessions import Attention
from mainplate.sessions import Claimed
from mainplate.sessions import Delayed
from mainplate.sessions import Idle
from mainplate.sessions import Queued
from mainplate.sessions import Session

# The session list itself, which is the region the live connection redraws: the `<ul>` and not the
# whole sidebar, so a list slid out on a phone is not snapped shut by its own redraw, since what holds
# it open is an attribute the script put on the sidebar and a morph of the sidebar would take it off.
LISTED_ID: Final = "listed"


# The word a session with something recorded since anybody looked is marked with, on a row in the
# list, on a row on a dashboard card, and as the heading over the dashboard's group of them. One
# word in all three, because they are one fact; `Session.unseen` is the same fact in the code.
UNREAD: Final = "unread"


# How far a branch is indented from the conversation it came from. Bounded, because the depth of a
# tree is not something a sidebar seventeen rems wide can keep spending on: past this a branch of a
# branch is drawn beside its parent rather than under it, and its own row still says where it came
# from.
DEEPEST: Final = 3


def arrange(listed: Sequence[Session]) -> tuple[tuple[Session, int], ...]:
    """
    Every session with how deep in the tree it sits, a branch directly under what it branched from.

    Pure, and separate from the rendering, because it is the one piece of real reasoning in the
    sidebar: the flat list the index hands back says nothing about shape, and the shape is the
    whole reason forking is worth having a picture of.

    The order is the index's to give, and this keeps it rather than sorting again: every active
    session above every archived one and each group most recently written to first, which
    `read_sessions` says why of. So a branch sits under its parent only where the two are in the same
    group. An active branch whose parent is archived or absent is a root among active sessions, and
    an archived branch whose parent is active or absent is a root among archived ones; forking from
    the end of an archived session always produces the first. The cost, stated: nothing on the list
    then says which session such a branch came from, since its row names only the turn it left at.

    Drawn as a root rather than left out, which is not a fallback but the honest reading: hiding a
    session because its parent went missing, or went into the other group, would lose a conversation
    somebody can still read.
    """
    known = {session.id: session for session in listed}
    children: dict[str | None, list[Session]] = {}
    for session in listed:
        origin = known.get(session.forked.session) if session.forked else None
        parent = origin.id if origin is not None and (origin.archived is None) == (session.archived is None) else None
        children.setdefault(parent, []).append(session)

    arranged: list[tuple[Session, int]] = []

    def walk(parent: str | None, depth: int) -> None:
        for session in children.get(parent, ()):
            arranged.append((session, depth))
            walk(session.id, min(depth + 1, DEEPEST))

    walk(None, 0)
    return tuple(arranged)


def sidebar(
    links: Links, reader: Reader, listed: tuple[Session, ...], showing: str | None, reachable: Reachable
) -> Element:
    """
    Every session, active ones above archived ones and each group the one most recently written to
    first, with branches under what they branched from and the current one marked. Which group a
    session is in is `read_sessions`'s to say and where a branch goes is `arrange`'s.

    Most recent first among siblings rather than across the whole list, which is what a tree costs
    and what it buys: a branch worked in this morning sits with the conversation it came from rather
    than at the top away from it, and the ordering among any one set of siblings is still the one a
    chat console reads in. The row dates itself by the same moment it is ordered by, since a list sorted by
    one date and labelled with another reads as unsorted.

    A row says which repository its session works in, because that is the thing two conversations
    with the same opening line are actually told apart by once a console is used to work in more
    than one. `reachable` is what turns the recorded id into the name somebody recognises, and a
    session working in nothing says nothing rather than saying so - most of a list is one or the
    other, and the majority does not need labelling.

    Every row of a session still open carries `archive_action`, so a list that has grown can be
    closed down from the list. An archived row carries nothing: the press is write-once, and a
    control that would do it again is a control that does nothing.

    The clasp comes first for the rail's reason: on a phone the list lies off the left edge of the
    page and this is left where its head was, so the sheet can slide out from under it. Which width
    that is stays the stylesheet's to say, and everywhere wider it is not drawn at all. It says its
    word rather than a glyph, because it stands in a row the page clears for it anyway.

    The button that puts the list away on a wide window is not drawn here at all: the script seats
    it, since a button that does nothing without the script is a control that lies.

    Everything but the clasp is in one sheet, which is the thing that slides: one box with a ground
    of its own, so that on a phone a finger between two rows lands on the list and scrolls it rather
    than falling through to the conversation underneath. On a wide window the sheet is simply the
    column, and it is the column that scrolls at every width.

    The list inside the sheet is `listed_region`, because it is the part the live connection redraws;
    see `LISTED_ID` for why the redraw stops there.
    """
    return aside(
        cls="sessions",
        attrs={"aria-label": "Sessions"},
        children=[
            button(
                cls="sessions__clasp",
                attrs={"type": "button", "aria-expanded": "false"},
                children="Sessions",
            ),
            div(
                cls="sessions__sheet",
                children=[
                    # The console's mark and name, as the way back to the dashboard: a link that reads
                    # as where you are rather than a button that reads as something to do, since the
                    # presses that start something are on the dashboard's cards.
                    a(
                        cls="home",
                        attrs={"href": links.to_home()},
                        children=[
                            # Drawn by reference rather than as an `<img>`, so the stylesheet can
                            # hand the plate the theme's colours: an image only ever sees the OS's.
                            svg(
                                cls="home__mark",
                                attrs={"viewBox": "0 0 512 512", "aria-hidden": "true"},
                                children=element("use", attrs={"href": f"{links.to_asset('icon.svg')}#plate"}),
                            ),
                            span(cls="home__name", children=DASHBOARD),
                        ],
                    ),
                    listed_region(links, reader, listed, showing, reachable),
                ],
            ),
        ],
    )


def listed_region(
    links: Links, reader: Reader, listed: tuple[Session, ...], showing: str | None, reachable: Reachable
) -> Element:
    """
    The rows of the session list, which is what the live connection sends when any session moves.

    Every row carries an id, and that is for the morph rather than for anybody to link to: the list
    reorders when a session is written to and a row moves up it, and a morph that cannot tell a moved
    row from a changed one rebuilds it, shutting an archive disclosure somebody had open on it.
    """
    return ul(
        attrs={"id": LISTED_ID},
        children=[
            li(
                attrs={"id": f"listed-{session.id}", "data-depth": str(depth)},
                children=[
                    session_row(links, reader, session, showing, depth, reachable),
                    *((archive_action(links, session.id),) if session.archived is None else ()),
                ],
            )
            for session, depth in arrange(listed)
        ],
    )


def session_row(
    links: Links, reader: Reader, session: Session, showing: str | None, depth: int, reachable: Reachable
) -> Element:
    """One session as the link to it, which is the row apart from the action laid over it."""
    return a(
        cls=(
            "session",
            "current" if session.id == showing else None,
            "forked" if depth else None,
            "archived" if session.archived is not None else None,
        ),
        attrs={"href": links.to_session(session.id)},
        children=[
            span(cls="name", children=session.title or UNTITLED),
            span(
                cls="meta",
                children=[
                    # Something recorded since anybody looked, said in a word beside the
                    # date for the archived word's reason: a dot on its own reads as a
                    # styling accident, and the word is what a reader scanning for
                    # something to catch up on looks for. Never on an archived row,
                    # since nothing more is said in one, and never on the row being
                    # looked at, because the page drawing it is what marks it seen.
                    *(
                        (
                            span(
                                cls="unseen",
                                attrs={"title": "Something new since you last looked"},
                                children=UNREAD,
                            ),
                        )
                        if session.unseen and session.archived is None
                        else ()
                    ),
                    # Whether something is still coming, which is the other reason to open a
                    # row: `unread` says the session has answered since anybody looked, and this
                    # says a pass is answering it or is going to. Never on an archived row, for the
                    # reason `unread` is not.
                    *(working_mark(session.attention) if session.archived is None else ()),
                    when_element(
                        session.latest,
                        cls="when",
                        title=moments(session, reader),
                        said=dated(session.latest, reader),
                    ),
                    # Said in a word as well as by muting the name, because a
                    # muted row on its own reads as a styling accident, and the
                    # word is what a reader scanning for a closed session looks
                    # for.
                    *(
                        (
                            span(
                                cls="archived",
                                attrs={"title": f"Archived {stamped(session.archived, reader)}"},
                                children="archived",
                            ),
                        )
                        if session.archived is not None
                        else ()
                    ),
                    # Where it left its parent, and whether it left meaning to
                    # come back. The glyph carries it rather than a word: a row
                    # this narrow has no room for one, and the two marks differ
                    # at a glance where "aside" and nothing would not.
                    *(
                        (
                            span(
                                cls=("from", "from--aside" if session.forked.aside else None),
                                attrs={
                                    "title": (
                                        f"An aside from turn {session.forked.turn}"
                                        if session.forked.aside
                                        else f"Forked at turn {session.forked.turn}"
                                    )
                                },
                                children=(
                                    f"\N{LEFTWARDS ARROW WITH HOOK}{session.forked.turn}"
                                    if session.forked.aside
                                    else f"\N{RIGHTWARDS ARROW}{session.forked.turn}"
                                ),
                            ),
                        )
                        if session.forked
                        else ()
                    ),
                    # What it takes on disk, drawn only once something has been
                    # measured and something is there: a session that has not
                    # worked yet holds nothing, and nothing is not worth a
                    # figure, for the reason a session in no repository says
                    # nothing about where it works.
                    *(
                        (
                            span(
                                cls="footprint",
                                attrs={"title": footprint_note(session.footprint, reader)},
                                children=sized(session.footprint.allocated),
                            ),
                        )
                        if session.footprint is not None and session.footprint.allocated
                        else ()
                    ),
                    *(
                        (
                            span(
                                cls="where",
                                # Titled as well as drawn, because a row this
                                # narrow cuts a name that two sessions may
                                # differ only in the tail of.
                                attrs={"title": reachable.readable(session.repository)},
                                children=reachable.readable(session.repository),
                            ),
                        )
                        if session.repository is not None
                        else ()
                    ),
                ],
            ),
        ],
    )


def working_mark(attention: Attention | None) -> tuple[Element, ...]:
    """
    The word a row says while the worker has something to do about its session, or nothing.

    One word for three arms, since what a reader scanning the list wants to know is whether to
    expect more, and the title says which: a pass answering now, a delivery the next pass will
    take, or one held back after a pass fell over. `Idle` and unread alike draw nothing, because a
    settled session is the ordinary row and the word is for the ones that are not.
    """
    match attention:
        case Claimed():
            title = "A pass is answering this session now"
        case Queued():
            title = "Queued, and the next pass will take it"
        case Delayed():
            title = "Held back after a failed pass, and will be tried again"
        case Idle() | None:
            return ()
        case _ as unreachable:
            assert_never(unreachable)
    return (span(cls="working", attrs={"title": title}, children="working"),)


def shell(
    links: Links,
    reader: Reader,
    listed: tuple[Session, ...],
    showing: str | None,
    reachable: Reachable,
    pane: Sequence[Element],
    aside_rail: Iterable[Element] = (),
) -> Element:
    return div(
        cls="shell",
        children=[
            sidebar(links, reader, listed, showing, reachable),
            # No banner over the pane on a wide window, and that is room rather than an omission:
            # the console's own name was a row on every page saying nothing the tab title does not.
            # What names the page is `<title>`, and what gets somebody back to the start is the
            # session list, which is always on screen there. A phone is the exception, and only
            # because the row is already spent: the two clasps stand in a band across the top of
            # the page that everything else starts under, so the name stands between them at no
            # cost. The stylesheet draws this nowhere else.
            header(cls="bar", children=[a(cls="brand", attrs={"href": links.to_home()}, children="mainplate")]),
            main(children=[*pane]),
            *aside_rail,
        ],
    )
