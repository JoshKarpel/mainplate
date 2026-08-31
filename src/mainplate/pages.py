# Every page and fragment the console renders, as node trees.
#
# Pure functions of already-answered questions: nothing here reads a store, and nothing here
# knows what a `Run` is. `console` gathers what a page needs and calls one of these, which is
# what lets a page and the fragment inside it be the *same* function called at two depths rather
# than two renderings of one thing that can disagree.
#
# One live region, and it is the transcript. A turn in flight is the only thing on this console
# that changes without somebody doing anything, so it is the only thing that asks again: the
# transcript replaces itself every second while it is waiting on an answer, and carries no poll
# at all once it has one. A console with nothing running makes no requests.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from without_html import DOCTYPE
from without_html import Element
from without_html import Node
from without_html import a
from without_html import article
from without_html import aside
from without_html import body
from without_html import button
from without_html import div
from without_html import form
from without_html import h1
from without_html import head
from without_html import header
from without_html import html
from without_html import li
from without_html import link
from without_html import main
from without_html import meta
from without_html import p
from without_html import render
from without_html import script
from without_html import span
from without_html import textarea
from without_html import time
from without_html import title
from without_html import ul
from without_web import Reversible
from without_web import url_for

from mainplate.conversation import Exchange
from mainplate.conversation import Transcript
from mainplate.service import Conversation
from mainplate.sessions import Session

# How often a transcript with an unanswered turn asks again. A person is watching for a reply
# that takes seconds, so this is short enough to feel like an answer arriving rather than a page
# refreshing. It costs nothing when nothing is pending, because a settled transcript carries no
# trigger at all.
WAITING = "load delay:1s"

# What every swap of the transcript does, which is to replace the region whole and put the reader
# at the newest thing in it. `scroll:bottom` on the poll as well as on the send, because the
# answer arriving is exactly the moment the column grows and the reader is looking at the wrong
# part of it. The cost is that scrolling up to reread something while a reply is in flight is
# undone on the next poll, which is a second or so; a wait long enough for that to matter is the
# thing to fix rather than the scroll.
SWAP: Final = "outerHTML scroll:bottom"

TRANSCRIPT_ID: Final = "transcript"

# What a session is called before anyone has said anything in it, and what the tab says on the
# page where a session does not exist yet.
UNTITLED: Final = "New chat"


@dataclass(frozen=True, slots=True)
class Links:
    """
    Every route the console links to, held as the route values themselves.

    Routes rather than paths, so a link is `url_for` over the same value that serves the request
    and no path is spelled twice. Filled in by `console`, which is where the routes are declared,
    and passed rather than imported, which is what keeps this module out of a cycle with that one.
    """

    home: Reversible
    start: Reversible
    session: Reversible
    say: Reversible
    session_fragment: Reversible
    # A prefix rather than a route, and the one exception: the route serving the assets needs an
    # inventory that does not exist until startup, where every field above is a module-level
    # value. Both are built from one constant, so they cannot disagree about where they are.
    assets: str

    def to_home(self) -> str:
        return url_for(self.home)

    def to_start(self) -> str:
        return url_for(self.start)

    def to_session(self, session: str) -> str:
        return url_for(self.session, {"session": session})

    def to_say(self, session: str) -> str:
        return url_for(self.say, {"session": session})

    def to_session_fragment(self, session: str) -> str:
        return url_for(self.session_fragment, {"session": session})

    def to_asset(self, name: str) -> str:
        return f"{self.assets}/{name}"


def document(links: Links, heading: str, children: Node) -> str:
    """
    The whole document, which every page is this with something different in the middle.

    The stylesheet and htmx are served from this process rather than from a CDN. The reason that
    matters most here is the last one anybody thinks of: a coding agent is pointed at a
    repository somewhere private, and a console that needs a third-party origin to render is a
    console that does not render there. The others are that a CDN sees every page anybody opens,
    and that a script fetched at page load is a dependency nothing in this repository pins.

    htmx is a plain blocking script tag, which is what the library asks for: `defer`,
    `type="module"`, and injecting it over AJAX are all documented as unreliable, and the failure
    is a page where no attribute does anything.
    """
    return render(
        [
            DOCTYPE,
            html(
                attrs={"lang": "en"},
                children=[
                    head(
                        children=[
                            meta(attrs={"charset": "utf-8"}),
                            meta(attrs={"name": "viewport", "content": "width=device-width, initial-scale=1"}),
                            title(children=heading),
                            link(attrs={"rel": "icon", "href": "data:,"}),
                            link(attrs={"rel": "stylesheet", "href": links.to_asset("mainplate.css")}),
                            script(attrs={"src": links.to_asset("htmx.min.js")}),
                        ]
                    ),
                    body(children=children),
                ],
            ),
        ]
    )


def sidebar(links: Links, listed: tuple[Session, ...], showing: str | None) -> Element:
    """Every session, newest first, with the one being read marked."""
    return aside(
        cls="sessions",
        children=[
            a(cls="start", attrs={"href": links.to_home()}, children=UNTITLED),
            ul(
                children=[
                    li(
                        children=a(
                            cls=("session", "current" if session.id == showing else None),
                            attrs={"href": links.to_session(session.id)},
                            children=[
                                span(cls="name", children=session.title or UNTITLED),
                                time(
                                    cls="when",
                                    attrs={"datetime": session.created_at.isoformat()},
                                    children=session.created_at.strftime("%b %d, %H:%M"),
                                ),
                            ],
                        )
                    )
                    for session in listed
                ]
            ),
        ],
    )


