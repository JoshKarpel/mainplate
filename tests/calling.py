# The client the console tests drive the server with, which is `without`'s own in-memory one.
#
# `loopback_client` is `serving` minus the kernel: the production connection pool encodes the
# request, the server's own connection loop decodes it and drives the app, and the bytes cross a
# pipe instead of a socket. So the wire is still under test while nothing binds a port, which is
# what lets the suite run these in parallel without port churn.
#
# Every write this console takes is a form post, so that is the only body shape here.

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlencode

from without_asgi import ASGIApp
from without_asgi import RawHeaders
from without_asgi.sse import ReceivedEvent
from without_asgi.sse import parse_events
from without_http import Client
from without_http import request
from without_http.testing import loopback_client

# The authority an in-memory server answers to, since a pipe has no port to name.
BASE = "http://testserver"
FORM = ((b"content-type", b"application/x-www-form-urlencoded"),)

# The clock every caller here reads pages against unless it asks for another.
#
# Stated rather than left to the machine, for `WHEN`'s reason one step along: the console draws a
# moment in whichever zone the request asks for and falls back to the machine's own, so a suite that
# said nothing would assert `15:09` on a UTC runner and `10:09` on a laptop in Chicago. A test about
# the conversion asks for a zone of its own.
UTC_ZONE = "UTC"


@dataclass(frozen=True, slots=True)
class Answer:
    """One response, already read."""

    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode()

    @property
    def location(self) -> str:
        return self.headers["location"]

    @property
    def varies_by(self) -> frozenset[str]:
        """The request fields this response says it depends on, lowercased, whichever `Vary` line named each."""
        return frozenset(field.strip().lower() for field in self.headers.get("vary", "").split(",") if field.strip())


@dataclass(frozen=True, slots=True)
class Caller:
    """The console as the two request shapes these tests make, and nothing more."""

    client: Client
    zone: str = UTC_ZONE
    """Which clock this caller reads moments against, carried on every request as the cookie."""

    @property
    def cookie(self) -> tuple[tuple[bytes, bytes], ...]:
        """The zone cookie a browser would be sending by the time it has loaded one page."""
        return ((b"cookie", f"zone={self.zone}".encode()),)

    async def get(self, path: str) -> Answer:
        return await self.send("GET", path)

    async def post(self, path: str, form: Mapping[str, str] | None = None, headers: RawHeaders = ()) -> Answer:
        """
        A form post, with nothing to say where it came from unless `headers` says it.

        This client is not a browser, so by default it sends no `Sec-Fetch-Site` and no `Origin`,
        which is what lets every write here through; a test about which page a write came from
        names those itself.
        """
        return await self.send("POST", path, form=form, headers=headers)

    @asynccontextmanager
    async def watching(self, path: str) -> AsyncIterator[AsyncIterator[ReceivedEvent]]:
        """
        A live connection to `path`, as the events coming down it.

        A context manager because the response has no end: the console's stream carries a page's
        conversation for as long as the page is open, so a caller takes the events it wants and
        leaves, and the block is what closes the body rather than the stream running out. Reading it
        with `send` would hang forever.

        The framing is really parsed rather than asserted against as text, which is the point of
        driving it through the loopback client at all: the response crosses the same encoder and
        decoder a socket would, so what a test reads is what a browser would.
        """
        async with request(self.client, "GET", f"{BASE}{path}", headers=self.cookie) as response:
            yield parse_events(response.body)

    async def send(
        self, method: str, path: str, form: Mapping[str, str] | None = None, headers: RawHeaders = ()
    ) -> Answer:
        sent = (*self.cookie, *headers) if form is None else (*FORM, *self.cookie, *headers)
        body = b"" if form is None else urlencode(form).encode()
        async with request(self.client, method, f"{BASE}{path}", headers=sent, body=body) as response:
            return Answer(
                status=response.head.status,
                headers=joined(response.head.headers),
                body=await response.body.read(),
            )


def joined(raw: RawHeaders) -> dict[str, str]:
    """
    A response's fields by lowercased name, a field sent on several lines joined with commas.

    Joined rather than keyed, because that is what several lines of one field mean (RFC 9110 §5.2):
    a page says `Vary: cookie` and compression adds `Vary: accept-encoding` on a line of its own, and
    a mapping that kept the last line would report a page that no longer varies by its cookie.
    """
    fields: dict[str, list[str]] = {}
    for name, value in raw:
        fields.setdefault(name.decode().lower(), []).append(value.decode())
    return {name: ", ".join(values) for name, values in fields.items()}


@asynccontextmanager
async def calling(app: ASGIApp, zone: str = UTC_ZONE) -> AsyncIterator[Caller]:
    """`app` served in memory for the block, with a caller pointed at it."""
    async with loopback_client(app) as client:
        yield Caller(client=client, zone=zone)
