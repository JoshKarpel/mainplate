# The console's routes, and what each of them reads or writes.
#
# Nothing here runs an agent. A write puts a message into a session's checkpoint and asks for the
# session to be looked at; the worker is what turns that into a model call, in its own time and
# possibly in another process. That is the whole reason a reply can outlive the request that
# asked for it, and it is why every one of these handlers is short.

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import assert_never
from urllib.parse import parse_qs

from pydantic_ai.settings import ThinkingLevel
from without_asgi import Response
from without_asgi import html_content
from without_asgi.sse import event_stream
from without_asgi.sse import with_heartbeat
from without_web import INT
from without_web import STR
from without_web import ExtractionError
from without_web import Reply
from without_web import Route
from without_web import body
from without_web import get
from without_web import once
from without_web import optional
from without_web import path_param
from without_web import post
from without_web import query_param

from mainplate.agent import Choice
from mainplate.conversation import BASE_FIELD
from mainplate.conversation import BRANCH_FIELD
from mainplate.conversation import DISPOSITION_FIELD
from mainplate.conversation import NETWORK_FIELD
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import TRUSTED_FIELD
from mainplate.conversation import Disposition
from mainplate.conversation import parse_disposition
from mainplate.pages import PLUGIN_LEADER
from mainplate.pages import SETTLING
from mainplate.pages import SHAPE_FIELD
from mainplate.pages import WORKSPACE_FIELD
from mainplate.pages import Links
from mainplate.pages import fork_page
from mainplate.pages import fragment
from mainplate.pages import missing_record
from mainplate.pages import model_cards
from mainplate.pages import plugin_card
from mainplate.pages import record_json
from mainplate.pages import refusal_page
from mainplate.pages import session_page
from mainplate.pages import settling
from mainplate.pages import stalled_by
from mainplate.pages import start_page
from mainplate.pages import starting_at
from mainplate.pages import transcript_region
from mainplate.plugins.protocol import settings_of
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.service import Service
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import read_tending
from mainplate.snapshots import parse_branch
from mainplate.snapshots import parse_commitish
from mainplate.streaming import watching
from mainplate.tending import AGAIN
from mainplate.tending import ENABLED_FIELD
from mainplate.tending import PLUGIN_FIELD
from mainplate.tending import SETTLE_FIELD
from mainplate.thinking import DEFAULT_THINKING
from mainplate.thinking import UnknownThinking
from mainplate.thinking import thinking_named

ASSETS = "/assets"

LOCATION = b"location"

# A bound on what one message may be, so a request that is not a message cannot be buffered into
# this process's memory as if it were. Generous by the standards of a chat box and small by the
# standards of anything else.
LONGEST_PROMPT = 100_000

session_id = path_param("session", STR)
# The endpoint whose models to render, which is the value of the select that asks for them.
of_endpoint = query_param("endpoint", once(str), schema={"type": "string"})
# Which workspace's branches to offer, which is the value of the card that asks for them. A query
# parameter for the reason the endpoint is one: htmx sends a triggering input's own value, so the
# card needs no interpolation and no script to build a URL.
of_workspace = query_param(WORKSPACE_FIELD, once(str), schema={"type": "string"})
# Which conversation a watching page is showing. A query parameter rather than a path segment
# because the stream belongs to the page: this narrows what one connection reports on, where a path
# segment would say the connection is a thing *of* that session. It is what lets a second region
# join the same connection later without the path becoming a lie.
watched = query_param("session", once(str), schema={"type": "string"})


def parse_shape(value: str) -> bool:
    """Whether a page said it is on the settings step, refusing any other word for a shape."""
    if value != SETTLING:
        raise ValueError(f"a page's shape is {SETTLING!r} or unstated, not {value!r}")
    return True


# Which shape the watching page was drawn in, which only the settings step states: a page showing
# the conversation says nothing, so absent is that shape. Parsed at the boundary into the boolean
# the stream reads, rather than carried as the word.
shaped = query_param(SHAPE_FIELD, optional(parse_shape), schema={"type": "string", "enum": [SETTLING]})
# Which turn a fork would start at, which is the first turn the branch does not inherit.
at_turn = query_param("at", once(int), schema={"type": "integer"})
# The two halves of a panel's identity, in the path because that is what they are: a panel is named
# by its turn and its position within it, which is the same pair its anchor and its label are built
# from. A query string would say these narrow something down, where they pick one thing out.
of_turn = path_param("turn", INT)
at_panel = path_param("at", INT)


class NotAMessage(ValueError):
    """A form post that did not carry a message this console could send."""


def fields_in(raw: bytes) -> Mapping[str, list[str]]:
    """
    A posted form as its fields, bounded so that a request which is not one cannot be buffered whole.

    The one place a body's size is checked, because it is the one place a body is read. Generous by
    the standards of a chat box and small by the standards of anything else.
    """
    if len(raw) > LONGEST_PROMPT:
        raise NotAMessage(f"a form may be at most {LONGEST_PROMPT} bytes")
    return parse_qs(raw.decode("utf-8", errors="replace"))


