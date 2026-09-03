# Every page and fragment the console renders, as node trees.
#
# Pure functions of already-answered questions: nothing here reads a store, and nothing here
# knows what a `Run` is. `console` gathers what a page needs and calls one of these, which is
# what lets a page and the fragment inside it be the *same* function called at two depths rather
# than two renderings of one thing that can disagree.
#
# One live region, and it is the transcript. A turn being answered is the only thing on this console
# that changes without somebody doing anything, and it changes several times while it runs, so the
# region carries no `hx-` attribute of its own: the page holds one connection, outside everything
# that swaps, and the server sends this region down it whenever the session records anything.
#
# The chrome that navigates the conversation (the search, the key, the dock) is deliberately
# *outside* that region too, so a swap cannot take a control away mid-press and nothing has to be
# rebuilt under a reader's finger. What the chrome projects back onto the transcript (search marks,
# the panel a reader landed on, which kinds are set aside) is reapplied after each swap by the
# script, which holds that state as values rather than reading it back out of the markup.

from __future__ import annotations

import json
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Final
from typing import assert_never

from pydantic_ai.settings import ThinkingLevel
from without_html import DOCTYPE
from without_html import Element
from without_html import VoidElement
from without_html import a
from without_html import article
from without_html import aside
from without_html import body
from without_html import button
from without_html import code
from without_html import datalist
from without_html import dd
from without_html import details
from without_html import div
from without_html import dl
from without_html import dt
from without_html import form
from without_html import h1
from without_html import h2
from without_html import head
from without_html import header
from without_html import html
from without_html import input_
from without_html import label
from without_html import li
from without_html import link
from without_html import main
from without_html import meta
from without_html import option
from without_html import p
from without_html import pre
from without_html import render
from without_html import script
from without_html import section
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
from mainplate.catalogue import Offering
from mainplate.catalogue import grouped
from mainplate.conversation import DISPOSITION_FIELD
from mainplate.conversation import NETWORK_FIELD
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import Block
from mainplate.conversation import Disposition
from mainplate.conversation import Kind
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reasoning
from mainplate.conversation import Spent
from mainplate.conversation import Steering
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.forge import Reachable
from mainplate.markup import as_markup
from mainplate.reference import Cost
from mainplate.reference import Described
from mainplate.reference import Reference
from mainplate.reference import describe
from mainplate.sandbox import Filesystem
from mainplate.service import Conversation
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import TITLE_LENGTH
from mainplate.sessions import Session
from mainplate.thinking import THINKING_CHOICES
from mainplate.thinking import name_of_thinking

# What every swap of the transcript does. `outerMorph` rather than `outerHTML`, because the server
# renders the whole conversation into every message and a wholesale replacement would throw away
# everything a reader had done to it: a tool call they had unfolded, the search marks laid over it,
# the panel they had landed on, and the caret if it were ever in there. Morphing merges the new
# markup into the DOM already on screen, so a panel that did not change is not touched. The server
# stays a pure function of the checkpoint either way, which is the property worth keeping: it is the
# swap that got cleverer, not the endpoint.
#
# It is also what makes a message that changed nothing free. A stream sends whole current state
# rather than deltas, so a reader may be handed markup they are already showing, and morphing that
# in touches no node at all.
SWAP: Final = "outerMorph"

# The element holding the page's live connection: an inert sink, not a region. A message carrying
# only `<hx-partial>` elements leaves it alone, and one that somehow carried bare markup lands here
# rather than over a conversation somebody is reading.
STREAM_ID: Final = "stream"

# What a send does, which is the same merge plus a scroll: a message just typed is the one thing a
# reader definitely wants to be looking at, and unlike an update arriving on its own this cannot
# fight somebody reading further up, because they were typing. What the stream sends carries no
# scroll at all; following the end is the dock's to offer and the reader's to switch off.
SEND_SWAP: Final = "outerMorph scroll:bottom"

TRANSCRIPT_ID: Final = "transcript"

MODEL_ID: Final = "model"

# The two folds on the picker. A `<label>` reaches its control by id, so these have to be different
# strings on a page that draws both, and stable across the swap that replaces the model group.
ENDPOINT_TOGGLE_ID: Final = "open-endpoint"

MODEL_TOGGLE_ID: Final = "open-model"

REPOSITORY_TOGGLE_ID: Final = "open-repository"

THINKING_TOGGLE_ID: Final = "open-thinking"

THINKING_ID: Final = "thinking"

REPOSITORY_ID: Final = "repository"

NETWORK_TOGGLE_ID: Final = "open-network"
NETWORK_ID: Final = "network"

# The one field the worktree group posts. Form-only rather than a recorded field, because what
# it carries is *two* recorded things at once - a repository and a filesystem level - and which
# two is decided by parsing it, at the boundary, once.
WORKSPACE_FIELD: Final = "workspace"

SENDING_ID: Final = "sending"

# The form the picker's controls belong to, named because on the start page they do not sit inside
# it. There the choosing fills `main`'s growing row and the box is pinned under it, so the endpoint
# radios, the model radios and the two selects are *siblings* of the form that posts them; without
# this the browser submits a message with no endpoint and no model on it and the console refuses its
# own page. `form` is what associates a control with a form it is not inside, so the layout stays
# what it is and the page still posts with no script at all. The fork page nests its picker inside
# the same-named form, where the attribute simply names the ancestor it already had.
CHOOSING_ID: Final = "choosing"

# What each kind of panel is called where a person reads it: the role label on the panel, and the
# chip in the key that governs it. One mapping, so the legend and the thing it is a legend for
# cannot come to disagree about what a kind is called.
NAMES: Final[tuple[tuple[Kind, str], ...]] = (
    ("person", "you"),
    ("steering", "you (steering)"),
    ("thinking", "thinking"),
    ("assistant", "assistant"),
    ("tool", "tool"),
)

# Which side of the exchange a kind is on: what reached the model, and what the model produced.
# The dock's flanking arrows step one side each, and the palette runs on this same axis, so it is
# stated once here rather than in both places.
SIDES: Final[dict[Kind, str]] = {
    "person": "person",
    "steering": "person",
    "assistant": "model",
    "thinking": "model",
    "tool": "model",
}

# What the link that starts one is called, and what the tab says on the page where a session does
# not exist yet. "Session" rather than "worktree", which is the other word for this and is already
# taken: a session's *worktree* is the git worktree it works in, so calling the session one too
# would make "a worktree's worktree" a sentence somebody has to parse.
NEW_SESSION: Final = "New session"

# What a session with no name of its own is called. Unreachable today, because every session is
# named when it is created - after the box, or after its first message - and nothing renames one.
# Kept as the answer to a row that has somehow lost its title, which is a database somebody edited
# rather than a state this console produces.
UNTITLED: Final = "Untitled"

# How much of a tree hash a rule prints. Git's own abbreviation length for a repository of any size,
# which is the number a reader is used to seeing and long enough to tell two snapshots apart.
SHORT_HASH: Final = 8


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
    stream: Reversible
    request_record: Reversible
    endpoint_models: Reversible
    fork_form: Reversible
    fork: Reversible
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

    def to_stream(self, session: str) -> str:
        """
        The connection a page holds open, told which conversation it is showing.

        A query parameter for the reason `to_endpoint_models` uses one: it narrows what a single
        connection reports on rather than picking a resource out. The stream is the page's, and the
        session is what the page happens to be looking at.
        """
        return f"{url_for(self.stream)}?session={session}"

    def to_endpoint_models(self) -> str:
        """
        The model select, asked for with an endpoint in the query string.

        A query parameter rather than a path segment, because the endpoint is *the value of the
        select that asks*: htmx sends a triggering input's own value, so this URL needs no
        interpolation and the select needs no script to build one.
        """
        return url_for(self.endpoint_models)

    def to_request_record(self, session: str, turn: int, at: int) -> str:
        """
        What the checkpoint holds behind one panel, addressed the way the panel itself is.

        Path segments rather than a query string, because a panel's identity *is* the pair: the
        anchor a permalink is built on is already `turn` and `at`, so this is the same address in
        another shape rather than a filter over something.
        """
        return url_for(self.request_record, {"session": session, "turn": turn, "at": at})

    def to_fork_form(self, session: str, at: int) -> str:
        """Where to ask what a branch from this turn should be answered with."""
        return f"{url_for(self.fork_form, {'session': session})}?at={at}"

    def to_fork(self, session: str) -> str:
        return url_for(self.fork, {"session": session})

    def to_asset(self, name: str) -> str:
        return f"{self.assets}/{name}"


# Which of the bundled extensions this console installs. An allowlist rather than a bundle taken
# whole: `htmax` registers everything it carries on inclusion, and several of those would change
# how this page behaves without being asked for - `history-cache` would put back the history store
# htmx 4 deliberately removed, and `hx-live` and `alpine-compat` are reactive scripting this
# console does not want. htmx reads it from the meta tag before any extension registers, so a name
# absent here is never installed rather than installed and unused.
EXTENSIONS: Final = "sse"


