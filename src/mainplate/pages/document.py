# The document every page is, and what every part of it draws with.
#
# The routes as `Links`, the one live connection and the names it and the forms share, and the
# smallest elements more than one part of the page uses (`fact`, `working`, `opens`). Everything
# else in this package imports from here, and this imports only how a moment is printed.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final
from urllib.parse import quote
from urllib.parse import urlencode

from without_html import DOCTYPE
from without_html import Attributes
from without_html import Element
from without_html import VoidElement
from without_html import a
from without_html import body
from without_html import dd
from without_html import div
from without_html import dt
from without_html import element
from without_html import h1
from without_html import head
from without_html import html
from without_html import link
from without_html import meta
from without_html import p
from without_html import render
from without_html import script
from without_html import span
from without_html import svg
from without_html import title
from without_web import Reversible
from without_web import url_for

from mainplate.pages.moments import ZONE_FIELD
from mainplate.pages.moments import Reader

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
# a page drawing the settings step whose session has since loaded its plugins, or a page showing a
# session somebody has since deleted, whose reload is the page saying so. It is named rather
# than a partial because there is nothing to swap - the page it is sent to has none of the regions
# the new shape has - so what a reader needs is the page again, and a named event is what htmx hands
# to a script rather than to a target. The stream element closes on it, and `mainplate.js` reloads.
# Three readers of one word: here, the stream, and the script, which spells it as a literal. It is
# the word `settling`'s other half already uses for the shape past the step.
LOADED: Final = "loaded"


# The query parameter a page states its shape on when it opens the stream. A page showing the
# conversation, and the new-session page, send nothing, since they have no region of their own the
# stream has to know about.
SHAPE_FIELD: Final = "shape"


class Shape(Enum):
    """Which page a stream is talking to, where that decides what the stream sends."""

    SETTLING = "settling"
    """A session's settings step, which has the step and no transcript."""

    DASHBOARD = "dashboard"
    """The dashboard, which has the sessions that want attention as a region of its own."""


# What the dashboard is called, in the tab and nowhere else: it is the console's own front page.
DASHBOARD: Final = "Mainplate"


# The one field that says what files a session has, posted hidden by the new-session page for the
# workspace the dashboard answered. Form-only rather than a recorded field, because what it carries
# is *two* recorded things at once - a repository and a filesystem level - and which two is decided
# by parsing it, at the boundary, once.
WORKSPACE_FIELD: Final = "workspace"

# Which version of an artifact a link names, and where a listing continues from: named here because
# `Links` writes them and `console.py` parses them, and the two must not drift.
VERSION_FIELD: Final = "version"
BEFORE_FIELD: Final = "before"


def rule_id(turn: int) -> str:
    """
    The id of the rule standing at the top of a turn, which is where a link to that turn lands.

    Here rather than beside the rule, because a link to a turn is written by pages that draw no
    transcript: the one spelling is what the rule is drawn with and what `Links.to_turn` points at.
    """
    return f"rule-{turn}"


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
# not exist yet. "Session" rather than "checkout", which is the other word for this and is already
# taken: a session's *checkout* is the directory of its repository's files it works in, so calling
# the session one too would make "a checkout's checkout" a sentence somebody has to parse.
NEW_SESSION: Final = "New session"