def said_in(fields: Mapping[str, list[str]]) -> str:
    """
    The message a form carried, or the empty string where the box was empty.

    `parse_qs` drops empty values, so a form submitted with an empty box arrives as no field at all
    rather than as an empty string, and both are the same answer here. Whether an empty one is a
    refusal is the caller's, because it depends on where the message was going: every disposition but
    `HANDOFF` demands one.
    """
    return fields.get("prompt", [""])[0].strip()


def parse_form_prompt(raw: bytes) -> str:
    """The message a form carried, refused if there is not one. What starting a session takes."""
    said = said_in(fields_in(raw))
    if not said:
        raise NotAMessage("a message cannot be empty")
    return said


prompt = body(parse_form_prompt, schema={"type": "string"}, media_type="application/x-www-form-urlencoded")


def posted_switches(fields: Mapping[str, list[str]]) -> dict[str, bool]:
    """
    Which plugins the settings step said this session runs, as a switch per qualified name.

    **An absent switch is off**, because an unchecked checkbox posts no field, and there is no third
    reading available to a form: this is what somebody just said, where the column's own absence is
    what nobody has ever said. So the form carries a hidden name per plugin beside each box, which
    is what makes the difference between "off" and "not on this form" representable at all.

    The names are read off the form rather than checked against a list, because what a session
    enrolled is the service's answer and a name that is not one of them is a write nothing will read.
    """
    return {
        name.removeprefix(f"{ENABLED_FIELD}:"): values[-1] == "on"
        for name, values in fields.items()
        if name.startswith(f"{ENABLED_FIELD}:")
    }


@dataclass(frozen=True, slots=True)
class SettingUp:
    """
    What the settings step posted: which plugins to run, and which of its two buttons was pressed.

    The button is a field rather than the shape of the post, because the two answers are not
    distinguishable by shape: a step with every switch off posts the same emptiness as a step with
    nothing to switch, and reading that as "try again" would silently discard the one answer somebody
    took the trouble to give.
    """

    switches: Mapping[str, bool]
    again: bool


def parse_form_setup(raw: bytes) -> SettingUp:
    """The settings step's own post, which carries a switch per plugin and the button that was pressed."""
    fields = fields_in(raw)
    return SettingUp(switches=posted_switches(fields), again=fields.get(SETTLE_FIELD, [""])[0] == AGAIN)


setting_up = body(parse_form_setup, schema={"type": "object"}, media_type="application/x-www-form-urlencoded")


@dataclass(frozen=True, slots=True)
class Pressed:
    """
    One plugin's card as the form holding it posted, which is every control on it at once.

    Which plugin is a value rather than a place in the resource tree, because a card *is* a form and
    what it posts names what it is about. That is what keeps one route serving every plugin, so a
    page can hold the address without knowing what a session enrolled.

    **The whole card, not the one control that moved**, because a form has no way to say which did:
    the box submits every control it holds whether the trigger was `Set` or a switch changing. What
    the service does with that is compare each against what is stored and tell the plugin about the
    ones that actually moved, which is both the honest reading and the one an `action` event means.

    `posted` holds only what the browser sent, so a switch that is off is simply absent - and that is
    resolvable rather than ambiguous, because the card declares every control it has and the service
    holds the card.
    """

    plugin: str
    posted: Mapping[str, str]


def parse_form_press(raw: bytes) -> Pressed:
    """
    What a plugin's card posted, as the plugin it belongs to and the controls it carried.

    Every control on a card is named `plugin:<qualified>:<control>`, so the plugin is read off the
    first field and the rest are held to naming the same one: a post carrying two plugins' controls
    is not a card this console drew.

    **Cut from the right and never from the left**, which is the one thing here that is easy to get
    wrong: a qualified name is `<tier>:<key>` and already holds a colon, so splitting at the first
    one names the *tier* and every press would look like a plugin called `bundled`. The control name
    is the last segment and the plugin is everything before it.
    """
    fields = fields_in(raw)
    named = {name: values[0] for name, values in fields.items() if name.startswith(f"{PLUGIN_FIELD}:")}
    if not named:
        raise NotAMessage("a card posts at least one of its own controls")
    plugin, _, _ = next(iter(named)).removeprefix(f"{PLUGIN_FIELD}:").rpartition(":")
    if not plugin:
        raise NotAMessage("a posted control does not name a plugin")
    posted: dict[str, str] = {}
    for name, value in named.items():
        held, _, control = name.removeprefix(f"{PLUGIN_FIELD}:").rpartition(":")
        if held != plugin or not control:
            raise NotAMessage(f"{name!r} is not a control of {plugin!r}")
        posted[control] = value
    return Pressed(plugin=plugin, posted=posted)


pressing = body(parse_form_press, schema={"type": "object"}, media_type="application/x-www-form-urlencoded")


@dataclass(frozen=True, slots=True)
class ToPlugin:
    """
    One of this session's plugins, named by the leader it answers to.

    Its own arm rather than a member of `Disposition`, because a disposition is a closed set this
    console owns and a leader is a word a session's own plugins claim: there is no list here to add
    one to, and which leaders exist is a fact about a session rather than about this module.
    """

    leader: str


@dataclass(frozen=True, slots=True)
class Sending:
    """What the composer posted: a message, and where it is going."""

    said: str
    where: Disposition | ToPlugin


