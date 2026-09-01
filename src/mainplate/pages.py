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
from collections.abc import Sequence
from dataclasses import dataclass
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
from mainplate.catalogue import Offering
from mainplate.catalogue import grouped
from mainplate.conversation import REPOSITORY_FIELD
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import Block
from mainplate.conversation import Kind
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reasoning
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.forge import Reachable
from mainplate.markup import as_markup
from mainplate.reference import Cost
from mainplate.reference import Described
from mainplate.reference import Reference
from mainplate.reference import describe
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

THINKING_ID: Final = "thinking"

REPOSITORY_ID: Final = "repository"

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

# What the link that starts one is called, and what the tab says on the page where a session does
# not exist yet. "Session" rather than "workspace", which is the other word for this and is already
# taken: a session's *workspace* is the git worktree it works in, so calling the session one too
# would make "a workspace's workspace" a sentence somebody has to parse.
NEW_SESSION: Final = "New session"

# What a session with no name of its own is called. Unreachable today, because every session is
# named when it is created - after the box, or after its first message - and nothing renames one.
# Kept as the answer to a row that has somehow lost its title, which is a database somebody edited
# rather than a state this console produces.
UNTITLED: Final = "Untitled"


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
    panel_record: Reversible
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

    def to_panel_record(self, session: str, turn: int, at: int) -> str:
        """
        What the checkpoint holds behind one panel, addressed the way the panel itself is.

        Path segments rather than a query string, because a panel's identity *is* the pair: the
        anchor a permalink is built on is already `turn` and `at`, so this is the same address in
        another shape rather than a filter over something.
        """
        return url_for(self.panel_record, {"session": session, "turn": turn, "at": at})

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


