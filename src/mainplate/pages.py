# Every page and fragment the console renders, as node trees.
#
# Pure functions of already-answered questions: nothing here reads a store, and nothing here
# knows what a `Run` is. `console` gathers what a page needs and calls one of these, which is
# what lets a page and the fragment inside it be the *same* function called at two depths rather
# than two renderings of one thing that can disagree.
#
# One live region, and it is the transcript. A turn in flight is the only thing on this console
# that changes without somebody doing anything, so it is the only thing that asks again: the
# transcript asks for itself once a second while it is waiting on an answer, and carries no poll
# at all once it has one. A console with nothing running makes no requests.
#
# The chrome that navigates the conversation (the search, the key, the dock) is deliberately
# *outside* that region, so a swap cannot take a control away mid-press and nothing has to be
# rebuilt a second later. What the chrome projects back onto the transcript (search marks, the
# panel a reader landed on, which kinds are set aside) is reapplied after each swap by the script,
# which holds that state as values rather than reading it back out of the markup.

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from typing import assert_never

from without_html import DOCTYPE
from without_html import Element
from without_html import Node
from without_html import a
from without_html import article
from without_html import aside
from without_html import body
from without_html import button
from without_html import dd
from without_html import details
from without_html import div
from without_html import dl
from without_html import dt
from without_html import form
from without_html import h1
from without_html import head
from without_html import header
from without_html import html
from without_html import input_
from without_html import li
from without_html import link
from without_html import main
from without_html import meta
from without_html import optgroup
from without_html import option
from without_html import p
from without_html import pre
from without_html import render
from without_html import script
from without_html import section
from without_html import select
from without_html import span
from without_html import summary
from without_html import textarea
from without_html import time
from without_html import title
from without_html import ul
from without_web import Reversible
from without_web import url_for

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import grouped
from mainplate.conversation import Block
from mainplate.conversation import Kind
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reasoning
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.markup import as_markup
from mainplate.service import Conversation
from mainplate.sessions import Session

# How often a transcript with an unanswered turn asks again. A person is watching for a reply
# that takes seconds, so this is short enough to feel like an answer arriving rather than a page
# refreshing. It costs nothing when nothing is pending, because a settled transcript carries no
# trigger at all.
#
# `every` and not `load delay:1s`, and the difference is not a preference. A `load` trigger fires
# once per element load, so it repeated only because each answer *replaced* the region and the
# replacement loaded. Morphing keeps the element, which is the whole point of it, so a `load` poll
# fires exactly once and a conversation waits forever on an answer that has already arrived. An
# interval belongs to the element rather than to its arrival, and htmx cancels it when the
# attribute goes, which is what a settled transcript comes back without.
WAITING = "every 1s"

# What every swap of the transcript does. `outerMorph` rather than `outerHTML`, because the server
# renders the whole conversation on every poll and a wholesale replacement would throw away
# everything a reader had done to it: a tool call they had unfolded, the search marks laid over it,
# the panel they had landed on, and the caret if it were ever in there. Morphing merges the new
# markup into the DOM already on screen, so a panel that did not change is not touched, and an
# attribute the new markup omits (the poll's own trigger, when a turn has been answered) is removed
# rather than left behind. The server stays a pure function of the checkpoint either way, which is
# the property worth keeping: it is the swap that got cleverer, not the endpoint.
SWAP: Final = "outerMorph"

# What a send does, which is the same merge plus a scroll: a message just typed is the one thing a
# reader definitely wants to be looking at, and unlike the poll this cannot fight somebody reading
# further up, because they were typing. The poll carries no scroll at all; following the end is the
# dock's to offer and the reader's to switch off.
SEND_SWAP: Final = "outerMorph scroll:bottom"

TRANSCRIPT_ID: Final = "transcript"

MODEL_ID: Final = "model"

SENDING_ID: Final = "sending"

# What each kind of panel is called where a person reads it: the role label on the panel, and the
# chip in the key that governs it. One mapping, so the legend and the thing it is a legend for
# cannot come to disagree about what a kind is called.
NAMES: Final[tuple[tuple[Kind, str], ...]] = (
    ("person", "you"),
    ("thinking", "thinking"),
    ("assistant", "assistant"),
    ("tool", "tool"),
)