def parse_form_send(raw: bytes) -> Sending:
    """
    The message the composer carried and the disposition it was sent under.

    Parsed together for the reason `parse_form_start` parses its fields together: they arrive in one
    body and mean nothing apart. An **absent** field is `HERE`, because a form predating the control
    posts a message and means what Send has always meant; a field naming something this console does
    not offer is a refusal, because guessing which destination somebody meant is the one thing that
    could silently put a message in the wrong conversation.

    **The disposition is read first, because it decides whether an empty box is a fault.** Every
    answer demands a message except `HANDOFF`, whose text is an optional note saying what the handoff
    should dwell on, and the ordinary handoff has nothing typed into it. That is the same split the
    button carries as `formnovalidate`: the browser refuses an empty box for every other submitter,
    and this is where the exception is honoured rather than trusted.
    """
    fields = fields_in(raw)
    named = fields.get(DISPOSITION_FIELD, [""])[0].strip()
    where: Disposition | ToPlugin | None
    if named.startswith(PLUGIN_LEADER):
        # A plugin's own answer, named by the leader it claims. Whether this session has one is not a
        # question this layer can put: what leaders exist is a fact about what that session
        # registered, so the handler asks the service and refuses there.
        where = ToPlugin(leader=named.removeprefix(PLUGIN_LEADER))
    else:
        where = Disposition.HERE if not named else parse_disposition(named)
    if where is None:
        raise NotAMessage(f"{named!r} is not somewhere a message can be sent")
    said = said_in(fields)
    # **Every console answer demands a message and a plugin's own may not**, which is what `demands`
    # on a declared answer says. This layer cannot tell which, for the reason above, so an empty box
    # is allowed through to the handler and the plugin is what does or does not mind.
    if not said and not isinstance(where, ToPlugin):
        raise NotAMessage("a message cannot be empty")
    return Sending(said=said, where=where)


sending = body(parse_form_send, schema={"type": "object"}, media_type="application/x-www-form-urlencoded")


def parse_form_start(raw: bytes) -> Started:
    """
    The whole of what the new-session form carries: a message, what to answer it with, and a name.

    Parsed together rather than in two extractors because they arrive in one body and are refused
    on one condition: a form that names an endpoint without a message is not half a request, it is
    not a request. Whether the pair is *configured* is not asked here, because this layer has no
    configuration; the handler asks that of the `Service` and refuses with a status of its own.

    The thinking level is the one field this layer can settle by itself, because unlike an endpoint
    and a model it is a closed set rather than something discovered. An absent field is the
    configured default rather than a refusal, so a form posted by something that predates the
    control still names a session's whole choice.

    The base and the branch are **refused** rather than dropped when they are not usable, and that is
    the split `posted_workspace` already makes: an empty box means somebody wants the default, where
    `my branch` in the box is somebody who meant something and would otherwise get a session quietly
    started somewhere else. Both become `git` arguments, so refusing here is also what keeps a
    leading `-` from ever reaching one.
    """
    fields = fields_in(raw)
    endpoint = fields.get("endpoint", [""])[0].strip()
    model = fields.get("model", [""])[0].strip()
    if not endpoint or not model:
        raise NotAMessage("a session needs an endpoint and a model to be answered on")
    return Started(
        # Optional, and an empty box is the same as no field at all: `parse_qs` drops empty values,
        # so both arrive here as nothing and both mean "name it after the first thing said". The
        # length is bounded by the form bound above, and cut to a name by `Service.start`, which is
        # where the one rule about what a session name is already lives.
        title=fields.get(TITLE_FIELD, [""])[0].strip() or None,
        chosen=Choice(
            endpoint=endpoint,
            model=model,
            repository=posted_workspace(fields)[0],
            base=posted_ref(fields, BASE_FIELD, parse_commitish, "a commit, branch or tag"),
            branch=posted_ref(fields, BRANCH_FIELD, parse_branch, "a branch name"),
            isolation=posted_isolation(fields),
            trusted=posted_trust(fields),
            thinking=posted_thinking(fields),
        ),
    )


def posted_trust(fields: Mapping[str, list[str]]) -> bool:
    """
    Whether this session runs code the repository carries, which is what the picker's third card asks.

    **Absent is trusted**, which is the default said as a parse and the opposite of how the network
    radio reads: refusing is the thing somebody has to have actually said, since a form predating the
    control names a session that would have run a repository's plugins. A session with no repository
    is settled to trusted by `Choice.settled` whatever arrives here, because there is nothing for it
    to be about.
    """
    return fields.get(TRUSTED_FIELD, ["on"])[0].strip() == "on"


def posted_ref(
    fields: Mapping[str, list[str]], named: str, parsing: Callable[[str], str | None], wanted: str
) -> str | None:
    """
    One posted git ref as the value it names, nothing where the box was empty, and a refusal where
    it holds something that is not one.

    Three answers from two, which is why this is not `parsing(...)` at the call site: an empty box
    and an unusable one are the same to the parser and must not be to the form. Blank is somebody
    taking the default; anything else is somebody who meant a particular thing, and starting a
    session somewhere other than where they said is the mistake worth a `422`.
    """
    written = fields.get(named, [""])[0].strip()
    if not written:
        return None
    found = parsing(written)
    if found is None:
        raise NotAMessage(f"{written!r} is not {wanted}")
    return found