# A session created without a name is untitled until its first message arrives.
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
    new_session: Reversible
    start: Reversible
    session: Reversible
    say: Reversible
    stream: Reversible
    seen: Reversible
    endpoint_models: Reversible
    workspace_branches: Reversible
    fork_form: Reversible
    fork: Reversible
    setup: Reversible
    press: Reversible
    rename: Reversible
    archive: Reversible
    delete: Reversible
    job: Reversible
    stop_job: Reversible
    job_output: Reversible
    artifacts: Reversible
    artifact: Reversible
    artifact_content: Reversible
    artifact_download: Reversible
    picture: Reversible
    # A prefix rather than a route, and the one exception: the route serving the assets needs an
    # inventory that does not exist until startup, where every field above is a module-level
    # value. Both are built from one constant, so they cannot disagree about where they are.
    assets: str

    def to_home(self) -> str:
        return url_for(self.home)

    def to_new_session(self, workspace: str) -> str:
        """
        The page that asks what a session in this workspace runs on, before anything is created.

        The workspace in the query string rather than the path, because it is the one answer this
        page's form already carries under that name: the page is the rest of the questions about a
        session in it, and posts the workspace back as it was given. A repository's id holds a colon,
        which is quoted so the value arrives as it was.
        """
        return f"{url_for(self.new_session)}?{urlencode({WORKSPACE_FIELD: workspace})}"

    def to_start(self) -> str:
        return url_for(self.start)

    def to_session(self, session: str) -> str:
        return url_for(self.session, {"session": session})

    def to_say(self, session: str) -> str:
        return url_for(self.say, {"session": session})

    def to_stream(self, session: str | None, shape: Shape | None = None) -> str:
        """
        The connection a page holds open, told which conversation it is showing and in which shape.

        A query parameter for the reason `to_endpoint_models` uses one: it narrows what a single
        connection reports on rather than picking a resource out. The stream is the page's, and the
        session is what the page happens to be looking at. A page looking at no session, which is the
        start page, names none and is sent the one region every page holding a stream has, the
        session list.

        **The shape rides along because the page is the only thing that knows it.** The stream sends
        whichever regions the page's shape has, and a page still drawing the settings step has no
        transcript for a message to land in: the moment the checkpoint's shape stops matching the
        page's, the stream says so once and the page reloads, which is `LOADED`. Sent by the page
        rather than remembered by the stream, so a connection re-opened after the change is told the
        page is still on the step and answers it the same way. The dashboard names itself for the
        same reason from the other side: it is the one page with a region beside the list that the
        stream must send, and a partial for a region a page does not have is dropped on the floor.
        """
        asked = [
            *(() if session is None else (f"session={session}",)),
            *((f"{SHAPE_FIELD}={shape.value}",) if shape is not None else ()),
        ]
        return url_for(self.stream) + (f"?{'&'.join(asked)}" if asked else "")

    def to_seen(self, session: str) -> str:
        """
        Where a page says it has shown the conversation as the connection just sent it.

        The page's to say and not the connection's: the server cannot tell a page that is reading from
        one whose tab has gone dark, since it learns of the latter only when a write fails, and the
        first write after a tab is hidden goes into a socket the browser has already left. The page
        lets go of the connection while hidden, so what it acknowledges is what it was shown. Beside
        the stream under `fragments/` and addressed the same way, because it is the same connection's
        other direction.
        """
        return f"{url_for(self.seen)}?session={session}"

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
        every card rather than one per repository, which is also what lets `only scratch` ask it and get
        a block with nothing to complete.
        """
        return url_for(self.workspace_branches)

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

    def to_rename(self, session: str) -> str:
        """Where the name row posts, which answers with the row itself rather than the page."""
        return url_for(self.rename, {"session": session})

    def to_turn(self, session: str, turn: int) -> str:
        """One turn of a session, as the session's page with the turn's rule as its target."""
        return f"{self.to_session(session)}#{rule_id(turn)}"

    def to_archive(self, session: str) -> str:
        """Where the press that closes a session goes, which is a plain form post answered with a redirect."""
        return url_for(self.archive, {"session": session})

    def to_delete(self, session: str) -> str:
        """Where the press that deletes an archived session goes, answered with a redirect to the dashboard."""
        return url_for(self.delete, {"session": session})

    def to_job(self, session: str, entry: str) -> str:
        """
        Where a job that serves is opened: a route that sends the browser on to its port.

        A route rather than the job's own address, because the address is this console's host as the
        browser reached it, which only the request knows and a page may not ask.
        """
        return url_for(self.job, {"session": session, "entry": entry})

    def to_stop_job(self, session: str, entry: str) -> str:
        """Where the press that stops a job goes, a plain form post answered with a redirect."""
        return url_for(self.stop_job, {"session": session, "entry": entry})

    def to_job_output(self, session: str, entry: str) -> str:
        """What a running job has printed so far, as plain text."""
        return url_for(self.job_output, {"session": session, "entry": entry})

    def to_artifacts(self, before: int | None = None) -> str:
        """Every artifact, newest first; `before` continues a listing from the last one it held."""
        return url_for(self.artifacts) + ("" if before is None else f"?{urlencode({BEFORE_FIELD: before})}")

    def to_artifact(self, artifact: str, version: int | None = None, before: int | None = None) -> str:
        """
        One artifact's page, at `version` or at whichever is current when it is opened.

        Unpinned is what a catalogue row links to, since it is listing the artifact and a reader
        following it wants what the artifact is now. A call's link to the version it kept pins it, as
        does a row of a version history, so it opens on what was kept however far the artifact has
        moved since. `before` pages the version history the same way the catalogue's pages.
        """
        asked = {
            **({} if version is None else {VERSION_FIELD: version}),
            **({} if before is None else {BEFORE_FIELD: before}),
        }
        return url_for(self.artifact, {"artifact": artifact}) + (f"?{urlencode(asked)}" if asked else "")

    def to_artifact_content(self, artifact: str, version: int) -> str:
        """The bytes of one version, as the preview frames them. Always pinned, so a preview cannot change under a reader."""
        return f"{url_for(self.artifact_content, {'artifact': artifact})}?{urlencode({VERSION_FIELD: version})}"

    def to_artifact_download(self, artifact: str, version: int) -> str:
        """The same bytes as `to_artifact_content`, served to be saved rather than shown."""
        return f"{url_for(self.artifact_download, {'artifact': artifact})}?{urlencode({VERSION_FIELD: version})}"

    def to_picture(self, session: str, turn: int, call: str, index: int) -> str:
        """
        One image a call handed the model, named by the record it is in and its place there.

        Path segments rather than a query, because each one narrows to a single thing that exists
        independently: a record is a turn's and a call's, and the image is one of that record's.

        **The call id is quoted, because a wire mints it and nothing promises it is a path segment.**
        `url_for` puts a value in as it is, which is right for this console's own hex ids and wrong
        for a string a provider chose: a space or a `|` in one is a request line no client will send.
        """
        return url_for(self.picture, {"session": session, "turn": turn, "call": quote(call, safe=""), "index": index})

    def to_asset(self, name: str) -> str:
        return f"{self.assets}/{name}"


# Which of the bundled extensions this console installs. An allowlist rather than a bundle taken
# whole: `htmax` registers everything it carries on inclusion, and several of those would change
# how this page behaves without being asked for - `history-cache` would put back the history store
# htmx 4 deliberately removed, and `hx-live` and `alpine-compat` are reactive scripting this
# console does not want. htmx reads it from the meta tag before any extension registers, so a name
# absent here is never installed rather than installed and unused.
EXTENSIONS: Final = "sse"


def stream_element(links: Links, session: str | None, shape: Shape | None = None) -> Element:
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

    On every page that draws the session list, the start page included, because the list is a region
    of every one of them and it moves when any session does: a session answered while somebody was
    choosing what to start next is the case. A page with no list, which is a refusal or an artifact's
    page, holds none, since a connection with nothing to report on would be a held socket and a
    heartbeat.

    The connection is let go while the page's tab is hidden and taken up again when it is shown, which
    is htmx's own `pauseOnBackground` and not anything this console does: a hidden page is not being
    read, so it costs the console no polling and acknowledges nothing. Coming back to the tab
    reconnects, and the first message on a connection is always the whole current state, so the page
    is current at once.

    `data-seen` is where the page says it has shown what a message carried, which is how a session is
    marked as looked at while it is open; see `Links.to_seen`. On the element that receives the
    messages, so the thing acknowledging is the thing that was sent to.
    """
    return div(
        attrs={
            "id": STREAM_ID,
            "hidden": True,
            "hx-sse:connect": links.to_stream(session, shape),
            "data-seen": None if session is None else links.to_seen(session),
            "hx-sse:close": LOADED,
            "hx-target": "this",
            "hx-swap": "innerHTML",
        }
    )


