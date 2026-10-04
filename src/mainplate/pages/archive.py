# Archiving a session, and deleting one that is archived: the card saying what each press takes, and
# the presses themselves. A module of its own because two parts of the page draw it, the rail's card
# about the session and the session list's row, and neither should import the other to reach it.

from __future__ import annotations

from datetime import datetime
from typing import Final

from without_html import Element
from without_html import button
from without_html import details
from without_html import div
from without_html import dl
from without_html import form
from without_html import p
from without_html import summary

from mainplate.pages.document import Links
from mainplate.pages.document import fact
from mainplate.pages.moments import Reader
from mainplate.pages.moments import dated
from mainplate.pages.moments import stamped


def archive_card(links: Links, reader: Reader, session: str, archived: datetime | None) -> Element:
    """
    The one control that closes a session, or the fact that somebody already did.

    Behind a disclosure rather than a bare button, because the press takes a checkout off the disk
    and a mis-press on a card in a rail is one thing this console can make impossible for the price
    of one more click: what opens says what the press does, and the button is under that sentence.
    A plain form answered with a redirect, so it works with the script absent and lands on the page as
    it now is.

    Once pressed, the card is the fact: when, as one row in the shape the card of facts above it
    draws. What became of the files is the card's title rather than a paragraph under the head,
    because the sentence was written for the disclosure, where it is a warning before a press; after
    the fact it is eight lines of prose in a column of controls, and the transcript already ends in
    the sentence saying why. The way back is the fork on the rule under the last turn, which is
    where every fork lives, so it is not repeated here.

    The same control stands on every live row of the session list, as `archive_action`, so a session
    can be closed without being opened first; the two share `archive_press`, which is the one form.

    **An archived card carries the press that deletes the session**, as the same disclosure over the
    same kind of sentence: it is the next act on a closed session and the only one left, and the card
    is already where the session's closing is said. Only here and not on an archived row of the list,
    so deleting a conversation takes opening it first, and the last thing somebody sees before the
    press is what they are about to lose.
    """
    if archived is not None:
        return div(
            cls="archive",
            attrs={
                "title": (
                    "Its checkout and scratch are taken off the disk; the conversation stays, and forking it "
                    "from the end carries on."
                )
            },
            children=[
                div(cls="archive__head", children="archived"),
                dl(cls="facts", children=[*fact("since", dated(archived, reader), title=stamped(archived, reader))]),
                details(
                    cls="archive",
                    children=[
                        summary(cls="archive__head", children="Delete"),
                        p(cls="archive__says", children=DELETING_TAKES),
                        form(
                            cls="archive__press",
                            attrs={"method": "post", "action": links.to_delete(session)},
                            children=button(cls="archive__set", attrs={"type": "submit"}, children="Delete"),
                        ),
                    ],
                ),
            ],
        )
    return details(
        cls="archive",
        children=[
            summary(cls="archive__head", children="Archive"),
            p(cls="archive__says", children=ARCHIVING_TAKES),
            archive_press(links, session),
        ],
    )


ARCHIVING_TAKES: Final = (
    "Takes its checkout, scratch and plugins off the disk and stops anything more being said in it. "
    "The conversation stays, and a fork of it carries on."
)
"""What the press does, said once for the card in the rail and the action on a row."""

DELETING_TAKES: Final = (
    "Takes the conversation and everything recorded for it out of the database, for good; nothing forks "
    "from it again. Its artifacts and any forks of it stay."
)
"""What deleting takes, and the two things it leaves, which are the two a reader would ask after."""


def archive_press(links: Links, session: str) -> Element:
    """The one form that closes a session, wherever the disclosure holding it is drawn."""
    return form(
        cls="archive__press",
        attrs={"method": "post", "action": links.to_archive(session)},
        children=button(cls="archive__set", attrs={"type": "submit"}, children="Archive"),
    )


def archive_action(links: Links, session: str) -> Element:
    """
    Archiving, on a row of the session list, for closing a session without opening it first.

    **Revealed on hover and on focus, and laid over the row rather than added to it.** A row action
    that took a line of its own would move every row beneath it as the pointer passed down the list,
    so the summary sits over the row's corner and costs the list nothing until it is opened; opened,
    the disclosure grows under the row, which is a layout change somebody asked for. Hidden by
    visibility rather than left visible and faint, because a control on every row of a list is a
    list of controls, and what the list is for is telling sessions apart.

    A sibling of the row's link rather than inside it, because a form inside an anchor is not markup
    a browser will keep together, and the summary is what the keyboard reaches: the row is in
    `:focus-within` while it is, so a reader tabbing through the list meets the action on each row.

    The same disclosure as the rail's card, behind the same sentence, posting the same form: the
    press takes a checkout off the disk, and a bare button in a list is exactly the mis-press that
    disclosure exists to make impossible. The redirect lands on the archived session, which is the
    page saying what just happened; a list that stayed put would say nothing.

    Not drawn on the strip a phone folds the list into, since a chip has no corner to lay it over
    and nothing hovers there; a session is opened and closed from its rail instead.
    """
    return details(
        cls="session__archive",
        children=[
            summary(
                cls="session__archive-head",
                attrs={"title": "Archive this session", "aria-label": "Archive this session"},
                children="archive",
            ),
            p(cls="archive__says", children=ARCHIVING_TAKES),
            archive_press(links, session),
        ],
    )