def posted_workspace(fields: Mapping[str, list[str]]) -> tuple[str | None, Filesystem]:
    """
    What files a form asked for, as the repository and the filesystem level it means.

    One posted field becoming two recorded values, parsed once here at the boundary. That is the
    whole point of the control being one group: a repository and the level it implies are one answer,
    so they are read out of one value and cannot arrive disagreeing.

    A value is either a `Filesystem` member's own name or a repository id, told apart without a
    prefix because an id is `forge:key` and so always holds a colon. An absent field is `NOTHING`
    rather than a refusal, so a form predating the control still names a whole choice and names the
    tightest answer, which is what a session had before there was anything to ask.
    """
    named = fields.get(WORKSPACE_FIELD, [""])[0].strip()
    if not named:
        return None, Filesystem.NOTHING
    if ":" in named:
        return named, Filesystem.WORKTREE
    try:
        return None, Filesystem(named)
    except ValueError:
        raise NotAMessage(f"{named!r} is not something a session can work in") from None


def posted_isolation(fields: Mapping[str, list[str]]) -> Isolation:
    """
    How confined a form asked for, as the two axes together.

    The network is a closed set, so this layer settles it the way it settles the thinking level and
    for the same reason: unlike an endpoint it is not discovered. A radio that is not checked posts
    no field at all, so an absent one has to mean off, which is also the safe answer.

    Nothing here reconciles the filesystem with the repository, and after the merge nothing needs to:
    they come out of one posted value. `Isolation.settled` is still what the service applies, because
    a fork's repository is inherited rather than posted and a form is not the only way in.
    """
    _, reaching = posted_workspace(fields)
    return Isolation(filesystem=reaching, network=fields.get(NETWORK_FIELD, [""])[0].strip() == "on")


def posted_thinking(fields: Mapping[str, list[str]]) -> ThinkingLevel | None:
    """
    The thinking level a form named, refused in this layer's own terms if it is not one.

    `UnknownThinking` is turned into `NotAMessage` rather than propagating, because the two mean
    the same thing here and only one of them is answered as a client error: a select is a
    suggestion the page made, so a value outside it came from something that is not this page.
    """
    named = fields.get(THINKING_FIELD, [DEFAULT_THINKING])[0].strip() or DEFAULT_THINKING
    try:
        return thinking_named(named)
    except UnknownThinking as unknown:
        raise NotAMessage(str(unknown)) from unknown


@dataclass(frozen=True, slots=True)
class Started:
    """A new session as the form describes it, before anything has decided it is possible."""

    chosen: Choice

    title: str | None = None
    """What to call it, or nothing at all to name it after its first message as every session was."""


starting = body(parse_form_start, schema={"type": "object"}, media_type="application/x-www-form-urlencoded")


@dataclass(frozen=True, slots=True)
class Forking:
    """A fork as the form describes it, before anything has decided it is possible."""

    at: int
    chosen: Choice
    said: str | None


def parse_form_fork(raw: bytes) -> Forking:
    """
    Where to fork, what to answer it with, and what to ask it first.

    The message is optional, which is the difference from `parse_form_start`: forking the end of a
    conversation has nothing to re-ask, where forking one of its turns carries that turn's own
    message back for asking again. An empty box is the first of those rather than a refusal, so a
    fork that is only meant to carry a past is a form somebody can submit.

    The turn is refused rather than defaulted, because a fork that silently branched at turn zero
    would throw away the conversation somebody meant to keep.
    """
    if len(raw) > LONGEST_PROMPT:
        raise NotAMessage(f"a message may be at most {LONGEST_PROMPT} bytes")
    fields = parse_qs(raw.decode("utf-8", errors="replace"))
    endpoint = fields.get("endpoint", [""])[0].strip()
    model = fields.get("model", [""])[0].strip()
    at = fields.get("at", [""])[0].strip()
    if not endpoint or not model:
        raise NotAMessage("a fork needs an endpoint and a model to be answered on")
    if not at.isdigit():
        raise NotAMessage("a fork needs the turn it forks at")
    return Forking(
        at=int(at),
        chosen=Choice(
            endpoint=endpoint,
            model=model,
            # Only meaningful for a fork of a session that has no repository, and the service is
            # what decides that: one already in a repository keeps it whatever arrives here.
            repository=posted_workspace(fields)[0],
            isolation=posted_isolation(fields),
            thinking=posted_thinking(fields),
        ),
        said=said_in(fields) or None,
    )


forking = body(parse_form_fork, schema={"type": "object"}, media_type="application/x-www-form-urlencoded")


def page_response(status: int, markup: str) -> Response:
    return Response.from_content(status, html_content(markup))


async def recover(raised: Exception) -> Response | None:
    """
    What a request nothing could read is answered with, which is a page and a client status.

    The whole of the policy, because the whole of what this console parses is one form field.
    Left unhandled these reach the ASGI plumbing as a `500` and a line of plain text, which says
    the server broke over a request that was simply not a message.

    Anything else propagates. A `ValueError` raised deeper in a handler is a fault here, and
    answering it as a client error would hide it; `ExtractionError` is the boundary type that
    keeps those two apart.
    """
    match raised:
        case ExtractionError(cause=NotAMessage() as why):
            return page_response(422, refusal_page(LINKS, 422, str(why)))
        case ExtractionError():
            return page_response(400, refusal_page(LINKS, 400, "this request could not be read"))
        case _:
            return None


