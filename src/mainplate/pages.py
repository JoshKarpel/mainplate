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
from without_html import Attributes
from without_html import Child
from without_html import Element
from without_html import Node
from without_html import VoidElement
from without_html import a
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

from mainplate.agent import RETENTION
from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Offering
from mainplate.catalogue import grouped
from mainplate.commands import UNFINISHED
from mainplate.conversation import BASE_FIELD
from mainplate.conversation import BRANCH_FIELD
from mainplate.conversation import DISPOSITION_FIELD
from mainplate.conversation import NETWORK_FIELD
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import TRUSTED_FIELD
from mainplate.conversation import Block
from mainplate.conversation import Command
from mainplate.conversation import Disposition
from mainplate.conversation import Guidance
from mainplate.conversation import Kind
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reasoning
from mainplate.conversation import Spent
from mainplate.conversation import Steering
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.forge import Reachable
from mainplate.markup import as_document
from mainplate.markup import as_message
from mainplate.plugins.asking import running
from mainplate.plugins.installed import ON
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.installed import grouped as by_tier
from mainplate.plugins.protocol import Number
from mainplate.plugins.protocol import Setting
from mainplate.plugins.protocol import Switch
from mainplate.plugins.protocol import settings_of
from mainplate.reference import Cost
from mainplate.reference import Described
from mainplate.reference import Reference
from mainplate.reference import describe
from mainplate.sandbox import Filesystem
from mainplate.service import Claimed
from mainplate.service import Conversation
from mainplate.service import Delayed
from mainplate.service import Idle
from mainplate.service import Queued
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import TITLE_LENGTH
from mainplate.sessions import Footprint
from mainplate.sessions import Session
from mainplate.snapshots import LONGEST_REF
from mainplate.tending import AGAIN
from mainplate.tending import ENABLED_FIELD
from mainplate.tending import PLUGIN_FIELD
from mainplate.tending import SETTLE_FIELD
from mainplate.tending import SETTLED
from mainplate.tending import Tending
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

# The one named event the stream sends, which says the page's shape is no longer the checkpoint's:
# a page drawing the settings step whose session has since loaded its plugins. It is named rather
# than a partial because there is nothing to swap - the page it is sent to has none of the regions
# the new shape has - so what a reader needs is the page again, and a named event is what htmx hands
# to a script rather than to a target. The stream element closes on it, and `mainplate.js` reloads.
# Three readers of one word: here, the stream, and the script, which spells it as a literal. It is
# the word `settling`'s other half already uses for the shape past the step.
LOADED: Final = "loaded"

# The query parameter a page states its shape on when it opens the stream, and the one value it
# takes. Only the step names itself; a page showing the conversation sends nothing, since that is
# the shape a page has unless it says otherwise.
SHAPE_FIELD: Final = "shape"
SETTLING: Final = "settling"

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

TRUST_TOGGLE_ID: Final = "open-trust"
TRUST_ID: Final = "trust"

# The one field the worktree group posts. Form-only rather than a recorded field, because what
# it carries is *two* recorded things at once - a repository and a filesystem level - and which
# two is decided by parsing it, at the boundary, once.
WORKSPACE_FIELD: Final = "workspace"

# What the two fields under the repository cards are, as one thing to swap: picking a repository
# replaces the whole block so the completions are that repository's. Both are named here because the
# card carries the target and the block carries the id, and the two must not drift.
BASIS_ID: Final = "basis"
BRANCHES_ID: Final = "branches"
FOUND_ID: Final = "branches-found"

# The dots inside that block, shown while the forge is being asked what branches it has. Named here
# for the same reason the block is: the card points `hx-indicator` at it and the block draws it.
BASIS_LOADING_ID: Final = "basis-loading"

SENDING_ID: Final = "sending"

# Whether the provider still holds this conversation's prefix, and what the next request pays if it
# does not. A region of its own with an id, because it sits in the composer and the composer is not
# what the stream replaces: what it says goes stale on every turn, since the context it prices grows
# with each one. So the page's one connection carries it as a second partial, which is the shape
# `streaming.py` was built for. See `cache_note`.
CACHE_ID: Final = "cache"

# The form the picker's controls belong to, named rather than relied on by nesting. Both pages nest
# them inside it today, so on both the `form` attribute names the ancestor a control already had -
# but what carries it is `model_cards` and `starting_at`, which are also served as fragments, and a
# fragment is markup with no ancestor at all until the swap lands. Naming the form is what makes a
# control's association a property of the control rather than of wherever it is put, which is what
# lets one component serve the page and the swap. `TestWhatAFormPosts` is what fails when it goes,
# by asking a browser what `form.elements` holds.
CHOOSING_ID: Final = "choosing"

# What each kind of panel is called where a person reads it: the role label on the panel, and the
# chip in the key that governs it. One mapping, so the legend and the thing it is a legend for
# cannot come to disagree about what a kind is called.
NAMES: Final[tuple[tuple[Kind, str], ...]] = (
    ("system-prompt", "system prompt"),
    ("guidance", "guidance"),
    ("prompt", "prompt"),
    ("note", "note"),
    ("steer", "steer"),
    ("command", "command"),
    ("thinking", "thinking"),
    ("assistant", "assistant"),
    ("tool", "tool"),
)

# The one kind whose label does not say everything about it. `you (ran)` used to carry the fact that
# no model was ever told about a command; `command` does not, so it is said here instead, which is
# where the cost estimate already says the thing a figure cannot. Only this one, because it is the
# only kind whose name leaves something out. Named for the attribute rather than for what it holds,
# since `aside` is taken and means a side conversation.
TITLES: Final[dict[Kind, str]] = {
    "command": "You ran this yourself, in the session's worktree. No model was told about it.",
    # The other kind whose label leaves something out: `guidance` says what it holds and not why it
    # is here, which is that a tool reached into a part of the repository carrying its own.
    "guidance": (
        "The console handed this to the model when it reached into a part of the repository "
        "that carries its own guidance."
    ),
    # The third, and the one where leaving it out would be worst: every other message in a
    # conversation was typed by somebody, so a reader has no reason to suspect this one was not.
    # **It is the fallback rather than the answer**: a note carries its own `title` where the plugin
    # that asked for it named one, since a pre-commit failure and a handoff document are different
    # things to meet halfway down a transcript. This is what a plugin that named none gets.
    "note": "A plugin asked for this. Nobody in the conversation typed it.",
}

# Which side of the exchange a kind is on: what reached the model, and what the model produced.
# The dock's flanking arrows step one side each, and the palette runs on this same axis, so it is
# stated once here rather than in both places.
SIDES: Final[dict[Kind, str]] = {
    # The person's side, by the same rule as `command`: the axis is who produced the text, and what
    # is in a system prompt was written by the operator and by whoever wrote the repository's own
    # guidance. The console composed it; it did not write it.
    "system-prompt": "person",
    # The same rule one mechanism along: whoever wrote the repository's `AGENTS.md` wrote this, and
    # the console handed it over. What separates it from the standing prompt is where it sits in the
    # request, which is not something a hue can say.
    "guidance": "person",
    "prompt": "person",
    # The person's side because the axis is who produced the *text*, and what a note holds was
    # produced by this console's own machinery with the model as the party about to be told. Its
    # `title` says a plugin asked for it, exactly as `command`'s says no model was told. What a
    # plugin gets to vary is the `tone`, which is weight *within* this side rather than a hue
    # competing with it.
    "note": "person",
    "steer": "person",
    # The person's side because the axis is who produced the text, which is the same rule `steer`
    # follows. It is the one kind on that side the model never saw, and the panel's own `title` says
    # so rather than the palette: a hue is for who, not for who was told.
    "command": "person",
    "assistant": "model",
    "thinking": "model",
    "tool": "model",
}

# Which panels the server draws open, and every panel folds, so this is the whole of the default.
#
# **The reader's is the last word, and the server only says where they start.** What a person opens
# or shuts survives every swap - `mainplate.js` keeps the decision, not a set of the ones they
# unfolded - so this is a first offer rather than a rule about what may be read. A kind added
# without an entry here is a `KeyError` at render, which is the same bargain `SIDES` takes and for
# the same reason: a default nobody chose is worse than a page that will not draw.
#
# Reference is shut and conversation is open, which is the one line through every kind here. A system
# prompt and a delivered guidance file are documents somebody committed, so they are drawn as the
# line they open with; everything else is what was said, and a conversation whose replies had
# to be opened one at a time would not be a transcript. A note falls on the conversation side of
# that despite often reading like a document: it is a message, it is the one nobody wrote, and so it
# is the one a reader cannot recall for themselves. A tool panel is *open* with each call inside it shut,
# which is today's rendering exactly: the calls are listed, and what each was handed is a press away.
OPENS: Final[dict[Kind, bool]] = {
    "system-prompt": False,
    "guidance": False,
    "prompt": True,
    # Open, because it is a message and not reference material: what it says is why the turn under it
    # goes the way it does, and it is the one message a reader did not write and so cannot recall.
    "note": True,
    "steer": True,
    "command": True,
    "thinking": True,
    "assistant": True,
    "tool": True,
}


def opens(starts: bool) -> Attributes:
    """
    Where a fold starts, said twice, because the two sayings answer different questions.

    `open` is the state, and it is what makes the page work with no script at all. `data-opens` is
    where the console *put* it, which stops being the same thing the moment a reader presses
    anything: one is mutable and one is not, so this is the original beside the current rather than a
    copy of it, and neither can go stale against the other.

    It exists for the dock's third fold button, which puts every fold back where the console had it.
    Once a reader has moved one - or a morph has, which on a turn being watched is most of them - the
    page holds that answer nowhere else, and the button has no way to ask the server for it without
    fetching the transcript again.

    The word is the dock's own (`data-fold="open"` / `"shut"`), so what a button posts and what a
    fold says about itself are one vocabulary.
    """
    return {"open": starts, "data-opens": "open" if starts else "shut"}


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
    workspace_branches: Reversible
    fork_form: Reversible
    fork: Reversible
    setup: Reversible
    press: Reversible
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

    def to_stream(self, session: str, settling: bool = False) -> str:
        """
        The connection a page holds open, told which conversation it is showing and in which shape.

        A query parameter for the reason `to_endpoint_models` uses one: it narrows what a single
        connection reports on rather than picking a resource out. The stream is the page's, and the
        session is what the page happens to be looking at.

        **The shape rides along because the page is the only thing that knows it.** The stream sends
        whichever regions the page's shape has, and a page still drawing the settings step has no
        transcript for a message to land in: the moment the checkpoint's shape stops matching the
        page's, the stream says so once and the page reloads, which is `LOADED`. Sent by the page
        rather than remembered by the stream, so a connection re-opened after the change is told the
        page is still on the step and answers it the same way. Only the step names itself, since the
        conversation is the shape a page has unless it says otherwise.
        """
        shape = f"&{SHAPE_FIELD}={SETTLING}" if settling else ""
        return f"{url_for(self.stream)}?session={session}{shape}"

    def to_endpoint_models(self) -> str:
        """
        The model select, asked for with an endpoint in the query string.

        A query parameter rather than a path segment, because the endpoint is *the value of the
        select that asks*: htmx sends a triggering input's own value, so this URL needs no
        interpolation and the select needs no script to build one.
        """
        return url_for(self.endpoint_models)

    def to_workspace_branches(self) -> str:
        """
        Where a repository's branches come from, asked for with the workspace in the query string.

        The same shape as `to_endpoint_models` and for the same reason: the workspace is *the value
        of the card that asks*, so htmx sends it and this URL needs no interpolation. One route for
        every card rather than one per repository, which is also what lets `no files` ask it and get
        a block with nothing to complete.
        """
        return url_for(self.workspace_branches)

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

    def to_setup(self, session: str) -> str:
        """Where a session says which of its plugins it runs, which it may until it answers a turn."""
        return url_for(self.setup, {"session": session})

    def to_press(self, session: str) -> str:
        """
        Where one control on one plugin's card posts to.

        One route for every plugin rather than one per plugin, because which plugin and which control
        are *values on the form* rather than places in the resource tree: a card is a form, and what
        it posts names what it is about. That is also what keeps the route a fixed string a page can
        hold without knowing what a session enrolled.
        """
        return url_for(self.press, {"session": session})

    def to_asset(self, name: str) -> str:
        return f"{self.assets}/{name}"


# Which of the bundled extensions this console installs. An allowlist rather than a bundle taken
# whole: `htmax` registers everything it carries on inclusion, and several of those would change
# how this page behaves without being asked for - `history-cache` would put back the history store
# htmx 4 deliberately removed, and `hx-live` and `alpine-compat` are reactive scripting this
# console does not want. htmx reads it from the meta tag before any extension registers, so a name
# absent here is never installed rather than installed and unused.
EXTENSIONS: Final = "sse"


