# The console's routes, and what each of them reads or writes.
#
# Nothing here runs an agent. A write puts a message into a session's checkpoint and asks for the
# session to be looked at; the worker is what turns that into a model call, in its own time and
# possibly in another process. That is the whole reason a reply can outlive the request that
# asked for it, and it is why every one of these handlers is short.

from __future__ import annotations

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
from without_web import path_param
from without_web import post
from without_web import query_param

from mainplate.agent import Choice
from mainplate.conversation import DISPOSITION_FIELD
from mainplate.conversation import NETWORK_FIELD
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import Disposition
from mainplate.conversation import parse_disposition
from mainplate.pages import WORKSPACE_FIELD
from mainplate.pages import Links
from mainplate.pages import fork_page
from mainplate.pages import fragment
from mainplate.pages import missing_record
from mainplate.pages import model_cards
from mainplate.pages import record_json
from mainplate.pages import refusal_page
from mainplate.pages import session_page
from mainplate.pages import stalled_by
from mainplate.pages import start_page
from mainplate.pages import transcript_region
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.service import Service
from mainplate.sessions import TITLE_FIELD
from mainplate.streaming import watching
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
# Which conversation a watching page is showing. A query parameter rather than a path segment
# because the stream belongs to the page: this narrows what one connection reports on, where a path
# segment would say the connection is a thing *of* that session. It is what lets a second region
# join the same connection later without the path becoming a lie.
watched = query_param("session", once(str), schema={"type": "string"})
# Which turn a fork would start at, which is the first turn the branch does not inherit.
at_turn = query_param("at", once(int), schema={"type": "integer"})
# The two halves of a panel's identity, in the path because that is what they are: a panel is named
# by its turn and its position within it, which is the same pair its anchor and its label are built
# from. A query string would say these narrow something down, where they pick one thing out.
of_turn = path_param("turn", INT)
at_panel = path_param("at", INT)


class NotAMessage(ValueError):
    """A form post that did not carry a message this console could send."""


def parse_form_prompt(raw: bytes) -> str:
    """
    The message a form carried, parsed at the boundary and refused if it is not one.

    `parse_qs` drops empty values, so a form submitted with an empty box arrives as no field at
    all rather than as an empty string, and both are the same refusal here. Refusing is what lets
    everything downstream treat a prompt as text somebody meant to send.
    """
    if len(raw) > LONGEST_PROMPT:
        raise NotAMessage(f"a message may be at most {LONGEST_PROMPT} bytes")
    fields = parse_qs(raw.decode("utf-8", errors="replace"))
    said = fields.get("prompt", [""])[0].strip()
    if not said:
        raise NotAMessage("a message cannot be empty")
    return said


prompt = body(parse_form_prompt, schema={"type": "string"}, media_type="application/x-www-form-urlencoded")


@dataclass(frozen=True, slots=True)
class Sending:
    """What the composer posted: a message, and where it is going."""

    said: str
    where: Disposition


def parse_form_send(raw: bytes) -> Sending:
    """
    The message the composer carried and the disposition it was sent under.

    Parsed together for the reason `parse_form_start` parses its fields together: they arrive in one
    body and mean nothing apart. An **absent** field is `HERE`, because a form predating the control
    posts a message and means what Send has always meant; a field naming something this console does
    not offer is a refusal, because guessing which destination somebody meant is the one thing that
    could silently put a message in the wrong conversation.
    """
    said = parse_form_prompt(raw)
    fields = parse_qs(raw.decode("utf-8", errors="replace"))
    named = fields.get(DISPOSITION_FIELD, [""])[0].strip()
    if not named:
        return Sending(said=said, where=Disposition.HERE)
    where = parse_disposition(named)
    if where is None:
        raise NotAMessage(f"{named!r} is not somewhere a message can be sent")
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
    """
    said = parse_form_prompt(raw)
    fields = parse_qs(raw.decode("utf-8", errors="replace"))
    endpoint = fields.get("endpoint", [""])[0].strip()
    model = fields.get("model", [""])[0].strip()
    if not endpoint or not model:
        raise NotAMessage("a message needs an endpoint and a model to be answered on")
    return Started(
        said=said,
        # Optional, and an empty box is the same as no field at all: `parse_qs` drops empty values,
        # so both arrive here as nothing and both mean "name it after what I said". The length is
        # bounded by the message bound above, and cut to a name by `Service.start`, which is where
        # the one rule about what a session name is already lives.
        title=fields.get(TITLE_FIELD, [""])[0].strip() or None,
        chosen=Choice(
            endpoint=endpoint,
            model=model,
            repository=posted_workspace(fields)[0],
            isolation=posted_isolation(fields),
            thinking=posted_thinking(fields),
        ),
    )


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

    said: str
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
        said=fields.get("prompt", [""])[0].strip() or None,
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


@post("/sessions", starting, summary="Say the first thing, which is what creates a session")
async def start(service: Service, started: Started) -> Response:
    """
    Mint a session on the chosen endpoint and hand it its first message.

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
    session = await service.start(started.said, started.chosen, started.title)
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