def seeing(where: str) -> Response:
    """
    Where to look now that a session exists, as a `303`.

    `303` rather than `302`, because the browser must follow it with a `GET`: the action was a
    POST, and a refresh repeating it would start a second session saying the same thing. It is
    also why this action has no rendering of its own to keep in step with the session page.
    """
    return Response(status=303, headers=((LOCATION, where.encode()),))


def navigating(where: str) -> Response:
    """
    Where to look now, for a request htmx is driving that made somewhere new to look.

    Named for what it does rather than for where it points, so it reads against `seeing` above: both
    say "look here now" and what separates them is *who* is asked to go, the browser following a
    `303` or htmx performing a navigation.

    `HX-Redirect` and not a `303`, and the difference is what the browser ends up showing. htmx
    follows a redirect itself and swaps whatever comes back into the target, so a `303` here would
    put the new session's transcript inside the old session's page and leave the reader at the
    parent's URL with the branch's conversation in it. This is read before any swap is considered
    and sets `location.href`, so the branch is arrived at properly and can be reloaded and linked.

    A `200` with no body, because htmx never renders one for a navigation and a status saying
    "created, go here" is not something the fetch layer would act on.
    """
    return Response(status=200, headers=((b"hx-redirect", where.encode()),))


async def redrawn(service: Service, session: str) -> Response:
    """
    The transcript as it now stands, which is what every arm that changed one answers with.

    A message, a command and a plugin's delivery all end the same way, because the page is a function
    of the checkpoint and the only thing they did was move it: there is nothing for an arm to say
    about its own write that reading again does not already show.
    """
    asked = await service.read(session)
    if asked is None:  # pragma: no cover - read a line ago, and nothing deletes a session
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return page_response(200, fragment(transcript_region(LINKS, asked)))


@get("/", summary="Start a session")
async def start_here(service: Service) -> Response:
    return page_response(
        200,
        start_page(
            LINKS,
            await service.listed(),
            service.catalogues.current,
            service.reachable,
            service.references.current,
        ),
    )


@post("/sessions", starting, summary="Create a session on a chosen endpoint and model")
async def start(service: Service, started: Started) -> Response:
    """
    Mint a session on the chosen endpoint, and go to it so it can be set up.

    **Nothing is said in it here**, which is the change the plugin protocol forced: a repository's
    plugin cannot be *named* until its worktree is planted, the worker plants it, and a session's
    settings step is drawn from what those files declared. So this records the choice and asks for a
    pass, and the message box is on the session's own page once there is a session to type into.

    An ordinary form post rather than an htmx one, because this is the request that changes which
    session the browser is looking at, and htmx never sees a redirect: the browser follows it
    internally and htmx is handed the final response, so a swap-driven version would render the
    new session into the old page's URL.

    The pair is checked here rather than trusted, because it arrived in a form: a select is a
    suggestion a browser was given, not a constraint on what somebody can post, so this is where a
    new session is held to a pair the picker actually rendered.

    It is the one place the *pair* is checked, and deliberately stricter than what stops an
    existing session, which is a missing endpoint alone. The asymmetry is the point: a conversation
    already under way should not be broken by a model quietly leaving a list, while a new one has
    no reason to start on something nobody was shown.
    """
    if not service.catalogues.current.offers(started.chosen.endpoint, started.chosen.model):
        return page_response(422, refusal_page(LINKS, 422, f"no endpoint on offer serves {started.chosen.model}"))
    # The repository is held to what the picker offered for the same reason the pair is, and the
    # check is only made where one was asked for: a session with no repository is an ordinary
    # session, and this console answered nothing else until repositories existed.
    if started.chosen.repository is not None and not service.reaches(started.chosen.repository):
        return page_response(422, refusal_page(LINKS, 422, f"no forge reaches {started.chosen.repository}"))
    session = await service.start(started.chosen, started.title)
    return seeing(LINKS.to_session(session.id))