def stream_element(links: Links, session: str, settling: bool = False) -> Element:
    """
    The page's one live connection, and the sink a message that named no region would land in.

    It says which shape the page was drawn in, and closes on the one event that says that shape is
    over: a settings step whose session has loaded is a page with nothing for a message to land in,
    so the connection ends and the script reloads the page rather than a partial being dropped on the
    floor. See `Links.to_stream`.

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
            "hx-sse:connect": links.to_stream(session, settling),
            "hx-sse:close": LOADED,
            "hx-target": "this",
            "hx-swap": "innerHTML",
        }
    )


def document(
    links: Links,
    heading: str,
    children: Element,
    session: str | None = None,
    forked_from: str | None = None,
    settling: bool = False,
) -> str:
    """
    The whole document, which every page is this with something different in the middle.

    `settling` is which shape the session page was drawn in, and it goes on the stream element so
    the connection can say when that shape is over; see `Links.to_stream`.

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
                        children=[*((stream_element(links, session, settling),) if session else ()), children],
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
                                        # What it takes on disk, drawn only once something has been
                                        # measured and something is there: a session that has not
                                        # worked yet holds nothing, and nothing is not worth a
                                        # figure, for the reason a session in no repository says
                                        # nothing about where it works.
                                        *(
                                            (
                                                span(
                                                    cls="footprint",
                                                    attrs={"title": footprint_note(session.footprint)},
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


def sized(allocated: int) -> str:
    """
    Bytes on disk as a person compares them, which is to two or three figures in a binary unit.

    Binary, unlike `tokens`, because this is a size to allocate against rather than a number to
    weigh: it is what `du` prints and what `df` will get back, and the unit says which base it is in
    so nobody has to know. One decimal under ten and none above, since `1.2 GiB` and `466 MiB` are
    each the digits a person reads and `1.21 GiB` is precision nobody asked for.
    """
    value = float(allocated)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            break
        value /= 1024
    figure = str(round(value)) if value >= 10 or unit == "B" else f"{value:.1f}".removesuffix(".0")
    return f"{figure} {unit}"


def footprint_note(footprint: Footprint) -> str:
    """
    What the figure on a row is a figure of, and when it was true, as the sentence behind it.

    One sentence for both places the figure is drawn, so the sidebar and the note under the message
    box cannot describe the same directories two ways. It says *when* because the number is as old
    as the last sweep, and a size with no time beside it reads as current.
    """
    return (
        f"{sized(footprint.allocated)} on disk across this session's worktree, scratch and plugins, "
        f"measured at {footprint.measured_at.strftime('%H:%M')}"
    )


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


def consumed(context: int, window: int | None) -> float | None:
    """
    How much of a model's context window this much context takes up, as a fraction of it.

    Nothing at all where either half is missing, which is one answer to three questions - no
    reference database, an endpoint that no longer lists the recorded id, a model nobody wrote a
    window down for - because the page does the same thing with all three.

    Uncapped, deliberately. A window is what a database says and the count is what a provider
    reported, so the two can disagree and a session past 100% is a real state worth seeing said
    rather than a figure to round back down to full. What is capped is the *gauge*, which cannot
    draw past its own width.
    """
    if not context or not window:
        return None
    return context / window


def along(fraction: float) -> str:
    """
    One fraction as a distance along a rule, capped because a gauge cannot draw past its own width.

    A session past a window the database understates asks for no more line than there is, which is
    the one way a fraction here can exceed one.
    """
    return f"{min(fraction, 1.0):.1%}"


def portion(fraction: float) -> str:
    """
    A fraction as the whole numbers of percent a person reads it in.

    Named as being under one rather than shown as `0%`, which is `charged`'s rule about `<$0.0001`
    at the other end of the same problem: a long conversation on a very large window really is a
    fraction of a percent of it, and rounding that to nothing says the window is untouched.
    """
    if fraction < 0.01:
        return "<1%"
    return f"{fraction:.0%}"


def spend_element(
    spent: Spent, whose: str, window: int | None = None, running: Decimal | None = None
) -> tuple[Element, ...]:
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

    **The input figure is the context and not the sum**, which is `Spent.context`'s whole argument
    said on the page: what a reader wants off a rule is how full the window is, and a turn's
    requests each carry the conversation again. The fraction beside it is the same fact as a
    percentage, and the gauge on the rule itself is the same fact again as a picture - one number
    said three ways because the question it answers is the one a long conversation ends on.

    **Symbols and not words.** A rule is a single line that must not wrap, and it now carries six
    figures where it carried three. `\N{UPWARDS ARROW}` and `\N{DOWNWARDS ARROW}` are a count of
    tokens going up to the model and coming back, `\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}` is how much of
    the first came out of the provider's cache instead, and `\N{GREEK CAPITAL LETTER DELTA}` against
    `\N{N-ARY SUMMATION}` is what this one exchange added against the running total, which is that
    pair's own notation and reads as a pair rather than as two prices to tell apart by size. Five
    cells rather than the twenty or so the words would take, and the words are in the titles where
    there is room to say which is which.

    `running` is everything up to and including whatever this rule speaks for, which is the figure a
    person scrolling actually wants: what one turn cost is only readable against what the
    conversation has cost so far. It is left out where it *is* what the rule already says, since the
    first priced turn of a session would otherwise print one number twice.

    **The separator goes between the figures rather than in front of each of them.** Every one of
    these is drawn only where there is something to say, so a dot carried by a figure is a dot that
    appears or disappears with it: baked in, the time had none and the count after it had one, and a
    turn nothing timed then opened with a dot standing for nothing. Interleaving it here is the one
    place that knows what is actually being drawn.
    """
    if not spent.asked and not spent.answered:
        return ()
    fraction = consumed(spent.context, window)
    figures: list[tuple[str, str, tuple[Child, ...]]] = []
    if spent.took is not None:
        figures.append(("took", f"{whose} spent {elapsed(spent.took)} waiting on the model", (elapsed(spent.took),)))
    if spent.context:
        figures.append(
            (
                "context",
                f"{whose}: {spent.context:,} tokens of context",
                (
                    f"\N{UPWARDS ARROW}{tokens(spent.context)}",
                    # Inside the count rather than beside it, and in brackets, because it is a fact
                    # *about* that count and not a figure of its own: what is cached is part of the
                    # context, the way the wire's own numbers nest. Its own element all the same, so
                    # a phone can drop the bracket and keep the count.
                    *(
                        (
                            span(
                                cls="rule__cached",
                                attrs={
                                    "title": f"{spent.cached:,} of those tokens were read from the provider's cache"
                                },
                                children=f" (\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}{tokens(spent.cached)})",
                            ),
                        )
                        if spent.cached
                        else ()
                    ),
                ),
            )
        )
    if fraction is not None and window is not None:
        figures.append(
            (
                "full",
                f"{portion(fraction)} of this model's context window, which the reference gives as {window:,} tokens",
                (portion(fraction),),
            )
        )
    figures.append(
        ("answered", f"{whose} wrote {spent.answered:,} tokens", (f"\N{DOWNWARDS ARROW}{tokens(spent.answered)}",))
    )
    if spent.cost is not None:
        figures.append(
            (
                "cost",
                f"{whose}, estimated from published rates and not billed: ${spent.cost:f}",
                (f"\N{GREEK CAPITAL LETTER DELTA}{charged(spent.cost)}",),
            )
        )
    if running is not None and running != spent.cost:
        figures.append(
            (
                "running",
                f"${running:f} up to and including {whose.lower()}, estimated from published rates and not billed",
                (f"\N{N-ARY SUMMATION}{charged(running)}",),
            )
        )
    return tuple(
        span(
            cls=f"rule__{named}",
            attrs={"title": title},
            children=list(said) if at == 0 else ["\N{MIDDLE DOT} ", *said],
        )
        for at, (named, title, said) in enumerate(figures)
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


def counted(many: int, thing: str, plural: str | None = None) -> str:
    """
    `3 models`, `1 option`, `2 branches`: a count with the word it counts, pluralised.

    The plural is given where an `s` does not make one, and asked for rather than worked out: an
    English pluraliser is a pile of rules and exceptions to get a handful of nouns right, where the
    caller already knows the word it is passing.
    """
    return f"{many} {thing if many == 1 else plural or f'{thing}s'}"


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


TRUST_CHOICES: Final[tuple[tuple[str, bool, str], ...]] = (
    ("trusted", True, "Its own plugins run, confined to the worktree"),
    ("read only", False, "None of its own code runs unattended"),
)
"""
The two answers to whether this session runs code the repository carries, and what each means.

**Trusted comes first because it is the default**, and the default is the honest reading of what
picking a repository already means: a session with a shell runs its build, its tests, its hooks and
whatever those shell out to, every one of them unread. A plugin is one more caller of that.

What the second answer is for is the session where that reading does not hold - a stranger's pull
request being read rather than worked in, or a session on `no files` that picked a repository and
hands the model no shell at all. That is why it is drawn here rather than inferred from the
isolation: the two are near enough to look like one question and are not.
"""


def trust_card(naming: str, holds: bool, saying: str, chosen: bool) -> Element:
    """
    One answer to whether a repository's own code runs, as a card in the group.

    The network group's own classes rather than a set of its own, because it is the same control
    asking the same shape of question one row down: two answers, one word and a phrase apiece. A
    second set would be a second thing to restyle the day either moves.

    `holds` is the answer this card *is* and `chosen` is whether it is the one picked, which are two
    things a boolean each and easy to run together: the value posted is the card's own, and only the
    picked one carries `checked`.
    """
    return label(
        cls="network",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="network__pick",
                attrs={
                    # `on` and nothing, by `network_card`'s rule inverted: a radio that is not checked
                    # posts no field, so an absent field has to mean the *default*, which here is
                    # trusted. Refusing is the thing somebody has to have actually said.
                    "type": "radio",
                    "name": TRUSTED_FIELD,
                    "value": "on" if holds else "",
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="network__name", children=naming),
            span(cls="network__note", children=saying),
        ],
    )


def trust_cards(trusted: bool) -> Element:
    """
    Whether this session runs code the repository carries, which today means the plugins it declares.

    **Per session and never per repository**, which is the whole shape of it: a repository changes,
    so an answer recorded against one covers a branch somebody pushed this morning as readily as the
    one you reviewed last year. Recorded on the `Choice` it is a decision about this conversation,
    settled before its first message and fixed for its life, and changing your mind is `fork`.

    Drawn only where a repository is picked, by the same rule the base and the branch follow: with no
    worktree there is nothing whose code could be trusted or not.

    The cost, stated: a repository's plugin runs unattended at every turn boundary and puts text into
    the conversation, which is a delivery channel for prompt injection with a guaranteed slot. The
    settings step is where every session then shows what it actually loaded, in those terms, before
    anything has been said to it - which is the part somebody can act on, since the grant is coarse
    and the exposure is what is made visible instead.
    """
    return choosing(
        "Repository code",
        TRUST_TOGGLE_ID,
        [named for named, _, _ in TRUST_CHOICES],
        div(
            cls="networks",
            attrs={"id": TRUST_ID, "role": "radiogroup", "aria-label": "Repository code"},
            children=[
                div(
                    cls="networks__grid",
                    children=[
                        trust_card(named, holds, saying, holds is trusted) for named, holds, saying in TRUST_CHOICES
                    ],
                )
            ],
        ),
    )


def workspace_card(links: Links, naming: str, value: str, saying: str, chosen: bool) -> Element:
    """
    One answer to what files a session has: a repository of its own, or one of the two that are not.

    A card rather than an `<option>`, and that is what the second line is drawn on: an `<option>`
    renders as text in every browser, so neither the forge a repository came from nor what a level
    means has anywhere to go inside a select. A row here carries it, which matters the moment two
    rows read `owner/repo` from different places.

    The `hx-get` is the same shape the endpoint cards use one group down, and for the same reason:
    what a session may *start at* is whatever this repository's branches are, so picking one asks for
    those and swaps the block under the cards. htmx sends the triggering input's own value, so it
    needs no interpolation, and the two cards that are not repositories ask the same question and get
    a block with no completions - which is what stops a list going stale under a reader who changed
    their mind about where they were working.

    `outerHTML` and not `outerMorph`, exactly as the model group is: the point of the swap is that
    the completions are now a *different* list, and merging would keep suggestions from a repository
    nobody is looking at any more.
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
                    "hx-get": links.to_workspace_branches(),
                    "hx-target": f"#{BASIS_ID}",
                    "hx-swap": "outerHTML",
                    # The dots inside the block being replaced, rather than the card that asked:
                    # what a reader is waiting on is the fields, and the card has already answered
                    # by drawing itself picked. Being inside the target is what makes it right
                    # rather than a problem - it is shown for exactly as long as the block it is
                    # standing in for has not arrived, and the swap that ends the request removes it.
                    "hx-indicator": f"#{BASIS_LOADING_ID}",
                    "hx-status:4xx": "swap:none",
                    "hx-status:5xx": "swap:none",
                },
            ),
            span(cls="repo__name", children=naming),
            span(cls="repo__forge", children=saying),
        ],
    )


