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
from without_html import option
from without_html import p
from without_html import render
from without_html import script
from without_html import select
from without_html import span
from without_html import textarea
from without_html import time
from without_html import title
from without_html import ul
from without_web import Reversible
from without_web import url_for

from mainplate.agent import Choice
from mainplate.conversation import Exchange
from mainplate.conversation import Transcript
from mainplate.profiles import Config
from mainplate.profiles import Profile
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

MODEL_ID: Final = "model"

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
    profile_models: Reversible
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

    def to_profile_models(self) -> str:
        """
        The model select, asked for with a profile in the query string.

        A query parameter rather than a path segment, because the profile is *the value of the
        select that asks*: htmx sends a triggering input's own value, so this URL needs no
        interpolation and the select needs no script to build one.
        """
        return url_for(self.profile_models)

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


def model_select(profile: Profile, chosen: str | None = None) -> Element:
    """
    The models one profile offers, as the select the form submits.

    Its own element with a stable id, because changing the profile replaces exactly this and
    nothing else on the page. A profile always offers at least one model (the parser refuses an
    empty list), so this is never an empty select nobody can submit.
    """
    picked = chosen if chosen in profile.models else profile.models[0]
    return select(
        attrs={"id": MODEL_ID, "name": "model", "aria-label": "Model"},
        children=[
            option(attrs={"value": model, "selected": model == picked}, children=model) for model in profile.models
        ],
    )


def profile_select(links: Links, config: Config) -> Element:
    """
    Which endpoint to answer on, and the control that swaps the model list beside it.

    htmx sends a triggering input's own value, so the `hx-get` needs no interpolation: choosing a
    profile asks for that profile's models and replaces the select next to this one. Without a
    browser the form still posts, carrying whatever models the page was rendered with, and the
    handler refuses a pair no profile offers.
    """
    return select(
        attrs={
            "name": "profile",
            "aria-label": "Profile",
            "hx-get": links.to_profile_models(),
            "hx-target": f"#{MODEL_ID}",
            "hx-swap": "outerHTML",
            "hx-status:4xx": "swap:none",
            "hx-status:5xx": "swap:none",
        },
        children=[
            option(attrs={"value": name, "selected": name == config.default}, children=name)
            for name in sorted(config.profiles)
        ],
    )


def picker(links: Links, config: Config) -> Element:
    """The two selects, which appear only where a session is being created."""
    return div(
        cls="picker",
        children=[
            span(cls="label", children="Answer with"),
            profile_select(links, config),
            model_select(config.profiles[config.default]),
        ],
    )


def chosen_note(chosen: Choice | None) -> Element:
    """What an existing session is on, as a fact rather than a control: it cannot be changed."""
    if chosen is None:
        return span(cls="picker")
    return span(cls=("picker", "settled"), children=f"{chosen.profile} \N{MIDDLE DOT} {chosen.model}")


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


def transcript_region(links: Links, session: str, said: Transcript, stalled: str | None = None) -> Element:
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
        if said.pending and stalled is None
        else {}
    )
    spoken: list[Element] = []
    for each in said.exchanges:
        spoken.extend(exchange(each))
    # One waiting bubble for however many messages are outstanding, because one reply is what is
    # actually being written: the turns behind it are queued, not in flight.
    spoken.extend(bubble("person", each) for each in said.pending)
    if said.pending and stalled is None:
        spoken.append(waiting_bubble())
    if stalled is not None:
        spoken.append(p(cls="stalled", children=stalled))
    return div(
        cls="transcript",
        attrs={"id": TRANSCRIPT_ID, **polling},
        children=spoken or p(cls="empty", children="Ask it something."),
    )


def composer(action: str, beneath: Element, *, live: bool, refusing: bool = False) -> Element:
    """
    The box you type in, which posts to `action`, with `beneath` under it.

    `live` is what differs between the two pages, and it is not styling: sending into a session
    that already exists swaps the transcript and leaves the address bar alone, while sending the
    first message *creates* a session and has to end up at that session's own URL. htmx cannot do
    the second one, because a redirect's headers never reach it, so the first message is an
    ordinary form post and the browser follows the `303` itself.

    `beneath` is the picker on one page and a note saying what a session is already on for the
    other, because a session's choice is fixed for its life and offering a control that could not
    change it would be a lie about what the page does.

    `refusing` disables the whole thing, for a session nothing can answer. The disabling is real
    rather than styling: a box that still submitted would record a message into a session whose
    profile is gone, which is one more thing to explain and nothing gained.

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
            div(
                cls="row",
                children=[
                    textarea(
                        attrs={
                            "name": "prompt",
                            "rows": 3,
                            "required": True,
                            "autofocus": not refusing,
                            "disabled": refusing,
                            "placeholder": "Say something",
                            "aria-label": "Message",
                        }
                    ),
                    button(attrs={"type": "submit", "disabled": refusing}, children="Send"),
                ],
            ),
            beneath,
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


def start_page(links: Links, listed: tuple[Session, ...], config: Config) -> str:
    """
    Where a session begins: an empty transcript, a box, and what to answer it with.

    Nothing is created until something is said, which is why this page has no id in its URL. A
    session that existed with nothing in it would be a row in the list nobody can name and nobody
    asked for, and it would have to be recorded on a profile chosen for it rather than by anybody.
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
                composer(links.to_start(), picker(links, config), live=False),
            ],
        ),
    )


def stalled_by(showing: Conversation) -> str | None:
    """
    Why this session cannot be answered, or nothing at all when it can.

    One sentence naming the profile, because that is the only thing a person can act on: the pair
    was configured when the session started, so putting it back in the configuration file is what
    makes the conversation continue exactly where it stopped.
    """
    if showing.answerable or showing.chosen is None:
        return None
    return (
        f"This session was started on profile {showing.chosen.profile!r} with {showing.chosen.model!r}, "
        f"which the configuration no longer offers. Put it back to carry on, or start a new session."
    )


def session_page(links: Links, listed: tuple[Session, ...], showing: Conversation) -> str:
    stalled = stalled_by(showing)
    return document(
        links,
        showing.session.title or UNTITLED,
        shell(
            links,
            listed,
            showing=showing.session.id,
            pane=[
                transcript_region(links, showing.session.id, showing.said, stalled),
                composer(
                    links.to_say(showing.session.id),
                    chosen_note(showing.chosen),
                    live=True,
                    refusing=stalled is not None,
                ),
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