@get(t"/sessions/{session_id}/forks/new", session_id, at_turn, summary="Where a fork would start")
async def fork_form(service: Service, session: str, at: int) -> Response:
    """
    The page that asks what to answer a branch with, before anything is created.

    A page of its own rather than a control inside the transcript, and the reason is what the
    transcript is: a region re-rendered whenever the turn in flight records anything. A picker
    rendered per person panel would be rebuilt under the reader's hand every time, and there would
    be one per turn. Here the question is asked once, on a page that is not swapping, and the answer
    arrives as an ordinary form post that a browser with no script can make.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    if not 0 <= at <= found.said.turns:
        return page_response(404, refusal_page(LINKS, 404, f"session {session} has no turn {at}"))
    return page_response(
        200,
        fork_page(
            LINKS,
            await service.listed(),
            found,
            at,
            service.catalogues.current,
            service.reachable,
            service.references.current,
        ),
    )


@post(t"/sessions/{session_id}/forks", session_id, forking, summary="Fork a session at one of its turns")
async def fork(service: Service, session: str, branch: Forking) -> Response:
    """
    Make the branch and go to it, which is the one moment a session's choice may differ.

    The pair is checked exactly as `start` checks it, and for the same reason: a select is a
    suggestion the page made rather than a constraint on what can be posted. A fork is the one
    place the model may change, so it is also where that check has to happen a second time.

    A `303` for the reason `start` returns one: this created something, and the browser must arrive
    at it with a `GET` so a refresh does not branch again.
    """
    if not service.catalogues.current.offers(branch.chosen.endpoint, branch.chosen.model):
        return page_response(422, refusal_page(LINKS, 422, f"no endpoint on offer serves {branch.chosen.model}"))
    # A repository is only ever *attached* here, so it is checked on the same terms a new session's
    # is. Whether it may be attached at all is the service's, since only it knows what the parent
    # is already in; a posted repository for a session that has one is ignored rather than refused.
    if branch.chosen.repository is not None and not service.reaches(branch.chosen.repository):
        return page_response(422, refusal_page(LINKS, 422, f"no forge reaches {branch.chosen.repository}"))
    forked = await service.fork(session, at=branch.at, chosen=branch.chosen, said=branch.said)
    if forked is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return seeing(LINKS.to_session(forked.id))


@get("/fragments/models", of_endpoint, summary="One endpoint's model cards")
async def endpoint_models(service: Service, endpoint: str) -> Response:
    """
    The model cards for an endpoint, which is what changing the endpoint swaps in.

    A fragment rather than a script over a table of models embedded in the page: what an endpoint
    offers is discovered and is refreshed while the page is open, so a list serialized into the
    document at render time is the one thing guaranteed to go stale, and cards rebuilt from the
    server cannot drift from what the form will actually be checked against. The reference is read
    here for the same reason, out of the holder the refresher writes, so a swap that lands after one
    has been read carries the same facts the first render would have.
    """
    found = service.catalogues.current.models_of(endpoint)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no endpoint {endpoint}"))
    return page_response(200, fragment(model_cards(found, service.references.current)))


@get("/fragments/branches", of_workspace, summary="Where a session on this workspace could start")
async def workspace_branches(service: Service, workspace: str) -> Response:
    """
    The branches a repository has, as the completions beside the field that asks where to start.

    Asked when a workspace card is picked, so the list is that repository's rather than the one that
    happened to be checked when the page was drawn. On demand rather than serialized into the page
    for every repository at once: a console reaching six repositories would make six network calls to
    render a page on which five of the lists are never looked at.

    **Nothing about a repository makes this refuse.** One no forge reaches and one whose host is not
    answering are the same block with nothing to complete, which is exactly the field as it was
    before it offered anything. That is `forge.offers`'s promise rather than `catalogue.discover`'s
    refusal, and the difference is the usual one - the field takes free text either way, so having no
    completions costs a suggestion and not an ability. A workspace value this console does not
    recognise is the one refusal, because that is a malformed request rather than an answer about an
    environment, and the card's own `hx-status:4xx` leaves the block standing.

    A workspace that is not a repository is answered with the empty block, which takes the fields
    themselves off the page: a base and a branch are answers *about* a repository, and `no files` has
    none for them to be about. Answered rather than left alone, because the previous repository's
    fields and completions are on the page until this swap replaces them.

    The values it renders are *not* trusted on the way back in: `parse_form_start` re-parses whatever
    was posted, since a completion menu is a suggestion a browser was given rather than a constraint
    on what a form can carry.
    """
    try:
        repository, _ = posted_workspace({WORKSPACE_FIELD: [workspace]})
    except NotAMessage as unknown:
        # Refused *here* rather than by `recover`, which only converts what an extractor raised: a
        # `ValueError` from inside a handler is a fault there, so this has to say so itself.
        return page_response(422, refusal_page(LINKS, 422, str(unknown)))
    branches = () if repository is None or service.workspaces is None else await service.workspaces.branches(repository)
    return page_response(200, fragment(starting_at(repository, None, None, branches)))


@get(t"/sessions/{session_id}", session_id, summary="One session, whole")
async def show_session(service: Service, session: str) -> Response:
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return page_response(200, session_page(LINKS, await service.listed(), found, service.reachable))


@get("/fragments/stream", watched, shaped, summary="What a page is watching, sent as it changes")
async def stream(service: Service, session: str, on_step: bool | None) -> Reply:
    """
    The live connection a page holds open, carrying whatever it is watching as that changes.

    One per page rather than one per region, which is why the session is a query parameter and not
    a path segment: this does not pick a conversation out of the resource tree, it tells a
    page-level connection which one that page is showing. What comes back is `<hx-partial>`
    elements naming their own targets, so a second region joins the same connection rather than
    opening another.

    The page says which shape it was drawn in, for the same reason it says which session: the stream
    sends what that shape has somewhere to put, and says once when the shape is over. See
    `Links.to_stream`.

    Under `fragments/` for the reason every other swap-shaped path is: it is the disposable half of
    the URL space, and it is now where all of a watching page's traffic goes, which makes it one
    filter in a log rather than a growing list of paths.

    The session is checked here rather than inside the stream, because a refusal has to be a
    refusal: an event stream that opened and immediately ended would be reconnected by the client
    forever, where a `404` is an answer it can act on.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return event_stream(with_heartbeat(watching(service, LINKS, session, service.watching, on_step=bool(on_step))))