def bubble(who: str, said: str) -> Element:
    """
    One thing somebody said.

    The text is a child, so it is escaped: what the model wrote is not markup, and rendering it
    as markup would let a model that echoed a prompt back put a script on this page. Turning it
    into Markdown is a later decision, and it is the one that has to be made carefully rather
    than by default.
    """
    return article(cls=("said", who), children=p(cls="text", children=said))


def waiting_bubble() -> Element:
    return article(cls=("said", "assistant", "waiting"), children=p(cls="text", children="\N{HORIZONTAL ELLIPSIS}"))


def exchange(said: Exchange) -> tuple[Element, Element]:
    return bubble("person", said.prompt), bubble("assistant", said.reply)


def transcript_region(links: Links, session: str, said: Transcript) -> Element:
    """
    The conversation, and whether it is still asking for the rest of it.

    The trigger is on the region itself and the swap is `outerHTML`, so an answer replaces this
    element attributes and all: a transcript that has been answered comes back carrying no
    trigger, which is how the polling stops. Nothing has to be told to stop it.

    A refusal or a fault is refused a swap. htmx 4 swaps every status but `204` and `304`, so
    what a poll does with an error is this page's decision rather than the library's default, and
    the right one is to leave the last good render on screen and ask again: the answer after a
    hiccup is usually a real one, where swapping would replace the conversation with an error and
    take the trigger that would have recovered it away at the same time.
    """
    polling = (
        {
            "hx-get": links.to_session_fragment(session),
            "hx-trigger": WAITING,
            "hx-swap": SWAP,
            "hx-status:4xx": "swap:none",
            "hx-status:5xx": "swap:none",
        }
        if said.pending
        else {}
    )
    spoken: list[Element] = []
    for each in said.exchanges:
        spoken.extend(exchange(each))
    # One waiting bubble for however many messages are outstanding, because one reply is what is
    # actually being written: the turns behind it are queued, not in flight.
    spoken.extend(bubble("person", each) for each in said.pending)
    if said.pending:
        spoken.append(waiting_bubble())
    return div(
        cls="transcript",
        attrs={"id": TRANSCRIPT_ID, **polling},
        children=spoken or p(cls="empty", children="Ask it something."),
    )


def composer(action: str, *, live: bool) -> Element:
    """
    The box you type in, which posts to `action` and is the same control on both pages.

    `live` is what differs, and it is not styling: sending into a session that already exists
    swaps the transcript and leaves the address bar alone, while sending the first message
    *creates* a session and has to end up at that session's own URL. htmx cannot do the second
    one, because a redirect's headers never reach it, so the first message is an ordinary form
    post and the browser follows the `303` itself.

    The reset is on `after:swap` rather than on `after:request`, so the box empties when the
    conversation on screen has actually taken the message rather than when the request left.
    """
    driving = (
        {
            "hx-post": action,
            "hx-target": f"#{TRANSCRIPT_ID}",
            "hx-swap": SWAP,
            "hx-on:htmx:after:swap": "this.reset()",
            "hx-disable": "find button, find textarea",
            # A refusal is not a transcript, so it must not become one. The box is `required`, so
            # the only way to reach this is a caller that is not this page; leaving the
            # conversation on screen is the honest answer to that.
            "hx-status:4xx": "swap:none",
            "hx-status:5xx": "swap:none",
        }
        if live
        else {}
    )
    return form(
        cls="composer",
        attrs={"method": "post", "action": action, **driving},
        children=[
            textarea(
                attrs={
                    "name": "prompt",
                    "rows": 3,
                    "required": True,
                    "autofocus": True,
                    "placeholder": "Say something",
                    "aria-label": "Message",
                }
            ),
            button(attrs={"type": "submit"}, children="Send"),
        ],
    )


def shell(links: Links, listed: tuple[Session, ...], showing: str | None, pane: Sequence[Element]) -> Element:
    return div(
        cls="shell",
        children=[
            sidebar(links, listed, showing),
            main(children=[header(children=h1(children="mainplate")), *pane]),
        ],
    )


def start_page(links: Links, listed: tuple[Session, ...]) -> str:
    """
    Where a session begins: an empty transcript and a box, with no session behind it yet.

    Nothing is created until something is said, which is why this page has no id in its URL. A
    session that existed with nothing in it would be a row in the list nobody can name and nobody
    asked for.
    """
    return document(
        links,
        UNTITLED,
        shell(
            links,
            listed,
            showing=None,
            pane=[
                transcript_region(links, session="", said=Transcript(exchanges=(), pending=())),
                composer(links.to_start(), live=False),
            ],
        ),
    )


def session_page(links: Links, listed: tuple[Session, ...], showing: Conversation) -> str:
    return document(
        links,
        showing.session.title or UNTITLED,
        shell(
            links,
            listed,
            showing=showing.session.id,
            pane=[
                transcript_region(links, showing.session.id, showing.said),
                composer(links.to_say(showing.session.id), live=True),
            ],
        ),
    )


def refusal_page(links: Links, status: int, why: str) -> str:
    """
    A page for a request nothing could answer, with a way back on it.

    A way back is the whole point of answering a browser with a page rather than a sentence:
    somebody who followed a stale link should be one click from the console rather than looking
    at a bare status code.
    """
    return document(
        links,
        f"{status}",
        div(
            cls="refusal",
            children=[
                h1(children=str(status)),
                p(children=why),
                a(attrs={"href": links.to_home()}, children="Back to mainplate"),
            ],
        ),
    )


def fragment(element: Element) -> str:
    """One element and no document, which is what a swap wants."""
    return render(element)