def stream_element(links: Links, session: str) -> Element:
    """
    The page's one live connection, and the sink a message that named no region would land in.

    Outside everything that swaps, which is what makes it the page's rather than a region's: the
    transcript is morphed whenever the session moves, and a connection held by the element being
    morphed would be a connection re-established by its own traffic. It sits directly under `body`
    for the same reason the rail sits outside the transcript.

    `hx-target` is itself and the swap is `innerHTML`, so this is an inert sink. Every message
    carries `<hx-partial>` elements naming their own targets, which htmx applies while leaving the
    connecting element untouched; the sink is what a message carrying anything else would land in,
    where a target pointing at the conversation would let a stray message replace it.

    Only where there is a session, because that is the only thing there is to watch. A page-level
    connection with nothing to report on would be a held socket and a heartbeat.
    """
    return div(
        attrs={
            "id": STREAM_ID,
            "hidden": True,
            "hx-sse:connect": links.to_stream(session),
            "hx-target": "this",
            "hx-swap": "innerHTML",
        }
    )


def document(
    links: Links, heading: str, children: Element, session: str | None = None, forked_from: str | None = None
) -> str:
    """
    The whole document, which every page is this with something different in the middle.

    `session` is on the body because what the reader has decided about a conversation, which is
    which kinds they set aside and what they have kept unsent, belongs to that conversation and
    to no other. Every session on this console shares one origin, so a store not scoped by it would
    be one conversation's decisions imposed on all of them. The theme is the exception and is
    deliberately unscoped: it is the reader's rather than any conversation's.

    `forked_from` is what lets a branch inherit its parent's shelf, and it is here because the copy
    is the *script's* to make. The server never sees a draft, so it cannot carry one across the way
    `Service.fork` carries a turn; what it can do is say which conversation this one came from and
    let the page holding both answer the rest.

    The stylesheet and htmx are served from this process rather than from a CDN. The reason that
    matters most here is the last one anybody thinks of: a coding agent is pointed at a
    repository somewhere private, and a console that needs a third-party origin to render is a
    console that does not render there. The others are that a CDN sees every page anybody opens,
    and that a script fetched at page load is a dependency nothing in this repository pins.

    htmx is a plain blocking script tag, which is what the library asks for: `defer`,
    `type="module"`, and injecting it over AJAX are all documented as unreliable, and the failure
    is a page where no attribute does anything.

    It is `htmax`, which is htmx bundled with its extensions in one file, and the reason is one
    file rather than the extension it is here for. Core and an extension vendored separately are
    two files that have to be kept on one version, and the failure when they are not is a swap that
    silently does the wrong thing rather than an error anybody sees. The price is a bundle carrying
    ten extensions this console does not use, which the `htmx-config` meta gates: `extensions`
    is an allowlist read before any of them register, so every one not named there is never
    installed. Naming them here is also the honest statement of which this console depends on.

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
                            meta(attrs={"name": "htmx-config", "content": f"extensions: {EXTENSIONS}"}),
                            script(attrs={"src": links.to_asset("htmax.min.js")}),
                            script(attrs={"src": links.to_asset("mainplate.js")}),
                        ]
                    ),
                    body(
                        attrs={"data-session": session, "data-forked-from": forked_from},
                        children=[*((stream_element(links, session),) if session else ()), children],
                    ),
                ],
            ),
        ]
    )


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

    A branch whose parent is not in the list is drawn as a root. That is not a fallback but the
    honest reading: the row says where it came from either way, and hiding a session because its
    parent went missing would lose a conversation somebody can still read.
    """
    known = {session.id for session in listed}
    children: dict[str | None, list[Session]] = {}
    for session in listed:
        parent = session.forked.session if session.forked and session.forked.session in known else None
        children.setdefault(parent, []).append(session)

    arranged: list[tuple[Session, int]] = []

    def walk(parent: str | None, depth: int) -> None:
        for session in children.get(parent, ()):
            arranged.append((session, depth))
            walk(session.id, min(depth + 1, DEEPEST))

    walk(None, 0)
    return tuple(arranged)