@get(
    t"/fragments/sessions/{session_id}/requests/{of_turn}/{at_panel}",
    session_id,
    of_turn,
    at_panel,
    summary="What the checkpoint holds for one model request",
)
async def request_record(service: Service, session: str, turn: int, at: int) -> Response:
    """
    What one model request of a turn came back with, fetched only when somebody opens its tag.

    On demand rather than rendered into the transcript, because the transcript is swapped whenever a
    running turn records anything: the raw record of every request is several times the size of the
    reading of it, and it would be carried by every message for something almost always closed.

    Settled for good the moment it exists, which is what lets the page fetch it once and keep it. A
    step's key is written once and never rewritten, so unlike a panel's record this is answerable
    while the turn is still running - the response is there as soon as the provider gave it.
    """
    held = await service.requested_at(session, turn, at)
    if held is None:
        return page_response(404, fragment(missing_record(turn, at)))
    return page_response(200, fragment(record_json(held)))


@post(t"/sessions/{session_id}/messages", session_id, sending, summary="Say something to a session")
async def say(service: Service, session: str, sending: Sending) -> Response:
    """
    Send the message the composer posted wherever it was addressed.

    Both arms are calls this console already made; what the disposition adds is which one, chosen by
    the person rather than by which form they happened to be looking at.

    `HERE` answers with a `200` carrying the transcript rather than a redirect, because htmx is
    driving it and the address bar does not change: the swap shows the message as pending straight
    away. It renders what the page's live connection would send a moment later, which is deliberate
    rather than duplicated work - this is the one request somebody is actually waiting on, so it
    answers rather than leaving a message to appear whenever the stream next looks, and the
    connection then sends the same thing, which morphs to nothing.

    `PARENT` cannot do that, because the message goes into a *different* session and the reader has
    to end up there. A `303` would be followed by htmx and swapped into the transcript, which would
    leave somebody reading the parent at the branch's URL, so this answers `HX-Redirect` and the
    browser navigates for real.

    Nothing here forks. A fork is made from a rule, at a turn boundary, through the fork page, and
    the composer offered one at the end of the conversation until it did not: carrying on a live
    session is typing into it, and an archived one carries the same link on the rule under its last
    turn.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    # Every arm, before any of them: an archived session takes nothing more, whichever way it was
    # addressed and whoever posted it. The page's own controls are disabled, so reaching this is a
    # caller that is not this page, and a message on the floor is the state the key exists to end.
    if found.session.archived is not None:
        return page_response(422, refusal_page(LINKS, 422, f"session {session} is archived; fork it instead"))
    # A plugin's own answer, before the console's, because it is not a `Disposition` at all: what
    # happens to what you typed is the plugin's to decide, and the effects it asks for are performed
    # by the service exactly as they are inside a pass.
    #
    # A session nobody can answer is refused rather than asked, because a plugin whose delivery
    # nothing will ever answer is a panel that waits for ever - the one state the stall sentence
    # exists to prevent, reached from the other direction.
    if isinstance(sending.where, ToPlugin):
        if stalled_by(found) is not None:
            return page_response(422, refusal_page(LINKS, 422, f"session {session} cannot be answered"))
        delivered = await service.compose(session, found, sending.where.leader, sending.said)
        if delivered is None:
            return page_response(
                422, refusal_page(LINKS, 422, f"no plugin of session {session} answers to /{sending.where.leader}")
            )
        # Read again only where something was actually put in the inbox. A plugin that asked for a
        # `set` and nothing else left the checkpoint exactly as it is above, and the conversation is
        # what a full decode of it costs.
        if not delivered:
            return page_response(200, fragment(transcript_region(LINKS, found)))
        return await redrawn(service, session)
    match sending.where:
        case Disposition.HERE:
            # Nobody here decides between a steer and a turn of its own, and that is the point: the
            # message goes in the queue and the pass that takes it decides, because it is the only
            # thing reading at the moment the answer is true. The page this was posted from was
            # rendered from a state that has since moved, and so was any read this could make.
            await service.send(session, sending.said)
            return await redrawn(service, session)
        case Disposition.NEXT | Disposition.FORGET:
            # One arm and a flag, the way `FORK | ASIDE` share theirs: both put the message in the
            # next free turn and differ only in what that turn opens on. A forget never reaches
            # `send`, because a boundary between turns is the only place one can be.
            await service.say(session, sending.said, forget=sending.where is Disposition.FORGET)
            return await redrawn(service, session)
        case Disposition.RUN:
            # Not a message at all: the text is run in this session's worktree, as the person, and
            # the record of it is never told to a model. Refused rather than silently ignored where
            # there is nowhere to run it, because a command that vanished would be indistinguishable
            # from one that did nothing.
            if not found.runnable:
                return page_response(
                    422, refusal_page(LINKS, 422, f"session {session} has no files to run a command in")
                )
            await service.run(session, sending.said)
            return await redrawn(service, session)
        case Disposition.PARENT:
            # Where this session came from, which is the only session a message may be sent to that
            # is not the one it was typed in. Read off the row rather than posted, so a form cannot
            # name a conversation somebody is not looking at.
            origin = found.session.forked
            if origin is None:
                return page_response(422, refusal_page(LINKS, 422, f"session {session} was not forked from anything"))
            if await service.read(origin.session) is None:
                return page_response(404, refusal_page(LINKS, 404, f"no session {origin.session}"))
            await service.say(origin.session, sending.said)
            return navigating(LINKS.to_session(origin.session))
        case _ as unreachable:
            assert_never(unreachable)


@post(t"/sessions/{session_id}/setup", session_id, setting_up, summary="Load the plugins a session runs")
async def setup(service: Service, session: str, wanted: SettingUp) -> Response:
    """
    Answer the settings step: record the switches, say somebody pressed, and ask for a pass.

    **This is the request that lets a plugin be executed at all, and that is what the step is for.**
    Nothing before it has run one: the pass that planted the worktree read what each tier *declares*
    out of files, and the switches on this form are drawn from that. So the press is the
    confirmation, and the pass that follows is what it confirms.

    **Live while the session is still settling, and refused afterwards.** A tool definition leaving
    the cached prefix invalidates everything under it exactly as one arriving late does, so a session
    that has been set up is one whose set of plugins is settled, and forking is how a conversation
    changes its mind. Asked of `settling`, which is the predicate the page's own shape is drawn from:
    the step is answerable exactly while it is the thing being drawn. That is refused here rather
    than in the service because what decides it is what the checkpoint holds, and the service holds
    no page.

    Two buttons and one route, told apart by a field rather than by the shape of the post. `Try
    again` asks for another *declaring* pass, which is the whole of what retrying a session whose
    worktree or whose `.mainplate/mainplate.yaml` refused is. Anything else asks for the setup.

    **One answer now, where there used to be two.** Both buttons end in a `303` to the session,
    because the press no longer decides anything: what it does is record the switches and queue a
    pass, and what that pass makes of them arrives on the page the redirect lands on. A setup that
    will not finish is the step again with the reason above the switches, drawn from what the pass
    recorded rather than from what this handler happened to see, so a reload says the same thing.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    if found.session.archived is not None:
        return page_response(422, refusal_page(LINKS, 422, f"session {session} is archived; fork it instead"))
    if not settling(found):
        return page_response(
            422, refusal_page(LINKS, 422, f"session {session} has already loaded its plugins; fork it instead")
        )
    if wanted.again:
        await service.setup_again(session)
    else:
        await service.settle(session, wanted.switches, found.attempts)
    return seeing(LINKS.to_session(session))