@get(t"/sessions/{session_id}", session_id, summary="One session, whole")
async def show_session(service: Service, session: str) -> Response:
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return page_response(200, session_page(LINKS, await service.listed(), found, service.reachable))


@get("/fragments/stream", watched, summary="What a page is watching, sent as it changes")
async def stream(service: Service, session: str) -> Reply:
    """
    The live connection a page holds open, carrying whatever it is watching as that changes.

    One per page rather than one per region, which is why the session is a query parameter and not
    a path segment: this does not pick a conversation out of the resource tree, it tells a
    page-level connection which one that page is showing. What comes back is `<hx-partial>`
    elements naming their own targets, so a second region joins the same connection rather than
    opening another.

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
    return event_stream(with_heartbeat(watching(service, LINKS, session, service.watching)))


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

    `FORK` cannot do that, because what it makes is a *different* session and the reader has to
    end up there. A `303` would be followed by htmx and swapped into the transcript, which would
    leave somebody reading the branch at the parent's URL, so this answers `HX-Redirect` and the
    browser navigates for real.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    match sending.where:
        case Disposition.HERE:
            await service.say(session, turn=found.said.turns, said=sending.said)
            asked = await service.read(session)
            if asked is None:  # pragma: no cover - read a line ago, and nothing deletes a session
                return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
            return page_response(200, fragment(transcript_region(LINKS, session, asked.said, stalled_by(asked))))
        case Disposition.FORK | Disposition.ASIDE:
            # The parent's own choice, not a posted one: a fork from the composer offers no picker,
            # and `Service.fork` is what decides the repository either way. Forking the *end* carries
            # every turn, so nothing is left behind and nothing is re-asked.
            if found.chosen is None:
                return page_response(422, refusal_page(LINKS, 422, f"session {session} records no endpoint"))
            forked = await service.fork(
                session,
                at=found.said.turns,
                chosen=found.chosen,
                said=sending.said,
                aside=sending.where is Disposition.ASIDE,
            )
            if forked is None:  # pragma: no cover - read a line ago, and nothing deletes a session
                return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
            return navigating(LINKS.to_session(forked.id))
        case Disposition.STEER:
            # Into the turn being answered, which is the one before the next free slot. Refused where
            # nothing is running: a steer nobody would ever read is a message on the floor, and
            # queueing it instead would answer a different question than the one that was asked.
            if found.said.answering is None:
                return page_response(422, refusal_page(LINKS, 422, f"session {session} is not answering anything"))
            await service.steer(session, turn=found.said.answering, said=sending.said)
            asked = await service.read(session)
            if asked is None:  # pragma: no cover - read a line ago, and nothing deletes a session
                return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
            return page_response(200, fragment(transcript_region(LINKS, session, asked.said, stalled_by(asked))))
        case Disposition.PARENT:
            # Where this session came from, which is the only session a message may be sent to that
            # is not the one it was typed in. Read off the row rather than posted, so a form cannot
            # name a conversation somebody is not looking at.
            origin = found.session.forked
            if origin is None:
                return page_response(422, refusal_page(LINKS, 422, f"session {session} was not forked from anything"))
            came_from = await service.read(origin.session)
            if came_from is None:
                return page_response(404, refusal_page(LINKS, 404, f"no session {origin.session}"))
            await service.say(origin.session, turn=came_from.said.turns, said=sending.said)
            return navigating(LINKS.to_session(origin.session))
        case _ as unreachable:
            assert_never(unreachable)


CONSOLE_ROUTES: tuple[Route[Service], ...] = (
    start_here,
    start,
    show_session,
    stream,
    endpoint_models,
    fork_form,
    fork,
    say,
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
    fork_form=fork_form,
    fork=fork,
    assets=ASSETS,
)