def document(
    links: Links,
    heading: str,
    children: Element,
    reader: Reader | None = None,
    session: str | None = None,
    forked_from: str | None = None,
    shape: Shape | None = None,
    live: bool = True,
) -> str:
    """
    The whole document, which every page is this with something different in the middle.

    `reader` is what this page's moments were drawn against, said on `<html>` so the script can tell
    whether it is this browser's. It is on the page rather than asked for by it: the server has
    already answered the question by the time anything is rendered, and a page that arrived drawn
    against the wrong clock is one the script asks for again rather than one it rewrites. See
    `paintClock`, and `reader_in` for where the answer comes from. A page with no moment on it says
    nothing, which is a refusal.

    **On `<html>` rather than on `<body>`, which is the whole reason the reload is not seen.** The
    script's first block runs before the body exists - that is what pins the theme without a flash -
    so an answer parked on the body cannot be read until the document is, and a reader in another
    zone would paint a full page of the wrong times before asking for the right ones. The open tag of
    `<html>` has been parsed by the time that block runs, so the check happens there and the page
    that gets thrown away was never painted. Same reason as the theme, one attribute along.

    `shape` is which shape the page was drawn in where the stream has to know it, and it goes on the
    stream element; see `Links.to_stream`. `live` is whether the page
    holds that connection at all, which every page with the session list on it does and a refusal
    and an artifact's page do not; see `stream_element`.

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
                attrs={
                    "lang": "en",
                    ZONE_FIELD: None if reader is None else reader.zone.key,
                    # Where the diagram library is, for the script to fetch the first time a
                    # drawing is asked for and never on load: the address is the server's to say,
                    # and the three and a half megabytes are a page that draws nothing's to skip.
                    "data-mermaid": links.to_asset("mermaid.min.js"),
                },
                children=[
                    head(
                        children=[
                            meta(attrs={"charset": "utf-8"}),
                            # `interactive-widget=resizes-content` asks a phone to shrink the layout
                            # viewport under its keyboard rather than lay the keyboard over the page,
                            # so the shell's `100dvh` ends where the keyboard begins and the box sits on
                            # it. Chrome and Firefox honour it; Safari does not, and `wireKeyboard` in
                            # the script is what covers that. See the assets note.
                            meta(
                                attrs={
                                    "name": "viewport",
                                    "content": "width=device-width, initial-scale=1, interactive-widget=resizes-content",
                                }
                            ),
                            meta(
                                attrs={
                                    "name": "theme-color",
                                    "content": "#f4f2ee",
                                    "media": "(prefers-color-scheme: light)",
                                }
                            ),
                            meta(
                                attrs={
                                    "name": "theme-color",
                                    "content": "#131316",
                                    "media": "(prefers-color-scheme: dark)",
                                }
                            ),
                            meta(attrs={"name": "apple-mobile-web-app-capable", "content": "yes"}),
                            meta(attrs={"name": "apple-mobile-web-app-title", "content": "mainplate"}),
                            meta(attrs={"name": "apple-mobile-web-app-status-bar-style", "content": "default"}),
                            title(children=heading),
                            link(attrs={"rel": "icon", "href": links.to_asset("icon.svg"), "type": "image/svg+xml"}),
                            link(attrs={"rel": "apple-touch-icon", "href": links.to_asset("apple-touch-icon.png")}),
                            # Chromium fetches a manifest with credentials omitted, same origin or not, unless
                            # the link says otherwise, so a login in front of the console must see the cookie.
                            link(
                                attrs={
                                    "rel": "manifest",
                                    "href": links.to_asset("manifest.webmanifest"),
                                    "crossorigin": "use-credentials",
                                }
                            ),
                            link(attrs={"rel": "stylesheet", "href": links.to_asset("mainplate.css")}),
                            meta(attrs={"name": "htmx-config", "content": f"extensions: {EXTENSIONS}"}),
                            script(attrs={"src": links.to_asset("htmax.min.js")}),
                            script(attrs={"src": links.to_asset("mainplate.js")}),
                        ]
                    ),
                    body(
                        attrs={
                            "data-session": session,
                            "data-forked-from": forked_from,
                        },
                        children=[*((stream_element(links, session, shape),) if live else ()), children],
                    ),
                ],
            ),
        ]
    )


def fact(key: str, value: str, *, cls: str | None = None, title: str | None = None) -> tuple[Element]:
    """
    One row of a card of facts: what it is, and what it is, as the pair a `dl` is made of.

    The pair is in a `div` of its own, which a `dl` allows, because the row is what wraps: the
    stylesheet keeps a value beside its key where it fits and drops it to the line under where it
    does not, and only a box around the pair can say which. The value carries the class and the
    title, since the value is what a test finds and what a reader hovers; the key is the same word
    on every session and needs neither. A one-tuple, so a row is spread into a card's children the
    same way a conditional row is.
    """
    return (div(cls="fact", children=[dt(children=key), dd(cls=cls, attrs={"title": title}, children=value)]),)


def working(*, saying: str = "working", extra: str | None = None, identified: str | None = None) -> Element:
    """
    Three dots that say something is still happening.

    Drawn the same whichever thing is still happening, because a reader is being told the same
    thing, and that is the whole of what these share. A turn with no answer yet, a tool call that has
    not returned, a session whose checkout is still being planted: each is a fact read off the
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