def starting_at(repository: str | None, base: str | None, branch: str | None, branches: Sequence[str] = ()) -> Element:
    """
    Where in the repository the worktree starts, and what branch it starts there.

    Free text and not cards, because neither has a closed set to draw: a commit-ish is anything `git
    rev-parse` resolves, and a branch is a name that does not exist yet. `choosing` is the component
    for a question with answers to show, and putting an unbounded one behind it would mean either
    drawing a card per ref a repository has or drawing a card that is really a text box.

    What the branches do instead is **narrow it**: they are drawn under the box and cut to what
    matches as it is typed, so the field is a search over what the repository has and still takes a
    tag, a hash or `main~3`. One box rather than the cards every other question here gets, because
    those cards *are* the answer where these only fill one in - and a card posting `base` beside a
    field posting `base` would be two places one value could come from.

    It is drawn twice on purpose and that is not a copy to keep in step: a `<datalist>` for the
    browser's own completion, and a list the script narrows. Both come from `branches` in this one
    call, and exactly one is ever live, because enhancing the field removes the `list` attribute that
    makes the first one work.

    **With no repository there are no fields, and the block is an empty anchor.** A base and a branch
    are answers *about* a repository, so with `no files` or `this whole machine` picked they are two
    boxes asking a question the session does not have - and `Choice.settled` drops whatever they hold
    anyway, which is a form saying one thing and a record keeping another. That is not the greying
    `workspace_cards` was written to undo, because nothing here is kept in step with anything: which
    fields exist and which branches complete them are one answer, decided in this one call from the
    same `repository`, and delivered by the one swap a card already makes. The anchor stays so the
    next pick has something to target.

    The cost, with htmx absent: a card cannot reveal the fields, so a session started that way begins
    on the repository's default branch under the name this console gives it. The branches were already
    the swap's to deliver, so what is given up is naming a base by hand on a page whose scripts did
    not load.

    Both are optional and the placeholders say what leaving them does, which is the whole of what
    keeps two more fields from becoming two more steps: blank is the repository's default branch as
    it stands now, on a branch this console names after the session.

    **Two fields and not one, because naming a base cannot check that branch out.** Git refuses a
    branch another worktree already holds, so a session started at `main` that was left *on* `main`
    would stop the next one planting at all. So one says where to begin and the other says what to
    begin, and the placeholder on this one has to say so rather than implying the first answers both.
    """
    # The branches come off a forge, so a card is picked and the fields under it *arrive*: on a cold
    # clone that is seconds of a block that has not changed yet, which reads as a card that did
    # nothing. The dots are drawn in both shapes because both are what a pick lands on, including the
    # empty anchor a repository is picked *from*. They take no room until the request starts; see
    # `.basis__loading`.
    loading = working(saying="loading branches", extra="basis__loading", identified=BASIS_LOADING_ID)
    if repository is None:
        return div(attrs={"id": BASIS_ID}, children=loading)
    return div(
        cls="basis",
        attrs={"id": BASIS_ID},
        children=[
            loading,
            label(
                cls="basis__field",
                children=[
                    span(
                        cls="basis__label",
                        children=[
                            "Start at",
                            # Said only where there is something to say it about, because "0
                            # branches" on a repository nobody could reach reads as a fact about the
                            # repository rather than about this console not having asked.
                            *(
                                (span(cls="basis__count", children=counted(len(branches), "branch", "branches")),)
                                if branches
                                else ()
                            ),
                        ],
                    ),
                    input_(
                        cls="basis__box",
                        attrs={
                            "type": "text",
                            "name": BASE_FIELD,
                            "value": base,
                            "form": CHOOSING_ID,
                            # The browser's own completion, which is the whole of what this field has
                            # with the script absent. `mainplate.js` takes this attribute *off* at
                            # the moment it takes the narrowing over, because two dropdowns over one
                            # box is one more than a reader can use. See `paintBranches`.
                            "list": BRANCHES_ID,
                            "maxlength": str(LONGEST_REF),
                            "autocapitalize": "off",
                            "autocomplete": "off",
                            "spellcheck": "false",
                            "placeholder": "a branch, tag or commit (or leave it at the default branch)",
                        },
                    ),
                    datalist(attrs={"id": BRANCHES_ID}, children=[option(attrs={"value": name}) for name in branches]),
                    # The same names again, as the list the script narrows and shows under the box.
                    # Not a second copy to keep in step: both are rendered from `branches` in this one
                    # call, and exactly one of them is ever live, because enhancing removes the
                    # `list` above.
                    #
                    # `hidden` from the server and shown by the script, so with the file absent it
                    # stays out of the flow rather than being a wall of fifty branches under a field
                    # nobody has typed in.
                    ul(
                        cls="basis__found",
                        attrs={"id": FOUND_ID, "hidden": True},
                        children=[
                            li(
                                children=button(
                                    cls="basis__found-one",
                                    # A `button` and not the `<label>` with a radio in it that every
                                    # card in this picker is. Those *are* the answer; these only fill
                                    # in the one answer beside them, and a second control posting
                                    # `base` would be two places one value could come from.
                                    attrs={"type": "button", "data-branch": name},
                                    children=name,
                                )
                            )
                            for name in branches
                        ],
                    ),
                ],
            ),
            label(
                cls="basis__field",
                children=[
                    span(cls="basis__label", children="New branch"),
                    input_(
                        attrs={
                            "type": "text",
                            "name": BRANCH_FIELD,
                            "value": branch,
                            "form": CHOOSING_ID,
                            "maxlength": str(LONGEST_REF),
                            "autocapitalize": "off",
                            "autocomplete": "off",
                            "spellcheck": "false",
                            "placeholder": "name one (or leave it off and this session gets its own)",
                        }
                    ),
                ],
            ),
        ],
    )


def workspace_cards(
    links: Links,
    reachable: Reachable,
    repository: str | None,
    chosen: Filesystem,
    base: str | None = None,
    branch: str | None = None,
) -> Element:
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
                            workspace_card(
                                links, naming, level.value, saying, chosen=repository is None and level is chosen
                            )
                            for naming, level, saying in WITHOUT_A_REPOSITORY
                        ),
                        *(
                            workspace_card(links, naming, reached.id, reached.forge, reached.id == repository)
                            for reached, naming in rows
                        ),
                    ],
                ),
                # Under the cards rather than beside them, because they are details of the answer
                # above: which repository comes first, and where in it comes after. So a workspace
                # that is not a repository has none of them, and a page nobody has picked on yet is
                # that case.
                #
                # With no completions, always: asking a repository what branches it has to draw a
                # page on which that field may never be looked at is a network call for nothing.
                # Picking a card is what fetches the one list that matters.
                starting_at(repository, base, branch),
            ],
        ),
    )


def picker(
    links: Links,
    catalogue: Catalogue,
    reachable: Reachable | None,
    reference: Reference | None,
    chosen: Choice | None = None,
    naming: Placed = None,
    acting: Placed = None,
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
                else (
                    workspace_cards(
                        links,
                        reachable,
                        starting.repository,
                        starting.isolation.filesystem,
                        starting.base,
                        starting.branch,
                    ),
                )
            ),
            # The network sits under the worktree and above the endpoint, because that is the order
            # of breadth: what a session's files are decides what it can touch, whether it can dial
            # out decides what it can do with them, and the endpoint and model only decide who
            # answers.
            network_cards(starting.isolation.network),
            # Whether the repository's own code runs, directly under what the session can reach and
            # whether it can dial out, because it is the third question about the same subject: what
            # this session's files are, what may be done with them, and whose code runs in them.
            # Drawn only where a repository is picked, since with none there is nothing to trust.
            *((trust_cards(starting.trusted),) if starting.repository is not None else ()),
            choosing(
                "Endpoint",
                ENDPOINT_TOGGLE_ID,
                list(catalogue.endpoints),
                endpoint_cards(links, catalogue, starting.endpoint),
            ),
            model_cards(catalogue.offered[starting.endpoint].models, reference, starting.model),
            thinking_cards(starting.thinking),
            # What to call it, last, because it is the one question here that decides nothing about
            # how the session runs: everything above it is what the session *is*, and this is what a
            # reader will call it. Handed in rather than drawn here, because the fork page asks the
            # same five questions and has nothing to name.
            naming,
            # And what acts on all of it, under the last question rather than pinned below the
            # picker. Pinned it needed a row of its own that `main`'s grid had to hold open, and a
            # reader who has answered the last question is already looking at the bottom of the list.
            acting,
        ],
    )