def document(links: Links, heading: str, children: Element, session: str | None = None) -> str:
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
                        attrs={"data-session": session},
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
                                        *(
                                            (span(cls="from", children=f"\N{RIGHTWARDS ARROW}{session.forked.turn}"),)
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
        attrs={"data-provider": facts.provider},
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


def model_cards(models: Sequence[Listed], reference: Reference | None, chosen: str | None = None) -> Element:
    """
    The models one endpoint offers, as the cards the form submits one of.

    Its own element with a stable id, because changing the endpoint replaces exactly this and
    nothing else on the page. An endpoint always offers at least one model (discovery refuses a
    endpoint that lists none), so this is never an empty group nobody can submit.

    Grouped by provider, because a gateway fronting several vendors answers with seventy entries and
    an ungrouped wall of seventy cards is worse than the ungrouped list of seventy it replaced. The
    value is the id the request will name; the name is whatever the endpoint calls it.
    """
    picked = chosen if any(chosen == model.id for model in models) else models[0].id
    return div(
        cls="models",
        attrs={"id": MODEL_ID, "role": "radiogroup", "aria-label": "Model"},
        children=[
            section(
                cls="models__provider",
                children=[
                    h2(cls="models__heading", children=provider),
                    div(
                        cls="models__grid",
                        children=[model_card(describe(model, reference), chosen=model.id == picked) for model in found],
                    ),
                ],
            )
            for provider, found in grouped(models)
        ],
    )


def endpoint_card(links: Links, offering: Offering, chosen: bool) -> Element:
    """
    One endpoint as where it actually points, rather than as a name somebody chose for it.

    The endpoint and the wire are on it because they are what distinguishes two endpoints that
    otherwise read alike, and on this machine that is the ordinary case rather than the exotic one:
    one gateway answers both wires, so a VM declares the same host twice and the *only* thing
    telling those two rows apart is the word `anthropic` or `openai` and the `/v1` on the end.

    htmx sends a triggering input's own value, so the `hx-get` needs no interpolation: choosing a
    endpoint asks for that endpoint's models and replaces the cards beside it. Without a browser the
    form still posts, carrying whatever models the page was rendered with, and the handler refuses a
    pair nothing offers.

    `outerHTML` and deliberately not the `outerMorph` the transcript uses. Morphing preserves what a
    control already holds, which is exactly right for a conversation being reread and exactly wrong
    here: the whole point of this swap is that the model list is now a *different* list, and a merge
    would keep a card the new endpoint may not even offer.
    """
    return label(
        cls="endpoint",
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


def thinking_select(chosen: ThinkingLevel | None) -> Element:
    """
    How hard to think, as a plain select with no cascade behind it.

    Unlike the model list this is the same everywhere, because it is a property of the request
    rather than of the endpoint: every level is offered against every endpoint, and a model that
    cannot reason refuses or ignores it on the turn. That is the same stance the model id gets, and
    for the same reason - the provider's own answer about what it supports is the authoritative
    one, and gating here would hide a level that in fact works.
    """
    return select(
        attrs={"id": THINKING_ID, "name": THINKING_FIELD, "form": CHOOSING_ID, "aria-label": "Thinking"},
        children=[
            option(attrs={"value": name, "selected": level == chosen}, children=name)
            for name, level in THINKING_CHOICES
        ],
    )


NO_REPOSITORY: Final = ""


def repository_select(reachable: Reachable, chosen: str | None) -> Element:
    """
    Which repository a session works in, offered only where there is one to offer.

    Nothing at all when no forge reaches anything, rather than an empty or disabled select: off
    exe.dev, or on a machine with no integrations attached, this console is what it was before
    repositories existed and a control for a choice with no options is a question nobody can
    answer.

    "No repository" is always offered even when there are some, because a conversation that is not
    about code is an ordinary thing to want and picking a repository for it would give the agent
    files nobody meant it to have.

    Labels come from `Reachable`, which qualifies a row only where two would otherwise read the
    same: the same repository can be attached twice with different rights, and choosing between two
    identical rows is guessing.
    """
    return select(
        attrs={"id": REPOSITORY_ID, "name": REPOSITORY_FIELD, "form": CHOOSING_ID, "aria-label": "Repository"},
        children=[
            option(attrs={"value": NO_REPOSITORY, "selected": chosen is None}, children="no repository"),
            *(
                option(attrs={"value": repository.id, "selected": repository.id == chosen}, children=label)
                for repository, label in reachable.labelled()
            ),
        ],
    )


def picker(
    links: Links,
    catalogue: Catalogue,
    reachable: Reachable,
    reference: Reference | None,
    chosen: Choice | None = None,
) -> Element:
    """
    Everything a session is decided by, laid out as the question it actually is.

    One block rather than a row of selects, because choosing a model is the one real decision on
    this page and a row of selects made it look like a footnote to the message box. The endpoints
    come first because the model list depends on which one is picked; the models are the body of it;
    the two settings that apply whatever you picked sit under them.

    `chosen` is what the controls start on, defaulting to the configured default for a new session.
    A fork passes the parent's own choice instead, so continuing on the same model is the path that
    needs nothing touched: the fork exists to let the choice change, not to require it.

    Whether a repository *can* be chosen here is the caller's answer, given as what it says is
    reachable: a fork of a session already in a repository is handed nothing, because it inherits
    that one and a control that could not be honoured would be a lie about what the page does.

    A choice naming an endpoint the catalogue no longer has falls back to the default rather than
    rendering a picker with nothing selected. That is the same case `stalled_by` explains on the
    session page, and here there is a sensible thing to show.
    """
    starting = chosen if chosen is not None and chosen.endpoint in catalogue.offered else catalogue.default
    return div(
        cls="picker",
        children=[
            section(
                cls="picker__part",
                children=[
                    h2(cls="picker__legend", children="Endpoint"),
                    endpoint_cards(links, catalogue, starting.endpoint),
                ],
            ),
            section(
                cls=("picker__part", "picker__part--models"),
                children=[
                    h2(cls="picker__legend", children="Model"),
                    model_cards(catalogue.offered[starting.endpoint].models, reference, starting.model),
                ],
            ),
            div(
                cls="picker__settings",
                children=[
                    span(cls="picker__label", children="Thinking"),
                    thinking_select(starting.thinking),
                    *(
                        (
                            span(cls="picker__label", children="Working in"),
                            repository_select(reachable, starting.repository),
                        )
                        if reachable.repositories
                        else ()
                    ),
                ],
            ),
        ],
    )


def chosen_note(chosen: Choice | None, repository: str | None = None, workspace: Path | None = None) -> Element:
    """
    What an existing session is on, as a fact rather than a control: it cannot be changed.

    Its own class rather than the picker's, and that is not tidying. The two used to look alike
    enough to share one, and once the picker became a page-filling block of cards they stopped
    being the same kind of thing at all: this is one line of faint text under a message box, and
    sharing a rule with a grid gave every session page a layout meant for the start page.

    The thinking level is named only when there is one to name. A session that said nothing about
    thinking is not a session set to some level called "default"; it is one that never raised the
    question, and printing a word for that would invent a setting nobody chose. The workspace is
    named on the same terms, and its absence means the same thing: no snapshots are being kept, so
    there is nothing a later fork could put back on disk.
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
                        cls="workspace",
                        # The repository is what a reader recognises and the worktree is where to
                        # point an editor, so one is shown and the other is there to be read. No
                        # worktree yet is an ordinary state rather than a missing one: the first
                        # pass makes it, so a session says where it works before it has worked.
                        attrs={
                            "title": f"This session's worktree: {workspace}"
                            if workspace is not None
                            else "This session works here once its first turn runs"
                        },
                        children=f"\N{MIDDLE DOT} {repository}",
                    ),
                )
                if repository is not None
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
    case, a fact read off the checkpoint that holds however many polls it takes. The two are drawn
    alike because a reader is being told the same thing.
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
    """What a panel nothing was recorded for says, which is a fragment rather than a refusal page."""
    return p(cls="record__missing", children=f"Nothing is recorded for panel {turn}.{at}.")


def record_element(links: Links, session: str, panel: Panel) -> Element:
    """
    The disclosure that shows what the checkpoint actually holds behind this panel.

    Closed and unfetched until somebody asks, because the transcript around it is swapped once a
    second while a turn is in flight and the raw record is several times the size of the reading of
    it. `once` is safe rather than merely cheap: a panel exists only once the value behind it has
    stopped changing, so what comes back is settled and there is nothing to ask again for.

    `hx-preserve` is what makes that hold through a poll, and it is load-bearing rather than
    decorative: the server renders this closed, so a morph over the region takes the `open`
    attribute back off and shuts the disclosure under the reader's hand once a second. Preserved,
    the swap steps over the element and leaves it as they left it.

    htmx reads the attribute off the *incoming* markup rather than off the element on screen, which
    is worth knowing before trying to check this: taking it off the live node proves nothing,
    because the next response puts it back.
    """
    return details(
        cls="record",
        attrs={
            "id": f"{panel.anchor}-record",
            "hx-preserve": True,
            "hx-get": links.to_panel_record(session, panel.turn, panel.at),
            "hx-trigger": "toggle once",
            "hx-target": "find .record__json",
            "hx-swap": "outerHTML",
        },
        children=[
            summary(cls="record__summary", children="recorded"),
            pre(cls="record__json", children=code(children="\N{HORIZONTAL ELLIPSIS}")),
        ],
    )


def panel_element(links: Links, session: str, panel: Panel) -> Element:
    """
    One run of one kind of thing, with the facts about it above it.

    `data-kind` and `data-side` are the whole of what the chrome needs to know: the key filters by
    kind, the dock's flanking arrows step by side, and the stylesheet draws the edge from the same
    attribute. Nothing has to keep a list of selectors in step with a list of kinds.

    Only a person's panel offers a branch, and that is the whole of where a session may be forked.
    It is not a simplification: what a fork has to hand the next model is a conversation with no
    half-finished exchange in it, and the boundary between one turn and the next is the only place
    a conversation is in that state. Inside a turn there is a call awaiting its result, or
    reasoning signed by the model that produced it, and neither survives being handed to another.
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
                    *(
                        (
                            span(
                                cls="panel__tree",
                                attrs={"title": f"The worktree this turn started on: {panel.tree}"},
                                children=panel.short_tree,
                            ),
                        )
                        if panel.short_tree is not None
                        else ()
                    ),
                    *(
                        (
                            a(
                                cls="panel__fork",
                                attrs={
                                    "href": links.to_fork_form(session, panel.turn),
                                    "title": f"Fork from turn {panel.turn}",
                                },
                                children="fork",
                            ),
                        )
                        if panel.kind == "person" and session
                        else ()
                    ),
                    a(
                        cls="panel__anchor",
                        attrs={"href": f"#{panel.anchor}"},
                        children=f"#{panel.label}",
                    ),
                ],
            ),
            *(block_element(block, panel.anchor, at) for at, block in enumerate(panel.blocks)),
            # Only where there is a session to ask, which the gallery's pages are rendered without:
            # a control pointed at no conversation is a dead button rather than an offer, which is
            # the same reason the fork link is conditional above.
            #
            # And only once what is behind the panel has stopped changing. A panel of the turn in
            # flight is read from that turn's steps, and `sourced_at` answers out of its messages,
            # which are not written until the turn ends: offering the disclosure there would fetch
            # nothing, once, and keep the nothing. It appears when the turn lands.
            *((record_element(links, session, panel),) if session and panel.settled else ()),
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
    """
    drawn: list[Element] = [panel_element(links, session, panel) for panel in said.panels]
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


def composer(
    action: str,
    beneath: Placed,
    *,
    live: bool,
    refusing: bool = False,
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
                    # The key is named on the button because otherwise nothing on the page says it
                    # exists, and a shortcut nobody can find is one nobody uses.
                    button(
                        attrs={"type": "submit", "disabled": refusing, "title": "Shift-Enter"},
                        children="Send",
                    ),
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
            main(children=[header(children=h1(children="mainplate")), *pane]),
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
                    chosen_note(showing.chosen, showing.repository, showing.workspace),
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


def attachable(showing: Conversation, reachable: Reachable) -> Reachable:
    """
    What a fork of this session may choose to work in, which is nothing once it works somewhere.

    A fork attaches a repository or inherits one; it never swaps. Answering that here, as an empty
    set of choices, is what keeps `picker` a rendering rather than a place that knows the rule.
    """
    settled = showing.chosen is not None and showing.chosen.repository is not None
    return Reachable(repositories=()) if settled else reachable


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
