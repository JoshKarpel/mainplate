# The console's routes: six paths, and what each of them reads or writes.
#
# Nothing here runs an agent. A write puts a message into a session's checkpoint and asks for the
# session to be looked at; the worker is what turns that into a model call, in its own time and
# possibly in another process. That is the whole reason a reply can outlive the request that
# asked for it, and it is why every one of these handlers is short.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs

from pydantic_ai.settings import ThinkingLevel
from without_asgi import Response
from without_asgi import html_content
from without_web import STR
from without_web import ExtractionError
from without_web import Route
from without_web import body
from without_web import get
from without_web import once
from without_web import path_param
from without_web import post
from without_web import query_param

from mainplate.agent import Choice
from mainplate.conversation import THINKING_FIELD
from mainplate.pages import Links
from mainplate.pages import fork_page
from mainplate.pages import fragment
from mainplate.pages import model_select
from mainplate.pages import refusal_page
from mainplate.pages import session_page
from mainplate.pages import stalled_by
from mainplate.pages import start_page
from mainplate.pages import transcript_region
from mainplate.service import Service
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
# The profile whose models to render, which is the value of the select that asks for them.
of_profile = query_param("profile", once(str), schema={"type": "string"})
# Which turn a fork would start at, which is the first turn the branch does not inherit.
at_turn = query_param("at", once(int), schema={"type": "integer"})


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