def session_spend(spent: Spent) -> str:
    """
    What a whole session has come to, as the sentence behind the figure under the message box.

    The time is named only where every turn in the session was timed, which is the rule the money
    follows: a total quietly missing a turn reads as the whole and understates it.

    `asked` here and `context` on a rule, deliberately, and they are different numbers: this is what
    the session has been *charged* for, which is every request's input added up, where a rule says how
    much of the window one exchange left in use. Both are true and neither substitutes for the other,
    so this is not the place to make them agree.
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
    footprint: Footprint | None = None,
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
                        # The branch, because that is what somebody about to run `git push` in the box
                        # below needs to know and the one part of this line they cannot work out from
                        # the repository's name - a generated one especially, since it is named after
                        # the session rather than after anything they typed. Where the session
                        # *began* is settled and on the first turn's own rule; this is where it is
                        # now. Conditional for the sessions recorded before every one had a branch.
                        children=f"\N{MIDDLE DOT} {repository}"
                        + (f" @ {chosen.branch}" if chosen.branch is not None else ""),
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
            *(
                (
                    span(
                        cls="footprint",
                        # Last, after what the session cost, because it is the other thing a session
                        # is spending and the one this line can do something about: the money is
                        # gone and the disk comes back. On the same terms as the sidebar's figure,
                        # which is the same sentence behind it, so a row and its page agree.
                        attrs={"title": footprint_note(footprint)},
                        children=f"\N{MIDDLE DOT} {sized(footprint.allocated)} on disk",
                    ),
                )
                if footprint is not None and footprint.allocated
                else ()
            ),
        ],
    )


def written(text: str, *, document: bool = False) -> Element:
    """
    Prose, as the Markdown its author almost certainly meant it to be.

    The renderer is what makes putting this in a child position safe, and it is the only reason
    this is not simply escaped text: it renders the Markdown and then throws away everything the
    result is not allowed to contain, so a model that echoed a prompt back cannot put a script or
    a `javascript:` link on this page. See `markup.py` for why both halves of that are needed.

    `document` says the text is a file rather than something typed into a box, which decides whether
    its own newlines are line breaks. See `markup.py` for why that is the one difference.
    """
    return div(cls="text", children=as_document(text) if document else as_message(text))


def working(*, saying: str = "working", extra: str | None = None, identified: str | None = None) -> Element:
    """
    Three dots that say something is still happening.

    Drawn the same whichever thing is still happening, because a reader is being told the same
    thing, and that is the whole of what these share. A turn with no answer yet, a tool call that has
    not returned, a session whose worktree is still being planted: each is a fact read off the
    checkpoint, holding across however many renders it takes. A request in flight is htmx's instead,
    held for one round trip and shown by whatever points `hx-indicator` at it, which is what `extra`
    and `identified` are for.

    What a caller must not do is read one off the other. A panel that drew itself working because a
    form was posting would be saying the model is answering when what is happening is a swap.
    """
    return span(
        cls=("waiting", extra),
        attrs={"id": identified, "role": "status", "aria-label": saying},
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

    The id is the panel's own plus this block's place in it, which is stable in both halves: a
    panel's blocks only ever grow at the end, so a call keeps its place once made whether or not it
    has come back, and everything a turn draws after a response - a later response, a command -
    lands after the panel rather than in front of it, so the panel's own `at` does not move either.
    A command's fold cannot be named this way for exactly that reason; see `command_block`. The
    script needs it to put a reader's unfolded calls back after a swap, since the server renders
    `open` for one state only and morphing removes an attribute the new markup does not carry.

    A call with no result is drawn open and working, which is what a call still out looks like
    while the turn that made it runs, and what a turn whose run ended between the call and its
    return looks like afterwards.
    """
    return details(
        cls="tool",
        attrs={"id": f"{anchor}-tool-{at}", **opens(used.returned is None)},
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


def status_element(status: int) -> Element:
    """
    What a command exited with, as the number and what it means.

    The **number**, not "failed", because an exit status is a program's own vocabulary and flattening
    it loses what it said: `git diff --quiet` exits 1 to mean *there are changes*, and `grep` exits 1
    to mean *no match*, neither of which is a failure. `ok` is written out for zero because that is
    the one value every program agrees on, and the rest are shown as they came.

    `UNFINISHED` is the console admitting it never learned, which is a third thing rather than a bad
    exit: the process that would have read the status was stopped first.
    """
    if status == UNFINISHED:
        return span(
            cls="ran__status",
            attrs={"data-status": "unfinished", "title": "This never reported a status"},
            children="unfinished",
        )
    ended = "ok" if status == 0 else f"exit {status}"
    return span(
        cls="ran__status",
        attrs={"data-status": "ok" if status == 0 else "other", "title": f"This exited with status {status}"},
        children=ended,
    )


def command_block(ran: Command) -> Element:
    """
    One command the person ran, with what it said open under it.

    **Open, where a tool call is folded, and the difference is who asked.** A call is the model
    reaching for context, so its output is something a reader opens when they want to check the
    work; a command is something the person typed themselves, and what it said is the whole of why
    they typed it. So it is still a `<details>` - it folds, the dock's fold controls act on it, and
    a reader who has read one can shut it - but it does not have to be opened to be read.

    Which means the fold is now a `<details>` the server renders *open* and the reader may shut,
    where a call is one it renders shut and the reader may open. `mainplate.js` therefore keeps
    what the reader decided rather than only what they unfolded, or a morph mid-turn would reopen
    a command they had just put away.

    **The id is the command's own inbox entry, and deliberately not the panel's anchor** as a call's
    is. A panel's position moves while a turn is answered - a response landing above pushes it down -
    so a fold identified by it is a decision the script loses on the next response. The entry is the
    store's own name for the thing, minted once and never reused, so this is the record's name for it
    rather than a second numbering.

    The command itself is shown verbatim and never as Markdown. It is a shell line, so the
    backticks, asterisks and underscores in it are characters rather than emphasis, and rendering it
    would change what a reader is told they ran.

    No `data-markdown`, for the same reason `tool_block` carries none: what is on the page is already
    the source, so an attribute repeating it would be the second copy that one is not.

    **A command that said nothing says so, rather than drawing an empty box.** Plenty of them do -
    `git diff --quiet` is the fixture's own example, and every command whose whole answer is its exit
    status - and a blank pane under one reads as output that failed to arrive. It is a stated absence
    for the same reason `no reference record` is: a reader's next question is what happened, and an
    empty rectangle makes them ask it.
    """
    said = None if ran.result is None else ran.result.output
    return details(
        cls="ran",
        attrs={"id": f"ran-{ran.entry}", **opens(True)},
        children=[
            summary(
                children=[
                    code(cls="ran__line", children=ran.text),
                    *(
                        (
                            span(
                                cls="ran__took",
                                attrs={"title": f"This took {elapsed(ran.result.took)}"},
                                children=elapsed(ran.result.took),
                            ),
                        )
                        if ran.result is not None and ran.result.took is not None
                        else ()
                    ),
                    working() if ran.result is None else status_element(ran.result.status),
                ]
            ),
            *(
                ()
                if said is None
                else (
                    div(
                        cls="ran__body",
                        children=pre(children=code(children=said))
                        if said
                        else span(cls="ran__silent", children="said nothing"),
                    ),
                )
            ),
        ],
    )


# How much of a panel's prose is carried into the line its row stands for it with. Not a decision
# about how much is *shown*: what a shut panel shows is whatever fits, clipped with an ellipsis by
# the browser at whatever width the panel happens to have, which is the one measurement no server can
# make. This is only the bound on what is carried, and it exists because a line holding the whole of
# a long block would put every word of it on the page twice, on a region that is re-rendered whenever
# the turn in flight records anything.
#
# The number is what the clipping needs to stay honest: clipped short of the bound the ellipsis says
# there is more, and clipped *at* the bound with no ellipsis it would say there is not. So it has to
# exceed what the widest panel can show, which is a bounded question because the transcript is capped
# at `--measure`. `TestTheLineAShutPanelStandsFor` measures the worst case there is - the narrowest
# character this console's prose face draws, repeated - and fails if it fits.
OPENING: Final = 320


def opening_of(text: str) -> str:
    """
    The front of a block of prose, as the one line a shut fold stands for.

    Whitespace collapsed rather than left as written, because the summary is one line either way: a
    browser collapses it in the markup, so a paragraph break carried here would spend the bound on
    characters that draw as one space. Collapsing first makes `OPENING` a count of what a reader
    could actually see.

    Markdown markers are left in it. A model that opened its reasoning with a heading wrote that
    heading, and rendering it here would need a second rendering path for a line that has nowhere to
    put a block element; the fold under it is one press away for anybody who wants it set properly.
    """
    return " ".join(text.split())[:OPENING]


def written_block(kind: str, text: str, *, document: bool = False) -> Element:
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

    `document` goes straight to `written`, and says the text is a file rather than something typed
    into a box: guidance is one, and everything else here was written in the conversation.
    """
    return div(cls=("block", kind), attrs={"data-markdown": text}, children=written(text, document=document))


def block_element(block: Block, panel: Panel, at: int) -> Element:
    """
    One block, told where it is by the panel holding it.

    The panel rather than its anchor, because the two kinds that fold are named from different
    halves of it: a call is addressed by the panel it is in, which never moves once made, and a
    command by its own inbox entry, because the panel a command is in does move. See
    `command_block`.

    **Only two kinds fold in here, and the rest are drawn plain.** A stretch of reasoning, the
    standing system prompt and a delivered guidance file each used to carry a `<details>` of its own
    whose summary was the front of its own body - so a panel spent one row saying what it was and a
    second row saying it again, and the second row was a lone marker once the fold was open. The
    panel is that fold now, and its opening line is on the panel's own row. See `panel_element`.

    A call and a command keep theirs, because neither summary is a prefix of anything: a tool's name
    with what it returned and how long it ran, and a command's line with the status a program chose,
    are facts about the block rather than the block restated. A panel also holds a *batch* of either,
    so a reader wanting one read out of three needs a fold per call and not only a fold per panel.
    """
    match block:
        case Prose(text=text):
            return written_block("block--text", text)
        case Steering(text=text):
            return written_block("block--text", text)
        case Guidance(text=text):
            # Drawn as the document the standing system prompt is drawn as, because it is the same
            # kind of thing: an `AGENTS.md` with a line of the console's own in front of it. What
            # separates the two is where each sits in the request, which is the panel's business.
            return written_block("block--document", text, document=True)
        case Command():
            return div(cls=("block", "block--ran"), children=command_block(block))
        case Reasoning(text=text):
            return written_block("block--thinking", text)
        case ToolUse():
            return div(cls=("block", "block--tool"), children=tool_block(block, panel.anchor, at))
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
    The `r{turn}.{at}` marker on a rule, and the raw record of that request behind it.

    Named the whole way, for `Panel.address`'s reason one level along: a rule inside a turn draws no
    `#N`, so a bare `r1` said which request without saying of what, and a reader following one
    permalink out of several had nothing to tell them apart. The `r` is what keeps it from being
    read as a panel, which numbers a different axis - `#3.1` is turn 3's second *panel* and `r3.1`
    is its second *request*, and one response becomes as many panels as it has kinds of part.

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
                children=span(cls="tag__at", children=f"r{turn}.{at}"),
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
    forget: bool = False,
    window: int | None = None,
    running: Decimal | None = None,
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

    **`rule--forget` is the one rule that describes what is *above* it**, and that is what lets the
    sentence be short: every other rule looks forward at the request or the turn it opens, so there is
    no ambiguity about which direction this one means. Its `cleared` is not the `clear` the control is
    deliberately not called, and the object is what tells them apart: what was cleared is the
    **context**, where a bare `clear` beside a transcript that keeps every word would be claiming the
    transcript was. It is drawn in a heavier line rather than a colour of its own, because the palette
    runs on one axis - cool for what the person produced, warm for what the model did - and a boundary
    is neither. The panels above are left exactly as they were: what changed is what the model is
    handed, not what is worth reading, and fading them would say the second thing while colliding with
    `muted`, which is the reader's own decision and already drawn that way.

    The fork link says something extra here and is the *same link*, at the same turn, posting the same
    thing. Continuing the conversation a forget closed is `fork` at that turn: `before` copies the
    turns below the branch point and the marker lives on the turn that opens, so the branch carries
    the whole backlog and no boundary. A control of its own would be a second name for one call.

    **The line is also the gauge**, filled from the left as far as this request's context reaches
    into the model's window and shading toward red as it goes. A rule is already a hairline drawn
    across the whole column at every request boundary, so the one thing a long conversation most
    wants to know - how close it is to the end of the window - costs no row and no control: a reader
    scrolling down watches the line lengthen and warm. The fraction is the only thing the server
    computes into the markup, as one custom property; the colours, the geometry and the cap are the
    stylesheet's, because they are decisions rather than facts.
    """
    filled = consumed(spent.context, window) if spent is not None else None
    return div(
        cls=("rule", "rule--turn" if opens else None, "rule--forget" if forget else None),
        attrs={
            "id": f"rule-{turn}" if opens else f"rule-{turn}-{asked}",
            "data-turn": str(turn),
            # Declared rather than inferred from the modifier, so the dock's column steps stops it is
            # told about the way every other arrow does. See `dock_card`.
            "data-stop": "forget" if forget else None,
            # Capped here as well as clipped there, so a session past a window the database
            # understates asks for no more line than there is.
            "style": None if filled is None else f"--filled: {along(filled)}",
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
                        attrs={
                            "href": links.to_fork_form(session, turn),
                            "title": (
                                f"Fork from turn {turn}, carrying everything above this line"
                                if forget
                                else f"Fork from turn {turn}"
                            ),
                        },
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
            # Between two spacers rather than beside the fork link, so it sits in the middle of the
            # line with the turn's own controls at one end and its figures at the other. That is what
            # a boundary is: it belongs to neither side, and drawn against the left group it read as
            # one more thing about the turn rather than as the thing the rule is saying.
            *((span(cls="rule__forget", children="context cleared"), span(cls="rule__span")) if forget else ()),
            *(
                spend_element(spent, f"Turn {turn}" if opens else f"Request {turn}.{asked}", window, running)
                if spent is not None
                else ()
            ),
        ],
    )


def panel_opening(blocks: Sequence[Block]) -> str:
    """
    The line a shut panel stands for: the front of what is in it.

    A shut panel has to say what it holds, or folding prose is a control that trades a paragraph for
    nothing. What that line *is* differs by what the blocks are, and the split is the one the old
    per-block folds already drew. Prose - a message, a steer, a reply, a stretch of reasoning, a
    document - stands for itself with its own opening, clipped by the browser at whatever width the
    panel has. A call and a command have no prose opening to take, so the panel names what is in it:
    a batch is `read, read` and `git status --short, git diff --quiet`, which is the one thing a
    reader scanning a shut turn wants from either.

    The *first* block for the prose kinds, and every block for the two that are named. That is not an
    inconsistency: an opening is a prefix, and a prefix of a run of paragraphs is the front of the
    first one, where a list of calls that named only its first would be hiding the rest.
    """
    match blocks:
        case [ToolUse(), *_]:
            return ", ".join(block.tool for block in blocks if isinstance(block, ToolUse))
        case [Command(), *_]:
            return ", ".join(block.text for block in blocks if isinstance(block, Command))
        case [Prose(text=text) | Steering(text=text) | Reasoning(text=text) | Guidance(text=text), *_]:
            return opening_of(text)
        case _:
            # A panel with nothing in it, which is the one being waited on: it is the working dots
            # and a role, and there is no line for it to stand for.
            return ""


def panel_element(links: Links, session: str, panel: Panel) -> Element:
    """
    One run of one kind of thing, folded from the row of facts above it.

    `data-kind` and `data-side` are the whole of what the chrome needs to know: the key filters by
    kind, the dock's flanking arrows step by side, and the stylesheet draws the edge from the same
    attribute. Nothing has to keep a list of selectors in step with a list of kinds.

    **Every panel folds, and the mark is on its own row rather than under it.** What a reader wants
    put away is decided by what they are reading, so the console picks where each kind *starts* -
    `OPENS` - and nothing more. Three kinds used to carry a `<details>` inside the panel whose summary
    was the front of its own body, which spent a second row restating what this row already says, and
    once open spent it on a lone marker. Hoisted here that row is gone, the opening line rides beside
    the role, and every other kind gains a fold it never had.

    **The row is the summary, and what is in it keeps working.** A press on the permalink or on the
    copy button does not toggle the panel: the summary's activation behaviour skips a press whose
    target is interactive content, so a link and a button inside one navigate and copy as they always
    did. That is what lets this row be the fold without the row losing anything; it is a fact about
    the browser rather than about this markup, so `TestFoldingAPanel` asks Chromium.

    What a panel says is still what is *in* it, and nothing about the turn or the request around it.
    The worktree, the fork, what was spent and the raw record are all facts about the exchange rather
    than about any one run of blocks, so they are on the rules between them. See `rule_element`.

    Nothing here draws the copy buttons, and that is not an omission. One of them sits inside a
    fenced block, which is markup the Markdown renderer produced and this has no node to reach into,
    so seating them is `mainplate.js`'s - and a button in the markup for the panel beside a seated one
    for the code in it would be two mechanisms for one thing.
    """
    return details(
        cls="panel",
        attrs={
            "id": panel.anchor,
            "data-kind": panel.kind,
            "data-side": SIDES[panel.kind],
            # Which ink a note takes, which is weight *within* its side rather than a hue competing
            # with it. Drawn from the attribute rather than from a colour in the markup, because a
            # hex a plugin wrote is one this console could never restyle and one that reads well in
            # the light theme is the one that disappears in the dark. Absent on every other kind,
            # since only a note is ever asked.
            "data-tone": panel.tone if panel.kind == "note" else None,
            "data-turn": str(panel.turn),
            **opens(OPENS[panel.kind]),
        },
        children=[
            panel_meta(
                panel.kind,
                panel_opening(panel.blocks),
                anchor=panel.anchor,
                label=panel.address,
                # What the plugin that asked for this note called it, and what it said the note is.
                # Both fall back to the console's own answer for the kind, which for the role is the
                # word `note` and for the hover text is the sentence saying nobody typed it.
                role=panel.role,
                title=panel.title,
            ),
            *(block_element(block, panel, at) for at, block in enumerate(panel.blocks)),
        ],
    )


def panel_meta(
    kind: Kind,
    opening: str | None,
    *,
    anchor: str | None = None,
    label: str | None = None,
    role: str | None = None,
    title: str | None = None,
) -> Element:
    """
    A panel's row of facts, which is also the summary that folds it.

    One function for all three panel shapes - a `Panel`, the standing system prompt, and the one
    saying a reply is being written - because the row is the same row and a second rendering of it
    would eventually disagree about where the marker sits or what the label is called.

    `opening` is `None` where there is nothing yet to stand for, and the row carries the working dots
    in the line's own place: a reply not written yet, and a stretch of context whose instructions the
    pass has still to compose. In the row rather than in the panel, because a panel opened to show
    three dots is a row spent on three dots, which is the thing this row exists not to spend.

    `anchor` is absent on the one panel there is nothing to link to: the panel saying a reply is
    being written is gone the moment it arrives, so a permalink to it points at nothing by the time
    anybody follows one.

    The marker itself is the stylesheet's, on the role, because it turns with the panel's own `open`
    and nothing here would have to be told twice.

    `role` and `title` are what a note's own plugin named, and every other kind passes neither: what a
    panel's role says is the console's word for the kind, and the one kind that can carry somebody
    else's is the one nobody in the conversation wrote. A plugin that named nothing gets the console's
    answer for both, which is why these are overrides rather than the only source.
    """
    return summary(
        cls="panel__meta",
        children=[
            span(
                cls="panel__role",
                attrs={"title": said} if (said := title or TITLES.get(kind)) is not None else {},
                children=role or dict(NAMES)[kind],
            ),
            span(cls="opening", children=opening if opening is not None else working()),
            *(
                (a(cls="panel__anchor", attrs={"href": f"#{anchor}"}, children=f"#{label or anchor}"),)
                if anchor is not None
                else ()
            ),
        ],
    )


def system_prompt_panel(turn: int, said: str | None) -> Element:
    """
    What every request in one stretch of context carried, under the rule that opens the stretch.

    Panel-shaped and not a `Panel`, which is the same split `waiting_panel` makes: a panel's identity
    is its turn and its position, and this belongs to the first but not the second. Giving it one
    would have taken `#N.0` off the person's opening message, which is an address the fork link and
    every permalink already point at.

    **One per stretch, under its own rule**, rather than one at the top of the page. A forget
    composes again, so a single panel above everything would be the newest instructions standing over
    turns answered under older ones. Under the rule the reader gets the order the conversation
    happened in: the boundary, then what the model is told from here, then the message.

    **`None` is a stretch whose instructions are not composed yet**, drawn as the panel with the
    working dots in it. Composing reads the repository's guidance out of a worktree the pass is the
    one to plant, so on a session's first turn there is a real gap between the message being there to
    render and this being there to put in it. Drawn rather than left out, so what is coming is
    visible from the moment the message is; it resolves on the same swap the first response arrives
    on, and `instructed_in` is what keeps it off a stretch nothing will ever compose for.

    **The dots go on the panel's own row, in place of the opening line, and the panel stays shut.**
    An open panel holding nothing but a spinner is a whole row spent on three dots, which is the
    thing hoisting the fold up here got rid of everywhere else. It also keeps this panel's default
    from *moving*: a fold whose default changes under a reader is one the console cannot draw either
    way once they have pressed it, because a press that put it back where it was is a decision
    withdrawn. See `opens` and `wireFolds`. Shut throughout, the wait is one row and what replaces it
    is the same row saying what the prompt opens with.

    **Drawn as the Markdown it is**, because what is in it is `.md` files - the operator's guidance
    and the repository's `AGENTS.md`, concatenated - so its headings, lists and fences are the
    structure its authors wrote, and a wall of `##` is the one reading of it nobody meant.

    That does not weaken the claim that this is what was *sent*. What the model was handed is the
    source, and the source is what this hands back: the block carries `data-markdown`, so the panel's
    copy button gives the characters rather than the rendering, and the raw record on the rule is the
    same value one step further out.

    It is a `.block` and not bare prose, and that is what puts the copy button on the panel: the
    script seats one against a panel's blocks, and it is the whole prompt somebody reaches for. A
    fence inside gets its own besides, which is the seating everywhere else.

    It is drawn as the same `block--document` the guidance a turn is handed mid-way is drawn as: on
    the page the two are the same thing, and what separates them is where each sits in the request,
    which is what the panel says rather than anything inside it. The id names the turn the stretch
    began at, which never moves, so a reader who shut this keeps it shut across every swap.
    """
    anchor = f"system-prompt-{turn}"
    return details(
        cls="panel",
        attrs={
            "id": anchor,
            "data-kind": "system-prompt",
            "data-side": SIDES["system-prompt"],
            **opens(OPENS["system-prompt"]),
        },
        children=[
            panel_meta(
                "system-prompt",
                opening_of(said) if said is not None else None,
                anchor=anchor,
            ),
            *((written_block("block--document", said, document=True),) if said is not None else ()),
        ],
    )


def out_on_a_call(said: Transcript) -> bool:
    """
    Whether the turn in flight is waiting on a tool rather than on the model.

    What decides whether the transcript already says it is working. A call with no result is drawn
    working on its own panel, and it is the model's call, so a second panel of dots under it says
    the same thing twice and says it in a shape - an empty reply - that nothing is writing.

    Asked of the turn being answered rather than of the last panel on the page, because a person can
    type while a reply is coming: what is at the bottom may be their message, and the turn that is
    actually out is the one above it.

    A *command* running is not this. It runs outside the conversation and no model was told about
    it, so it says nothing about whether one is answering.
    """
    return any(
        isinstance(block, ToolUse) and block.returned is None
        for panel in said.panels
        if panel.turn == said.answering
        for block in panel.blocks
    )


def waiting_panel() -> Element:
    """
    One panel for however many messages are outstanding, because one reply is what is actually
    being written: the turns behind it are queued, not in flight.

    Shut, with the working dots on its own row where the opening line goes, which is what a panel
    with nothing in it yet should cost: the wait is the whole of what this says, and a panel opened
    to show three dots spends a second row saying it again. See `panel_meta`.

    Not drawn at all where a call is still out, which `out_on_a_call` decides: what this panel is for
    is a wait nothing else on the page accounts for.
    """
    return details(
        cls="panel",
        attrs={"id": "waiting", "data-kind": "assistant", "data-side": "model", **opens(False)},
        children=panel_meta("assistant", None),
    )


def running_to(before: Decimal | None, spent: Spent | None) -> Decimal | None:
    """
    What a conversation has cost once one more turn or request is counted into it.

    `altogether`'s rule, applied one at a time rather than to a whole session: unknown anywhere is
    unknown from there on, so the first unpriced turn takes the running total off every rule below
    it rather than leaving a figure that is quietly the sum of everything else. A total missing a
    part reads as the whole and understates it, and a reader has no way to tell that from a cheap
    conversation.

    A turn nothing is recorded for is a turn nobody has asked anything yet, which costs nothing and
    so leaves the total where it was. That is a different answer from an unpriced turn, and the two
    arrive here as the same `None` from two different places: one is a turn absent from the mapping,
    the other is a cost the reference could not supply.
    """
    if before is None:
        return None
    if spent is None:
        return before
    if spent.cost is None:
        return None
    return before + spent.cost


def cache_note(showing: Conversation) -> Element:
    """
    Whether the provider still holds this conversation's prefix, and what the next request pays if not.

    Meaningless before there was a cache, and worth a line now that there is one: a conversation picked
    up after lunch pays full input price for everything said in it, and nothing about the request looks
    any different. On a long conversation that is most of the bill.

    **A figure rather than a warning, in the family of the gauge and the `▣` count.** It does not tell
    anybody to `forget`: at low utilization the right move is to carry on, and choosing which figure
    matters is the reader's. So it says what is true and stops.

    **One-sided, always.** Past the retention a prefix is cold and this says so; under it nothing can
    be asserted, because eviction is unobservable from here, so what it says is `warm as of 12m` - a
    claim about when the prefix was last *written*, which is what a response landing is, and which is
    true on any wire whatever that wire's own TTL. `RETENTION` works as the one threshold for the same
    reason: it is the longest this console asks for anywhere, so past it the prefix is gone everywhere.

    **The server renders an absolute time and the script renders the relative one.** Nothing here is
    re-rendered on the clock - the stream sends this when the session *records* something, and the
    interval that matters is exactly the one where nothing is recorded - so a server-rendered `warm`
    would sit there while the retention rolled past it. `cached at 15:09` is a fact that cannot rot,
    which is what a reader with no script gets; `data-since` and `data-retention` are what the script
    needs to say `cold` or `warm as of 12m`, and it measures the rest against its own clock from the
    moment it first saw them, so no two machines' clocks are ever subtracted. See `wireCache`.

    Cold is the one state the server *can* assert, since it was already true when this was rendered
    and nothing makes a cold prefix warm again.

    **The money is a floor and says so.** What it prices is the input of the next turn's *first*
    request - re-sending what has already been said - and not the answer, the tools that turn runs, or
    the further requests it makes, any of which can dwarf it. A bare figure would read as what the
    next turn costs and understate it by however much work that turn turns out to be, so it carries a
    `+` and the title spells out what sits on top. That is the one thing about a turn nobody has
    started that can be stated exactly rather than guessed at.

    **The money is absent where the price is**, exactly as the gauge's fraction is: no reference
    database, an endpoint that no longer lists the recorded id, a model with no record, or a record
    with no price. What is left is when the prefix was written, which is worth saying on its own.

    Empty before anything has been answered, and empty rather than absent because it is what the
    stream's partial targets - the same reason `starting_at` leaves a block behind with no repository.
    """
    if showing.said.answered_at is None or showing.since is None:
        return p(cls="cache", attrs={"id": CACHE_ID})
    context = showing.said.total.context
    figures: list[Element] = [
        span(
            cls="cache__state",
            attrs={"title": f"This conversation's prefix was last written at {showing.said.answered_at:%H:%M}"},
            children="cold" if showing.since >= RETENTION else f"cached at {showing.said.answered_at:%H:%M}",
        )
    ]
    if showing.resending is not None:
        held = showing.resending
        # `▣` for the cached end, which is the mark the rule already uses for the part of an input a
        # provider read from its cache. Labelling them `warm` and `cold` instead would put those two
        # words on the line twice over, since the state beside them is already one of the two.
        said = (
            charged(held.cold)
            if held.warm is None
            else f"\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}{charged(held.warm)} / {charged(held.cold)}"
        )
        spread = (
            f"${held.cold:f} with none of it read from a cache"
            if held.warm is None
            else f"between ${held.warm:f} with all of it read from a cache and ${held.cold:f} with none of it"
        )
        figures.append(
            span(
                cls="cache__cost",
                attrs={
                    "title": (
                        f"Re-sending the {context:,} tokens already said costs {spread}, estimated from "
                        f"published rates and not billed. That is where the next turn *starts*: what it "
                        f"answers with, the tools it runs and any further requests it makes are all on top."
                    )
                },
                children=[
                    "\N{MIDDLE DOT} ",
                    # The `+` is the whole of what keeps this honest on the line, and it applies to both
                    # ends. What they price is the *input of the next turn's first request* and nothing
                    # else - not the answer, not the tool calls, not the further requests a turn of any
                    # size makes - so a bare figure would read as what the next turn costs and understate
                    # it by however much work the turn turns out to be. A floor is what can be said
                    # exactly, and the pair is what says what waiting costs.
                    f"\N{UPWARDS ARROW}{tokens(context)} at {said}+",
                ],
            )
        )
    return p(
        cls="cache",
        attrs={
            "id": CACHE_ID,
            # Seconds rather than the absolute time, so the script adds to a duration the server
            # measured instead of subtracting one clock from another. See the docstring.
            "data-since": str(int(showing.since.total_seconds())),
            "data-retention": str(int(RETENTION.total_seconds())),
        },
        children=figures,
    )


# `reserve_mark` used to be here, and the plugin protocol is what deleted it. It drew a bar across
# every rule at the fraction the handoff reserve opens at, read off two columns this table no longer
# has: a reserve is a plugin's own setting now, and the console has no vocabulary for a plugin
# drawing in the transcript region. Stretching the card language that far to reach it would be
# inventing an axis in order to have a cross-product, so the gauge stays the console's and a handoff
# shipped as a plugin does without the mark. The fill itself is unaffected, since how much of the
# window a request used is the console's own arithmetic.


def transcript_region(links: Links, showing: Conversation) -> Element:
    """
    The conversation, and whether it is still waiting on the rest of it.

    A whole `Conversation` rather than the four things drawn out of one, because every caller had one
    in hand and was taking them apart the same way: what the region needs is the session, what was
    said, whether it is stalled, the model's window and where its reserve falls, and five arguments
    derived from one value are five chances for a caller to pair a transcript with another session's
    window.

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
    session = showing.session.id
    said = showing.said
    window = showing.window
    stalled = stalled_by(showing)
    drawn: list[Element] = []
    # What the conversation has cost by the time each rule is drawn. A turn rule carries the total
    # through the turn it opens, exactly as it already carries that turn's own spend: both figures on
    # it summarise what is below rather than what is above, so the pair reads as one statement about
    # the turn. The request rules within it then step from the total the turn began at up to that
    # same figure.
    before: Decimal | None = Decimal(0)
    for turn, panels in groupby(said.panels, key=lambda panel: panel.turn):
        within = tuple(panels)
        asking = said.requests.get(turn, ())
        spent = said.spent.get(turn)
        through = running_to(before, spent)
        # And the same total at each request within the turn, by index rather than by counting the
        # rules that get drawn: request 0 never gets a rule of its own, since the turn's rule already
        # stands at that boundary, and a request that produced no panel gets none either.
        climbing: list[Decimal | None] = []
        for one in asking:
            climbing.append(running_to(climbing[-1] if climbing else before, one.spent))
        drawn.append(
            rule_element(
                links,
                session,
                turn,
                asked=0 if asking else None,
                tree=within[0].tree,
                spent=spent,
                opens=True,
                # Off the turn's first panel beside its tree, which is where both facts about a turn
                # rather than about a request are carried.
                forget=within[0].forget,
                window=window,
                running=through,
            )
        )
        # Directly under the rule that opens the stretch, so a reader meets the boundary, then what
        # the model is told from here, then the message it is told it about. Absent on every turn
        # that continues a stretch rather than beginning one.
        if turn in said.system_prompts:
            drawn.append(system_prompt_panel(turn, said.system_prompts[turn]))
        at = 0
        for panel in within:
            if panel.asked is not None and panel.asked != at:
                at = panel.asked
                if at < len(asking):
                    drawn.append(
                        rule_element(
                            links,
                            session,
                            turn,
                            asked=at,
                            tree=asking[at].tree,
                            spent=asking[at].spent,
                            window=window,
                            running=climbing[at],
                        )
                    )
            drawn.append(panel_element(links, session, panel))
        before = through
    # What is happening at the end of the conversation, as one of three things and never two. A
    # refusal outranks the rest because it is the only one nothing can be waiting on; then why nothing
    # is happening, where something should be; and the dots last, which is what a reply being written
    # actually looks like. A call still out is already drawn working on its own panel, so the dots are
    # left off there and the sentence is not: a pass can fall over with a call outstanding.
    waiting = None if stalled is not None else waiting_for(showing)
    if stalled is not None:
        drawn.append(p(cls="stalled", children=stalled))
    elif waiting is not None:
        drawn.append(attention_element(showing, waiting))
    elif said.awaiting and not out_on_a_call(said):
        drawn.append(waiting_panel())
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

    Four columns of arrows, widest first, which is the picker's own ordering: the left one steps the
    points where the model's history starts again, the next steps whole turns, landing on the rule
    that opens each, the third steps every panel the key leaves in play, and the right one steps only
    what the model produced.

    The forget column is drawn in every session and steps nothing in most of them, which is right
    rather than a gap: the rail lives outside the region that swaps, so a column that appeared with
    the first forget would not appear until a reload. Its stops are found in the live transcript the
    way every other column's are, so one added mid-session is reachable at once, and its upper
    terminus is the top - which is what "before any forget" means.

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
                        "dock__btn--forget",
                        "Previous forget",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-stop": "forget"},
                    ),
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
                        "dock__btn--forget",
                        "Next forget",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-stop": "forget"},
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
                    # The start is the rule that opens the first turn rather than the first panel
                    # under it, which is where the turn's own facts and its fork link are, and above
                    # whatever the stretch was told. See `wireDock`.
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
                    # Every fold on the page, which since a panel is one means the whole conversation
                    # rather than only the calls in it: shut, the transcript is its own outline, one
                    # row per panel saying what is in it, and one press puts it all back.
                    dock_button(None, "Unfold everything", "\N{DOWNWARDS DOUBLE ARROW}", {"data-fold": "open"}),
                    dock_button(None, "Fold everything", "\N{UPWARDS DOUBLE ARROW}", {"data-fold": "shut"}),
                    # The third answer, and it is not a midpoint between the two beside it: those set
                    # every fold one way, and this hands the question back, so what a reader gets is
                    # a call folded, a command open and a system prompt away - the shape the console
                    # renders rather than any single state. It is the way back from either of the
                    # others, which without it are one-way presses over a whole conversation.
                    #
                    # Where each fold started is `data-opens`, which the server puts on every one of
                    # them because nothing else on the page still holds it: a turn being watched is
                    # morphed constantly, and every morph records the state it delivered. See `opens`.
                    dock_button(
                        None,
                        "Fold as the console does",
                        "\N{ANTICLOCKWISE OPEN CIRCLE ARROW}",
                        {"data-fold": "default"},
                    ),
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


SWITCH_CLASS: Final = "plugin__switch"
"""
What a control that takes effect on the press is called, named once because three things reach it.

The card's `hx-trigger` listens for a `change` from it, the stylesheet draws it, and the script's
tier switch sets every one under a heading. A class a trigger or the script depends on is a name to
write down rather than to spell three times.
"""

TIER_SWITCH_CLASS: Final = "tier__switch"
"""
What the switch on a tier's heading is called, which sets the switches under it and nothing else.

**It is not a third kind of answer.** What a session records is a switch per plugin; this is one
control that moves all of them in its group, which is why it posts no field of its own and works only
with the script present. Without `mainplate.js` every plugin's own switch still works, which is the
standing bargain every other scripted control here takes.
"""


def plugin_control(control: Switch | Number, held: Setting, plugin: str, form: str | None = None) -> Element:
    """
    One row of a plugin's card, drawn by the console from what the plugin declared.

    **A card is declared, not rendered.** A render executes nothing, and only a press runs the
    script, which is what lets a card come from a repository at all: rendering is constant and an
    action is rare, so the one place a spawn is affordable is exactly where it lands. It is also what
    keeps a plugin's card in step with the console's own controls, where a card a plugin drew would
    drift the first time anything was restyled.

    The declared card is also the settings schema, so `held` is a value read back under the same name
    the control declares: there is no second schema and no way for the two to disagree about a type.

    `form` is what associates a control with a form it is not nested inside, which the settings step
    needs and the rail's card does not.
    """
    named = f"{PLUGIN_FIELD}:{plugin}:{control.name}"
    if isinstance(control, Switch):
        return label(
            cls=SWITCH_CLASS,
            children=[
                input_(
                    attrs={
                        # The state, not a default: an unchecked box posts no field, so what comes
                        # back is exactly what the box shows.
                        "type": "checkbox",
                        "name": named,
                        "checked": bool(held),
                        "form": form,
                    }
                ),
                span(children=control.label),
            ],
        )
    return label(
        cls="plugin__number",
        children=[
            span(children=control.label),
            input_(
                attrs={
                    "type": "number",
                    "name": named,
                    "value": str(int(held)),
                    # The bounds the plugin declared, said to the browser as well so the refusal
                    # usually happens before the post rather than only after it. Re-checked at the
                    # boundary regardless, because a `min` on an input is a suggestion.
                    "min": None if control.least is None else str(control.least),
                    "max": None if control.most is None else str(control.most),
                    "form": form,
                    "aria-label": control.label,
                }
            ),
            # The unit, which is what lets a box hold two digits instead of six. Not part of the
            # label's own words, because it belongs after the number rather than before it.
            *((span(cls="plugin__unit", children=control.unit),) if control.unit else ()),
        ],
    )


def plugin_card(links: Links, session: str, plugin: Enrolled, settings: Mapping[str, Setting]) -> Element:
    """
    One running plugin's own card, drawn from what it declared and posting back to it.

    A form and not a scripted control, so it works with `mainplate.js` absent, and it swaps *itself*
    rather than the transcript: nothing about the conversation changed, and the only thing that did
    is inside this card.

    **The switch takes effect on the press and the number does not**, which is the difference between
    a control you set and one you type into. A checkbox says the whole of what it means the moment it
    moves, so waiting for `Set` leaves a console that looks switched and is not; a number is
    half-written for as long as somebody is writing it, so a `change` on the box would post whatever
    was in it when they tabbed away.

    A plugin that declared no card gets none here, rather than an empty frame with its name on it: a
    heading over nothing reports a feature rather than a fact.
    """
    if plugin.described.card is None:
        return div(cls="plugin", attrs={"hidden": True})
    card = plugin.described.card
    return div(
        cls="plugin",
        attrs={"id": plugin_id(plugin.qualified)},
        children=[
            div(cls="plugin__head", children=card.heading),
            form(
                cls="plugin__settings",
                attrs={
                    "method": "post",
                    "action": links.to_press(session),
                    "hx-post": links.to_press(session),
                    "hx-target": f"#{plugin_id(plugin.qualified)}",
                    "hx-swap": "outerHTML",
                    "hx-status:4xx": "swap:none",
                    "hx-status:5xx": "swap:none",
                    "hx-trigger": f"submit, change from:.{SWITCH_CLASS}",
                    "aria-label": card.heading,
                },
                children=[
                    *(
                        plugin_control(
                            row.control, settings.get(row.control.name, row.control.default), plugin.qualified
                        )
                        for row in card.rows
                    ),
                    button(cls="plugin__set", attrs={"type": "submit"}, children="Set"),
                ],
            ),
        ],
    )


TIER_NAMES: Final[dict[Tier, tuple[str, str]]] = {
    Tier.BUNDLED: ("bundled", "Shipped with this console."),
    Tier.USER: ("yours", "Installed by whoever runs this console, in its config.yaml."),
    Tier.REPOSITORY: (
        "this repository's",
        "Carried by the repository this session works in. They run unattended at every turn boundary, "
        "and what they write is said to the model.",
    ),
}
"""
What each tier is called on the settings step, and the one line under the heading.

**Grouping by where a plugin came from rather than by what it does is the whole point**: the three
are not equally trusted, and a reader deciding what to leave on is deciding about provenance.

The repository's line is the exposure in plain terms rather than a warning that something may be
unsafe. Behind the confinement one of those plugins can read and write the worktree and run what is
in it, which is what that session's `bash` could already do; the two things it adds are that it runs
unattended rather than because a model asked, and that it puts text into the conversation, which is a
delivery channel for prompt injection with a guaranteed slot on every turn. A reader's next question
is always *what happens*, and this answers it.
"""

SETUP_ID: Final = "setup"
"""The step's own id, because it is what its own form swaps and what the live connection replaces."""


def plugin_switch(plugin: Installed, on: bool, form: str | None = None) -> Element:
    """
    One plugin's own on-and-off, which is the whole of what the settings step decides.

    **Drawn from what was declared and never from what a plugin said**, because none of them has
    been asked anything yet: this is the control that decides which of these programs is run at all,
    so it is made of the name somebody installed the plugin under and the file it is. What a plugin
    calls itself is on its card, and its card comes back from having asked it.

    **Live here because nothing has been run yet, and gone afterwards**, which is not a
    preference: a tool definition leaving the cached prefix invalidates everything under it exactly
    as one arriving late does, so what plugins a session runs is settled the moment they are loaded.
    The rail then draws each running plugin's card and does not draw these, and forking is how a
    conversation changes its mind, as it is for the model and the repository.
    """
    return label(
        cls=SWITCH_CLASS,
        children=[
            # The same name, `off`, ahead of the box: an unchecked checkbox posts no field at all, so
            # without this a plugin somebody turned off is indistinguishable from one this form never
            # carried, and a step with every switch off posts nothing whatsoever. The box wins where
            # it is checked because both values arrive and the reader takes the last.
            input_(
                attrs={"type": "hidden", "name": f"{ENABLED_FIELD}:{plugin.qualified}", "value": "off", "form": form}
            ),
            input_(
                attrs={
                    "type": "checkbox",
                    "name": f"{ENABLED_FIELD}:{plugin.qualified}",
                    "checked": on,
                    "form": form,
                }
            ),
            span(cls="plugin__name", children=plugin.name),
            # And the file it is, for the two tiers where a reader is deciding about a program
            # somebody else wrote: the path is the whole of what there is to go on before it has been
            # asked anything, and it is what tells two plugins with the same name apart. A bundled
            # one's path is inside this package and says nothing a reader can act on.
            *(() if plugin.tier is Tier.BUNDLED else (span(cls="plugin__says", children=str(plugin.path)),)),
        ],
    )


def tier_group(tier: Tier, plugins: Sequence[Installed], tending: Tending, form: str) -> Element:
    """
    One tier's plugins, under a heading whose switch sets every switch below it.

    **The tier switch is one control that moves the ones under it, and never a second answer.** What
    a session records is a switch per plugin, so turning a tier off is turning each of its plugins
    off; a tier that recorded an answer of its own would be a second place the same question is
    answered, which is what this console removes wherever it finds it.

    Every tier is drawn, empty ones included, so the step is the same shape on every session and the
    flow can be learned and tested as one thing rather than as however many lists a repository
    happens to produce.
    """
    named, saying = TIER_NAMES[tier]
    states = [tending.on(plugin.qualified, ON) for plugin in plugins]
    switches = [plugin_switch(plugin, on, form=form) for plugin, on in zip(plugins, states, strict=True)]
    on = sum(states)
    return div(
        cls="tier",
        attrs={"data-tier": tier.value},
        children=[
            label(
                cls=TIER_SWITCH_CLASS,
                children=[
                    input_(
                        attrs={
                            # No `name`, because it posts nothing: it is a control over the controls
                            # below it, which is why it works only with the script present and why
                            # every plugin's own switch works without it.
                            "type": "checkbox",
                            "checked": bool(states) and on == len(states),
                            "indeterminate": 0 < on < len(states),
                            "disabled": not states,
                        }
                    ),
                    span(cls="tier__head", children=named),
                ],
            ),
            p(cls="tier__says", children=saying),
            *(
                (p(cls="tier__none", children="none"),)
                if not switches
                else (div(cls="tier__plugins", children=switches),)
            ),
        ],
    )


SETUP_SAYS: Final = (
    "None of these has been run. Setting up executes each one you leave on, once, to install "
    "whatever it needs and ask it what it contributes; that set is then fixed for this "
    "conversation, and forking is how it changes."
)
"""
The line above the switches, which says what the button does rather than what the list is.

**A reader deciding here is deciding whether to execute somebody else's program**, and nothing else
on the page says so: the tier lines say who wrote each one, and the names say what they are called.
Three halves of the sentence are load-bearing - that nothing has run yet is why the step is worth
stopping at, that a setup *installs* is why it is the one moment with a network, and that the set is
then fixed is why it cannot be left until later.
"""

SETUP_WORKING: Final = "Setting up: each plugin is installing whatever it needs and saying what it contributes."
"""
What the page says while the pass that answers the press is out.

It names the slow half rather than the press, because that is what somebody is waiting on and what
can take minutes: a repository plugin fetching a toolchain is a session sitting here, and a line
saying only "loading" would read as this console being slow.
"""


def setup_step(links: Links, showing: Conversation) -> Element:
    """
    Which of the plugins a session declares to load, drawn between creating one and typing into it.

    **The step is the confirmation before anything is executed.** A plugin is a program, so the pass
    that plants a session's worktree reads only what each tier *declares* - a directory listing and
    two YAML mappings - and the switches here are drawn from that. Pressing the button is what runs
    them, in the request that answers this form, and only the ones left on. So a session that never
    gets past this screen has invoked nothing at all.

    **Always drawn while it applies, and there is always something in it.** Skipping it when nothing
    is declared would make the number of steps depend on what a repository happens to carry, so the
    flow could not be described, learned or tested as one thing - and the empty version is not a case
    worth designing around anyway, because a console ships bundled plugins and so the step always has
    at least a heading and a switch in it.

    **It is a state of the session page and not a route of its own.** The session id exists from the
    moment the choices are posted, so the URL is stable and bookmarkable while the clone runs, and
    the page already has the live connection that fills this in when the declaration lands. A second
    address would be a page somebody can be sitting on when the thing it is waiting for arrives
    somewhere else.

    **It stands alone on that page rather than above the conversation.** A message box drawn beside
    this is pointed at a harness nobody has chosen yet, and the rail draws a card per *running
    plugin*, which is a plugin's own surface standing on the screen that exists to decide whether to
    run it. Which shape the page takes is `settling`, and the live connection sends whichever regions
    that shape has.

    **A branch is the case where there is something to stand in front of**, since a fork carries its
    parent's turns and none of its plugins. Its transcript is withheld until the step is answered
    rather than drawn under it: what a reader can act on there is the press, and every control the
    conversation would offer - sending, forking, going back to a parent - wants a session whose set
    of tools is settled. The cost, stated: a fork made only to re-read what its parent said has to be
    set up before it will show it.

    Four states, and each says the one thing a reader can act on. Nothing declared yet is the clone
    and the worktree. A refusal names what could not be read and offers another pass. A press that
    has been answered and not yet finished is the setup itself, which is the other slow moment in a
    session's life and the one a repository's own plugin decides the length of. Otherwise it is the
    tiers, with a switch apiece, under the button that sets them up - carrying the reason the last
    attempt stopped, where one did.

    Every button here is a plain submit and none of them swaps, because what each one leads to is a
    differently shaped page: settling and loaded are the two halves of `settling`'s own condition, so
    answering with a fragment would leave a reader on the half they had just left.
    """
    # The id and the wrapper once rather than once per arm, because the id is what the live
    # connection replaces: three spellings of it is three chances for a state to stop being the thing
    # that gets swapped in, and only one of them would be visible.
    inside: Node
    if showing.declared is None:
        inside = [
            p(cls="setup__working", children=working()),
            p(
                cls="setup__says",
                children=(
                    "Setting up: planting this session's worktree and reading what it declares."
                    if showing.refused_plugins is None
                    else showing.refused_plugins.why
                ),
            ),
            *(
                (
                    form(
                        cls="setup__again",
                        attrs={"method": "post", "action": links.to_setup(showing.session.id)},
                        children=button(
                            attrs={"type": "submit", "name": SETTLE_FIELD, "value": AGAIN},
                            children="Try again",
                        ),
                    ),
                )
                if showing.refused_plugins is not None
                else ()
            ),
        ]
    elif showing.settling_up and showing.refused_setup is None:
        inside = [p(cls="setup__working", children=working()), p(cls="setup__says", children=SETUP_WORKING)]
    else:
        # Named rather than spelled at both ends: the switches sit outside the form and are bound to
        # it by this id, so the two coming to differ is every control posting nothing.
        settling = SETUP_ID + "-form"
        inside = form(
            cls="setup__plugins",
            attrs={
                "id": settling,
                "method": "post",
                "action": links.to_setup(showing.session.id),
                "aria-label": "Which plugins this session loads",
            },
            children=[
                p(cls="setup__says", children=SETUP_SAYS),
                # Why the last attempt did not get anywhere, above the switches rather than beside
                # the plugin it names: what a reader does about a plugin that will not set up is turn
                # it off, and the switch is one line down. It is recorded against that attempt, so
                # pressing again is a new one and this sentence goes.
                *(
                    ()
                    if showing.refused_setup is None
                    else (p(cls="setup__failed", children=showing.refused_setup.why),)
                ),
                *(
                    tier_group(tier, plugins, showing.session.tending, form=settling)
                    for tier, plugins in by_tier(showing.declared)
                ),
                button(
                    cls="setup__set",
                    attrs={"type": "submit", "name": SETTLE_FIELD, "value": SETTLED},
                    children="Load plugins",
                ),
            ],
        )
    return div(cls="setup", attrs={"id": SETUP_ID}, children=div(cls="settling", children=inside))


def plugin_id(qualified: str) -> str:
    """
    A card's own id, which is what its settings form swaps.

    The qualified name with the colon taken out, because a colon in an id is a selector somebody has
    to escape and `hx-target` is a selector. One rule, here, so the element and everything pointing at
    it are built from one call.
    """
    return f"plugin-{qualified.replace(':', '-')}"


def rail(links: Links, session: str, tended: Tending, plugins: Sequence[Enrolled] = ()) -> Element:
    """
    Everything that navigates the conversation, in one column outside the region that swaps.

    Outside deliberately. The transcript is replaced whenever an answer arrives, and a control
    living inside it would be rebuilt under a reader's finger, lose its focus, and forget what
    they had typed into it. What the rail *projects* onto the transcript survives instead by being
    reapplied after each swap, which is the script's job.

    The clasp comes first so that on a window too narrow to stand the rail beside the conversation
    it is left where the cards' head was, and the cards slide off. Which width that is stays the
    stylesheet's to say.

    **What it holds is conversation controls, which is wider than navigating and always was**: the
    `aria-label` has said so since there was a rail, and a plugin's card is about a session rather
    than about moving around inside one. They sit under the shelf, which is the boundary: everything
    above it reads the conversation, and these are the first things that change how it is run.

    **A card per running plugin, in enrolment order, and none for a plugin that declared none.**
    Which plugins those are is settled for the session, so the rail draws the cards a session has
    rather than every card this console could draw; a plugin that is off contributes nothing here for
    the same reason it contributes no tool.

    **The switches that turn plugins on and off are not here**, and that is the one asymmetry worth
    naming: those are live only before the first message, because a tool definition leaving the
    cached prefix invalidates everything under it exactly as one arriving late does. So the settings
    step draws them and the rail draws what a running plugin's card says. A plugin's *settings* stay
    live where its being loaded does not, and the two are different questions rather than an
    inconsistency: a setting is a value the plugin reads when it runs, and being loaded decides what
    is in the prefix.

    **The theme goes last, pinned to the bottom by the stylesheet**, because it is the one card here
    that is not about this conversation at all - it is the reader's, across every session - so it is
    the one thing a reader scanning the rail for something about *this* session can skip.
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
            *(
                plugin_card(links, session, plugin, settings_of(plugin.described, tended.of(plugin.qualified)))
                for plugin in plugins
                if plugin.described.card is not None
            ),
            theme_card(),
        ],
    )


def naming() -> Element:
    """
    What to call this session, as one more of the picker's questions rather than a stray box.

    **Drawn like every other control on the page**, which is what its own class used to prevent: it
    was a field above a message box, and there is no message box here any more, so a full-width input
    in the composer's own idiom read as the thing somebody came to type rather than as the optional
    half of a choice. Legend, then field, in the shape `starting_at` already draws a base and a
    branch in.

    Optional, and the placeholder says what happens if you leave it: a session with no name given is
    named after its first message, exactly as every session was before this existed.

    A plain input with no `hx-` attribute on it, because it is submitted with the choices rather than
    being a question of its own: nothing exists to name until the form posts.
    """
    return div(
        cls="naming",
        children=[
            div(
                cls="basis__field",
                children=[
                    span(cls="basis__label", children=span(cls="picker__legend", children="Name")),
                    input_(
                        attrs={
                            "type": "text",
                            "name": TITLE_FIELD,
                            "maxlength": str(TITLE_LENGTH),
                            "placeholder": "Named after the first thing said in it",
                            "aria-label": "Session name",
                        }
                    ),
                ],
            )
        ],
    )


type Placed = Element | VoidElement | None
"""One thing a caller hands the composer to put above or below the box, or nothing at all."""


@dataclass(frozen=True, slots=True)
class Answer:
    """
    One thing that can happen to what you typed.

    **One word used three times.** `leader` is what the menu row is called, what is typed after `/`
    to reach it from the keyboard, and what the form carries in `data-leading` while the box is in
    its mode. Where it names a disposition it *is* that disposition's recorded value, so the word on
    the page, the word on the keyboard and the word in the store cannot come apart; `keep` is the one
    answer with no disposition behind it, because it sends the text nowhere.

    That is also why there is no separate label. A button reading `Fork` and a leader spelled
    `/branch` would be a synonym to keep in step for ever, so what a control says is the word itself.

    `staying` is whether the box is still in this mode once what was typed has gone, and it is each
    answer's own answer rather than one rule over all of them. A run of commands is what `Run` is for,
    so it stays; everything else is a thing you meant once, so the box comes back to `Send` and the
    next message goes where a message normally goes. It is carried on the button rather than kept in a
    list in the script, for the reason every other fact about a mode is: there is one place that
    decides what an answer is, and it is here.
    """

    leader: str
    saying: str
    posts: Mapping[str, str | int | bool | None]
    staying: bool

    demands: bool = True
    """
    Whether this answer needs something in the box, which every one of them does but `handoff`.

    The box is `required`, which is right for a message and wrong for a handoff: what a handoff takes
    is an optional note saying what to dwell on, and the ordinary one has nothing typed into it. So
    the answer that does not demand a message says so, and both of its renderings carry
    `formnovalidate` - the browser's own way of saying that this submitter does not need the form's
    required fields, which is a mechanism rather than a script toggling an attribute under a reader.

    Both renderings, because both submit: a menu row is a submit button exactly as the mode's own
    button is, so an exception on one of them would be a control that refuses from the menu and works
    from the keyboard.
    """

    @property
    def named(self) -> str:
        """What it is called, which is its own word: there is no second name to drift from."""
        return self.leader.capitalize()


def dispatched(disposition: Disposition, saying: str, *, staying: bool = False, demands: bool = True) -> Answer:
    """One answer that posts a disposition, named after the value it posts."""
    return Answer(
        leader=disposition.value,
        saying=saying,
        posts={"type": "submit", "name": DISPOSITION_FIELD, "value": disposition.value},
        staying=staying,
        demands=demands,
    )


PLUGIN_LEADER: Final = "plugin:"
"""
What a plugin's own answer posts in the disposition field, ahead of the leader it owns.

A prefix rather than a field of its own, because a submit button carries one name and one value and
the menu row, the mode button and the sentence are all that one button. The boundary reads the prefix
first and everything after it is the leader somebody typed, which is a word the session's own plugins
answer to rather than one this console has a list of.
"""


def plugin_answers(plugins: Sequence[Enrolled]) -> tuple[Answer, ...]:
    """
    Everything this session's plugins offer to do with what you typed, each under its own leader.

    **Declared once by the plugin and rendered three times by the console**, exactly as the console's
    own answers are: a row in the menu, the button the box shows in that mode, and the sentence above
    it. That is what stops a plugin's control drifting from the console's the first time anything is
    restyled, and it is the whole argument for a card being declared rather than drawn.

    `demands` is the one thing a plugin has to be able to say about a control it does not draw: the
    box is `required`, which is right for a message and wrong for an answer whose text is an optional
    note. Both renderings carry `formnovalidate` where it says so, because both submit.
    """
    return tuple(
        Answer(
            leader=leader,
            saying=declared.saying,
            posts={"type": "submit", "name": DISPOSITION_FIELD, "value": f"{PLUGIN_LEADER}{leader}"},
            # Never, because a plugin's answer is a thing somebody meant once. `Run` is the one mode
            # the box stays in, and it stays because a session that reaches for it reaches again a
            # line later; nothing here can claim that of somebody else's answer.
            staying=False,
            demands=declared.demands,
        )
        for plugin in plugins
        for leader, declared in plugin.answers()
    )


def sending_answers(returning: bool, answering: bool, running: bool) -> tuple[Answer, ...]:
    """
    Everything that can happen to what you typed, other than the thing Send already does.

    **Declared once and rendered three times**: as a row in the menu, as the button the box shows
    once it is in that answer's mode, and as the sentence above the box saying what will happen. The
    three cannot disagree about what is on offer, what it is called or what it posts, which is the
    same bargain the branch field takes in rendering one list as a `<datalist>` and a narrowed list.

    Ordered by how far the text travels: waiting for the next turn keeps it here and merely later,
    `Forget` keeps it here and drops what the model was told, a `Handoff` keeps it here and has the
    session write down what the model should be told instead, an `Aside` is a step out you mean to
    come back from, a `Fork` is a conversation of its own, `Parent` reaches the one this came out of,
    `Run` is not a message at all, and `Keep` sends it nowhere.

    **A plugin's own answers are not here**, and they are appended by `composer` rather than merged
    into this list: what a session offers depends on what it loaded, where these are the
    console's own and are the same on every session. `handoff` used to be one of these and is now the
    bundled handoff plugin's leader, which is what makes the pair worth keeping apart - this list is
    a constant, and that one is read off a session.
    """
    return (
        *(
            (
                dispatched(
                    Disposition.NEXT,
                    "Queue it behind the reply that is coming instead of putting it to the model now",
                ),
            )
            if answering
            else ()
        ),
        dispatched(
            Disposition.FORGET,
            "Ask it with the model's context cleared, leaving the whole conversation on the page",
        ),
        dispatched(Disposition.ASIDE, "Step out into a side conversation you mean to come back from"),
        dispatched(Disposition.FORK, "Ask it in a new session carrying this whole conversation"),
        *(
            (dispatched(Disposition.PARENT, "Send it to the conversation this one was forked out of"),)
            if returning
            else ()
        ),
        *(
            (
                dispatched(
                    Disposition.RUN,
                    "Run it in this session's worktree, as you rather than as the agent, without telling the model",
                    # The one answer the box stays in, because a command is rarely the only one: a
                    # session that reaches for `Run` reaches for it again a line later, where every
                    # other answer here is a thing somebody meant once.
                    staying=True,
                ),
            )
            if running
            else ()
        ),
        Answer(
            leader="keep",
            saying="Put it on the shelf, unsent, and clear the box",
            posts={"type": "button", "data-shelf": "keep"},
            staying=False,
        ),
    )


def sending_option(answer: Answer, refusing: bool) -> Element:
    """
    One answer as a row in the menu.

    What it does is written under its name rather than left to a `title`, because a control somebody
    opened a menu to find is one they have not used before, and a tooltip is not where anybody looks
    first. The leader is printed beside the name for the same reason the Send button names
    Shift-Enter: a shortcut nothing on the page mentions is one nobody uses.
    """
    return button(
        cls="sender__option",
        attrs={
            **answer.posts,
            "disabled": refusing,
            "data-leader": answer.leader,
            "formnovalidate": not answer.demands,
        },
        children=[
            span(
                cls="sender__option-head",
                children=[
                    span(cls="sender__option-name", children=answer.named),
                    span(cls="sender__option-leader", children=f"/{answer.leader}"),
                ],
            ),
            span(cls="sender__option-said", children=answer.saying),
        ],
    )


def sending_leader(answer: Answer, refusing: bool) -> Element:
    """
    One answer as the button the box shows while it is in that answer's mode.

    The *same* control as the menu row, rendered beside Send rather than made out of it by the
    script. One button per answer and one of them visible, because what makes a leader safe is that a
    reader can see which one they are about to press: a single button whose name, value and label the
    script rewrote would be exactly the `Send` that forks this console refuses everywhere else.

    `data-staying` is how the script learns whether the mode outlives what was just sent, which is the
    same bargain: the answer decides, the button carries it, and there is no second list to keep in
    step with this one.
    """
    return button(
        cls="sender__leader",
        attrs={
            **answer.posts,
            "disabled": refusing,
            "data-leader": answer.leader,
            "data-staying": answer.staying,
            "formnovalidate": not answer.demands,
            "title": "Shift-Enter \N{MIDDLE DOT} Escape to go back to a message",
        },
        children=answer.named,
    )


def sending_control(refusing: bool, answers: Sequence[Answer]) -> Element:
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

    **Everything that *sends* works with no script.** The fold is a `<details>`, which is how
    everything else here folds, and each destination posts its own `name`/`value` the way the browser
    has always submitted a named button. `Keep` is the exception and is honestly the odd one out: the
    shelf is `localStorage`, so that row does nothing with `mainplate.js` absent, exactly as the
    shelf's own card in the rail shows nothing then.

    It deliberately does *not* remember what was chosen last, which is where GitHub's version of this
    control goes further. That would mean a button labelled `Send` that forks, which is the one
    failure a control like this can have that nobody notices until after it has happened; here what a
    button says is always what it does, and a mode is only ever entered by asking for it by name.

    **A leader is a shortcut to a row and never a second way to say it.** With `mainplate.js`
    present, typing `/fork ` into an empty box - or `! `, which is `/run`'s own key - turns the box
    into that answer's box: the button beside it says `Fork`, and a sentence above it says what will
    happen. **The space is what commits it**, and until it is pressed the word is ordinary text with
    the menu open beside it, so nothing happens on a keystroke somebody was in the middle of. That is
    the whole safety property, and it is why every mode's button is rendered here rather than made out
    of `Send` by the script. The server parses no leader out of what was posted, so a paragraph that
    opens with `/` is a paragraph, and the menu is what works with the file absent.

    With no conversation yet there are no answers and so no menu, only Send: nothing to fork from,
    and no session for a shelf to belong to.
    """
    send = button(
        # Classed rather than found by position, because the script has to name it: it is the
        # submitter `requestSubmit` is handed, so that the keyboard posts the same pair the button
        # would. `form.querySelector('button[type=submit]')` matched it today and would have matched
        # a menu row the moment one was rendered above it.
        cls="sender__send",
        # The key is named on the button because otherwise nothing on the page says it exists, and a
        # shortcut nobody can find is one nobody uses.
        attrs={"type": "submit", "disabled": refusing, "title": "Shift-Enter"},
        children="Send",
    )
    if not answers:
        return send
    return div(
        cls="sender",
        children=[
            send,
            *(sending_leader(answer, refusing) for answer in answers),
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
                        children=[sending_option(answer, refusing) for answer in answers],
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
    running: bool = False,
    above: Placed = None,
    identified: str | None = None,
    plugins: Sequence[Enrolled] = (),
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

    `running` says this session has files a command could run in, which is what puts `Run` among the
    answers. Nothing else is needed to gate the mode: the script can only put the box into a mode the
    server drew a button for, so a page for a session with no repository has no run mode to enter and
    the two cannot drift.

    **The sentence saying what the box will do sits above it.** The composer is the bottom of the
    page, so a row appearing anywhere in its column pushes everything above that row upward - and
    with the sentence under the box, entering a mode moved the box itself out from under the cursor.
    Above it, what grows is the composer's top edge and the box stays exactly where it was.
    """
    answers = (*sending_answers(returning, answering, running), *plugin_answers(plugins)) if continuing else ()
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
            # What the box will do with what is in it, said in words and only while that is not what
            # the box normally does. One per answer, drawn by the stylesheet off the form's own
            # `data-leading`, so the words live here and the script sets one attribute; a `title`
            # would not do, because a mode nobody can see is the whole failure these exist to
            # prevent.
            #
            # A row of the composer's own column rather than an item beside something, because this
            # is a whole sentence: sharing a row would squeeze it to half the width on every window
            # to make room for something that is usually not there.
            *(
                p(
                    cls="leading",
                    attrs={"data-leader": answer.leader, "role": "status"},
                    children=f"{answer.saying}. Escape to go back to a message.",
                )
                for answer in answers
            ),
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
                    sending_control(refusing, answers),
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
    Where a session begins: everything it is decided by, and the button that creates it.

    **There is no message box here any more**, and that is the visible half of a change with two
    mechanical causes. A repository's plugins cannot be named until its worktree is planted, which the
    worker does on a pass; and none of them may be run until somebody has seen the list, because
    running one is executing a program. So this page records the choices, the settings step on the
    session's own page decides what it loads, and the message box is there once both are settled.

    The cost, stated: **creating stops being fire-and-forget.** Time to a first answer is unchanged,
    since the clone happens either way, but you now create, wait, confirm, and come back to type.
    That is bigger than an extra click, and it is taken because a boundary in front of executing
    somebody else's program is worth more here than the convenience.

    There is no transcript element on this page at all, for the reason there never was: what somebody
    is doing here is deciding what they are about to talk to.
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
                form(
                    cls="choosing",
                    attrs={"id": CHOOSING_ID, "method": "post", "action": links.to_start()},
                    children=div(
                        cls="setup",
                        children=picker(
                            links,
                            catalogue,
                            reachable,
                            reference,
                            naming=naming(),
                            # Named for what it makes rather than for what it begins, because the
                            # page after this one is the settings step and not a conversation: a
                            # button saying `Start` promised a session you could type into.
                            acting=div(
                                cls="starting", children=button(attrs={"type": "submit"}, children="Create session")
                            ),
                        ),
                    ),
                )
            ],
        ),
    )


def stalled_by(showing: Conversation) -> str | None:
    """
    Why this session cannot be answered, or nothing at all when it can.

    Two ways to be stopped, and each says the one thing a person can act on. The endpoint is a
    configuration file somebody can put back, so the conversation continues exactly where it left
    off. A refused request cannot be put back at all, because what the provider turned down is the
    recorded history itself, so what it names is `fork`: forking at the refused turn drops that
    turn's own requests and keeps everything under them, which is the shape that fits again.

    The endpoint is asked first because it is the cheaper failure to fix, and because a session whose
    endpoint is gone has no provider to have been refused by.

    It names the endpoint and not the model on purpose. A model missing from the picker does not
    stop a session, since an endpoint routes more ids than it advertises, so saying so here would
    tell somebody to fix something that is not broken.
    """
    if showing.chosen is None:
        return None
    if not showing.answerable:
        return (
            f"This session was started on endpoint {showing.chosen.endpoint!r}, which the configuration "
            f"no longer declares. Put it back to carry on, or start a new session."
        )
    if showing.refused is None:
        return None
    # The status where there is one, because the number is what somebody looks up, and never in
    # place of the provider's own words: a refusal says which of the several things a 400 can mean
    # this one was, and flattening that to a code would take the answer away.
    said = showing.refused.why
    coded = "" if showing.refused.status is None else f" ({showing.refused.status})"
    return (
        f"The provider refused this turn{coded} and would refuse it again, so nothing is waiting on "
        f"it: {said}. Fork at this turn to carry on without the requests it made."
    )


DUE_FIELD: Final = "data-due"
"""
How long until the next pass at this session is due, in seconds, as of this render.

Read by `paintDue`, which is what keeps the figure current: the stream sends this region when the
worker's standing *changes*, and counting down is exactly the interval where it does not. The word is
here rather than at both ends, for `CACHE_ID`'s reason.
"""

ATTENTION_ID: Final = "attention"
"""The one line saying why nothing is happening, where nothing is and something should be."""


@dataclass(frozen=True, slots=True)
class Waiting:
    """
    Why nothing is happening to this session, in the three parts the line is drawn from.

    **Three parts and not one sentence, because one of them is not prose.** `reason` is an exception's
    `repr`: it can be a line or a paragraph, it is full of quotes and brackets and paths, and it is
    the one thing in the box a reader has to actually read. Run together with the text either side of
    it, it is a wall nobody can find the edges of, so the page sets it apart and this is what lets it.

    `then` is what happens next, which is the half that makes this different from a refusal: the
    session is coming back, and saying so is what turns an error into a wait. Absent only where the
    statement is already the whole of it.
    """

    said: str
    reason: str | None = None
    then: str | None = None


def waiting_for(showing: Conversation) -> Waiting | None:
    """
    Why nothing is happening to this session, where something should be and nothing is.

    **The dots are the answer for every ordinary state, and this is the answer for the ones they lie
    about.** A reply being written and a session no worker will ever pick up drew the same three dots,
    for as long as the second lasted, which made a broken pass a thing nobody could see. So this
    speaks only where the dots would be wrong, and the caller draws them where it returns nothing.

    **Driven by the recorded failure rather than by the worker's standing**, which is what keeps it
    quiet. A delivery held back is ordinary for a moment on every pass - the queue reserves the row
    before the claim lands - so a line drawn on `Delayed` alone would flash "nothing is answering
    this" through healthy turns. A live failure is what tells a held-back delivery the worker is
    waiting out from the one nobody is coming back to, and `failure_in` is what makes it live.

    **The exception is a session nothing is scheduled for at all.** There is no race that produces one
    with something outstanding: a message and the row that queues it are written in a single commit,
    and a pass asks for the next one from inside itself, so this state is a session that has genuinely
    been dropped and is worth saying so about even with no reason recorded.

    Nothing at all where nothing is outstanding and nothing failed, which is a settled conversation:
    there is nothing to be stuck about, so there is nothing to say.

    It says why the session is stopped and not *when* it resumes, which is `attention_element`'s half:
    one of these is a fact that will read the same in an hour and the other is a figure that is wrong
    a second later, so only the second needs the script.
    """
    failed = showing.failed
    if not showing.said.awaiting and failed is None:
        return None
    fell = "The last pass at this session failed." if failed is not None else None
    why = None if failed is None else failed.why
    match showing.attention:
        case Claimed():
            return None if fell is None else Waiting(said=fell, reason=why, then="Another pass is answering it now.")
        case Queued():
            return None if fell is None else Waiting(said=fell, reason=why, then="It is queued for another pass.")
        case Delayed():
            # **It does not promise the retry will work**, and that is deliberate rather than hedging.
            # Most of what lands here is fixable and the next pass carries on from where this one
            # stopped; some of it is not, because what a pass replays is *recorded*, so a response the
            # agent will not accept is one every later pass will also not accept. Nothing here can
            # tell those apart, so it says what the mechanism does and names the way out of the second
            # - which is `stalled_by`'s way out, for the same reason: forking drops the turn's own
            # requests and keeps everything under them.
            carrying = "It will be tried again, carrying on from here. Fork at this turn if it keeps failing."
            return None if fell is None else Waiting(said=fell, reason=why, then=carrying)
        case Idle():
            nothing = "Nothing is answering this session and nothing is scheduled to."
            return Waiting(said=nothing) if fell is None else Waiting(said=fell, reason=why, then=nothing)
        case _ as unreachable:
            assert_never(unreachable)


def attention_element(showing: Conversation, waiting: Waiting) -> Element:
    """
    Why nothing is happening and how long until something does, with the reason set apart.

    **Three children rather than one paragraph**, which is what makes the reason readable: it is an
    exception's `repr`, so it is the one thing in the box a reader has to work through, and it sits in
    a block of its own in the monospace face with the prose above and below it. Centred prose around a
    left-aligned block, because a `repr` that wraps is unreadable centred and a one-line statement is
    not.

    **The server renders the figure and the script keeps it current**, which is `cache_note`'s bargain
    one field along and for the same reason: the stream sends this region when the worker's standing
    changes, and counting down is exactly the interval where it does not. `data-due` is what the script
    measures from, against its own clock from the moment it first saw the element, so no two machines'
    clocks are subtracted. A reader with no script gets the wait as it was when the page was drawn,
    which is a figure that goes stale rather than a sentence that is missing.

    The figure is only on the one arm that has one, and the sentence before it reads correctly alone.
    """
    due = showing.attention.until if isinstance(showing.attention, Delayed) else None
    then: list[Element | str] = [] if waiting.then is None else [waiting.then]
    if due is not None and waiting.then is not None:
        then.extend((" Due in ", span(cls="attention__due", children=elapsed(due)), "."))
    return div(
        cls="attention",
        attrs={"id": ATTENTION_ID, **({DUE_FIELD: f"{due.total_seconds():.0f}"} if due is not None else {})},
        children=[
            p(cls="attention__said", children=waiting.said),
            *(
                ()
                if waiting.reason is None
                else (pre(cls="attention__reason", children=code(children=waiting.reason)),)
            ),
            *(() if not then else (p(cls="attention__then", children=then),)),
        ],
    )


def settling(showing: Conversation) -> bool:
    """
    Whether this session is still on its settings step, which is what shape its page takes.

    **The registration alone, and the turn count deliberately not.** A session that has set nothing up
    is on the step, whether nobody has pressed the button yet or a pass is out answering the press,
    because the thing that takes a session past the step is a registration and nothing else writes one.

    A fork is why the turn count is not read here. It carries its parent's turns and none of its
    plugins, so it is a session holding a conversation and still owing an answer to the step - which is
    the point, since it plants a fresh worktree whose toolchain nothing has installed yet and may be
    planted at a tree where `.mainplate/` says something new. What the cached prefix cannot survive is
    a plugin set changing under a request already made, and a fork has made none.

    Two shapes of one page rather than one page with a banner: settling is the step alone, and loaded
    is the transcript, the message box and the rail. The live connection sends whichever regions the
    page's shape has and says so when the checkpoint's stops matching it, and the route answering the
    step refuses anything this says is past it, so this is the one predicate all three read.
    """
    return showing.plugins is None


def running_plugins(showing: Conversation) -> tuple[Enrolled, ...]:
    """
    The plugins this session actually runs, which is what the rail draws a card for.

    Asked of the registration and the session's own switches together, which is `running`'s job:
    nothing here decides what a default is, and a plugin that is off contributes no card for the same
    reason it contributes no tool.

    Thinned by `running` itself, because a card is a control over something that runs: a repository
    contribution dropped for claiming a name already taken reaches no event, so a card for it would
    be a form whose `Set` changes nothing anybody can see.
    """
    if showing.plugins is None:
        return ()
    return running(showing.plugins, showing.session.tending)


def session_page(links: Links, listed: tuple[Session, ...], showing: Conversation, reachable: Reachable) -> str:
    """
    One session, in whichever of its two shapes it is in.

    **Settling is the step alone**, in the transcript's own place because that is what it stands in
    for: a message box would be pointed at a harness nobody has chosen yet, and every control in the
    rail belongs to a session whose tools are settled. Settled is the page this console is otherwise
    about. `settling` is what decides, and `streaming.watching` and the route answering the step read
    the same predicate, so the page, the connection driving it and the press cannot disagree about
    which shape is on screen.

    A branch has turns and still takes the first shape, which is the one place these two are not
    "before the conversation" and "after it" - see `setup_step`.
    """
    if settling(showing):
        return document(
            links,
            showing.session.title or UNTITLED,
            shell(links, listed, showing=showing.session.id, reachable=reachable, pane=[setup_step(links, showing)]),
            session=showing.session.id,
            forked_from=showing.session.forked.session if showing.session.forked is not None else None,
            settling=True,
        )
    stalled = stalled_by(showing)
    # Once for both readers, so the composer's menu and the rail's cards cannot be built from two
    # answers to the same question, and the thinning behind it is done once per render.
    plugins = running_plugins(showing)
    return document(
        links,
        showing.session.title or UNTITLED,
        shell(
            links,
            listed,
            showing=showing.session.id,
            reachable=reachable,
            pane=[
                transcript_region(links, showing),
                composer(
                    links.to_say(showing.session.id),
                    chosen_note(
                        showing.chosen,
                        showing.repository,
                        showing.worktree,
                        showing.said.total,
                        showing.session.footprint,
                    ),
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
                    # Only where there are files to run in. A session with no repository has no
                    # worktree, so `Run` would be an offer with nowhere to honour it.
                    running=showing.runnable,
                    # Above the box, where the mode sentence already is, and above that sentence: this
                    # is a standing fact about the conversation and that is what the next press does,
                    # so the transient one sits closest to the thing it describes.
                    above=cache_note(showing),
                    # What this session's own plugins offer in the composer, each under the leader
                    # somebody types. Declared once by the plugin and rendered by the console, so a
                    # menu row, the button the box shows in that mode, and the sentence above it
                    # cannot disagree about what is on offer.
                    plugins=plugins,
                ),
            ],
            aside_rail=[rail(links, showing.session.id, showing.session.tending, plugins)],
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
                        picker(
                            links,
                            catalogue,
                            attachable(showing, reachable),
                            reference,
                            showing.chosen,
                            # And no name to give: a fork's title is its parent's, because it
                            # literally carries it - the title is what the first message says, and
                            # the first message came across with the rest.
                        ),
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