# Which side of the exchange a kind is on: what reached the model, and what the model produced.
# The dock's flanking arrows step one side each, and the palette runs on this same axis, so it is
# stated once here rather than in both places.
SIDES: Final[dict[Kind, str]] = {
    "person": "person",
    "assistant": "model",
    "thinking": "model",
    "tool": "model",
}

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


def document(links: Links, heading: str, children: Node, session: str | None = None) -> str:
    """
    The whole document, which every page is this with something different in the middle.

    `session` is on the body because what the reader has decided about a conversation (which kinds
    they set aside, which calls they unfolded, whether they are following the end) belongs to that
    conversation and to no other. Every session on this console shares one origin, so a store not
    scoped by it would be one conversation's state imposed on all of them.

    The stylesheet and htmx are served from this process rather than from a CDN. The reason that
    matters most here is the last one anybody thinks of: a coding agent is pointed at a
    repository somewhere private, and a console that needs a third-party origin to render is a
    console that does not render there. The others are that a CDN sees every page anybody opens,
    and that a script fetched at page load is a dependency nothing in this repository pins.

    htmx is a plain blocking script tag, which is what the library asks for: `defer`,
    `type="module"`, and injecting it over AJAX are all documented as unreliable, and the failure
    is a page where no attribute does anything.

    The console's own script is blocking and in the head for a different reason: it pins the
    reader's chosen theme on `<html>` before the first paint, so a page opened dark does not flash
    light on the way there. Everything else it does waits for the document, which it arranges
    itself rather than by being deferred.
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
                            script(attrs={"src": links.to_asset("mainplate.js")}),
                        ]
                    ),
                    body(attrs={"data-session": session}, children=children),
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


def model_select(models: Sequence[Listed], chosen: str | None = None) -> Element:
    """
    The models one profile offers, as the select the form submits.

    Its own element with a stable id, because changing the profile replaces exactly this and
    nothing else on the page. A profile always offers at least one model (discovery refuses a
    profile that lists none), so this is never an empty select nobody can submit.

    Grouped by family, because a gateway fronting several vendors answers with seventy entries and
    an ungrouped list of seventy is a list nobody reads. The value is the id the request will name;
    the text is whatever the endpoint calls it, which on the Anthropic wire is written for a person
    and on the OpenAI wire is the id again.
    """
    picked = chosen if any(chosen == model.id for model in models) else models[0].id
    return select(
        attrs={"id": MODEL_ID, "name": "model", "aria-label": "Model"},
        children=[
            optgroup(
                attrs={"label": family},
                children=[
                    option(attrs={"value": model.id, "selected": model.id == picked}, children=model.label)
                    for model in found
                ],
            )
            for family, found in grouped(models)
        ],
    )


def profile_select(links: Links, catalogue: Catalogue) -> Element:
    """
    Which endpoint to answer on, and the control that swaps the model list beside it.

    htmx sends a triggering input's own value, so the `hx-get` needs no interpolation: choosing a
    profile asks for that profile's models and replaces the select next to this one. Without a
    browser the form still posts, carrying whatever models the page was rendered with, and the
    handler refuses a pair nothing offers.

    `outerHTML` and deliberately not the `outerMorph` the transcript uses. Morphing preserves what
    a control already holds, which is exactly right for a conversation being reread and exactly
    wrong here: the whole point of this swap is that the model list is now a *different* list, and
    a merge would keep a selection the new profile may not even offer.
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
            option(attrs={"value": name, "selected": name == catalogue.default.profile}, children=name)
            for name in catalogue.profiles
        ],
    )


def picker(links: Links, catalogue: Catalogue) -> Element:
    """The two selects, which appear only where a session is being created."""
    return div(
        cls="picker",
        children=[
            span(cls="label", children="Answer with"),
            profile_select(links, catalogue),
            model_select(catalogue.offered[catalogue.default.profile], catalogue.default.model),
        ],
    )


def chosen_note(chosen: Choice | None) -> Element:
    """What an existing session is on, as a fact rather than a control: it cannot be changed."""
    if chosen is None:
        return span(cls="picker")
    return span(cls=("picker", "settled"), children=f"{chosen.profile} \N{MIDDLE DOT} {chosen.model}")