def sidebar(links: Links, listed: tuple[Session, ...], showing: str | None, reachable: Reachable) -> Element:
    """
    Every session, newest first, with branches under what they branched from and the current one marked.

    Newest first among siblings rather than across the whole list, which is what a tree costs and
    what it buys: a branch made this morning sits with the conversation it came from rather than at
    the top away from it, and the ordering within any one group is still the one a chat console
    reads in.

    A row says which repository its session works in, because that is the thing two conversations
    with the same opening line are actually told apart by once a console is used to work in more
    than one. `reachable` is what turns the recorded id into the name somebody recognises, and a
    session working in nothing says nothing rather than saying so - most of a list is one or the
    other, and the majority does not need labelling.
    """
    return aside(
        cls="sessions",
        children=[
            a(cls="start", attrs={"href": links.to_home()}, children=NEW_SESSION),
            ul(
                children=[
                    li(
                        attrs={"data-depth": str(depth)},
                        children=a(
                            cls=("session", "current" if session.id == showing else None, "forked" if depth else None),
                            attrs={"href": links.to_session(session.id)},
                            children=[
                                span(cls="name", children=session.title or UNTITLED),
                                span(
                                    cls="meta",
                                    children=[
                                        time(
                                            cls="when",
                                            attrs={"datetime": session.created_at.isoformat()},
                                            children=session.created_at.strftime("%b %d, %H:%M"),
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
                        ),
                    )
                    for session, depth in arrange(listed)
                ]
            ),
        ],
    )


def tokens(count: int) -> str:
    """
    A token count as a person compares them, which is to two or three figures and no more.

    Decimal throughout, because the two wires disagree about the base and a card mixing them would
    be the one place on the page where 256K and 262K meant the same thing. A model's context is a
    number to weigh against another model's, not a size to allocate against.
    """
    if count >= 1_000_000:
        # One decimal, and not even that when it would be a zero. A context window is a number to
        # weigh against another model's, so the digits past the first are noise: a 2^20 window is
        # `1.04858M` at full precision, which reads as precision nobody asked for about a figure
        # that is round in the other base.
        return f"{f'{count / 1_000_000:.1f}'.removesuffix('.0')}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}K"
    return str(count)


def dollars(rate: float) -> str:
    """
    A price per million tokens, carrying the digits that vary and no others.

    Significant figures rather than a fixed two decimals, because these rates span four orders of
    magnitude: a fixed width writes the cheap end as `$0.08` where the difference between models is
    in the next digit, and pads the expensive end to `$75.00` where the cents are noise. It also
    keeps a pair consistent with itself - `$5.00/$25` reads as two differently-measured numbers,
    where `$5/$25` reads as the ratio it is.
    """
    return "free" if rate == 0 else f"${rate:g}"


def charged(cost: Decimal) -> str:
    """
    What a turn or a session came to, at the precision a person actually reads.

    Four decimal places is the floor rather than the format, because these figures span from a
    fraction of a cent to tens of dollars and no single width serves both: a fixed two decimals
    writes most single turns as `$0.00`, and full precision writes a long session as fourteen
    digits of a number nobody is going to check to the picogram. Below the floor the figure is
    named as being under it, which is a true thing to say where `$0.00` is not.

    Zero is `free` and not `$0`, matching what a card says about a model that charges nothing: the
    two are the same claim and reading them differently on one page would suggest they are not.
    """
    if cost == 0:
        return "free"
    if cost >= 1:
        return f"${cost:.2f}"
    if cost < Decimal("0.0001"):
        return "<$0.0001"
    return f"${cost.quantize(Decimal('0.0001')):f}".rstrip("0").rstrip(".")


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
    """
    seconds = took.total_seconds()
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes:.0f}m {rest:.0f}s"


def spend_element(spent: Spent, whose: str) -> tuple[Element, ...]:
    """
    What something cost, as counts and money, or nothing at all where it has not answered yet.

    The counts are always drawn and the money only where something priced it, because they fail
    independently: the tokens are on the response itself and are known for every turn on every wire,
    where the price needs a record in a database this console may not have been asked to read. A
    turn showing counts and no money is one whose model nobody published a price for, which is the
    same blank its card shows and is why the two are separate elements rather than one sentence.

    `whose` names what the figures are of, because a turn rule stands at a request boundary too and
    carries the **turn's** total beside that request's own marker. Identical where a turn took one
    round trip and visibly different where it took several, so the title is what settles which is
    being read rather than the reader inferring it from the size of the number.

    The time leads the figures because it is the one a reader is usually waiting on, and it says in
    its title what it is the time *of*: the round trips to the provider and not the turn from end to
    end, since the calls a turn made in between are timed on their own panels.
    """
    if not spent.asked and not spent.answered:
        return ()
    return (
        *(
            (
                span(
                    cls="rule__took",
                    attrs={"title": f"{whose} spent {elapsed(spent.took)} waiting on the model"},
                    children=elapsed(spent.took),
                ),
            )
            if spent.took is not None
            else ()
        ),
        span(
            cls="rule__tokens",
            attrs={"title": f"{whose}: {spent.asked:,} tokens in, {spent.answered:,} out"},
            children=f"{tokens(spent.asked)} in \N{MIDDLE DOT} {tokens(spent.answered)} out",
        ),
        *(
            (
                span(
                    cls="rule__cost",
                    attrs={"title": f"{whose}, estimated from published rates and not billed: ${spent.cost:f}"},
                    children=f"\N{MIDDLE DOT} {charged(spent.cost)}",
                ),
            )
            if spent.cost is not None
            else ()
        ),
    )


def model_card(described: Described, chosen: bool) -> Element:
    """
    One model as something you pick rather than something you scroll past.

    A `<label>` around a radio, so it works with no script at all: the whole card is the hit target,
    the browser does the selecting, and the form posts the same field a select posted. That is the
    same bargain the rest of this console makes - the page works without JavaScript and is nicer
    with it - and it is why this is not a grid of buttons driven by a handler.

    Every fact on it comes from the reference database, so a card says the same kinds of things
    whichever endpoint serves the model. What it says nothing about it simply omits: an absent price
    is a blank rather than a zero, because a model shown as costing nothing is worse than a model
    shown as costing something nobody wrote down.
    """
    facts = described.listed
    return label(
        cls="model",
        # The name this card answers to, which is the same string the group's completion list is
        # built from: naming one exactly is how the reader picks it without reaching for the card.
        attrs={"data-provider": facts.provider, "data-name": facts.label},
        children=[
            input_(
                cls="model__pick",
                attrs={
                    "type": "radio",
                    "name": "model",
                    "value": facts.id,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="model__name", children=facts.label),
            span(cls="model__id", children=facts.id),
            *(
                (span(cls="model__about", attrs={"title": about}, children=about),)
                if (about := described.about)
                else ()
            ),
            div(
                cls="model__facts",
                children=[
                    *measured("context", described.context, "of context"),
                    *measured("output", described.output, "of output"),
                    *priced(described.cost),
                ],
            ),
            div(
                cls="model__traits",
                children=[span(cls="model__trait", children=trait) for trait in described.traits],
            ),
            # Only where a reference was configured and had nothing to say. With the setting absent
            # nothing is missing, because nothing was ever looked up, and a marker there would be
            # reporting the absence of a feature nobody turned on.
            *((span(cls="model__unknown", children="no reference record"),) if described.unreferenced else ()),
        ],
    )


def measured(what: str, count: int | None, saying: str) -> tuple[Element, ...]:
    """One token figure, or nothing at all where the database does not carry it."""
    if count is None:
        return ()
    return (
        span(
            cls=("model__fact", f"model__fact--{what}"),
            attrs={"title": f"{count:,} tokens {saying}"},
            children=[span(cls="model__figure", children=tokens(count)), span(cls="model__unit", children=what)],
        ),
    )


def priced(cost: Cost | None) -> tuple[Element, ...]:
    """
    What a million tokens in and a million out cost, as one figure pair.

    Both together because neither is a price on its own: a model that reads cheaply and writes
    expensively is the common shape, and showing one number would rank the list wrongly.
    """
    if cost is None:
        return ()
    return (
        span(
            cls=("model__fact", "model__fact--cost"),
            attrs={"title": f"{dollars(cost.input)} in and {dollars(cost.output)} out, per million tokens"},
            children=[
                span(cls="model__figure", children=f"{dollars(cost.input)}/{dollars(cost.output)}"),
                span(cls="model__unit", children="per Mtok"),
            ],
        ),
    )


def counted(many: int, thing: str) -> str:
    """`3 models`, `1 option`: a count with the word it counts, pluralised."""
    return f"{many} {thing}{'' if many == 1 else 's'}"


def choosing(
    legend: str,
    toggle: str,
    names: Sequence[str],
    body: Element,
    *,
    identified: str | None = None,
    extra: str | None = None,
) -> Element:
    """
    One group of cards, folded down to the one that is picked.

    A wall of cards is what the start page used to be: seventy of them on a real gateway, so the
    choice already made was somewhere in a list you had to scroll, and everything after that list
    was past the end of it. Shut, a group is the card you picked and nothing else; open, it is
    everything on offer, in place.

    **The fold is a checkbox and the folding is `:has()`, so no script decides any of this.** That
    is what keeps a shut group honest: what it draws is the card whose radio is actually checked,
    read off the radio itself, so there is no second copy of the choice to go stale and nothing to
    keep in step. A summary line naming the model would have been that second copy, and with
    scripting off it would have named the wrong one the moment somebody picked.

    The count on the control is there rather than left to be inferred, because a shut group is a
    single card with nothing about it saying others exist. It is what makes the group read as a
    picker, and it has to be rendered *inside* whatever the endpoint swap replaces or it keeps
    saying 27 after the list under it became 46.

    `names` is what an open group can be narrowed by, and it is the same list the cards are drawn
    from rather than a second one: one argument gives the count, the datalist and the filter, so
    none of the three can disagree about what is on offer.
    """
    listed = f"{toggle}-names"
    return section(
        cls=("picker__part", extra),
        attrs={"id": identified},
        children=[
            # A real checkbox, so opening a group is the browser's own behaviour. No `name`, so it
            # is never submitted, and deliberately no `form`, so it is not associated with one
            # either: this is a fold, not part of what a session is decided by.
            input_(cls="picker__toggle", attrs={"type": "checkbox", "id": toggle}),
            div(
                cls="picker__head",
                children=[
                    h2(cls="picker__legend", children=legend),
                    label(
                        cls="picker__more",
                        attrs={"for": toggle},
                        children=[
                            span(cls="picker__more--shut", children=counted(len(names), "option")),
                            span(cls="picker__more--open", children="done"),
                        ],
                    ),
                ],
            ),
            narrowing(listed, names),
            body,
        ],
    )


def narrowing(listed: str, names: Sequence[str]) -> Element:
    """
    A box that narrows an open group to the cards matching what is typed.

    The `<datalist>` is what makes typing worth anything with no script: the browser completes a
    name from the same list the cards are drawn from, so a long model id is a few keystrokes either
    way and the completion menu is itself a way of reading what is on offer. The *narrowing* is the
    script's, which is why the cards remain the thing that actually answers the question - with the
    file absent this is a box that suggests and does not filter, and every card is still there to
    be picked.

    Taking an entry from that menu names one exactly, and the script reads that as the choice: the
    card is checked and the group shuts. So the box is two things at once - a filter while a name is
    partial, and a way of picking once it is whole - which is what the completion menu already
    implies it should be. Matching a whole name and never a prefix is what keeps the two apart, and
    what stops the keystrokes spelling one name choosing a shorter one on the way past.

    Drawn on every group rather than only the long one. Searching by name is the same question
    whether a list holds four endpoints or seventy models, and a control that appeared once a list
    passed some length would be one nobody learns to expect.
    """
    return div(
        cls="picker__filter",
        children=[
            input_(
                cls="picker__filter-field",
                attrs={
                    "type": "search",
                    "list": listed,
                    "placeholder": "find",
                    "aria-label": f"Find in {listed}",
                    "autocomplete": "off",
                },
            ),
            datalist(attrs={"id": listed}, children=[option(attrs={"value": name}) for name in names]),
        ],
    )


def model_cards(models: Sequence[Listed], reference: Reference | None, chosen: str | None = None) -> Element:
    """
    The models one endpoint offers, as the cards the form submits one of.

    The whole group carries the stable id, head and fold included, because changing the endpoint
    replaces exactly this: the count beside the legend is a fact about the list below it, so it has
    to travel with it. Replacing the group also resets the fold, which is the right state to arrive
    in - a new endpoint means a new default model, already picked and worth seeing shut.

    An endpoint always offers at least one model (discovery refuses one that lists none), so this is
    never an empty group nobody can submit.

    Grouped by provider, because a gateway fronting several vendors answers with seventy entries and
    an ungrouped wall of seventy cards is worse than the ungrouped list of seventy it replaced. The
    value is the id the request will name; the name is whatever the endpoint calls it.
    """
    picked = chosen if any(chosen == model.id for model in models) else models[0].id
    return choosing(
        "Model",
        MODEL_TOGGLE_ID,
        # One name per card, which is what keeps the count honest: the datalist offering the label
        # *and* the routed id would be two entries per model and a group announcing 54 options over
        # 27 cards. Searching by id still works, because the filter matches a card's whole text and
        # the id is printed on it; the completion list is the readable half, and a list of names
        # interleaved with `anthropic/claude-sonnet-4-6` is not the readable half.
        [model.label for model in models],
        div(
            cls="models",
            attrs={"role": "radiogroup", "aria-label": "Model"},
            children=[
                section(
                    cls="models__provider",
                    children=[
                        h2(cls="models__heading", children=provider),
                        div(
                            cls="models__grid",
                            children=[
                                model_card(describe(model, reference), chosen=model.id == picked) for model in found
                            ],
                        ),
                    ],
                )
                for provider, found in grouped(models)
            ],
        ),
        identified=MODEL_ID,
        extra="picker__part--models",
    )


def endpoint_card(links: Links, offering: Offering, chosen: bool) -> Element:
    """
    One endpoint as where it actually points, rather than as a name somebody chose for it.

    The endpoint and the wire are on it because they are what distinguishes two endpoints that
    otherwise read alike, and on this machine that is the ordinary case rather than the exotic one:
    one gateway answers both wires, so a VM declares the same host twice and the *only* thing
    telling those two rows apart is the word `anthropic` or `openai` and the `/v1` on the end.

    htmx sends a triggering input's own value, so the `hx-get` needs no interpolation: choosing an
    endpoint asks for that endpoint's models and replaces the whole model group with them, head and
    fold included, so the count beside the legend is the new list's rather than the old one's.
    Without a browser the form still posts, carrying whatever models the page was rendered with, and
    the handler refuses a pair nothing offers.

    `outerHTML` and deliberately not the `outerMorph` the transcript uses. Morphing preserves what a
    control already holds, which is exactly right for a conversation being reread and exactly wrong
    here: the whole point of this swap is that the model list is now a *different* list, and a merge
    would keep a card the new endpoint may not even offer.
    """
    return label(
        cls="endpoint",
        attrs={"data-name": offering.endpoint},
        children=[
            input_(
                cls="endpoint__pick",
                attrs={
                    "type": "radio",
                    "name": "endpoint",
                    "value": offering.endpoint,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                    "hx-get": links.to_endpoint_models(),
                    "hx-target": f"#{MODEL_ID}",
                    "hx-swap": "outerHTML",
                    "hx-status:4xx": "swap:none",
                    "hx-status:5xx": "swap:none",
                },
            ),
            span(cls="endpoint__name", children=offering.endpoint),
            span(cls="endpoint__format", children=f"{offering.format} format"),
            span(cls="endpoint__url", children=offering.where),
            span(
                cls="endpoint__count",
                children=f"{len(offering.models)} model{'' if len(offering.models) == 1 else 's'}",
            ),
        ],
    )


def endpoint_cards(links: Links, catalogue: Catalogue, chosen: str | None = None) -> Element:
    picked = chosen if chosen in catalogue.offered else catalogue.default.endpoint
    return div(
        cls="endpoints",
        attrs={"role": "radiogroup", "aria-label": "Endpoint"},
        children=[endpoint_card(links, catalogue.offered[name], chosen=name == picked) for name in catalogue.endpoints],
    )


def thinking_card(naming: str, chosen: bool) -> Element:
    return label(
        cls="think",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="think__pick",
                attrs={
                    "type": "radio",
                    "name": THINKING_FIELD,
                    "value": naming,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="think__name", children=naming),
        ],
    )


def thinking_cards(chosen: ThinkingLevel | None) -> Element:
    """
    How hard to think, with no cascade behind it.

    Unlike the model list this is the same everywhere, because it is a property of the request
    rather than of the endpoint: every level is offered against every endpoint, and a model that
    cannot reason refuses or ignores it on the turn. That is the same stance the model id gets, and
    for the same reason - the provider's own answer about what it supports is the authoritative
    one, and gating here would hide a level that in fact works.

    Cards like the other three, which is worth more here than the control it replaced: eight levels
    is enough that a shut group saying `high` is a better answer than a select showing it, and
    naming them in one vocabulary means the same fold, the same count and the same narrowing serve
    every question this page asks.
    """
    picked = next((name for name, level in THINKING_CHOICES if level == chosen), None)
    return choosing(
        "Thinking",
        THINKING_TOGGLE_ID,
        [name for name, _ in THINKING_CHOICES],
        div(
            cls="thinks",
            attrs={"id": THINKING_ID, "role": "radiogroup", "aria-label": "Thinking"},
            children=[
                div(
                    cls="thinks__grid",
                    children=[thinking_card(name, chosen=name == picked) for name, _ in THINKING_CHOICES],
                )
            ],
        ),
    )


# The two answers to "what files does this session have" that are not a repository. Their values are
# the `Filesystem` members they mean, and that is unambiguous rather than lucky: a repository's id is
# `forge:key`, so it always holds a colon and can never be either of these.
NO_FILES: Final = "no files"
WHOLE_MACHINE: Final = "this whole machine"

WITHOUT_A_REPOSITORY: Final[tuple[tuple[str, Filesystem, str], ...]] = (
    (NO_FILES, Filesystem.NOTHING, "a conversation with nothing to edit"),
    (WHOLE_MACHINE, Filesystem.EVERYTHING, "every file this console can reach, including its own"),
)

NETWORK_CHOICES: Final[tuple[tuple[str, bool, str], ...]] = (
    ("off", False, "commands cannot dial out"),
    ("on", True, "commands can reach anything this machine can"),
)


def network_card(naming: str, saying: str, chosen: bool) -> Element:
    return label(
        cls="network",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="network__pick",
                attrs={
                    "type": "radio",
                    "name": NETWORK_FIELD,
                    # `on` and nothing, because a radio that is not checked posts no field at all and
                    # an absent field has to mean the safe answer. Spelling the off card's value as
                    # anything else would make "no field" and "the off card" two different strings
                    # meaning one thing, which is the shape that goes wrong when one of them is
                    # forgotten.
                    "value": "on" if naming == NETWORK_CHOICES[1][0] else "",
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="network__name", children=naming),
            span(cls="network__note", children=saying),
        ],
    )


def network_cards(chosen: bool) -> Element:
    """
    Whether a session's commands may dial out, offered wherever there are commands to run.

    On or off, and off rather than a list of hosts. An allowlist containing a code forge contains
    every gist on it and one containing a package registry contains a package anybody can publish,
    so what it would buy is a defence against a repository's own build script and very little
    against anything deliberate - at the price of a proxy in front of every command.
    """
    return choosing(
        "Network",
        NETWORK_TOGGLE_ID,
        [naming for naming, _, _ in NETWORK_CHOICES],
        div(
            cls="networks",
            attrs={"id": NETWORK_ID, "role": "radiogroup", "aria-label": "Network"},
            children=[
                div(
                    cls="networks__grid",
                    children=[
                        network_card(naming, saying, chosen=reaching is chosen)
                        for naming, reaching, saying in NETWORK_CHOICES
                    ],
                )
            ],
        ),
    )


def workspace_card(naming: str, value: str, saying: str, chosen: bool) -> Element:
    """
    One answer to what files a session has: a repository of its own, or one of the two that are not.

    A card rather than an `<option>`, and that is what the second line is drawn on: an `<option>`
    renders as text in every browser, so neither the forge a repository came from nor what a level
    means has anywhere to go inside a select. A row here carries it, which matters the moment two
    rows read `owner/repo` from different places.
    """
    return label(
        cls="repo",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="repo__pick",
                attrs={
                    "type": "radio",
                    "name": WORKSPACE_FIELD,
                    "value": value,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="repo__name", children=naming),
            span(cls="repo__forge", children=saying),
        ],
    )


def workspace_cards(reachable: Reachable, repository: str | None, chosen: Filesystem) -> Element:
    """
    What files a session has, as **one** question rather than two that must be kept agreeing.

    A repository and a filesystem level used to be separate groups, and that was the mistake: the
    level a repository implies is not a second decision beside it, so the two controls had to be kept
    in step - the worktree level greyed until a repository was picked, a swap to update the greying,
    and a filter that could name a card it must not check. Every one of those is machinery for
    keeping one answer stored in two places, which is the thing this console refuses everywhere else.

    Asked once, the answers are simply the cards: each repository this console can reach, and the two
    that are not a repository. Picking one settles `repository` and `isolation.filesystem` together,
    so they cannot disagree at the source rather than being reconciled after the fact.

    Values are the repository's id or the `Filesystem` member's own name, and that is unambiguous
    rather than lucky: an id is `forge:key`, so it always holds a colon and can never be either name.

    `this whole machine` is worth reading twice before picking. A session on it can read this
    console's own configuration, which holds the credentials, and its store, which holds every other
    conversation. It is still inside a sandbox, so the network answer below still means what it says,
    but nothing about the filesystem is held back.

    Labels come from `Reachable`, which qualifies a row only where two would otherwise read the same:
    the same repository can be attached twice with different rights, and choosing between two
    identical rows is guessing.
    """
    rows = reachable.labelled()
    return choosing(
        "Worktree",
        REPOSITORY_TOGGLE_ID,
        [*(naming for naming, _, _ in WITHOUT_A_REPOSITORY), *(naming for _, naming in rows)],
        div(
            cls="repos",
            attrs={"id": REPOSITORY_ID, "role": "radiogroup", "aria-label": "Worktree"},
            children=[
                div(
                    cls="repos__grid",
                    children=[
                        *(
                            workspace_card(naming, level.value, saying, chosen=repository is None and level is chosen)
                            for naming, level, saying in WITHOUT_A_REPOSITORY
                        ),
                        *(
                            workspace_card(naming, reached.id, reached.forge, reached.id == repository)
                            for reached, naming in rows
                        ),
                    ],
                )
            ],
        ),
    )


def picker(
    links: Links,
    catalogue: Catalogue,
    reachable: Reachable | None,
    reference: Reference | None,
    chosen: Choice | None = None,
) -> Element:
    """
    Everything a session is decided by, laid out as the question it actually is.

    One block rather than a row of selects, because choosing a model is the one real decision on
    this page and a row of selects made it look like a footnote to the message box.

    **The order is what a session is decided by, widest first: where it works, what answers it,
    which model, and how hard that model thinks.** The repository comes first because it is the
    broadest of the four and the only one that decides what the agent can touch at all; the endpoint
    and the model are next and are adjacent because they are a pair, the list being whatever the
    endpoint above it offers; the thinking level is last because it is a setting on the model rather
    than a choice beside it.

    Both card groups are folded down to what is picked (see `choosing`), so the order above is what
    a reader sees rather than what they would reach after scrolling: four labelled lines and the two
    cards that are the current choice. Opened, the models are the one part with no bound on their
    length, and on a wide window they are the only part that scrolls - the list is as long as
    whatever gateway you are pointed at makes it, where everything else here is a fixed handful of
    rows, so it is what gives up height when there is not enough.

    `chosen` is what the controls start on, defaulting to the configured default for a new session.
    A fork passes the parent's own choice instead, so continuing on the same model is the path that
    needs nothing touched: the fork exists to let the choice change, not to require it.

    Whether the worktree *can* be chosen here is the caller's answer, given as `None` rather than
    as an empty set: a fork of a session already in a repository is handed `None`, because it
    inherits that one and a control that could not be honoured would be a lie about what the page
    does. An empty `Reachable` is a different thing and still draws the group, since a machine with
    no forge attached still has two answers worth offering.

    A choice naming an endpoint the catalogue no longer has falls back to the default rather than
    rendering a picker with nothing selected. That is the same case `stalled_by` explains on the
    session page, and here there is a sensible thing to show.
    """
    starting = chosen if chosen is not None and chosen.endpoint in catalogue.offered else catalogue.default
    return div(
        cls="picker",
        children=[
            *(
                ()
                if reachable is None
                else (workspace_cards(reachable, starting.repository, starting.isolation.filesystem),)
            ),
            # The network sits under the worktree and above the endpoint, because that is the order
            # of breadth: what a session's files are decides what it can touch, whether it can dial
            # out decides what it can do with them, and the endpoint and model only decide who
            # answers.
            network_cards(starting.isolation.network),
            choosing(
                "Endpoint",
                ENDPOINT_TOGGLE_ID,
                list(catalogue.endpoints),
                endpoint_cards(links, catalogue, starting.endpoint),
            ),
            model_cards(catalogue.offered[starting.endpoint].models, reference, starting.model),
            thinking_cards(starting.thinking),
        ],
    )


def session_spend(spent: Spent) -> str:
    """
    What a whole session has come to, as the sentence behind the figure under the message box.

    The time is named only where every turn in the session was timed, which is the rule the money
    follows: a total quietly missing a turn reads as the whole and understates it.
    """
    said = f"{spent.asked:,} tokens in and {spent.answered:,} out over this session"
    if spent.took is not None:
        said = f"{said}, {elapsed(spent.took)} waiting on the model"
    return f"{said}, estimated from published rates rather than billed"


def chosen_note(
    chosen: Choice | None,
    repository: str | None = None,
    worktree: Path | None = None,
    spent: Spent | None = None,
) -> Element:
    """
    What an existing session is on, as a fact rather than a control: it cannot be changed.

    Its own class rather than the picker's, and that is not tidying. The two used to look alike
    enough to share one, and once the picker became a page-filling block of cards they stopped
    being the same kind of thing at all: this is one line of faint text under a message box, and
    sharing a rule with a grid gave every session page a layout meant for the start page.

    The thinking level is named only when there is one to name. A session that said nothing about
    thinking is not a session set to some level called "default"; it is one that never raised the
    question, and printing a word for that would invent a setting nobody chose. The worktree is
    named on the same terms, and its absence means the same thing: no snapshots are being kept, so
    there is nothing a later fork could put back on disk.

    The session's total is here rather than on a rule because it is a fact about the whole
    conversation and the rules each speak for one turn. It is drawn only once something has been
    priced: a session whose models nobody publishes a price for says nothing about money, which is
    the same silence its cards keep, where `free` would be a claim nobody made.
    """
    if chosen is None:
        return span(cls="chosen")
    said = f"{chosen.endpoint} \N{MIDDLE DOT} {chosen.model}"
    if chosen.thinking is not None:
        said = f"{said} \N{MIDDLE DOT} thinking {name_of_thinking(chosen.thinking)}"
    return span(
        cls="chosen",
        children=[
            span(children=said),
            *(
                (
                    span(
                        cls="worktree",
                        # The repository is what a reader recognises and the worktree is where to
                        # point an editor, so one is shown and the other is there to be read. No
                        # worktree yet is an ordinary state rather than a missing one: the first
                        # pass makes it, so a session says where it works before it has worked.
                        attrs={
                            "title": f"This session's worktree: {worktree}"
                            if worktree is not None
                            else "This session works here once its first turn runs"
                        },
                        children=f"\N{MIDDLE DOT} {repository}",
                    ),
                )
                if repository is not None
                else ()
            ),
            *(
                (
                    span(
                        cls="spent",
                        # The time joins the counts in the title rather than the money on the line.
                        # This is one line of faint text under a message box and the figure a
                        # session is asked for is what it cost; how long it has spent waiting is
                        # worth having and not worth a fourth thing to read past.
                        attrs={"title": session_spend(spent)},
                        children=f"\N{MIDDLE DOT} {charged(spent.cost)}",
                    ),
                )
                if spent is not None and spent.cost is not None
                else ()
            ),
        ],
    )


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
    case, a fact read off the checkpoint that holds across however many renders it takes. The two
    are drawn alike because a reader is being told the same thing.
    """
    return span(
        cls="waiting",
        attrs={"role": "status", "aria-label": "working"},
        children=[span(), span(), span()],
    )


# How wide JSON is indented where a person reads it. Two, because the point of showing it is the
# shape, and a value nested four deep at four spaces is mostly margin in a column this narrow.
INDENT: Final = 2


def laid_out(said: str) -> str:
    """
    JSON laid out to be read, and anything else left exactly as it arrived.

    What a model hands a tool is JSON by construction, and a single line of it is where a reader
    has to count brackets to find the argument they came for. It is not *reliably* well-formed,
    though: `args_as_json_str` returns whatever the provider sent when the arguments arrived as a
    string, so a malformed call reaches here as that text. Showing it unchanged is the honest
    rendering, and it is the call a reader most needs to look at.

    Deliberately not used on what a call *returned*. That is whatever the tool produced - usually
    the contents of a file - so text that merely happens to parse as JSON would be reformatted, and
    a reader would be shown something other than what the model was handed.
    """
    try:
        return json.dumps(json.loads(said), indent=INDENT, ensure_ascii=False)
    except ValueError:
        return said


def tool_block(used: ToolUse, anchor: str, at: int) -> Element:
    """
    One call, folded, with what it was handed and what it gave back.

    Folded because a call's arguments and its output are context a reader reaches for rather than
    prose they read through, and a real `<details>` because that is what works with no script at
    all and what the dock's fold controls act on.

    The id is the panel's own plus this block's place in it, which is stable because a panel's
    blocks only ever grow at the end: a call keeps its place in the panel once made, whether or not
    it has come back yet. The script needs it to put a reader's unfolded calls back after a swap,
    since the server renders `open` for one state only and morphing removes an attribute the new
    markup does not carry.

    A call with no result is drawn open and working, which is what a call still out looks like
    while the turn that made it runs, and what a turn whose run ended between the call and its
    return looks like afterwards.
    """
    return details(
        cls="tool",
        attrs={"id": f"{anchor}-tool-{at}", "open": used.returned is None},
        children=[
            summary(
                children=[
                    span(cls="tool__name", children=used.tool),
                    # Beside the outcome rather than in the body, because how long a call ran is what
                    # a reader scanning a folded turn wants and the body is what they open when they
                    # want the rest. Absent while a call is still out: a figure there would have to
                    # count up, and what says a call is running is the working mark already beside it.
                    *(
                        (
                            span(
                                cls="tool__took",
                                attrs={"title": f"This call took {elapsed(used.took)}"},
                                children=elapsed(used.took),
                            ),
                        )
                        if used.took is not None
                        else ()
                    ),
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
                    dd(children=pre(children=laid_out(used.arguments))),
                    *(
                        ()
                        if used.returned is None
                        else (dt(children="returned"), dd(children=pre(children=used.returned.content)))
                    ),
                ],
            ),
        ],
    )


def written_block(kind: str, text: str) -> Element:
    """
    One block of rendered Markdown, carrying the Markdown it was rendered from.

    The attribute is what a copy button hands over, and it has to be here because the rendering is
    lossy in exactly the way a person copying cares about: the fences, the emphasis, the list markers
    and the table are all gone from the text of the page, and no reading of the rendered markup gets
    them back. Nothing else reads it - it is not a second copy of anything, since it is the same
    value this element was built from, put into the same render.

    Only the kinds that *are* Markdown. A tool's arguments and its return are shown verbatim already,
    so what is on the page is the source, and an attribute repeating it would be the second copy this
    one is not.
    """
    return div(cls=("block", kind), attrs={"data-markdown": text}, children=written(text))


def block_element(block: Block, anchor: str, at: int) -> Element:
    match block:
        case Prose(text=text):
            return written_block("block--text", text)
        case Steering(text=text):
            return written_block("block--text", text)
        case Reasoning(text=text):
            return written_block("block--thinking", text)
        case ToolUse():
            return div(cls=("block", "block--tool"), children=tool_block(block, anchor, at))
        case _ as unreachable:
            assert_never(unreachable)


def record_json(held: object) -> Element:
    """
    One panel's stored value, as the text the checkpoint holds it as.

    `ensure_ascii` off, because a conversation is prose: escaping every non-ASCII character turns a
    message somebody can read into one they have to decode, and this is served as UTF-8 either way.

    Text and not markup, which is the whole of what makes it safe to show. Everything in here was
    shaped by whatever reached the message box, and a node tree escapes a text child, so the raw
    record of a reply that contains a `<script>` renders as those characters.
    """
    return pre(cls="record__json", children=code(children=json.dumps(held, indent=INDENT, ensure_ascii=False)))


def missing_record(turn: int, at: int) -> Element:
    """What a request nothing was recorded for says, which is a fragment rather than a refusal page."""
    return p(cls="record__missing", children=f"Nothing is recorded for request {turn}.{at}.")


def record_fold(links: Links, session: str, turn: int, at: int) -> Element:
    """
    The `r{at}` marker on a rule, and the raw record of that request behind it.

    Fetched only when opened, because the transcript is re-rendered whenever a running turn records
    anything and the raw record is several times the size of the reading of it. `hx-preserve` is what
    keeps it open through those swaps, since the server renders it closed and a morph would otherwise
    shut it under the reader's hand. htmx reads that attribute off the *incoming* markup, so taking
    it off the live node proves nothing.

    `once` is safe here in a way it never was under a panel: a step's key is written once and never
    rewritten, so a request's record is settled the moment it exists, where a panel's record came out
    of `turn:{n}:messages` and did not exist until the turn ended.
    """
    return details(
        cls="tag",
        attrs={
            "id": f"tag-{turn}-{at}",
            "hx-preserve": True,
            "hx-get": links.to_request_record(session, turn, at),
            "hx-trigger": "toggle once",
            "hx-target": "find .record__json",
            "hx-swap": "outerHTML",
        },
        children=[
            summary(
                cls="tag__summary",
                attrs={"title": f"The {ordinal(at)} model request of turn {turn}"},
                children=span(cls="tag__at", children=f"r{at}"),
            ),
            pre(cls="record__json", children=code(children="\N{HORIZONTAL ELLIPSIS}")),
        ],
    )


def ordinal(at: int) -> str:
    """`0` as `first`, for a title that reads as a sentence rather than as an index."""
    names = ("first", "second", "third", "fourth", "fifth")
    return names[at] if at < len(names) else f"{at + 1}th"


def rule_element(
    links: Links,
    session: str,
    turn: int,
    asked: int | None = None,
    tree: str | None = None,
    spent: Spent | None = None,
    opens: bool = False,
) -> Element:
    """
    A line across the conversation where one round trip to the model began.

    A rule rather than a row on a panel, because everything on it is true of a *request* or of the
    turn around it, and a panel is neither. A request is a round trip and a panel is a run of one
    kind, so one response becomes as many panels as it has kinds of part: a marker hung on one of
    them attributed a round trip to a fraction of itself, and a turn's cost had nowhere to go at all.

    A rule per request, with the **turn rule** being the first one, which costs no new concept: every
    rule this transcript has ever drawn already stood at a request boundary, because a turn opens
    with its first request. What the first one carries besides is the turn's own facts - which turn
    it is, where the session may be forked from, and what the whole turn spent - and the later ones
    carry only their own request's.

    It is the fork's own place for a reason beyond tidiness. The branch point is *before* the forked
    turn's message, which is exactly where a turn rule sits, so the link names the position it acts
    on instead of sitting inside the first thing that comes after it. That also retires the hover: a
    control revealed by pointing at a panel does not exist on a touch screen, and forking is the
    only way a session changes its mind.

    A turn rule **names its turn**, and that is what makes it readable rather than decorative. It
    sits directly under the last panel of the turn before it, so a bare row of figures there reads as
    a footer summarising what is above it, which is the opposite of what it says: these are the
    counts for the turn that starts below. `#1` against the `#1.0` and `#1.1` on the panels under it
    settles the direction, and it doubles as the permalink to the boundary the fork acts on.

    `rule--turn` is what the dock's turn arrows step, so that column keeps stepping turns now that
    there are rules between them as well as before them.
    """
    return div(
        cls=("rule", "rule--turn" if opens else None),
        attrs={
            "id": f"rule-{turn}" if opens else f"rule-{turn}-{asked}",
            "data-turn": str(turn),
        },
        children=[
            *(
                (a(cls="rule__at", attrs={"href": f"#rule-{turn}", "title": f"Turn {turn}"}, children=f"#{turn}"),)
                if opens
                else ()
            ),
            *(
                (
                    a(
                        cls="rule__fork",
                        attrs={"href": links.to_fork_form(session, turn), "title": f"Fork from turn {turn}"},
                        children="fork",
                    ),
                )
                if opens and session
                else ()
            ),
            # Only where there is a session to ask, which the gallery's pages are rendered without: a
            # control pointed at no conversation is a dead button rather than an offer, the same
            # reason the fork link is conditional.
            *((record_fold(links, session, turn, asked),) if session and asked is not None else ()),
            *(
                (
                    span(
                        cls="rule__tree",
                        # Short here and whole in the title: a hash is read to tell two apart and to
                        # be typed at git, and the first several characters do the first job in a
                        # tenth of the width. The second is what a reader copies, so it stays intact.
                        attrs={
                            "title": (
                                f"The worktree this turn started on: {tree}"
                                if opens
                                else f"The worktree this request was made against: {tree}"
                            )
                        },
                        children=tree[:SHORT_HASH],
                    ),
                )
                if tree is not None
                else ()
            ),
            span(cls="rule__span"),
            *(
                spend_element(spent, f"Turn {turn}" if opens else f"Request {turn}.{asked}")
                if spent is not None
                else ()
            ),
        ],
    )


def panel_element(links: Links, session: str, panel: Panel) -> Element:
    """
    One run of one kind of thing, with the facts about it above it.

    `data-kind` and `data-side` are the whole of what the chrome needs to know: the key filters by
    kind, the dock's flanking arrows step by side, and the stylesheet draws the edge from the same
    attribute. Nothing has to keep a list of selectors in step with a list of kinds.

    What a panel says is what is *in* it, and nothing about the turn or the request around it. The
    worktree, the fork, what was spent and the raw record are all facts about the exchange rather
    than about any one run of blocks, so they are on the rules between them. See `rule_element`.

    Nothing here draws the copy buttons, and that is not an omission. One of them sits inside a
    fenced block, which is markup the Markdown renderer produced and this has no node to reach into,
    so seating them is `mainplate.js`'s - and a button in the markup for the panel beside a seated one
    for the code in it would be two mechanisms for one thing.
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
                        children=f"#{panel.label}",
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
    The conversation, and whether it is still waiting on the rest of it.

    Markup and nothing else: it carries no `hx-` attribute at all, because it neither asks for
    itself nor decides when to. The page's one connection sends this region whenever the session
    records anything, so what used to be a trigger the region carried, cancelled by its own absence
    once a turn was answered, is now a message that simply stops arriving.

    That is a real simplification rather than a move. A trigger on a region that is itself replaced
    has to be got exactly right (`every` and not `load`, since morphing keeps the element and a
    `load` poll would fire once and wait forever); a region with no trigger has nothing to get
    wrong.

    The rules are drawn from the panels rather than carried beside them, which is what keeps them
    from disagreeing: a turn is a run of consecutive panels sharing a `turn` and a request is a run
    of consecutive panels sharing an `asked`, so the one walk that groups the panels is the one place
    that decides where either begins.

    A turn's own rule takes its tree from the turn's first panel rather than from request 0, which
    holds the same key. The panel has it a request earlier: `turn:{n}:tree:0` is written *before* the
    model is asked, so a turn whose first answer has not landed yet still says what it started on.
    """
    drawn: list[Element] = []
    for turn, panels in groupby(said.panels, key=lambda panel: panel.turn):
        within = tuple(panels)
        asking = said.requests.get(turn, ())
        drawn.append(
            rule_element(
                links,
                session,
                turn,
                asked=0 if asking else None,
                tree=within[0].tree,
                spent=said.spent.get(turn),
                opens=True,
            )
        )
        at = 0
        for panel in within:
            if panel.asked is not None and panel.asked != at:
                at = panel.asked
                if at < len(asking):
                    drawn.append(
                        rule_element(links, session, turn, asked=at, tree=asking[at].tree, spent=asking[at].spent)
                    )
            drawn.append(panel_element(links, session, panel))
    if said.awaiting and stalled is None:
        drawn.append(waiting_panel())
    if stalled is not None:
        drawn.append(p(cls="stalled", children=stalled))
    return div(
        cls="transcript",
        attrs={"id": TRANSCRIPT_ID},
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


def shelf_card() -> Element:
    """
    Text written and not sent, kept for this conversation and pulled back into the box on demand.

    What the shelf *holds* is here, in the rail, for the reason everything else there is: the rail is
    outside the region that swaps, so a slot is never rebuilt under the reader's hand while a turn
    records. What *fills* it is not here - `Keep` sits beside Send, because it acts on the box and a
    control belongs next to the thing it acts on, which is the same reasoning that keeps a fork link
    on the rule it forks at.

    Its list is rendered by the script and by nothing else, because what it holds is a reader's own
    decision and the server is never told any of it. That is the rail's standing bargain rather than
    an exception - the search field does nothing without the script either - and the page still
    renders, posts and folds with the file absent.

    An empty state rather than a hidden card, so somewhere to put an unsent paragraph is discoverable
    before there is one in it.
    """
    return div(
        cls="shelf",
        attrs={"aria-label": "Shelf"},
        children=[
            span(cls="shelf__title", children="shelf"),
            ul(cls="shelf__list", attrs={"data-shelf": "list"}),
            p(cls="shelf__empty", attrs={"data-shelf": "empty"}, children="Nothing kept yet."),
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

    Three columns of arrows, at the two granularities a reader moves in: the left one steps whole
    turns, landing on the rule that opens each, the middle one steps every panel the key leaves in
    play, and the right one steps only what the model produced.

    Every arrow says what it steps over rather than being told apart by what it lacks. A button
    identified as "the one with no side" stops being identifiable the instant a second kind of stop
    exists, which is markup becoming ambiguous with nothing failing to say so.

    The left column used to seek the person's own panels, and stepping turns is what that *was*:
    there is exactly one message per turn, so the two columns would visit the same positions and
    differ only in where they stopped. Two controls answering one question is the thing this console
    removes wherever it finds it, so the coarse move now lands on the boundary, where the fork link
    and what the turn cost are, rather than a few lines below it.
    """
    return div(
        cls="dock",
        children=[
            div(
                cls="dock__nav",
                children=[
                    dock_button(
                        "dock__btn--turn",
                        "Previous turn",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-stop": "turn"},
                    ),
                    dock_button(None, "Previous panel", "\N{UPWARDS ARROW}", {"data-step": "-1", "data-stop": "panel"}),
                    dock_button(
                        "dock__btn--assistant",
                        "Previous panel from the model",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-stop": "panel", "data-side": "model"},
                    ),
                    dock_button(
                        "dock__btn--turn",
                        "Next turn",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-stop": "turn"},
                    ),
                    dock_button(None, "Next panel", "\N{DOWNWARDS ARROW}", {"data-step": "1", "data-stop": "panel"}),
                    dock_button(
                        "dock__btn--assistant",
                        "Next panel from the model",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-stop": "panel", "data-side": "model"},
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
            shelf_card(),
            theme_card(),
        ],
    )


def naming() -> VoidElement:
    """
    What to call this session, offered above the box and safe to ignore.

    Optional, and the placeholder says what happens if you leave it: a session with no name given is
    named after its first message, exactly as every session was before this existed. So the field
    adds a choice without adding a step, which is the only way it earns a place above the thing
    somebody actually came here to type.

    A plain input with no `hx-` attribute on it, because it is submitted with the message rather
    than being a question of its own: nothing exists to name until the form posts.
    """
    return input_(
        cls="composer__name",
        attrs={
            "type": "text",
            "name": TITLE_FIELD,
            "maxlength": str(TITLE_LENGTH),
            "placeholder": "Name this session (or leave it to the first message)",
            "aria-label": "Session name",
        },
    )


type Placed = Element | VoidElement | None
"""One thing a caller hands the composer to put above or below the box, or nothing at all."""


def sending_option(name: str, saying: str, attrs: Mapping[str, str | int | bool | None]) -> Element:
    """
    One answer to what happens to what you typed, as a row in the menu.

    What it does is written under its name rather than left to a `title`, because a control somebody
    opened a menu to find is one they have not used before, and a tooltip is not where anybody looks
    first.
    """
    return button(
        cls="sender__option",
        attrs=attrs,
        children=[
            span(cls="sender__option-name", children=name),
            span(cls="sender__option-said", children=saying),
        ],
    )


def sending_control(refusing: bool, continuing: bool, returning: bool = False, answering: bool = False) -> Element:
    """
    What happens to what you typed: send it, and everything else folded behind a caret beside it.

    **One question, so one control.** Sending it here, asking it in a new session, and setting it
    aside unsent are answers to "what do I do with this", and answering one question in two places is
    what this console removes wherever it finds it - which is exactly what a `Keep` button standing
    beside `Send` had become. It is also the only shape that stays affordable: each further answer
    costs a line in a menu nobody has to open, where each further button costs a slot in the row above
    the message box, which is the row a phone has least of.

    **`Send` does not say whether it steers, because it cannot know and neither can the reader.** This
    page was rendered from a checkpoint that has moved since, so a `Steer` button beside `Send` asked
    somebody to choose between two moments against a state that no longer held, and the server then
    honoured a decision about the wrong turn. `Service.send` decides instead, reading the record and
    writing to it in one breath.

    What is left for the menu is the one thing the record cannot settle: wanting to be answered
    *after* the reply that is coming. It is offered only while something is being answered, since
    with nothing running it is what `Send` already does.

    The menu is ordered by how far the text travels: waiting for the next turn keeps it here and
    merely later, an `Aside` is a step out you mean to come back from, a `Fork` is a conversation of
    its own, going back reaches the one this came out of, and `Keep` sends it nowhere at all and is
    under a rule for that reason.

    `returning` is offered by any fork rather than only an aside, because what it needs is
    `Origin.session` and every fork has one. An aside is the case it is *for*, and gating it on the
    flag would be inventing a restriction to make the flag look load-bearing.

    **Everything that *sends* works with no script.** The fold is a `<details>`, which is how
    everything else here folds, and each destination posts its own `name`/`value` the way the browser
    has always submitted a named button. `Keep` is the exception and is honestly the odd one out: the
    shelf is `localStorage`, so that row does nothing with `mainplate.js` absent, exactly as the
    shelf's own card in the rail shows nothing then.

    It deliberately does *not* switch what the primary button does, which is where GitHub's version of
    this control goes further. Remembering a choice would mean a button labelled `Send` that forks,
    which is the one failure a control like this can have that nobody notices until after it has
    happened; here what a button says is always what it does.

    With no conversation yet there is no menu, only Send: nothing to fork from, and no session for a
    shelf to belong to.
    """
    send = button(
        # The key is named on the button because otherwise nothing on the page says it exists, and a
        # shortcut nobody can find is one nobody uses.
        attrs={"type": "submit", "disabled": refusing, "title": "Shift-Enter"},
        children="Send",
    )
    if not continuing:
        return send
    return div(
        cls="sender",
        children=[
            send,
            details(
                cls="sender__more",
                children=[
                    summary(
                        cls="sender__caret",
                        attrs={"aria-label": "What else to do with this", "title": "What else to do with this"},
                        children="\N{BLACK DOWN-POINTING SMALL TRIANGLE}",
                    ),
                    div(
                        cls="sender__menu",
                        children=[
                            *(
                                (
                                    sending_option(
                                        "Wait for the next turn",
                                        "Queue it behind the reply that is coming instead of putting"
                                        " it to the model now",
                                        {
                                            "type": "submit",
                                            "disabled": refusing,
                                            "name": DISPOSITION_FIELD,
                                            "value": Disposition.NEXT.value,
                                        },
                                    ),
                                )
                                if answering
                                else ()
                            ),
                            sending_option(
                                "Aside",
                                "Step out into a side conversation you mean to come back from",
                                {
                                    "type": "submit",
                                    "disabled": refusing,
                                    "name": DISPOSITION_FIELD,
                                    "value": Disposition.ASIDE.value,
                                },
                            ),
                            sending_option(
                                "Fork",
                                "Ask it in a new session carrying this whole conversation",
                                {
                                    "type": "submit",
                                    "disabled": refusing,
                                    "name": DISPOSITION_FIELD,
                                    "value": Disposition.FORK.value,
                                },
                            ),
                            *(
                                (
                                    sending_option(
                                        "Back to where this came from",
                                        "Send it to the conversation this one was forked out of",
                                        {
                                            "type": "submit",
                                            "disabled": refusing,
                                            "name": DISPOSITION_FIELD,
                                            "value": Disposition.PARENT.value,
                                        },
                                    ),
                                )
                                if returning
                                else ()
                            ),
                            sending_option(
                                "Keep",
                                "Put it on the shelf, unsent, and clear the box",
                                {"type": "button", "disabled": refusing, "data-shelf": "keep"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def composer(
    action: str,
    beneath: Placed,
    *,
    live: bool,
    refusing: bool = False,
    continuing: bool = False,
    returning: bool = False,
    answering: bool = False,
    above: Placed = None,
    identified: str | None = None,
) -> Element:
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
    endpoint is gone, which is one more thing to explain and nothing gained.

    The reset is on `after:swap` rather than on `after:request`, so the box empties when the
    conversation on screen has actually taken the message rather than when the request left.

    `hx-indicator` names what is shown while the post is in flight, which is a different thing
    from the working dots in the transcript: this one says *your message has not landed yet*, and
    it is over in a round trip. The one in the transcript says the model has not answered yet, and
    is read off the checkpoint rather than off a request.

    `identified` is what the picker's controls name to reach this form from outside it, and is given
    only on the page that has one: an id nothing points at would say there is something here to
    associate with.

    `continuing` is whether this composer is in a conversation that already exists, which is what
    decides the menu: there is nothing to fork from and no session for a shelf to belong to until
    there is one. Its own argument rather than read off `live` even though the two coincide today,
    because they mean different things - `live` is whether the answer swaps or navigates - so tying
    them together would be one of the two silently deciding the other.
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
        attrs={"method": "post", "action": action, "id": identified, **driving},
        children=[
            above,
            div(
                cls="row",
                children=[
                    # `rows` is the floor only where `field-sizing` is not supported: the box sizes
                    # itself from what is typed, and a browser that can do that ignores `rows`
                    # entirely. See the growth rule in `mainplate.css`.
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
                    sending_control(refusing, continuing, returning, answering),
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
    reachable: Reachable,
    pane: Sequence[Element],
    aside_rail: Iterable[Element] = (),
) -> Element:
    return div(
        cls="shell",
        children=[
            sidebar(links, listed, showing, reachable),
            # No banner over the pane, and that is room rather than an omission: the console's own
            # name was a row on every page saying nothing the tab title does not, and on a phone it
            # was a twentieth of the screen spent on it. What names the page is `<title>`, and what
            # gets somebody back to the start is the session list, which is always on screen.
            main(children=[*pane]),
            *aside_rail,
        ],
    )


def start_page(
    links: Links,
    listed: tuple[Session, ...],
    catalogue: Catalogue,
    reachable: Reachable,
    reference: Reference | None = None,
) -> str:
    """
    Where a session begins: what to answer it with, and the box that starts it.

    The choosing fills the page and the box sits under it, which is the opposite of what a session
    page does and is right for the same reason. On a session page the conversation is the content
    and the box is how you add to it. Here there is no conversation, and what somebody is actually
    doing is deciding what they are about to talk to; a page that gave that a row of selects under
    the message box was answering the wrong question first.

    There is no transcript element on this page at all. An empty one was a region with nothing in it
    saying "ask it something", which is the message box's job to say and the message box says it
    better by being the thing you type into.

    Nothing is created until something is said, which is why this page has no id in its URL. A
    session that existed with nothing in it would be a row in the list nobody can name and nobody
    asked for, and it would have to be recorded on an endpoint chosen for it rather than by anybody.
    """
    return document(
        links,
        NEW_SESSION,
        shell(
            links,
            listed,
            showing=None,
            reachable=reachable,
            pane=[
                div(cls="setup", children=picker(links, catalogue, reachable, reference)),
                composer(links.to_start(), None, live=False, above=naming(), identified=CHOOSING_ID),
            ],
        ),
    )


def stalled_by(showing: Conversation) -> str | None:
    """
    Why this session cannot be answered, or nothing at all when it can.

    One sentence naming the endpoint, because that is the only thing a person can act on: the
    endpoint was configured when the session started, so putting it back in the configuration file
    is what makes the conversation continue exactly where it stopped.

    It names the endpoint and not the model on purpose. A model missing from the picker does not
    stop a session, since an endpoint routes more ids than it advertises, so saying so here would
    tell somebody to fix something that is not broken.
    """
    if showing.answerable or showing.chosen is None:
        return None
    return (
        f"This session was started on endpoint {showing.chosen.endpoint!r}, which the configuration "
        f"no longer declares. Put it back to carry on, or start a new session."
    )


def session_page(links: Links, listed: tuple[Session, ...], showing: Conversation, reachable: Reachable) -> str:
    stalled = stalled_by(showing)
    return document(
        links,
        showing.session.title or UNTITLED,
        shell(
            links,
            listed,
            showing=showing.session.id,
            reachable=reachable,
            pane=[
                transcript_region(links, showing.session.id, showing.said, stalled),
                composer(
                    links.to_say(showing.session.id),
                    chosen_note(showing.chosen, showing.repository, showing.worktree, showing.said.total),
                    live=True,
                    refusing=stalled is not None,
                    # Only where there is something to act on. Forking an empty conversation makes a
                    # session identical to starting one, so the offer would be a second way to do
                    # what the sidebar's own button already does.
                    continuing=showing.said.turns > 0,
                    # Any fork can send back to what it came out of; an aside is the case it is for.
                    returning=showing.session.forked is not None,
                    # Only while something is actually being answered: a steer into a turn nobody is
                    # running would sit in the store unread, which is a message on the floor.
                    answering=showing.said.answering is not None,
                ),
            ],
            # Only where there is a conversation to navigate. On the page where a session does not
            # exist yet every control in it would be pointed at an empty transcript, which is a
            # row of dead buttons rather than an offer.
            aside_rail=[rail()],
        ),
        session=showing.session.id,
        forked_from=showing.session.forked.session if showing.session.forked is not None else None,
    )


def attachable(showing: Conversation, reachable: Reachable) -> Reachable | None:
    """
    What a fork of this session may work in, or nothing at all once the question is already answered.

    A fork attaches a repository or inherits one; it never swaps. `None` rather than an empty set of
    choices, and the difference is load-bearing now that the worktree group holds more than
    repositories: empty means "no forge reaches anything", which still leaves two answers worth
    offering, where `None` means the question is settled and the control would be a lie about what
    the page does. Answering it here is what keeps `picker` a rendering rather than a place that
    knows the rule.
    """
    settled = showing.chosen is not None and showing.chosen.repository is not None
    return None if settled else reachable


def inheriting(at: int, asking: bool) -> str:
    """
    What a fork from `at` would carry, said in turns.

    Stated rather than left to the transcript below, because "fork at turn 3" has two readings and
    the wrong one silently throws away the turn somebody meant to keep. The cases are written out
    because a single sentence with a range in it reads as nonsense at both ends: forking at turn 1
    would say "turns 0 to 0", and at turn 0 there is no range at all.

    `asking` is whether there is a turn to re-ask. Forking one of a conversation's turns offers
    that turn's message back, so the fork *asks it again* on the new model; forking the end has
    nothing to re-ask and simply waits.
    """
    carried = "Carries nothing" if at == 0 else "Carries turn 0" if at == 1 else f"Carries turns 0 to {at - 1}"
    return f"{carried}, then asks turn {at} again." if asking else f"{carried}, then waits for turn {at}."


def branching_files(at: int, repository: str) -> str:
    """
    What a fork does to the files, said before somebody finds out afterwards.

    The turns a fork carries are visible on the page below it; what happens to the working files is
    not visible anywhere, and it is the half with work in it. A branch gets *its own* worktree
    checked out at the tree the forked turn started on, so anything committed or edited after that
    turn is simply not in it, and its scratch directory starts empty.

    Nothing is destroyed and the sentence says so, because "restores the worktree" reads as an
    action on the conversation you are looking at. The parent keeps its worktree and its scratch
    exactly as they are: a fork is a new session beside this one, never this one moved backwards.
    """
    return (
        f"Gets a fresh worktree of {repository} at the files turn {at} started on, and an empty "
        f"scratch directory. This conversation's own files are left as they are."
    )


def fork_page(
    links: Links,
    listed: tuple[Session, ...],
    showing: Conversation,
    at: int,
    catalogue: Catalogue,
    reachable: Reachable,
    reference: Reference | None = None,
) -> str:
    """
    What a branch from one turn would be, and the one control that may answer differently.

    It shows what carries over rather than only asking a question, because "fork at turn 3" is a
    sentence with two readings and the wrong one silently discards work. What is kept is stated in
    turns, and the transcript beneath is the same one the session page draws, cut to the prefix
    the branch inherits.

    The picker is the *session's* choice rather than the configured default, so the common branch -
    go back and try that turn again on the same model - is the one that needs nothing changed. A
    fork is the one moment a choice may differ, and it is deliberately not the moment it must.
    """
    kept = tuple(panel for panel in showing.said.panels if panel.turn < at)
    asked = showing.said.asked_at(at)
    return document(
        links,
        f"Fork {showing.session.title or UNTITLED}",
        shell(
            links,
            listed,
            showing=showing.session.id,
            reachable=reachable,
            pane=[
                form(
                    cls="forking",
                    attrs={"method": "post", "action": links.to_fork(showing.session.id), "id": CHOOSING_ID},
                    children=[
                        h1(children="Fork this conversation"),
                        p(cls="forking__kept", children=inheriting(at, asked is not None)),
                        # Only where there is a repository to say it about. A session working in no
                        # files has no worktree and no scratch, so the sentence would be describing
                        # something that does not exist for it.
                        *(
                            (p(cls="forking__files", children=branching_files(at, showing.repository)),)
                            if showing.repository is not None
                            else ()
                        ),
                        input_(attrs={"type": "hidden", "name": "at", "value": str(at)}),
                        # The turn's own message, back in a box you can edit. Without it a fork is
                        # a conversation that stops where you wanted it to continue, and seeing the
                        # same turn answered differently would mean retyping the question first,
                        # which is a different question by the time you have retyped it.
                        textarea(
                            attrs={
                                "name": "prompt",
                                "rows": 3,
                                "autofocus": True,
                                "placeholder": "Say something" if asked is None else None,
                                "aria-label": "Message",
                            },
                            children=asked or "",
                        ),
                        # A repository is offered only to a fork of a session that has none.
                        # One already in a repository inherits it, so there is nothing to choose;
                        # one in none may pick a repository up here, which is the ordinary shape
                        # of having thought something through and then going to work on it.
                        picker(links, catalogue, attachable(showing, reachable), reference, showing.chosen),
                        div(
                            cls="forking__act",
                            children=[
                                button(attrs={"type": "submit"}, children="Fork" if asked is None else "Fork and ask"),
                                a(
                                    cls="forking__back",
                                    attrs={"href": links.to_session(showing.session.id)},
                                    children="Back to the conversation",
                                ),
                            ],
                        ),
                    ],
                ),
                div(
                    cls="transcript",
                    attrs={"id": TRANSCRIPT_ID},
                    children=[panel_element(links, "", panel) for panel in kept]
                    or p(cls="empty", children="Nothing carries over."),
                ),
            ],
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