def parse_form_start(raw: bytes) -> Started:
    """
    The whole of what the new-chat form carries: a message, and what to answer it with.

    Parsed together rather than in two extractors because they arrive in one body and are refused
    on one condition: a form that names a profile without a message is not half a request, it is
    not a request. Whether the pair is *configured* is not asked here, because this layer has no
    configuration; the handler asks that of the `Service` and refuses with a status of its own.

    The thinking level is the one field this layer can settle by itself, because unlike a profile
    and a model it is a closed set rather than something discovered. An absent field is the
    configured default rather than a refusal, so a form posted by something that predates the
    control still names a session's whole choice.
    """
    said = parse_form_prompt(raw)
    fields = parse_qs(raw.decode("utf-8", errors="replace"))
    profile = fields.get("profile", [""])[0].strip()
    model = fields.get("model", [""])[0].strip()
    if not profile or not model:
        raise NotAMessage("a message needs a profile and a model to be answered on")
    return Started(said=said, chosen=Choice(profile=profile, model=model, thinking=posted_thinking(fields)))


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
    profile = fields.get("profile", [""])[0].strip()
    model = fields.get("model", [""])[0].strip()
    at = fields.get("at", [""])[0].strip()
    if not profile or not model:
        raise NotAMessage("a fork needs a profile and a model to be answered on")
    if not at.isdigit():
        raise NotAMessage("a fork needs the turn it forks at")
    return Forking(
        at=int(at),
        chosen=Choice(profile=profile, model=model, thinking=posted_thinking(fields)),
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


@get("/", summary="Start a session")
async def start_here(service: Service) -> Response:
    return page_response(200, start_page(LINKS, await service.listed(), service.catalogues.current))


@post("/sessions", starting, summary="Say the first thing, which is what creates a session")
async def start(service: Service, started: Started) -> Response:
    """
    Mint a session on the chosen profile and hand it its first message.

    An ordinary form post rather than an htmx one, because this is the request that changes which
    session the browser is looking at, and htmx never sees a redirect: the browser follows it
    internally and htmx is handed the final response, so a swap-driven version would render the
    new session into the old page's URL.

    The pair is checked here rather than trusted, because it arrived in a form: a select is a
    suggestion a browser was given, not a constraint on what somebody can post, so this is where a
    new session is held to a pair the picker actually rendered.

    It is the one place the *pair* is checked, and deliberately stricter than what stops an
    existing session, which is a missing profile alone. The asymmetry is the point: a conversation
    already under way should not be broken by a model quietly leaving a list, while a new one has
    no reason to start on something nobody was shown.
    """
    if not service.catalogues.current.offers(started.chosen.profile, started.chosen.model):
        return page_response(422, refusal_page(LINKS, 422, f"no profile on offer serves {started.chosen.model}"))
    session = await service.start(started.said, started.chosen)
    return seeing(LINKS.to_session(session.id))


@get(t"/sessions/{session_id}/forks/new", session_id, at_turn, summary="Where a fork would start")
async def fork_form(service: Service, session: str, at: int) -> Response:
    """
    The page that asks what to answer a branch with, before anything is created.

    A page of its own rather than a control inside the transcript, and the reason is what the
    transcript is: a region replaced once a second while a turn is in flight. A picker rendered per
    person panel would be rebuilt under the reader's hand on every poll, and there would be one per
    turn. Here the question is asked once, on a page that is not swapping, and the answer arrives
    as an ordinary form post that a browser with no script can make.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    if not 0 <= at <= found.said.turns:
        return page_response(404, refusal_page(LINKS, 404, f"session {session} has no turn {at}"))
    return page_response(200, fork_page(LINKS, await service.listed(), found, at, service.catalogues.current))


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
    if not service.catalogues.current.offers(branch.chosen.profile, branch.chosen.model):
        return page_response(422, refusal_page(LINKS, 422, f"no profile on offer serves {branch.chosen.model}"))
    forked = await service.fork(session, at=branch.at, chosen=branch.chosen, said=branch.said)
    if forked is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return seeing(LINKS.to_session(forked.id))


@get("/fragments/models", of_profile, summary="One profile's model select")
async def profile_models(service: Service, profile: str) -> Response:
    """
    The model select for a profile, which is what changing the profile swaps in.

    A fragment rather than a script over a table of models embedded in the page: what an endpoint
    offers is discovered and is refreshed while the page is open, so a list serialized into the
    document at render time is the one thing guaranteed to go stale, and a select rebuilt from the
    server cannot drift from what the form will actually be checked against.
    """
    found = service.catalogues.current.models_of(profile)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no profile {profile}"))
    return page_response(200, fragment(model_select(found)))


@get(t"/sessions/{session_id}", session_id, summary="One session, whole")
async def show_session(service: Service, session: str) -> Response:
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return page_response(200, session_page(LINKS, await service.listed(), found))


@get(t"/fragments/sessions/{session_id}", session_id, summary="One session's transcript alone, for a live region")
async def session_fragment(service: Service, session: str) -> Response:
    """
    The same transcript the page holds, built by the same function, with no document around it.

    Under `fragments/` rather than at `/sessions/{id}/transcript`, so the segment after a session
    id keeps meaning something about that session rather than sometimes naming part of a page.
    Fetching one gives an unstyled element with no document around it, which is not a promise a
    path shaped like a detail page should make; and it is the disposable half of the URL space,
    so keeping it out of the durable half leaves something saying which is which. It is also
    where nearly all the traffic goes while a reply is in flight, which makes it one filter in a
    log rather than a growing list of paths scattered through the resource tree.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return page_response(200, fragment(transcript_region(LINKS, session, found.said, stalled_by(found))))


@post(t"/sessions/{session_id}/messages", session_id, prompt, summary="Say something to a session")
async def say(service: Service, session: str, said: str) -> Response:
    """
    Put a message into a session's checkpoint and answer with the transcript that now holds it.

    A `200` carrying the transcript rather than a redirect, because htmx is driving this one and
    the address bar does not change: the swap replaces the conversation with one showing the
    message as pending, carrying the poll that will replace it again once it is answered.
    """
    found = await service.read(session)
    if found is None:
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    await service.say(session, turn=found.said.turns, said=said)
    asked = await service.read(session)
    if asked is None:  # pragma: no cover - read a line ago, and nothing deletes a session
        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))
    return page_response(200, fragment(transcript_region(LINKS, session, asked.said, stalled_by(asked))))


CONSOLE_ROUTES: tuple[Route[Service], ...] = (
    start_here,
    start,
    show_session,
    session_fragment,
    profile_models,
    fork_form,
    fork,
    say,
)

LINKS = Links(
    home=start_here,
    start=start,
    session=show_session,
    say=say,
    session_fragment=session_fragment,
    profile_models=profile_models,
    fork_form=fork_form,
    fork=fork,
    assets=ASSETS,
)