def written(text: str) -> Element:
    """
    Prose, as the Markdown its author almost certainly meant it to be.

    `as_markup` is what makes putting this in a child position safe, and it is the only reason
    this is not simply escaped text: it renders the Markdown and then throws away everything the
    result is not allowed to contain, so a model that echoed a prompt back cannot put a script or
    a `javascript:` link on this page. See `markup.py` for why both halves of that are needed.
    """
    return div(cls="text", children=as_markup(text))


def working() -> Element:
    """
    Three dots that say something is still happening.

    Not an `hx-indicator`: those show while a *request* is in flight, and this is the opposite
    case, a fact read off the checkpoint that holds however many polls it takes. The two are drawn
    alike because a reader is being told the same thing.
    """
    return span(
        cls="waiting",
        attrs={"role": "status", "aria-label": "working"},
        children=[span(), span(), span()],
    )


def tool_block(used: ToolUse, anchor: str, at: int) -> Element:
    """
    One call, folded, with what it was handed and what it gave back.

    Folded because a call's arguments and its output are context a reader reaches for rather than
    prose they read through, and a real `<details>` because that is what works with no script at
    all and what the dock's fold controls act on.

    The id is the panel's own plus this block's place in it, which is stable for the same reason
    the panel's anchor is: a turn is rendered only once its messages are recorded, so the blocks
    inside a panel never change afterwards. The script needs it to put a reader's unfolded calls
    back after a swap, since the server renders `open` for one state only and morphing removes an
    attribute the new markup does not carry.

    A call with no result is drawn open and working. Today that means a turn whose run ended
    between the call and its return, because a turn is written to the checkpoint whole; when the
    turn in flight becomes readable from its own model steps, this is already what it looks like.
    """
    return details(
        cls="tool",
        attrs={"id": f"{anchor}-tool-{at}", "open": used.returned is None},
        children=[
            summary(
                children=[
                    span(cls="tool__name", children=used.tool),
                    working()
                    if used.returned is None
                    else span(
                        cls="tool__outcome",
                        attrs={"data-outcome": used.returned.outcome},
                        children=used.returned.outcome,
                    ),
                ]
            ),
            dl(
                cls="tool__body",
                children=[
                    dt(children="called with"),
                    dd(children=pre(children=used.arguments)),
                    *(
                        ()
                        if used.returned is None
                        else (dt(children="returned"), dd(children=pre(children=used.returned.content)))
                    ),
                ],
            ),
        ],
    )


def block_element(block: Block, anchor: str, at: int) -> Element:
    match block:
        case Prose(text=text):
            return div(cls=("block", "block--text"), children=written(text))
        case Reasoning(text=text):
            return div(cls=("block", "block--thinking"), children=written(text))
        case ToolUse():
            return div(cls=("block", "block--tool"), children=tool_block(block, anchor, at))
        case _ as unreachable:
            assert_never(unreachable)


def panel_element(panel: Panel) -> Element:
    """
    One run of one kind of thing, with the facts about it above it.

    `data-kind` and `data-side` are the whole of what the chrome needs to know: the key filters by
    kind, the dock's flanking arrows step by side, and the stylesheet draws the edge from the same
    attribute. Nothing has to keep a list of selectors in step with a list of kinds.
    """
    return article(
        cls="panel",
        attrs={
            "id": panel.anchor,
            "data-kind": panel.kind,
            "data-side": SIDES[panel.kind],
            "data-turn": str(panel.turn),
        },
        children=[
            header(
                cls="panel__meta",
                children=[
                    span(cls="panel__role", children=dict(NAMES)[panel.kind]),
                    a(
                        cls="panel__anchor",
                        attrs={"href": f"#{panel.anchor}"},
                        children=f"#{panel.turn}",
                    ),
                ],
            ),
            *(block_element(block, panel.anchor, at) for at, block in enumerate(panel.blocks)),
        ],
    )


def waiting_panel() -> Element:
    """
    One panel for however many messages are outstanding, because one reply is what is actually
    being written: the turns behind it are queued, not in flight.
    """
    return article(
        cls="panel",
        attrs={"id": "waiting", "data-kind": "assistant", "data-side": "model"},
        children=[
            header(cls="panel__meta", children=span(cls="panel__role", children="assistant")),
            div(cls=("block", "block--text"), children=working()),
        ],
    )