type Placed = Element | VoidElement | None
"""One thing a caller hands the composer to put above or below the box, or nothing at all."""


def home(links: Links) -> Element:
    """
    The console's mark and name, as the way back to the dashboard from wherever a page is.

    **This is the one piece of navigation every page owes a reader**, rather than the session list:
    the dashboard is where every other place is reached from, so a page with this on it is never a
    dead end, and a page whose subject is not a conversation can leave the list off without leaving
    somebody stranded. The session list draws it at its head and an artifact's bar draws it at its
    left, as one element, so the way home looks the same from both.

    A link that reads as where you are rather than a button that reads as something to do, since
    the presses that start something are on the dashboard's cards. The mark is drawn by reference
    rather than as an `<img>`, so the stylesheet can hand the plate the theme's colours: an image
    only ever sees the OS's.
    """
    return a(
        cls="home",
        attrs={"href": links.to_home()},
        children=[
            svg(
                cls="home__mark",
                attrs={"viewBox": "0 0 512 512", "aria-hidden": "true"},
                children=element("use", attrs={"href": f"{links.to_asset('icon.svg')}#plate"}),
            ),
            span(cls="home__name", children=DASHBOARD),
        ],
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
        live=False,
    )


def fragment(element: Element) -> str:
    """One element and no document, which is what a swap wants."""
    return render(element)