@post(t"/sessions/{session_id}/plugins", session_id, pressing, summary="Set one plugin's own settings")
async def press(service: Service, session: str, pressed: Pressed) -> Response:
    """
    Save one plugin's card as it was posted, and answer with the card as it now stands.

    Answered with the card for the reason the step is answered with itself: nothing about the
    conversation changed, so swapping the transcript would replace the whole region to show what is
    already in the rail.

    Taken whatever the session's state, unlike a message: a session nobody can answer is exactly one
    somebody might want to stop a plugin spending anything on, and refusing to record that would be
    refusing the only useful thing left to do with it. What is settled for a session's life is *which*
    plugins run, not what they are set to.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    enrolled = await service.press(session, found, pressed.plugin, pressed.posted)
    if enrolled is None:
        return page_response(422, refusal_page(LINKS, 422, f"session {session} runs no plugin {pressed.plugin!r}"))
    # The two columns rather than the conversation, because that is what the press wrote and what
    # the card draws: reading the checkpoint again would decode every recorded step of a session to
    # answer a question about one row. It is read rather than assumed because a plugin's `action`
    # handler may have written through `storing` while this was running.
    tended = await read_tending(service.database, session)
    settings = settings_of(enrolled.described, tended.of(enrolled.qualified))
    return page_response(200, fragment(plugin_card(LINKS, session, enrolled, settings)))


@post(t"/sessions/{session_id}/archive", session_id, summary="Archive a session, keeping its conversation")
async def archive(service: Service, session: str) -> Response:
    """
    Close the session, and go back to it as the archived thing it now is.

    An ordinary form post answered with a `303`, because what changes is not one region: the box
    goes, the rail's card says when, the transcript says why, and the row in the sidebar is muted.
    The live connection carries only the transcript, so a fragment would leave three of those as they
    were until a reload, and a reload is what the redirect is. It is also why the press on a sidebar
    row lands on the session it closed rather than back where the row was: the page that opens is
    the one saying what just happened.

    A `303` to the same page is also what makes pressing it twice harmless: the key is write-once, so
    the second press is the first one again, and the page it lands on is the same page.
    """
    archived = await service.archive(session)
    if archived is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return seeing(LINKS.to_session(session))


CONSOLE_ROUTES: tuple[Route[Service], ...] = (
    start_here,
    start,
    show_session,
    stream,
    endpoint_models,
    workspace_branches,
    fork_form,
    fork,
    say,
    setup,
    press,
    archive,
    request_record,
)

LINKS = Links(
    home=start_here,
    start=start,
    session=show_session,
    say=say,
    stream=stream,
    request_record=request_record,
    endpoint_models=endpoint_models,
    workspace_branches=workspace_branches,
    fork_form=fork_form,
    fork=fork,
    setup=setup,
    press=press,
    archive=archive,
    assets=ASSETS,
)