def transcript_region(links: Links, session: str, said: Transcript, stalled: str | None = None) -> Element:
    """
    The conversation, and whether it is still asking for the rest of it.

    The trigger is on the region itself, so a transcript that has been answered comes back
    carrying no trigger and the polling stops. Nothing has to be told to stop it: morphing removes
    an attribute the new markup does not have, exactly as replacement did.

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
        if said.awaiting and stalled is None
        else {}
    )
    drawn: list[Element] = [panel_element(panel) for panel in said.panels]
    if said.awaiting and stalled is None:
        drawn.append(waiting_panel())
    if stalled is not None:
        drawn.append(p(cls="stalled", children=stalled))
    return div(
        cls="transcript",
        attrs={"id": TRANSCRIPT_ID, **polling},
        children=drawn or p(cls="empty", children="Ask it something."),
    )


def search_card() -> Element:
    """
    A field that marks every match in the conversation and steps through them.

    Its own card, and no `hx-` attribute anywhere on it: searching what is already on the page is
    not a question for the server, and asking one would mean a round trip per keystroke to render
    a conversation the browser is already holding.
    """
    return div(
        cls="search",
        children=[
            input_(
                cls="search__input",
                attrs={"type": "search", "placeholder": "find", "aria-label": "Find in conversation"},
            ),
            div(
                cls="search__bar",
                children=[
                    span(cls="search__count", attrs={"role": "status"}),
                    button(
                        cls="search__nav",
                        attrs={
                            "type": "button",
                            "data-search": "prev",
                            "aria-label": "Previous match",
                            "disabled": True,
                        },
                        children="\N{UPWARDS ARROW}",
                    ),
                    button(
                        cls="search__nav",
                        attrs={"type": "button", "data-search": "next", "aria-label": "Next match", "disabled": True},
                        children="\N{DOWNWARDS ARROW}",
                    ),
                ],
            ),
        ],
    )


def key_card() -> Element:
    """
    Which kinds are in play, and the legend for every edge in the margin at the same time.

    Every kind is listed whether or not the conversation currently holds one, because this is a
    legend before it is a filter: a key that grew a row the first time the model reasoned would be
    a control that moved under the reader's hand. The search and the dock both read it, so a
    reader says once what they are looking through rather than once per control that looks.
    """
    return div(
        cls="key",
        children=[
            button(
                cls="key__chip",
                attrs={"type": "button", "data-kind": kind, "aria-pressed": "true"},
                children=name,
            )
            for kind, name in NAMES
        ],
    )


def dock_button(cls: str | None, label: str, glyph: str, attrs: dict[str, str]) -> Element:
    return button(
        cls=("dock__btn", cls),
        attrs={"type": "button", "aria-label": label, "title": label, **attrs},
        children=glyph,
    )


def dock_card() -> Element:
    """
    Stepping, leaping, folding, and following: everything that moves a reader through a session.

    Three columns of arrows, because the conversation has two sides and a reader usually wants one
    of them: the flanking columns step what a person said and what the model produced, in each
    one's own hue, and the middle column steps every panel the key leaves in play.
    """
    return div(
        cls="dock",
        children=[
            div(
                cls="dock__nav",
                children=[
                    dock_button(
                        "dock__btn--person",
                        "Previous message of yours",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-side": "person"},
                    ),
                    dock_button(None, "Previous panel", "\N{UPWARDS ARROW}", {"data-step": "-1"}),
                    dock_button(
                        "dock__btn--assistant",
                        "Previous panel from the model",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-side": "model"},
                    ),
                    dock_button(
                        "dock__btn--person",
                        "Next message of yours",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-side": "person"},
                    ),
                    dock_button(None, "Next panel", "\N{DOWNWARDS ARROW}", {"data-step": "1"}),
                    dock_button(
                        "dock__btn--assistant",
                        "Next panel from the model",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-side": "model"},
                    ),
                ],
            ),
            div(
                cls="dock__leap",
                children=[
                    dock_button(None, "To the start", "\N{UPWARDS ARROW TO BAR}", {"data-leap": "start"}),
                    dock_button(None, "To the end", "\N{DOWNWARDS ARROW TO BAR}", {"data-leap": "end"}),
                    # A mode rather than a jump, so it says whether it is on: while it is, the end
                    # stays pinned as answers arrive, and scrolling away is what switches it off.
                    dock_button(
                        "dock__btn--follow",
                        "Follow the end",
                        "\N{BLACK DOWN-POINTING TRIANGLE}",
                        {"data-follow": "toggle", "aria-pressed": "true"},
                    ),
                ],
            ),
            div(
                cls="dock__fold",
                children=[
                    dock_button(None, "Unfold every tool call", "\N{DOWNWARDS DOUBLE ARROW}", {"data-fold": "open"}),
                    dock_button(None, "Fold every tool call", "\N{UPWARDS DOUBLE ARROW}", {"data-fold": "shut"}),
                ],
            ),
        ],
    )


def theme_card() -> Element:
    """
    What the console is read by. Three states, because "follow the machine" is a choice too and a
    two-way toggle silently takes it away from anybody who had it.
    """
    return div(
        cls="theme",
        attrs={"role": "group", "aria-label": "Theme"},
        children=[
            button(attrs={"type": "button", "data-theme-choice": choice}, children=label)
            for choice, label in (("light", "day"), ("system", "auto"), ("dark", "night"))
        ],
    )


def rail() -> Element:
    """
    Everything that navigates the conversation, in one column outside the region that swaps.

    Outside deliberately. The transcript is replaced whenever an answer arrives, and a control
    living inside it would be rebuilt under a reader's finger, lose its focus, and forget what
    they had typed into it. What the rail *projects* onto the transcript survives instead by being
    reapplied after each swap, which is the script's job.

    The clasp comes first so that on a window too narrow to stand the rail beside the conversation
    it is left where the cards' head was, and the cards slide off. Which width that is stays the
    stylesheet's to say.
    """
    return section(
        cls="rail",
        attrs={"aria-label": "Conversation controls"},
        children=[
            button(
                cls="rail__clasp",
                attrs={"type": "button", "aria-expanded": "false", "aria-label": "Conversation controls"},
                children="\N{EQUALS SIGN}",
            ),
            search_card(),
            key_card(),
            dock_card(),
            theme_card(),
        ],
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

    `hx-indicator` names what is shown while the post is in flight, which is a different thing
    from the working dots in the transcript: this one says *your message has not landed yet*, and
    it is over in a round trip. The one in the transcript says the model has not answered yet, and
    is read off the checkpoint rather than off a request.
    """
    driving = (
        {
            "hx-post": action,
            "hx-target": f"#{TRANSCRIPT_ID}",
            "hx-swap": SEND_SWAP,
            "hx-indicator": f"#{SENDING_ID}",
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
            div(
                cls="row",
                children=[
                    beneath,
                    span(
                        cls=("sending", "htmx-indicator"),
                        attrs={"id": SENDING_ID, "role": "status"},
                        children="sending\N{HORIZONTAL ELLIPSIS}",
                    ),
                ],
            ),
        ],
    )


