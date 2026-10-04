# Which requests may change something, decided by where the page that sent them was served from.
#
# The console has no accounts, so what guards it is who can reach it, and a browser that can reach
# it carries that reach to every page it has open. A page on another port of the same host is the
# same *site* to that browser, cookies included, since a cookie is scoped to a host and never to a
# port: on exe.dev the login cookie in front of the console rides along on a post from any page at
# `<vm>.exe.xyz:<port>`. Its scripts cannot read what the console answers, but a form posted to
# `/sessions` needs no answer read to start a session on the whole machine. So a write is answered
# only when the browser says it came from the console's own origin.
#
# This is the algorithm Go 1.25's `http.CrossOriginProtection` runs, described in Filippo Valsorda's
# https://words.filippo.io/csrf/, taken whole rather than improved on.

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

from without_asgi import HttpHandler
from without_asgi import HttpScope
from without_asgi import Inbound
from without_asgi import Outbound
from without_asgi import RawHeaders
from without_asgi import Response
from without_asgi import encode_response
from without_asgi import headers
from without_asgi.routing import HttpMiddleware
from without_streams import Stream

# A method a browser sends on a link, a reload or a prefetch, which therefore must not change
# anything here and is answered from wherever it came.
READING: Final = frozenset({"GET", "HEAD", "OPTIONS"})

# What `Sec-Fetch-Site` says when the request is the console's own page or a person at the address
# bar. `same-site` is deliberately not here: that is the other port.
OWN: Final = frozenset({b"same-origin", b"none"})


def is_crossing(method: str, sent: RawHeaders) -> bool:
    """
    Whether this request changes something on behalf of a page the console did not serve.

    `Sec-Fetch-Site` decides when it is there, because it is the browser's own account of where the
    request came from and no page can set it. It is sent only to a trustworthy origin, so a console
    reached over plain HTTP at an address that is not loopback gets none, and `Origin` is compared
    against `Host` instead. A request carrying neither came from something that is not a browser,
    a `curl` or the suite's own client, which cannot be a page somebody else wrote and is let
    through.

    The cost, stated: that `Origin` comparison is exact, so a proxy that rewrites `Host` in front of
    a console served over plain HTTP refuses the console's own writes. Over HTTPS, exe.dev's
    included, the browser sends `Sec-Fetch-Site` and `Host` is never consulted.
    """
    if method in READING:
        return False
    if (site := headers.first(sent, b"sec-fetch-site")) is not None:
        return site not in OWN
    if (origin := headers.first(sent, b"origin")) is None:
        return False
    # `null` is what an opaque origin sends, an artifact's frame among them, and it has no host to
    # match, so it is refused like any other stranger.
    return urlsplit(origin.decode("latin-1")).netloc != (headers.first(sent, b"host") or b"").decode("latin-1")


def refusing_crossings(refused: Response) -> HttpMiddleware[object]:
    """
    Answer a crossing write with `refused` before any route sees it.

    Here rather than on each `post`, because a route added later is protected by being added, and a
    check written per route is one that the next route forgets. The body is never read, as
    `limit_concurrent_requests` never reads one it sheds.
    """
    shed = tuple(encode_response(refused))

    def middleware(handler: HttpHandler, _state: object, scope: HttpScope) -> HttpHandler:
        if not is_crossing(scope.method, scope.headers):
            return handler

        # `inputs` by name, since `Processor` is a protocol whose call names its parameter.
        async def refusing(inputs: Stream[Inbound]) -> Stream[Outbound]:
            for event in shed:
                yield event

        return refusing

    return middleware