def shell(
    links: Links,
    listed: tuple[Session, ...],
    showing: str | None,
    pane: Sequence[Element],
    aside_rail: Iterable[Element] = (),
) -> Element:
    return div(
        cls="shell",
        children=[
            sidebar(links, listed, showing),
            main(children=[header(children=h1(children="mainplate")), *pane]),
            *aside_rail,
        ],
    )


def start_page(links: Links, listed: tuple[Session, ...], catalogue: Catalogue) -> str:
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
                transcript_region(links, session="", said=Transcript(panels=(), awaiting=False, turns=0)),
                composer(links.to_start(), picker(links, catalogue), live=False),
            ],
        ),
    )


def stalled_by(showing: Conversation) -> str | None:
    """
    Why this session cannot be answered, or nothing at all when it can.

    One sentence naming the pair, because that is the only thing a person can act on. It says
    "no longer offers" without guessing which half moved, since a pair that was available when the
    session started can stop being so in two ways this cannot tell apart: the profile edited out of
    the configuration file, or the endpoint dropping a model it used to list. Naming one would be a
    guess, and the pair is what somebody has to restore either way.
    """
    if showing.answerable or showing.chosen is None:
        return None
    return (
        f"This session was started on profile {showing.chosen.profile!r} with {showing.chosen.model!r}, "
        f"which is no longer offered. Put it back to carry on, or start a new session."
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
            # Only where there is a conversation to navigate. On the page where a session does not
            # exist yet every control in it would be pointed at an empty transcript, which is a
            # row of dead buttons rather than an offer.
            aside_rail=[rail()],
        ),
        session=showing.session.id,
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
